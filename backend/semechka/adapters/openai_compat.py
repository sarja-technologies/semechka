"""OpenAI-compatible servers: llama.cpp `llama-server`, vLLM, LM Studio, and others.

Capabilities are read from what the server returns. llama-server reports `meta.n_ctx_train`, `n_params` and
`size` on /v1/models; vLLM reports `max_model_len`. Anything absent stays absent.
"""
from __future__ import annotations

import json

import httpx

from .base import Adapter, AdapterError, DiscoveredModel, Manifest

_SAMPLING = ("temperature", "top_p", "max_tokens", "seed", "presence_penalty", "frequency_penalty", "stop")
_EXTRA = ("top_k", "min_p", "repeat_penalty")  # accepted by llama.cpp / vLLM


def _headers(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


class OpenAICompatAdapter(Adapter):
    manifest = Manifest(
        id="openai_compat", name="OpenAI-compatible (llama.cpp, vLLM, others)", reports_usage_tokens=True,
        reports_timings=True, model_metadata=["context", "params", "size"],
        notes="Streaming, cancellation and usage are supported. Load/unload/install depend on the server and are "
              "not exposed.")

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.base_url + "/models", headers=_headers(self.api_key))
            if r.status_code == 401:
                return {"ok": False, "version": None, "detail": "Server rejected the API key."}
            r.raise_for_status()
            return {"ok": True, "version": None, "detail": "reachable"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "version": None, "detail": _short(e)}

    async def discover(self) -> list[DiscoveredModel]:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(self.base_url + "/models", headers=_headers(self.api_key))
                r.raise_for_status()
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"Could not list models: {_short(e)}") from e
        out = []
        for m in r.json().get("data", []):
            meta = {}
            mm = m.get("meta") or {}  # llama.cpp
            if mm.get("n_ctx"):
                meta["context"] = int(mm["n_ctx"])  # what the running server will actually accept
            if mm.get("n_ctx_train"):
                meta["context_trained"] = int(mm["n_ctx_train"])
                meta.setdefault("context", int(mm["n_ctx_train"]))
            if mm.get("ftype"):
                meta["quantization"] = str(mm["ftype"])
            if str(m["id"]).lower().endswith(".gguf"):
                meta["format"] = "GGUF"
            if mm.get("n_params"):
                meta["params"] = int(mm["n_params"])
            if mm.get("size"):
                meta["size_bytes"] = int(mm["size"])
            if m.get("max_model_len"):  # vLLM
                meta["context"] = int(m["max_model_len"])
            meta["owned_by"] = m.get("owned_by") or None
            meta = {k: v for k, v in meta.items() if v is not None}
            out.append(DiscoveredModel(real_id=m["id"], capabilities=["chat"], meta=meta,
                                       external=bool(self.options.get("external"))))
        return out

    async def chat_stream(self, model, messages, params):
        body = {"model": model, "messages": messages, "stream": True, "stream_options": {"include_usage": True}}
        for k in _SAMPLING + _EXTRA:
            if params.get(k) is not None:
                body[k] = params[k]
        usage = timings = finish = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as c:
                async with c.stream("POST", self.base_url + "/chat/completions", json=body,
                                    headers=_headers(self.api_key)) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")[:300]
                        raise AdapterError(f"Backend returned {r.status_code}: {_scrub(text)}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            ch = json.loads(payload)
                        except ValueError:
                            continue
                        for choice in ch.get("choices") or []:
                            d = choice.get("delta") or {}
                            c_, r_ = d.get("content") or "", d.get("reasoning_content") or d.get("reasoning") or ""
                            if c_ or r_:
                                yield {"type": "delta", "content": c_, "reasoning": r_}
                            if choice.get("finish_reason"):
                                finish = choice["finish_reason"]
                        if ch.get("usage"):
                            usage = {"prompt_tokens": ch["usage"].get("prompt_tokens"),
                                     "completion_tokens": ch["usage"].get("completion_tokens")}
                        if ch.get("timings"):
                            t = ch["timings"]
                            timings = {"prompt_per_second": t.get("prompt_per_second"),
                                       "predicted_per_second": t.get("predicted_per_second")}
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}") from e
        yield {"type": "done", "finish_reason": finish, "usage": usage, "timings": timings}


def _short(e: Exception) -> str:
    s = str(e) or e.__class__.__name__
    return s[:160]


def _scrub(text: str) -> str:
    return " ".join(text.split())[:200]
