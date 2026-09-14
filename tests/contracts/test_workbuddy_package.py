"""WorkBuddy upload package contract checks."""

import json
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKBUDDY = REPO_ROOT / "packages" / "workbuddy"


def test_skill_manifest_has_workbuddy_market_fields():
    text = (WORKBUDDY / "skill/skills/good-student/SKILL.md").read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert "display_name:" in frontmatter
    assert "description_zh:" in frontmatter
    assert "description_en:" in frontmatter
    assert "version:" in frontmatter
    assert "author:" in frontmatter


def test_connector_metadata_and_cli_config_are_publish_safe(tmp_path):
    import importlib.util

    metadata = json.loads((WORKBUDDY / "connector/connector-meta.json").read_text(encoding="utf-8"))
    assert metadata["source"] == "good-student"
    assert metadata["type"] == "cli"
    assert len(metadata["examples_zh"]) >= 2
    assert len(metadata["examples_en"]) >= 2
    assert "minWorkbuddyVersion" in metadata  # runtime python 自 5.0.0 起支持

    # 通过真实构建验证暂存产物：cli.json 注入 wheel 文件名，pkg/ 内置 wheel
    spec = importlib.util.spec_from_file_location(
        "build_workbuddy_packages", REPO_ROOT / "scripts/build_workbuddy_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    staged = module.stage_connector(tmp_path)
    config = json.loads((staged / "cli.json").read_text(encoding="utf-8"))
    assert config["runtime"]["type"] == "python"
    assert set(config["init"]) == {"darwin", "linux", "win32"}
    wheels = list((staged / "pkg").glob("*.whl"))
    assert len(wheels) == 1
    assert f"./pkg/{wheels[0].name}" in config["init"]["darwin"]
    assert not (staged / "mcp.json").exists()
    blob = json.dumps(config).lower() + json.dumps(metadata).lower()
    for word in ("uvx", "token", "secret"):
        assert word not in blob


def test_built_archives_have_expected_roots(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "build_workbuddy_packages", REPO_ROOT / "scripts/build_workbuddy_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    archives = module.build(tmp_path)
    assert len(archives) == 3
    with zipfile.ZipFile(archives[0]) as archive:
        names = set(archive.namelist())
        assert "skills/good-student/SKILL.md" in names
    with zipfile.ZipFile(archives[1]) as archive:
        names = set(archive.namelist())
        assert "connector-meta.json" in names
        assert "cli.json" in names
        assert any(n.startswith("pkg/") and n.endswith(".whl") for n in names)
        assert "mcp.json" not in names
        assert "skills/good-student/SKILL.md" in names
    with zipfile.ZipFile(archives[2]) as archive:
        names = set(archive.namelist())
        assert ".codebuddy-plugin/plugin.json" in names
        assert ".mcp.json" in names
        assert "cli.json" in names
        assert "agents/wrong-book-coach.md" in names
        assert "avatars/expert.png" in names
        assert "skills/good-student/SKILL.md" in names
        assert any(n.startswith("pkg/") and n.endswith(".whl") for n in names)
        assert ".mcp.template.json" not in names
        assert "cli.template.json" not in names


def test_expert_manifest_matches_platform_constraints():
    plugin = json.loads(
        (WORKBUDDY / "expert/.codebuddy-plugin/plugin.json").read_text(encoding="utf-8")
    )
    assert plugin["expertType"] == "agent"
    assert plugin["agentName"] == "wrong-book-coach"
    assert 40 <= len(plugin["displayDescription"]["zh"]) <= 50
    assert len(plugin["tags"]) == 3
    assert len(plugin["quickPrompts"]) == 3
    assert plugin["defaultInitPrompt"] == plugin["quickPrompts"][0]
    assert plugin["categoryId"] == "15-Education"
    assert plugin["version"] == "1.1.0"
    assert plugin["dependencies"] == {"mcpServers": "./.mcp.json"}
    assert plugin["skills"] == ["./skills/good-student"]
    assert "token" not in json.dumps(plugin).lower()
    avatar = WORKBUDDY / "expert" / plugin["avatar"]
    assert avatar.is_file() and avatar.stat().st_size < 500 * 1024
