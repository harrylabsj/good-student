"""旧数据迁移器（设计 §22）：student-companion-agent → good-student。

只读读取旧版单文件 JSON 数据（默认 ``~/.local/share/student-companion-agent/student-data.json``）：

- 旧 ``students[name]`` 映射为稳定 UUID5 的 Student（display_name 取旧 name）；
- ``score``/``homework``/``progress``/``evidence`` 映射为 ``legacy_records`` 科目概览证据，
  整场考试分数绝不写 attempts，因此不生成题目级掌握结论；
- 迁移前自动备份目标库，全部写入在单事务内完成，失败整体回滚；
- 幂等：学生与记录均使用旧数据的稳定 UUID5，重复迁移不产生重复行；
- 只读保证：旧目录仅以只读方式打开，不创建锁文件、不写临时文件。
"""

import hashlib
import json
import math
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from good_student import clock
from good_student.errors import GoodStudentError
from good_student.storage import Store

LEGACY_APP = "student-companion-agent"
LEGACY_DATA_FILENAME = "student-data.json"

# 固定命名空间（勿改）：保证同一旧 name/记录跨机器、跨重跑得到同一 UUID
LEGACY_NAMESPACE = uuid.UUID("7f1c2a3e-9b4d-4e5a-8c6f-2d1b0a9e8c7d")

RECORD_TYPES = ("score", "homework", "progress", "evidence")

_VALID_HOMEWORK_STATUS = {"completed", "needs_review", "missing", "late"}
_VALID_PROGRESS_STATUS = {"not_started", "learning", "blocked", "reviewing", "mastered"}
_VALID_EVIDENCE_TYPE = {"image", "audio", "file", "text"}


def student_uuid(name: str) -> str:
    """旧学生 name 的稳定 UUID5，重复迁移命中同一主键。"""
    return str(uuid.uuid5(LEGACY_NAMESPACE, f"student:{name}"))


def record_uuid(name: str, record: dict) -> str:
    """旧记录的稳定 UUID5：同一学生同一条记录重跑幂等。"""
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True)
    return str(uuid.uuid5(LEGACY_NAMESPACE, f"record:{name}:{canonical}"))


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def resolve_legacy_file(legacy_path: Path) -> Path:
    """接受旧数据目录或 student-data.json 文件路径，返回数据文件路径。"""
    path = Path(legacy_path).expanduser()
    if path.is_dir():
        path = path / LEGACY_DATA_FILENAME
    if not path.is_file():
        raise GoodStudentError(
            "not_found",
            f"旧数据文件不存在：{path}（目录需包含 {LEGACY_DATA_FILENAME}）",
        )
    return path


def load_legacy_store(legacy_file: Path) -> dict:
    """只读加载旧数据文件；JSON 损坏或结构非法时报错，不写任何数据。"""
    try:
        with legacy_file.open("r", encoding="utf-8") as handle:
            store = json.load(handle)
    except json.JSONDecodeError as exc:
        raise GoodStudentError(
            "legacy_data_corrupt", f"旧数据文件不是合法 JSON：{legacy_file}（{exc}）"
        ) from exc
    except UnicodeDecodeError as exc:
        raise GoodStudentError(
            "legacy_data_corrupt", f"旧数据文件不是合法 UTF-8：{legacy_file}（{exc}）"
        ) from exc
    if not isinstance(store, dict) or not isinstance(store.get("students"), dict):
        raise GoodStudentError(
            "legacy_data_corrupt",
            f"旧数据结构非法：{legacy_file} 应为含 students 对象的 JSON",
        )
    return store


