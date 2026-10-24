"""Sarness Engine image/video jobs.

Audited against the engine source (api/jobs.py, jobs/service.py, workflow/templates.py, api/library.py):
  POST   /v1/jobs {kind, simple, title, client_key}   client_key makes submission idempotent
  GET    /v1/jobs/{id}                                 status, progress (0-1), progress_text, error, generations[]
  GET    /v1/jobs?status=queued                        queue_position per job
  POST   /v1/jobs/{id}/cancel
  GET    /v1/files/{file_id}                           bytes (Bearer)
  DELETE /v1/generations/{id}?permanent=true           removes the output file and its library row
  GET    /v1/models                                    category image|video with capabilities, defaults, limits
"""
from __future__ import annotations

import re

import httpx

from .base import AdapterError, DiscoveredModel
from .openai_compat import _short

_PATH = re.compile(r"(?:/[\w.@+\-]+){3,}")
_IMAGE_KEYS = ("model", "prompt", "negative_prompt", "width", "height", "steps", "guidance", "seed", "scheduler")
_VIDEO_KEYS = ("model", "prompt", "negative_prompt", "width", "height", "frames", "fps", "steps", "guidance", "seed",
               "flow_shift")


def clean_error(text) -> str:
    """User-safe engine message: no server file paths, bounded length."""
    return " ".join(_PATH.sub("<path>", str(text)).split())[:300]


def _h(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


class SarnessMediaMixin:
    async def media_models(self) -> list[DiscoveredModel]:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(self.base_url + "/v1/models", headers=_h(self.api_key))
            r.raise_for_status()
            rows = r.json()
        except (httpx.HTTPError, ValueError):
            return []
        out = []
        for m in rows if isinstance(rows, list) else []:
            cat = m.get("category")
            if cat not in ("image", "video") or not m.get("supported", True):
                continue
            caps = m.get("capabilities") or {}
            meta = {"family": m.get("family"), "license": m.get("license"), "vram_estimate_mb": m.get("vram_mb"),
                    "size_bytes": m.get("size"), "description": m.get("description"),
                    "media": {"kind": cat, "modes": caps.get("modes"),
                              "negative_prompt": bool(caps.get("negative_prompt")),
                              "schedulers": caps.get("schedulers") or [], "audio": bool(caps.get("audio")),
                              "defaults": m.get("defaults") or {}, "limits": m.get("limits") or {}}}
            out.append(DiscoveredModel(f"{cat}:{m['id']}", [cat], {k: v for k, v in meta.items() if v is not None},
                                       loaded=bool(m.get("loaded"))))
        return out

    async def _call(self, method, path, *, json=None, timeout=20):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.request(method, self.base_url + path, json=json, headers=_h(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e
        if r.status_code in (401, 403):
            raise AdapterError("Engine rejected the API token.", kind="auth")
        if r.status_code >= 400:
            try:
                d = r.json().get("detail", r.text)
            except ValueError:
                d = r.text
            if isinstance(d, dict):
                d = d.get("message") or d
            raise AdapterError(f"Sarness: {clean_error(d)}", status=422 if r.status_code == 422 else 502,
                               kind="rejected" if r.status_code < 500 else "backend_error")
        return r

    async def media_submit(self, kind: str, params: dict, client_key: str) -> str:
        keys = _IMAGE_KEYS if kind == "image" else _VIDEO_KEYS
        simple = {k: params[k] for k in keys if params.get(k) is not None}
        r = await self._call("POST", "/v1/jobs", json={"kind": kind, "simple": simple, "client_key": client_key,
                                                      "title": f"semechka {kind}"})
        return r.json()["id"]

    async def media_status(self, remote_id: str) -> dict:
        j = (await self._call("GET", f"/v1/jobs/{remote_id}")).json()
        status = j.get("status")
        pos = None
        if status == "queued":
            try:
                q = (await self._call("GET", "/v1/jobs?status=queued&limit=200")).json()
                pos = next((x.get("queue_position") for x in q if x.get("id") == remote_id), None)
            except AdapterError:
                pass
        err = j.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        outs = []
        for g in j.get("generations") or []:
            outs.append({"ref": g.get("file_id") or (g.get("url") or "").rsplit("/", 1)[-1], "discard_id": g.get("id"),
                         "kind": g.get("type"), "width": g.get("width"), "height": g.get("height"),
                         "seed": g.get("seed"), "duration": g.get("duration"), "fps": g.get("fps"),
                         "frames": g.get("frames")})
        prog = j.get("progress")
        return {"status": status, "progress": float(prog) if isinstance(prog, (int, float)) else None,
                "text": clean_error(j.get("progress_text") or ""), "queue_position": pos,
                "error": clean_error(err) if err else None, "outputs": outs}

    async def media_cancel(self, remote_id: str) -> None:
        await self._call("POST", f"/v1/jobs/{remote_id}/cancel")

    async def media_fetch(self, ref: str, max_bytes: int) -> tuple[bytes, str]:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(300, connect=10)) as c:
                async with c.stream("GET", f"{self.base_url}/v1/files/{ref}", headers=_h(self.api_key)) as r:
                    if r.status_code >= 400:
                        raise AdapterError(f"Sarness could not return the file (HTTP {r.status_code}).")
                    if int(r.headers.get("content-length") or 0) > max_bytes:
                        raise AdapterError("The generated file is larger than this deployment allows.", kind="too_large")
                    buf = bytearray()
                    async for chunk in r.aiter_bytes():
                        buf += chunk
                        if len(buf) > max_bytes:
                            raise AdapterError("The generated file is larger than this deployment allows.", kind="too_large")
                    return bytes(buf), r.headers.get("content-type", "application/octet-stream").split(";")[0]
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e

    async def media_discard(self, discard_id: str) -> None:
        await self._call("DELETE", f"/v1/generations/{discard_id}?permanent=true")
