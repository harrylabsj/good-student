"""宿主桥抽象：把 Hermes 运行时（ctx）隔离为薄接口，核心逻辑不依赖 Hermes SDK。

设计依据：产品文档 §7.1（Plugin 职责）、§9（跨宿主适配）、§10.1 模式 A
（`ctx.llm.complete_structured()`）、§19（错误与降级）。

- `HermesHostBridge` 是协议：能力探测、附件解析、结构化模型调用、数据目录。
- `HermesBridge` 是真实实现，包装 Hermes 注入的 `ctx`。
- 测试使用 `testing.FakeHermesBridge`（内存实现），Hermes 运行时无需可用。
"""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# 输入块大小上限：避免病态输入撑爆模型上下文（参照 Hermes 官方示例插件的 16k 截断）。
MAX_TEXT_CHARS = 16_000
# 附件文件大小上限：防止病态/注入输入撑爆内存（图片不截断，必须先于 read_bytes 检查）。
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

ATTACHMENT_KINDS = ("path", "bytes", "url")


def _under(path: Path, root: Path) -> bool:
    """path 是否位于 root 之内（两者均已 resolve）。"""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class HostLlmError(Exception):
    """宿主模型调用失败（超时/限流/拒绝/供应商错误等）。

    `kind` 取 timeout / refused / rate_limited / unknown，用于错误注册表（§19）映射。
    """

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


class HostCapabilityError(Exception):
    """宿主缺少必需能力（如无视觉能力）时的内部信号。"""


@dataclass
class HostCapabilities:
    """宿主能力快照。`vision` 为 False 时插件走文本降级路径（§19）。"""

    host: str
    vision: bool
    detail: str = ""


@dataclass
class AttachmentRef:
    """宿主传入的附件引用。默认只保存引用，不复制原图（§18.1）。

    - kind="path"：宿主本地文件路径（ref 为路径字符串）。
    - kind="bytes"：已读入的字节（ref 为展示名，data 为内容）。
    - kind="url"：远程 URL（仅记录引用；本骨架不主动下载）。
    """

    kind: str
    ref: str
    mime_type: str | None = None
    data: bytes | None = None


@dataclass
class StructuredResult:
    """宿主结构化调用的归一化结果（对齐 Hermes PluginLlmStructuredResult）。

    parsed 为 None 表示模型未产出可解析 JSON，text 携带原始输出。
    """

    parsed: Any | None
    text: str
    provider: str = ""
    model: str = ""
    content_type: str = "json"
    audit: dict = field(default_factory=dict)


@runtime_checkable
class HermesHostBridge(Protocol):
    """插件对宿主的全部依赖。核心逻辑只面向本协议编程。"""

    def capabilities(self) -> HostCapabilities:
        """返回宿主能力快照（宿主名、是否有视觉能力等）。"""
        ...

    def data_dir(self) -> Path | None:
        """宿主提供的数据目录；返回 None 时插件回落到默认 `~/.good-student/`。"""
        ...

    def resolve_attachments(self, attachments: list[AttachmentRef]) -> list[dict]:
        """把附件引用解析为 `complete_structured` 的 typed input blocks。

        图片块形如 {"type": "image", "data": bytes, "mime_type": ...}；
        文本块形如 {"type": "text", "text": ...}。
        """
        ...

    def complete_structured(
        self,
        instructions: str,
        input_blocks: list[dict],
        json_schema: dict,
        purpose: str,
        timeout: int | None = None,
    ) -> StructuredResult:
        """调用宿主模型做结构化识别（模式 A）。

        - 模型输出无法解析为 JSON 时返回 parsed=None。
        - Schema 校验失败时抛 ValueError（对齐 Hermes 文档行为）。
        - 超时/拒绝/限流/供应商错误抛 HostLlmError。
        """
        ...

    def log(self, level: str, message: str) -> None:
        """写宿主日志（只记元数据，不记题目/答案/路径，§18.1）。"""
        ...


def _classify_exception(exc: Exception) -> str:
    name = type(exc).__name__.lower()
    text = f"{name} {exc}".lower()
    if "timeout" in text or "timed out" in text:
        return "timeout"
    if "ratelimit" in name or "rate_limit" in text or "429" in text:
        return "rate_limited"
    if "refus" in text or "content_filter" in text or "moderation" in text:
        return "refused"
    return "unknown"


