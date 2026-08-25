"""枚举与常量：状态机（设计 §12）、错因体系（设计 §15）与确认编辑约束。"""

from enum import Enum


class CandidateStatus(str, Enum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    EXTRACTION_FAILED = "extraction_failed"
    RETRYING = "retrying"
    NEEDS_CONFIRMATION = "needs_confirmation"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CONFIRMED = "confirmed"
    ANALYZED = "analyzed"
    ARCHIVED = "archived"


class WeaknessStatus(str, Enum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    SUSPECTED = "suspected_weakness"
    EVIDENCED = "evidenced_weakness"
    IMPROVING = "improving"
    MASTERED = "mastered"
    REVIEW_DUE = "review_due"


# 错因代码 -> 中文标签（设计 §15）
ERROR_REASON_LABELS = {
    "concept_gap": "概念不清",
    "procedure_gap": "步骤错误",
    "misread": "审题错误",
    "calculation": "计算错误",
    "memory": "记忆遗忘",
    "careless_checking": "检查不足",
    "unknown": "未确认",
}

# 确认阶段允许家长修改的字段（设计 §16 good_student_confirm_questions）
CONFIRM_EDIT_FIELDS = {
    "subject",
    "grade",
    "question_text",
    "student_answer",
    "correct_answer",
    "is_wrong",
    "error_reason",
    "knowledge_labels",
    "attempted_at",
    "correction_status",
}

CORRECTION_STATUSES = {"uncorrected", "corrected"}

# 低置信阈值：低于该值或含 uncertain_fields 的候选在确认界面需要突出显示
LOW_CONFIDENCE_THRESHOLD = 0.8

# 独立复测（新变式 + 无提示）的通过线
PASS_ACCURACY_THRESHOLD = 0.8

# mastered 状态的记忆衰减复习间隔（天）
REVIEW_INTERVAL_DAYS = 14

DEFAULT_CANDIDATE_TTL_HOURS = 24
