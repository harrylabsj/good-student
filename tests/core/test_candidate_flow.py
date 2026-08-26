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
    service.ingest_candidates(student, make_batch([make_question()]))
    # 直接把候选置为已过期（模拟 24h 未确认），不依赖负 ttl（L7 已禁止负值）
    with service.store.tx():
        service.store._conn.execute(
            "UPDATE candidates SET expires_at = '2000-01-01T00:00:00+00:00' WHERE student_id = ?",
            (student,),
        )
    listing = service.list_pending(student)
    assert listing["data"]["pending"] == []
    assert listing["data"]["expired_now"] == 1

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


# ---- M3：confirm edits 类型校验（宿主模型传候选结构 dict 时给明确错误）----


def test_confirm_edits_subject_dict_returns_invalid_argument(service):
    """M3：edits.subject 按候选 payload 的 {value,confidence} 结构传 dict 时，
    应给 invalid_argument，而不是误导性的 persistence_error。"""
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))  # 先入库不确认，留下 pending
    pending = service.list_pending(student)["data"]["pending"]
    resp = service.confirm_questions(
        student,
        [{"candidate_id": pending[0]["candidate_id"], "action": "confirm", "edits": {"subject": {"value": "数学"}}}],
    )
    assert resp["ok"] is True  # 单条失败走 data.failed，不炸 envelope
    assert resp["data"]["applied"] == []
    assert len(resp["data"]["failed"]) == 1
    assert resp["data"]["failed"][0]["code"] == "invalid_argument"
    assert "字符串" in resp["data"]["failed"][0]["message"]


def test_confirm_edits_attempted_at_weird_type_fails_cleanly(service):
    student = make_student(service)
    service.ingest_candidates(student, make_batch([make_question()]))
    pending = service.list_pending(student)["data"]["pending"]
    resp = service.confirm_questions(
        student,
        [{"candidate_id": pending[0]["candidate_id"], "action": "confirm", "edits": {"attempted_at": 12345}}],
    )
    assert resp["ok"] is True
    assert len(resp["data"]["failed"]) == 1
    assert resp["data"]["failed"][0]["code"] == "invalid_argument"


def test_ingest_ttl_hours_out_of_range_rejected(service):
    """L7：ttl_hours 越界（0 / 超长）应给 invalid_argument，而不是预过期/永不过期。"""
    student = make_student(service)
    resp = service.ingest_candidates(student, make_batch([make_question(locator="q1")]), ttl_hours=0)
    assert resp["ok"] is False
    assert resp["error"]["code"] == "invalid_argument"
    resp2 = service.ingest_candidates(student, make_batch([make_question(locator="q2")]), ttl_hours=24 * 365 + 1)
    assert resp2["ok"] is False
    assert resp2["error"]["code"] == "invalid_argument"
