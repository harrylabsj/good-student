"""服务层：编排候选暂存、确认、分析、建议、复测与学生数据治理。

所有公开方法返回统一 envelope（设计 §16）：{ok, data, warnings, error, trace_id}。
写操作支持 idempotency_key；未确认候选绝不进入分析与画像。
"""

import hashlib
import json
import os
import sqlite3
import traceback
import uuid
from datetime import timedelta
from functools import wraps
from pathlib import Path
from typing import Any

from good_student import __version__, analysis, clock, recommendations, validation
from good_student import migrate_legacy as legacy_migrator
from good_student.errors import GoodStudentError
from good_student.models import (
    CONFIRM_EDIT_FIELDS,
    CORRECTION_STATUSES,
    DEFAULT_CANDIDATE_TTL_HOURS,
    ERROR_REASON_LABELS,
    LOW_CONFIDENCE_THRESHOLD,
    PASS_ACCURACY_THRESHOLD,
    CandidateStatus,
)
from good_student.storage import Store

TOOLS = [
    "good_student_capabilities",
    "good_student_create_student",
    "good_student_ingest_candidates",
    "good_student_list_pending",
    "good_student_confirm_questions",
    "good_student_analyze",
    "good_student_create_plan",
    "good_student_record_reassessment",
    "good_student_export_student",
    "good_student_delete_student",
    "good_student_doctor",
]


def _ok(data: Any, warnings: list[str] | None = None) -> dict:
    return {
        "ok": True,
        "data": data,
        "warnings": warnings or [],
        "error": None,
        "trace_id": str(uuid.uuid4()),
    }


def _err(code: str, message: str, details: Any = None) -> dict:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"ok": False, "data": None, "warnings": [], "error": error, "trace_id": str(uuid.uuid4())}


