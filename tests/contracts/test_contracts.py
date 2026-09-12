"""合约测试：工具清单、Schema 一致性、MCP 注册、发布校验脚本。"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from good_student.service import TOOLS
from good_student.validation import validate

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPECTED_TOOLS = {
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
}


def test_tool_list_matches_design_section16():
    assert set(TOOLS) == EXPECTED_TOOLS
    assert len(TOOLS) == 15


def test_example_batch_validates_against_schema():
    batch = json.loads((REPO_ROOT / "examples" / "batch.json").read_text(encoding="utf-8"))
    assert validate(batch, "candidate-batch") == []


def test_closed_loop_output_validates_against_analysis_schema(tmp_path):
    from good_student.service import Service

    service = Service(tmp_path)
    try:
        student = service.create_student("小美")["data"]["student"]["id"]
        batch = json.loads((REPO_ROOT / "examples" / "batch.json").read_text(encoding="utf-8"))
        assert service.ingest_candidates(student, batch)["ok"]
        pending = service.list_pending(student)["data"]["pending"]
        items = [{"candidate_id": p["candidate_id"], "action": "confirm"} for p in pending]
        assert service.confirm_questions(student, items)["ok"]

        result = service.analyze(student)
        assert result["ok"]
        assert validate(result["data"], "analysis-result") == []

        plan = service.create_plan(student)
        assert plan["ok"]
        assert plan["data"]["actions"] or plan["data"]["skipped"]
    finally:
        service.close()


def test_validate_packages_script():
    spec = importlib.util.spec_from_file_location(
        "validate_packages", REPO_ROOT / "scripts" / "validate_packages.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main() == 0


def test_mcp_server_registers_all_tools():
    pytest.importorskip("mcp")
    spec = importlib.util.spec_from_file_location(
        "gs_mcp_server", REPO_ROOT / "mcp" / "server.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("gs_mcp_server", module)
    spec.loader.exec_module(module)
    for name in EXPECTED_TOOLS:
        tool = getattr(module, name, None)
        assert tool is not None, f"MCP 未注册工具 {name}"
