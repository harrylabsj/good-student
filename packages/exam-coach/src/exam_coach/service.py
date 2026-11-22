"""Persist confirmed learning evidence and grade account-owned practice sets."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import unicodedata
import uuid
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from .catalog import TOPICS, Answer, Difficulty, Identifier, Question, Subject, Text, answer_key

DRAFT_TTL = 24 * 60 * 60
MIN_RETEST = 3
RETEST_GAP = 48 * 60 * 60
UncertainField = Literal[
    "question", "student_answer", "reasoning", "subject", "topic_id", "reason", "original_question_id",
]
Reason = Literal["概念缺失", "方法不熟", "步骤错误", "审题失误", "计算失误", "证据不足"]


class CoachError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class Candidate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    subject: Subject
    question: Answer
    student_answer: Answer | None = None
    reasoning: Answer | None = None
    locator: Text | None = None
    topic_id: Identifier | None = None
    reason: Reason = "证据不足"
    confidence: float = Field(ge=0, le=1, strict=True)
    uncertain_fields: list[UncertainField] = Field(default_factory=list, max_length=10)
    original_question_id: Identifier | None = None


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    draft_id: Identifier
    corrected: Candidate | None = None


class Response(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: Identifier
    answer: Answer
    used_hints: StrictBool
    previously_seen: StrictBool


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: Identifier
    points: dict[Identifier, Annotated[int, Field(ge=0, strict=True)]] = Field(min_length=1, max_length=15)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS students (
    account TEXT PRIMARY KEY,
    nickname TEXT NOT NULL,
    grade INTEGER NOT NULL CHECK (grade BETWEEN 10 AND 12),
    region TEXT NOT NULL,
    created INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL REFERENCES students(account) ON DELETE CASCADE,
    payload TEXT NOT NULL,
    expires INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS draft_account_expiry ON drafts(account, expires);
CREATE TABLE IF NOT EXISTS observations (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL REFERENCES students(account) ON DELETE CASCADE,
    fingerprint TEXT NOT NULL,
    subject TEXT NOT NULL,
    topic TEXT,
    reason TEXT NOT NULL,
    original_question_id TEXT,
    payload TEXT NOT NULL,
    created INTEGER NOT NULL,
    UNIQUE (account, fingerprint)
);
CREATE INDEX IF NOT EXISTS observation_topic ON observations(account, subject, topic, created);
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    account TEXT NOT NULL REFERENCES students(account) ON DELETE CASCADE,
    subject TEXT NOT NULL,
    topic TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (mode IN ('practice', 'retest')),
    created INTEGER NOT NULL,
    submitted INTEGER,
    completed INTEGER,
    passed INTEGER CHECK (passed IN (0, 1)),
    independent INTEGER CHECK (independent IN (0, 1))
);
CREATE INDEX IF NOT EXISTS session_topic ON sessions(account, subject, topic, created);
CREATE TABLE IF NOT EXISTS practice_items (
    session TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    snapshot TEXT NOT NULL,
    response TEXT,
    independent INTEGER CHECK (independent IN (0, 1)),
    score INTEGER,
    max_score INTEGER NOT NULL,
    PRIMARY KEY (session, question)
);
"""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _fingerprint(item: Candidate) -> str:
    normalized = "".join(unicodedata.normalize("NFKC", item.question).split()).casefold()
    return hashlib.sha256(_json([item.subject, normalized]).encode()).hexdigest()


