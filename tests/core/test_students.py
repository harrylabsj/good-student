from conftest import ingest_and_confirm, make_question


def test_same_display_name_students_are_isolated(service):
    id_a = service.create_student("小明")["data"]["student"]["id"]
    id_b = service.create_student("小明")["data"]["student"]["id"]
    assert id_a != id_b  # UUID 主键，同名不同人

    ingest_and_confirm(service, id_a, [make_question()])
    result_b = service.analyze(id_b)["data"]
    assert result_b["weaknesses"] == []
    result_a = service.analyze(id_a)["data"]
    assert len(result_a["weaknesses"]) == 1


def test_export_contains_full_loop_data(service):
    student = service.create_student("小红", grade="四年级")["data"]["student"]["id"]
    ingest_and_confirm(service, student, [make_question()])
    service.analyze(student)
    service.create_plan(student)

    exported = service.export_student(student)["data"]
    assert exported["student"]["display_name"] == "小红"
    assert len(exported["attempts"]) == 1
    assert exported["attempts"][0]["knowledge_components"][0]["canonical_name"] == "异分母分数加法"
    assert len(exported["sources"]) == 1
    assert exported["snapshots"] and exported["learning_actions"]
    assert exported["format_version"] == 1


def test_delete_requires_two_step_confirmation(service):
    student = service.create_student("小刚")["data"]["student"]["id"]
    ingest_and_confirm(service, student, [make_question()])

    first = service.delete_student(student)
    assert first["ok"]
    assert first["data"]["confirmation_required"] is True
    assert first["data"]["scope"]["will_delete"]["attempts"] == 1
    assert service.analyze(student)["ok"]  # 尚未删除

    wrong = service.delete_student(student, confirm_phrase="别人")
    assert not wrong["ok"]
    assert wrong["error"]["code"] == "confirm_phrase_mismatch"
    assert service.analyze(student)["ok"]

    ok = service.delete_student(student, confirm_phrase="小刚")
    assert ok["ok"] and ok["data"]["deleted"] is True
    assert ok["data"]["removed"]["attempts"] == 1

    gone = service.export_student(student)
    assert not gone["ok"]
    assert gone["error"]["code"] == "not_found"
    assert service.store.list_students() == []


def test_create_student_idempotency(service):
    r1 = service.create_student("小丽", idempotency_key="k1")
    r2 = service.create_student("小丽", idempotency_key="k1")
    assert r1["data"]["student"]["id"] == r2["data"]["student"]["id"]
    assert "idempotent_replay" in r2["warnings"]
    assert len(service.store.list_students()) == 1


def test_create_student_validates_name(service):
    resp = service.create_student("   ")
    assert not resp["ok"]
    assert resp["error"]["code"] == "invalid_argument"
