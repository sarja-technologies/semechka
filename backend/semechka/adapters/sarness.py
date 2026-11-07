"""Sarness Engine adapter (preferred, optional).

Audited against the Sarness Engine HTTP API v1 (engine 0.4.0, API 1.1; its /v1/openapi.json and source):
  GET  /v1/health            no auth; api_version, versions
  GET  /v1/capabilities      identity, memory, every installed model with category, runtime, kinds, quantisation,
                             size, memory estimate and loaded state, the chat model's state and the job queue
  GET  /v1/models            installed models with description, licence, tags, architecture, layers, trained context,
                             media defaults and limits (also server file paths, which are never copied)
  GET  /v1/models/catalog    the engine's catalogue: the repository each installed model came from
  GET  /v1/agent/models      chat models: local GGUFs (`local:<id>`) and remote endpoint models (`endpoint:...`)
  POST /v1/agent/completions stateless SSE completion; Semechka owns the transcript
  GET  /v1/llm/status        state stopped|loading|ready|error, model_id, active_streams, ctx, loaded_at, error
  POST /v1/llm/load          {model_id}: blocks until llama-server is ready (up to 300 s). It first stops whatever chat
                             model is loaded, even mid-reply, and nothing lets a client reserve the slot, so a check
                             made beforehand cannot protect a reply another client starts. Semechka never calls it.
  POST /v1/agent/completions also switches the chat model (same code path, engine `ensure_local`) when the request
                             names another model or another context window, again even mid-reply.
  POST /v1/llm/unload        409 while a reply is being generated (checked inside the engine)
  POST /v1/models/{id}/unload  frees a chat model or an image/video model held by the GPU worker; 409 while busy
  POST /v1/worker/stop       stops the picture/video worker process, returning all of its memory; 409 while it runs a job
  GET  /v1/jobs?status=      the engine's job queue (all of its clients, so prompts are never copied)
Semechka does not use Sarness' own conversation store, so no chat content is persisted by Sarness.
Models Sarness reaches through a remote endpoint are marked external, so the egress policy applies, except for an
endpoint the administrator lists in the backend option `local_endpoints` (a list of engine endpoint ids): that endpoint
runs on this machine or network (Trinodal AI is one), so its models are not external. For those, and only those, the
`usage.trinodal` object an endpoint returns in the final `done` event is passed on, cleaned, as `done["trinodal"]`.
Image and video models have no load call: the engine loads one when a job needs it.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote

import httpx

from .base import Adapter, AdapterError, DiscoveredModel, Manifest
from .openai_compat import _short
from .sarness_media import SarnessMediaMixin, clean_error

PRELOAD_OFF = ("Loading a chat model ahead of time is turned off for this engine: it switches its one chat model even "
               "while another client's reply is being written, and offers no way to reserve it. A chat model loads "
               "when someone first uses it.")


def endpoint_id(ref: str) -> str | None:
    """`endpoint:<id>:<model>` -> `<id>`; None for anything else (local models, picture and video models)."""
    if not isinstance(ref, str) or not ref.startswith("endpoint:"):
        return None
    return ref.split(":", 2)[1] if ref.count(":") >= 2 else None


def local_endpoints(options: dict | None) -> list[str]:
    v = (options or {}).get("local_endpoints")
    return [x for x in v if isinstance(x, str) and x] if isinstance(v, list) else []


def is_local_endpoint(ref: str, options: dict | None) -> bool:
    """True when `ref` is a model on a remote-endpoint entry of the engine that the administrator declared local."""
    eid = endpoint_id(ref)
    return eid is not None and eid in local_endpoints(options)


_TRINODAL_STR, _TRINODAL_ID = 120, re.compile(r"[\w.:/\- ]{1,64}")


def clean_trinodal(u) -> dict | None:
    """The `usage.trinodal` object of a Trinodal AI reply, reduced to the known fields with bounded size and type. The
    object is metadata about the run (which nodes took part, timings, counters). It never carries message text, so
    anything else, or anything out of shape, is dropped rather than shown."""
    if not isinstance(u, dict):
        return None
    out: dict = {}

    def text(v, n=_TRINODAL_STR):
        return v.strip()[:n] if isinstance(v, str) and v.strip() and v.isprintable() else None

    if (t := text(u.get("trace_id"), 64)) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", t):
        out["trace_id"] = t
    for key in ("nodes", "trained_components"):
        v = u.get(key)
        if isinstance(v, list):
            ids = [x for x in v[:64] if isinstance(x, str) and _TRINODAL_ID.fullmatch(x)]
            if ids:
                out[key] = ids
    if t := text(u.get("route")):
        out["route"] = t
    lc = u.get("language_core")
    if isinstance(lc, dict):
        core = {k: text(lc.get(k)) for k in ("model", "backend")}
        core = {k: v for k, v in core.items() if v}
        if core:
            out["language_core"] = core
    for key in ("ms", "llm_calls", "refused", "errors"):
        v = u.get(key)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v < 1e12:
            out[key] = round(v, 1) if isinstance(v, float) else v
    return out or None


def _h(key):
    return {"Authorization": f"Bearer {key}"} if key else {}


def _plain(v, depth: int = 0):
    """JSON data with strings cleaned of server paths and bounded in size; nothing else survives."""
    if isinstance(v, str):
        return clean_error(v)
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if depth > 4:
        return None
    if isinstance(v, list):
        return [_plain(x, depth + 1) for x in v[:60]]
    if isinstance(v, dict):
        return {str(k)[:60]: _plain(x, depth + 1) for k, x in list(v.items())[:60]}
    return None


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def _repos(catalog) -> dict[str, str]:
    """model id (and catalogue id) -> the repository the engine downloaded it from."""
    out: dict[str, str] = {}
    for e in catalog if isinstance(catalog, list) else []:
        repo = e.get("repo")
        if not isinstance(repo, str) or not repo:
            continue
        out[e.get("id")] = repo
        for q in e.get("quantizations") or []:
            if q.get("model_id"):
                out[q["model_id"]] = repo
    return out


def llm_meta(a: dict, cm: dict, im: dict, repo: str | None) -> dict:
    """Metadata for one local chat model, merged from /v1/agent/models (a), /v1/capabilities (cm) and /v1/models (im).
    Only values the engine reported; no server paths."""
    imeta, icaps, kinds = im.get("meta") or {}, im.get("capabilities") or {}, cm.get("kinds") or []
    fmt = im.get("format")
    return _clean({
        "engine_name": a.get("name") or cm.get("name") or im.get("name"),
        "context": a.get("context_max") or icaps.get("context_max"),
        "context_trained": imeta.get("context_length"),
        "default_ctx": a.get("default_ctx") or icaps.get("default_ctx"),
        "size_bytes": cm.get("size_bytes") or a.get("size") or im.get("size"),
        "vram_estimate_mb": cm.get("memory_mb") or a.get("vram_mb") or im.get("vram_mb"),
        "quantization": cm.get("quantization") or im.get("quantization"),
        "format": fmt.upper() if isinstance(fmt, str) else None,
        "architecture": imeta.get("architecture"),
        "layers": imeta.get("layers"),
        "license": im.get("license"),
        "description": im.get("description"),
        "engine_tags": im.get("tags"),
        "runtime": cm.get("runtime"),
        "source_repo": repo,
        "reasoning": True if "reasoning" in kinds else None,
        "multi_token_prediction": True if (imeta.get("mtp") or imeta.get("nextn_layers")) else None,
    })


def media_meta(cm: dict, im: dict, repo: str | None) -> dict:
    caps = im.get("capabilities") or {}
    kind = cm.get("category") or im.get("category")
    return _clean({
        "engine_name": cm.get("name") or im.get("name"),
        "architecture": cm.get("family") or im.get("family"),
        "license": im.get("license"),
        "description": im.get("description"),
        "engine_tags": im.get("tags"),
        "size_bytes": cm.get("size_bytes") or im.get("size"),
        "vram_estimate_mb": cm.get("memory_mb") or im.get("vram_mb"),
        "vram_offload_mb": im.get("vram_offload_mb"),
        "runtime": cm.get("runtime"),
        "source_repo": repo,
        "media": {"kind": kind, "modes": caps.get("modes") or cm.get("modes"),
                  "negative_prompt": bool(caps.get("negative_prompt")),
                  "schedulers": caps.get("schedulers") or [], "audio": bool(caps.get("audio")),
                  # how many pictures an image model takes as conditioning (edit models: the source counts as one); absent = it takes none
                  **({"references": int(caps["references"])} if isinstance(caps.get("references"), int) and not isinstance(caps.get("references"), bool) and caps["references"] > 0 else {}),
                  "defaults": im.get("defaults") or {}, "limits": im.get("limits") or {}},
    })


def profile_meta(p: dict) -> dict:
    """A composite capability (e.g. OpenJutsu) as a video model. Readiness and the input form come from the ENGINE; nothing here decides
    whether it works."""
    r = p.get("readiness") or {}
    return _clean({
        "engine_name": p.get("name"), "architecture": p.get("family"), "description": p.get("notes"),
        "media": {"kind": p.get("category") or "video", "modes": ["video-edit"], "negative_prompt": False, "schedulers": [],
                  "audio": True, "defaults": {}, "limits": {},
                  "profile": {"id": p["id"], "family": p.get("family"), "ready": bool(r.get("ready")), "state": r.get("state"), "reason": r.get("reason"),
                              "official": p.get("official", True), "inputs_schema": p.get("inputs_schema") or {},
                              "operations": (p.get("capabilities") or {}).get("operations") or [],
                              "estimate_mb": (p.get("memory") or {}).get("estimate_mb"),
                              # OpenJutsu Studio additions (engine contract 2.1): carried as the engine published them; absent on older engines
                              **{k: _plain(p[k]) for k in ("modes", "limits", "reference_policy", "subject_selection", "operation_status") if p.get(k) not in (None, "", [], {})}}},
    })


class SarnessAdapter(SarnessMediaMixin, Adapter):
    manifest = Manifest(
        id="sarness", name="Sarness Engine", model_load=False, model_unload=True, system_telemetry=True, media=True,
        exclusive_chat_model=True, worker_stop=True,
        reports_usage_tokens=True, reports_timings=True,
        model_metadata=["engine_name", "context", "context_trained", "default_ctx", "size", "vram_estimate_mb",
                        "quantization", "format", "architecture", "layers", "license", "description", "runtime",
                        "source_repo", "reasoning"],
        notes="One chat model is loaded at a time for every client, and it loads when someone first uses it. Loading "
              "one ahead of time is not offered: the engine switches models even mid-reply and has no way to reserve "
              "it. Image and video models load when a job needs them and can be freed afterwards. Install of new "
              "models is done in Sarness itself.")

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

    async def _json(self, c: httpx.AsyncClient, path: str):
        """GET a JSON document; None when the engine does not offer it (older engines) or it is unreadable."""
        try:
            r = await c.get(self.base_url + path, headers=_h(self.api_key))
        except httpx.HTTPError:
            return None
        if r.status_code in (401, 403):
            raise AdapterError("Engine rejected the API token.", status=502, kind="auth")
        if r.status_code != 200:
            return None
        try:
            return r.json()
        except ValueError:
            return None

    async def discover(self) -> list[DiscoveredModel]:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(self.base_url + "/v1/agent/models", headers=_h(self.api_key))
                if r.status_code in (401, 403):
                    raise AdapterError("Engine rejected the API token.", status=502, kind="auth")
                r.raise_for_status()
                caps = await self._json(c, "/v1/capabilities") or {}
                inv = await self._json(c, "/v1/models") or []
                catalog = await self._json(c, "/v1/models/catalog") or []
                llm = caps.get("llm") or await self._json(c, "/v1/llm/status") or {}
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"Could not list models: {_short(e)}") from e
        by_cap = {m.get("id"): m for m in caps.get("models") or [] if isinstance(m, dict)}
        by_inv = {m.get("id"): m for m in inv if isinstance(m, dict)} if isinstance(inv, list) else {}
        repos = _repos(catalog)
        loaded_llm = llm.get("model_id") if llm.get("state") in ("ready", "loading") else None
        out = []
        for a in r.json():
            ref, local = a["ref"], bool(a.get("local"))
            mid = ref.split(":", 1)[1] if ":" in ref else ref
            cm, im = (by_cap.get(mid) or {}, by_inv.get(mid) or {}) if local else ({}, {})
            vision = a.get("vision") or (im.get("capabilities") or {}).get("vision") or "vision" in (cm.get("kinds") or [])
            caps_list = ["chat"] + (["vision"] if vision else []) + (["tools"] if a.get("tools") else [])
            if local:
                meta = llm_meta(a, cm, im, repos.get(mid) or repos.get(im.get("catalog_id")))
                loaded = (mid == loaded_llm) if (cm or llm) else None
            else:
                meta = _clean({"engine_name": a.get("name"), "context": a.get("context_max"), "endpoint": a.get("provider")})
                loaded = None
            external = bool(self.options.get("external")) or (not local and not is_local_endpoint(ref, self.options))
            out.append(DiscoveredModel(ref, caps_list, meta, external=external, loaded=loaded))
        worker_loaded = {m["id"] for m in by_cap.values() if m.get("loaded") and m.get("category") in ("image", "video")}
        ids = [i for i, m in by_cap.items() if m.get("category") in ("image", "video")] if by_cap else \
              [i for i, m in by_inv.items() if m.get("category") in ("image", "video")]
        for mid in ids:
            cm, im = by_cap.get(mid) or {}, by_inv.get(mid) or {}
            if not (cm.get("supported", im.get("supported", True))):
                continue
            kind = cm.get("category") or im.get("category")
            out.append(DiscoveredModel(f"{kind}:{mid}", [kind],
                                       media_meta(cm, im, repos.get(mid) or repos.get(im.get("catalog_id"))),
                                       loaded=mid in worker_loaded if by_cap else bool(im.get("loaded"))))
        # Composite profiles (OpenJutsu ...) from the engine's canonical registry; absent on engines that predate it
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                reg = await self._json(c, "/v1/registry") or {}
        except httpx.HTTPError:
            reg = {}
        # The registry may state an image model's reference limit when /v1/models does not
        reg_caps = {m.get("id"): m.get("capabilities") or {} for m in reg.get("models") or [] if isinstance(m, dict)}
        for dm in out:
            media = (dm.meta or {}).get("media") if isinstance(dm.meta, dict) else None
            mid = dm.real_id.split(":", 1)[1] if ":" in dm.real_id else dm.real_id
            rc = reg_caps.get(mid) or {}
            if media and media.get("kind") == "image" and "references" not in media and isinstance(rc.get("references"), int) \
                    and not isinstance(rc.get("references"), bool) and rc["references"] > 0:
                media["references"] = int(rc["references"])
        for pr in reg.get("profiles") or []:
            if isinstance(pr, dict) and pr.get("id"):
                kind = pr.get("category") or "video"
                out.append(DiscoveredModel(f"{kind}:{pr['id']}", [kind], profile_meta(pr),
                                           loaded=(pr.get("residency") or {}).get("state") == "loaded"))
        return out

    async def status(self) -> dict | None:
        """Live state. Memory in MB; times are engine epoch seconds. Server paths and other clients' prompts never
        appear here."""
        async with httpx.AsyncClient(timeout=8) as c:
            caps = await self._json(c, "/v1/capabilities")
            llm = await self._json(c, "/v1/llm/status") or {}
            system = await self._json(c, "/v1/system") or {}
        if caps is None and not llm:
            return None
        caps = caps or {}
        worker = ((system.get("runtime") or {}).get("worker") or {})
        loaded_media = [m["id"] for m in caps.get("models") or [] if m.get("loaded") and m.get("category") in ("image", "video")]
        mem, gpu, jobs = caps.get("memory") or {}, caps.get("gpu") or {}, caps.get("jobs") or {}
        return {
            "engine": _clean({"version": caps.get("engine_version"), "api_version": caps.get("api_version"),
                              "hostname": caps.get("hostname")}),
            "memory": _clean({"total_mb": mem.get("total_mb") or gpu.get("memory_total_mb"),
                              "used_mb": mem.get("used_mb"),
                              "available_mb": mem.get("available_mb") or gpu.get("memory_free_mb"),
                              "unified": gpu.get("unified_memory"), "gpu": gpu.get("name"),
                              "gpu_utilisation_pct": gpu.get("utilization_pct")}),
            "llm": {"state": llm.get("state") or (caps.get("llm") or {}).get("state"),
                    "model_id": llm.get("model_id"), "active_streams": int(llm.get("active_streams") or 0),
                    "ctx": llm.get("ctx") if llm.get("model_id") else None, "loaded_at": llm.get("loaded_at"),
                    "last_used": llm.get("last_used"), "vram_estimate_mb": llm.get("vram_estimate_mb"),
                    "error": clean_error(llm["error"]) if llm.get("error") else None},
            "worker": {"alive": worker.get("alive"), "busy": worker.get("busy"), "loaded": loaded_media},
            "jobs": {"running": jobs.get("running"), "queued": jobs.get("queued"),
                     "concurrency": jobs.get("concurrency"), "busy_reason": jobs.get("busy_reason")},
            "health": caps.get("health"),
        }

    async def engine_jobs(self) -> list[dict] | None:
        out: list[dict] = []
        async with httpx.AsyncClient(timeout=8) as c:
            for st in ("running", "queued"):
                rows = await self._json(c, f"/v1/jobs?status={st}&limit=200")
                if rows is None:
                    return None
                for j in rows if isinstance(rows, list) else []:
                    simple = j.get("simple") or {}
                    out.append(_clean({"id": j.get("id"), "kind": j.get("kind"), "status": j.get("status"),
                                       "model": simple.get("model"),
                                       "progress": j.get("progress") if isinstance(j.get("progress"), (int, float)) else None,
                                       "queue_position": j.get("queue_position"),
                                       "waiting_reason": clean_error(j["waiting_reason"]) if j.get("waiting_reason") else None,
                                       "created_at": j.get("created_at"), "started_at": j.get("started_at")}))
        return out

    async def registry(self) -> dict | None:
        """GET /v1/registry, reduced to the fields an agent needs; absent on engines that predate it. No server paths."""
        async with httpx.AsyncClient(timeout=10) as c:
            reg = await self._json(c, "/v1/registry")
        if not isinstance(reg, dict):
            return None

        def entry(m: dict) -> dict:
            keep = {k: _plain(m.get(k)) for k in ("name", "category", "family", "kinds", "installed", "managed", "official", "notes",
                                                  "residency", "readiness", "workload", "memory", "capabilities", "inputs_schema",
                                                  "modes", "limits", "reference_policy", "subject_selection", "operation_status")
                    if m.get(k) not in (None, "", [], {})}
            return keep
        out: dict = {"models": {}, "profiles": {}, "external": {}}
        for key in out:
            for m in reg.get(key) or []:
                if isinstance(m, dict) and isinstance(m.get("id"), str):
                    out[key][m["id"]] = entry(m)
        return out

    async def resources(self) -> dict | None:
        """GET /v1/resources: the shared memory ledger. Light (no hardware audit)."""
        async with httpx.AsyncClient(timeout=10) as c:
            r = await self._json(c, "/v1/resources")
        if not isinstance(r, dict):
            return None
        led = r.get("ledger") or {}
        mem = led.get("memory") or {}
        eng = r.get("this_engine") or {}
        return {"enabled": bool(r.get("enabled")), "error": _plain(r.get("error")),
                "memory_mb": {k: mem.get(k) for k in ("total_mb", "available_mb", "usable_mb", "headroom_mb", "system_reserve_mb")
                              if isinstance(mem.get(k), (int, float))},
                "free_mb": led.get("free_mb") if isinstance(led.get("free_mb"), (int, float)) else None,
                "unconsumed_mb": led.get("unconsumed_mb") if isinstance(led.get("unconsumed_mb"), (int, float)) else None,
                "holders": _plain(led.get("holders") or [])[:30],
                "llm": _plain(eng.get("llm") or {}), "gpu_worker": _plain(eng.get("gpu_worker") or {})}

    async def chat_stream(self, model, messages, params):
        allowed = ("temperature", "top_p", "top_k", "min_p", "repeat_penalty", "presence_penalty",
                   "frequency_penalty", "max_tokens", "seed", "ctx")  # ctx: context window the engine loads the model with
        body = {"model_ref": model, "messages": messages,
                "params": {k: params[k] for k in allowed if params.get(k) is not None}}
        if isinstance(params.get("thinking"), bool):
            body["params"]["thinking"] = params["thinking"]  # reasoning on/off; the engine ignores it for models without it
        finish = usage = timings = trinodal = None
        keep_trinodal = is_local_endpoint(model, self.options)  # only an endpoint declared local may add run metadata
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
                                if keep_trinodal:
                                    trinodal = clean_trinodal(u.get("trinodal"))
                            tm = ev.get("timings")
                            if tm:
                                timings = {"prompt_per_second": tm.get("prompt_per_second"),
                                           "predicted_per_second": tm.get("predicted_per_second")}
        except AdapterError:
            raise
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}") from e
        done = {"type": "done", "finish_reason": finish, "usage": usage, "timings": timings}
        if trinodal:
            done["trinodal"] = trinodal
        yield done

    async def system_info(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                r = await c.get(self.base_url + "/v1/system", headers=_h(self.api_key))
                r.raise_for_status()
                return r.json()
        except httpx.HTTPError:
            return None

    async def load(self, model: str) -> None:
        raise AdapterError(PRELOAD_OFF, status=501, kind="unsupported")

    async def chat_slot(self) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                llm = await self._json(c, "/v1/llm/status")
        except httpx.HTTPError:
            return None
        if not llm:
            return None
        return {"state": llm.get("state"), "model_id": llm.get("model_id"), "ctx": llm.get("ctx"),
                "active_streams": int(llm.get("active_streams") or 0)}

    async def stop_worker(self) -> None:
        await self._post("/v1/worker/stop", None, timeout=120)

    async def unload(self, model: str) -> None:
        if model.startswith("endpoint:"):
            raise AdapterError("Models on a remote endpoint are not loaded by this engine.", status=501, kind="unsupported")
        await self._post(f"/v1/models/{quote(model.split(':', 1)[-1], safe='@')}/unload", None)

    async def _post(self, path, body, timeout=60):
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as c:
                r = await c.post(self.base_url + path, json=body, headers=_h(self.api_key))
        except httpx.HTTPError as e:
            raise AdapterError(f"Backend unreachable: {_short(e)}", kind="unreachable") from e
        if r.status_code in (401, 403):
            raise AdapterError("Engine rejected the API token.", kind="auth")
        if r.status_code >= 400:
            try:
                d = r.json().get("detail", r.text)
            except ValueError:
                d = r.text
            raise AdapterError(f"Sarness: {clean_error(d)}", status=409 if r.status_code == 409 else 502,
                               kind="busy" if r.status_code == 409 else "backend_error")
