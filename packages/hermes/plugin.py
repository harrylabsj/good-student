"""Good-student Hermes 插件核心编排：识别（模式 A）→ 候选校验 → ingest。

业务逻辑全部复用 `good_student.service`（校验、存储、分析、建议都在 core），
本模块只做宿主侧编排：能力探测、附件解析、宿主模型调用、§19 降级与重试。

宿主模型失败策略（§19 错误注册表）：
- 超时/限流/拒绝：产品级重试最多一次；仍失败则不写入任何数据，返回 host_llm_error。
- 非法 JSON / Schema 不匹配：携带修复提示重试一次；仍失败返回
  structured_extraction_failed，不写入数据，不展示伪结果。
- 宿主无视觉能力：不调用模型，返回 host_capability_missing 并给出降级选项。
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from good_student import __version__ as core_version
from good_student.service import TOOLS, Service
from good_student.validation import load_schema

from .bridge import AttachmentRef, HermesHostBridge, HostLlmError

HOST_NAME = "hermes"
EXTRACT_TOOL = "good_student_extract_attachments"
LLM_TIMEOUT_SECONDS = 120

_PLUGIN_DIR = Path(__file__).resolve().parent
_PROMPT_RELATIVE = Path("prompts") / "wrong-book-extraction.md"

_REPAIR_HINT = (
    "\n\n上次输出未通过校验（{reason}）。请严格按 JSON Schema 重新输出完整候选批次："
    "只输出一个 JSON 对象，不要输出任何解释文字；不确定的字段用 null 并写入 "
    "uncertain_fields。"
)


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


def _find_repo_root() -> Path | None:
    """定位仓库根（含 prompts/ 与 schemas/）。

    开发态：core 以 editable 方式安装，good_student 包上两级即仓库根。
    发布态：发布脚本把 prompts/ 复制进插件目录（见 _load_extraction_prompt）。
    """
    try:
        import good_student

        candidate = Path(good_student.__file__).resolve().parents[2]
        if (candidate / _PROMPT_RELATIVE).exists():
            return candidate
    except Exception:  # noqa: BLE001 — 定位失败按无仓库根处理
        return None
    return None


def _load_extraction_prompt() -> str | None:
    """加载提取指令。优先插件内置副本（发布脚本同步），否则回退仓库根。"""
    for base in (_PLUGIN_DIR, _find_repo_root()):
        if base is None:
            continue
        path = base / _PROMPT_RELATIVE
        if path.exists():
            return path.read_text(encoding="utf-8")
    return None


def _load_candidate_schema() -> dict:
    return load_schema("candidate-batch")


def _has_image(blocks: list[dict]) -> bool:
    return any(block.get("type") == "image" for block in blocks)


def _infer_source_type(blocks: list[dict]) -> str:
    return "image" if _has_image(blocks) else "text"


class GoodStudentPlugin:
    """Hermes 宿主侧编排。所有返回都是统一 envelope：{ok, data, warnings, error, trace_id}。"""

    def __init__(self, bridge: HermesHostBridge, data_dir: str | Path | None = None):
        self.bridge = bridge
        self._data_dir = data_dir
        # Service 惰性创建：register() 在 Hermes 每次启动时执行，若在注册时建库，
        # 会在默认目录产生空库副作用（M1 真机验收发现）；首次工具调用才落地数据目录。
        self._service: Service | None = None

    @property
    def service(self) -> Service:
        if self._service is None:
            resolved = self._data_dir or self.bridge.data_dir()  # None → Service 默认
            self._service = Service(resolved)
        return self._service

    def close(self) -> None:
        if self._service is not None:
            self._service.close()

    # ---- 健康检查与能力发现（§7.1）----

    def capabilities(self) -> dict:
        resp = self.service.capabilities()
        if not resp["ok"]:
            return resp
        caps = self.bridge.capabilities()
        resp["data"]["host"] = {
            "name": caps.host,
            "vision": caps.vision,
            "detail": caps.detail,
        }
        resp["data"]["plugin"] = {
            "name": "good-student-hermes",
            "version": core_version,
            "extraction_mode": "A",
            "extraction_tool": EXTRACT_TOOL,
        }
        resp["data"]["tools"] = [*TOOLS, EXTRACT_TOOL]
        return resp

    def doctor(self) -> dict:
        resp = self.service.doctor()
        if not resp["ok"]:
            return resp
        checks = resp["data"]["checks"]
        caps = self.bridge.capabilities()
        checks.append(
            {"check": "host:bridge", "ok": True, "detail": f"{caps.host} vision={caps.vision}"}
        )
        prompt = _load_extraction_prompt()
        checks.append(
            {
                "check": "asset:extraction_prompt",
                "ok": prompt is not None,
                "detail": "loaded" if prompt else "prompts/wrong-book-extraction.md 未找到",
            }
        )
        try:
            _load_candidate_schema()
            checks.append({"check": "asset:candidate_schema", "ok": True, "detail": "loaded"})
        except Exception as exc:  # noqa: BLE001
            checks.append({"check": "asset:candidate_schema", "ok": False, "detail": str(exc)})
        resp["data"]["ok"] = all(c["ok"] for c in checks)
        return resp

    # ---- 识别主流程（模式 A）----

    def extract_and_ingest(
        self,
        student_id: str,
        attachments: list[dict] | list[AttachmentRef],
        source_type: str | None = None,
        page_count: int | None = None,
        captured_at: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict:
        """附件 → 宿主模型结构化识别 → core 校验 ingest。任何失败都不写入数据。"""
        refs = [a if isinstance(a, AttachmentRef) else AttachmentRef(**a) for a in attachments]
        if not refs:
            return _err("no_attachment", "没有可读取的错题材料，请先上传附件")

        caps = self.bridge.capabilities()
        try:
            blocks = self.bridge.resolve_attachments(refs)
        except Exception as exc:  # noqa: BLE001 — 附件读取失败不进入识别
            return _err("no_attachment", f"附件读取失败，未调用模型：{exc}")
        if not blocks:
            return _err("no_attachment", "没有可读取的错题材料，请先上传附件")

        if _has_image(blocks) and not caps.vision:
            return _err(
                "host_capability_missing",
                "当前宿主模型无视觉能力，无法识别图片材料；"
                "请改用文本粘贴、可读 PDF 文本或手工录入",
                details={"required": "vision", "host": caps.host},
            )

        prompt = _load_extraction_prompt()
        if prompt is None:
            return _err(
                "asset_missing",
                "提取指令 prompts/wrong-book-extraction.md 未找到，未调用模型",
            )

        source_ref = refs[0].ref if len(refs) == 1 else f"{HOST_NAME}-batch-{uuid.uuid4()}"
        batch, failure = self._extract_with_retry(prompt, blocks)
        if failure is not None:
            return failure

        batch.setdefault("schema_version", 1)
        source = batch.setdefault("source", {})
        source.setdefault("source_type", source_type or _infer_source_type(blocks))
        source.setdefault("source_ref", source_ref)
        if page_count is not None:
            source.setdefault("page_count", page_count)

        resp = self.service.ingest_candidates(
            student_id,
            batch,
            host=HOST_NAME,
            captured_at=captured_at,
            idempotency_key=idempotency_key,
        )
        if resp.get("ok"):
            self.bridge.log(
                "info",
                f"extraction_ingested batch_id={resp['data'].get('batch_id')} "
                f"count={resp['data'].get('candidate_count')}",
            )
        return resp

    def _extract_with_retry(
        self, instructions: str, blocks: list[dict]
    ) -> tuple[dict | None, dict | None]:
        """最多两次尝试：首次 + 一次重试。返回 (batch, failure_envelope)。

        修复提示（"上次输出未通过校验，请严格按 Schema 重出"）只附加给**输出校验类**失败
        （invalid_json / schema_mismatch）——只有模型确实产出不合规输出才需要按 Schema 重出；
        超时/限流/拒绝是宿主侧瞬时问题，模型根本没产出，附加修复提示是误导（M5）。
        附加时替换 {reason} 占位符（原实现把字面量 {reason} 直接拼进 prompt）。
        """
        schema = _load_candidate_schema()
        last_kind = ""
        last_reason = ""
        for attempt in (1, 2):
            if attempt == 1:
                text = instructions
            elif last_kind in ("invalid_json", "schema_mismatch"):
                text = instructions + _REPAIR_HINT.format(reason=last_reason)
            else:
                text = instructions  # 宿主侧失败（timeout/refused/rate_limited）：不带修复提示重试
            try:
                result = self.bridge.complete_structured(
                    instructions=text,
                    input_blocks=blocks,
                    json_schema=schema,
                    purpose="good-student.extract",
                    timeout=LLM_TIMEOUT_SECONDS,
                )
            except HostLlmError as exc:
                # 超时/拒绝/限流：重试一次后放弃（§19 host LLM 行）
                last_kind, last_reason = exc.kind, exc.message
                self.bridge.log("warning", f"host_llm_error kind={exc.kind} attempt={attempt}")
                continue
            except ValueError as exc:
                # 宿主侧 Schema 校验失败：修复重试一次后放弃（§19 structured extraction 行）
                last_kind, last_reason = "schema_mismatch", str(exc)
                self.bridge.log("warning", f"schema_validation_failed attempt={attempt}")
                continue
            if isinstance(result.parsed, dict):
                return result.parsed, None
            last_kind, last_reason = "invalid_json", (result.text or "")[:200]
            self.bridge.log("warning", f"invalid_json attempt={attempt}")

        if last_kind in ("timeout", "refused", "rate_limited", "unknown"):
            return None, _err(
                "host_llm_error",
                "识别暂时失败（宿主模型调用未成功），数据未写入，可稍后重试",
                details={"kind": last_kind, "attempts": 2, "reason": last_reason},
            )
        return None, _err(
            "structured_extraction_failed",
            "宿主模型两次输出都未通过结构化校验，数据未写入；请改用人工录入",
            details={"kind": last_kind, "attempts": 2, "reason": last_reason},
        )

    # ---- core 工具透传（保持 envelope 一致，业务逻辑不复制）----

    def create_student(self, *args, **kwargs) -> dict:
        return self.service.create_student(*args, **kwargs)

    def list_students(self, *args, **kwargs) -> dict:
        return self.service.list_students(*args, **kwargs)

    def record_scores(self, *args, **kwargs) -> dict:
        return self.service.record_scores(*args, **kwargs)

    def list_scores(self, *args, **kwargs) -> dict:
        return self.service.list_scores(*args, **kwargs)

    def ingest_candidates(self, *args, **kwargs) -> dict:
        kwargs.setdefault("host", HOST_NAME)
        return self.service.ingest_candidates(*args, **kwargs)

    def list_pending(self, *args, **kwargs) -> dict:
        return self.service.list_pending(*args, **kwargs)

    def confirm_questions(self, *args, **kwargs) -> dict:
        return self.service.confirm_questions(*args, **kwargs)

    def analyze(self, *args, **kwargs) -> dict:
        return self.service.analyze(*args, **kwargs)

    def create_plan(self, *args, **kwargs) -> dict:
        return self.service.create_plan(*args, **kwargs)

    def record_reassessment(self, *args, **kwargs) -> dict:
        return self.service.record_reassessment(*args, **kwargs)

    def weekly_brief(self, *args, **kwargs) -> dict:
        return self.service.weekly_brief(*args, **kwargs)

    def export_student(self, *args, **kwargs) -> dict:
        return self.service.export_student(*args, **kwargs)

    def delete_student(self, *args, **kwargs) -> dict:
        return self.service.delete_student(*args, **kwargs)
