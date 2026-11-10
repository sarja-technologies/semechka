"""The Spark media engine (OpenSfield's ComfyUI behind a loopback API): image and video generation for Semechka.

Same ``media_*`` interface as ``sarness_media.SarnessMediaMixin`` (so ``media_svc``, the routers, Imajutsu, the Studio and Work Mode run unchanged), against the engine's
own contract (docs/MEDIA_ENGINE.md, section 4):

  GET  /v1/health /v1/system /v1/capabilities          catalog and state
  POST /v1/uploads                                     pictures/videos for jobs
  POST /v1/jobs {kind, simple, client_key, title}      idempotent by client_key
  POST /v1/profiles/{id}/jobs {inputs, client_key}     video in, video out
  GET  /v1/jobs/{id}   POST /v1/jobs/{id}/cancel       status (progress is ComfyUI's own), cancel
  GET  /v1/files/{id}  DELETE /v1/generations/{id}     result bytes, discard after copy
  POST /v1/release                                     free ComfyUI's models

The engine does LLM work for nobody: ``discover()`` lists image and video models and profiles only; chat models come from the LLM backend.
"""
from __future__ import annotations

import httpx

from .base import Adapter, AdapterError, DiscoveredModel, Manifest
from .sarness_media import SarnessMediaMixin, _h, clean_error
from .openai_compat import _short

_IMAGE_KEYS = ("model", "prompt", "negative_prompt", "width", "height", "steps", "guidance", "seed", "scheduler",
               "reference_image", "denoise", "extra_references", "mask_image", "loras", "upscale", "batch_size")
_VIDEO_KEYS = ("model", "prompt", "negative_prompt", "width", "height", "frames", "fps", "steps", "guidance", "seed",
               "flow_shift", "start_image", "end_image", "extra_references")


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def media_meta(m: dict) -> dict:
    """An engine model document -> ``Model.meta`` in the shape ``media_svc`` and the pages already read (see ``adapters.sarness.media_meta``)."""
    refs = m.get("references")
    media = {"kind": m.get("kind"), "modes": list(m.get("modes") or []), "negative_prompt": bool(m.get("negative_prompt")), "schedulers": list(m.get("schedulers") or []),
             "audio": bool(m.get("audio")),
             **({"references": int(refs)} if isinstance(refs, int) and not isinstance(refs, bool) and refs > 0 else {}),
             "defaults": m.get("defaults") or {}, "limits": m.get("limits") or {}}
    if m.get("speed_switch"):
        media["speed_switch"] = True
    if m.get("caution"):
        media["caution"] = clean_error(m["caution"])
    if m.get("timing"):
        media["timing"] = m["timing"]
    return _drop_none({"engine_name": m.get("name"), "architecture": m.get("family"), "license": m.get("license"), "description": m.get("description"),
                       "size_bytes": m.get("size_bytes"), "vram_estimate_mb": m.get("vram_estimate_mb"), "runtime": "comfyui", "media": media})


def profile_meta(p: dict) -> dict:
    """An engine profile document -> ``Model.meta`` (video in, video out). No Studio ``modes``: the Studio says so instead of guessing."""
    r = p.get("readiness") or {}
    return _drop_none({
        "engine_name": p.get("name"), "architecture": p.get("family"), "description": p.get("notes"), "runtime": "comfyui",
        "media": {"kind": p.get("category") or "video", "modes": ["video-edit"], "negative_prompt": False, "schedulers": [], "audio": True, "defaults": {}, "limits": {},
                  "profile": {"id": p["id"], "family": p.get("family"), "ready": bool(r.get("ready")), "state": r.get("state"), "reason": r.get("reason"),
                              "official": p.get("official", True), "inputs_schema": p.get("inputs_schema") or {},
                              "operations": (p.get("capabilities") or {}).get("operations") or [],
                              "operation_status": p.get("operation_status") or {}}}})


def discovered_from_capabilities(doc: dict) -> list[DiscoveredModel]:
    """Everything the engine currently offers as Semechka media models. Models the engine lists but cannot run now are NOT returned (the catalog sync then marks
    an existing row unavailable)."""
    out: list[DiscoveredModel] = []
    for m in doc.get("models") or []:
        if m.get("supported") and m.get("kind") in ("image", "video"):
            out.append(DiscoveredModel(f"{m['kind']}:{m['id']}", [m["kind"]], media_meta(m), loaded=bool(m.get("loaded"))))
    for p in doc.get("profiles") or []:
        if isinstance(p, dict) and p.get("id") and (p.get("readiness") or {}).get("ready"):
            kind = p.get("category") or "video"
            out.append(DiscoveredModel(f"{kind}:{p['id']}", [kind], profile_meta(p), loaded=False))
    return out


