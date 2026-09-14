#!/usr/bin/env python3
"""Build the three WorkBuddy upload archives from repository sources.

Archives:
- Skill archive: independently installable Skill market asset.
- Connector archive: CLI + Skill connector. The Good-student wheel is built
  from the repository and **bundled inside** the connector package
  (``pkg/*.whl``); ``cli.json`` declares WorkBuddy's managed Python runtime so
  installation runs ``python -m pip install ./pkg/<wheel>`` — no PyPI
  publishing and no uvx required. (jsonschema 等运行依赖由平台托管 pip 解析。)
- Expert archive: independently runnable WorkBuddy expert with an embedded
  Skill, stdio MCP declaration, managed-Python installer, and bundled wheel.

The script validates the expert package against WorkBuddy platform constraints
(display description length, tag/quick prompt counts, avatar size, dependency
declaration) before zipping, so upload-time parse failures are caught early.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKBUDDY = ROOT / "packages" / "workbuddy"

_KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


class ValidationError(RuntimeError):
    """A package violates a WorkBuddy platform constraint."""


def _write_zip(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            archive.write(path, path.relative_to(source).as_posix())


def _png_dimensions(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n" or header[12:16] != b"IHDR":
        raise ValidationError(f"{path} 不是有效的 PNG 文件")
    width, height = struct.unpack(">II", header[16:24])
    return width, height


def _check(condition: bool, message: str) -> None:
    if not condition:
        raise ValidationError(message)


def _project_version() -> str:
    match = re.search(
        r'^version\s*=\s*"([^"]+)"',
        (ROOT / "pyproject.toml").read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    if not match:
        raise ValidationError("无法从 pyproject.toml 读取 version")
    return match.group(1)


def _build_wheel(work_dir: Path) -> Path:
    """从仓库构建 wheel，返回 wheel 路径。"""
    out_dir = work_dir / "wheel"
    out_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(out_dir), str(ROOT)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise ValidationError(f"wheel 构建失败（需要 pip install build）：\n{proc.stderr[-2000:]}")
    wheels = list(out_dir.glob("*.whl"))
    _check(len(wheels) == 1, "wheel 构建产物不唯一")
    return wheels[0]


def stage_connector(work_dir: Path) -> Path:
    """把 connector/ 复制到暂存目录，注入 cli.json 与内置 wheel，返回暂存路径。"""
    staged = work_dir / "connector"
    shutil.copytree(WORKBUDDY / "connector", staged)
    (staged / "cli.template.json").unlink()

    wheel = _build_wheel(work_dir)
    pkg = staged / "pkg"
    pkg.mkdir(exist_ok=True)
    shutil.copy2(wheel, pkg / wheel.name)

    template = (WORKBUDDY / "connector" / "cli.template.json").read_text(encoding="utf-8")
    (staged / "cli.json").write_text(
        template.replace("${WHEEL_FILENAME}", wheel.name), encoding="utf-8"
    )
    return staged


def stage_expert(work_dir: Path) -> Path:
    """暂存可独立运行的专家包，注入内嵌 Skill、MCP 配置和核心 wheel。"""
    staged = work_dir / "expert"
    shutil.copytree(WORKBUDDY / "expert", staged)

    wheel = _build_wheel(work_dir)
    pkg = staged / "pkg"
    pkg.mkdir(exist_ok=True)
    shutil.copy2(wheel, pkg / wheel.name)

    skill_source = WORKBUDDY / "connector" / "skills" / "good-student"
    shutil.copytree(skill_source, staged / "skills" / "good-student")

    cli_template = (staged / "cli.template.json").read_text(encoding="utf-8")
    (staged / "cli.json").write_text(
        cli_template.replace("${WHEEL_FILENAME}", wheel.name), encoding="utf-8"
    )
    (staged / "cli.template.json").unlink()
    (staged / ".mcp.json").write_text(
        (staged / ".mcp.template.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    (staged / ".mcp.template.json").unlink()
    return staged


def validate_expert(source: Path) -> None:
    """Validate the expert package against WorkBuddy platform rules."""
    plugin_path = source / ".codebuddy-plugin" / "plugin.json"
    _check(plugin_path.is_file(), "缺少 .codebuddy-plugin/plugin.json")
    plugin = json.loads(plugin_path.read_text(encoding="utf-8"))

    _check(_KEBAB_RE.match(plugin.get("name", "")) is not None, "name 必须是小写 kebab-case")
    _check(plugin.get("expertType") == "agent", "expertType 必须为 agent")
    agent_name = plugin.get("agentName", "")
    agent_file = source / "agents" / f"{agent_name}.md"
    _check(agent_name and agent_file.is_file(), f"缺少 agents/{agent_name}.md")
    _check(agent_file.read_text(encoding="utf-8").lstrip().startswith("---"),
           f"agents/{agent_name}.md 缺少 YAML frontmatter")

    desc_zh = plugin.get("displayDescription", {}).get("zh", "")
    _check(40 <= len(desc_zh) <= 50,
           f"displayDescription.zh 必须 40-50 字，当前 {len(desc_zh)} 字")

    for field in ("displayName", "profession", "displayDescription", "defaultInitPrompt"):
        value = plugin.get(field, {})
        _check(bool(value.get("zh")) and bool(value.get("en")),
               f"{field} 必须同时提供 zh 和 en")

    tags = plugin.get("tags", [])
    _check(len(tags) == 3, f"tags 必须固定 3 个，当前 {len(tags)} 个")
    quick = plugin.get("quickPrompts", [])
    _check(len(quick) == 3, f"quickPrompts 必须固定 3 个，当前 {len(quick)} 个")
    init = plugin.get("defaultInitPrompt", {})
    _check(init.get("zh") == quick[0].get("zh") and init.get("en") == quick[0].get("en"),
           "defaultInitPrompt 必须与第一条 quickPrompt 一致")

    _check(plugin.get("categoryId", "").startswith("15-"), "categoryId 应为 15-Education")
    dependencies = plugin.get("dependencies", {})
    _check(dependencies.get("mcpServers") == "./.mcp.json",
           "dependencies.mcpServers 必须指向内嵌 .mcp.json")
    _check(not dependencies.get("connectors"), "独立专家包不得依赖市场连接器")
    _check(plugin.get("skills") == ["./skills/good-student"],
           "专家必须预加载内嵌 good-student Skill")

    mcp = json.loads((source / ".mcp.json").read_text(encoding="utf-8"))
    _check(mcp.get("preAuth") == "cli", ".mcp.json 必须通过 preAuth=cli 安装内嵌 wheel")
    servers = mcp.get("mcpServers", {})
    _check(set(servers) == {"good-student"}, ".mcp.json 必须仅声明 good-student Server")
    server = servers["good-student"]
    _check(server.get("type") == "stdio", "内嵌 MCP 必须使用 stdio")
    _check(server.get("command") == "good-student-mcp", "MCP 启动命令必须为 good-student-mcp")
    _check(server.get("x-workbuddy", {}).get("auth", {}).get("type") == "none",
           "本地 MCP 必须声明无需认证")

    cli = json.loads((source / "cli.json").read_text(encoding="utf-8"))
    _check(cli.get("runtime", {}).get("type") == "python", "专家 cli.json 必须声明 Python 运行时")
    wheels = list((source / "pkg").glob("*.whl"))
    _check(len(wheels) == 1, "专家 pkg/ 必须有且仅有一个内置 wheel")
    for platform, command in cli.get("init", {}).items():
        _check(platform in {"darwin", "linux", "win32"}, f"未知平台：{platform}")
        _check(f"./pkg/{wheels[0].name}[mcp]" in command,
               f"init.{platform} 必须安装内置 wheel 的 mcp extra")
    _check(set(cli.get("init", {})) == {"darwin", "linux", "win32"},
           "专家 cli.json init 必须覆盖 darwin/linux/win32")
    _check((source / "skills" / "good-student" / "SKILL.md").is_file(),
           "专家包缺少内嵌 good-student Skill")

    avatar = source / plugin.get("avatar", "")
    _check(avatar.is_file(), f"缺少头像 {plugin.get('avatar')}")
    _check(avatar.stat().st_size < 500 * 1024,
           f"头像超过 500KB：{avatar.stat().st_size} bytes")
    _check(_png_dimensions(avatar) == (512, 512), "头像必须是 512×512 PNG")


def validate_connector(source: Path) -> None:
    """校验**暂存后**的连接器目录（含构建期生成的 cli.json 与 pkg/*.whl）。"""
    meta = json.loads((source / "connector-meta.json").read_text(encoding="utf-8"))
    _check(_KEBAB_RE.match(meta.get("source", "")) is not None, "connector source 必须 kebab-case")
    _check(meta.get("type") == "cli", "connector type 必须为 cli（CLI + Skill 方案）")
    for field in ("name", "name_zh", "name_en", "description", "description_zh", "description_en"):
        _check(bool(meta.get(field)), f"connector-meta.json 缺少 {field}")
    for field in ("examples_zh", "examples_en"):
        examples = meta.get(field, [])
        _check(2 <= len(examples) <= 5, f"{field} 建议 2-5 条，当前 {len(examples)} 条")
    _check("minWorkbuddyVersion" in meta,
           "使用 runtime python（5.0.0）必须声明 minWorkbuddyVersion")

    cli = json.loads((source / "cli.json").read_text(encoding="utf-8"))
    _check(cli.get("runtime", {}).get("type") == "python", "cli.json 必须声明 python 托管运行时")
    init = cli.get("init", {})
    _check(set(init) == {"darwin", "linux", "win32"}, "cli.json init 必须覆盖 darwin/linux/win32")
    wheels = list((source / "pkg").glob("*.whl"))
    _check(len(wheels) == 1, "pkg/ 必须有且仅有一个内置 wheel")
    for platform, command in init.items():
        _check(f"./pkg/{wheels[0].name}" in command,
               f"init.{platform} 必须安装内置 wheel ./pkg/{wheels[0].name}")
    forbidden = ("uvx", "token", "secret", "password")
    blob = json.dumps(cli).lower() + json.dumps(meta).lower()
    for word in forbidden:
        _check(word not in blob, f"连接器配置不得包含 {word}")
    _check(not (source / "mcp.json").exists(), "CLI 方案不得再携带 mcp.json")
    _check((source / "icon.svg").is_file(), "缺少 icon.svg")
    _check((source / "skills" / "good-student" / "SKILL.md").is_file(),
           "缺少连接器内嵌 Skill（CLI 方案强烈推荐）")


def validate_skill(source: Path) -> None:
    skill = source / "skills" / "good-student" / "SKILL.md"
    _check(skill.is_file(), "缺少 skills/good-student/SKILL.md")
    text = skill.read_text(encoding="utf-8")
    _check(text.lstrip().startswith("---"), "SKILL.md 缺少 YAML frontmatter")
    for field in ("name:", "description:", "description_zh:", "description_en:", "version:", "author:"):
        _check(field in text, f"SKILL.md frontmatter 缺少 {field}")


def build(output_dir: Path) -> list[Path]:
    with tempfile.TemporaryDirectory() as tmp:
        staged_connector = stage_connector(Path(tmp))
        staged_expert = stage_expert(Path(tmp))
        validate_skill(WORKBUDDY / "skill")
        validate_connector(staged_connector)
        validate_expert(staged_expert)
        archives = [
            (WORKBUDDY / "skill", output_dir / "good-student-workbuddy-skill.zip"),
            (staged_connector, output_dir / "good-student-workbuddy-connector.zip"),
            (staged_expert, output_dir / "good-student-workbuddy-expert.zip"),
        ]
        built = []
        for source, destination in archives:
            _write_zip(source, destination)
            built.append(destination)
    return built


def main() -> int:
    parser = argparse.ArgumentParser(description="构建并校验 WorkBuddy Skill、CLI 连接器、专家上传包")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=WORKBUDDY / "dist",
        help="输出目录，默认 packages/workbuddy/dist",
    )
    args = parser.parse_args()
    try:
        built = build(args.output_dir)
    except ValidationError as exc:
        parser.exit(1, f"校验失败：{exc}\n")
    for archive in built:
        print(archive)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
