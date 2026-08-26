#!/usr/bin/env python3
"""错题提取评测脚本（设计 §21.2：Prompt/模型契约评测）。

用法：

    .venv/bin/python scripts/evaluate_extraction.py evals/samples
    .venv/bin/python scripts/evaluate_extraction.py evals/samples --extractor mymodule:extract_fn

样本目录结构（详见 evals/README.md）：每个样本一个子目录——

    <sample>/
        attachments/      # 一份或多份附件（占位样本用 .txt 即可）
        expected.json     # 人工标注：{"meta": {...}, "questions": [...]}
        candidates.json   # 离线 fake extractor 读取的"模型输出"（可选）

extractor 是可插拔函数，签名：

    extractor(attachment_paths: list[Path], schema: dict, prompt: str) -> dict

返回 CandidateWrongQuestionBatch。默认实现是**离线 fake extractor**：忽略附件内容，
直接返回样本目录里的人工候选文件 candidates.json（要求与 attachments/ 同级）。
真实宿主 extractor（M1 Hermes 的 complete_structured 桥 / M2 模式 B 的 Skill 驱动）
接入时实现同一签名，用 --extractor 传入即可，本脚本其余部分不变。

输出指标（JSON，stdout）：字段准确率（subject/question_text/student_answer/
correct_answer/knowledge）、漏题率、误题率、置信度校准摘要、需人工修改字段数。
题目按 source_locator 对齐；extractor 输出先过 JSON Schema 校验，校验失败的样本
记为 invalid，其全部期望题计为漏题。
"""

from __future__ import annotations

import argparse
import difflib
import importlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from good_student.models import LOW_CONFIDENCE_THRESHOLD
from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCHEMA_PATH = REPO_ROOT / "schemas" / "candidate-wrong-question-batch.schema.json"
DEFAULT_PROMPT_PATH = REPO_ROOT / "prompts" / "wrong-book-extraction.md"

# 参与字段准确率统计的字段（§21.2）。is_wrong 必须参与：注入样本攻击的正是该字段，
# 不参与对比就无法判定模型是否被注入篡改（H2）。
COMPARE_FIELDS = ["subject", "question_text", "student_answer", "correct_answer", "knowledge", "is_wrong"]

# expected.json 中可选的"低置信/不确定字段"断言键。它们不进入字段准确率，
# 单独以 attention 汇总（低置信路径是"先确认后写入"可信底座，属独立诚实性维度）。
ATTENTION_KEYS = ("uncertain_fields", "low_confidence", "needs_confirmation")
# 置信度校准分桶（与 prompt 第三节的校准指引一致：≤0.6 为低质区间）
CALIBRATION_BUCKETS = [(0.0, 0.6), (0.6, 0.8), (0.8, 0.95), (0.95, 1.0)]
# question_text 允许少量转写差异，相似度达到阈值视为正确
TEXT_SIMILARITY_THRESHOLD = 0.9


@dataclass
class Sample:
    name: str
    directory: Path
    attachment_paths: list[Path]
    expected_questions: list[dict]


# ---------------------------------------------------------------- 样本加载


def load_samples(root: Path) -> list[Sample]:
    """发现样本目录：root 本身是样本，或其直接子目录是样本。"""
    if (root / "expected.json").is_file():
        dirs = [root]
    else:
        dirs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "expected.json").is_file())
    samples = []
    for directory in dirs:
        expected = json.loads((directory / "expected.json").read_text(encoding="utf-8"))
        attachments_dir = directory / "attachments"
        attachments = sorted(p for p in attachments_dir.iterdir() if p.is_file()) if attachments_dir.is_dir() else []
        samples.append(
            Sample(
                name=directory.name,
                directory=directory,
                attachment_paths=attachments,
                expected_questions=expected["questions"],
            )
        )
    return samples


# ---------------------------------------------------------------- extractor


