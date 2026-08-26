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


MIGRATIONS: list[tuple[int, Migrator]] = [(1, _v1), (2, _v2)]

SCHEMA_VERSION_LATEST = 2


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

