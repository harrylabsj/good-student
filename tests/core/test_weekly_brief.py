"""每周家长简报与 memory 间隔复习排期。"""

from conftest import ingest_and_confirm, make_question, make_student


def test_weekly_brief_counts_and_structure(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [make_question(locator="a"), make_question(locator="b", subject="英语", kc="时态")],
    )
    service.create_plan(student)

    resp = service.weekly_brief(student)
    assert resp["ok"], resp
    brief = resp["data"]
    assert brief["new_wrong_count"] == 2
    assert brief["new_wrong_by_subject"] == {"数学": 1, "英语": 1}
    assert brief["weakness_summary"]["total_weaknesses"] == 2
    assert len(brief["active_actions"]) >= 1
    assert brief["window"]["from"] < brief["window"]["to"]
    assert "掌握率" in brief["note"]


def test_weekly_brief_includes_reassessment(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    analysis = service.analyze(student)["data"]
    kc_id = analysis["weaknesses"][0]["knowledge_component"]["id"]
    resp = service.record_reassessment(student, kc_id, correct_count=4, total_count=5)
    assert resp["ok"], resp

    brief = service.weekly_brief(student)["data"]
    assert brief["reassessments_this_week"]["count"] == 1
    assert brief["reassessments_this_week"]["passed"] == 1


def test_memory_reason_gets_spaced_review_schedule(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(reason="memory")])
    plan = service.create_plan(student)["data"]
    assert plan["actions"], plan
    action = plan["actions"][0]
    assert action["error_reason"] == "memory"
    assert len(action["review_schedule"]) == 4
    assert action["review_schedule"] == sorted(action["review_schedule"])


def test_plan_action_carries_prerequisite_hint(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(kc="分数应用题")], source_ref="s1")
    ingest_and_confirm(
        service,
        student,
        [make_question(locator="p2", kc="分数运算", text="计算：1/2 + 1/3 = ?")],
        source_ref="s2",
    )
    plan = service.create_plan(student)["data"]
    hint_actions = [a for a in plan["actions"] if a.get("prerequisite_hint")]
    assert hint_actions, plan
    assert any("分数运算" in a["prerequisite_hint"] for a in hint_actions)


def test_weekly_brief_unknown_student(service):
    resp = service.weekly_brief("no-such-student")
    assert not resp["ok"]
    assert resp["error"]["code"] == "not_found"
