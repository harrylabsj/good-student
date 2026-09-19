import pytest
from conftest import ingest_and_confirm, make_batch, make_question, make_student
from good_student.validation import validate


def assert_envelope(resp):
    errors = validate(resp, "tool-response")
    assert errors == [], errors


def test_all_responses_use_envelope(service):
    student = make_student(service)
    assert_envelope(service.capabilities())
    assert_envelope(service.list_students())
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


# ---- M2：envelope 兜底内部异常 + 事务任意异常回滚 ----


def test_internal_error_returns_envelope(service, monkeypatch):
    """M2：未知内部异常降级为 internal_error envelope，不击穿宿主工具循环。"""
    import good_student.service as svc_mod

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(svc_mod.analysis, "analyze_student", boom)
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    resp = service.analyze(student)
    assert_envelope(resp)
    assert resp["ok"] is False
    assert resp["error"]["code"] == "internal_error"
    assert "boom" in resp["error"]["message"]


def test_tx_rolls_back_on_generic_exception(service):
    """M2：事务内任意异常都回滚，不残留未提交写入污染后续操作。"""
    with pytest.raises(ValueError):
        with service.store.tx():
            student = service.store.insert_student("回滚测试", "五年级", [], [])
            raise ValueError("boom")
    assert service.store.get_student(student["id"]) is None
    # 后续事务仍可用
    resp = service.create_student("小明", grade="五年级")
    assert resp["ok"]


def test_self_reported_confidence_type_checked(service):
    """M2：self_reported_confidence 传字符串应给 invalid_argument，而非 internal_error。"""
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]
    resp = service.record_reassessment(
        student, kc_id, correct_count=1, total_count=1, self_reported_confidence="0.5"
    )
    assert resp["ok"] is False
    assert resp["error"]["code"] == "invalid_argument"


def test_idempotency_records_pruned_after_ttl(service):
    """幂等表按 TTL 清理：过期键不再占库，新键写入不受影响。"""
    from good_student import clock
    from good_student.models import IDEMPOTENCY_TTL_DAYS

    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]), idempotency_key="old-key")
    with service.store.tx():
        service.store._conn.execute(
            "UPDATE idempotency SET created_at = ? WHERE key = 'old-key'",
            (clock.add_days(clock.iso(), -(IDEMPOTENCY_TTL_DAYS + 1)),),
        )
    service.ingest_candidates(
        student, make_batch([make_question(locator="p1-q2")]), idempotency_key="new-key"
    )
    keys = {r["key"] for r in service.store._conn.execute("SELECT key FROM idempotency").fetchall()}
    assert "old-key" not in keys
    assert "new-key" in keys