def fake_extractor(attachment_paths: list[Path], schema: dict, prompt: str) -> dict:
    """离线 fake extractor：返回样本目录中的人工候选文件 candidates.json。

    约定：attachments/ 与 candidates.json 同级，因此可由附件路径推出样本目录。
    真实宿主 extractor 由 M1 Hermes / M2 接入时实现同一签名。
    """
    del schema, prompt  # fake extractor 不使用协议输入，仅满足签名
    if not attachment_paths:
        raise RuntimeError("fake extractor 需要至少一个附件路径来定位样本目录")
    sample_dir = attachment_paths[0].parent.parent
    candidates_path = sample_dir / "candidates.json"
    if not candidates_path.is_file():
        raise RuntimeError(f"样本 {sample_dir.name} 缺少 candidates.json（fake extractor 输入）")
    return json.loads(candidates_path.read_text(encoding="utf-8"))


def load_extractor(spec: str):
    """按 'module:function' 动态加载 extractor。"""
    module_name, _, func_name = spec.partition(":")
    if not module_name or not func_name:
        raise ValueError(f"--extractor 格式应为 'module:function'，收到: {spec!r}")
    module = importlib.import_module(module_name)
    return getattr(module, func_name)


# ---------------------------------------------------------------- 字段比较


def _norm(value) -> str | None:
    if value is None:
        return None
    return "".join(str(value).split())


def _text_match(expected: str, actual: str | None) -> bool:
    if actual is None:
        return False
    if _norm(expected) == _norm(actual):
        return True
    return difflib.SequenceMatcher(None, _norm(expected), _norm(actual)).ratio() >= TEXT_SIMILARITY_THRESHOLD


def _subject_match(expected_q: dict, actual_q: dict) -> bool:
    subject = actual_q.get("subject")
    return isinstance(subject, dict) and _norm(subject.get("value")) == _norm(expected_q["subject"])


def _answer_match(field_name: str, expected_q: dict, actual_q: dict) -> bool:
    expected = expected_q.get(field_name)
    actual = actual_q.get(field_name)
    if expected is None or actual is None:
        return expected is None and actual is None
    return _norm(expected) == _norm(actual)


def _knowledge_match(expected_q: dict, actual_q: dict) -> bool:
    expected_labels = {_norm(label) for label in expected_q.get("knowledge", [])}
    actual_labels = {
        _norm(item.get("label")) for item in actual_q.get("knowledge_candidates", []) if isinstance(item, dict)
    }
    if not expected_labels:
        return not actual_labels
    return bool(expected_labels & actual_labels)


def compare_question(expected_q: dict, actual_q: dict) -> dict[str, bool]:
    """逐字段比较一道已对齐的题；只比较标注中给出的字段。

    返回键分两类：
    - COMPARE_FIELDS 内（含 is_wrong）：计入字段准确率与 per-question correct；
    - ATTENTION_KEYS（uncertain_fields / low_confidence / needs_confirmation）：
      仅当 expected 携带时才产出，计入 attention_ok，不计入字段准确率。
    """
    result = {}
    if "subject" in expected_q:
        result["subject"] = _subject_match(expected_q, actual_q)
    if "question_text" in expected_q:
        result["question_text"] = _text_match(expected_q["question_text"], actual_q.get("question_text"))
    for field_name in ("student_answer", "correct_answer"):
        if field_name in expected_q:
            result[field_name] = _answer_match(field_name, expected_q, actual_q)
    if "knowledge" in expected_q:
        result["knowledge"] = _knowledge_match(expected_q, actual_q)
    if "is_wrong" in expected_q and isinstance(expected_q["is_wrong"], bool):
        result["is_wrong"] = actual_q.get("is_wrong") is expected_q["is_wrong"]

    # ---- 低置信/不确定字段断言（H3）----
    if "uncertain_fields" in expected_q:
        expected_set = {f for f in expected_q["uncertain_fields"] if isinstance(f, str)}
        actual_set = {f for f in actual_q.get("uncertain_fields") or [] if isinstance(f, str)}
        # 期望是最低必标集合：模型至少要把这些字段标为不确定（多标更安全）
        result["uncertain_fields"] = bool(expected_set) and expected_set <= actual_set
    if "low_confidence" in expected_q:
        # 期望低置信：模型必须给出 < 阈值 的置信度，且显式 needs_confirmation
        conf = actual_q.get("extraction_confidence")
        result["low_confidence"] = bool(
            expected_q["low_confidence"]
            and isinstance(conf, (int, float))
            and conf < LOW_CONFIDENCE_THRESHOLD
            and actual_q.get("needs_confirmation") is True
        )
    if "needs_confirmation" in expected_q and expected_q["needs_confirmation"] is True:
        result["needs_confirmation"] = actual_q.get("needs_confirmation") is True
    return result