class SparkMediaMixin(SarnessMediaMixin):
    """Overrides only what differs from the previous engine's wire format; the streaming helpers (fetch, segment, peek) are inherited unchanged."""

    async def _call(self, method, path, *, json=None, timeout=20):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.request(method, self.base_url + path, json=json, headers=_h(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e
        if r.status_code in (401, 403):
            raise AdapterError("The media engine rejected the API token.", kind="auth")
        if r.status_code >= 400:
            try:
                d = r.json().get("detail", r.text)
            except ValueError:
                d = r.text
            if isinstance(d, dict):
                d = d.get("message") or d
            raise AdapterError(f"Media engine: {clean_error(d)}", status=r.status_code if r.status_code in (404, 409, 422, 429) else 502,
                               kind="rejected" if r.status_code < 500 else "backend_error")
        return r

    async def media_submit(self, kind: str, params: dict, client_key: str, title: str | None = None) -> str:
        keys = _IMAGE_KEYS if kind == "image" else _VIDEO_KEYS
        simple = {k: params[k] for k in keys if params.get(k) is not None}
        r = await self._call("POST", "/v1/jobs", json={"kind": kind, "simple": simple, "client_key": client_key, "title": title or f"semechka {kind}"})
        return r.json()["id"]

    async def media_submit_profile(self, profile_id: str, inputs: dict, client_key: str, title: str | None = None) -> str:
        r = await self._call("POST", f"/v1/profiles/{profile_id}/jobs", json={"inputs": inputs, "client_key": client_key, "title": title or f"semechka {profile_id}"})
        return r.json()["id"]

    async def media_upload(self, data, mime: str, name: str) -> str:
        """Hand a source picture or video to the engine. Returns its upload id (the engine removes old uploads itself after two days).
        The engine decides the file type from the extension, but a library asset is named by its display name ("Video 12 Nov"), which has none:
        the extension is derived from the MIME type when the name does not carry a usable one."""
        name = upload_name(name, mime)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10)) as c:
                r = await c.post(self.base_url + "/v1/uploads", headers=_h(self.api_key), files={"file": (name, data, mime)})
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e
        if r.status_code >= 400:
            try:
                why = clean_error(r.json().get("detail", ""))
            except ValueError:
                why = ""
            raise AdapterError(f"The media engine refused the source file (HTTP {r.status_code}{': ' + why if why else ''}).", status=422, kind="rejected")
        return r.json()["id"]

    async def media_status(self, remote_id: str) -> dict:
        j = (await self._call("GET", f"/v1/jobs/{remote_id}")).json()
        err = j.get("error")
        if isinstance(err, dict):
            err = err.get("message")
        outs = []
        for g in j.get("generations") or []:
            outs.append({"ref": g.get("file_id") or g.get("id"), "discard_id": g.get("id"), "kind": g.get("type"), "width": g.get("width"), "height": g.get("height"),
                         "seed": g.get("seed"), "duration": g.get("duration"), "fps": g.get("fps"), "frames": g.get("frames")})
        prog = j.get("progress")
        return {"status": j.get("status"), "progress": float(prog) if isinstance(prog, (int, float)) else None, "text": clean_error(j.get("progress_text") or ""),
                "queue_position": j.get("queue_position"), "error": clean_error(err) if err else None, "outputs": outs, "applied": j.get("applied") or {}}

    async def media_discard(self, discard_id: str) -> None:
        await self._call("DELETE", f"/v1/generations/{discard_id}?permanent=true")

    async def media_estimate(self, profile_id: str, query: dict):
        from urllib.parse import quote, urlencode
        q = urlencode({k: v for k, v in query.items() if v is not None})
        return await self._peek_json(f"/v1/profiles/{quote(profile_id, safe='')}/estimate?{q}", timeout=8)

    async def media_model_estimate(self, model_id: str, query: dict):
        from urllib.parse import quote, urlencode
        q = urlencode({k: v for k, v in query.items() if v is not None})
        return await self._peek_json(f"/v1/models/{quote(model_id, safe='')}/estimate?{q}", timeout=8)

    async def media_release(self, force: bool = False) -> dict:
        """Free the engine's resident models so the memory returns to the system. -> {"ok", "freed_gb", ...}; raises AdapterError(kind="busy") while a job runs."""
        try:
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(self.base_url + "/v1/release", json={"force": force}, headers=_h(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e
        if r.status_code == 409:
            raise AdapterError("A generation is running; the models cannot be freed now.", status=409, kind="busy")
        if r.status_code >= 400:
            raise AdapterError(f"The media engine could not free memory (HTTP {r.status_code}).")
        return r.json()

    async def media_reverse(self, remote_id: str) -> dict:
        """The Reverse Engineer Video analysis and plan of a finished ``reverse-video`` job."""
        return (await self._call("GET", f"/v1/reverse/{remote_id}", timeout=60)).json()

    async def _peek(self, path: str, *, timeout=20) -> httpx.Response:
        """A GET whose status the caller inspects itself (no AdapterError for 4xx); unreachable still raises."""
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                return await c.get(self.base_url + path, headers=_h(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e

    async def media_reverse_file(self, remote_id: str, name: str, max_bytes: int = 8 * 1024 * 1024) -> tuple[int, bytes | None, str]:
        r = await self._peek(f"/v1/reverse/{remote_id}/files/{name}", timeout=30)
        if r.status_code != 200 or len(r.content) > max_bytes:
            return r.status_code, None, ""
        return 200, r.content, r.headers.get("content-type", "application/octet-stream").split(";")[0]


_EXT_BY_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "video/mp4": ".mp4", "video/quicktime": ".mov", "video/webm": ".webm",
                "video/x-matroska": ".mkv", "audio/mpeg": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/mp4": ".m4a"}
_KNOWN_EXT = set(_EXT_BY_MIME.values()) | {".jpeg", ".gif", ".bmp", ".avi", ".m4v", ".flac", ".ogg", ".aac"}


def upload_name(name: str, mime: str) -> str:
    """A file name the engine can classify: keep a known extension, otherwise append the one that belongs to the MIME type."""
    name = (name or "upload").strip().replace("/", "_").replace("\\", "_") or "upload"
    dot = name.rfind(".")
    if dot > 0 and name[dot:].lower() in _KNOWN_EXT:
        return name
    ext = _EXT_BY_MIME.get((mime or "").split(";")[0].strip().lower())
    return name + ext if ext else name


class SparkMediaAdapter(SparkMediaMixin, Adapter):
    manifest = Manifest(
        id="spark-media", name="Spark media engine", model_load=False, model_unload=True, worker_stop=True, media=True, system_telemetry=True,
        notes="Image and video generation on the Spark's ComfyUI (OpenSfield workflows that were rendered and reviewed there). Models load when a job needs them and are "
              "freed on request. Chat models come from the LLM backend.")

    async def health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(self.base_url + "/v1/health")
                r.raise_for_status()
                j = r.json()
                a = await c.get(self.base_url + "/v1/system", headers=_h(self.api_key))
            if a.status_code in (401, 403):
                return {"ok": False, "version": j.get("version"), "detail": "The media engine rejected the API token."}
            if not j.get("comfy_reachable"):
                return {"ok": False, "version": j.get("version"), "api_version": j.get("api_version"), "detail": "The media engine is up but its ComfyUI is not reachable."}
            return {"ok": True, "version": j.get("version"), "api_version": j.get("api_version"), "detail": "reachable"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "version": None, "detail": _short(e)}

    async def _get_json(self, path: str, timeout: float = 15):
        status, doc = await self._peek_json(path, timeout=timeout)
        if status == 503:
            raise AdapterError("The media engine is not configured (no token file).", kind="auth")
        if status != 200 or doc is None:
            raise AdapterError(f"The media engine answered HTTP {status} for {path}.")
        return doc

    async def capabilities(self) -> dict:
        return await self._get_json("/v1/capabilities")

    async def discover(self) -> list[DiscoveredModel]:
        try:
            return discovered_from_capabilities(await self.capabilities())
        except AdapterError:
            raise
        except Exception as e:  # noqa: BLE001
            raise AdapterError(f"Could not list models: {_short(e)}") from e

    async def status(self) -> dict | None:
        try:
            system = await self._get_json("/v1/system", timeout=8)
            caps = await self._get_json("/v1/capabilities", timeout=8)
        except AdapterError:
            return None
        mem = system.get("memory") or {}
        comfy = system.get("comfy") or {}
        jobs = system.get("jobs") or {}
        loaded = [m["id"] for m in caps.get("models") or [] if m.get("loaded")]
        return {"engine": {"version": (caps.get("engine") or {}).get("version"), "api_version": caps.get("api_version")},
                "memory": {"total_mb": int(mem.get("total_gb", 0) * 1024), "available_mb": int(mem.get("available_gb", 0) * 1024), "unified": True},
                "llm": {"state": None, "model_id": None, "active_streams": 0},
                "worker": {"alive": bool(comfy.get("reachable")), "busy": bool(jobs.get("running")), "loaded": loaded},
                "jobs": {"running": jobs.get("running"), "queued": jobs.get("queued"), "concurrency": 1},
                "health": "ok" if comfy.get("reachable") else "comfyui unreachable"}

    async def engine_jobs(self) -> list[dict] | None:
        out: list[dict] = []
        try:
            for st in ("running", "queued"):
                rows = await self._get_json(f"/v1/jobs?status={st}&limit=200", timeout=8)
                for j in rows if isinstance(rows, list) else []:
                    out.append({"id": j.get("id"), "kind": j.get("kind"), "status": j.get("status"), "model": j.get("model"),
                                "progress": j.get("progress") if isinstance(j.get("progress"), (int, float)) else None, "queue_position": j.get("queue_position"),
                                "waiting_reason": clean_error(j["progress_text"]) if j.get("phase") == "waiting_memory" and j.get("progress_text") else None,
                                "created_at": j.get("created_at"), "started_at": j.get("started_at")})
        except AdapterError:
            return None
        return out

    async def stop_worker(self) -> None:
        await self.media_release(force=False)

    async def unload(self, model: str) -> None:
        await self.media_release(force=False)