def _envelope(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except GoodStudentError as exc:
            return _err(exc.code, exc.message, exc.details)
        except sqlite3.Error as exc:
            return _err("persistence_error", f"本地存储操作失败，原数据未覆盖：{exc}")
        except Exception as exc:  # noqa: BLE001 — LLM 宿主必须始终收到 envelope
            # 未知缺陷降级为 internal_error（写入事务已在 storage.tx 回滚），
            # traceback 落 stderr 供本地排查，不回传给宿主模型完整细节。
            traceback.print_exc()
            return _err("internal_error", f"内部错误：{type(exc).__name__}: {exc}")

    return wrapper


def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _content_hash(batch: dict) -> str:
    material = {
        "source_type": batch["source"]["source_type"],
        "page_count": batch["source"].get("page_count"),
        "questions": batch["questions"],
    }
    return hashlib.sha256(_canonical(material).encode("utf-8")).hexdigest()


def _require_student(store: Store, student_id: str) -> dict:
    student = store.get_student(student_id)
    if not student:
        raise GoodStudentError("not_found", f"学生不存在：{student_id}")
    return student


class Service:
    DATA_DIR_ENV = "GOOD_STUDENT_DATA"

    def __init__(self, data_dir: str | Path | None = None):
        resolved = data_dir or os.environ.get(self.DATA_DIR_ENV) or Path.home() / ".good-student"
        self.data_dir = Path(resolved)
        self.store = Store(self.data_dir)

    def close(self) -> None:
        self.store.close()

    # ---- 内部工具 ----

    def _expire_now(self) -> int:
        now = clock.iso()
        with self.store.tx():
            expired = self.store.expire_candidates(now)
        return expired

    def _idempotent_call(self, key: str | None, run) -> dict:
        if key:
            cached = self.store.idempotency_get(key)
            if cached is not None:
                replayed = json.loads(json.dumps(cached))
                replayed.setdefault("warnings", []).append("idempotent_replay")
                return replayed
        result = run()
        if key and result.get("ok"):
            self.store.idempotency_put(key, result)
        return result

    # ---- 工具实现（§16）----

    @_envelope
    def capabilities(self) -> dict:
        return _ok(
            {
                "name": "good-student",
                "version": __version__,
                "analysis_model_version": analysis.MODEL_VERSION,
                "tools": TOOLS,
                "accepted_inputs": ["image", "pdf", "text", "markdown", "csv", "json"],
                "extraction": "宿主模型负责识别，核心只接受候选 JSON（先确认后写入）",
                "storage": {"data_dir": str(self.data_dir), "backend": "sqlite-wal"},
                "schema_versions": {"candidate_batch": 1, "analysis_result": 1, "tool_response": 1},
                "candidate_ttl_hours": DEFAULT_CANDIDATE_TTL_HOURS,
            }
        )

    @_envelope
    def create_student(
        self,
        display_name: str,
        grade: str | None = None,
        active_subjects: list[str] | None = None,
        goals: list[str] | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        if not isinstance(display_name, str) or not display_name.strip() or len(display_name) > 64:
            raise GoodStudentError("invalid_argument", "display_name 必须为 1-64 个字符")

        def run() -> dict:
            def _do() -> dict:
                with self.store.tx():
                    student = self.store.insert_student(
                        display_name.strip(), grade, active_subjects or [], goals or []
                    )
                    self.store.log_event("student_created", {"student_id": student["id"]})
                    resp = _ok(
                        {
                            "student": {
                                "id": student["id"],
                                "display_name": student["display_name"],
                                "grade": student["grade"],
                                "active_subjects": active_subjects or [],
                                "goals": goals or [],
                            }
                        }
                    )
                    if idempotency_key:
                        self.store.idempotency_put(idempotency_key, resp)
                    return resp

            return self._idempotent_call(idempotency_key, _do)

        return run()

    @_envelope
    def ingest_candidates(
        self,
        student_id: str,
        batch: dict,
        host: str = "core",
        ttl_hours: int = DEFAULT_CANDIDATE_TTL_HOURS,
        captured_at: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        _require_student(self.store, student_id)
        errors, warnings = validation.validate_candidate_batch(batch)
        if errors:
            raise GoodStudentError(
                "schema_validation_failed", "候选批次未通过 Schema 校验，未写入任何数据", details=errors
            )
        if captured_at is not None:
            try:
                captured_at = clock.to_utc_iso(captured_at)
            except ValueError as exc:
                raise GoodStudentError("invalid_argument", f"captured_at 不是合法时间：{exc}") from exc
        if not isinstance(ttl_hours, int) or isinstance(ttl_hours, bool) or not 1 <= ttl_hours <= 24 * 365:
            raise GoodStudentError(
                "invalid_argument", "ttl_hours 必须为 1 到 8760 之间的整数（小时）"
            )

        content_hash = _content_hash(batch)
        now = clock.iso()

        def _do() -> dict:
            existing = self.store.find_source_by_hash(student_id, content_hash)
            if existing:
                candidates = self.store.candidates_for_source(existing["id"])
                confirmed_statuses = (CandidateStatus.CONFIRMED.value, CandidateStatus.ANALYZED.value)
                pending = [c for c in candidates if c["status"] == CandidateStatus.NEEDS_CONFIRMATION.value]
                confirmed = [c for c in candidates if c["status"] in confirmed_statuses]
                existing_texts = {c["payload"].get("question_text", "") for c in candidates}
                new_texts = [q.get("question_text", "") for q in batch["questions"]]
                resp = _ok(
                    {
                        "duplicate": True,
                        "existing": {
                            "batch_id": candidates[0]["batch_id"] if candidates else None,
                            "source_id": existing["id"],
                            "pending_count": len(pending),
                            "confirmed_count": len(confirmed),
                        },
                        "diff": {
                            "new_questions": [t for t in new_texts if t not in existing_texts],
                            "missing_questions": [t for t in existing_texts if t not in new_texts],
                        },
                    },
                    warnings=["duplicate_material: 该材料已导入过，可直接继续确认，未重复写入"],
                )
                if idempotency_key:
                    with self.store.tx():
                        self.store.idempotency_put(idempotency_key, resp)
                return resp

            with self.store.tx():
                source = self.store.insert_source(
                    student_id,
                    batch["source"]["source_type"],
                    host,
                    batch["source"]["source_ref"],
                    content_hash,
                    batch["source"].get("page_count"),
                    captured_at,
                    now,
                )
                batch_id = str(uuid.uuid4())
                expires_at = clock.iso(clock.utcnow() + timedelta(hours=ttl_hours))
                inserted = [
                    self.store.insert_candidate(batch_id, student_id, source["id"], q, expires_at)
                    for q in batch["questions"]
                ]
                self.store.log_event(
                    "candidates_ingested",
                    {"student_id": student_id, "batch_id": batch_id, "count": len(inserted)},
                )
                resp = _ok(
                    {
                        "duplicate": False,
                        "batch_id": batch_id,
                        "source_id": source["id"],
                        "candidate_count": len(inserted),
                        "expires_at": expires_at,
                        "pending": [self._pending_view(c) for c in inserted],
                    },
                    warnings=warnings or None,
                )
                if idempotency_key:
                    self.store.idempotency_put(idempotency_key, resp)
                return resp

        return self._idempotent_call(idempotency_key, _do)

    def _pending_view(self, candidate: dict) -> dict:
        payload = candidate["payload"]
        subject = (payload.get("subject") or {}).get("value")
        extraction_confidence = payload.get("extraction_confidence")
        attention_fields = list(payload.get("uncertain_fields") or [])
        if not subject:
            attention_fields.append("subject")
        reasons = payload.get("error_reason_candidates") or []
        return {
            "candidate_id": candidate["id"],
            "batch_id": candidate["batch_id"],
            "source_locator": candidate["source_locator"],
            "subject": subject,
            "grade": (payload.get("grade") or {}).get("value"),
            "question_text": payload.get("question_text"),
            "student_answer": payload.get("student_answer"),
            "correct_answer": payload.get("correct_answer"),
            "is_wrong": payload.get("is_wrong"),
            "knowledge_candidates": [k.get("label") for k in payload.get("knowledge_candidates") or []],
            "error_reason": reasons[0].get("code") if reasons else None,
            "extraction_confidence": extraction_confidence,
            "needs_confirmation": payload.get("needs_confirmation", True),
            "attention": bool(
                attention_fields
                or payload.get("needs_confirmation")
                or (extraction_confidence is not None and extraction_confidence < LOW_CONFIDENCE_THRESHOLD)
            ),
            "attention_fields": attention_fields,
        }

    @_envelope
    def list_pending(self, student_id: str) -> dict:
        _require_student(self.store, student_id)
        expired = self._expire_now()
        pending = self.store.pending_candidates(student_id)
        return _ok(
            {
                "student_id": student_id,
                "expired_now": expired,
                "pending": [self._pending_view(c) for c in pending],
            },
            warnings=["已自动过期一批未确认候选，未进入学习画像"] if expired else None,
        )

    @_envelope
    def confirm_questions(
        self, student_id: str, items: list[dict], idempotency_key: str | None = None
    ) -> dict:
        _require_student(self.store, student_id)
        self._expire_now()
        if not isinstance(items, list) or not items:
            raise GoodStudentError("invalid_argument", "items 必须为非空数组")

        applied: list[dict] = []
        failed: list[dict] = []
        plan: list[dict] = []

        def fail(index: int, code: str, message: str) -> None:
            failed.append({"index": index, "code": code, "message": message})

        for index, item in enumerate(items):
            if not isinstance(item, dict) or "candidate_id" not in item:
                fail(index, "invalid_argument", f"items[{index}] 缺少 candidate_id")
                continue
            action = item.get("action", "confirm")
            if action not in ("confirm", "reject"):
                fail(index, "invalid_argument", f"items[{index}].action 必须为 confirm 或 reject")
                continue
            candidate = self.store.get_candidate(item["candidate_id"])
            if not candidate or candidate["student_id"] != student_id:
                fail(index, "not_found", f"候选不存在：{item['candidate_id']}")
                continue
            if candidate["status"] == CandidateStatus.EXPIRED.value:
                fail(index, "candidate_expired", f"候选已过期（24 小时未确认）：{item['candidate_id']}")
                continue
            if candidate["status"] != CandidateStatus.NEEDS_CONFIRMATION.value:
                fail(index, "invalid_state", f"候选当前状态为 {candidate['status']}，不能确认")
                continue

            edits = item.get("edits") or {}
            if not isinstance(edits, dict) or set(edits) - CONFIRM_EDIT_FIELDS:
                unknown = sorted(set(edits) - CONFIRM_EDIT_FIELDS) if isinstance(edits, dict) else []
                fail(index, "invalid_argument", f"不允许的编辑字段：{unknown or 'edits'}")
                continue

            # 编辑字段类型校验（M3）：宿主模型可能按候选 payload 的 {value,confidence} 结构
            # 传 subject/grade 等，直接写 SQLite 会得到误导性的 persistence_error。
            bad_type = [
                f
                for f in ("subject", "grade", "question_text", "student_answer", "correct_answer")
                if f in edits and edits[f] is not None and not isinstance(edits[f], str)
            ]
            if bad_type:
                fail(index, "invalid_argument", f"edits.{bad_type[0]} 必须为字符串或 null")
                continue

            if action == "reject":
                plan.append({"kind": "reject", "candidate": candidate})
                continue

            payload = candidate["payload"]
            subject = edits.get("subject") or (payload.get("subject") or {}).get("value")
            if not subject:
                fail(index, "subject_missing", "缺少科目且未在 edits.subject 补充，无法确认")
                continue
            error_reason = edits.get("error_reason") or next(
                (r.get("code") for r in payload.get("error_reason_candidates") or []), "unknown"
            )
            if error_reason not in ERROR_REASON_LABELS:
                fail(index, "invalid_argument", f"未知错因代码：{error_reason}")
                continue
            correction_status = edits.get("correction_status", "uncorrected")
            if correction_status not in CORRECTION_STATUSES:
                fail(index, "invalid_argument", f"correction_status 必须为 {sorted(CORRECTION_STATUSES)}")
                continue
            source = self.store.get_source(candidate["source_id"])
            attempted_at = edits.get("attempted_at") or (source or {}).get("captured_at") or clock.iso()
            try:
                attempted_at = clock.to_utc_iso(attempted_at)
            except (ValueError, TypeError) as exc:
                fail(index, "invalid_argument", f"attempted_at 不是合法时间：{exc}")
                continue
            if edits.get("knowledge_labels") is not None:
                labels = edits["knowledge_labels"]
                if not isinstance(labels, list) or not all(isinstance(x, str) and x.strip() for x in labels):
                    fail(index, "invalid_argument", "knowledge_labels 必须为非空字符串数组")
                    continue
            else:
                labels = [k["label"] for k in payload.get("knowledge_candidates") or []]

            is_wrong = edits.get("is_wrong")
            if is_wrong is None:
                is_wrong = payload.get("is_wrong", True)
            if not isinstance(is_wrong, bool):
                fail(index, "invalid_argument", "is_wrong 必须为布尔值")
                continue

            plan.append(
                {
                    "kind": "confirm",
                    "candidate": candidate,
                    "attempt": {
                        "student_id": student_id,
                        "source_id": candidate["source_id"],
                        "candidate_id": candidate["id"],
                        "subject_id": subject,
                        "grade": edits.get("grade") or (payload.get("grade") or {}).get("value"),
                        "question_text": edits.get("question_text") or payload.get("question_text", ""),
                        "student_answer": edits.get("student_answer", payload.get("student_answer")),
                        "correct_answer": edits.get("correct_answer", payload.get("correct_answer")),
                        "is_correct": int(not is_wrong),
                        "error_reason": error_reason,
                        "correction_status": correction_status,
                        "attempted_at": attempted_at,
                        "confirmed_at": clock.iso(),
                        "confirmed_by": "user",
                    },
                    "labels": labels,
                    "kc_source": "user_confirmed" if edits.get("knowledge_labels") is not None else "model_suggested",
                }
            )

        def _do() -> dict:
            with self.store.tx():
                for step in plan:
                    candidate = step["candidate"]
                    if step["kind"] == "reject":
                        self.store.update_candidate_status(candidate["id"], CandidateStatus.REJECTED)
                        applied.append({"candidate_id": candidate["id"], "action": "rejected"})
                        continue
                    attempt = self.store.insert_attempt(step["attempt"])
                    for label in step["labels"]:
                        kc, _created = self.store.resolve_kc(attempt["subject_id"], label)
                        self.store.insert_attempt_kc(attempt["id"], kc["id"], step["kc_source"])
                    self.store.update_candidate_status(candidate["id"], CandidateStatus.CONFIRMED)
                    entry = {
                        "candidate_id": candidate["id"],
                        "action": "confirmed",
                        "attempt_id": attempt["id"],
                    }
                    if not step["labels"]:
                        entry["warnings"] = ["未关联知识点，请补充 edits.knowledge_labels 后才能进入分析"]
                    applied.append(entry)
                self.store.log_event(
                    "questions_confirmed",
                    {"student_id": student_id, "applied": len(applied), "failed": len(failed)},
                )
                resp = _ok({"applied": applied, "failed": failed})
                if idempotency_key:
                    self.store.idempotency_put(idempotency_key, resp)
                return resp

        return self._idempotent_call(idempotency_key, _do)

    @_envelope
    def analyze(self, student_id: str, persist_snapshot: bool = True, now: str | None = None) -> dict:
        _require_student(self.store, student_id)
        self._expire_now()
        result, warnings = analysis.analyze_student(self.store, student_id, now)
        with self.store.tx():
            self.store.mark_candidates_analyzed(student_id)
            if persist_snapshot:
                self.store.replace_snapshots(student_id, analysis.snapshot_rows(student_id, result))
            self.store.log_event(
                "analysis_generated",
                {"student_id": student_id, "weaknesses": result["summary"]["total_weaknesses"]},
            )
        return _ok(result, warnings=warnings or None)

    @_envelope
    def create_plan(self, student_id: str, idempotency_key: str | None = None, now: str | None = None) -> dict:
        _require_student(self.store, student_id)
        result, _ = analysis.analyze_student(self.store, student_id, now)
        reason_by_kc = self._dominant_reasons(student_id)

        def _do() -> dict:
            actions, skipped = recommendations.build_actions(result, reason_by_kc)
            with self.store.tx():
                rows = [
                    self.store.insert_action(
                        {
                            "student_id": student_id,
                            "kc_id": action.pop("kc_id"),
                            "error_reason": action["error_reason"],
                            "why": action["why"],
                            "action": action["action"],
                            "duration_minutes": action["duration_minutes"],
                            "question_count": action["question_count"],
                            "due_date": action["due_date"],
                            "acceptance_criteria": action["acceptance_criteria"],
                            "next_step_if_fail": action["next_step_if_fail"],
                            "reassessment_method": action["reassessment_method"],
                            "status": "active",
                        }
                    )
                    for action in actions
                ]
                self.store.log_event("plan_created", {"student_id": student_id, "actions": len(rows)})
                resp = _ok(
                    {
                        "actions": [
                            {**a, "kc_id": r["kc_id"], "id": r["id"], "status": r["status"]}
                            for a, r in zip(actions, rows, strict=False)
                        ],
                        "skipped": skipped,
                    }
                )
                if idempotency_key:
                    self.store.idempotency_put(idempotency_key, resp)
                return resp

        return self._idempotent_call(idempotency_key, _do)

    def _dominant_reasons(self, student_id: str) -> dict[str, str]:
        counts: dict[str, dict[str, int]] = {}
        links = {link["attempt_id"]: link for link in self.store.attempt_kc_rows(student_id)}
        for attempt in self.store.attempts_for_student(student_id):
            if attempt["is_correct"] or attempt["error_reason"] == "unknown":
                continue
            link = links.get(attempt["id"])
            if not link:
                continue
            reasons = counts.setdefault(link["kc_id"], {})
            reasons[attempt["error_reason"]] = reasons.get(attempt["error_reason"], 0) + 1
        return {
            kc_id: max(reasons.items(), key=lambda kv: kv[1])[0] for kc_id, reasons in counts.items()
        }

    @_envelope
    def record_reassessment(
        self,
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
        now: str | None = None,
    ) -> dict:
        _require_student(self.store, student_id)
        if not isinstance(correct_count, int) or not isinstance(total_count, int) or total_count < 1:
            raise GoodStudentError("invalid_argument", "correct_count/total_count 必须为整数且 total_count ≥ 1")
        if not 0 <= correct_count <= total_count:
            raise GoodStudentError("invalid_argument", "correct_count 必须在 0 与 total_count 之间")
        if self_reported_confidence is not None and (
            not isinstance(self_reported_confidence, (int, float))
            or isinstance(self_reported_confidence, bool)  # bool 是 int 子类，须排除
            or not 0 <= self_reported_confidence <= 1
        ):
            raise GoodStudentError("invalid_argument", "self_reported_confidence 必须为 0-1 之间的数字")
        if not any(link["kc_id"] == kc_id for link in self.store.attempt_kc_rows(student_id)):
            raise GoodStudentError("not_found", f"知识点 {kc_id} 未出现在该学生的已确认作答中")
        try:
            completed = clock.to_utc_iso(completed_at) if completed_at else (now or clock.iso())
        except ValueError as exc:
            raise GoodStudentError("invalid_argument", f"completed_at 不是合法时间：{exc}") from exc

        def _do() -> dict:
            with self.store.tx():
                reassessment = self.store.insert_reassessment(
                    {
                        "student_id": student_id,
                        "kc_id": kc_id,
                        "action_id": action_id,
                        "is_new_variant": int(is_new_variant),
                        "no_hints": int(no_hints),
                        "correct_count": correct_count,
                        "total_count": total_count,
                        "accuracy": round(correct_count / total_count, 3),
                        "time_seconds": time_seconds,
                        "self_reported_confidence": self_reported_confidence,
                        "completed_at": completed,
                    }
                )
                self.store.log_event(
                    "reassessment_recorded", {"student_id": student_id, "kc_id": kc_id}
                )
            result, _ = analysis.analyze_student(self.store, student_id, now)
            with self.store.tx():
                self.store.replace_snapshots(student_id, analysis.snapshot_rows(student_id, result))
            weakness = next(
                (
                    w
                    for w in result["weaknesses"]
                    if w["knowledge_component"]["id"] == kc_id
                ),
                None,
            )
            resp = _ok(
                {
                    "reassessment": {
                        "id": reassessment["id"],
                        "kc_id": kc_id,
                        "correct_count": correct_count,
                        "total_count": total_count,
                        "accuracy": reassessment["accuracy"],
                        "is_new_variant": is_new_variant,
                        "no_hints": no_hints,
                        "completed_at": completed,
                    },
                    "passed": correct_count / total_count >= PASS_ACCURACY_THRESHOLD,
                    "updated_weakness": weakness,
                }
            )
            if idempotency_key:
                with self.store.tx():
                    self.store.idempotency_put(idempotency_key, resp)
            return resp

        return self._idempotent_call(idempotency_key, _do)

    @_envelope
    def export_student(self, student_id: str) -> dict:
        student = _require_student(self.store, student_id)
        attempts = self.store.attempts_for_student(student_id)
        links = self.store.attempt_kc_rows(student_id)
        links_by_attempt: dict[str, list[dict]] = {}
        for link in links:
            links_by_attempt.setdefault(link["attempt_id"], []).append(
                {"kc_id": link["kc_id"], "canonical_name": link["canonical_name"]}
            )
        kc_ids = {link["kc_id"] for link in links}
        with self.store.tx():
            self.store.log_event("student_exported", {"student_id": student_id})
        return _ok(
            {
                "student_id": student_id,
                "exported_at": clock.iso(),
                "format_version": 1,
                "student": {
                    "id": student["id"],
                    "display_name": student["display_name"],
                    "grade": student["grade"],
                    "active_subjects": json.loads(student["active_subjects"]),
                    "goals": json.loads(student["goals"]),
                    "created_at": student["created_at"],
                },
                "sources": [
                    {
                        k: s[k]
                        for k in (
                            "id",
                            "source_type",
                            "host",
                            "source_ref",
                            "content_hash",
                            "captured_at",
                        )
                    }
                    for s in self.store.sources_for_student(student_id)
                ],
                "attempts": [
                    {
                        k: a[k]
                        for k in (
                            "id",
                            "subject_id",
                            "grade",
                            "question_text",
                            "student_answer",
                            "correct_answer",
                            "is_correct",
                            "error_reason",
                            "correction_status",
                            "attempted_at",
                            "confirmed_at",
                        )
                    }
                    | {"knowledge_components": links_by_attempt.get(a["id"], [])}
                    for a in attempts
                ],
                "knowledge_component_ids": sorted(kc_ids),
                "snapshots": self.store.current_snapshots(student_id),
                "learning_actions": self.store.actions_for_student(student_id),
                "reassessments": self.store.reassessments_for_student(student_id),
            }
        )

    @_envelope
    def delete_student(
        self, student_id: str, confirm_phrase: str | None = None, idempotency_key: str | None = None
    ) -> dict:
        student = _require_student(self.store, student_id)

        if confirm_phrase is None:
            counts = self.store.student_row_counts(student_id)
            return _ok(
                {
                    "confirmation_required": True,
                    "scope": {"display_name": student["display_name"], "will_delete": counts},
                    "how_to_confirm": (
                        "再次调用本工具并传入 confirm_phrase=学生显示名（"
                        f"{student['display_name']}）才会执行删除"
                    ),
                },
                warnings=["confirmation_required: 尚未删除任何数据"],
            )
        if confirm_phrase != student["display_name"]:
            raise GoodStudentError(
                "confirm_phrase_mismatch",
                "confirm_phrase 与学生显示名不一致，已拒绝删除（数据未变动）",
            )

        def _do() -> dict:
            with self.store.tx():
                self.store.log_event("student_deleted", {"student_id": student_id})
                removed = self.store.delete_student(student_id)
            resp = _ok({"deleted": True, "student_id": student_id, "removed": removed})
            if idempotency_key:
                with self.store.tx():
                    self.store.idempotency_put(idempotency_key, resp)
            return resp

        return self._idempotent_call(idempotency_key, _do)

    @_envelope
    def migrate_legacy(self, legacy_path: str | Path, dry_run: bool = False) -> dict:
        """只读迁移旧 student-companion-agent 数据（设计 §22），返回迁移摘要。"""
        summary = legacy_migrator.run(self.store, Path(legacy_path), dry_run=dry_run)
        return _ok(summary, warnings=summary["warnings"] or None)

    @_envelope
    def doctor(self) -> dict:
        checks = self.store.doctor_checks()
        for name in validation.SCHEMA_FILES:
            try:
                validation.load_schema(name)
                checks.append({"check": f"schema:{name}", "ok": True, "detail": "loaded"})
            except (OSError, json.JSONDecodeError) as exc:
                checks.append({"check": f"schema:{name}", "ok": False, "detail": str(exc)})
        return _ok({"ok": all(c["ok"] for c in checks), "checks": checks})
