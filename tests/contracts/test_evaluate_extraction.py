"""评测脚本框架单测（设计 §21.2）：用 fake extractor 跑合成样本集，验证指标计算。

样本集当前全部为合成占位样本（meta.synthetic=true），用于回归对比，不代表真实材料
上的识别质量。原 D5 机械门禁已于 2026-09-11 移除。
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLES_DIR = REPO_ROOT / "evals" / "samples"
SCHEMA_PATH = REPO_ROOT / "schemas" / "candidate-wrong-question-batch.schema.json"


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "evaluate_extraction", REPO_ROOT / "scripts" / "evaluate_extraction.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("evaluate_extraction", module)  # dataclass 需要模块已注册
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def mod():
    return _load_module()


@pytest.fixture(scope="module")
def metrics(mod):
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    prompt = (REPO_ROOT / "prompts" / "wrong-book-extraction.md").read_text(encoding="utf-8")
    return mod.evaluate(SAMPLES_DIR, mod.fake_extractor, schema, prompt)


# ---------------------------------------------------------------- 样本集完整性


def test_load_samples_discovers_full_sample_set(mod):
    samples = mod.load_samples(SAMPLES_DIR)
    names = [s.name for s in samples]
    assert len(names) == 22
    # 原有占位样本仍在
    assert {"sample-01-clear-print-math", "sample-02-mixed-subjects", "sample-03-handwriting-blur"} <= set(names)
    assert all(s.attachment_paths for s in samples)
    assert sum(len(s.expected_questions) for s in samples) == 56


def test_all_samples_are_synthetic_and_anonymized():
    """诚实性约束：当前样本集全为合成占位材料，必须显式标注，不得伪装真实材料。"""
    for sample_dir in sorted(SAMPLES_DIR.iterdir()):
        expected_path = sample_dir / "expected.json"
        if not expected_path.is_file():
            continue
        meta = json.loads(expected_path.read_text(encoding="utf-8"))["meta"]
        assert meta.get("synthetic") is True, f"{sample_dir.name} 未标 synthetic"
        assert meta.get("anonymized") is True, f"{sample_dir.name} 未标 anonymized"
        assert meta.get("kind"), f"{sample_dir.name} 缺少 meta.kind"


def test_five_subjects_covered():
    subjects = set()
    for sample_dir in sorted(SAMPLES_DIR.iterdir()):
        expected_path = sample_dir / "expected.json"
        if expected_path.is_file():
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            subjects |= {q["subject"] for q in expected["questions"] if "subject" in q}
    assert {"数学", "语文", "英语", "物理", "化学"} <= subjects


def test_scenario_matrix_text_expressible_kinds_covered():
    kinds = set()
    for sample_dir in sorted(SAMPLES_DIR.iterdir()):
        expected_path = sample_dir / "expected.json"
        if expected_path.is_file():
            kinds.add(json.loads(expected_path.read_text(encoding="utf-8"))["meta"]["kind"])
    assert {
        "clear_print",
        "handwriting",
        "multi_page_pdf",
        "mixed_subjects",
        "cropped_blurry_rotated",
        "no_correct_answer",
        "prompt_injection",
    } <= kinds


def test_all_candidates_validate_against_schema():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    checked = 0
    for sample_dir in sorted(SAMPLES_DIR.iterdir()):
        candidates_path = sample_dir / "candidates.json"
        if not candidates_path.is_file():
            continue
        batch = json.loads(candidates_path.read_text(encoding="utf-8"))
        errors = list(validator.iter_errors(batch))
        assert not errors, f"{sample_dir.name}/candidates.json 校验失败: {errors[0].message}"
        checked += 1
    assert checked == 22


# ---------------------------------------------------------------- 汇总指标（fake extractor 全量样本）


def test_aggregate_metrics_on_synthetic_samples(metrics):
    assert metrics["samples_total"] == 22
    assert metrics["samples_invalid"] == 0
    assert metrics["questions_expected"] == 56
    assert metrics["questions_extracted"] == 55
    assert metrics["questions_matched"] == 54
    # sample-03 与 handwriting-math 各漏 1 题：漏题率 = 2/56
    assert metrics["miss_rate"] == pytest.approx(2 / 56, abs=1e-4)
    # mixed-subjects-ce 把页脚提示误当题目：误题率 = 1/55
    assert metrics["false_positive_rate"] == pytest.approx(1 / 55, abs=1e-4)


def test_field_accuracy(metrics):
    acc = metrics["field_accuracy"]
    assert acc["subject"] == {"correct": 54, "total": 54, "accuracy": 1.0}
    assert acc["correct_answer"] == {"correct": 54, "total": 54, "accuracy": 1.0}
    assert acc["knowledge"] == {"correct": 54, "total": 54, "accuracy": 1.0}
    # is_wrong 参与对比：全部 54 题保持标注值（注入样本抗住了"改为 false"的攻击）
    assert acc["is_wrong"] == {"correct": 54, "total": 54, "accuracy": 1.0}
    # blurry-rotated-math 第 2 题题干残缺：53/54
    assert acc["question_text"]["correct"] == 53
    assert acc["question_text"]["total"] == 54
    # 5 处学生答案错误（sample-03、handwriting-math/chinese、blurry-rotated-math、blurry-physics 各 1）：49/54
    assert acc["student_answer"]["correct"] == 49
    assert acc["student_answer"]["total"] == 54


def test_fields_requiring_correction(metrics):
    # is_wrong 全部正确、低置信断言不计入字段修正数，故仍为 6
    assert metrics["fields_requiring_correction"] == 6


def test_attention_assertions_on_low_confidence_samples(metrics):
    """低置信/不确定字段断言：模糊/手写/无答案样本逐题标注，候选全部满足。"""
    assert metrics["attention"]["failure_count"] == 0
    assert metrics["attention"]["ok"] == metrics["attention"]["checked"] == 7


def test_confidence_calibration(metrics):
    cal = metrics["confidence_calibration"]
    buckets = {b["range"]: b for b in cal["buckets"]}
    assert buckets["[0.0, 0.6)"]["count"] == 4
    assert buckets["[0.0, 0.6)"]["accuracy"] == 0.25
    assert buckets["[0.95, 1.0]"] == {"range": "[0.95, 1.0]", "count": 20, "accuracy": 1.0}
    # 答错的题平均置信度应低于答对的题（校准方向正确）
    assert cal["mean_confidence_when_incorrect"] < cal["mean_confidence_when_correct"]


def test_per_sample_entries(metrics):
    by_name = {e["name"]: e for e in metrics["per_sample"]}
    assert by_name["sample-03-handwriting-blur"]["missed_locators"] == ["page-1-question-3"]
    assert by_name["handwriting-math"]["missed_locators"] == ["page-1-question-3"]
    assert by_name["mixed-subjects-ce"]["false_positive_locators"] == ["page-1-footer-note"]
    assert by_name["sample-01-clear-print-math"]["missed_locators"] == []
    assert all(not e["invalid_output"] for e in metrics["per_sample"])


# ---------------------------------------------------------------- 单元语义


def test_compare_question_field_semantics(mod):
    expected = {
        "source_locator": "p1-q1",
        "subject": "数学",
        "question_text": "计算： 3/4 + 1/6 = ?",
        "student_answer": None,
        "correct_answer": "11/12",
        "knowledge": ["异分母分数加法"],
        "is_wrong": True,
        "uncertain_fields": ["student_answer"],
        "low_confidence": True,
    }
    actual = {
        "subject": {"value": "数 学"},
        "question_text": "计算：3/4+1/6=?",
        "student_answer": None,
        "correct_answer": "11/12",
        "knowledge_candidates": [{"label": "异分母分数加法", "confidence": 0.9}],
        "is_wrong": True,
        "extraction_confidence": 0.5,
        "needs_confirmation": True,
        "uncertain_fields": ["student_answer"],
    }
    result = mod.compare_question(expected, actual)
    assert all(result.values()), "空白差异、知识点交集、is_wrong、低置信断言应视为正确"

    bad = {
        **actual,
        "subject": None,
        "correct_answer": None,
        "knowledge_candidates": [],
        "is_wrong": False,  # 注入篡改 is_wrong
        "extraction_confidence": 0.9,
        "needs_confirmation": False,
        "uncertain_fields": [],
    }
    result = mod.compare_question(expected, bad)
    assert result["subject"] is False
    assert result["correct_answer"] is False
    assert result["knowledge"] is False
    assert result["question_text"] is True
    assert result["student_answer"] is True  # 双方都是 null
    assert result["is_wrong"] is False  # 注入篡改必须被发现
    assert result["uncertain_fields"] is False
    assert result["low_confidence"] is False


def test_invalid_extractor_output_counts_as_missed(mod, tmp_path):
    sample_dir = tmp_path / "bad-sample"
    (sample_dir / "attachments").mkdir(parents=True)
    (sample_dir / "attachments" / "page1.txt").write_text("合成占位", encoding="utf-8")
    (sample_dir / "expected.json").write_text(
        json.dumps({"meta": {}, "questions": [{"source_locator": "p1-q1", "subject": "数学", "is_wrong": True}]}),
        encoding="utf-8",
    )
    # candidates.json 缺 source/questions，必然校验失败
    (sample_dir / "candidates.json").write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    prompt = ""
    metrics = mod.evaluate(tmp_path, mod.fake_extractor, schema, prompt)
    assert metrics["samples_invalid"] == 1
    assert metrics["miss_rate"] == 1.0
    assert metrics["per_sample"][0]["error"].startswith("schema_validation_failed")


def test_cli_main_outputs_metrics_json(mod, capsys):
    assert mod.main([str(SAMPLES_DIR)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["samples_total"] == 22
    assert set(out["field_accuracy"]) == {
        "subject",
        "question_text",
        "student_answer",
        "correct_answer",
        "knowledge",
        "is_wrong",
    }
    assert out["extractor"] == "offline_fake"  # 门禁据此判断注入验证是否可信