class Coach:
    def __init__(self, path: Path, bank: dict[str, Question], now: Callable[[], float] = time.time):
        self.path, self.bank, self.now = path, bank, now
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        with closing(sqlite3.connect(path, timeout=10)) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(_SCHEMA)

    @contextmanager
    def connection(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA secure_delete=ON")
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @property
    def timestamp(self) -> int:
        return int(self.now())

    @staticmethod
    def student(db: sqlite3.Connection, account: str) -> dict:
        row = db.execute("SELECT nickname,grade,region FROM students WHERE account=?", (account,)).fetchone()
        if not row:
            raise CoachError("profile_required", "请先建一个学生档案")
        return dict(row)

    def profile(self, account: str) -> dict:
        with self.connection() as db:
            return self.student(db, account)

    def create_student(self, account: str, nickname: Text, grade: int, region: Text, permission: bool) -> dict:
        if permission is not True:
            raise CoachError("consent_required", "需确认已获学生或监护人授权")
        with self.connection(write=True) as db:
            db.execute("INSERT OR IGNORE INTO students VALUES (?,?,?,?,?)",
                       (account, nickname, grade, region, self.timestamp))
            saved = self.student(db, account)
            if saved != {"nickname": nickname, "grade": grade, "region": region}:
                raise CoachError("student_exists", "该账号已有一个学生档案，已保留原档案")
            return saved

    def _check_candidate(self, candidate: Candidate) -> None:
        if candidate.topic_id and candidate.topic_id not in TOPICS[candidate.subject]:
            raise CoachError("invalid_topic", "知识点未登记或不属于该学科")
        if candidate.original_question_id:
            question = self.bank.get(candidate.original_question_id)
            if not question or question.subject != candidate.subject:
                raise CoachError("invalid_question", "关联真题未登记或不属于该学科")

    def stage(self, account: str, candidates: list[Candidate], permission: bool) -> dict:
        if permission is not True:
            raise CoachError("consent_required", "请先获得本次材料处理授权")
        for candidate in candidates:
            self._check_candidate(candidate)
        now = self.timestamp
        with self.connection(write=True) as db:
            self.student(db, account)
            db.execute("DELETE FROM drafts WHERE account=? AND expires<=?", (account, now))
            results = []
            for candidate in candidates:
                draft_id = uuid.uuid4().hex
                data = candidate.model_dump(mode="json")
                db.execute("INSERT INTO drafts VALUES (?,?,?,?)",
                           (draft_id, account, _json(data), now + DRAFT_TTL))
                results.append({"draft_id": draft_id, **data,
                                "needs_attention": bool(candidate.confidence < 0.8
                                                        or candidate.uncertain_fields or not candidate.topic_id)})
            return {"drafts": results, "expires_at": now + DRAFT_TTL}

    def confirm(self, account: str, confirmations: list[Confirmation], confirmed: bool) -> dict:
        if confirmed is not True:
            raise CoachError("confirmation_required", "请让用户确认要归档的错题和知识点")
        if len({row.draft_id for row in confirmations}) != len(confirmations):
            raise CoachError("duplicate_draft", "一条候选在同一批次只能确认一次")
        now = self.timestamp
        with self.connection(write=True) as db:
            self.student(db, account)
            results = []
            for item in confirmations:
                row = db.execute("SELECT payload FROM drafts WHERE id=? AND account=? AND expires>?",
                                 (item.draft_id, account, now)).fetchone()
                if not row:
                    raise CoachError("draft_not_found", "候选不存在或已过期；本次未归档")
                candidate = item.corrected or Candidate.model_validate_json(row["payload"])
                self._check_candidate(candidate)
                # Unconfirmed fields remain visibly uncertain. Never count a
                # topic marked uncertain as evidence for that topic.
                disputed = {"question", "topic_id", "subject", "reason"} & set(candidate.uncertain_fields)
                topic = candidate.topic_id if not disputed else None
                fingerprint = _fingerprint(candidate)
                existing = db.execute("SELECT id,topic FROM observations WHERE account=? AND fingerprint=?",
                                      (account, fingerprint)).fetchone()
                if existing:
                    result = {"observation_id": existing["id"], "duplicate": True, "topic_id": existing["topic"]}
                else:
                    observation_id = uuid.uuid4().hex
                    db.execute("INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?)", (
                        observation_id, account, fingerprint, candidate.subject, topic, candidate.reason,
                        candidate.original_question_id, candidate.model_dump_json(), now,
                    ))
                    result = {"observation_id": observation_id, "duplicate": False, "topic_id": topic}
                results.append({"draft_id": item.draft_id, **result})
                db.execute("DELETE FROM drafts WHERE id=? AND account=?", (item.draft_id, account))
            return {"confirmed": results}

    @staticmethod
    def _observation_view(row: sqlite3.Row) -> dict:
        return {**json.loads(row["payload"]), "observation_id": row["id"],
                "recorded_topic_id": row["topic"], "at": row["created"]}

    def observation(self, account: str, observation_id: str) -> dict:
        with self.connection() as db:
            row = db.execute("SELECT * FROM observations WHERE account=? AND id=?",
                             (account, observation_id)).fetchone()
            if not row:
                raise CoachError("observation_not_found", "本账号没有这道已归档的错题")
            return self._observation_view(row)

    def list_observations(self, account: str, subject: Subject, topic: str | None, limit: int) -> dict:
        if topic and topic not in TOPICS[subject]:
            raise CoachError("invalid_topic", "知识点未登记或不属于该学科")
        with self.connection() as db:
            self.student(db, account)
            if topic:
                rows = db.execute("SELECT * FROM observations WHERE account=? AND subject=? AND topic=? "
                                  "ORDER BY created DESC,id LIMIT ?", (account, subject, topic, limit))
            else:
                rows = db.execute("SELECT * FROM observations WHERE account=? AND subject=? "
                                  "ORDER BY created DESC,id LIMIT ?", (account, subject, limit))
            return {"subject": subject, "observations": [self._observation_view(row) for row in rows]}

    @staticmethod
    def owned_session(db: sqlite3.Connection, account: str, session_id: str) -> sqlite3.Row:
        row = db.execute("SELECT * FROM sessions WHERE id=? AND account=?", (session_id, account)).fetchone()
        if not row:
            raise CoachError("session_not_found", "本账号没有这组练习")
        return row

    @staticmethod
    def session_items(db: sqlite3.Connection, session: sqlite3.Row) -> list[sqlite3.Row]:
        return db.execute("SELECT * FROM practice_items WHERE session=? ORDER BY ordinal",
                          (session["id"],)).fetchall()

    def start(
        self, account: str, subject: Subject, topic: str, mode: Literal["practice", "retest"],
        count: int, difficulty: Difficulty | None, strict_region: bool,
    ) -> dict:
        if topic not in TOPICS[subject]:
            raise CoachError("invalid_topic", "知识点未登记或不属于该学科")
        if mode == "retest" and count < MIN_RETEST:
            raise CoachError("too_few_questions", "复测至少需要 3 道未见真题")
        with self.connection(write=True) as db:
            profile = self.student(db, account)
            used = {r[0] for r in db.execute(
                "SELECT i.question FROM practice_items i JOIN sessions s ON s.id=i.session WHERE s.account=?",
                (account,),
            )}
            used.update(r[0] for r in db.execute(
                "SELECT original_question_id FROM observations WHERE account=? AND original_question_id IS NOT NULL",
                (account,),
            ))
            matches = [q for q in self.bank.values() if (
                q.subject == subject and topic in q.topics and q.id not in used
                and q.min_grade <= profile["grade"] and (difficulty is None or q.difficulty == difficulty)
                and (not strict_region or q.source.region == profile["region"])
            )]
            matches.sort(key=lambda q: (q.source.region != profile["region"], -q.source.year, q.id))
            if len(matches) < count:
                return {"status": "insufficient_questions", "requested": count, "available": len(matches),
                        "topic": TOPICS[subject][topic], "session_id": None, "questions": []}
            selected = matches[:count]
            session_id = uuid.uuid4().hex
            db.execute("INSERT INTO sessions(id,account,subject,topic,mode,created) VALUES(?,?,?,?,?,?)",
                       (session_id, account, subject, topic, mode, self.timestamp))
            for index, question in enumerate(selected):
                db.execute(
                    "INSERT INTO practice_items(session,question,ordinal,snapshot,max_score) VALUES(?,?,?,?,?)",
                    (session_id, question.id, index, question.model_dump_json(),
                     sum(c.points for c in question.rubric) or 1),
                )
            return {"status": "ready", "session_id": session_id, "mode": mode,
                    "questions": [q.public() for q in selected]}

    def practice(self, account: str, session_id: str) -> dict:
        with self.connection() as db:
            row = self.owned_session(db, account, session_id)
            return self._view(db, row)

    def _view(self, db: sqlite3.Connection, session: sqlite3.Row) -> dict:
        questions = []
        for row in self.session_items(db, session):
            question = Question.model_validate_json(row["snapshot"])
            item = question.public()
            if session["submitted"] is not None:
                item.update({"score": row["score"], "max_score": row["max_score"], "response": row["response"],
                             "independent": bool(row["independent"])})
                if question.kind == "rubric":
                    item["rubric"] = [c.model_dump() for c in question.rubric]
                else:
                    item["answer_key"] = question.answers
                item["source"] = question.source.public(graded=True)
            questions.append(item)
        status = "open" if session["submitted"] is None else (
            "review_required" if session["completed"] is None else "graded")
        return {"session_id": session["id"], "subject": session["subject"], "topic_id": session["topic"],
                "mode": session["mode"], "status": status, "passed": (
                    None if session["passed"] is None else bool(session["passed"])
                ), "qualifies_as_retest": session["mode"] == "retest" and session["independent"] == 1,
                "questions": questions}

    def submit(self, account: str, session_id: str, responses: list[Response], confirmed: bool) -> dict:
        if confirmed is not True:
            raise CoachError("confirmation_required", "请确认交卷后再显示答案")
        answers = {response.question_id: response for response in responses}
        with self.connection(write=True) as db:
            session = self.owned_session(db, account, session_id)
            if session["submitted"] is not None:
                return self._view(db, session)
            items = self.session_items(db, session)
            if len(answers) != len(responses) or set(answers) != {row["question"] for row in items}:
                raise CoachError("incomplete_submission", "请提交本组每题的答案及独立作答情况")
            for row in items:
                question = Question.model_validate_json(row["snapshot"])
                response = answers[question.id]
                score = None if question.kind == "rubric" else int(
                    answer_key(response.answer, question.kind) in {
                        answer_key(key, question.kind) for key in question.answers
                    }
                )
                db.execute("UPDATE practice_items SET response=?,independent=?,score=? WHERE session=? AND question=?",
                           (response.answer, int(not response.used_hints and not response.previously_seen), score,
                            session_id, question.id))
            db.execute("UPDATE sessions SET submitted=? WHERE id=?", (self.timestamp, session_id))
            self._finalize(db, session_id)
            return self._view(db, self.owned_session(db, account, session_id))

    def review(self, account: str, session_id: str, reviews: list[Review], confirmed: bool) -> dict:
        if confirmed is not True:
            raise CoachError("confirmation_required", "请逐项核对评分依据并让用户确认")
        if len({review.question_id for review in reviews}) != len(reviews):
            raise CoachError("invalid_review", "同一道题在一批复核中只能出现一次")
        with self.connection(write=True) as db:
            session = self.owned_session(db, account, session_id)
            if session["submitted"] is None or session["completed"] is not None:
                raise CoachError("invalid_review", "这组练习没有待确认的主观题")
            items = {row["question"]: row for row in self.session_items(db, session)}
            for review in reviews:
                row = items.get(review.question_id)
                if not row or row["score"] is not None:
                    raise CoachError("invalid_review", "只可复核本组尚未判分的主观题")
                question = Question.model_validate_json(row["snapshot"])
                limits = {c.id: c.points for c in question.rubric}
                if set(review.points) != set(limits) or any(
                    not 0 <= score <= limits[key] for key, score in review.points.items()
                ):
                    raise CoachError("invalid_review", "评分要点必须完整，且各项得分不可超出范围")
                db.execute("UPDATE practice_items SET score=? WHERE session=? AND question=?",
                           (sum(review.points.values()), session_id, question.id))
            self._finalize(db, session_id)
            return self._view(db, self.owned_session(db, account, session_id))

    def _finalize(self, db: sqlite3.Connection, session_id: str) -> None:
        totals = db.execute(
            "SELECT COUNT(*) AS n, COUNT(score) AS graded, "
            "SUM(CASE WHEN score=max_score THEN 1 ELSE 0 END) AS correct, MIN(independent) AS independent "
            "FROM practice_items WHERE session=?", (session_id,),
        ).fetchone()
        if totals["n"] == totals["graded"]:
            passed = totals["correct"] * 3 >= totals["n"] * 2
            db.execute("UPDATE sessions SET completed=?,passed=?,independent=? WHERE id=?",
                       (self.timestamp, int(passed), totals["independent"], session_id))

    def weaknesses(self, account: str, subject: Subject) -> dict:
        with self.connection() as db:
            self.student(db, account)
            records = db.execute("SELECT * FROM observations WHERE account=? AND subject=? ORDER BY created,id",
                                 (account, subject)).fetchall()
            results = []
            for topic, label in TOPICS[subject].items():
                evidence = [item for item in records if item["topic"] == topic
                            and item["reason"] in {"概念缺失", "方法不熟", "步骤错误"}]
                if not evidence:
                    continue
                latest_wrong = evidence[-1]["created"]
                rounds = db.execute(
                    "SELECT id,submitted,passed FROM sessions WHERE account=? AND subject=? AND topic=? "
                    "AND mode='retest' AND independent=1 AND completed IS NOT NULL AND submitted>? "
                    "ORDER BY submitted DESC, completed DESC, id",
                    (account, subject, topic, latest_wrong),
                ).fetchall()
                status = "疑似薄弱" if len(evidence) == 1 else "重复出现"
                next_retest = None
                if rounds:
                    if not rounds[0]["passed"]:
                        status = "仍需练习"
                    else:
                        status = "首次复测通过"
                        for earlier in rounds[1:]:
                            if not earlier["passed"]:
                                break
                            if rounds[0]["submitted"] - earlier["submitted"] >= RETEST_GAP:
                                status = "已通过两次复测"
                                break
                        if status == "首次复测通过":
                            streak = []
                            for row in rounds:
                                if not row["passed"]:
                                    break
                                streak.append(row)
                            next_retest = streak[-1]["submitted"] + RETEST_GAP
                results.append({"topic_id": topic, "topic": label, "status": status,
                                "evidence_count": len(evidence),
                                "next_retest_at": next_retest,
                                "evidence": [{"observation_id": row["id"], "reason": row["reason"],
                                              "question": json.loads(row["payload"])["question"], "at": row["created"]}
                                             for row in evidence[-5:]],
                                "recent_retests": [dict(row) for row in rounds[:2]]})
            return {"subject": subject, "topics": results,
                    "without_topic": sum(r["topic"] is None for r in records)}

    def history(self, account: str) -> dict:
        with self.connection() as db:
            self.student(db, account)
            rows = db.execute("SELECT id,subject,topic,mode,created,submitted,completed,passed FROM sessions "
                              "WHERE account=? ORDER BY created DESC,id LIMIT 30", (account,)).fetchall()
            return {"sessions": [dict(row) for row in rows]}

    def export(self, account: str) -> dict:
        with self.connection() as db:
            profile = self.student(db, account)
            observations = db.execute("SELECT * FROM observations WHERE account=? ORDER BY created",
                                      (account,)).fetchall()
            sessions = db.execute("SELECT * FROM sessions WHERE account=? ORDER BY created", (account,)).fetchall()
            return {"profile": profile, "observations": [self._observation_view(row) for row in observations],
                    "sessions": [self._view(db, row) for row in sessions]}

    def delete(self, account: str, nickname: Text | None, confirmed: bool) -> dict:
        with self.connection(write=True) as db:
            profile = self.student(db, account)
            if confirmed is not True:
                return {"requires_confirmation": True, "nickname": profile["nickname"]}
            if nickname != profile["nickname"]:
                raise CoachError("confirm_phrase_mismatch", "需输入档案昵称才能删除")
            db.execute("DELETE FROM students WHERE account=?", (account,))
            return {"deleted": True}
