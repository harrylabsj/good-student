"""MCP server 装配冒烟测试（H1）：mcp>=2.0 的 mcpserver 模块可导入、全部工具注册。

防止回归：pyproject 将 mcp 降至 1.x（模块布局不同）或工具名/数量漂移时此测试必挂。
"""

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TOOLS = [
    "good_student_capabilities",
    "good_student_list_students",
    "good_student_create_student",
    "good_student_record_scores",
    "good_student_list_scores",
    "good_student_ingest_candidates",
    "good_student_list_pending",
    "good_student_confirm_questions",
    "good_student_analyze",
    "good_student_create_plan",
    "good_student_record_reassessment",
    "good_student_weekly_brief",
    "good_student_export_student",
    "good_student_delete_student",
    "good_student_doctor",
]


def test_mcp_server_imports_and_registers_all_tools():
    pytest.importorskip("mcp")
    spec = importlib.util.spec_from_file_location(
        "mcp_server_good_student", REPO_ROOT / "mcp" / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("mcp_server_good_student", module)
    spec.loader.exec_module(module)

    names = asyncio.run(_list_tool_names(module.mcp))
    assert len(names) == len(EXPECTED_TOOLS)
    assert set(EXPECTED_TOOLS) <= set(names), f"缺少工具：{set(EXPECTED_TOOLS) - set(names)}"


async def _list_tool_names(mcp) -> list[str]:
    return [t.name for t in await mcp.list_tools()]
