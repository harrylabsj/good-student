"""WorkBuddy upload package contract checks."""

import asyncio
import json
import os
import shutil
import subprocess
import sys
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
        assert "cli.json" not in names
        assert "bin/start_mcp.py" in names
        assert "runtime-constraints.txt" in names
        assert "agents/wrong-book-coach.md" in names
        assert "avatars/expert.png" in names
        assert "skills/good-student/SKILL.md" in names
        assert "skills/good-student/references/error-registry.md" in names
        assert "skills/good-student/references/state-machines.md" in names
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
    assert plugin["version"] == "1.1.2"
    assert plugin["dependencies"] == {"mcpServers": "./.mcp.json"}
    assert plugin["skills"] == ["./skills/good-student"]
    assert "token" not in json.dumps(plugin).lower()
    avatar = WORKBUDDY / "expert" / plugin["avatar"]
    assert avatar.is_file() and avatar.stat().st_size < 500 * 1024


def test_expert_skill_uses_mcp_contract_without_cli_conflict():
    skill = (WORKBUDDY / "expert/skills/good-student/SKILL.md").read_text(encoding="utf-8")
    assert "good_student_ingest_candidates" in skill
    assert "good_student_confirm_questions" in skill
    assert "不要调用 `good-student` CLI 命令" in skill
    assert "good-student capabilities" not in skill
    assert "家长明确确认" in skill


def test_embedded_expert_cold_and_warm_start_from_config(tmp_path):
    """Launch the generated config without installing anything in the child first."""
    import importlib.util

    import pytest
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from mcp import ClientSession

    pytest.importorskip("mcp")
    spec = importlib.util.spec_from_file_location(
        "build_workbuddy_packages", REPO_ROOT / "scripts/build_workbuddy_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    staged = module.stage_expert(tmp_path / "package with spaces")
    config_text = (staged / ".mcp.json").read_text()
    config_text = config_text.replace("${CODEBUDDY_PLUGIN_ROOT}", str(staged))
    config_text = config_text.replace("${CODEBUDDY_PLUGIN_DATA}", str(tmp_path / "plugin data"))
    config = json.loads(config_text)["mcpServers"]["good-student"]
    env = os.environ.copy()
    for key in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "GOOD_STUDENT_SCHEMA_DIR"):
        env.pop(key, None)
    env.update(config["env"])
    env["GOOD_STUDENT_DATA"] = str(tmp_path / "student-data")
    # Remove the development venv from PATH: use the actual system python3.
    env["PATH"] = os.pathsep.join(p for p in env["PATH"].split(os.pathsep) if ".venv" not in p)
    server = StdioServerParameters(
        command=config["command"], args=config["args"], env=env, cwd=config["cwd"],
    )

    async def run_smoke(warm=False):
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=180) as session:
                await session.initialize()
                assert len((await session.list_tools()).tools) == 15

                async def call(name, args=None):
                    result = await session.call_tool(name, args or {})
                    assert not result.is_error, result
                    payload = json.loads(result.content[0].text)
                    assert payload["ok"], payload
                    return payload["data"]

                assert (await call("good_student_doctor"))["ok"] is True
                if warm:
                    students = await call("good_student_list_students")
                    assert any(s["display_name"] == "冷启动验收" for s in students["students"])
                else:
                    await call("good_student_create_student", {"display_name": "冷启动验收", "grade": "五年级"})

    asyncio.run(run_smoke())
    marker = next(Path(env["GOOD_STUDENT_RUNTIME"]).glob("*/.ready"))
    installed_at = marker.stat().st_mtime_ns
    # Cached restart must work even with an unreachable package index.
    env["PIP_INDEX_URL"] = "http://127.0.0.1:9/unavailable"
    asyncio.run(run_smoke(warm=True))
    assert marker.stat().st_mtime_ns == installed_at


def test_incomplete_expert_reports_startup_error_on_stderr(tmp_path):
    package = tmp_path / "incomplete expert"
    (package / "bin").mkdir(parents=True)
    bootstrap = package / "bin/start_mcp.py"
    shutil.copy2(WORKBUDDY / "expert/bin/start_mcp.py", bootstrap)
    result = subprocess.run(
        [sys.executable, str(bootstrap)], cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "专家包缺少唯一的核心 wheel" in result.stderr
