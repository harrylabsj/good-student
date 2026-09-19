"""record_reassessment 的参数校验与 now 归一化回归。"""

from conftest import ingest_and_confirm, make_question, make_student


def _setup_weakness(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]
    return student, kc_id


def test_record_reassessment_rejects_foreign_action_id(service):
    student_a, kc_a = _setup_weakness(service)
    student_b, _ = _setup_weakness(service)
    plan_b = service.create_plan(student_b)
    assert plan_b["data"]["actions"], plan_b
    foreign_action_id = plan_b["data"]["actions"][0]["id"]

    resp = service.record_reassessment(student_a, kc_a, 1, 1, action_id=foreign_action_id)
    assert not resp["ok"]
    assert resp["error"]["code"] == "not_found"
    assert service.store.reassessments_for_student(student_a) == []


def test_record_reassessment_accepts_own_action_id(service):
    student, kc_id = _setup_weakness(service)
    plan = service.create_plan(student)
    assert plan["data"]["actions"], plan
    own_action_id = plan["data"]["actions"][0]["id"]

    resp = service.record_reassessment(student, kc_id, 1, 1, action_id=own_action_id)
    assert resp["ok"], resp
    assert resp["data"]["reassessment"]["action_id"] == own_action_id


def test_record_reassessment_time_seconds_type_checked(service):
    student, kc_id = _setup_weakness(service)
    resp = service.record_reassessment(student, kc_id, 1, 1, time_seconds="90")
    assert not resp["ok"]
    assert resp["error"]["code"] == "invalid_argument"

    resp_bool = service.record_reassessment(student, kc_id, 1, 1, time_seconds=True)
    assert not resp_bool["ok"]
    assert resp_bool["error"]["code"] == "invalid_argument"


def test_analyze_normalizes_now_with_offset(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    resp = service.analyze(student, now="2026-09-19T08:00:00+08:00")
    assert resp["ok"]
    assert resp["data"]["generated_at"] == "2026-09-19T00:00:00+00:00"


def test_analyze_rejects_invalid_now(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    resp = service.analyze(student, now="not-a-time")
    assert not resp["ok"]
    assert resp["error"]["code"] == "invalid_argument"
