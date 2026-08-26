"""Hermes 工具 Schema：模型可见的工具描述与参数规格（对齐 core TOOLS + 提取工具）。

description 是模型选择工具的依据，保持与 MCP server（mcp/server.py）同一语义。
"""

_IDEMPOTENCY = {
    "type": "string",
    "description": "幂等键；同一键重复调用返回首次结果，不重复写入",
}
_STUDENT_ID = {"type": "string", "description": "学生 UUID"}

TOOL_SCHEMAS = {
    "good_student_capabilities": {
        "name": "good_student_capabilities",
        "description": "能力发现与健康检查：返回版本、工具清单、宿主能力与数据目录。首次使用前先调用。",
        "parameters": {"type": "object", "properties": {}},
    },
    "good_student_create_student": {
        "name": "good_student_create_student",
        "description": "创建学生档案（UUID 主键，display_name 只是展示名）。多个孩子必须分开建档。",
        "parameters": {
            "type": "object",
            "properties": {
                "display_name": {"type": "string", "description": "展示名，1-64 字符"},
                "grade": {"type": "string", "description": "年级，如 五年级"},
                "active_subjects": {"type": "array", "items": {"type": "string"}},
                "goals": {"type": "array", "items": {"type": "string"}},
                "idempotency_key": _IDEMPOTENCY,
            },
            "required": ["display_name"],
        },
    },
    "good_student_extract_attachments": {
        "name": "good_student_extract_attachments",
        "description": (
            "模式 A：把错题附件（图片/PDF/文本）交给宿主模型做结构化识别，"
            "通过 Schema 校验后进入候选暂存区（24 小时未确认自动过期）。"
            "识别失败不写入任何数据。首次处理附件前须向用户说明材料将由宿主模型处理。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": _STUDENT_ID,
                "attachments": {
                    "type": "array",
                    "description": "宿主附件引用列表",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["path", "bytes", "url"],
                                "description": "path=宿主本地路径；bytes=已读入字节；url=远程引用",
                            },
                            "ref": {"type": "string", "description": "路径 / 展示名 / URL"},
                            "mime_type": {"type": "string", "description": "如 image/png、application/pdf"},
                        },
                        "required": ["kind", "ref"],
                    },
                },
                "source_type": {
                    "type": "string",
                    "enum": ["image", "pdf", "text", "markdown", "csv", "json"],
                    "description": "缺省时按附件内容推断",
                },
                "page_count": {"type": "integer", "minimum": 1, "maximum": 1000},
                "captured_at": {"type": "string", "description": "材料采集时间，ISO 8601"},
                "idempotency_key": _IDEMPOTENCY,
            },
            "required": ["student_id", "attachments"],
        },
    },
    "good_student_ingest_candidates": {
        "name": "good_student_ingest_candidates",
        "description": (
            "把符合 CandidateWrongQuestionBatch Schema 的候选批次写入暂存区"
            "（模式 B 人工/模型直出 JSON 时使用）。校验失败不写入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": _STUDENT_ID,
                "batch": {"type": "object", "description": "CandidateWrongQuestionBatch JSON"},
                "captured_at": {"type": "string"},
                "idempotency_key": _IDEMPOTENCY,
            },
            "required": ["student_id", "batch"],
        },
    },
    "good_student_list_pending": {
        "name": "good_student_list_pending",
        "description": "列出待确认候选。attention=true 的字段需家长重点核对。先确认、后分析。",
        "parameters": {
            "type": "object",
            "properties": {"student_id": _STUDENT_ID},
            "required": ["student_id"],
        },
    },
    "good_student_confirm_questions": {
        "name": "good_student_confirm_questions",
        "description": "家长逐题/批量确认或拒绝候选，可带字段级 edits。确认后才进入学习画像。",
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": _STUDENT_ID,
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "candidate_id": {"type": "string"},
                            "action": {"type": "string", "enum": ["confirm", "reject"]},
                            "edits": {"type": "object"},
                        },
                        "required": ["candidate_id"],
                    },
                },
                "idempotency_key": _IDEMPOTENCY,
            },
            "required": ["student_id", "items"],
        },
    },
    "good_student_analyze": {
        "name": "good_student_analyze",
        "description": (
            "对已确认作答生成薄弱点分析（疑似/已证实/改善中/已掌握/待复习）。"
            "证据不足时返回 insufficient，不得编造掌握率。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": _STUDENT_ID,
                "persist_snapshot": {"type": "boolean", "default": True},
            },
            "required": ["student_id"],
        },
    },
    "good_student_create_plan": {
        "name": "good_student_create_plan",
        "description": "按分析结果生成学习行动：每条含证据、动作、时长题量、复测日期、验收标准与未通过后手。",
        "parameters": {
            "type": "object",
            "properties": {"student_id": _STUDENT_ID, "idempotency_key": _IDEMPOTENCY},
            "required": ["student_id"],
        },
    },
    "good_student_record_reassessment": {
        "name": "good_student_record_reassessment",
        "description": "记录一次复测结果并刷新知识点状态。只有新变式 + 无提示的复测才推进状态。",
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": _STUDENT_ID,
                "kc_id": {"type": "string", "description": "知识点 UUID"},
                "correct_count": {"type": "integer", "minimum": 0},
                "total_count": {"type": "integer", "minimum": 1},
                "is_new_variant": {"type": "boolean", "default": True},
                "no_hints": {"type": "boolean", "default": True},
                "action_id": {"type": "string"},
                "time_seconds": {"type": "integer"},
                "self_reported_confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "completed_at": {"type": "string"},
                "idempotency_key": _IDEMPOTENCY,
            },
            "required": ["student_id", "kc_id", "correct_count", "total_count"],
        },
    },
    "good_student_export_student": {
        "name": "good_student_export_student",
        "description": "导出学生全部数据（JSON，format_version=1），用于备份或跨宿主迁移。",
        "parameters": {
            "type": "object",
            "properties": {"student_id": _STUDENT_ID},
            "required": ["student_id"],
        },
    },
    "good_student_delete_student": {
        "name": "good_student_delete_student",
        "description": (
            "删除学生全部数据。不带 confirm_phrase 调用时只返回删除范围；"
            "须用户明确同意后带 confirm_phrase=学生显示名再次调用才执行。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "student_id": _STUDENT_ID,
                "confirm_phrase": {"type": "string"},
                "idempotency_key": _IDEMPOTENCY,
            },
            "required": ["student_id"],
        },
    },
    "good_student_doctor": {
        "name": "good_student_doctor",
        "description": "本地数据与插件资产完整性检查（SQLite、Schema、提取指令、宿主桥）。",
        "parameters": {"type": "object", "properties": {}},
    },
}
