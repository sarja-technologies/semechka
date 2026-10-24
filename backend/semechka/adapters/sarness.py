"""Sarness Engine adapter (preferred, optional).

Audited against the Sarness Engine HTTP API v1 (docs/API.md in the Sarness repository):
  GET  /v1/health            no auth; api_version, versions
  GET  /v1/system            hardware, runtime versions, worker/chat state   (Bearer)
  GET  /v1/agent/models      chat models: local GGUFs (`local:<id>`) and remote endpoint models (`endpoint:...`)
  POST /v1/agent/completions stateless SSE completion; Semechka owns the transcript
  POST /v1/llm/load|unload   {model_id}
Semechka does not use Sarness' own conversation store, so no chat content is persisted by Sarness.
Models Sarness reaches through a remote endpoint are marked external, so the egress policy applies.
"""
from __future__ import annotations

import json

import httpx

from .base import Adapter, AdapterError, DiscoveredModel, Manifest
from .openai_compat import _short
from .sarness_media import SarnessMediaMixin


def _h(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


class SarnessAdapter(SarnessMediaMixin, Adapter):
    manifest = Manifest(
        id="sarness", name="Sarness Engine", model_load=True, model_unload=True, system_telemetry=True, media=True,
        reports_usage_tokens=True, reports_timings=True,
        model_metadata=["context", "size", "vram_estimate_mb"],
        notes="Install of new models is done in Sarness itself. Image and video generation use the engine's job queue; video is text-to-video only in this release.")

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.base_url + "/v1/health")
                r.raise_for_status()
                j = r.json()
                a = await c.get(self.base_url + "/v1/system", headers=_h(self.api_key))
            if a.status_code in (401, 403):
                return {"ok": False, "version": j.get("version"), "detail": "Engine rejected the API token."}
            return {"ok": True, "version": j.get("version") or j.get("engine_version"),
                    "api_version": j.get("api_version"), "detail": "reachable"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "version": None, "detail": _short(e)}

    async def discover(self) -> list[DiscoveredModel]:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(self.base_url + "/v1/agent/models", headers=_h(self.api_key))
                if r.status_code in (401, 403):
                    raise AdapterError("Engine rejected the API token.", status=502, kind="auth")
                r.raise_for_status()
                st = await c.get(self.base_url + "/v1/llm/status", headers=_h(self.api_key))
                loaded_model = (st.json() or {}).get("model") if st.status_code == 200 else None
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"Could not list models: {_short(e)}") from e
        out = []
        for m in r.json():
            caps = ["chat"] + (["vision"] if m.get("vision") else [])
            if m.get("tools"):
                caps.append("tools")
            meta = {"context": m.get("context_max"), "size_bytes": m.get("size"), "vram_estimate_mb": m.get("vram_mb"),
                    "provider": m.get("provider")}
            local = bool(m.get("local"))
            out.append(DiscoveredModel(
                m["ref"], caps, {k: v for k, v in meta.items() if v is not None},
                external=not local or bool(self.options.get("external")),
                loaded=(m["ref"].split(":", 1)[1] == loaded_model) if local and loaded_model is not None else None))
        out.extend(await self.media_models())  # image/video models (own capabilities; never offered to chat)
        return out

    async def chat_stream(self, model, messages, params):
        allowed = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty",
                   "frequency_penalty", "max_tokens", "seed")
        body = {"model_ref": model, "messages": messages,
                "params": {k: params[k] for k in allowed if params.get(k) is not None}}
        finish = usage = timings = None
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as c:
                async with c.stream("POST", self.base_url + "/v1/agent/completions", json=body,
                                    headers=_h(self.api_key)) as r:
                    if r.status_code >= 400:
                        text = (await r.aread()).decode(errors="replace")
                        try:
                            text = json.loads(text).get("detail", text)
                        except ValueError:
                            pass
                        raise AdapterError(f"Sarness: {' '.join(str(text).split())[:200]}")
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        ev = json.loads(line[5:])
                        t = ev.get("type")
                        if t == "delta":
                            yield {"type": "delta", "content": ev.get("content") or "",
                                   "reasoning": ev.get("reasoning") or ""}
                        elif t == "error":
                            raise AdapterError(f"Sarness: {str(ev.get('message'))[:200]}")
                        elif t == "done":
                            finish = ev.get("finish_reason")
                            u = ev.get("usage")
                            if u:
                                usage = {"prompt_tokens": u.get("prompt_tokens"),
                                         "completion_tokens": u.get("completion_tokens")}
                            tm = ev.get("timings")
                            if tm:
                                timings = {"prompt_per_second": tm.get("prompt_per_second"),
                                           "predicted_per_second": tm.get("predicted_per_second")}
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}") from e
        yield {"type": "done", "finish_reason": finish, "usage": usage, "timings": timings}

    async def system_info(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(self.base_url + "/v1/system", headers=_h(self.api_key))
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError:
            return None

    async def load(self, model: str) -> None:
        if model.startswith(("image:", "video:")):
            raise AdapterError("Image and video models are loaded by the engine when a job needs them.", status=501, kind="unsupported")
        await self._post("/v1/llm/load", {"model_id": model.split(":", 1)[-1]})

    async def unload(self, model: str) -> None:
        if model.startswith(("image:", "video:")):
            raise AdapterError("Image and video models are unloaded by the engine itself.", status=501, kind="unsupported")
        await self._post("/v1/llm/unload", {"model_id": model.split(":", 1)[-1]})

    async def _post(self, path, body):
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                r = await c.post(self.base_url + path, json=body, headers=_h(self.api_key))
                r.raise_for_status()
        except httpx.HTTPError as e:
            raise AdapterError(f"Sarness refused: {_short(e)}") from e
