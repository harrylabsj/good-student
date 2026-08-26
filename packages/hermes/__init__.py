"""Good-student Hermes 原生插件入口（plugin.yaml + register(ctx)，设计 §9/§17）。

职责（§7.1）：注册工具、解析宿主附件引用、通过 ctx.llm.complete_structured()
调用宿主模型做结构化识别（模式 A）、把候选交给 core 校验、管理数据目录、健康检查。
业务逻辑全部在 core/good_student/，本包只做宿主桥接与编排。

Hermes 运行时在本仓库测试中不可用：核心逻辑经 HermesHostBridge 协议隔离，
测试使用 testing.FakeHermesBridge。register() 仅在 Hermes 加载插件时执行。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from .bridge import HermesBridge
from .plugin import EXTRACT_TOOL, GoodStudentPlugin
from .tool_schemas import TOOL_SCHEMAS

logger = logging.getLogger(__name__)

TOOLSET = "good-student"
SKILL_NAME = "good-student"


def _json_handler(fn):
    """Hermes 工具处理器约定：接收 args dict，永远返回 envelope JSON 字符串，不抛异常。"""

    def handler(args: dict, **kwargs) -> str:
        try:
            envelope = fn(**(args or {}))
        except TypeError as exc:
            envelope = {
                "ok": False,
                "data": None,
                "warnings": [],
                "error": {"code": "invalid_argument", "message": f"参数错误：{exc}"},
                "trace_id": str(uuid.uuid4()),
            }
        except Exception as exc:  # noqa: BLE001 — 工具处理器不得抛出
            envelope = {
                "ok": False,
                "data": None,
                "warnings": [],
                "error": {"code": "plugin_internal_error", "message": str(exc)},
                "trace_id": str(uuid.uuid4()),
            }
        return json.dumps(envelope, ensure_ascii=False)

    return handler


def _register_skills(ctx: Any) -> None:
    skills_dir = Path(__file__).parent / "skills"
    if not skills_dir.is_dir():
        return
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)


def register(ctx: Any) -> None:
    """Hermes 插件入口：启动时调用一次。异常只会禁用本插件，不影响宿主。"""
    bridge = HermesBridge(ctx)
    plugin = GoodStudentPlugin(bridge)

    dispatch = {
        "good_student_capabilities": plugin.capabilities,
        "good_student_create_student": plugin.create_student,
        "good_student_ingest_candidates": plugin.ingest_candidates,
        "good_student_list_pending": plugin.list_pending,
        "good_student_confirm_questions": plugin.confirm_questions,
        "good_student_analyze": plugin.analyze,
        "good_student_create_plan": plugin.create_plan,
        "good_student_record_reassessment": plugin.record_reassessment,
        "good_student_export_student": plugin.export_student,
        "good_student_delete_student": plugin.delete_student,
        "good_student_doctor": plugin.doctor,
        EXTRACT_TOOL: plugin.extract_and_ingest,
    }
    for name, fn in dispatch.items():
        ctx.register_tool(
            name=name,
            toolset=TOOLSET,
            schema=TOOL_SCHEMAS[name],
            handler=_json_handler(fn),
        )
    _register_skills(ctx)
    logger.debug("good-student: 已注册 %d 个工具与 namespaced Skill", len(dispatch))
