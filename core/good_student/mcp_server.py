"""Installed stdio MCP entry point for Good-student.

The WorkBuddy connector starts ``good-student-mcp`` after installing the
package.  The compatibility module at ``mcp/server.py`` re-exports this
server so existing source-tree workflows keep working.
"""

from __future__ import annotations

import sys
import threading

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    print(
        "MCP 服务器需要 mcp>=2.0：pip install 'good-student[mcp]' 后重试",
        file=sys.stderr,
    )
    raise

from good_student.service import Service

mcp = MCPServer("good-student")
_service: Service | None = None
_service_lock = threading.Lock()

__all__ = [
    "mcp",
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


def _svc() -> Service:
    """进程级单例；MCP SDK 在 worker 线程池并发执行同步工具，初始化必须加锁。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = Service()
    return _service


@mcp.tool()
def good_student_capabilities() -> dict:
    """返回版本、宿主能力、支持输入与健康状态（只读）。"""
    return _svc().capabilities()


@mcp.tool()
def good_student_list_students() -> dict:
    """列出已有学生档案及其 UUID，用于在新会话中继续使用既有记录（只读）。"""
    return _svc().list_students()


@mcp.tool()
def good_student_create_student(
    display_name: str,
    grade: str | None = None,
    active_subjects: list[str] | None = None,
    goals: list[str] | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """建立 UUID 学生档案（写）。display_name 仅为展示名，不是主键。"""
    return _svc().create_student(display_name, grade, active_subjects, goals, idempotency_key)


@mcp.tool()
def good_student_record_scores(
    student_id: str,
    records: list[dict],
    idempotency_key: str | None = None,
) -> dict:
    """原子保存一批结构化成绩。每条至少含 subject、assessment_name、assessed_at 和 score/grade_label。"""
    return _svc().record_scores(student_id, records, idempotency_key)


@mcp.tool()
def good_student_list_scores(
    student_id: str,
    subject: str | None = None,
    assessment_type: str | None = None,
    term: str | None = None,
    from_date: str | None = None,
    to_date: str | None = None,
) -> dict:
    """查询成绩，可按学科、类型、学期和时间区间筛选，并返回分科汇总。"""
    return _svc().list_scores(student_id, subject, assessment_type, term, from_date, to_date)


@mcp.tool()
def good_student_ingest_candidates(
    student_id: str,
    batch: dict,
    host: str = "core",
    ttl_hours: int = 24,
    captured_at: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """校验并保存临时候选批次（写入临时区，不进入画像）。"""
    return _svc().ingest_candidates(student_id, batch, host, ttl_hours, captured_at, idempotency_key)


@mcp.tool()
def good_student_list_pending(student_id: str) -> dict:
    """列出待确认题目（只读）。"""
    return _svc().list_pending(student_id)


@mcp.tool()
def good_student_confirm_questions(
    student_id: str,
    items: list[dict],
    idempotency_key: str | None = None,
) -> dict:
    """确认、修改或拒绝候选（写）。"""
    return _svc().confirm_questions(student_id, items, idempotency_key)


@mcp.tool()
def good_student_analyze(student_id: str, persist_snapshot: bool = True) -> dict:
    """生成疑似薄弱点与证据（写）。"""
    return _svc().analyze(student_id, persist_snapshot)


@mcp.tool()
def good_student_create_plan(student_id: str, idempotency_key: str | None = None) -> dict:
    """从已确认分析生成本周学习动作（写）。"""
    return _svc().create_plan(student_id, idempotency_key)


@mcp.tool()
def good_student_record_reassessment(
    student_id: str,
    kc_id: str,
    correct_count: int,
    total_count: int,
    is_new_variant: bool = True,
    no_hints: bool = True,
    action_id: str | None = None,
    time_seconds: int | None = None,
    self_reported_confidence: float | None = None,
    completed_at: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """记录复测结果（写）。新变式 + 无提示的复测才会推进状态。"""
    return _svc().record_reassessment(
        student_id,
        kc_id,
        correct_count,
        total_count,
        is_new_variant,
        no_hints,
        action_id,
        time_seconds,
        self_reported_confidence,
        completed_at,
        idempotency_key,
    )


@mcp.tool()
def good_student_weekly_brief(student_id: str) -> dict:
    """每周家长简报（只读）：本周新增错题、薄弱点状态、待办动作、到期复习与复测完成情况。"""
    return _svc().weekly_brief(student_id)


@mcp.tool()
def good_student_export_student(student_id: str) -> dict:
    """导出单个学生全部数据（只读）。"""
    return _svc().export_student(student_id)


@mcp.tool()
def good_student_delete_student(
    student_id: str,
    confirm_phrase: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """删除学生及关联数据（破坏性写）。必须使用学生显示名确认。"""
    return _svc().delete_student(student_id, confirm_phrase, idempotency_key)


@mcp.tool()
def good_student_doctor() -> dict:
    """校验 Schema、SQLite 完整性、迁移版本与外键（只读）。"""
    return _svc().doctor()


def main() -> None:
    """Run the stdio MCP server."""
    mcp.run()


if __name__ == "__main__":
    main()
