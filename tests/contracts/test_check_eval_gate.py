"""D5 门禁脚本单测（scripts/check_eval_gate.py）：逐条规则的达标/不达标判定。"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location("check_eval_gate", REPO_ROOT / "scripts" / "check_eval_gate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("check_eval_gate", module)  # dataclass 需要模块已注册
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load_module()


@pytest.fixture
def config(gate):
    """缩小门槛以便构造小型测试样本集。"""
    return gate.GateConfig(min_questions=2, required_subjects=("数学", "语文"))


def write_sample(
    root: Path,
    name: str,
    kind: str,
    subjects: list[str],
    synthetic: bool,
    attention_q: dict | None = None,
) -> None:
    sample_dir = root / name
    (sample_dir / "attachments").mkdir(parents=True)
    (sample_dir / "attachments" / "page1.txt").write_text("测试材料", encoding="utf-8")
    questions = [
        {"source_locator": f"page-1-question-{i + 1}", "subject": subject, "is_wrong": True}
        for i, subject in enumerate(subjects)
    ]
    if attention_q:
        for q in questions:
            q.update(attention_q)
    expected = {
        "meta": {"synthetic": synthetic, "anonymized": True, "kind": kind, "subjects": subjects},
        "questions": questions,
    }
    (sample_dir / "expected.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")


def make_metrics(per_sample: list[dict]) -> dict:
    return {"per_sample": per_sample}


def entry(
    name: str,
    expected: int,
    fields: dict[str, bool] | list[dict[str, bool]] | None = None,
    missed: list[str] | None = None,
    attention_ok: bool | None = None,
) -> dict:
    if fields is None:
        field_results: list[dict] = []
    elif isinstance(fields, dict):
        field_results = [
            {"source_locator": "page-1-question-1", "fields": fields, "correct": all(fields.values())}
        ]
    else:
        field_results = [
            {"source_locator": f"page-1-question-{i + 1}", "fields": f, "correct": all(f.values())}
            for i, f in enumerate(fields)
        ]
    if attention_ok is not None:
        for r in field_results:
            r["attention_checked"] = True
            r["attention_ok"] = attention_ok
    return {
        "name": name,
        "questions_expected": expected,
        "missed_locators": missed or [],
        "field_results": field_results,
    }


ALL_RIGHT = {
    f: True for f in ("subject", "question_text", "student_answer", "correct_answer", "knowledge", "is_wrong")
}


def by_name(results):
    return {r.name: r for r in results}


# ---------------------------------------------------------------- 逐条规则


def test_all_real_samples_pass(gate, config, tmp_path):
    write_sample(tmp_path, "real-clear", "clear_print", ["数学"], synthetic=False)
    write_sample(tmp_path, "real-handwriting", "handwriting", ["语文"], synthetic=False)
    # 真实低置信样本必须携带并满足 attention 断言，否则 attention_accuracy 不通过
    write_sample(
        tmp_path, "real-blurry", "cropped_blurry_rotated", ["数学"], synthetic=False,
        attention_q={"low_confidence": True, "uncertain_fields": ["student_answer"]},
    )
    metrics = make_metrics(
        [
            entry("real-clear", 1, ALL_RIGHT),
            entry("real-handwriting", 1, ALL_RIGHT),
            entry("real-blurry", 1, ALL_RIGHT, attention_ok=True),
        ]
    )
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert all(r.passed for r in results.values()), results
    assert gate.overall_pass(list(results.values()))


def test_all_synthetic_must_fail(gate, config, tmp_path):
    """全 synthetic 样本集即使指标满分也必然不达 D5。"""
    write_sample(tmp_path, "syn-clear", "clear_print", ["数学"], synthetic=True)
    write_sample(tmp_path, "syn-handwriting", "handwriting", ["语文"], synthetic=True)
    metrics = make_metrics([entry("syn-clear", 1, ALL_RIGHT), entry("syn-handwriting", 1, ALL_RIGHT)])
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert results["sample_count"].passed
    assert results["subject_coverage"].passed
    for name in ("real_material", "clear_print_accuracy", "handwriting_accuracy", "miss_rate"):
        assert not results[name].passed, name
        assert "synthetic" in results[name].detail
    assert not gate.overall_pass(list(results.values()))


def test_sample_count_below_threshold_fails(gate, tmp_path):
    config = gate.GateConfig(min_questions=50, required_subjects=("数学",))
    write_sample(tmp_path, "real-clear", "clear_print", ["数学"], synthetic=False)
    metrics = make_metrics([entry("real-clear", 1, ALL_RIGHT)])
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["sample_count"].passed


def test_missing_subject_fails_coverage(gate, config, tmp_path):
    write_sample(tmp_path, "real-clear", "clear_print", ["数学", "数学"], synthetic=False)
    metrics = make_metrics([entry("real-clear", 2, ALL_RIGHT)])
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["subject_coverage"].passed
    assert "语文" in results["subject_coverage"].detail
    assert not results["real_material"].passed  # 真实材料同样要求五科（此处两科）齐全


def test_clear_print_accuracy_below_threshold_fails(gate, config, tmp_path):
    write_sample(tmp_path, "real-clear", "clear_print", ["数学"], synthetic=False)
    write_sample(tmp_path, "real-handwriting", "handwriting", ["语文"], synthetic=False)
    fields = {**ALL_RIGHT, "student_answer": False}  # 4/5 = 0.8 < 0.90
    metrics = make_metrics([entry("real-clear", 1, fields), entry("real-handwriting", 1, ALL_RIGHT)])
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["clear_print_accuracy"].passed
    assert "student_answer" in results["clear_print_accuracy"].detail
    assert results["handwriting_accuracy"].passed


def test_handwriting_threshold_is_looser_than_clear_print(gate, config, tmp_path):
    """手写层门槛 70%：student_answer 逐字段 0.8 在手写层 pass；清晰印刷层无真实样本仍 fail。"""
    write_sample(tmp_path, "real-handwriting", "handwriting", ["数学", "语文"], synthetic=False)
    # 5 题中 1 题学生答案错误 → student_answer 字段准确率 4/5 = 0.8 ≥ 0.7
    fields = [ALL_RIGHT] * 4 + [{**ALL_RIGHT, "student_answer": False}]
    metrics = make_metrics([entry("real-handwriting", 5, fields)])
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert results["handwriting_accuracy"].passed
    assert not results["clear_print_accuracy"].passed  # 无真实清晰印刷样本


def test_handwriting_accuracy_below_threshold_fails(gate, config, tmp_path):
    """手写层逐字段准确率 < 70% 同样 fail。"""
    write_sample(tmp_path, "real-handwriting", "handwriting", ["数学", "语文"], synthetic=False)
    fields = [ALL_RIGHT] * 3 + [{**ALL_RIGHT, "student_answer": False}] * 2  # student_answer 3/5 = 0.6 < 0.7
    metrics = make_metrics([entry("real-handwriting", 5, fields)])
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["handwriting_accuracy"].passed
    assert "student_answer" in results["handwriting_accuracy"].detail


def test_miss_rate_above_threshold_fails(gate, config, tmp_path):
    write_sample(tmp_path, "real-clear", "clear_print", ["数学"], synthetic=False)
    write_sample(tmp_path, "real-handwriting", "handwriting", ["语文"], synthetic=False)
    metrics = make_metrics(
        [entry("real-clear", 1, ALL_RIGHT), entry("real-handwriting", 1, ALL_RIGHT, missed=["page-1-question-1"])]
    )
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["miss_rate"].passed
    assert "0.5000" in results["miss_rate"].detail


def test_metrics_missing_sample_fails(gate, config, tmp_path):
    """指标 JSON 与样本目录对不上时不得误判达标。"""
    write_sample(tmp_path, "real-clear", "clear_print", ["数学"], synthetic=False)
    write_sample(tmp_path, "real-handwriting", "handwriting", ["语文"], synthetic=False)
    metrics = make_metrics([entry("real-clear", 1, ALL_RIGHT)])  # 缺 real-handwriting
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["handwriting_accuracy"].passed
    assert "real-handwriting" in results["handwriting_accuracy"].detail


# ---------------------------------------------------------------- 注入验证（M6）


def test_injection_samples_under_fake_extractor_fail_gate(gate, config, tmp_path):
    """注入样本若由离线 fake extractor 评测，不得声称抗注入已覆盖。"""
    write_sample(tmp_path, "real-inj", "prompt_injection", ["数学"], synthetic=False)
    metrics = make_metrics([entry("real-inj", 1, ALL_RIGHT)])  # 无 extractor 字段 → 视为 offline_fake
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert not results["injection_validation"].passed
    assert "fake extractor" in results["injection_validation"].detail


def test_injection_samples_under_real_extractor_pass_if_is_wrong_kept(gate, config, tmp_path):
    write_sample(tmp_path, "real-inj", "prompt_injection", ["数学"], synthetic=False)
    metrics = make_metrics([entry("real-inj", 1, ALL_RIGHT)])
    metrics["extractor"] = "hermes:extract_fn"
    results = by_name(gate.check_gates(tmp_path, metrics, config))
    assert results["injection_validation"].passed
    assert "1/1" in results["injection_validation"].detail

    # 被注入篡改 is_wrong 时 must fail
    bad_fields = {**ALL_RIGHT, "is_wrong": False}
    metrics2 = make_metrics([entry("real-inj", 1, bad_fields)])
    metrics2["extractor"] = "hermes:extract_fn"
    results2 = by_name(gate.check_gates(tmp_path, metrics2, config))
    assert not results2["injection_validation"].passed
    assert "0/1" in results2["injection_validation"].detail


# ---------------------------------------------------------------- CLI


def test_cli_exit_codes(gate, config, tmp_path, capsys):
    samples = tmp_path / "samples"
    write_sample(samples, "real-clear", "clear_print", ["数学"], synthetic=False)
    write_sample(samples, "real-handwriting", "handwriting", ["语文"], synthetic=False)
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(
        json.dumps(make_metrics([entry("real-clear", 1, ALL_RIGHT), entry("real-handwriting", 1, ALL_RIGHT)])),
        encoding="utf-8",
    )

    # CLI 固定使用默认 GateConfig（50 题），此小型样本集必然未达标；验证退出码协议：未达标 1、输入错误 2
    assert gate.main([str(samples), "--metrics", str(metrics_path)]) == 1
    out = capsys.readouterr().out
    assert "[FAIL] sample_count" in out
    assert "[PASS] clear_print_accuracy" in out
    assert "总体：FAIL" in out

    assert gate.main([str(samples), "--metrics", str(tmp_path / "missing.json")]) == 2
    assert gate.main([str(tmp_path / "no-such-dir"), "--metrics", str(metrics_path)]) == 2


def test_cli_output_json(gate, tmp_path, capsys):
    samples = tmp_path / "samples"
    write_sample(samples, "syn-clear", "clear_print", ["数学"], synthetic=True)
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps(make_metrics([entry("syn-clear", 1, ALL_RIGHT)])), encoding="utf-8")
    output_path = tmp_path / "gate.json"
    assert gate.main([str(samples), "--metrics", str(metrics_path), "--output", str(output_path)]) == 1
    capsys.readouterr()
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["gate"] == "D5"
    assert payload["overall_pass"] is False
    assert {c["name"] for c in payload["checks"]} == {
        "sample_count",
        "subject_coverage",
        "real_material",
        "clear_print_accuracy",
        "handwriting_accuracy",
        "miss_rate",
        "attention_accuracy",
        "injection_validation",
    }


def test_repo_sample_set_current_gate_status(gate):
    """仓库现状：规模/覆盖 pass，真实材料相关项 fail（合成占位样本不计入 D5）。"""
    metrics = json.loads((REPO_ROOT / "evals" / "last-run.json").read_text(encoding="utf-8"))
    results = by_name(gate.check_gates(REPO_ROOT / "evals" / "samples", metrics))
    assert results["sample_count"].passed
    assert results["subject_coverage"].passed
    assert not results["real_material"].passed
    assert not gate.overall_pass(list(results.values()))
