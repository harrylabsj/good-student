import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from exam_coach.catalog import Question, load_bank
from exam_coach.server import AccessVerifier, create_app, http_app
from exam_coach.service import RETEST_GAP, Candidate, Coach, CoachError, Confirmation, Response, Review
from starlette.testclient import TestClient

ISSUER = "https://identity.example.test"
RESOURCE = "https://coach.example.test/mcp"
CLIENT = "workbuddy-test-client"


def question(index, *, subject="数学", topic="math-sets", rubric=False, licensed=False):
    return Question.model_validate({
        "id": f"fixture-{subject == '数学' and 'math' or 'physics'}-{index}",
        "subject": subject, "min_grade": 10, "topics": [topic], "difficulty": "基础",
        "kind": "rubric" if rubric else "choice", "answers": [] if rubric else ["B"],
        "rubric": [{"id": "method", "description": "给出正确方法", "points": 2},
                   {"id": "unit", "description": "保留正确单位", "points": 1}] if rubric else [],
        "source": {"exam": "仅测试夹具", "paper": "测试卷", "year": 2025, "region": "广东",
                   "number": str(index), "paper_url": "https://exam.example.test/question",
                   "answer_url": "https://exam.example.test/answer", "publisher": "测试夹具"},
        "reviewed_by": "测试夹具",
        "use": "licensed" if licensed else "link_only",
        "rights_reference": "仅测试" if licensed else None,
        "prompt": "测试题面" if licensed else None,
    })


@pytest.fixture
def local(tmp_path):
    clock = [1_800_000_000]
    bank = [question(i) for i in range(1, 10)]
    bank.append(question(1, subject="物理", topic="physics-electric", rubric=True, licensed=True))
    service = Coach(tmp_path / "coach.db", {q.id: q for q in bank}, now=lambda: clock[0])
    service.create_student("a", "学生甲", 11, "广东", True)
    service.create_student("b", "学生乙", 12, "上海", True)
    return service, clock


def flagged(service, account="a", question_text="带参数的集合交集", **overrides):
    item = Candidate.model_validate({
        "subject": "数学", "question": question_text, "topic_id": "math-sets",
        "reason": "概念缺失", "confidence": 0.9, **overrides,
    })
    return service.stage(account, [item], True)["drafts"][0]["draft_id"]


def test_only_confirmed_owned_and_certain_wrong_questions_count(local):
    service, _ = local
    first = flagged(service)
    assert not service.weaknesses("a", "数学")["topics"]
    with pytest.raises(CoachError, match="请让用户确认"):
        service.confirm("a", [Confirmation(draft_id=first)], False)
    with pytest.raises(CoachError, match="不存在"):
        service.confirm("b", [Confirmation(draft_id=first)], True)
    service.confirm("a", [Confirmation(draft_id=first)], True)
    second = flagged(service, question_text="带参数 的集合交集")
    result = service.confirm("a", [Confirmation(draft_id=second)], True)
    assert result["confirmed"][0]["duplicate"] is True
    third = flagged(service, question_text="题面不清晰", uncertain_fields=["question"])
    service.confirm("a", [Confirmation(draft_id=third)], True)
    weak = service.weaknesses("a", "数学")
    assert weak["topics"][0]["status"] == "疑似薄弱"
    assert weak["without_topic"] == 1
    observation_id = weak["topics"][0]["evidence"][0]["observation_id"]
    assert service.observation("a", observation_id)["recorded_topic_id"] == "math-sets"
    assert len(service.list_observations("a", "数学", None, 10)["observations"]) == 2
    assert len(service.list_observations("a", "数学", "math-sets", 10)["observations"]) == 1
    with pytest.raises(CoachError, match="没有这道"):
        service.observation("b", observation_id)
    assert not service.weaknesses("a", "物理")["topics"]
    assert not service.weaknesses("b", "数学")["topics"]


