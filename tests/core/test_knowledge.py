"""知识点种子包：加载、归一、前置链与降级。"""

import json

import pytest
from conftest import ingest_and_confirm, make_question, make_student
from good_student import knowledge


def test_packs_load_and_validate():
    packs = knowledge.load_packs()
    subjects = {p["subject_id"] for p in packs}
    assert {"数学", "语文", "英语"} <= subjects
    assert sum(len(p["components"]) for p in packs) >= 30


def test_alias_match_and_unknown():
    assert knowledge.match("数学", "分数解决问题")["canonical_name"] == "分数应用题"
    assert knowledge.match("数学", "分数应用题")["canonical_name"] == "分数应用题"
    assert knowledge.match("数学", "量子速读") is None
    assert knowledge.match("不存在的科目", "分数应用题") is None


def test_prerequisite_chain():
    prereqs = knowledge.prerequisites("数学", "分数应用题")
    assert [p["canonical_name"] for p in prereqs] == ["分数运算"]
    # 自定义/未知知识点没有前置链
    assert knowledge.prerequisites("数学", "异分母分数加法") == []


def test_pack_origin():
    assert knowledge.pack_origin("数学", "分数应用题") == ("k12-math", 1)
    assert knowledge.pack_origin("数学", "异分母分数加法") == (None, None)


def test_bad_prerequisite_reference_rejected(tmp_path, monkeypatch):
    bad_pack = {
        "pack_id": "bad",
        "version": 1,
        "subject_id": "数学",
        "components": [
            {"id": "a", "canonical_name": "甲", "aliases": [], "prerequisite_ids": ["ghost"]}
        ],
    }
    (tmp_path / "bad.json").write_text(json.dumps(bad_pack), encoding="utf-8")
    monkeypatch.setenv("GOOD_STUDENT_KNOWLEDGE_DIR", str(tmp_path))
    with pytest.raises(ValueError, match="前置引用不存在"):
        knowledge.load_packs(force_reload=True)
    monkeypatch.delenv("GOOD_STUDENT_KNOWLEDGE_DIR")
    knowledge.load_packs(force_reload=True)  # 恢复缓存


def test_resolve_kc_normalizes_to_pack_node(service):
    student = make_student(service)
    ingest_and_confirm(
        service, student, [make_question(kc="分数解决问题")], source_ref="s1"
    )
    row = service.store._conn.execute(
        "SELECT * FROM knowledge_components WHERE canonical_name = '分数应用题'"
    ).fetchone()
    assert row is not None
    assert row["is_custom"] == 0
    assert row["curriculum_ref"] == "pack:k12-math@1"
    assert "分数解决问题" in json.loads(row["aliases"])
    assert json.loads(row["prerequisite_ids"]) == ["分数运算"]


def test_unknown_label_stays_custom(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(kc="异分母分数加法")])
    row = service.store._conn.execute(
        "SELECT * FROM knowledge_components WHERE canonical_name = '异分母分数加法'"
    ).fetchone()
    assert row is not None
    assert row["is_custom"] == 1


def test_analysis_surfaces_prerequisite_hints(service):
    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(kc="分数应用题")], source_ref="s1")
    ingest_and_confirm(
        service,
        student,
        [make_question(locator="p2", kc="分数运算", text="计算：1/2 + 1/3 = ?")],
        source_ref="s2",
    )
    result = service.analyze(student)["data"]
    weakness = next(
        w for w in result["weaknesses"]
        if w["knowledge_component"]["canonical_name"] == "分数应用题"
    )
    hint = next(
        h for h in weakness["prerequisite_hints"]
        if h["canonical_name"] == "分数运算"
    )
    assert hint["status_hint"] == "prerequisite_also_weak"
    # 前置也薄弱时应出现在 caveats 中
    assert any("前置" in c for c in result["caveats"])
    # 未出现在数据中的前置应标 no_evidence
    chain = knowledge.prerequisites("数学", "分数运算")
    assert chain  # 分数运算自身也有前置（分数的意义）
    prereq_weak = next(
        w for w in result["weaknesses"]
        if w["knowledge_component"]["canonical_name"] == "分数运算"
    )
    no_evidence = next(
        h for h in prereq_weak["prerequisite_hints"]
        if h["canonical_name"] == "分数的意义"
    )
    assert no_evidence["status_hint"] == "prerequisite_no_evidence"


def test_analysis_result_schema_accepts_prerequisite_hints(service):
    from good_student import validation

    student = make_student(service)
    ingest_and_confirm(service, student, [make_question(kc="分数应用题")], source_ref="s1")
    result = service.analyze(student)["data"]
    errors = validation.validate(result, "analysis-result")
    assert errors == []
