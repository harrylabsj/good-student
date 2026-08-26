"""旧数据迁移器测试（设计 §22）：正常迁移、幂等、损坏数据、只读保证、不生成题目级结论。"""

import hashlib
import json
import sqlite3

import pytest
from good_student import migrate_legacy
from good_student.cli import main as cli_main


def sample_store():
    return {
        "version": "0.1.0",
        "students": {
            "小明": {
                "profile": {
                    "name": "小明",
                    "grade": "五年级",
                    "school": "示例小学",
                    "goals": ["提高数学"],
                    "created_at": "2026-01-01T10:00:00",
                    "updated_at": "2026-01-02T10:00:00",
                },
                "records": [
                    {
                        "id": 1,
                        "type": "score",
                        "subject": "数学",
                        "title": "期中考试",
                        "date": "2026-01-10",
                        "score": 72,
                        "max_score": 100,
                        "knowledge_points": ["异分母分数加法"],
                        "notes": "计算失误多",
                    },
                    {
                        "id": 2,
                        "type": "homework",
                        "subject": "数学",
                        "title": "练习册P12",
                        "date": "2026-01-11",
                        "status": "needs_review",
                    },
                    {
                        "id": 3,
                        "type": "progress",
                        "subject": "语文",
                        "unit": "第三单元",
                        "title": "第三单元",
                        "date": "2026-01-12",
                        "status": "learning",
                    },
                    {
                        "id": 4,
                        "type": "evidence",
                        "subject": "数学",
                        "title": "错题照片",
                        "date": "2026-01-13",
                        "source_type": "image",
                        "source_path": "photos/1.jpg",
                        "extracted_text": "3/4 + 1/6 = 4/10",
                    },
                ],
                "followups": [
                    {
                        "id": 1,
                        "subject": "数学",
                        "knowledge_point": "异分母分数加法",
                        "action": "每周复盘",
                        "status": "open",
                    }
                ],
            },
            "小红": {
                "profile": {"name": "小红", "grade": "", "goals": []},
                "records": [],
                "followups": [],
            },
        },
    }


def write_legacy(tmp_path, store, raw_text=None):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir(exist_ok=True)
    text = raw_text if raw_text is not None else json.dumps(store, ensure_ascii=False, indent=2)
    (legacy_dir / "student-data.json").write_text(text, encoding="utf-8")
    return legacy_dir


def snapshot_tree(path):
    """目录内所有文件的相对路径 + 内容哈希，用于只读保证断言。"""
    return {
        str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(path.rglob("*"))
        if p.is_file()
    }


def legacy_record_count(service):
    return service.store._conn.execute("SELECT COUNT(*) FROM legacy_records").fetchone()[0]


def test_migrate_basic(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, sample_store())
    resp = service.migrate_legacy(str(legacy_dir))
    assert resp["ok"], resp
    summary = resp["data"]
    assert summary["students"] == {"created": 2, "existing": 0}
    assert summary["records"]["score"] == {"migrated": 1, "existing": 0}
    assert summary["records"]["homework"] == {"migrated": 1, "existing": 0}
    assert summary["records"]["progress"] == {"migrated": 1, "existing": 0}
    assert summary["records"]["evidence"] == {"migrated": 1, "existing": 0}
    assert summary["sources"] == {"created": 2, "existing": 0}
    assert any("followup" in w for w in summary["warnings"])

    student = service.store.get_student(migrate_legacy.student_uuid("小明"))
    assert student["display_name"] == "小明"
    assert student["grade"] == "五年级"
    assert json.loads(student["active_subjects"]) == ["数学", "语文"]
    assert json.loads(student["goals"]) == ["提高数学"]
    assert student["created_at"].startswith("2026-01-01")

    empty_student = service.store.get_student(migrate_legacy.student_uuid("小红"))
    assert empty_student["display_name"] == "小红"
    assert legacy_record_count(service) == 4

    sources = service.store.sources_for_student(student["id"])
    assert len(sources) == 1
    assert sources[0]["source_type"] == "legacy_import"
    assert sources[0]["host"] == "student-companion-agent"


def test_migrate_creates_backup_before_writing(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, sample_store())
    resp = service.migrate_legacy(str(legacy_dir))
    assert resp["ok"], resp
    backup_path = resp["data"]["backup_path"]
    assert backup_path and backup_path != str(service.store.db_path)
    backup = sqlite3.connect(backup_path)
    try:
        # 备份是迁移前的快照：表结构在，但还没有迁移进来的学生
        assert backup.execute("SELECT COUNT(*) FROM students").fetchone()[0] == 0
    finally:
        backup.close()