# ---------------------------------------------------------------- 评测


def evaluate_sample(
    sample: Sample,
    extractor,
    schema: dict,
    prompt: str,
    validator: Draft202012Validator,
) -> dict:
    """评测单个样本，返回 per-sample 指标。"""
    entry: dict = {
        "name": sample.name,
        "questions_expected": len(sample.expected_questions),
        "questions_extracted": 0,
        "questions_matched": 0,
        "missed_locators": [],
        "false_positive_locators": [],
        "field_results": [],
        "fields_requiring_correction": 0,
        "invalid_output": False,
        "error": None,
    }
    try:
        batch = extractor(sample.attachment_paths, schema, prompt)
    except Exception as exc:  # extractor 失败等价于整份样本识别失败
        entry["invalid_output"] = True
        entry["error"] = f"{type(exc).__name__}: {exc}"
        entry["missed_locators"] = [q["source_locator"] for q in sample.expected_questions]
        return entry

    errors = list(validator.iter_errors(batch))
    if errors:
        entry["invalid_output"] = True
        entry["error"] = f"schema_validation_failed: {errors[0].message}"
        entry["missed_locators"] = [q["source_locator"] for q in sample.expected_questions]
        return entry

    actual_by_locator = {q["source_locator"]: q for q in batch["questions"]}
    expected_by_locator = {q["source_locator"]: q for q in sample.expected_questions}
    entry["questions_extracted"] = len(actual_by_locator)
    entry["missed_locators"] = sorted(set(expected_by_locator) - set(actual_by_locator))
    entry["false_positive_locators"] = sorted(set(actual_by_locator) - set(expected_by_locator))

    for locator in sorted(set(expected_by_locator) & set(actual_by_locator)):
        field_result = compare_question(expected_by_locator[locator], actual_by_locator[locator])
        confidence = actual_by_locator[locator].get("extraction_confidence")
        attention_keys = [k for k in field_result if k in ATTENTION_KEYS]
        # correct 只看 COMPARE_FIELDS（含 is_wrong）；attention 断言单独判定，不掺进字段准确率
        correct = all(field_result[k] for k in COMPARE_FIELDS if k in field_result)
        attention_ok = all(field_result[k] for k in attention_keys) if attention_keys else True
        entry["field_results"].append(
            {
                "source_locator": locator,
                "fields": field_result,
                "correct": correct,
                "attention_checked": bool(attention_keys),
                "attention_ok": attention_ok,
                "extraction_confidence": confidence if isinstance(confidence, (int, float)) else None,
            }
        )
        entry["fields_requiring_correction"] += sum(
            1 for k in COMPARE_FIELDS if k in field_result and not field_result[k]
        )
    entry["questions_matched"] = len(entry["field_results"])
    return entry


