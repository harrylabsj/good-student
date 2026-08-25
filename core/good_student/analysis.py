"""分析引擎 v1：可解释规则（设计 §14），只输出错误集中度与疑似薄弱点，不输出掌握率。"""

import json
import uuid
from collections import defaultdict

from good_student import clock
from good_student.models import PASS_ACCURACY_THRESHOLD, REVIEW_INTERVAL_DAYS, WeaknessStatus
from good_student.storage import Store

MODEL_VERSION = "rules-v1"

_ACTIONABLE = {
    WeaknessStatus.SUSPECTED.value,
    WeaknessStatus.EVIDENCED.value,
    WeaknessStatus.REVIEW_DUE.value,
}


def _passed(reassessment: dict) -> bool:
    total = max(reassessment["total_count"], 1)
    return reassessment["correct_count"] / total >= PASS_ACCURACY_THRESHOLD


def _is_independent(reassessment: dict) -> bool:
    return reassessment["is_new_variant"] and reassessment["no_hints"]


def analyze_student(store: Store, student_id: str, now: str | None = None) -> tuple[dict, list[str]]:
    now = now or clock.iso()
    student = store.get_student(student_id)
    attempts = store.attempts_for_student(student_id)
    kc_links = store.attempt_kc_rows(student_id)
    reassessments = store.reassessments_for_student(student_id)
    warnings: list[str] = []

    links_by_attempt: dict[str, list[dict]] = defaultdict(list)
    for link in kc_links:
        links_by_attempt[link["attempt_id"]].append(link)

    future = [a for a in attempts if a["attempted_at"] > now]
    if future:
        warnings.append(
            f"忽略 {len(future)} 条 attempted_at 晚于当前时间的作答（未来日期不进入当前窗口）"
        )
    used = [a for a in attempts if a["attempted_at"] <= now]

    kc_by_id: dict[str, dict] = {link["kc_id"]: link for link in kc_links}
    wrongs_by_kc: dict[str, list[dict]] = defaultdict(list)
    successes_by_kc: dict[str, list[dict]] = defaultdict(list)
    for attempt in used:
        for link in links_by_attempt.get(attempt["id"], []):
            bucket = wrongs_by_kc if not attempt["is_correct"] else successes_by_kc
            bucket[link["kc_id"]].append(attempt)

    rass_by_kc: dict[str, list[dict]] = defaultdict(list)
    for reassessment in reassessments:
        if reassessment["completed_at"] <= now:
            rass_by_kc[reassessment["kc_id"]].append(reassessment)

    source_refs = {}

    def _source_ref(source_id: str | None) -> str | None:
        if not source_id:
            return None
        if source_id not in source_refs:
            source = store.get_source(source_id)
            source_refs[source_id] = source["source_ref"] if source else None
        return source_refs[source_id]

    weaknesses = []
    for kc_id, wrongs in wrongs_by_kc.items():
        link = kc_by_id[kc_id]
        rass = rass_by_kc.get(kc_id, [])
        independent = [r for r in rass if _is_independent(r)]
        evidence_count = len(wrongs) + len(rass)

        status = WeaknessStatus.SUSPECTED.value
        risk = "low"
        confidence = "low"
        reasons = ["single_error" if len(wrongs) == 1 else "multiple_errors"]
        next_review_at: str | None = None

        distinct_sources = {w["source_id"] for w in wrongs}
        if len(distinct_sources) >= 2:
            risk = "medium"
            confidence = "medium"
            reasons.append("repeated_across_sources")

        corrected_dates = [w["attempted_at"] for w in wrongs if w["correction_status"] == "corrected"]
        if corrected_dates and any(w["attempted_at"] > min(corrected_dates) for w in wrongs):
            risk = "high"
            confidence = "medium"
            reasons.append("recurrence_after_correction")

        if independent:
            last = independent[-1]
            if not _passed(last):
                status = WeaknessStatus.EVIDENCED.value
                risk = "high"
                confidence = "high" if evidence_count >= 2 else "medium"
                reasons.append("failed_independent_reassessment")
            else:
                status = WeaknessStatus.IMPROVING.value
                reasons.append("passed_independent_reassessment")
                streak = 0
                for reassessment in reversed(independent):
                    if _passed(reassessment):
                        streak += 1
                    else:
                        break
                if streak >= 2:
                    status = WeaknessStatus.MASTERED.value
                    next_review_at = clock.add_days(last["completed_at"], REVIEW_INTERVAL_DAYS)
                    reasons.append("consecutive_independent_passes")
                    if now > next_review_at:
                        status = WeaknessStatus.REVIEW_DUE.value
                        reasons.append("review_due_by_decay")

        if evidence_count < 2:
            confidence = "low"

        evidence = [
            {
                "attempt_id": a["id"],
                "attempted_at": a["attempted_at"],
                "source_ref": _source_ref(a["source_id"]),
                "confirmed": True,
                "is_correct": a["is_correct"],
                "question_excerpt": a["question_text"][:120],
            }
            for a in sorted(wrongs + successes_by_kc.get(kc_id, []), key=lambda a: a["attempted_at"])
        ]
        last_error_at = max((w["attempted_at"] for w in wrongs), default=None)
        success_dates = [s["attempted_at"] for s in successes_by_kc.get(kc_id, [])] + [
            r["completed_at"] for r in independent if _passed(r)
        ]
        last_success_at = max(success_dates, default=None)

        weaknesses.append(
            {
                "knowledge_component": {
                    "id": link["kc_id"],
                    "canonical_name": link["canonical_name"],
                    "subject_id": link["subject_id"],
                    "is_custom": link["is_custom"],
                },
                "status": status,
                "risk_level": risk,
                "confidence_level": confidence,
                "evidence_count": evidence_count,
                "evidence": evidence,
                "reassessments": [
                    {
                        "id": r["id"],
                        "completed_at": r["completed_at"],
                        "passed": _passed(r),
                        "is_new_variant": r["is_new_variant"],
                        "no_hints": r["no_hints"],
                    }
                    for r in rass
                ],
                "reason_codes": reasons,
                "last_error_at": last_error_at,
                "last_success_at": last_success_at,
                "next_review_at": next_review_at,
            }
        )

    risk_order = {"high": 0, "medium": 1, "low": 2}
    weaknesses.sort(key=lambda w: (risk_order[w["risk_level"]], -w["evidence_count"]))

    subject_totals: dict[str, int] = defaultdict(int)
    subject_kc: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for attempt in used:
        if attempt["is_correct"]:
            continue
        for link in links_by_attempt.get(attempt["id"], []):
            subject_totals[attempt["subject_id"]] += 1
            subject_kc[attempt["subject_id"]][link["kc_id"]] += 1

    subjects = []
    for subject_id in sorted(subject_totals):
        total = subject_totals[subject_id]
        kcs = [
            {
                "id": kc_id,
                "canonical_name": kc_by_id[kc_id]["canonical_name"],
                "wrong_count": count,
                "concentration": round(count / total, 3) if total else 0.0,
            }
            for kc_id, count in sorted(
                subject_kc[subject_id].items(), key=lambda kv: -kv[1]
            )
        ]
        subjects.append(
            {
                "subject_id": subject_id,
                "wrong_attempt_count": total,
                "knowledge_components": kcs,
                "note": "仅有错题证据，以下为错误集中度（占本科目错题比例），不是掌握率",
            }
        )

    by_status: dict[str, int] = defaultdict(int)
    for weakness in weaknesses:
        by_status[weakness["status"]] += 1

    caveats = [
        "错题本缺少完整作答分母，本报告不包含精确掌握率，仅报告错误集中度与疑似薄弱点",
        "所有判断均可回溯到已确认的题目级证据（attempt_id、日期、来源）",
    ]
    if any(w["confidence_level"] == "low" for w in weaknesses):
        caveats.append("存在证据少于两条的低置信判断，请结合复测验证")

    result = {
        "schema_version": 1,
        "student": {
            "id": student_id,
            "display_name": student["display_name"] if student else None,
            "grade": student["grade"] if student else None,
        },
        "generated_at": now,
        "model_version": MODEL_VERSION,
        "weaknesses": weaknesses,
        "subjects": subjects,
        "summary": {
            "total_weaknesses": len(weaknesses),
            "by_status": dict(by_status),
            "low_confidence_count": sum(1 for w in weaknesses if w["confidence_level"] == "low"),
        },
        "caveats": caveats,
    }
    return result, warnings


def snapshot_rows(student_id: str, result: dict) -> list[dict]:
    now = result["generated_at"]
    snapshots = []
    for weakness in result["weaknesses"]:
        snapshots.append(
            {
                "id": str(uuid.uuid4()),
                "student_id": student_id,
                "kc_id": weakness["knowledge_component"]["id"],
                "status": weakness["status"],
                "risk_level": weakness["risk_level"],
                "confidence_level": weakness["confidence_level"],
                "evidence_count": weakness["evidence_count"],
                "last_error_at": weakness["last_error_at"],
                "last_success_at": weakness["last_success_at"],
                "next_review_at": weakness["next_review_at"],
                "reason_codes": json.dumps(weakness["reason_codes"], ensure_ascii=False),
                "model_version": result["model_version"],
                "generated_at": now,
            }
        )
    return snapshots


def actionable_weaknesses(result: dict) -> list[dict]:
    return [w for w in result["weaknesses"] if w["status"] in _ACTIONABLE]
