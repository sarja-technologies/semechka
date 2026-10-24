"""Ollama native API. Reports model family, parameter size and quantisation, and supports load/unload."""
from __future__ import annotations

import json

import httpx

from .base import Adapter, AdapterError, DiscoveredModel, Manifest
from .openai_compat import _short

_OPTS = {"temperature": "temperature", "top_p": "top_p", "top_k": "top_k", "min_p": "min_p", "seed": "seed",
         "max_tokens": "num_predict", "repeat_penalty": "repeat_penalty", "presence_penalty": "presence_penalty",
         "frequency_penalty": "frequency_penalty"}


class OllamaAdapter(Adapter):
    manifest = Manifest(
        id="ollama", name="Ollama", model_load=True, model_unload=True, reports_usage_tokens=True,
        reports_timings=True, model_metadata=["family", "params_label", "quantization", "format", "context", "size"],
        notes="Model install is not exposed from Semechka; use `ollama pull`.")

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.base_url + "/api/version")
            r.raise_for_status()
            return {"ok": True, "version": r.json().get("version"), "detail": "reachable"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "version": None, "detail": _short(e)}

    async def discover(self) -> list[DiscoveredModel]:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(self.base_url + "/api/tags")
                r.raise_for_status()
                ps = await c.get(self.base_url + "/api/ps")
                loaded = {m["name"] for m in ps.json().get("models", [])} if ps.status_code == 200 else None
                out = []
                for m in r.json().get("models", []):
                    det = m.get("details") or {}
                    caps = ["chat"]
                    meta = {"family": det.get("family"), "params_label": det.get("parameter_size"),
                            "quantization": det.get("quantization_level"), "format": det.get("format"),
                            "size_bytes": m.get("size")}
                    try:
                        sh = await c.post(self.base_url + "/api/show", json={"model": m["name"]})
                        if sh.status_code == 200:
                            j = sh.json()
                            for k, v in (j.get("model_info") or {}).items():
                                if k.endswith(".context_length"):
                                    meta["context"] = int(v)
                            for cap in j.get("capabilities") or []:
                                if cap in ("vision", "tools", "embedding", "thinking"):
                                    caps.append("embeddings" if cap == "embedding" else cap)
                    except httpx.HTTPError:
                        pass
                    out.append(DiscoveredModel(m["name"], caps, {k: v for k, v in meta.items() if v is not None},
                                               external=bool(self.options.get("external")),
                                               loaded=None if loaded is None else m["name"] in loaded))
                return out
        except httpx.HTTPError as e:
            raise AdapterError(f"Could not list models: {_short(e)}") from e

    async def chat_stream(self, model, messages, params):
        opts = {dst: params[src] for src, dst in _OPTS.items() if params.get(src) is not None}
        body = {"model": model, "messages": messages, "stream": True, "options": opts}
        finish = usage = timings = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10)) as c:
                async with c.stream("POST", self.base_url + "/api/chat", json=body) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")[:200]
                        raise AdapterError(f"Backend returned {r.status_code}: {' '.join(text.split())}")
                    async for line in r.aiter_lines():
                        if not line.strip():
                            continue
                        j = json.loads(line)
                        if j.get("error"):
                            raise AdapterError(str(j["error"])[:200])
                        msg = j.get("message") or {}
                        if msg.get("content") or msg.get("thinking"):
                            yield {"type": "delta", "content": msg.get("content", ""),
                                   "reasoning": msg.get("thinking", "")}
                        if j.get("done"):
                            finish = j.get("done_reason")
                            usage = {"prompt_tokens": j.get("prompt_eval_count"),
                                     "completion_tokens": j.get("eval_count")}
                            if j.get("eval_duration"):
                                timings = {"predicted_per_second": j["eval_count"] / (j["eval_duration"] / 1e9),
                                           "prompt_per_second": (j["prompt_eval_count"] / (j["prompt_eval_duration"] / 1e9))
                                           if j.get("prompt_eval_duration") and j.get("prompt_eval_count") else None}
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}") from e
        yield {"type": "done", "finish_reason": finish, "usage": usage, "timings": timings}

    async def load(self, model: str) -> None:
        await self._keepalive(model, -1)

    async def unload(self, model: str) -> None:
        await self._keepalive(model, 0)

    async def _keepalive(self, model, keep):
        try:
            async with httpx.AsyncClient(timeout=120) as c:
                r = await c.post(self.base_url + "/api/generate", json={"model": model, "keep_alive": keep})
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend refused: {_short(e)}") from e
