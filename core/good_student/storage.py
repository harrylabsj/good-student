"""SQLite 存储：单文件、WAL、外键约束、原子事务。写操作由调用方通过 tx() 包裹。"""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from good_student import clock, migrations
from good_student.errors import GoodStudentError
from good_student.models import CandidateStatus


def _new_id() -> str:
    return str(uuid.uuid4())


def _row(cursor_row: sqlite3.Row) -> dict[str, Any]:
    return dict(cursor_row)


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "good_student.db"
        try:
            self._conn = sqlite3.connect(self.db_path, timeout=10)
        except sqlite3.Error as exc:
            raise GoodStudentError("persistence_error", f"无法打开数据库：{exc}") from exc
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=10000")
        try:
            migrations.migrate(self._conn)
        except sqlite3.DatabaseError as exc:
            raise GoodStudentError("persistence_error", f"迁移失败：{exc}") from exc

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._conn
            self._conn.commit()
        except sqlite3.Error:
            self._conn.rollback()
            raise
        except GoodStudentError:
            self._conn.rollback()
            raise

    # ---- 学生 ----

    def insert_student(self, display_name: str, grade: str | None, subjects: list[str], goals: list[str]) -> dict:
        now = clock.iso()
        student = {
            "id": _new_id(),
            "display_name": display_name,
            "grade": grade,
            "active_subjects": json.dumps(subjects, ensure_ascii=False),
            "goals": json.dumps(goals, ensure_ascii=False),
            "created_at": now,
            "updated_at": now,
        }
        self._conn.execute(
            "INSERT INTO students(id, display_name, grade, active_subjects, goals, created_at, updated_at)"
            " VALUES(:id, :display_name, :grade, :active_subjects, :goals, :created_at, :updated_at)",
            student,
        )
        return student

    def get_student(self, student_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM students WHERE id = ?", (student_id,)).fetchone()
        return _row(row) if row else None

    def list_students(self) -> list[dict]:
        return [_row(r) for r in self._conn.execute("SELECT * FROM students ORDER BY created_at")]

    # ---- 来源与候选 ----

    def insert_source(
        self,
        student_id: str,
        source_type: str,
        host: str,
        source_ref: str,
        content_hash: str,
        page_count: int | None,
        captured_at: str | None,
        processed_at: str,
    ) -> dict:
        source = {
            "id": _new_id(),
            "student_id": student_id,
            "source_type": source_type,
            "host": host,
            "source_ref": source_ref,
            "content_hash": content_hash,
            "page_count": page_count,
            "captured_at": captured_at,
            "processed_at": processed_at,
        }
        self._conn.execute(
            "INSERT INTO sources(id, student_id, source_type, host, source_ref, content_hash,"
            " page_count, captured_at, processed_at, retention_mode, created_at)"
            " VALUES(:id, :student_id, :source_type, :host, :source_ref, :content_hash,"
            " :page_count, :captured_at, :processed_at, 'reference_only', :created_at)",
            {**source, "created_at": processed_at},
        )
        return source

    def find_source_by_hash(self, student_id: str, content_hash: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE student_id = ? AND content_hash = ? ORDER BY created_at DESC LIMIT 1",
            (student_id, content_hash),
        ).fetchone()
        return _row(row) if row else None

    def get_source(self, source_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
        return _row(row) if row else None

    def sources_for_student(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM sources WHERE student_id = ? ORDER BY created_at", (student_id,)
        )
        return [_row(r) for r in rows]

    def insert_candidate(
        self, batch_id: str, student_id: str, source_id: str, payload: dict, expires_at: str
    ) -> dict:
        now = clock.iso()
        candidate_id = _new_id()
        self._conn.execute(
            "INSERT INTO candidates(id, batch_id, student_id, source_id, source_locator, payload,"
            " status, expires_at, created_at, updated_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                candidate_id,
                batch_id,
                student_id,
                source_id,
                payload.get("source_locator", ""),
                json.dumps(payload, ensure_ascii=False),
                CandidateStatus.NEEDS_CONFIRMATION.value,
                expires_at,
                now,
                now,
            ),
        )
        return {
            "id": candidate_id,
            "batch_id": batch_id,
            "student_id": student_id,
            "source_id": source_id,
            "source_locator": payload.get("source_locator", ""),
            "payload": payload,
            "status": CandidateStatus.NEEDS_CONFIRMATION.value,
            "expires_at": expires_at,
            "created_at": now,
            "updated_at": now,
        }

    def get_candidate(self, candidate_id: str) -> dict | None:
        row = self._conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
        if not row:
            return None
        candidate = _row(row)
        candidate["payload"] = json.loads(candidate["payload"])
        return candidate

    def candidates_for_source(self, source_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM candidates WHERE source_id = ? ORDER BY created_at", (source_id,)
        )
        candidates = []
        for row in rows:
            candidate = _row(row)
            candidate["payload"] = json.loads(candidate["payload"])
            candidates.append(candidate)
        return candidates

    def pending_candidates(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM candidates WHERE student_id = ? AND status = ? ORDER BY created_at",
            (student_id, CandidateStatus.NEEDS_CONFIRMATION.value),
        )
        candidates = []
        for row in rows:
            candidate = _row(row)
            candidate["payload"] = json.loads(candidate["payload"])
            candidates.append(candidate)
        return candidates

    def update_candidate_status(self, candidate_id: str, status: CandidateStatus) -> None:
        self._conn.execute(
            "UPDATE candidates SET status = ?, updated_at = ? WHERE id = ?",
            (status.value, clock.iso(), candidate_id),
        )

    def expire_candidates(self, now: str) -> int:
        cursor = self._conn.execute(
            "UPDATE candidates SET status = ?, updated_at = ?"
            " WHERE status = ? AND expires_at <= ?",
            (
                CandidateStatus.EXPIRED.value,
                now,
                CandidateStatus.NEEDS_CONFIRMATION.value,
                now,
            ),
        )
        return cursor.rowcount

    def mark_candidates_analyzed(self, student_id: str) -> None:
        self._conn.execute(
            "UPDATE candidates SET status = ?, updated_at = ? WHERE student_id = ? AND status = ?",
            (CandidateStatus.ANALYZED.value, clock.iso(), student_id, CandidateStatus.CONFIRMED.value),
        )

    # ---- 题目作答 ----

    def insert_attempt(self, attempt: dict) -> dict:
        attempt = {
            "id": _new_id(),
            "created_at": clock.iso(),
            "score_fraction": None,
            "difficulty": None,
            "time_seconds": None,
            "hint_count": None,
            **attempt,
        }
        self._conn.execute(
            "INSERT INTO attempts(id, student_id, source_id, candidate_id, subject_id, grade,"
            " question_text, student_answer, correct_answer, is_correct, score_fraction, difficulty,"
            " time_seconds, hint_count, error_reason, correction_status, attempted_at, confirmed_at,"
            " confirmed_by, created_at)"
            " VALUES(:id, :student_id, :source_id, :candidate_id, :subject_id, :grade,"
            " :question_text, :student_answer, :correct_answer, :is_correct, :score_fraction, :difficulty,"
            " :time_seconds, :hint_count, :error_reason, :correction_status, :attempted_at, :confirmed_at,"
            " :confirmed_by, :created_at)",
            attempt,
        )
        return attempt

    def attempts_for_student(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM attempts WHERE student_id = ? ORDER BY attempted_at, created_at", (student_id,)
        )
        attempts = []
        for row in rows:
            attempt = _row(row)
            attempt["is_correct"] = bool(attempt["is_correct"])
            attempts.append(attempt)
        return attempts

    # ---- 知识组件 ----

    def resolve_kc(self, subject_id: str, label: str) -> tuple[dict, bool]:
        """按 canonical_name 或 alias 匹配；未命中则创建 custom 知识组件（设计 §13.4）。"""
        row = self._conn.execute(
            "SELECT * FROM knowledge_components WHERE subject_id = ? AND canonical_name = ?",
            (subject_id, label),
        ).fetchone()
        if row:
            kc = _row(row)
            kc["is_custom"] = bool(kc["is_custom"])
            return kc, False
        for row in self._conn.execute(
            "SELECT * FROM knowledge_components WHERE subject_id = ?", (subject_id,)
        ):
            if label in json.loads(row["aliases"]):
                kc = _row(row)
                kc["is_custom"] = bool(kc["is_custom"])
                return kc, False
        kc = {
            "id": _new_id(),
            "subject_id": subject_id,
            "canonical_name": label,
            "aliases": "[]",
            "grade_range": None,
            "curriculum_ref": None,
            "prerequisite_ids": "[]",
            "is_custom": True,
        }
        self._conn.execute(
            "INSERT INTO knowledge_components(id, subject_id, canonical_name, aliases, grade_range,"
            " curriculum_ref, prerequisite_ids, is_custom, created_at)"
            " VALUES(:id, :subject_id, :canonical_name, :aliases, :grade_range,"
            " :curriculum_ref, :prerequisite_ids, 1, :created_at)",
            {**kc, "created_at": clock.iso()},
        )
        return kc, True

    def insert_attempt_kc(self, attempt_id: str, kc_id: str, source: str, weight: float = 1.0) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO attempt_kcs(attempt_id, kc_id, weight, source) VALUES(?, ?, ?, ?)",
            (attempt_id, kc_id, weight, source),
        )

    def attempt_kc_rows(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT ak.attempt_id AS attempt_id, kc.id AS kc_id, kc.canonical_name AS canonical_name,"
            " kc.subject_id AS subject_id, kc.is_custom AS is_custom"
            " FROM attempt_kcs ak"
            " JOIN knowledge_components kc ON kc.id = ak.kc_id"
            " JOIN attempts a ON a.id = ak.attempt_id"
            " WHERE a.student_id = ? ORDER BY ak.attempt_id",
            (student_id,),
        )
        result = []
        for row in rows:
            item = _row(row)
            item["is_custom"] = bool(item["is_custom"])
            result.append(item)
        return result

    # ---- 薄弱点快照 ----

    def replace_snapshots(self, student_id: str, snapshots: list[dict]) -> None:
        self._conn.execute(
            "UPDATE weakness_snapshots SET is_current = 0 WHERE student_id = ? AND is_current = 1",
            (student_id,),
        )
        for snapshot in snapshots:
            self._conn.execute(
                "INSERT INTO weakness_snapshots(id, student_id, kc_id, status, risk_level,"
                " confidence_level, evidence_count, last_error_at, last_success_at, next_review_at,"
                " reason_codes, model_version, generated_at, is_current)"
                " VALUES(:id, :student_id, :kc_id, :status, :risk_level, :confidence_level,"
                " :evidence_count, :last_error_at, :last_success_at, :next_review_at,"
                " :reason_codes, :model_version, :generated_at, 1)",
                snapshot,
            )

    def current_snapshots(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM weakness_snapshots WHERE student_id = ? AND is_current = 1", (student_id,)
        )
        snapshots = []
        for row in rows:
            snapshot = _row(row)
            snapshot["reason_codes"] = json.loads(snapshot["reason_codes"])
            snapshots.append(snapshot)
        return snapshots

    # ---- 学习动作与复测 ----

    def insert_action(self, action: dict) -> dict:
        action = {"id": _new_id(), "created_at": clock.iso(), **action}
        self._conn.execute(
            "INSERT INTO learning_actions(id, student_id, kc_id, error_reason, why, action,"
            " duration_minutes, question_count, due_date, acceptance_criteria, next_step_if_fail,"
            " reassessment_method, status, created_at)"
            " VALUES(:id, :student_id, :kc_id, :error_reason, :why, :action,"
            " :duration_minutes, :question_count, :due_date, :acceptance_criteria, :next_step_if_fail,"
            " :reassessment_method, :status, :created_at)",
            action,
        )
        return action

    def actions_for_student(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM learning_actions WHERE student_id = ? ORDER BY created_at", (student_id,)
        )
        return [_row(r) for r in rows]

    def update_action_status(self, action_id: str, status: str) -> None:
        self._conn.execute(
            "UPDATE learning_actions SET status = ? WHERE id = ?", (status, action_id)
        )

    def insert_reassessment(self, reassessment: dict) -> dict:
        reassessment = {"id": _new_id(), "created_at": clock.iso(), **reassessment}
        self._conn.execute(
            "INSERT INTO reassessments(id, student_id, kc_id, action_id, is_new_variant, no_hints,"
            " correct_count, total_count, accuracy, time_seconds, self_reported_confidence,"
            " completed_at, created_at)"
            " VALUES(:id, :student_id, :kc_id, :action_id, :is_new_variant, :no_hints,"
            " :correct_count, :total_count, :accuracy, :time_seconds, :self_reported_confidence,"
            " :completed_at, :created_at)",
            reassessment,
        )
        return reassessment

    def reassessments_for_student(self, student_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM reassessments WHERE student_id = ? ORDER BY completed_at, created_at",
            (student_id,),
        )
        result = []
        for row in rows:
            item = _row(row)
            item["is_new_variant"] = bool(item["is_new_variant"])
            item["no_hints"] = bool(item["no_hints"])
            result.append(item)
        return result

    # ---- 事件、幂等、体检 ----

    def log_event(self, event: str, payload: dict | None = None) -> None:
        self._conn.execute(
            "INSERT INTO events(ts, event, payload) VALUES(?, ?, ?)",
            (clock.iso(), event, json.dumps(payload or {}, ensure_ascii=False)),
        )

    def idempotency_get(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT response FROM idempotency WHERE key = ?", (key,)
        ).fetchone()
        return json.loads(row["response"]) if row else None

    def idempotency_put(self, key: str, response: dict) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO idempotency(key, response, created_at) VALUES(?, ?, ?)",
            (key, json.dumps(response, ensure_ascii=False), clock.iso()),
        )

    def student_row_counts(self, student_id: str) -> dict[str, int]:
        counts = {}
        for table in ("sources", "candidates", "attempts", "weakness_snapshots", "learning_actions", "reassessments"):
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM {table} WHERE student_id = ?", (student_id,)
            ).fetchone()
            counts[table] = row["n"]
        return counts

    def delete_student(self, student_id: str) -> dict[str, int]:
        counts = self.student_row_counts(student_id)
        with_foreign_keys = self._conn.execute("PRAGMA foreign_keys").fetchone()[0]
        if not with_foreign_keys:  # pragma: no cover - 防御性兜底
            raise GoodStudentError("persistence_error", "外键约束未启用，拒绝删除")
        self._conn.execute("DELETE FROM students WHERE id = ?", (student_id,))
        return counts

    def doctor_checks(self) -> list[dict]:
        checks: list[dict] = []
        integrity = self._conn.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append({"check": "sqlite_integrity", "ok": integrity == "ok", "detail": str(integrity)})
        version = migrations.current_version(self._conn)
        checks.append(
            {
                "check": "schema_version",
                "ok": version == migrations.SCHEMA_VERSION_LATEST,
                "detail": f"{version} / latest {migrations.SCHEMA_VERSION_LATEST}",
            }
        )
        orphans = self._conn.execute(
            "SELECT COUNT(*) AS n FROM attempts a LEFT JOIN students s ON s.id = a.student_id"
            " WHERE s.id IS NULL"
        ).fetchone()["n"]
        checks.append({"check": "orphan_attempts", "ok": orphans == 0, "detail": f"{orphans} orphans"})
        journal = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
        checks.append({"check": "wal_mode", "ok": journal == "wal", "detail": str(journal)})
        return checks
