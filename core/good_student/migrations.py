"""SQLite 迁移框架：每个迁移在单事务内执行，失败整体回滚，版本记录在 meta 表。"""

import sqlite3
from collections.abc import Callable

Migrator = Callable[[sqlite3.Connection], None]

_DDL_V1: list[str] = [
    """
    CREATE TABLE students(
      id TEXT PRIMARY KEY,
      display_name TEXT NOT NULL,
      grade TEXT,
      active_subjects TEXT NOT NULL DEFAULT '[]',
      goals TEXT NOT NULL DEFAULT '[]',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE sources(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      source_type TEXT NOT NULL,
      host TEXT NOT NULL,
      source_ref TEXT NOT NULL,
      content_hash TEXT NOT NULL,
      page_count INTEGER,
      captured_at TEXT,
      processed_at TEXT,
      retention_mode TEXT NOT NULL DEFAULT 'reference_only',
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_sources_hash ON sources(student_id, content_hash)",
    """
    CREATE TABLE candidates(
      id TEXT PRIMARY KEY,
      batch_id TEXT NOT NULL,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
      source_locator TEXT NOT NULL,
      payload TEXT NOT NULL,
      status TEXT NOT NULL,
      expires_at TEXT NOT NULL,
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_candidates_status ON candidates(student_id, status)",
    """
    CREATE TABLE attempts(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
      candidate_id TEXT,
      subject_id TEXT NOT NULL,
      grade TEXT,
      question_text TEXT NOT NULL,
      student_answer TEXT,
      correct_answer TEXT,
      is_correct INTEGER NOT NULL,
      score_fraction REAL,
      difficulty REAL,
      time_seconds INTEGER,
      hint_count INTEGER,
      error_reason TEXT NOT NULL DEFAULT 'unknown',
      correction_status TEXT NOT NULL DEFAULT 'uncorrected',
      attempted_at TEXT NOT NULL,
      confirmed_at TEXT NOT NULL,
      confirmed_by TEXT NOT NULL DEFAULT 'user',
      created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_attempts_student ON attempts(student_id, attempted_at)",
    """
    CREATE TABLE knowledge_components(
      id TEXT PRIMARY KEY,
      subject_id TEXT NOT NULL,
      canonical_name TEXT NOT NULL,
      aliases TEXT NOT NULL DEFAULT '[]',
      grade_range TEXT,
      curriculum_ref TEXT,
      prerequisite_ids TEXT NOT NULL DEFAULT '[]',
      is_custom INTEGER NOT NULL DEFAULT 1,
      created_at TEXT NOT NULL,
      UNIQUE(subject_id, canonical_name)
    )
    """,
    """
    CREATE TABLE attempt_kcs(
      attempt_id TEXT NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
      kc_id TEXT NOT NULL REFERENCES knowledge_components(id) ON DELETE CASCADE,
      weight REAL NOT NULL DEFAULT 1.0,
      source TEXT NOT NULL DEFAULT 'user_confirmed',
      PRIMARY KEY(attempt_id, kc_id)
    )
    """,
    """
    CREATE TABLE weakness_snapshots(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      kc_id TEXT NOT NULL REFERENCES knowledge_components(id) ON DELETE CASCADE,
      status TEXT NOT NULL,
      risk_level TEXT NOT NULL,
      confidence_level TEXT NOT NULL,
      evidence_count INTEGER NOT NULL,
      last_error_at TEXT,
      last_success_at TEXT,
      next_review_at TEXT,
      reason_codes TEXT NOT NULL DEFAULT '[]',
      model_version TEXT NOT NULL,
      generated_at TEXT NOT NULL,
      is_current INTEGER NOT NULL DEFAULT 0
    )
    """,
    "CREATE INDEX idx_snapshots_current ON weakness_snapshots(student_id, is_current)",
    """
    CREATE TABLE learning_actions(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      kc_id TEXT NOT NULL REFERENCES knowledge_components(id) ON DELETE CASCADE,
      error_reason TEXT NOT NULL,
      why TEXT NOT NULL,
      action TEXT NOT NULL,
      duration_minutes INTEGER,
      question_count INTEGER,
      due_date TEXT,
      acceptance_criteria TEXT NOT NULL,
      next_step_if_fail TEXT NOT NULL,
      reassessment_method TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'active',
      created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE reassessments(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      kc_id TEXT NOT NULL REFERENCES knowledge_components(id) ON DELETE CASCADE,
      action_id TEXT REFERENCES learning_actions(id) ON DELETE SET NULL,
      is_new_variant INTEGER NOT NULL DEFAULT 1,
      no_hints INTEGER NOT NULL DEFAULT 1,
      correct_count INTEGER NOT NULL DEFAULT 0,
      total_count INTEGER NOT NULL DEFAULT 1,
      accuracy REAL,
      time_seconds INTEGER,
      self_reported_confidence REAL,
      completed_at TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
    "CREATE TABLE events(ts TEXT NOT NULL, event TEXT NOT NULL, payload TEXT NOT NULL DEFAULT '{}')",
    """
    CREATE TABLE idempotency(
      key TEXT PRIMARY KEY,
      response TEXT NOT NULL,
      created_at TEXT NOT NULL
    )
    """,
]


def _v1(conn: sqlite3.Connection) -> None:
    for ddl in _DDL_V1:
        conn.execute(ddl)


# v2：旧数据迁移器（设计 §22）的 legacy 证据表。整场分数只进科目概览证据，
# 绝不写 attempts，因此不生成题目级掌握结论。
_DDL_V2: list[str] = [
    """
    CREATE TABLE legacy_records(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      record_type TEXT NOT NULL,
      subject TEXT NOT NULL DEFAULT '',
      title TEXT NOT NULL DEFAULT '',
      record_date TEXT NOT NULL DEFAULT '',
      payload TEXT NOT NULL,
      migrated_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX idx_legacy_records_student ON legacy_records(student_id, record_type)",
]


def _v2(conn: sqlite3.Connection) -> None:
    for ddl in _DDL_V2:
        conn.execute(ddl)


_DDL_V3: list[str] = [
    """
    CREATE TABLE score_records(
      id TEXT PRIMARY KEY,
      student_id TEXT NOT NULL REFERENCES students(id) ON DELETE CASCADE,
      subject TEXT NOT NULL,
      assessment_name TEXT NOT NULL,
      assessment_type TEXT NOT NULL DEFAULT 'exam',
      score REAL,
      max_score REAL,
      percentage REAL,
      grade_label TEXT,
      term TEXT,
      class_rank INTEGER,
      class_size INTEGER,
      assessed_at TEXT NOT NULL,
      notes TEXT,
      source_ref TEXT,
      created_at TEXT NOT NULL,
      CHECK(assessment_type IN ('exam', 'quiz', 'homework', 'practice', 'other')),
      CHECK(score IS NOT NULL OR grade_label IS NOT NULL),
      CHECK(max_score IS NULL OR max_score > 0),
      CHECK(score IS NULL OR score >= 0),
      CHECK(percentage IS NULL OR (percentage >= 0 AND percentage <= 100)),
      CHECK(class_rank IS NULL OR class_rank > 0),
      CHECK(class_size IS NULL OR class_size > 0),
      CHECK(class_rank IS NULL OR class_size IS NULL OR class_rank <= class_size)
    )
    """,
    "CREATE INDEX idx_score_records_student_date ON score_records(student_id, assessed_at)",
    "CREATE INDEX idx_score_records_student_subject ON score_records(student_id, subject)",
]


def _v3(conn: sqlite3.Connection) -> None:
    for ddl in _DDL_V3:
        conn.execute(ddl)


def _v4(conn: sqlite3.Connection) -> None:
    """将年级排名从备注提升为独立结构化字段。"""
    conn.execute("ALTER TABLE score_records ADD COLUMN grade_rank INTEGER")


def _v5(conn: sqlite3.Connection) -> None:
    """保存动作的间隔复习排期，供后续简报和客户端重启后继续使用。"""
    conn.execute("ALTER TABLE learning_actions ADD COLUMN review_schedule TEXT NOT NULL DEFAULT '[]'")


def _v6(conn: sqlite3.Connection) -> None:
    """sources(student_id, content_hash) 唯一化：并发导入同一材料时由数据库约束兜底去重。

    旧库可能已存在并发窗口产生的重复来源；先合并到最早一条（候选与作答改挂到保留行）
    再重建唯一索引，否则建索引会失败。
    """
    conn.execute("DROP INDEX IF EXISTS idx_sources_hash")
    conn.execute(
        "CREATE TEMP TABLE dup_sources AS"
        " SELECT s.id AS dup_id,"
        " (SELECT k.id FROM sources k WHERE k.student_id = s.student_id"
        "  AND k.content_hash = s.content_hash ORDER BY k.created_at, k.rowid LIMIT 1) AS keep_id"
        " FROM sources s"
    )
    conn.execute("DELETE FROM dup_sources WHERE dup_id = keep_id")
    conn.execute(
        "UPDATE candidates SET source_id = (SELECT keep_id FROM dup_sources WHERE dup_id = candidates.source_id)"
        " WHERE source_id IN (SELECT dup_id FROM dup_sources)"
    )
    conn.execute(
        "UPDATE attempts SET source_id = (SELECT keep_id FROM dup_sources WHERE dup_id = attempts.source_id)"
        " WHERE source_id IN (SELECT dup_id FROM dup_sources)"
    )
    conn.execute("DELETE FROM sources WHERE id IN (SELECT dup_id FROM dup_sources)")
    conn.execute("DROP TABLE dup_sources")
    conn.execute("CREATE UNIQUE INDEX idx_sources_hash ON sources(student_id, content_hash)")


MIGRATIONS: list[tuple[int, Migrator]] = [(1, _v1), (2, _v2), (3, _v3), (4, _v4), (5, _v5), (6, _v6)]

SCHEMA_VERSION_LATEST = 6


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    return int(row["value"]) if row else 0


def migrate(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    version = current_version(conn)
    for target, run in MIGRATIONS:
        if target <= version:
            continue
        try:
            # 显式 BEGIN：legacy 模式不会对 DDL 自动开事务，而 SQLite 的 DDL 是事务性的
            conn.execute("BEGIN")
            run(conn)
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (str(target),),
            )
            conn.commit()
        except sqlite3.Error as exc:  # 迁移失败整体回滚，不留下半成品结构
            conn.rollback()
            raise sqlite3.DatabaseError(f"migration {target} failed: {exc}") from exc
