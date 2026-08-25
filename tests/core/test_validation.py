import copy

from conftest import make_batch, make_question
from good_student import validation


def valid_batch():
    return make_batch([make_question()])


def test_valid_batch_passes():
    errors, warnings = validation.validate_candidate_batch(valid_batch())
    assert errors == []
    assert warnings == []


def test_missing_required_fields():
    batch = valid_batch()
    del batch["source"]
    errors, _ = validation.validate_candidate_batch(batch)
    assert any("source" in e for e in errors)


def test_empty_questions_rejected():
    batch = valid_batch()
    batch["questions"] = []
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_nil_question_text_rejected():
    batch = valid_batch()
    batch["questions"][0]["question_text"] = None
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_overlong_text_rejected():
    batch = valid_batch()
    batch["questions"][0]["question_text"] = "长" * 20001
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_wrong_types_rejected():
    batch = valid_batch()
    batch["schema_version"] = "1"
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors

    batch = valid_batch()
    batch["questions"][0]["extraction_confidence"] = "high"
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors

    batch = valid_batch()
    batch["questions"][0]["is_wrong"] = "yes"
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_nan_infinity_rejected():
    batch = valid_batch()
    batch["questions"][0]["extraction_confidence"] = float("nan")
    errors, _ = validation.validate_candidate_batch(batch)
    assert any("non-finite" in e for e in errors)  # NaN 比较恒 False，schema 拦不住，靠语义检查

    batch = valid_batch()
    batch["questions"][0]["subject"]["confidence"] = float("inf")
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors  # inf 触发 maximum 校验，同样被拒绝


def test_confidence_out_of_range_rejected():
    batch = valid_batch()
    batch["questions"][0]["extraction_confidence"] = 1.5
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_bad_error_reason_enum_rejected():
    batch = valid_batch()
    batch["questions"][0]["error_reason_candidates"] = [{"code": "lazy"}]
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_additional_properties_rejected():
    batch = valid_batch()
    batch["questions"][0]["mastery_rate"] = 0.9
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_bad_uncertain_field_rejected():
    batch = valid_batch()
    batch["questions"][0]["uncertain_fields"] = ["nonsense"]
    errors, _ = validation.validate_candidate_batch(batch)
    assert errors


def test_low_confidence_without_confirmation_flag_warns():
    batch = valid_batch()
    batch["questions"][0]["extraction_confidence"] = 0.4
    batch["questions"][0]["needs_confirmation"] = False
    errors, warnings = validation.validate_candidate_batch(batch)
    assert errors == []
    assert any("needs_confirmation" in w for w in warnings)


def test_missing_subject_and_knowledge_warn():
    batch = make_batch([make_question(subject=None, kc=None)])
    errors, warnings = validation.validate_candidate_batch(batch)
    assert errors == []
    assert any("缺少科目" in w for w in warnings)
    assert any("知识点" in w for w in warnings)


def test_batch_is_not_mutated():
    batch = valid_batch()
    snapshot = copy.deepcopy(batch)
    validation.validate_candidate_batch(batch)
    assert batch == snapshot
