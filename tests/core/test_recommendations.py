from conftest import ingest_and_confirm, make_question, make_student

ACTION_REQUIRED_KEYS = {
    "id",
    "kc_id",
    "kc_name",
    "subject_id",
    "error_reason",
    "why",
    "action",
    "duration_minutes",
    "question_count",
    "due_date",
    "reassessment_method",
    "acceptance_criteria",
    "next_step_if_fail",
    "status",
}


def test_plan_for_each_error_reason_template(service):
    student = make_student(service)
    questions = [
        make_question(locator="1", kc="概念不清点", reason="concept_gap"),
        make_question(locator="2", kc="步骤错误点", reason="procedure_gap"),
        make_question(locator="3", kc="审题错误点", reason="misread"),
        make_question(locator="4", kc="计算错误点", reason="calculation"),
        make_question(locator="5", kc="记忆遗忘点", reason="memory"),
        make_question(locator="6", kc="检查不足点", reason="careless_checking"),
    ]
    ingest_and_confirm(service, student, questions)
    plan = service.create_plan(student)
    assert plan["ok"], plan
    actions = plan["data"]["actions"]
    assert len(actions) == 6
    for action in actions:
        assert ACTION_REQUIRED_KEYS <= set(action)
        assert action["acceptance_criteria"]
        assert action["next_step_if_fail"]
        assert action["due_date"] > "2026-01-01"  # 是合法的未来 ISO 日期


def test_unknown_reason_is_skipped_not_invented(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(reason=None)])
    plan = service.create_plan(student)["data"]
    assert plan["actions"] == []
    assert len(plan["skipped"]) == 1
    assert plan["skipped"][0]["reason"] == "error_reason_unknown"


def test_improving_and_mastered_get_no_actions(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]
    service.record_reassessment(student, kc_id, correct_count=5, total_count=5)
    plan = service.create_plan(student)["data"]
    assert plan["actions"] == []
    assert plan["skipped"] == []


def test_actions_persisted_with_active_status(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    service.create_plan(student)
    rows = service.store.actions_for_student(student)
    assert len(rows) == 1
    assert rows[0]["status"] == "active"
    assert rows[0]["error_reason"] == "concept_gap"


def test_reassessment_validation(service):
    student = make_student(service)
    resp = service.record_reassessment(student, "kc-nope", 1, 1)
    assert resp["error"]["code"] == "not_found"

    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]
    resp = service.record_reassessment(student, kc_id, 6, 5)
    assert resp["error"]["code"] == "invalid_argument"
    resp = service.record_reassessment(student, kc_id, -1, 5)
    assert resp["error"]["code"] == "invalid_argument"