def _validate_record(record: Any) -> str | None:
    """返回 None 表示可迁移，否则返回跳过原因。校验规则对齐旧 CLI 的写入校验。"""
    if not isinstance(record, dict):
        return "记录不是对象"
    record_type = record.get("type")
    if record_type not in RECORD_TYPES:
        return f"未知记录类型：{record_type!r}"
    if record_type == "score":
        try:
            score = float(record.get("score"))
            max_score = float(record.get("max_score"))
        except (TypeError, ValueError):
            return "score/max_score 不是数字"
        if not math.isfinite(score) or not math.isfinite(max_score):
            return "score/max_score 不是有限数字"
        if max_score <= 0 or not 0 <= score <= max_score:
            return f"分数越界：{score}/{max_score}"
    elif record_type == "homework" and record.get("status") not in _VALID_HOMEWORK_STATUS:
        return f"非法作业状态：{record.get('status')!r}"
    elif record_type == "progress" and record.get("status") not in _VALID_PROGRESS_STATUS:
        return f"非法进度状态：{record.get('status')!r}"
    elif record_type == "evidence" and record.get("source_type") not in _VALID_EVIDENCE_TYPE:
        return f"非法证据类型：{record.get('source_type')!r}"
    return None


def _profile_timestamp(profile: dict, key: str) -> str | None:
    value = profile.get(key)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return clock.to_utc_iso(value)
    except ValueError:
        return None


def build_plan(store: dict) -> tuple[list[dict], list[dict], list[str]]:
    """把旧数据解析为迁移计划（纯函数，不触碰任何存储）。

    返回 (学生计划, 记录计划, 警告)。非法记录跳过并记入 warnings。
    """
    students: list[dict] = []
    records: list[dict] = []
    warnings: list[str] = []
    for name, entry in store["students"].items():
        if not isinstance(name, str) or not name.strip():
            warnings.append(f"跳过非法学生名：{name!r}")
            continue
        if not isinstance(entry, dict):
            warnings.append(f"跳过学生 {name}：数据不是对象")
            continue
        profile = entry.get("profile") if isinstance(entry.get("profile"), dict) else {}
        raw_records = entry.get("records")
        if raw_records is None:
            raw_records = []
        if not isinstance(raw_records, list):
            warnings.append(f"学生 {name} 的 records 不是数组，已按空处理")
            raw_records = []
        followups = entry.get("followups")
        if isinstance(followups, list) and followups:
            warnings.append(f"学生 {name} 的 {len(followups)} 条 followup 未迁移（新模型以学习动作替代）")

        student_records: list[dict] = []
        for record in raw_records:
            reason = _validate_record(record)
            if reason is not None:
                title = record.get("title", "") if isinstance(record, dict) else ""
                warnings.append(f"跳过学生 {name} 的记录「{title}」：{reason}")
                continue
            student_records.append(record)

        subjects = sorted(
            {str(r.get("subject")).strip() for r in student_records if str(r.get("subject") or "").strip()}
        )
        goals = profile.get("goals")
        goals = [g for g in goals if isinstance(g, str)] if isinstance(goals, list) else []
        now = clock.iso()
        students.append(
            {
                "id": student_uuid(name),
                "legacy_name": name,
                "display_name": name,
                "grade": profile.get("grade") or None,
                "active_subjects": subjects,
                "goals": goals,
                "created_at": _profile_timestamp(profile, "created_at") or now,
                "updated_at": _profile_timestamp(profile, "updated_at") or now,
                # 血缘证据的 content_hash：整个旧学生 JSON 块的摘要
                "content_hash": hashlib.sha256(
                    _canonical({"name": name, "profile": profile, "records": raw_records}).encode("utf-8")
                ).hexdigest(),
            }
        )
        for record in student_records:
            records.append(
                {
                    "id": record_uuid(name, record),
                    "student_id": student_uuid(name),
                    "record_type": record["type"],
                    "subject": str(record.get("subject") or "").strip(),
                    "title": str(record.get("title") or record.get("unit") or "").strip(),
                    "record_date": str(record.get("date") or "").strip(),
                    "payload": record,
                }
            )
    return students, records, warnings


