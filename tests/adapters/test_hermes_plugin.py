"""Hermes 插件合约测试：用 FakeHermesBridge（内存实现）替代 Hermes 运行时。

覆盖（设计 §21.3 + §19）：能力发现、成功识别→ingest、无视觉能力降级、
超时/拒绝/非法 JSON、Schema 不匹配重试一次后失败、envelope 一致性、数据目录隔离。

插件包（packages/hermes/）不在 setuptools 包路径内，这里用 importlib 按路径加载，
包名用独立命名空间 good_student_hermes，避免污染主包。
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
HERMES_DIR = REPO_ROOT / "packages" / "hermes"
ENVELOPE_KEYS = {"ok", "data", "warnings", "error", "trace_id"}

_hermes_pkg = None


def hermes_pkg():
    """按路径加载插件包（进程内只加载一次）。"""
    global _hermes_pkg
    if _hermes_pkg is None:
        spec = importlib.util.spec_from_file_location(
            "good_student_hermes",
            HERMES_DIR / "__init__.py",
            submodule_search_locations=[str(HERMES_DIR)],
        )
        module = importlib.util.module_from_spec(spec)
        sys.modules["good_student_hermes"] = module
        spec.loader.exec_module(module)
        _hermes_pkg = module
    return _hermes_pkg


def _import(modname: str):
    hermes_pkg()
    return importlib.import_module(f"good_student_hermes.{modname}")


@pytest.fixture
def plugin(tmp_path):
    bridge_mod = _import("bridge")
    testing = _import("testing")
    plugin_mod = _import("plugin")
    bridge = testing.FakeHermesBridge()
    instance = plugin_mod.GoodStudentPlugin(bridge, data_dir=tmp_path / "data")
    yield instance, bridge, bridge_mod
    instance.close()


def make_student(plugin):
    resp = plugin.create_student(display_name="小明", grade="五年级")
    assert resp["ok"], resp
    return resp["data"]["student"]["id"]


def make_batch(source_ref="att-1"):
    return {
        "schema_version": 1,
        "source": {"source_type": "image", "source_ref": source_ref, "page_count": 1},
        "questions": [
            {
                "source_locator": "page-1-question-1",
                "subject": {"value": "数学", "confidence": 0.95},
                "question_text": "计算：3/4 + 1/6 = ?",
                "student_answer": "4/10",
                "correct_answer": "11/12",
                "is_wrong": True,
                "knowledge_candidates": [{"label": "异分母分数加法", "confidence": 0.85}],
                "error_reason_candidates": [{"code": "concept_gap", "confidence": 0.7}],
                "extraction_confidence": 0.9,
                "needs_confirmation": True,
                "uncertain_fields": [],
            }
        ],
        "warnings": [],
    }


def image_attachment():
    return [{"kind": "bytes", "ref": "photo.png", "mime_type": "image/png", "data": b"fake"}]


def assert_envelope(resp):
    assert set(resp.keys()) == ENVELOPE_KEYS
    assert isinstance(resp["ok"], bool)
    assert isinstance(resp["warnings"], list)
    if resp["ok"]:
        assert resp["error"] is None
    else:
        assert resp["data"] is None
        assert set(resp["error"]) >= {"code", "message"}


# ---- 能力发现 ----


def test_capabilities(plugin):
    instance, _, _ = plugin
    resp = instance.capabilities()
    assert_envelope(resp)
    assert resp["ok"]
    data = resp["data"]
    assert data["plugin"]["extraction_mode"] == "A"
    assert data["host"]["vision"] is True
    assert "good_student_extract_attachments" in data["tools"]
    assert "good_student_analyze" in data["tools"]


def test_doctor_healthy(plugin):
    instance, _, _ = plugin
    resp = instance.doctor()
    assert_envelope(resp)
    assert resp["ok"]
    assert resp["data"]["ok"]
    names = {c["check"] for c in resp["data"]["checks"]}
    assert {"host:bridge", "asset:extraction_prompt", "asset:candidate_schema"} <= names


# ---- 成功识别 → ingest ----


def test_extract_success_ingests_candidates(plugin):
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bridge.queue_parsed(make_batch())

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert_envelope(resp)
    assert resp["ok"], resp
    assert resp["data"]["duplicate"] is False
    assert resp["data"]["candidate_count"] == 1
    assert len(bridge.calls) == 1

    pending = instance.list_pending(student_id)
    assert pending["ok"] and len(pending["data"]["pending"]) == 1

    # 识别调用携带提取指令与候选 Schema
    call = bridge.calls[0]
    assert "错题" in call["instructions"]
    assert call["json_schema"]["title"] == "CandidateWrongQuestionBatch"
    assert call["input_blocks"][0]["type"] == "image"


def test_extract_repairs_once_then_succeeds(plugin):
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bridge.queue_error(ValueError("schema validation failed"))
    bridge.queue_parsed(make_batch())

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert resp["ok"], resp
    assert len(bridge.calls) == 2
    assert "上次输出未通过校验" in bridge.calls[1]["instructions"]


# ---- 无视觉能力降级 ----


def test_no_vision_degrades_without_llm_call(plugin):
    instance, bridge, _ = plugin
    bridge.vision = False
    student_id = make_student(instance)

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert_envelope(resp)
    assert not resp["ok"]
    assert resp["error"]["code"] == "host_capability_missing"
    assert len(bridge.calls) == 0  # 不调用模型
    pending = instance.list_pending(student_id)
    assert pending["data"]["pending"] == []  # 不写入数据


def test_no_vision_text_attachment_still_works(plugin):
    instance, bridge, _ = plugin
    bridge.vision = False
    student_id = make_student(instance)
    batch = make_batch()
    batch["source"]["source_type"] = "text"
    bridge.queue_parsed(batch)

    resp = instance.extract_and_ingest(
        student_id, [{"kind": "bytes", "ref": "note.txt", "mime_type": "text/plain", "data": b"3/4+1/6"}]
    )
    assert resp["ok"], resp


# ---- 宿主模型失败：超时 / 拒绝 ----


@pytest.mark.parametrize("kind", ["timeout", "refused", "rate_limited"])
def test_host_llm_failure_retries_once_then_fails(plugin, kind):
    instance, bridge, bridge_mod = plugin
    student_id = make_student(instance)
    bridge.queue_error(bridge_mod.HostLlmError(kind, f"宿主模型 {kind}"))
    bridge.queue_error(bridge_mod.HostLlmError(kind, f"宿主模型 {kind} again"))

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert_envelope(resp)
    assert not resp["ok"]
    assert resp["error"]["code"] == "host_llm_error"
    assert resp["error"]["details"]["kind"] == kind
    assert resp["error"]["details"]["attempts"] == 2
    assert len(bridge.calls) == 2
    pending = instance.list_pending(student_id)
    assert pending["data"]["pending"] == []


def test_host_llm_recovers_on_retry(plugin):
    instance, bridge, bridge_mod = plugin
    student_id = make_student(instance)
    bridge.queue_error(bridge_mod.HostLlmError("timeout", "超时"))
    bridge.queue_parsed(make_batch())

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert resp["ok"], resp
    assert len(bridge.calls) == 2


# ---- 非法 JSON ----


def test_invalid_json_retries_once_then_fails(plugin):
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bridge.queue_invalid_json("这不是 JSON")
    bridge.queue_invalid_json("依然不是 JSON")

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert_envelope(resp)
    assert not resp["ok"]
    assert resp["error"]["code"] == "structured_extraction_failed"
    assert resp["error"]["details"]["kind"] == "invalid_json"
    assert len(bridge.calls) == 2
    pending = instance.list_pending(student_id)
    assert pending["data"]["pending"] == []


# ---- Schema 不匹配：重试一次后失败 ----


def test_schema_mismatch_retries_once_then_fails(plugin):
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bridge.queue_error(ValueError("schema validation failed: missing questions"))
    bridge.queue_error(ValueError("schema validation failed: missing questions"))

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert_envelope(resp)
    assert not resp["ok"]
    assert resp["error"]["code"] == "structured_extraction_failed"
    assert resp["error"]["details"]["kind"] == "schema_mismatch"
    assert resp["error"]["details"]["attempts"] == 2
    assert len(bridge.calls) == 2  # 最多一次修复重试
    pending = instance.list_pending(student_id)
    assert pending["data"]["pending"] == []


def test_model_batch_failing_core_validation_not_written(plugin):
    """宿主侧放行的批次仍由 core 严格校验；失败不写入。"""
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bad = make_batch()
    bad["questions"][0]["extraction_confidence"] = 1.5  # 超出 0-1
    bridge.queue_parsed(bad)

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert not resp["ok"]
    assert resp["error"]["code"] == "schema_validation_failed"
    pending = instance.list_pending(student_id)
    assert pending["data"]["pending"] == []


# ---- envelope 一致性 ----


def test_envelope_consistency_across_paths(plugin):
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bridge.queue_invalid_json()
    bridge.queue_invalid_json()

    responses = [
        instance.capabilities(),
        instance.doctor(),
        instance.list_pending(student_id),
        instance.create_student(display_name="小红"),
        instance.extract_and_ingest(student_id, []),  # no_attachment
        instance.extract_and_ingest(student_id, image_attachment()),  # invalid_json
        instance.delete_student(student_id),  # confirmation_required 分支
    ]
    for resp in responses:
        assert_envelope(resp)


def test_no_attachment(plugin):
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    resp = instance.extract_and_ingest(student_id, [])
    assert_envelope(resp)
    assert not resp["ok"]
    assert resp["error"]["code"] == "no_attachment"
    assert len(bridge.calls) == 0


# ---- 数据目录 ----


def test_data_dir_isolation(tmp_path):
    plugin_mod = _import("plugin")
    testing = _import("testing")
    bridge_a = testing.FakeHermesBridge()
    bridge_b = testing.FakeHermesBridge()
    dir_a = tmp_path / "a"
    dir_b = tmp_path / "b"
    pa = plugin_mod.GoodStudentPlugin(bridge_a, data_dir=dir_a)
    pb = plugin_mod.GoodStudentPlugin(bridge_b, data_dir=dir_b)
    try:
        student = make_student(pa)
        assert pa.service.data_dir == dir_a
        assert pb.service.data_dir == dir_b
        # B 看不到 A 的学生
        resp = pb.list_pending(student)
        assert not resp["ok"] and resp["error"]["code"] == "not_found"
    finally:
        pa.close()
        pb.close()


def test_bridge_data_dir_used_when_no_explicit_dir(tmp_path):
    plugin_mod = _import("plugin")
    testing = _import("testing")
    bridge = testing.FakeHermesBridge(data_dir=tmp_path / "host-provided")
    instance = plugin_mod.GoodStudentPlugin(bridge)
    try:
        assert instance.service.data_dir == tmp_path / "host-provided"
    finally:
        instance.close()


# ---- register(ctx) 装配 ----


def test_register_wires_tools_and_skill(tmp_path, monkeypatch):
    """用假 ctx 走一遍 register()：12 个工具全部注册，Skill 可发现。"""
    import json

    pkg = hermes_pkg()

    class FakeLlm:
        def complete_structured(self, **kwargs):  # pragma: no cover - 本用例不触发
            raise AssertionError("不应被调用")

    class FakeCtx:
        def __init__(self):
            self.llm = FakeLlm()
            self.tools = {}
            self.skills = {}

        def register_tool(self, name, toolset, schema, handler):
            self.tools[name] = {"toolset": toolset, "schema": schema, "handler": handler}

        def register_skill(self, name, path):
            self.skills[name] = Path(path)

        def get_config(self, key, default=None):
            return default

    monkeypatch.setenv("GOOD_STUDENT_DATA", str(tmp_path / "env-data"))
    ctx = FakeCtx()
    pkg.register(ctx)

    from good_student.service import TOOLS

    assert set(ctx.tools) == {*TOOLS, "good_student_extract_attachments"}
    assert all(t["toolset"] == "good-student" for t in ctx.tools.values())
    assert "good-student" in ctx.skills
    assert ctx.skills["good-student"].name == "SKILL.md"

    # 处理器契约：接收 args dict，返回 envelope JSON 字符串，不抛异常
    raw = ctx.tools["good_student_capabilities"]["handler"]({})
    resp = json.loads(raw)
    assert_envelope(resp)
    assert resp["ok"] and resp["data"]["host"]["name"] == "hermes"
    raw_err = ctx.tools["good_student_list_pending"]["handler"]({})
    assert_envelope(json.loads(raw_err))


# ---- H5：真实 HermesBridge 的附件路径守卫（大小上限 / 常规文件 / attachment_roots 包含）----


class _Ctx:
    def __init__(self, config=None):
        self._config = config or {}

    def get_config(self, key, default=None):
        return self._config.get(key, default)


def _real_bridge(config=None):
    bridge_mod = _import("bridge")
    return bridge_mod.HermesBridge(_Ctx(config))


def test_real_bridge_path_block_reads_small_text(tmp_path):
    bridge_mod = _import("bridge")
    f = tmp_path / "page.txt"
    f.write_text("合成占位材料", encoding="utf-8")
    block = bridge_mod.HermesBridge._path_block(f)
    assert block["type"] == "text"
    assert "合成占位材料" in block["text"]


def test_real_bridge_path_block_rejects_directory(tmp_path):
    bridge_mod = _import("bridge")
    with pytest.raises(bridge_mod.HostCapabilityError, match="常规文件"):
        bridge_mod.HermesBridge._path_block(tmp_path)


def test_real_bridge_path_block_rejects_oversized_file(tmp_path, monkeypatch):
    bridge_mod = _import("bridge")
    monkeypatch.setattr(bridge_mod, "MAX_ATTACHMENT_BYTES", 10)
    f = tmp_path / "big.txt"
    f.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(bridge_mod.HostCapabilityError, match="大小上限"):
        bridge_mod.HermesBridge._path_block(f)


def test_real_bridge_path_block_image_passes_bytes(tmp_path):
    bridge_mod = _import("bridge")
    img = tmp_path / "page.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    block = bridge_mod.HermesBridge._path_block(img)
    assert block["type"] == "image"
    assert block["data"].startswith(b"\x89PNG")


def test_real_bridge_attachment_roots_containment(tmp_path):
    """配置 attachment_roots 后，注入模型只能读根目录内的附件（H5）。"""
    bridge_mod = _import("bridge")
    allowed = tmp_path / "uploads"
    allowed.mkdir()
    (allowed / "ok.txt").write_text("ok", encoding="utf-8")
    secret = tmp_path / "secret.txt"
    secret.write_text("top secret", encoding="utf-8")

    b = _real_bridge({"attachment_roots": [str(allowed)]})
    blocks = b.resolve_attachments(
        [bridge_mod.AttachmentRef(kind="path", ref=str(allowed / "ok.txt"))]
    )
    assert blocks[0]["type"] == "text"
    with pytest.raises(bridge_mod.HostCapabilityError, match="attachment_roots"):
        b.resolve_attachments([bridge_mod.AttachmentRef(kind="path", ref=str(secret))])


def test_real_bridge_attachment_roots_unconfigured_allows(tmp_path):
    """未配置 attachment_roots 时保持默认（本地用户显式提供的附件），仍受大小上限约束。"""
    bridge_mod = _import("bridge")
    f = tmp_path / "page.txt"
    f.write_text("材料", encoding="utf-8")
    b = _real_bridge({})
    blocks = b.resolve_attachments([bridge_mod.AttachmentRef(kind="path", ref=str(f))])
    assert blocks[0]["type"] == "text"


# ---- M5：修复提示只附加给输出校验类失败，且 {reason} 占位符被替换 ----


def test_repair_hint_not_attached_on_host_llm_error(plugin):
    """M5：超时/限流/拒绝是宿主侧问题，模型没产出，第二轮不得带"请按 Schema 重出"提示。"""
    instance, bridge, bridge_mod = plugin
    student_id = make_student(instance)
    bridge.queue_error(bridge_mod.HostLlmError("timeout", "超时"))
    bridge.queue_parsed(make_batch())

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert resp["ok"], resp
    assert len(bridge.calls) == 2
    assert "上次输出未通过校验" not in bridge.calls[1]["instructions"]
    assert "{reason}" not in bridge.calls[1]["instructions"]


def test_repair_hint_attached_with_substituted_reason_on_invalid_json(plugin):
    """M5：非法 JSON 是输出校验类失败，第二轮带修复提示，且 {reason} 被替换为真实原因。"""
    instance, bridge, _ = plugin
    student_id = make_student(instance)
    bridge.queue_invalid_json("第一次不是 JSON")
    bridge.queue_parsed(make_batch())

    resp = instance.extract_and_ingest(student_id, image_attachment())
    assert resp["ok"], resp
    second = bridge.calls[1]["instructions"]
    assert "上次输出未通过校验（第一次不是 JSON）" in second
    assert "{reason}" not in second
