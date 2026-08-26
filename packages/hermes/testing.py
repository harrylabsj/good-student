"""测试用内存宿主桥：不依赖 Hermes 运行时，可脚本化每次模型调用的结果。

用法：按调用顺序向 `responses` 队列压入结果——
- `queue_parsed(batch)`：成功返回结构化批次；
- `queue_invalid_json(text)`：模型输出无法解析（parsed=None）；
- `queue_error(exc)`：抛 HostLlmError（timeout/refused/...）或 ValueError（Schema 校验失败）。
"""

from __future__ import annotations

import json
from pathlib import Path

from .bridge import AttachmentRef, HostCapabilities, StructuredResult


class FakeHermesBridge:
    """HermesHostBridge 协议的内存实现。"""

    def __init__(self, vision: bool = True, data_dir: str | Path | None = None):
        self.vision = vision
        self._data_dir = Path(data_dir) if data_dir else None
        self.responses: list = []
        self.calls: list[dict] = []
        self.logs: list[tuple[str, str]] = []

    # ---- 编排接口 ----

    def queue_parsed(self, batch: dict) -> None:
        self.responses.append(
            StructuredResult(
                parsed=batch,
                text=json.dumps(batch, ensure_ascii=False),
                provider="fake",
                model="fake-model",
            )
        )

    def queue_invalid_json(self, text: str = "这不是 JSON") -> None:
        self.responses.append(
            StructuredResult(parsed=None, text=text, provider="fake", model="fake-model",
                             content_type="text")
        )

    def queue_error(self, exc: Exception) -> None:
        self.responses.append(exc)

    # ---- HermesHostBridge 协议 ----

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(host="fake-hermes", vision=self.vision)

    def data_dir(self) -> Path | None:
        return self._data_dir

    def resolve_attachments(self, attachments: list[AttachmentRef]) -> list[dict]:
        blocks: list[dict] = []
        for att in attachments:
            if att.kind == "url":
                blocks.append({"type": "image", "url": att.ref, "mime_type": att.mime_type or "image/png"})
            elif (att.mime_type or "").startswith("image/"):
                blocks.append(
                    {"type": "image", "data": att.data or b"fake", "mime_type": att.mime_type}
                )
            else:
                text = (att.data or b"").decode("utf-8", errors="replace") or f"[fake] {att.ref}"
                blocks.append({"type": "text", "text": text})
        return blocks

    def complete_structured(self, **kwargs) -> StructuredResult:
        self.calls.append(kwargs)
        if not self.responses:
            raise AssertionError("FakeHermesBridge.responses 为空：测试未准备本次调用的结果")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def log(self, level: str, message: str) -> None:
        self.logs.append((level, message))
