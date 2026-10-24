"""Adapter contract.

An adapter turns one kind of inference server into a uniform surface. Every optional capability is declared
in the manifest and checked by callers; a missing capability is reported as unsupported, never faked.

Stream events yielded by `chat_stream`:
  {"type": "delta", "content": str, "reasoning": str}
  {"type": "done", "finish_reason": str|None, "usage": {"prompt_tokens","completion_tokens"}|None,
   "timings": {"prompt_per_second","predicted_per_second"}|None}
Errors are raised as AdapterError with a user-safe message.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, ClassVar


class AdapterError(Exception):
    def __init__(self, message: str, *, status: int = 502, kind: str = "backend_error"):
        super().__init__(message)
        self.status, self.kind = status, kind


class Unsupported(AdapterError):
    def __init__(self, what: str):
        super().__init__(f"This backend does not support: {what}.", status=501, kind="unsupported")


@dataclass
class Manifest:
    id: str
    name: str
    contract_version: int = 1
    streaming: bool = True
    cancel: bool = True  # cancellation = closing the stream
    model_load: bool = False
    model_unload: bool = False
    model_install: bool = False
    media: bool = False  # image/video generation jobs (media_* methods)
    system_telemetry: bool = False
    reports_usage_tokens: bool = False
    reports_timings: bool = False
    model_metadata: list[str] = field(default_factory=list)  # which meta keys it can supply
    notes: str = ""

    def as_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class DiscoveredModel:
    real_id: str
    capabilities: list[str]
    meta: dict[str, Any] = field(default_factory=dict)  # only fields the backend reported
    external: bool = False
    loaded: bool | None = None


class Adapter:
    manifest: ClassVar[Manifest]

    def __init__(self, base_url: str, api_key: str | None = None, options: dict | None = None):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.options = options or {}

    async def health(self) -> dict:  # {"ok": bool, "version": str|None, "detail": str}
        raise NotImplementedError

    async def discover(self) -> list[DiscoveredModel]:
        raise NotImplementedError

    def chat_stream(self, model: str, messages: list[dict], params: dict) -> AsyncIterator[dict]:
        raise NotImplementedError

    async def system_info(self) -> dict | None:
        return None

    async def load(self, model: str) -> None:
        raise Unsupported("loading models")

    async def unload(self, model: str) -> None:
        raise Unsupported("unloading models")


# ---- media block (image / video jobs) -------------------------------------------------------------------
# media_status() returns a normalised dict:
#   {"status": "queued|running|completed|failed|cancelled", "progress": float|None, "text": str,
#    "queue_position": int|None, "error": str|None,
#    "outputs": [{"ref", "discard_id", "kind", "width", "height", "seed", "duration", "fps", "frames"}]}
async def _media_unsupported(self, *a, **k):
    raise Unsupported("image and video generation")


for _name in ("media_submit", "media_status", "media_cancel", "media_fetch", "media_discard"):
    setattr(Adapter, _name, _media_unsupported)