def test_two_spaced_independent_passing_rounds_and_new_wrong_resets(local):
    service, clock = local
    original = "fixture-math-1"
    draft = flagged(service, original_question_id=original)
    service.confirm("a", [Confirmation(draft_id=draft)], True)

    def round_(hints=False):
        clock[0] += 1
        session = service.start("a", "数学", "math-sets", "retest", 3, None, False)
        assert original not in {q["question_id"] for q in session["questions"]}
        assert all("answer_key" not in q and "answer_url" not in q["source"] for q in session["questions"])
        answers = [Response(question_id=q["question_id"], answer="Ｂ",
                            used_hints=hints, previously_seen=False) for q in session["questions"]]
        return service.submit("a", session["session_id"], answers, True)

    assert round_(hints=True)["qualifies_as_retest"] is False
    assert service.weaknesses("a", "数学")["topics"][0]["status"] == "疑似薄弱"
    first = round_()
    assert first["passed"] is True
    assert service.weaknesses("a", "数学")["topics"][0]["next_retest_at"] == clock[0] + RETEST_GAP

    clock[0] += RETEST_GAP
    # Simulate a reviewed catalog update after the initial rounds.
    extra = question(11)
    service.bank[extra.id] = extra
    second = round_()
    assert first["session_id"] != second["session_id"]
    assert {q["question_id"] for q in first["questions"]}.isdisjoint(q["question_id"] for q in second["questions"])
    assert service.weaknesses("a", "数学")["topics"][0]["status"] == "已通过两次复测"
    clock[0] += 1
    draft = flagged(service, question_text="新的不同题面")
    service.confirm("a", [Confirmation(draft_id=draft)], True)
    assert service.weaknesses("a", "数学")["topics"][0]["status"] == "重复出现"


def test_rubric_is_hidden_until_submit_and_needs_explicit_review(local):
    service, _ = local
    result = service.start("a", "物理", "physics-electric", "practice", 1, None, True)
    sid, q = result["session_id"], result["questions"][0]
    assert q["prompt"]
    assert "rubric" not in q
    answer = Response(question_id=q["question_id"], answer="电流为 1 A", used_hints=False, previously_seen=False)
    with pytest.raises(CoachError, match="本账号没有"):
        service.submit("b", sid, [answer], True)
    graded = service.submit("a", sid, [answer], True)
    assert graded["status"] == "review_required"
    invalid = Review(question_id=q["question_id"], points={"method": 2, "unit": 2})
    with pytest.raises(CoachError, match="不可超出范围"):
        service.review("a", sid, [invalid], True)
    valid = Review(question_id=q["question_id"], points={"method": 2, "unit": 1})
    with pytest.raises(CoachError, match="让用户确认"):
        service.review("a", sid, [valid], False)
    assert service.review("a", sid, [valid], True)["questions"][0]["score"] == 3
    assert service.practice("a", sid)["status"] == "graded"


def test_shortage_is_explicit_and_records_survive_restart(local):
    service, _ = local
    result = service.start("b", "物理", "physics-electric", "practice", 1, None, True)
    assert result == {"status": "insufficient_questions", "requested": 1, "available": 0,
                      "topic": "静电场与电路", "session_id": None, "questions": []}
    started = service.start("b", "数学", "math-sets", "practice", 1, None, False)
    restarted = Coach(service.path, service.bank)
    assert restarted.practice("b", started["session_id"])["status"] == "open"
    assert not restarted.history("a")["sessions"]
    with pytest.raises(CoachError, match="已有一个学生"):
        restarted.create_student("b", "另一个孩子", 10, "上海", True)
    assert service.delete("b", None, False)["requires_confirmation"]
    assert service.delete("b", "学生乙", True)["deleted"]
    with service.connection() as db:
        assert not db.execute("SELECT * FROM practice_items").fetchall()
    assert service.profile("a")["nickname"] == "学生甲"


def test_sources_in_sample_are_high_school_and_all_three_subjects_can_start(tmp_path):
    bank = load_bank()
    coach = Coach(tmp_path / "samples.db", bank)
    coach.create_student("samples", "本地验收", 12, "广东", True)
    assert {q.subject for q in bank.values()} == {"数学", "英语", "物理"}
    for subject, topic in (("数学", "math-sets"), ("英语", "english-formation"), ("物理", "physics-momentum")):
        result = coach.start("samples", subject, topic, "practice", 3, None, False)
        assert result["status"] == "ready"
        assert len(result["questions"]) == 3
        for item in result["questions"]:
            assert item["display"] == "link_only"
            assert item["source"]["paper_url"].startswith("https://")
            assert "高等学校" in item["source"]["exam"] or "高中" in item["source"]["exam"]
            assert "answer_url" not in item["source"] and "prompt" not in item
        result = coach.submit("samples", result["session_id"], [
            Response(question_id=item["question_id"], answer=bank[item["question_id"]].answers[0],
                     used_hints=False, previously_seen=False)
            for item in result["questions"]
        ], True)
        assert result["status"] == "graded"
        assert result["passed"] is True
        assert all(item["score"] == 1 for item in result["questions"])


