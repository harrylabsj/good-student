import pytest
from good_student.service import Service


@pytest.fixture
def service(tmp_path):
    svc = Service(tmp_path)
    yield svc
    svc.close()


def make_student(service, name="小明", grade="五年级"):
    resp = service.create_student(name, grade=grade)
    assert resp["ok"], resp
    return resp["data"]["student"]["id"]


def make_question(
    locator="page-1-question-1",
    subject="数学",
    kc="异分母分数加法",
    reason="concept_gap",
    is_wrong=True,
    text=None,
):
    question = {
        "source_locator": locator,
        "grade": {"value": "五年级", "confidence": 0.7},
        "question_text": text or f"[{locator}] 计算：3/4 + 1/6 = ?",
        "student_answer": "4/10" if is_wrong else "11/12",
        "correct_answer": "11/12",
        "is_wrong": is_wrong,
        "knowledge_candidates": [{"label": kc, "confidence": 0.85}] if kc else [],
        "error_reason_candidates": [{"code": reason, "confidence": 0.7}] if reason else [],
        "extraction_confidence": 0.9,
        "needs_confirmation": True,
        "uncertain_fields": [],
    }
    if subject is not None:
        question["subject"] = {"value": subject, "confidence": 0.95}
    return question


def make_batch(questions, source_ref="test-source", source_type="image", page_count=1):
    return {
        "schema_version": 1,
        "source": {"source_type": source_type, "source_ref": source_ref, "page_count": page_count},
        "questions": questions,
        "warnings": [],
    }


def ingest_and_confirm(service, student_id, questions, source_ref="test-source", edits_by_index=None):
    resp = service.ingest_candidates(student_id, make_batch(questions, source_ref=source_ref))
    assert resp["ok"], resp
    pending = service.list_pending(student_id)["data"]["pending"]
    items = []
    for i, p in enumerate(pending):
        item = {"candidate_id": p["candidate_id"], "action": "confirm"}
        if edits_by_index and i in edits_by_index:
            item["edits"] = edits_by_index[i]
        items.append(item)
    resp = service.confirm_questions(student_id, items)
    assert resp["ok"] and not resp["data"]["failed"], resp
    return resp["data"]["applied"]
