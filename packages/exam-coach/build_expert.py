"""Build an independent WorkBuddy expert ZIP for an existing HTTPS OAuth MCP."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from exam_coach.server import require_https

TEMPLATE = Path(__file__).parent / "workbuddy"


def build(endpoint: str, output: Path) -> None:
    endpoint = require_https(endpoint, resource=True)
    manifest = json.loads((TEMPLATE / ".codebuddy-plugin" / "plugin.json").read_text(encoding="utf-8"))
    if (manifest["name"] != "exam-gap-coach"
            or not 40 <= len(manifest["displayDescription"]["zh"]) <= 50
            or len(manifest["quickPrompts"]) != 3 or len(manifest["tags"]) != 3
            or manifest["defaultInitPrompt"] != manifest["quickPrompts"][0]):
        raise ValueError("专家清单不满足 WorkBuddy 展示字段约束")
    config = {"mcpServers": {"exam-gap-coach": {
        "type": "http", "url": endpoint,
        "x-workbuddy": {
            "displayName": {"zh": "真题与学生档案", "en": "Exams and student records"},
            "description": {
                "zh": "授权后查询真题并保存本账号学习记录。",
                "en": "Access your exam and learning records.",
            },
            "icon": "./avatars/expert.png", "auth": {"type": "oauth"},
        },
    }}}
    entries = (
        ".codebuddy-plugin/plugin.json", "agents/exam-gap-coach.md",
        "skills/exam-coach/SKILL.md", "avatars/expert.png",
    )
    if (TEMPLATE / "avatars/expert.png").stat().st_size > 500_000:
        raise ValueError("专家头像超过 500 KB")
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for path in entries:
            archive.write(TEMPLATE / path, arcname=path)
        archive.writestr(".mcp.json", json.dumps(config, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mcp-url", required=True)
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    build(options.mcp_url, options.output)
