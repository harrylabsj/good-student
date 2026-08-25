from conftest import ingest_and_confirm, make_batch, make_question, make_student


def test_ingest_and_list_pending(service):
    student = make_student(service)
    resp = service.ingest_candidates(student, make_batch([make_question(), make_question(locator="p1-q2")]))
    assert resp["ok"]
    assert resp["data"]["candidate_count"] == 2
    assert not resp["data"]["duplicate"]

    pending = service.list_pending(student)["data"]["pending"]
    assert len(pending) == 2
    first = pending[0]
    assert first["subject"] == "数学"
    assert first["attention"] is True  # needs_confirmation=True


def test_ingest_invalid_schema_writes_nothing(service):
    student = make_student(service)
    bad = make_batch([make_question()])
    bad["questions"][0]["is_wrong"] = "yes"
    resp = service.ingest_candidates(student, bad)
    assert not resp["ok"]
    assert resp["error"]["code"] == "schema_validation_failed"
    assert service.list_pending(student)["data"]["pending"] == []


def test_duplicate_ingest_returns_existing_and_diff(service):
    student = make_student(service)
    batch = make_batch([make_question()])
    service.ingest_candidates(student, batch)
    resp = service.ingest_candidates(student, batch)
    assert resp["ok"]
    assert resp["data"]["duplicate"] is True
    assert resp["data"]["existing"]["pending_count"] == 1
    assert resp["data"]["diff"]["new_questions"] == []
    assert service.list_pending(student)["data"]["pending"].__len__() == 1


def test_expired_candidates_not_pending_and_not_confirmable(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]), ttl_hours=-1)
    listing = service.list_pending(student)
    assert listing["data"]["pending"] == []
    assert listing["data"]["expired_now"] == 1

    expired_id = service.store.pending_candidates(student)  # 空
    assert expired_id == []
    row = service.store._conn.execute("SELECT id, status FROM candidates").fetchone()
    resp = service.confirm_questions(student, [{"candidate_id": row["id"], "action": "confirm"}])
    assert resp["ok"]
    assert resp["data"]["failed"][0]["code"] == "candidate_expired"
    assert service.store.attempts_for_student(student) == []


def test_rejected_candidates_never_enter_analysis(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    pending = service.list_pending(student)["data"]["pending"]
    resp = service.confirm_questions(
        student, [{"candidate_id": pending[0]["candidate_id"], "action": "reject"}]
    )
    assert resp["ok"] and resp["data"]["applied"][0]["action"] == "rejected"

    result = service.analyze(student)["data"]
    assert result["weaknesses"] == []


def test_unconfirmed_not_analyzed(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    result = service.analyze(student)["data"]
    assert result["weaknesses"] == []

    ingest_and_confirm(service, student, [make_question(locator="p1-q2")])
    result = service.analyze(student)["data"]
    assert len(result["weaknesses"]) == 1


def test_multi_subject_batch(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [
            make_question(locator="m1", subject="数学"),
            make_question(locator="c1", subject="语文", kc="近义词"),
            make_question(locator="e1", subject="英语", kc="一般过去时", reason="memory"),
        ],
    )
    result = service.analyze(student)["data"]
    subjects = {s["subject_id"] for s in result["subjects"]}
    assert subjects == {"数学", "语文", "英语"}
    assert len(result["weaknesses"]) == 3


def test_one_question_multiple_knowledge_components(service):
    student = make_student(service)
    q = make_question()
    q["knowledge_candidates"] = [
        {"label": "异分母分数加法", "confidence": 0.8},
        {"label": "分数基本性质", "confidence": 0.6},
    ]
    ingest_and_confirm(service, student, [q])
    result = service.analyze(student)["data"]
    names = {w["knowledge_component"]["canonical_name"] for w in result["weaknesses"]}
    assert names == {"异分母分数加法", "分数基本性质"}


def test_confirm_with_edits_overrides_fields(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [make_question()],
        edits_by_index={0: {"subject": "数学", "error_reason": "careless_checking"}},
    )
    attempt = service.store.attempts_for_student(student)[0]
    assert attempt["error_reason"] == "careless_checking"
    links = service.store.attempt_kc_rows(student)
    assert links[0]["attempt_id"] == attempt["id"]


def test_confirm_missing_subject_requires_edit(service):
    student = make_student(service)
    q = make_question(subject=None)  # 候选可以缺科目，但确认时必须补
    service.ingest_candidates(student, make_batch([q]))
    pending = service.list_pending(student)["data"]["pending"]

    resp = service.confirm_questions(
        student, [{"candidate_id": pending[0]["candidate_id"], "action": "confirm"}]
    )
    assert resp["data"]["failed"][0]["code"] == "subject_missing"

    resp = service.confirm_questions(
        student,
        [{"candidate_id": pending[0]["candidate_id"], "action": "confirm", "edits": {"subject": "科学"}}],
    )
    assert resp["ok"] and not resp["data"]["failed"]
    assert service.store.attempts_for_student(student)[0]["subject_id"] == "科学"


def test_confirm_rejects_unknown_edit_fields_and_bad_dates(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    cid = service.list_pending(student)["data"]["pending"][0]["candidate_id"]

    resp = service.confirm_questions(
        student, [{"candidate_id": cid, "action": "confirm", "edits": {"hack": 1}}]
    )
    assert resp["data"]["failed"][0]["code"] == "invalid_argument"

    resp = service.confirm_questions(
        student, [{"candidate_id": cid, "action": "confirm", "edits": {"attempted_at": "not-a-date"}}]
    )
    assert resp["data"]["failed"][0]["code"] == "invalid_argument"
    assert service.store.attempts_for_student(student) == []


def test_confirm_unknown_candidate_reports_failure(service):
    student = make_student(service)
    resp = service.confirm_questions(
        student, [{"candidate_id": "nope", "action": "confirm"}]
    )
    assert resp["ok"]
    assert resp["data"]["failed"][0]["code"] == "not_found"


def test_correct_question_confirmed_is_success_not_weakness(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(is_wrong=False)])
    result = service.analyze(student)["data"]
    assert result["weaknesses"] == []