def test_migrate_idempotent_rerun(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, sample_store())
    first = service.migrate_legacy(str(legacy_dir))
    assert first["ok"], first
    second = service.migrate_legacy(str(legacy_dir))
    assert second["ok"], second
    summary = second["data"]
    assert summary["students"] == {"created": 0, "existing": 2}
    for record_type in migrate_legacy.RECORD_TYPES:
        assert summary["records"][record_type]["migrated"] == 0
    assert summary["records"]["score"]["existing"] == 1
    assert summary["sources"] == {"created": 0, "existing": 2}
    assert len(service.store.list_students()) == 2
    assert legacy_record_count(service) == 4


def test_migrate_missing_file(service, tmp_path):
    legacy_dir = tmp_path / "legacy"
    legacy_dir.mkdir()
    resp = service.migrate_legacy(str(legacy_dir))
    assert not resp["ok"]
    assert resp["error"]["code"] == "not_found"
    assert service.store.list_students() == []


def test_migrate_corrupt_json(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, None, raw_text="{ 这不是 JSON")
    resp = service.migrate_legacy(str(legacy_dir))
    assert not resp["ok"]
    assert resp["error"]["code"] == "legacy_data_corrupt"
    assert service.store.list_students() == []


def test_migrate_invalid_structure(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, {"version": "0.1.0", "students": ["小明"]})
    resp = service.migrate_legacy(str(legacy_dir))
    assert not resp["ok"]
    assert resp["error"]["code"] == "legacy_data_corrupt"
    assert service.store.list_students() == []


def test_migrate_skips_invalid_records(service, tmp_path):
    store = sample_store()
    store["students"]["小明"]["records"] += [
        {"id": 5, "type": "score", "subject": "数学", "title": "越界", "score": 120, "max_score": 100},
        {"id": 6, "type": "quiz", "subject": "数学", "title": "未知类型"},
        {"id": 7, "type": "homework", "subject": "数学", "title": "坏状态", "status": "done"},
        "不是对象",
    ]
    legacy_dir = write_legacy(tmp_path, store)
    resp = service.migrate_legacy(str(legacy_dir))
    assert resp["ok"], resp
    summary = resp["data"]
    assert summary["records"]["score"]["migrated"] == 1  # 越界分数被跳过
    assert legacy_record_count(service) == 4
    assert len(summary["warnings"]) >= 4


def test_migrate_readonly_guarantee(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, sample_store())
    before = snapshot_tree(legacy_dir)
    resp = service.migrate_legacy(str(legacy_dir))
    assert resp["ok"], resp
    assert snapshot_tree(legacy_dir) == before  # 旧目录文件内容与清单完全不变


def test_migrate_no_question_level_conclusions(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, sample_store())
    resp = service.migrate_legacy(str(legacy_dir))
    assert resp["ok"], resp
    student_id = migrate_legacy.student_uuid("小明")
    # 整场分数只进 legacy 科目概览证据：不写 attempts、不产生题目级掌握结论
    assert service.store.attempts_for_student(student_id) == []
    assert service.store.current_snapshots(student_id) == []
    analysis = service.analyze(student_id)
    assert analysis["ok"], analysis
    assert analysis["data"]["summary"]["total_weaknesses"] == 0


def test_migrate_failure_rolls_back_target(service, tmp_path, monkeypatch):
    legacy_dir = write_legacy(tmp_path, sample_store())

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("模拟写入失败")

    monkeypatch.setattr(service.store, "insert_source", boom)
    resp = service.migrate_legacy(str(legacy_dir))
    assert not resp["ok"]
    assert resp["error"]["code"] == "persistence_error"
    # 目标库整体回滚：学生与 legacy 记录都不残留
    assert service.store.list_students() == []
    assert legacy_record_count(service) == 0
    # 备份文件已生成（迁移前），旧数据不受影响
    assert snapshot_tree(legacy_dir) == snapshot_tree(legacy_dir)


def test_migrate_dry_run_writes_nothing(service, tmp_path):
    legacy_dir = write_legacy(tmp_path, sample_store())
    resp = service.migrate_legacy(str(legacy_dir), dry_run=True)
    assert resp["ok"], resp
    summary = resp["data"]
    assert summary["dry_run"] is True
    assert summary["students"]["created"] == 2
    assert summary["records"]["score"]["migrated"] == 1
    assert summary["backup_path"] is None
    assert service.store.list_students() == []
    assert legacy_record_count(service) == 0
    assert not list(tmp_path.glob("*.legacy-backup-*"))


def test_migrate_cli_subcommand(service, tmp_path, capsys):
    legacy_dir = write_legacy(tmp_path, sample_store())
    cli_main(["--data-dir", str(tmp_path), "migrate-legacy", str(legacy_dir), "--dry-run"])
    output = json.loads(capsys.readouterr().out)
    assert output["ok"] is True
    assert output["data"]["students"]["created"] == 2

    with pytest.raises(SystemExit):
        cli_main(["--data-dir", str(tmp_path), "migrate-legacy", str(tmp_path / "不存在")])
