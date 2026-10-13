"""MCP server 装配冒烟测试（H1）：mcp>=2.0 的 mcpserver 模块可导入、全部工具注册。

防止回归：pyproject 将 mcp 降至 1.x（模块布局不同）或工具名/数量漂移时此测试必挂。
"""

import asyncio
import importlib.util
import json
import os
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


def test_stdio_server_end_to_end_closed_loop(tmp_path):
    """Exercise the same stdio transport used by the standalone embedded expert MCP."""
    pytest.importorskip("mcp")
    from mcp.client.stdio import StdioServerParameters, stdio_client

    from mcp import ClientSession

    async def run_flow():
        env = os.environ.copy()
        env["GOOD_STUDENT_DATA"] = str(tmp_path / "data")
        server = StdioServerParameters(
            command=sys.executable,
            args=["-m", "good_student.mcp_server"],
            env=env,
            cwd=REPO_ROOT,
        )
        async with stdio_client(server) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == set(EXPECTED_TOOLS)

                async def call(name, arguments=None):
                    result = await session.call_tool(name, arguments or {})
                    assert not result.is_error, result
                    return json.loads(result.content[0].text)

                assert (await call("good_student_doctor"))["data"]["ok"] is True
                created = await call(
                    "good_student_create_student",
                    {"display_name": "沙箱验收", "grade": "五年级"},
                )
                student_id = created["data"]["student"]["id"]
                batch = {
                    "schema_version": 1,
                    "source": {"source_type": "text", "source_ref": "stdio-smoke"},
                    "questions": [{
                        "source_locator": "page-1-question-1",
                        "subject": {"value": "数学", "confidence": 0.95},
                        "grade": {"value": "五年级", "confidence": 0.9},
                        "question_text": "计算 1/2 + 1/3",
                        "student_answer": "2/5",
                        "correct_answer": "5/6",
                        "is_wrong": True,
                        "knowledge_candidates": [{"label": "异分母分数加法", "confidence": 0.9}],
                        "error_reason_candidates": [{"code": "concept_gap", "confidence": 0.8}],
                        "extraction_confidence": 0.95,
                        "needs_confirmation": True,
                        "uncertain_fields": [],
                    }],
                }
                ingested = await call(
                    "good_student_ingest_candidates",
                    {"student_id": student_id, "batch": batch},
                )
                assert ingested["ok"] is True, ingested
                pending = await call("good_student_list_pending", {"student_id": student_id})
                candidate_id = pending["data"]["pending"][0]["candidate_id"]
                confirmed = await call(
                    "good_student_confirm_questions",
                    {"student_id": student_id, "items": [
                        {"candidate_id": candidate_id, "action": "confirm"}
                    ]},
                )
                assert confirmed["ok"] is True, confirmed
                analysis = await call("good_student_analyze", {"student_id": student_id})
                assert analysis["ok"] is True and analysis["data"]["weaknesses"]
                plan = await call("good_student_create_plan", {"student_id": student_id})
                assert plan["ok"] is True, plan
                reassessed = await call(
                    "good_student_record_reassessment",
                    {
                        "student_id": student_id,
                        "kc_id": analysis["data"]["weaknesses"][0]["knowledge_component"]["id"],
                        "correct_count": 4,
                        "total_count": 5,
                        "is_new_variant": True,
                        "no_hints": True,
                    },
                )
                assert reassessed["ok"] is True, reassessed
                assert (await call("good_student_weekly_brief", {"student_id": student_id}))["ok"]
                assert (await call("good_student_export_student", {"student_id": student_id}))["ok"]
                preview = await call("good_student_delete_student", {"student_id": student_id})
                assert preview["data"]["confirmation_required"] is True
                deleted = await call(
                    "good_student_delete_student",
                    {"student_id": student_id, "confirm_phrase": "沙箱验收"},
                )
                assert deleted["data"]["deleted"] is True

    asyncio.run(run_flow())