class HermesBridge:
    """真实宿主桥：包装 Hermes 注入的插件上下文 `ctx`。

    API 依据 Hermes 官方文档《Plugin LLM Access》：
    - `ctx.llm.complete_structured(instructions=, input=, json_schema=, ...)`
      返回对象带 `.parsed`（校验失败抛 ValueError；无法解析时 parsed=None）。
    - `ctx.get_config(key, default=...)` 读取本插件的 settings 命名空间。
    """

    def __init__(self, ctx: Any):
        self._ctx = ctx

    def capabilities(self) -> HostCapabilities:
        # 假设：Hermes 自带视觉路由（active 模型无视觉时自动回落到配置的视觉模型），
        # 因此默认 vision=True；运营者可用 `vision_capable: false` 显式声明降级。
        # 详见 README「API 假设」。
        vision = True
        try:
            vision = bool(self._ctx.get_config("vision_capable", default=True))
        except Exception:  # noqa: BLE001 — 配置读取失败不阻断注册
            vision = True
        return HostCapabilities(host="hermes", vision=vision, detail="host vision routing")

    def data_dir(self) -> Path | None:
        try:
            configured = self._ctx.get_config("data_dir", default="")
        except Exception:  # noqa: BLE001
            configured = ""
        return Path(configured).expanduser() if configured else None

    def resolve_attachments(self, attachments: list[AttachmentRef]) -> list[dict]:
        blocks: list[dict] = []
        roots = self._attachment_roots()
        for att in attachments:
            if att.kind == "bytes":
                blocks.append(self._bytes_block(att.data or b"", att.mime_type, att.ref))
            elif att.kind == "path":
                path = Path(att.ref).expanduser().resolve()
                if roots is not None and not any(_under(path, root) for root in roots):
                    raise HostCapabilityError(
                        f"附件路径不在允许的 attachment_roots 内：{path}（配置的根目录："
                        + "、".join(str(r) for r in roots)
                        + "）"
                    )
                blocks.append(self._path_block(path))
            elif att.kind == "url":
                mime = att.mime_type or ""
                if mime.startswith("image/"):
                    blocks.append({"type": "image", "url": att.ref, "mime_type": mime})
                else:
                    blocks.append({"type": "text", "text": f"[附件引用] {att.ref}"})
            else:
                raise HostCapabilityError(f"未知附件类型：{att.kind}")
        return blocks

    @staticmethod
    def _bytes_block(data: bytes, mime_type: str | None, name: str) -> dict:
        mime = mime_type or "application/octet-stream"
        if mime.startswith("image/"):
            return {"type": "image", "data": data, "mime_type": mime, "file_name": name}
        text = data.decode("utf-8", errors="replace")
        if len(text) > MAX_TEXT_CHARS:
            text = text[:MAX_TEXT_CHARS] + "\n[... truncated ...]"
        return {"type": "text", "text": text}

    def _attachment_roots(self) -> list[Path] | None:
        """读取宿主配置 attachment_roots；未配置返回 None（不强制包含校验）。

        配置为单个目录路径字符串或目录列表；被注入的模型只能从这些根目录读附件，
        防止读取 ~/.ssh/id_rsa 等任意敏感文件（H5）。
        """
        try:
            raw = self._ctx.get_config("attachment_roots", default=None)
        except Exception:  # noqa: BLE001 — 配置读取失败不阻断
            raw = None
        if not raw:
            return None
        if isinstance(raw, str):
            raw = [raw]
        roots = [Path(r).expanduser().resolve() for r in raw if isinstance(r, str) and r.strip()]
        return roots or None

    @classmethod
    def _path_block(cls, path: Path) -> dict:
        if not path.is_file():
            raise HostCapabilityError(f"附件必须是常规文件（拒绝目录/特殊文件）：{path}")
        size = path.stat().st_size
        if size > MAX_ATTACHMENT_BYTES:
            raise HostCapabilityError(
                f"附件超过大小上限 {MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB：{path}（{size} 字节）"
            )
        mime, _ = mimetypes.guess_type(str(path))
        if mime and mime.startswith("image/"):
            return {
                "type": "image",
                "data": path.read_bytes(),
                "mime_type": mime,
                "file_name": path.name,
            }
        return cls._bytes_block(path.read_bytes(), mime or "text/plain", path.name)

    def complete_structured(
        self,
        instructions: str,
        input_blocks: list[dict],
        json_schema: dict,
        purpose: str,
        timeout: int | None = None,
    ) -> StructuredResult:
        kwargs: dict[str, Any] = {
            "instructions": instructions,
            "input": input_blocks,
            "json_schema": json_schema,
            "schema_name": "good-student.candidate-batch",
            "purpose": purpose,
            "temperature": 0.0,
        }
        if timeout is not None:
            kwargs["timeout"] = timeout
        try:
            result = self._ctx.llm.complete_structured(**kwargs)
        except ValueError:
            raise  # 空输入或 Schema 校验失败：属于结构化识别失败，由上层走修复重试
        except Exception as exc:  # noqa: BLE001 — 归一化为 HostLlmError
            raise HostLlmError(_classify_exception(exc), str(exc)) from exc
        return StructuredResult(
            parsed=getattr(result, "parsed", None),
            text=getattr(result, "text", "") or "",
            provider=getattr(result, "provider", "") or "",
            model=getattr(result, "model", "") or "",
            content_type=getattr(result, "content_type", "json") or "json",
            audit=getattr(result, "audit", None) or {},
        )

    def log(self, level: str, message: str) -> None:
        import logging

        logging.getLogger("good-student-hermes").log(
            getattr(logging, level.upper(), logging.INFO), "%s", message
        )
