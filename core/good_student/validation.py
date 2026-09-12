"""Schema 校验：加载 schemas/ 单一源文件，附加语义检查（非有限数、低置信提醒）。"""

import json
import math
import os
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from good_student.models import LOW_CONFIDENCE_THRESHOLD

SCHEMA_DIR = Path(__file__).resolve().parents[2] / "schemas"

SCHEMA_FILES = {
    "candidate-batch": "candidate-wrong-question-batch.schema.json",
    "analysis-result": "analysis-result.schema.json",
    "tool-response": "tool-response.schema.json",
}

_validators: dict[str, Draft202012Validator] = {}


def load_schema(name: str) -> dict[str, Any]:
    filename = SCHEMA_FILES[name]
    candidates = [
        Path(os.environ["GOOD_STUDENT_SCHEMA_DIR"]) if os.environ.get("GOOD_STUDENT_SCHEMA_DIR") else None,
        SCHEMA_DIR,
    ]
    for directory in candidates:
        if directory is None:
            continue
        path = directory / filename
        if path.is_file():
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
    try:
        return json.loads(files("good_student.schemas").joinpath(filename).read_text(encoding="utf-8"))
    except (ModuleNotFoundError, FileNotFoundError):
        pass
    searched = ", ".join(str(path) for path in candidates if path is not None)
    raise FileNotFoundError(f"Good-student Schema 未找到：{filename}；已搜索 {searched}")


def _validator(name: str) -> Draft202012Validator:
    if name not in _validators:
        schema = load_schema(name)
        Draft202012Validator.check_schema(schema)
        _validators[name] = Draft202012Validator(schema)
    return _validators[name]


def validate(instance: Any, schema_name: str) -> list[str]:
    """返回格式化错误列表；空列表表示通过。"""
    errors = [
        f"at {list(e.absolute_path) or ['$']}: {e.message}"
        for e in _validator(schema_name).iter_errors(instance)
    ]
    return errors


def _find_non_finite(node: Any, path: str = "$") -> list[str]:
    problems: list[str] = []
    if isinstance(node, float) and not math.isfinite(node):
        problems.append(f"at {path}: non-finite number (NaN/Infinity)")
    elif isinstance(node, dict):
        for key, value in node.items():
            problems += _find_non_finite(value, f"{path}.{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            problems += _find_non_finite(value, f"{path}[{i}]")
    return problems


def validate_candidate_batch(batch: Any) -> tuple[list[str], list[str]]:
    """Schema 校验 + 语义检查。返回 (errors, warnings)。"""
    warnings: list[str] = []
    errors = validate(batch, "candidate-batch")
    if errors:
        return errors, warnings
    errors = _find_non_finite(batch)
    if errors:
        return errors, warnings

    for i, question in enumerate(batch["questions"]):
        extraction_confidence = question.get("extraction_confidence")
        uncertain = question.get("uncertain_fields") or []
        low_confidence = (
            extraction_confidence is not None
            and extraction_confidence < LOW_CONFIDENCE_THRESHOLD
        )
        if (low_confidence or uncertain) and not question.get("needs_confirmation"):
            warnings.append(
                f"questions[{i}]: 低置信（{extraction_confidence}）或有不确定字段但未标记 "
                "needs_confirmation，确认时请重点核对"
            )
        if not question.get("subject"):
            warnings.append(f"questions[{i}]: 缺少科目，确认时必须补充")
        if not question.get("knowledge_candidates"):
            warnings.append(f"questions[{i}]: 没有知识点候选，确认时建议补充")
    warnings += [f"batch: {w}" for w in batch.get("warnings") or []]
    return errors, warnings
