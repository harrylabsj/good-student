from conftest import make_batch, make_question, make_student
from good_student.validation import validate


def assert_envelope(resp):
    errors = validate(resp, "tool-response")
    assert errors == [], errors


def test_all_responses_use_envelope(service):
    student = make_student(service)
    assert_envelope(service.capabilities())
    assert_envelope(service.doctor())
    assert_envelope(service.list_pending(student))
    assert_envelope(service.analyze(student))
    assert_envelope(service.export_student(student))
    assert_envelope(service.delete_student(student, confirm_phrase="小明"))
    assert_envelope(service.ingest_candidates(student, {"bad": 1}))  # 错误响应也走 envelope
    assert_envelope(service.analyze("nope"))


def test_ingest_idempotency_does_not_duplicate(service):
    student = make_student(service)
    batch = make_batch([make_question()])
    r1 = service.ingest_candidates(student, batch, idempotency_key="ing-1")
    r2 = service.ingest_candidates(student, batch, idempotency_key="ing-1")
    assert r1["data"]["batch_id"] == r2["data"]["batch_id"]
    assert "idempotent_replay" in r2["warnings"]
    assert len(service.list_pending(student)["data"]["pending"]) == 1


def test_confirm_idempotency_does_not_duplicate_attempts(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    items = [
        {"candidate_id": p["candidate_id"], "action": "confirm"}
        for p in service.list_pending(student)["data"]["pending"]
    ]
    r1 = service.confirm_questions(student, items, idempotency_key="cf-1")
    r2 = service.confirm_questions(student, items, idempotency_key="cf-1")
    assert r1["data"]["applied"][0]["attempt_id"] == r2["data"]["applied"][0]["attempt_id"]
    assert len(service.store.attempts_for_student(student)) == 1


def test_error_envelope_shape(service):
    resp = service.analyze("nope")
    assert resp["ok"] is False
    assert resp["data"] is None
    assert resp["error"]["code"] == "not_found"
    assert resp["trace_id"]