def backup_database(db_path: Path) -> Path | None:
    """迁移前备份目标库（sqlite 备份 API，WAL 下也一致）；库不存在则不备份。"""
    if not db_path.exists():
        return None
    stamp = clock.utcnow().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.legacy-backup-{stamp}")
    suffix = 1
    while backup_path.exists():
        backup_path = db_path.with_name(f"{db_path.name}.legacy-backup-{stamp}-{suffix}")
        suffix += 1
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return backup_path


def _empty_summary(legacy_file: Path, dry_run: bool) -> dict:
    return {
        "legacy_file": str(legacy_file),
        "legacy_app": LEGACY_APP,
        "dry_run": dry_run,
        "backup_path": None,
        "students": {"created": 0, "existing": 0},
        "records": {t: {"migrated": 0, "existing": 0} for t in RECORD_TYPES},
        "sources": {"created": 0, "existing": 0},
        "warnings": [],
    }


def run(store: Store, legacy_path: Path, dry_run: bool = False) -> dict:
    """执行迁移并返回摘要。失败抛 GoodStudentError/sqlite3.Error，目标库整体回滚。"""
    legacy_file = resolve_legacy_file(legacy_path)
    legacy_store = load_legacy_store(legacy_file)
    students, records, warnings = build_plan(legacy_store)
    summary = _empty_summary(legacy_file, dry_run)
    summary["warnings"] = warnings

    existing_students = {
        s["id"] for s in (store.get_student(p["id"]) for p in students) if s is not None
    }
    with store.tx() as conn:
        existing_records = {
            row["id"]
            for row in conn.execute(
                f"SELECT id FROM legacy_records WHERE id IN ({','.join('?' * len(records))})",
                [r["id"] for r in records],
            )
        } if records else set()
    for plan in students:
        key = "existing" if plan["id"] in existing_students else "created"
        summary["students"][key] += 1
    for plan in records:
        bucket = summary["records"][plan["record_type"]]
        bucket["existing" if plan["id"] in existing_records else "migrated"] += 1

    if dry_run:
        return summary

    summary["backup_path"] = str(backup) if (backup := backup_database(store.db_path)) else None

    now = clock.iso()
    with store.tx() as conn:
        for plan in students:
            if plan["id"] in existing_students:
                continue
            conn.execute(
                "INSERT INTO students(id, display_name, grade, active_subjects, goals, created_at, updated_at)"
                " VALUES(?, ?, ?, ?, ?, ?, ?)",
                (
                    plan["id"],
                    plan["display_name"],
                    plan["grade"],
                    json.dumps(plan["active_subjects"], ensure_ascii=False),
                    json.dumps(plan["goals"], ensure_ascii=False),
                    plan["created_at"],
                    plan["updated_at"],
                ),
            )
        for plan in students:
            if store.find_source_by_hash(plan["id"], plan["content_hash"]):
                summary["sources"]["existing"] += 1
                continue
            store.insert_source(
                plan["id"],
                "legacy_import",
                LEGACY_APP,
                f"{LEGACY_APP}:{legacy_file.name}#{plan['legacy_name']}",
                plan["content_hash"],
                None,
                plan["created_at"],
                now,
            )
            summary["sources"]["created"] += 1
        for plan in records:
            if plan["id"] in existing_records:
                continue
            conn.execute(
                "INSERT INTO legacy_records(id, student_id, record_type, subject, title,"
                " record_date, payload, migrated_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    plan["id"],
                    plan["student_id"],
                    plan["record_type"],
                    plan["subject"],
                    plan["title"],
                    plan["record_date"],
                    json.dumps(plan["payload"], ensure_ascii=False),
                    now,
                ),
            )
        store.log_event(
            "legacy_migrated",
            {
                "legacy_file": str(legacy_file),
                "students_created": summary["students"]["created"],
                "records_migrated": sum(b["migrated"] for b in summary["records"].values()),
            },
        )
    return summary
