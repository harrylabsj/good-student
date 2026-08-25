import json

from conftest import ingest_and_confirm, make_question, make_student


def get_weakness(result, name):
    matches = [
        w for w in result["weaknesses"] if w["knowledge_component"]["canonical_name"] == name
    ]
    assert matches, f"未找到知识点 {name}：{[w['knowledge_component']['canonical_name'] for w in result['weaknesses']]}"
    return matches[0]


def test_single_error_is_suspected_low_confidence(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    w = get_weakness(service.analyze(student)["data"], "异分母分数加法")
    assert w["status"] == "suspected_weakness"
    assert w["risk_level"] == "low"
    assert w["confidence_level"] == "low"  # 少于两条证据必须低置信
    assert w["reason_codes"] == ["single_error"]
    assert w["evidence_count"] == 1
    assert w["evidence"][0]["confirmed"] is True


def test_repeated_across_sources_raises_risk(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(locator="a")], source_ref="source-a")
    ingest_and_confirm(service, student, [make_question(locator="b")], source_ref="source-b")
    w = get_weakness(service.analyze(student)["data"], "异分母分数加法")
    assert w["risk_level"] == "medium"
    assert w["confidence_level"] == "medium"
    assert "repeated_across_sources" in w["reason_codes"]
    assert w["evidence_count"] == 2


def test_recurrence_after_correction_is_strong_signal(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [make_question(locator="a")],
        source_ref="source-a",
        edits_by_index={0: {"correction_status": "corrected", "attempted_at": "2026-08-10T10:00:00"}},
    )
    ingest_and_confirm(service, student, [make_question(locator="b")], source_ref="source-b")
    w = get_weakness(service.analyze(student)["data"], "异分母分数加法")
    assert w["risk_level"] == "high"
    assert "recurrence_after_correction" in w["reason_codes"]


def test_failed_independent_reassessment_gives_evidenced(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    links = service.store.attempt_kc_rows(student)
    kc_id = links[0]["kc_id"]

    resp = service.record_reassessment(student, kc_id, correct_count=1, total_count=5)
    assert resp["ok"]
    assert resp["data"]["passed"] is False
    w = resp["data"]["updated_weakness"]
    assert w["status"] == "evidenced_weakness"
    assert w["risk_level"] == "high"
    assert "failed_independent_reassessment" in w["reason_codes"]


def test_passed_reassessment_gives_improving_then_mastered(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]

    first = service.record_reassessment(
        student, kc_id, correct_count=4, total_count=5, completed_at="2026-08-20T10:00:00"
    )
    assert first["data"]["updated_weakness"]["status"] == "improving"

    second = service.record_reassessment(
        student, kc_id, correct_count=5, total_count=5, completed_at="2026-08-21T10:00:00"
    )
    w = second["data"]["updated_weakness"]
    assert w["status"] == "mastered"
    assert w["next_review_at"] == "2026-09-04T10:00:00+00:00"  # 08-21 + 14 天


def test_mastered_decays_to_review_due(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]
    service.record_reassessment(
        student, kc_id, correct_count=5, total_count=5, completed_at="2026-07-20T10:00:00"
    )
    resp = service.record_reassessment(
        student, kc_id, correct_count=5, total_count=5, completed_at="2026-07-21T10:00:00"
    )
    w = resp["data"]["updated_weakness"]
    assert w["status"] == "review_due"
    assert "review_due_by_decay" in w["reason_codes"]


def test_hinted_or_same_variant_reassessment_does_not_advance(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    kc_id = service.store.attempt_kc_rows(student)[0]["kc_id"]
    resp = service.record_reassessment(
        student, kc_id, correct_count=5, total_count=5, no_hints=False
    )
    assert resp["data"]["updated_weakness"]["status"] == "suspected_weakness"


def test_future_attempted_at_excluded_from_window(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [make_question(locator="future")],
        edits_by_index={0: {"attempted_at": "2030-01-01T00:00:00"}},
    )
    resp = service.analyze(student)
    assert any("未来日期" in w for w in resp["warnings"])
    assert resp["data"]["weaknesses"] == []
    # 数据仍在，但未进入当前窗口
    assert len(service.store.attempts_for_student(student)) == 1


def test_no_mastery_rate_anywhere_in_output(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [make_question(locator="a"), make_question(locator="b", kc="分数应用题")],
    )
    result = service.analyze(student)["data"]
    assert "掌握率" in result["caveats"][0] or any("掌握率" in c for c in result["caveats"])
    assert "mastery" not in json.dumps(result, ensure_ascii=False)


def test_error_concentration_in_subject_overview(service):
    student = make_student(service)
    ingest_and_confirm(
        service,
        student,
        [
            make_question(locator="a", kc="异分母分数加法"),
            make_question(locator="b", kc="分数应用题"),
        ],
    )
    result = service.analyze(student)["data"]
    math_overview = next(s for s in result["subjects"] if s["subject_id"] == "数学")
    assert math_overview["wrong_attempt_count"] == 2
    concentrations = {k["canonical_name"]: k["concentration"] for k in math_overview["knowledge_components"]}
    assert concentrations["异分母分数加法"] == 0.5
    assert concentrations["分数应用题"] == 0.5
    assert "不是掌握率" in math_overview["note"]


def test_plan_completion_does_not_fabricate_mastery(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    before = service.analyze(student)["data"]
    plan = service.create_plan(student)["data"]
    assert plan["actions"]

    for action in plan["actions"]:
        service.store.update_action_status(action["id"], "completed")

    after = service.analyze(student)["data"]
    before_w = get_weakness(before, "异分母分数加法")
    after_w = get_weakness(after, "异分母分数加法")
    assert after_w["status"] == before_w["status"] == "suspected_weakness"


def test_evidence_traceability(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()], source_ref="试卷-期中")
    w = get_weakness(service.analyze(student)["data"], "异分母分数加法")
    ev = w["evidence"][0]
    assert ev["source_ref"] == "试卷-期中"
    assert ev["attempted_at"]
    assert ev["confirmed"] is True
    assert ev["is_correct"] is False


def test_snapshot_persisted_and_replaced(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question()])
    service.analyze(student)
    snapshots = service.store.current_snapshots(student)
    assert len(snapshots) == 1
    assert snapshots[0]["status"] == "suspected_weakness"

    kc_id = snapshots[0]["kc_id"]
    service.record_reassessment(student, kc_id, correct_count=4, total_count=5)
    snapshots = service.store.current_snapshots(student)
    assert len(snapshots) == 1  # 旧快照被替换而不是堆积
    assert snapshots[0]["status"] == "improving"