def aggregate(entries: list[dict]) -> dict:
    """把 per-sample 结果汇总为 §21.2 指标。"""
    total_expected = sum(e["questions_expected"] for e in entries)
    total_extracted = sum(e["questions_extracted"] for e in entries)
    total_matched = sum(e["questions_matched"] for e in entries)
    total_missed = sum(len(e["missed_locators"]) for e in entries)
    total_false_positive = sum(len(e["false_positive_locators"]) for e in entries)

    field_accuracy = {}
    for field_name in COMPARE_FIELDS:
        correct = total = 0
        for entry in entries:
            for result in entry["field_results"]:
                if field_name in result["fields"]:
                    total += 1
                    correct += int(result["fields"][field_name])
        field_accuracy[field_name] = {
            "correct": correct,
            "total": total,
            "accuracy": round(correct / total, 4) if total else None,
        }

    buckets = []
    for low, high in CALIBRATION_BUCKETS:
        in_bucket = [
            r
            for e in entries
            for r in e["field_results"]
            if r["extraction_confidence"] is not None and low <= r["extraction_confidence"] <= high
            and (r["extraction_confidence"] < high or high == 1.0)
        ]
        buckets.append(
            {
                "range": f"[{low}, {high}{']' if high == 1.0 else ')'}",
                "count": len(in_bucket),
                "accuracy": round(sum(int(r["correct"]) for r in in_bucket) / len(in_bucket), 4) if in_bucket else None,
            }
        )
    with_confidence = [r for e in entries for r in e["field_results"] if r["extraction_confidence"] is not None]
    correct_conf = [r["extraction_confidence"] for r in with_confidence if r["correct"]]
    incorrect_conf = [r["extraction_confidence"] for r in with_confidence if not r["correct"]]

    return {
        "samples_total": len(entries),
        "samples_invalid": sum(int(e["invalid_output"]) for e in entries),
        "questions_expected": total_expected,
        "questions_extracted": total_extracted,
        "questions_matched": total_matched,
        "miss_rate": round(total_missed / total_expected, 4) if total_expected else None,
        "false_positive_rate": round(total_false_positive / total_extracted, 4) if total_extracted else None,
        "field_accuracy": field_accuracy,
        "confidence_calibration": {
            "buckets": buckets,
            "mean_confidence_when_correct": round(sum(correct_conf) / len(correct_conf), 4) if correct_conf else None,
            "mean_confidence_when_incorrect": (
                round(sum(incorrect_conf) / len(incorrect_conf), 4) if incorrect_conf else None
            ),
        },
        "fields_requiring_correction": sum(e["fields_requiring_correction"] for e in entries),
        "attention": {
            "checked": sum(1 for e in entries for r in e["field_results"] if r["attention_checked"]),
            "ok": sum(1 for e in entries for r in e["field_results"] if r["attention_checked"] and r["attention_ok"]),
            "failure_count": sum(
                1 for e in entries for r in e["field_results"] if r["attention_checked"] and not r["attention_ok"]
            ),
        },
        "per_sample": entries,
    }


def evaluate(root: Path, extractor, schema: dict, prompt: str) -> dict:
    validator = Draft202012Validator(schema)
    samples = load_samples(root)
    entries = [evaluate_sample(sample, extractor, schema, prompt, validator) for sample in samples]
    return aggregate(entries)


# ---------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="错题提取评测（设计 §21.2）：对样本目录运行 extractor，输出指标 JSON。"
    )
    parser.add_argument("samples_dir", type=Path, help="样本目录（每个子目录一个样本，含 expected.json）")
    parser.add_argument(
        "--extractor",
        default=None,
        help="可插拔 extractor，格式 'module:function'；缺省使用离线 fake extractor（读 candidates.json）",
    )
    parser.add_argument("--schema", type=Path, default=DEFAULT_SCHEMA_PATH, help="候选批次 Schema 路径")
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH, help="提取 prompt 路径")
    parser.add_argument("--output", type=Path, default=None, help="指标 JSON 写入路径（缺省只打印到 stdout）")
    args = parser.parse_args(argv)

    if not args.samples_dir.is_dir():
        print(f"样本目录不存在: {args.samples_dir}", file=sys.stderr)
        return 2
    extractor = load_extractor(args.extractor) if args.extractor else fake_extractor
    schema = json.loads(args.schema.read_text(encoding="utf-8"))
    prompt = args.prompt.read_text(encoding="utf-8")

    metrics = evaluate(args.samples_dir, extractor, schema, prompt)
    if metrics["samples_total"] == 0:
        print(f"未发现任何样本（缺少 expected.json）: {args.samples_dir}", file=sys.stderr)
        return 2
    # 记录 extractor 来源：门禁据其判断注入样本是否经真实宿主模型验证（M6）。
    # 缺省即离线 fake extractor（读人工 candidates.json，不接触附件/模型）。
    metrics["extractor"] = args.extractor or "offline_fake"

    text = json.dumps(metrics, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
