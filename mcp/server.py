"""Good-student stdio MCP server（设计 §16 工具合约的薄封装）。

业务逻辑全部在 good_student.service；本文件只做参数声明与转发，
保证 MCP 边界与 CLI/Python API 行为完全一致。
启动：python mcp/server.py （需 pip install 'good-student[mcp]'）
"""

import sys

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # pragma: no cover
    print(
        "缺少 MCP 依赖：pip install 'good-student[mcp]' 后重试",
        file=sys.stderr,
    )
    raise

from good_student.service import Service

mcp = MCPServer("good-student")
_service: Service | None = None


def _svc() -> Service:
    global _service
    if _service is None:
        _service = Service()
    return _service


@mcp.tool()
def good_student_capabilities() -> dict:
    """返回版本、宿主能力、支持输入与健康状态（只读）。"""
    return _svc().capabilities()


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
def good_student_ingest_candidates(
    student_id: str,
    batch: dict,
    host: str = "core",
    ttl_hours: int = 24,
    captured_at: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """校验并保存临时候选批次（写入临时区，不进入画像）。batch 必须符合 candidate-wrong-question-batch Schema。"""
    return _svc().ingest_candidates(student_id, batch, host, ttl_hours, captured_at, idempotency_key)


@mcp.tool()
def good_student_list_pending(student_id: str) -> dict:
    """列出待确认题目（只读）。低置信字段与需要关注的字段会标记 attention。"""
    return _svc().list_pending(student_id)


@mcp.tool()
def good_student_confirm_questions(
    student_id: str,
    items: list[dict],
    idempotency_key: str | None = None,
) -> dict:
    """确认、修改或拒绝候选（写）。items: [{candidate_id, action: confirm|reject, edits?{...}}]。"""
    return _svc().confirm_questions(student_id, items, idempotency_key)


@mcp.tool()
def good_student_analyze(student_id: str, persist_snapshot: bool = True) -> dict:
    """生成疑似薄弱点与证据（只读；persist_snapshot=true 时保存当前快照）。"""
    return _svc().analyze(student_id, persist_snapshot)


@mcp.tool()
def good_student_create_plan(student_id: str, idempotency_key: str | None = None) -> dict:
    """从已确认分析生成本周学习动作（写）。每条动作含验收标准与复测方式。"""
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
    """记录复测结果（写）。新变式 + 无提示的复测才会推进/回退知识点状态。"""
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
def good_student_export_student(student_id: str) -> dict:
    """导出单个学生全部数据（只读）。"""
    return _svc().export_student(student_id)


@mcp.tool()
def good_student_delete_student(
    student_id: str,
    confirm_phrase: str | None = None,
    idempotency_key: str | None = None,
) -> dict:
    """删除学生及关联数据（破坏性写）。必须两步调用：先不带 confirm_phrase 查看范围，再带学生显示名确认。"""
    return _svc().delete_student(student_id, confirm_phrase, idempotency_key)


@mcp.tool()
def good_student_doctor() -> dict:
    """校验 Schema、SQLite 完整性、迁移版本与外键（只读）。"""
    return _svc().doctor()


if __name__ == "__main__":
    mcp.run()
