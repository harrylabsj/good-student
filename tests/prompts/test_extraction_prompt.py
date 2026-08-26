"""提取 prompt 定稿的回归测试（设计 §10.2、§10.3）。

防两类漂移：
1. prompt 丢失 §10.2 的任何一条模型系统指令边界（关键词检查）。
2. prompt 内嵌 Schema / 示例与 schemas/ 单一源不一致（解析级比对 + jsonschema 校验）。
"""

import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPT_PATH = REPO_ROOT / "prompts" / "wrong-book-extraction.md"
SCHEMA_PATH = REPO_ROOT / "schemas" / "candidate-wrong-question-batch.schema.json"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _json_blocks(text: str) -> list[dict]:
    """提取 prompt 中所有 ```json 代码块并解析为对象。"""
    blocks = []
    for match in re.finditer(r"```json\n(.*?)```", text, re.DOTALL):
        blocks.append(json.loads(match.group(1)))
    return blocks


# §10.2 模型系统指令边界：每条边界至少一个不可替换的关键词。
BOUNDARY_KEYWORDS = [
    "不可信数据",  # 附件文本视为不可信数据
    "提示注入",  # 防注入
    "null",  # 不确定返回 null
    "needs_confirmation",  # 不确定时标记需确认
    "拆分为多题",  # 混合科目拆题
    "uncertain_fields",  # 字段级不确定性
    "学生画像",  # 不得修改学生画像
    "schema_version",  # 协议版本
    "修复重试",  # 受限修复变体
]


def test_prompt_exists_and_nonempty():
    text = _load_prompt()
    assert len(text) > 2000


def test_prompt_covers_all_design_section_10_2_boundaries():
    text = _load_prompt()
    for keyword in BOUNDARY_KEYWORDS:
        assert keyword in text, f"prompt 缺少边界关键词: {keyword}"


def test_prompt_embeds_full_schema_identical_to_single_source():
    """prompt 内嵌的完整 Schema 必须与 schemas/ 单一源解析结果一致。"""
    schema = _load_schema()
    blocks = _json_blocks(_load_prompt())
    embedded = [b for b in blocks if isinstance(b, dict) and b.get("$id") == schema["$id"]]
    assert len(embedded) == 1, "prompt 中应恰好内嵌一份完整 Schema"
    assert embedded[0] == schema, "prompt 内嵌 Schema 与 schemas/ 单一源漂移"


def test_prompt_schema_version_matches_schema_const():
    schema = _load_schema()
    version = schema["properties"]["schema_version"]["const"]
    assert f"<!-- schema_version: {version} -->" in _load_prompt()


def test_all_embedded_examples_validate_against_schema():
    """prompt 中每个候选批次示例都必须通过 Schema 校验。"""
    schema = _load_schema()
    validator = Draft202012Validator(schema)
    version = schema["properties"]["schema_version"]["const"]
    examples = [b for b in _json_blocks(_load_prompt()) if b.get("schema_version") == version]
    assert len(examples) >= 3, "至少包含清晰印刷、混合科目、模糊手写三个示例"
    for i, example in enumerate(examples):
        errors = list(validator.iter_errors(example))
        assert not errors, f"示例 {i + 1} 校验失败: {[e.message for e in errors]}"


def test_prompt_includes_mixed_subject_and_handwriting_examples():
    text = _load_prompt()
    assert "混合科目" in text
    assert "手写" in text
    # 混合科目示例必须真的拆成多题、且科目不同
    examples = [b for b in _json_blocks(text) if b.get("schema_version") == 1]
    mixed = [e for e in examples if len({q["subject"]["value"] for q in e["questions"] if "subject" in q}) > 1]
    assert mixed, "缺少同页多科目的拆分示例"
    # 模糊手写示例必须演示字段级不确定性
    uncertain = [e for e in examples if any(q.get("uncertain_fields") for q in e["questions"])]
    assert uncertain, "缺少字段级不确定性（uncertain_fields）示例"


def test_prompt_includes_constrained_repair_retry_section():
    text = _load_prompt()
    assert "修复重试" in text
    assert "只进行一次" in text  # 受限：仅一次修复重试
    assert "不得改动你已提取的内容取值" in text  # 受限：只修结构、不改取值
