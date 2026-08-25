import sqlite3

import pytest
from conftest import make_student
from good_student import migrations
from good_student.errors import GoodStudentError
from good_student.storage import Store


def test_init_creates_schema_and_wal(tmp_path):
    store = Store(tmp_path)
    try:
        tables = {
            r["name"]
            for r in store._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {
            "students",
            "sources",
            "candidates",
            "attempts",
            "knowledge_components",
            "attempt_kcs",
            "weakness_snapshots",
            "learning_actions",
            "reassessments",
        } <= tables
        assert migrations.current_version(store._conn) == migrations.SCHEMA_VERSION_LATEST
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        store.close()


def test_reopen_is_idempotent(tmp_path):
    Store(tmp_path).close()
    store = Store(tmp_path)  # 不应重复迁移或报错
    try:
        assert migrations.current_version(store._conn) == migrations.SCHEMA_VERSION_LATEST
    finally:
        store.close()


def test_failed_migration_rolls_back(tmp_path, monkeypatch):
    def bad_v2(conn):
        conn.execute("CREATE TABLE should_rollback(x TEXT)")
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(migrations, "MIGRATIONS", [(1, migrations.MIGRATIONS[0][1]), (2, bad_v2)])
    with pytest.raises(GoodStudentError) as excinfo:
        Store(tmp_path)
    assert excinfo.value.code == "persistence_error"

    raw = sqlite3.connect(tmp_path / "good_student.db")
    try:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "should_rollback" not in tables  # 失败迁移整体回滚
        version = raw.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        assert version[0] == "1"
    finally:
        raw.close()


def test_tx_rollback_keeps_data_atomic(tmp_path):
    store = Store(tmp_path)
    try:
        with pytest.raises(GoodStudentError):
            with store.tx():
                store.insert_student("A", None, [], [])
                store.insert_student("B", None, [], [])
                raise GoodStudentError("invalid_argument", "模拟失败")
        assert store.list_students() == []
    finally:
        store.close()


def test_two_services_share_dir(tmp_path):
    # 每宿主独立目录是默认策略，但同目录双连接（WAL + busy_timeout）不应损坏数据
    from good_student.service import Service

    s1 = Service(tmp_path)
    s2 = Service(tmp_path)
    try:
        make_student(s1, "小明")
        make_student(s2, "小红")
        assert len(s1.store.list_students()) == 2
    finally:
        s1.close()
        s2.close()
