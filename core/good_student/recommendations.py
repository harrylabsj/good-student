"""建议引擎 v1：错因 × 动作 × 复测模板（设计 §15）。

每条建议必须包含：为什么推荐、本次做什么、时长与题量、何时复测、通过标准、未通过后手。
unknown 错因不生成强结论，转为请求家长/老师确认。
"""

from good_student import clock
from good_student.models import ACTIONABLE_WEAKNESS_STATUSES, ERROR_REASON_LABELS

TEMPLATES: dict[str, dict] = {
    "concept_gap": {
        "action": "请孩子口头解释该概念，再精读一个 worked example，最后完成 2 道基础题",
        "duration_minutes": 25,
        "question_count": 3,
        "reassessment_method": "同概念、不同表述的新变式题，无提示完成",
        "interval_days": 3,
        "acceptance_criteria": "新变式题无提示正确率 ≥ 80%",
    },
    "procedure_gap": {
        "action": "把解题步骤写成步骤模板，第一遍照模板做，之后逐步撤掉提示",
        "duration_minutes": 20,
        "question_count": 3,
        "reassessment_method": "无步骤提示完成 2 道同型题",
        "interval_days": 3,
        "acceptance_criteria": "无提示完成 2 道同型题且全部正确",
    },
    "misread": {
        "action": "练习圈出题目关键词，并用自己的话复述已知条件与所求",
        "duration_minutes": 15,
        "question_count": 4,
        "reassessment_method": "信息密度更高的变式题",
        "interval_days": 2,
        "acceptance_criteria": "变式题正确率 ≥ 75% 且不再出现审题类错误",
    },
    "calculation": {
        "action": "定位出错的具体运算规则，做短时、少量的精准计算练习",
        "duration_minutes": 10,
        "question_count": 10,
        "reassessment_method": "限时但低题量的计算题",
        "interval_days": 2,
        "acceptance_criteria": "限时内正确率 ≥ 90%",
    },
    "memory": {
        "action": "用间隔复习 + 主动回忆巩固需要记忆的内容",
        "duration_minutes": 10,
        "question_count": 5,
        "reassessment_method": "1/3/7/14 天间隔回忆测试",
        "interval_days": 1,
        "acceptance_criteria": "14 天内各次回忆测试正确率 ≥ 80%",
    },
    "careless_checking": {
        "action": "使用固定检查清单逐项自检，不追加概念训练",
        "duration_minutes": 10,
        "question_count": 4,
        "reassessment_method": "完成后强制自检再提交",
        "interval_days": 2,
        "acceptance_criteria": "自检后提交正确率 ≥ 90%",
    },
}

NEXT_STEP_IF_FAIL = (
    "复测未通过：该知识点回到/保持疑似薄弱状态，缩短练习间隔并降低题目难度，3 天后再次复测；"
    "连续两次未通过建议请老师一起确认知识点与错因"
)

# memory 错因的间隔复习节奏（天）：1/3/7/14 主动回忆（设计 §15）
SPACED_REVIEW_DAYS = (1, 3, 7, 14)


def build_actions(
    analysis_result: dict, reason_by_kc: dict[str, str], now: str | None = None
) -> tuple[list[dict], list[dict]]:
    """从分析结果生成学习动作。返回 (actions, skipped)；skipped 为需要先确认错因的知识点。"""
    now = now or clock.iso()
    actions: list[dict] = []
    skipped: list[dict] = []

    for weakness in analysis_result["weaknesses"]:
        if weakness["status"] not in ACTIONABLE_WEAKNESS_STATUSES:
            continue
        kc = weakness["knowledge_component"]
        reason = reason_by_kc.get(kc["id"])
        if not reason or reason == "unknown" or reason not in TEMPLATES:
            skipped.append(
                {
                    "knowledge_component": kc,
                    "reason": "error_reason_unknown",
                    "message": (
                        f"「{kc['canonical_name']}」的错因尚未确认，请先与孩子/老师确认错因"
                        "（对照错因清单），确认后再生成针对性建议"
                    ),
                }
            )
            continue
        template = TEMPLATES[reason]
        evidence_count = weakness["evidence_count"]
        status_label = {
            "suspected_weakness": "疑似薄弱",
            "evidenced_weakness": "已证实薄弱",
            "review_due": "待复习（记忆衰减）",
        }[weakness["status"]]
        why = (
            f"基于 {evidence_count} 条已确认证据，该知识点当前为「{status_label}」"
            f"（置信度 {weakness['confidence_level']}），主要错因："
            f"{ERROR_REASON_LABELS[reason]}；证据编号："
            f"{', '.join(e['attempt_id'][:8] for e in weakness['evidence'] if not e['is_correct'])}"
        )
        prerequisite_hint = next(
            (
                h["message"]
                for h in weakness.get("prerequisite_hints", [])
                if h["status_hint"] == "prerequisite_also_weak"
            ),
            None,
        )
        if prerequisite_hint:
            why += f"；{prerequisite_hint}"
        action = {
            "kc_id": kc["id"],
            "kc_name": kc["canonical_name"],
            "subject_id": kc["subject_id"],
            "error_reason": reason,
            "why": why,
            "action": template["action"],
            "duration_minutes": template["duration_minutes"],
            "question_count": template["question_count"],
            "due_date": clock.add_days(now, template["interval_days"]),
            "reassessment_method": template["reassessment_method"],
            "acceptance_criteria": template["acceptance_criteria"],
            "next_step_if_fail": NEXT_STEP_IF_FAIL,
            "prerequisite_hint": prerequisite_hint,
        }
        if reason == "memory":
            action["review_schedule"] = [
                clock.add_days(now, day) for day in SPACED_REVIEW_DAYS
            ]
        actions.append(action)
    return actions, skipped
