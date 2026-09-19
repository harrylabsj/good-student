"""K12 知识点种子包加载与归一（设计 §13.4 的标准知识点来源）。

加载优先级（与 validation.load_schema 同一模式）：
1. 环境变量 GOOD_STUDENT_KNOWLEDGE_DIR 指向的目录（同名文件覆盖内置包）；
2. 仓库根 knowledge_packs/（源码运行）；
3. wheel 内置的 good_student.knowledge_packs 子包（隔离安装运行）。

种子包只做两件事：
- 归一：把自由文本知识点标签映射到标准节点（canonical_name / aliases）；
- 前置链：给出 prerequisite_ids，供分析层提示"根子可能在哪"。
它不改变薄弱点判断的证据来源——判断仍然只来自已确认的题目级证据。
"""

from __future__ import annotations

import json
import os
import threading
from importlib.resources import files
from pathlib import Path
from typing import Any

PACKS_DIR = Path(__file__).resolve().parents[2] / "knowledge_packs"

_cache: dict[str, Any] | None = None
_lock = threading.Lock()


def _read_pack(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_pack(pack: dict, origin: str) -> None:
    """结构性校验：前置引用必须指向本包内节点。"""
    ids = {c["id"] for c in pack["components"]}
    for component in pack["components"]:
        for field in ("id", "canonical_name", "aliases", "prerequisite_ids"):
            if field not in component:
                raise ValueError(f"{origin}: 知识点 {component.get('id')} 缺少 {field}")
        unknown = set(component["prerequisite_ids"]) - ids
        if unknown:
            raise ValueError(f"{origin}: {component['id']} 的前置引用不存在：{sorted(unknown)}")


def load_packs(force_reload: bool = False) -> list[dict]:
    """加载全部知识点包；失败时抛出 ValueError/FileNotFoundError。"""
    global _cache
    with _lock:
        if _cache is not None and not force_reload:
            return _cache["packs"]

        packs: dict[str, dict] = {}
        override = os.environ.get("GOOD_STUDENT_KNOWLEDGE_DIR")
        dirs = [Path(override)] if override else []
        dirs.append(PACKS_DIR)
        for directory in dirs:
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.json")):
                packs[path.name] = _read_pack(path)
        try:
            resource_dir = files("good_student.knowledge_packs")
            for entry in sorted(resource_dir.iterdir()):
                if entry.name.endswith(".json") and entry.name not in packs:
                    packs[entry.name] = json.loads(entry.read_text(encoding="utf-8"))
        except (ModuleNotFoundError, FileNotFoundError):
            pass

        result = list(packs.values())
        for pack in result:
            _validate_pack(pack, pack.get("pack_id", "?"))
        _cache = {"packs": result, "by_subject": _index(result)}
        return result


def _index(packs: list[dict]) -> dict[str, dict[str, Any]]:
    """subject_id -> {"components": {id: component}, "labels": {label: component}}"""
    by_subject: dict[str, dict[str, Any]] = {}
    for pack in packs:
        bucket = by_subject.setdefault(pack["subject_id"], {"components": {}, "labels": {}})
        for component in pack["components"]:
            bucket["components"][component["id"]] = component
            bucket["labels"][component["canonical_name"]] = component
            for alias in component.get("aliases", []):
                bucket["labels"].setdefault(alias, component)
    return by_subject


def match(subject_id: str, label: str) -> dict | None:
    """把自由文本标签归一到标准知识点；未命中返回 None。"""
    label = (label or "").strip()
    if not label:
        return None
    bucket = load_packs_safe()["by_subject"].get(subject_id)
    if not bucket:
        return None
    return bucket["labels"].get(label)


def prerequisites(subject_id: str, canonical_name: str) -> list[dict]:
    """返回标准知识点的前置链（一层）；非标准知识点返回空列表。"""
    bucket = load_packs_safe()["by_subject"].get(subject_id)
    if not bucket:
        return []
    component = bucket["labels"].get(canonical_name)
    if not component:
        return []
    return [
        bucket["components"][pid]
        for pid in component.get("prerequisite_ids", [])
        if pid in bucket["components"]
    ]


def pack_origin(subject_id: str, canonical_name: str) -> tuple[str | None, int | None]:
    """返回标准知识点所属 (pack_id, version)；非标准知识点返回 (None, None)。"""
    try:
        packs = load_packs()
    except (OSError, ValueError, json.JSONDecodeError):
        return None, None
    for pack in packs:
        if pack["subject_id"] != subject_id:
            continue
        for component in pack["components"]:
            if component["canonical_name"] == canonical_name:
                return pack["pack_id"], pack["version"]
    return None, None


def load_packs_safe() -> dict[str, Any]:
    """加载失败时降级为空索引，不阻断核心流程。"""
    try:
        load_packs()
    except (OSError, ValueError, json.JSONDecodeError):
        return {"packs": [], "by_subject": {}}
    return _cache or {"packs": [], "by_subject": {}}
