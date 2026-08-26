#!/usr/bin/env python3
"""校验共享单一源文件（Schema/prompt/Skill）存在、可解析且分发无漂移（设计 §17/M2 任务 1）。

检查项：
1. Schema 三件套存在且是合法 JSON。
2. 根目录共享文件存在（prompt / Skill / references）。
3. **Skill frontmatter name 规则**：所有 skills/<dir>/SKILL.md（根与 packages/* 副本）的
   frontmatter `name` 必须等于其目录名（Agent Skills 规范；根目录已在 SKILL.md 注明）。
4. **references 漂移**：packages/*/skills/*/references/ 下的 .md 必须与根目录
   skills/good-student/references/ 同名文件逐字节一致（references 是共享单一源；
   SKILL.md 本体允许宿主变体，不做字节比对）。
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SCHEMAS = [
    "schemas/candidate-wrong-question-batch.schema.json",
    "schemas/analysis-result.schema.json",
    "schemas/tool-response.schema.json",
]
SHARED = [
    "prompts/wrong-book-extraction.md",
    "skills/good-student/SKILL.md",
    "skills/good-student/references/error-registry.md",
    "skills/good-student/references/state-machines.md",
]
ROOT_REFERENCES = ROOT / "skills" / "good-student" / "references"


def _skill_name(path: Path) -> str | None:
    """从 SKILL.md frontmatter 提取 name；无 frontmatter 或无 name 返回 None。"""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    fm = text[3:end] if end != -1 else text[3:]
    for line in fm.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip("\"'")
    return None


def _iter_skill_dirs(root: Path) -> list[Path]:
    """返回所有含 SKILL.md 的 skill 目录（根 skills/ + 各 packages/*/skills/）。"""
    dirs: list[Path] = [root / "skills"]
    packages = root / "packages"
    if packages.is_dir():
        for pkg in sorted(packages.iterdir()):
            pkg_skills = pkg / "skills"
            if pkg.is_dir() and pkg_skills.is_dir():
                dirs.append(pkg_skills)
    result: list[Path] = []
    for skills_dir in dirs:
        if not skills_dir.is_dir():
            continue
        for skill_dir in sorted(skills_dir.iterdir()):
            if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
                result.append(skill_dir)
    return result


def _check_skill_name(root: Path, skill_dir: Path, failed: list[str]) -> None:
    name = _skill_name(skill_dir / "SKILL.md")
    rel = skill_dir.relative_to(root)
    if name is None:
        failed.append(f"skill name: {rel}/SKILL.md 缺少 frontmatter name")
    elif name != skill_dir.name:
        failed.append(
            f"skill name: {rel}/SKILL.md frontmatter name={name!r} 与目录名 {skill_dir.name!r} 不一致"
        )


def _check_references_drift(root: Path, skill_dir: Path, failed: list[str]) -> None:
    """packages 副本的 references 必须与根单一源逐字节一致（根本身跳过）。"""
    root_refs = root / "skills" / "good-student" / "references"
    if skill_dir == root / "skills" / "good-student":
        return
    refs_dir = skill_dir / "references"
    if not refs_dir.is_dir():
        return
    rel = skill_dir.relative_to(root)
    for ref in sorted(refs_dir.iterdir()):
        if not ref.is_file() or ref.suffix != ".md":
            continue
        root_ref = root_refs / ref.name
        if not root_ref.is_file():
            failed.append(f"references: {rel}/references/{ref.name} 在根单一源中不存在")
            continue
        if ref.read_bytes() != root_ref.read_bytes():
            failed.append(f"references: {rel}/references/{ref.name} 与根 {root_ref.relative_to(root)} 不一致")
    for root_ref in sorted(root_refs.iterdir()):
        if root_ref.is_file() and not (refs_dir / root_ref.name).is_file():
            failed.append(f"references: {rel}/references/ 缺少根单一源的 {root_ref.name}")


def _check(root: Path) -> list[str]:
    failed: list[str] = []
    for rel in SCHEMAS:
        path = root / rel
        if not path.is_file():
            failed.append(f"missing: {rel}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failed.append(f"invalid json: {rel}: {exc}")
    for rel in SHARED:
        if not (root / rel).is_file():
            failed.append(f"missing: {rel}")

    skill_dirs = _iter_skill_dirs(root)
    for skill_dir in skill_dirs:
        _check_skill_name(root, skill_dir, failed)
        _check_references_drift(root, skill_dir, failed)
    return failed


def main() -> int:
    failed = _check(ROOT)

    if failed:
        for line in failed:
            print(line, file=sys.stderr)
        return 1
    print(
        f"ok: {len(SCHEMAS)} schemas + {len(SHARED)} shared files + "
        f"{len(_iter_skill_dirs(ROOT))} skill(s) name 规则与 references 漂移检查通过"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
