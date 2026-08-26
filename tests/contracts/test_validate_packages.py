"""validate_packages.py 单测（M4）：skill name 规则 + references 漂移检测。"""

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    spec = importlib.util.spec_from_file_location(
        "validate_packages", REPO_ROOT / "scripts" / "validate_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("validate_packages", module)
    spec.loader.exec_module(module)
    return module


def _make_repo(root: Path, pkg_name: str = "hermes") -> None:
    """构造最小仓库结构：根 skills/good-student + 一个 packages/*/skills/ 副本。"""
    (sk := root / "skills" / "good-student").mkdir(parents=True)
    (sk / "SKILL.md").write_text("---\nname: good-student\n---\n", encoding="utf-8")
    (rf := sk / "references").mkdir()
    (rf / "error-registry.md").write_text("ROOT-CONTENT\n", encoding="utf-8")
    (rf / "state-machines.md").write_text("ROOT-SM\n", encoding="utf-8")

    (pkg := root / "packages" / pkg_name / "skills" / "good-student").mkdir(parents=True)
    (pkg / "SKILL.md").write_text("---\nname: good-student\n---\n", encoding="utf-8")
    (pkg / "references").mkdir()
    (pkg / "references" / "error-registry.md").write_text("ROOT-CONTENT\n", encoding="utf-8")
    (pkg / "references" / "state-machines.md").write_text("ROOT-SM\n", encoding="utf-8")


def test_repo_passes_validation():
    vp = _load_module()
    assert vp._check(REPO_ROOT) == []


def test_skill_name_mismatch_detected(tmp_path):
    vp = _load_module()
    _make_repo(tmp_path)
    # 把副本的 frontmatter name 改成与目录不符
    pkg_skill = tmp_path / "packages" / "hermes" / "skills" / "good-student" / "SKILL.md"
    pkg_skill.write_text("---\nname: wrong-book-coach\n---\n", encoding="utf-8")
    failed = vp._check(tmp_path)
    assert any("name" in f and "不一致" in f for f in failed)


def test_references_drift_detected(tmp_path):
    vp = _load_module()
    _make_repo(tmp_path)
    pkg_ref = tmp_path / "packages" / "hermes" / "skills" / "good-student" / "references" / "error-registry.md"
    pkg_ref.write_text("DRIFTED\n", encoding="utf-8")
    failed = vp._check(tmp_path)
    assert any("error-registry.md" in f and "不一致" in f for f in failed)


def test_missing_root_reference_detected(tmp_path):
    vp = _load_module()
    _make_repo(tmp_path)
    # 副本缺少根单一源里的 state-machines.md
    (tmp_path / "packages" / "hermes" / "skills" / "good-student" / "references" / "state-machines.md").unlink()
    failed = vp._check(tmp_path)
    assert any("缺少根单一源" in f and "state-machines.md" in f for f in failed)