def test_production_refuses_unlicensed_sample_bank(monkeypatch, tmp_path):
    monkeypatch.setenv("EXAM_COACH_ISSUER", ISSUER)
    monkeypatch.setenv("EXAM_COACH_RESOURCE_URL", RESOURCE)
    monkeypatch.setenv("EXAM_COACH_PUBLIC_KEY_FILE", str(tmp_path / "not-read.pem"))
    monkeypatch.delenv("EXAM_COACH_ALLOW_LINK_SAMPLES", raising=False)
    monkeypatch.delenv("EXAM_COACH_BANK", raising=False)
    with pytest.raises(ValueError, match="均有经授权的题面"):
        create_app()


def test_concurrent_creation_keeps_one_student(local):
    service, _ = local

    def create(name):
        try:
            return service.create_student("c", name, 11, "广东", True)["nickname"]
        except CoachError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(create, ["昵称一", "昵称二"]))
    assert "student_exists" in outcomes
    assert service.profile("c")["nickname"] in outcomes


def test_real_http_mcp_rejects_untrusted_tokens_and_isolates_accounts(local):
    service, _ = local
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    verifier = AccessVerifier(ISSUER, RESOURCE, public, CLIENT, require_entitlement=True)
    app = http_app(service, verifier)

    def signed(subject="alice", typ="at+jwt", **overrides):
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": RESOURCE, "exp": now + 60, "iat": now - 1, "sub": subject,
                  "client_id": CLIENT, "scope": "exam-coach", "exam_coach_entitled": True, **overrides}
        return jwt.encode(claims, private, algorithm="RS256", headers={"typ": typ})

    with TestClient(app, base_url=RESOURCE.removesuffix("/mcp")) as client:
        def post(token, method, params):
            headers = {"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-11-25"}
            if token:
                headers["authorization"] = f"Bearer {token}"
            return client.post("/mcp", headers=headers,
                               json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})

        params = {"name": "exam_get_profile", "arguments": {}}
        assert post(None, "tools/call", params).status_code == 401
        for invalid in (signed(aud="https://wrong.example.test/mcp"), signed(typ="JWT"),
                        signed(scope="other"), signed(client_id="other"), signed(exp=1),
                        signed(exam_coach_entitled=False), signed(exam_coach_entitled=1)):
            assert post(invalid, "tools/call", params).status_code == 401

        def call(token, name, args):
            response = post(token, "tools/call", {"name": name, "arguments": args})
            assert response.status_code == 200, response.text
            result = response.json()["result"]
            return result.get("structuredContent") or json.loads(result["content"][0]["text"])

        alice, bob = signed(), signed("bob")
        payload = {"nickname": "甲", "grade": 11, "region": "福建", "permission": True}
        assert call(alice, "exam_create_profile", payload)["data"]["nickname"] == "甲"
        assert call(bob, "exam_get_profile", {})["error"]["code"] == "profile_required"
        assert call(bob, "exam_create_profile", {**payload, "nickname": "乙"})["data"]["nickname"] == "乙"
        assert call(alice, "exam_get_profile", {})["data"]["nickname"] == "甲"
        questions = call(alice, "exam_start_practice",
                         {"subject": "数学", "topic_id": "math-sets", "mode": "practice", "count": 1})
        sid = questions["data"]["session_id"]
        assert call(bob, "exam_get_practice", {"session_id": sid})["error"]["code"] == "session_not_found"
        schema = post(alice, "tools/list", {}).json()["result"]["tools"]
        assert all("account" not in t["inputSchema"].get("properties", {}) for t in schema)


def test_expert_packaging_uses_distinct_identity_and_requires_https(tmp_path):
    import importlib.util
    import zipfile

    script = Path(__file__).parents[1] / "build_expert.py"
    spec = importlib.util.spec_from_file_location("coach_builder", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "coach.zip"
    with pytest.raises(ValueError):
        module.build("http://127.0.0.1/mcp", destination)
    module.build(RESOURCE, destination)
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        manifest = json.loads(archive.read(".codebuddy-plugin/plugin.json"))
        assert manifest["profession"]["zh"] == "真题查漏教练"
        assert manifest["displayName"]["zh"] == "海纳-教育专家"
        assert "dependencies" not in manifest
        config = json.loads(archive.read(".mcp.json"))["mcpServers"]["exam-gap-coach"]
        assert config["url"] == RESOURCE and config["x-workbuddy"]["auth"]["type"] == "oauth"
        assert not any("source_bank" in name for name in archive.namelist())
