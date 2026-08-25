#!/usr/bin/env python3
"""校验共享单一源文件（Schema/prompt/Skill）存在且可解析，防止分发漂移。"""

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


def main() -> int:
    failed = []
    for rel in SCHEMAS:
        path = ROOT / rel
        if not path.is_file():
            failed.append(f"missing: {rel}")
            continue
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failed.append(f"invalid json: {rel}: {exc}")
    for rel in SHARED:
        if not (ROOT / rel).is_file():
            failed.append(f"missing: {rel}")
    if failed:
        for line in failed:
            print(line, file=sys.stderr)
        return 1
    print(f"ok: {len(SCHEMAS)} schemas + {len(SHARED)} shared files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
