"""Work Mode engine tools: models, resources, images, video and jobs (docs/WORK_MODE.md section 9.8).

Everything here runs on the HOST, through Semechka's own engine adapter and the SAME media machinery the Image and Video modes use
(`media_svc.admit` for limits and daily quotas, `media_svc.submit/submit_profile/refresh/cancel` for the job, `MediaJob` and
`MediaAsset` for the result, retention and the Library). The container has no route to the engine and never loads a model.

A person's rules are applied on every call, from the person's CURRENT permissions: a model they may not use is not listed and
cannot be called (it answers exactly like a model that does not exist). A generation tool returns success only after the
engine job completed AND the result file is in the workspace and listed as an artifact. The engine's own words (memory, not
ready, not allowed) go back to the agent unchanged.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import hashlib
import json
import re
import time
from collections import Counter
from types import SimpleNamespace

from sqlalchemy import select

from . import agent_svc, catalog_svc, gateway_svc, media_svc, services, work_svc
from .adapters import AdapterError
from .agent_svc import ToolError, ToolOut
from .backends_svc import adapter_for
from .media_models import MediaAsset, MediaJob
from .models import Backend, Model, User
from .models_agents import Agent
from .models_files import Job
from .models_tenancy import Org, Workspace
from .models_work import WorkEvent, WorkSession
from .work_tools import Wk, a_int, a_str, guess_mime, norm_path, put, register_artifact, scrub, work_tool

POLL_S = 2.0              # how often a waiting tool asks the engine (tests make it small)
CACHE_S = 5.0             # engine registry and resources are read at most this often
MAX_SOURCE_BYTES = 40 * 1024 * 1024
KINDS = ("chat", "image", "video")


# ---- the bridge to the run's event loop ------------------------------------------------------------------------------
def bridge(coro, timeout: float):
    """Run an engine coroutine from a tool's worker thread. When the run's loop is known the coroutine runs ON it (the scheduler and the
    media block's locks belong to that loop); otherwise it gets a loop of its own. Engine errors become the agent's error text."""
    loop = agent_svc.RUN_LOOP.get()
    try:
        if loop is not None and loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(coro, loop)
            try:
                return fut.result(timeout)
            except concurrent.futures.TimeoutError:
                fut.cancel()
                raise ToolError(f"The engine did not answer within {int(timeout)} seconds.") from None
        return asyncio.run(asyncio.wait_for(coro, timeout))
    except AdapterError as e:
        raise ToolError(scrub(str(e))) from None
    except asyncio.TimeoutError:
        raise ToolError(f"The engine did not answer within {int(timeout)} seconds.") from None


# ---- who is asking ----------------------------------------------------------------------------------------------------
@contextlib.contextmanager
def scope(w: Wk):
    """(session, principal, org, internal workspace, work session) from the person's CURRENT state, in a short read transaction."""
    with w.st.db.session() as s:
        user = s.get(User, w.user_id)
        sess = s.get(WorkSession, w.sid)
        if user is None or user.status != "active" or sess is None or sess.deleted_at:
            raise ToolError("The session or its owner is no longer available.")
        principal = services.load_principal(s, user)
        agent = s.get(Agent, sess.agent_id)
        yield s, principal, s.get(Org, sess.org_id), s.get(Workspace, agent.workspace_id) if agent else None, sess


def models_of(st, s, principal, org, ws, kind: str) -> list[tuple[Model, Backend]]:
    """The models this person may use for `kind` ('chat', 'image', 'video'), in the administrator's order. The same answer the
    Image and Video modes give, narrowed by the organisation's allow-list."""
    mode = "work" if kind == "chat" else kind
    if kind in ("image", "video") and not services.mode_state(s, principal, st, kind)["enabled"]:
        return []
    allowed = {m["id"] for m in gateway_svc.models_for(st, s, principal, org, ws, "chat" if kind == "chat" else kind)}
    return [(m, b) for m, b in services.accessible_models(s, principal, st, mode, include_hidden=False) if m.id in allowed]


def find_model(rows: list[tuple[Model, Backend]], ref: str) -> tuple[Model, Backend] | None:
    ref = (ref or "").strip()
    low = ref.lower()
    for m, b in rows:
        if m.id == ref:
            return m, b
    for m, b in rows:
        if (m.alias or "").lower() == low or m.real_id.lower() == low or m.real_id.split(":", 1)[-1].lower() == low:
            return m, b
    return None


def kind_of(m: Model) -> str:
    return "image" if m.real_id.startswith("image:") else "video" if m.real_id.startswith("video:") else "chat"


def engine_id(m: Model) -> str:
    return m.real_id.split(":", 1)[-1]


# ---- engine registry and resources (cached, light) ---------------------------------------------------------------------
def engine_info(st, b: Backend, *, fresh: bool = False) -> dict:
    """{"registry": {...}|None, "resources": {...}|None, "jobs": [...]|None, "at": epoch}: read together, at most every CACHE_S seconds
    per backend. None parts mean the engine does not report them (older engines)."""
    cache = st.__dict__.setdefault("work_engine_cache", {})
    hit = cache.get(b.id)
    if hit and not fresh and time.time() - hit["at"] < CACHE_S:
        return hit
    ad = adapter_for(b, st)

    async def read():
        async def one(c):
            try:
                return await c
            except Exception:  # noqa: BLE001  an engine that cannot answer one question must not hide the others
                return None
        return await asyncio.gather(one(ad.registry()), one(ad.resources()), one(ad.engine_jobs()))
    try:
        reg, res, jobs = bridge(read(), 25)
    except ToolError:
        reg = res = jobs = None
    out = {"registry": reg, "resources": res, "jobs": jobs, "at": time.time()}
    cache[b.id] = out
    return out


def live_entry(info: dict, m: Model) -> dict | None:
    reg = info.get("registry") or {}
    eid = engine_id(m)
    for part in ("models", "profiles", "external"):
        if eid in (reg.get(part) or {}):
            return reg[part][eid]
    return None


def readiness(m: Model, b: Backend, entry: dict | None) -> tuple[bool, str, str | None]:
    """(ready, state, reason) from the engine's registry when it has one, else from the last model sync."""
    if entry and isinstance(entry.get("readiness"), dict):
        r = entry["readiness"]
        return bool(r.get("ready")), str(r.get("state") or ("ready" if r.get("ready") else "not ready")), r.get("reason")
    ok = bool(m.available and b.enabled)
    return ok, "ready" if ok else "not available", None


def _schema_brief(schema: dict) -> dict:
    keep = ("type", "kind", "required", "values", "min", "max", "default", "max_files", "multi", "label", "description", "max_length")
    return {k: {a: v[a] for a in keep if a in v} for k, v in (schema or {}).items() if isinstance(v, dict)}


def model_row(st, m: Model, b: Backend, info: dict, current: str | None) -> dict:
    kind = kind_of(m)
    entry = live_entry(info, m)
    ready, state, reason = readiness(m, b, entry)
    meta = m.meta or {}
    mi = media_svc.media_info(m)
    caps = (entry or {}).get("capabilities") or {}
    residency = (entry or {}).get("residency") or {}
    loaded = (residency.get("state") == "loaded") if residency else meta.get("loaded")
    row = {"id": m.id, "alias": m.alias, "kind": kind, "ready": ready, "state": state, "loaded": bool(loaded) if loaded is not None else None}
    if reason:
        row["reason"] = reason
    est = ((entry or {}).get("memory") or {}).get("estimate_mb") or meta.get("vram_estimate_mb")
    if est:
        row["memory_mb"] = est
    if kind == "chat":
        row["vision"] = "vision" in catalog_svc.capabilities(m)
        ctx = meta.get("context")
        if ctx:
            row["context"] = ctx
        row["current"] = m.id == current
    else:
        if mi.get("modes"):
            row["modes"] = mi["modes"]
        prof = media_svc.profile_info(m)
        if prof:
            row["profile"] = {"id": prof.get("id"), "operations": prof.get("operations") or caps.get("operations") or [],
                              "inputs_schema": _schema_brief(prof.get("inputs_schema") or (entry or {}).get("inputs_schema") or {})}
        else:
            lim, dfl = mi.get("limits") or {}, mi.get("defaults") or {}
            row["defaults"], row["limits"] = dfl, {k: v for k, v in lim.items() if k in ("steps", "min_side", "max_side", "multiple", "frames", "fps", "resolutions", "frame_step")}
            if mi.get("negative_prompt"):
                row["negative_prompt"] = True
    if meta.get("description"):
        row["about"] = str(meta["description"])[:160]
    if services.is_external(m, b):
        row["external"] = True
    return row


@work_tool("list_models", "Models YOU may use, with readiness, loaded state, memory and capabilities (profiles show their inputs). kind: chat, image or video.",
           '{"kind": "image"}')
def t_list_models(tc, w: Wk, a: dict) -> ToolOut:
    kind = a_str(a, "kind", required=False, maxlen=20).strip().lower()
    if kind and kind not in KINDS:
        raise ToolError(f"kind must be one of: {', '.join(KINDS)}.")
    kinds = [kind] if kind else list(KINDS)
    rows: list[dict] = []
    with scope(w) as (s, p, org, ws, sess):
        cur = sess.model_id
        found = {k: models_of(w.st, s, p, org, ws, k) for k in kinds}
        for k in kinds:
            infos: dict[str, dict] = {}
            for m, b in found[k]:
                if b.id not in infos:
                    infos[b.id] = engine_info(w.st, b) if b.adapter == "sarness" else {}
                rows.append(model_row(w.st, m, b, infos[b.id], cur))
    if not rows:
        return ToolOut("No " + (kind or "") + " models are available to you." if kind else "No models are available to you.")
    lines = [f"{len(rows)} model(s) you may use (ready=false means the engine will refuse it now; loaded=true starts fastest):"]
    lines += [json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in rows]
    return ToolOut(scrub("\n".join(lines)))


# ---- resources ----------------------------------------------------------------------------------------------------------
def _gib(mb) -> str:
    return f"{mb / 1024:.1f} GiB" if isinstance(mb, (int, float)) else "unknown"


@work_tool("resources", "Free memory, loaded models, active engine jobs and your generation limits. Check before a heavy job.", "{}")
def t_resources(tc, w: Wk, a: dict) -> ToolOut:
    lines: list[str] = []
    with scope(w) as (s, p, org, ws, sess):
        backends: dict[str, Backend] = {}
        for k in KINDS:
            for m, b in models_of(w.st, s, p, org, ws, k):
                if b.adapter == "sarness":
                    backends[b.id] = b
        val = lambda key: w.st.settings.value_for(s, key, p)  # noqa: E731
        mine = {"active": media_svc.count_active(s, w.user_id), "concurrent": val("media.max_concurrent_jobs"),
                "today": media_svc.generations_today(s, w.user_id), "daily": val("media.daily_generations") or None,
                "stored": media_svc.count_assets(s, w.user_id), "max_assets": val("media.max_assets")}
        own_remote = {r for (r,) in s.execute(select(MediaJob.remote_id).where(MediaJob.user_id == w.user_id, MediaJob.status.in_(media_svc.ACTIVE)))}
    for b in backends.values():
        info = engine_info(w.st, b)
        res, reg = info.get("resources"), info.get("registry")
        lines.append(f"Engine '{b.name}' (read {int(time.time() - info['at'])} s ago, cached for at most {int(CACHE_S)} s):")
        if res is None:
            lines.append("  The engine does not report resources.")
        else:
            mem = res.get("memory_mb") or {}
            lines.append(f"  Free unified memory: {_gib(res.get('free_mb'))} of {_gib(mem.get('usable_mb') or mem.get('total_mb'))} usable "
                         f"(total {_gib(mem.get('total_mb'))}, kept in reserve {_gib(mem.get('headroom_mb'))}).")
            if not res.get("enabled", True):
                lines.append("  The engine's memory ledger is switched off: free memory is not tracked.")
            llm, gpu = res.get("llm") or {}, res.get("gpu_worker") or {}
            lines.append(f"  Chat model: {llm.get('state') or 'unknown'}" + (f" ({llm['model_id']})" if llm.get("model_id") else "")
                         + (", replying now" if llm.get("busy") else ""))
            lines.append(f"  Picture/video worker: {'running' if gpu.get('alive') else 'stopped'}" + (", busy" if gpu.get("busy") else "")
                         + (f", loaded: {', '.join(map(str, gpu['loaded']))}" if gpu.get("loaded") else ""))
            holders = res.get("holders") or []
            lines.append("  Memory held by: " + (json.dumps(holders, separators=(",", ":"))[:600] if holders else "nothing"))
        if reg:
            loaded = [f"{i} ({e.get('category', '?')})" for part in ("models", "profiles", "external") for i, e in (reg.get(part) or {}).items()
                      if (e.get("residency") or {}).get("state") == "loaded"]
            lines.append("  Loaded models: " + (", ".join(loaded) if loaded else "none"))
        jobs = info.get("jobs")
        if jobs is None:
            lines.append("  Engine jobs: not reported.")
        else:
            mine_j = [j for j in jobs if j.get("id") in own_remote]
            others = [j for j in jobs if j.get("id") not in own_remote]
            lines.append("  Your engine jobs: " + (", ".join(
                f"{j.get('kind')} {j.get('status')}" + (f" {j['model']}" if j.get("model") else "")
                + (f" {int(j['progress'] * 100)}%" if isinstance(j.get("progress"), (int, float)) else "")
                + (f" waiting: {j['waiting_reason']}" if j.get("waiting_reason") else "") for j in mine_j[:12]) if mine_j else "none"))
            if others:  # other people's jobs are counted, not described
                lines.append("  Other jobs on the engine: " + ", ".join(
                    f"{n} {k} {st_}" for (k, st_), n in sorted(Counter((j.get("kind") or "?", j.get("status") or "?") for j in others).items())))
    if not backends:
        lines.append("No engine reports resources for your models.")
    lines.append(f"Your generation limits: {mine['active']} running of {mine['concurrent']} at once; {mine['today']} started in the last 24 h"
                 + (f" of {mine['daily']} allowed" if mine["daily"] else " (no daily limit)")
                 + f"; {mine['stored']} items in your Library" + (f" of {mine['max_assets']}" if mine["max_assets"] else "") + ".")
    return ToolOut(scrub("\n".join(lines)))


# ---- jobs ---------------------------------------------------------------------------------------------------------------
def snapshot(st, job_id: str) -> dict | None:
    with st.db.session() as s:
        j = s.get(MediaJob, job_id)
        if j is None:
            return None
        return {"id": j.id, "user_id": j.user_id, "kind": j.kind, "model_id": j.model_id, "status": j.status, "progress": j.progress,
                "text": j.progress_text or "", "error": j.error, "queue_position": j.queue_position, "save": j.save,
                "started_at": j.started_at, "created_at": j.created_at, "finished_at": j.finished_at}


def cancel_requested(w: Wk) -> bool:
    if not w.job_id:
        return False
    with w.st.db.session() as s:
        j = s.get(Job, w.job_id)
        return bool(j and j.cancel_requested)


def job_event(w: Wk, snap: dict, alias: str, **extra) -> dict:
    """Emit one `job` event (the UI folds events with one job_id into one card)."""
    prog = snap["progress"] if snap["status"] in ("queued", "running") else (1.0 if snap["status"] == "completed" else None)
    data = {"job_id": snap["id"], "job_kind": snap["kind"], "model": alias, "status": snap["status"], "phase": snap["text"] or None,
            "progress": round(prog, 3) if isinstance(prog, (int, float)) else None, **extra}
    if snap.get("queue_position") is not None and snap["status"] == "queued":
        data["queue_position"] = snap["queue_position"]
    if snap.get("error"):
        data["error"] = snap["error"]
    data = {k: v for k, v in data.items() if v is not None or k == "progress"}
    with w.st.db.session(write=True) as s:
        sess = s.get(WorkSession, w.sid)
        if sess is not None:
            pct = f" {int(prog * 100)}%" if isinstance(prog, (int, float)) and snap["status"] == "running" else ""
            work_svc.emit(w.st, s, sess, "job", f"{snap['kind']} job {snap['status']}{pct}", data, run_id=w.run_id, step_n=w.step_n)
    return data


def last_job_event(st, sid: str, job_id: str) -> dict | None:
    """The newest `job` event of this session for a job (None when the job did not start in this session)."""
    with st.db.session() as s:
        sess = s.get(WorkSession, sid)
        rows = s.execute(select(WorkEvent).where(WorkEvent.session_id == sid, WorkEvent.kind == "job")
                         .order_by(WorkEvent.id.desc()).limit(300)).scalars().all()
        for ev in rows:
            try:
                d = json.loads(work_svc.dec(st, sess.org_id, ev.payload_enc)) if ev.payload_enc else {}
            except ValueError:
                continue
            if d.get("job_id") == job_id:
                return d
    return None


def own_job(w: Wk, job_id: str) -> tuple[dict, dict]:
    """(snapshot, last event data) of a job that THIS session started for THIS person; anything else is 'no such job'."""
    job_id = (job_id or "").strip()
    ev = last_job_event(w.st, w.sid, job_id) if job_id else None
    snap = snapshot(w.st, job_id) if ev else None
    if not ev or not snap or snap["user_id"] != w.user_id:
        raise ToolError(f"No generation job '{job_id[:40]}' was started in this session.")
    return snap, ev


def _alias(st, model_id: str) -> str:
    with st.db.session() as s:
        m = s.get(Model, model_id)
        return m.alias if m else model_id


def refresh_job(st, job_id: str) -> None:
    """Advance one job through the media block. The block reads in one transaction and then writes in it, which SQLite refuses at once (not after
    waiting) when another writer holds the lock at that moment; that is not a failure of the job, so ask again a few times."""
    from sqlalchemy.exc import OperationalError
    for attempt in range(6):
        try:
            bridge(media_svc.refresh(st, job_id), 90)
            return
        except OperationalError as e:
            if "locked" not in str(e) or attempt == 5:
                raise
            time.sleep(0.2 * (attempt + 1))


def wait_for(w: Wk, job_id: str, timeout_s: float, alias: str, extra: dict) -> dict:
    """Advance the job through the media block until it ends or `timeout_s` passes; one `job` event per real change. -> snapshot."""
    deadline = time.monotonic() + max(0.0, timeout_s)
    last_key, last_t = None, 0.0
    while True:
        refresh_job(w.st, job_id)
        snap = snapshot(w.st, job_id)
        if snap is None:
            raise ToolError("The job record is gone.")
        prog = snap["progress"] if isinstance(snap["progress"], (int, float)) else None
        key = (snap["status"], snap["text"], None if prog is None else int(prog * 50), snap["queue_position"])
        if key != last_key and (snap["status"] not in ("queued", "running") or last_key is None or key[:2] != last_key[:2] or time.monotonic() - last_t >= 1.0):
            job_event(w, snap, alias, **extra)
            last_key, last_t = key, time.monotonic()
        if snap["status"] not in media_svc.ACTIVE:
            return snap
        if cancel_requested(w):
            with contextlib.suppress(ToolError):
                bridge(media_svc.cancel(w.st, job_id), 60)
            snap = snapshot(w.st, job_id) or snap
            job_event(w, snap, alias, **extra)
            raise ToolError(f"Cancelled: the engine job {job_id} was cancelled because the run was cancelled.")
        left = deadline - time.monotonic()
        if left <= 0:
            return snap
        time.sleep(min(POLL_S, left))


def result_file(st, job_id: str, user_id: str) -> tuple[bytes, str, dict, str | None] | None:
    """(bytes, mime, meta, library asset id) of the finished job's first result: the Library copy, or the held no-save copy."""
    with st.db.session() as s:
        a = s.execute(select(MediaAsset).where(MediaAsset.job_id == job_id, MediaAsset.user_id == user_id)
                      .order_by(MediaAsset.created_at)).scalars().first()
        if a is not None:
            return media_svc.read_asset(st, a), a.mime, {"width": a.width, "height": a.height, "duration": a.duration, "seed": a.seed}, a.id
    media_svc.purge_mem(st)
    for v in media_svc._mem(st).values():
        if v["job_id"] == job_id and v["user_id"] == user_id:
            return v["data"], v["mime"], {k: v["meta"].get(k) for k in ("width", "height", "duration", "seed")}, None
    return None


def deliver(w: Wk, snap: dict, alias: str, out: str | None, req: str | None = None) -> ToolOut:
    """The job completed: put its file in the workspace, verify it, list it as an artifact. Success is reported only if all of that held."""
    got = result_file(w.st, snap["id"], w.user_id)
    if got is None:
        raise ToolError(f"The engine job {snap['id']} completed but its result file is not available (it may have been deleted from the "
                        "Library, or expired). Nothing was written to the workspace.")
    data, mime, meta, asset_id = got
    ext = media_svc.SAFE_MIME.get(mime, "bin")
    rel = norm_path(out, allow_empty=False) if out else f"artifacts/{snap['kind']}-{snap['id'][-8:]}.{ext}"
    if not rel.rsplit("/", 1)[-1].lower().endswith("." + ext) and "." not in rel.rsplit("/", 1)[-1]:
        rel += "." + ext
    name = w.container()
    head = put(w, name, rel, data)
    info = w.rt.stat(name, rel)
    if not info.get("exists") or info.get("size") != len(data):
        raise ToolError(f"The result could not be written to /work/{rel} (size mismatch). The engine job {snap['id']} is complete and its "
                        "result is in the Library.")
    try:
        art, _changed = register_artifact(w, name, rel)
    except ToolError as e:
        raise ToolError(f"The file was written to /work/{rel} but is not listed as an artifact: {e}") from None
    w.done(path=rel, bytes=len(data), files=[rel], job=snap["id"])
    if asset_id:  # the Library already holds this result; mark the artifact so the screen does not offer a second copy
        with w.st.db.session(write=True) as s:
            from .models_work import WorkArtifact
            row = s.get(WorkArtifact, art["id"])
            if row is not None:
                row.saved_to_library, row.library_asset_id = True, asset_id
    facts = ", ".join(f"{k} {meta[k]}" for k in ("width", "height", "duration", "seed") if meta.get(k) is not None)
    job_event(w, snap, alias, artifact_id=art["id"], out=rel, **({"req": req} if req else {}))
    elapsed = round((snap["finished_at"] or time.time()) - (snap["started_at"] or snap["created_at"]), 1)
    return ToolOut(f"Done: {snap['kind']} job {snap['id']} completed in {elapsed}s with {alias}. Wrote /work/{rel} "
                   f"({len(data):,} bytes, {mime}, sha256 {head['sha256'][:12]}{', ' + facts if facts else ''}) and listed it as artifact {art['id']}."
                   + (" A copy is in your Library." if asset_id else ""),
                   artifacts=[{"file_id": art["id"], "name": art["name"], "version": 1}])


def outcome(w: Wk, snap: dict, alias: str, out: str | None, req: str | None = None) -> ToolOut:
    st_ = snap["status"]
    if st_ == "completed":
        return deliver(w, snap, alias, out, req)
    if st_ == "failed":
        raise ToolError(f"The engine job {snap['id']} failed: {snap['error'] or 'the engine gave no reason'}")
    if st_ == "cancelled":
        raise ToolError(f"The engine job {snap['id']} was cancelled.")
    return ToolOut("")


def _still_running(snap: dict, waited: float) -> str:
    pct = f" at {int(snap['progress'] * 100)}%" if isinstance(snap["progress"], (int, float)) else ""
    qp = f" (place {snap['queue_position']} in the engine queue)" if snap["status"] == "queued" and snap.get("queue_position") else ""
    return (f"Job {snap['id']} is still {snap['status']}{pct}{qp} after {int(waited)}s: {snap['text'] or 'no detail from the engine'}. It keeps running. "
            f"Collect it with wait_job {{\"id\": \"{snap['id']}\"}}; nothing has been written to the workspace yet.")


# ---- generation ---------------------------------------------------------------------------------------------------------
def _live_check(w: Wk, m: Model, b: Backend) -> None:
    """The engine's own readiness, read now: a model it will refuse is refused here with its reason."""
    if b.adapter != "sarness":
        return
    entry = live_entry(engine_info(w.st, b), m)
    if entry:
        ok, state, reason = readiness(m, b, entry)
        if not ok:
            raise ToolError(f"{m.alias} is not ready: {reason or state}")


def _parse_size(v, default_w: int, default_h: int) -> tuple[int, int]:
    if v in (None, ""):
        return default_w, default_h
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v), int(v)
    m = re.fullmatch(r"\s*(\d{2,5})\s*[xX*]\s*(\d{2,5})\s*", str(v))
    if m:
        return int(m.group(1)), int(m.group(2))
    if str(v).strip().isdigit():
        return int(str(v).strip()), int(str(v).strip())
    raise ToolError("size must look like 512 or 768x512.")


def _read_source(w: Wk, name: str, rel: str, what: str, *, kinds: tuple[str, ...]) -> tuple[bytes, str]:
    info = w.rt.stat(name, rel)
    if not info.get("exists"):
        raise ToolError(f"{what}: no such file /work/{rel}")
    if info.get("type") != "file":
        raise ToolError(f"{what}: /work/{rel} is not a file.")
    if info["size"] == 0:
        raise ToolError(f"{what}: /work/{rel} is empty.")
    if info["size"] > MAX_SOURCE_BYTES * 5:
        raise ToolError(f"{what}: /work/{rel} is {info['size'] / 1048576:.0f} MB, which is too large to send to the engine.")
    mime = guess_mime(rel)
    if not mime.startswith(kinds):
        raise ToolError(f"{what}: /work/{rel} is {mime}; expected " + " or ".join(k.rstrip('/') for k in kinds) + ".")
    data, _ = w.rt.get_file(name, rel, limit=info["size"] + 1)
    return data, mime


def _launch(w: Wk, kind: str, m: Model, b: Backend, p, params: dict | None, profile: tuple | None, label: str) -> tuple[str, str]:
    """Admission, then submit through the media block. -> (job id, model alias)."""
    with w.st.db.session() as s:
        try:
            save = media_svc.admit(w.st, s, p, w.user_id, True, kind)
        except media_svc.AdmitError as e:
            raise ToolError(e.message) from None
        days = w.st.settings.value_for(s, "privacy.chat_retention_days", p)
    expires = time.time() + days * 86400 if days and days > 0 else None
    who = SimpleNamespace(id=w.user_id)
    if profile is not None:
        clean, uploads = profile
        jid = bridge(media_svc.submit_profile(w.st, who, m, b, clean, uploads, save, expires), 600)
    else:
        jid = bridge(media_svc.submit(w.st, who, kind, m, b, params, save, expires), 600)
    return jid, m.alias


def wait_cap(w: Wk) -> float:
    """How long a generation tool waits for the engine before it hands the still-running job back to the agent."""
    with w.st.db.session() as s:
        return float(w.st.settings.deployment(s, "work.engine_wait_s"))


def request_key(kind: str, model_id: str, params: dict, files: dict | None = None) -> str:
    """Names one generation request: the same model, the same settings and the same input files (by content)."""
    blob = json.dumps([kind, model_id, params, files or {}], sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def same_request(w: Wk, key: str) -> ToolOut | None:
    """A small model that does not know it is done calls the same generation again and again, each time spending a job, the person's daily
    quota and a Library slot. Within ONE run an identical request is not started a second time: the agent is pointed at what it already has
    (or at the job that is still running). A failed or cancelled earlier job, a deleted file, or a new run (a new message) all allow a fresh try."""
    with w.st.db.session() as s:
        sess = s.get(WorkSession, w.sid)
        rows = s.execute(select(WorkEvent).where(WorkEvent.session_id == w.sid, WorkEvent.kind == "job", WorkEvent.run_id == w.run_id)
                         .order_by(WorkEvent.id.desc()).limit(300)).scalars().all()
        seen: dict[str, dict] = {}
        for ev in rows:
            try:
                d = json.loads(work_svc.dec(w.st, sess.org_id, ev.payload_enc)) if ev.payload_enc else {}
            except ValueError:
                continue
            if d.get("req") == key and d.get("job_id") not in seen:
                seen[d["job_id"]] = d
    for jid, d in seen.items():
        if d.get("status") in ("queued", "running"):
            snap = snapshot(w.st, jid)
            if snap and snap["status"] in media_svc.ACTIVE:
                return ToolOut(f"Not started again: this exact request is already running as job {jid}. Collect it with wait_job {{\"id\": \"{jid}\"}}.")
        if d.get("status") == "completed" and d.get("out") and d.get("artifact_id"):
            info = w.rt.stat(w.container(), d["out"])
            if info.get("exists"):
                w.done(path=d["out"], files=[d["out"]], job=jid)
                return ToolOut(f"Not started again: this run already made exactly this (job {jid}) and /work/{d['out']} is in your workspace "
                               f"({info['size']:,} bytes). Use that file. For a different result change the prompt, the seed or the model.")
    return None


def _finish_tool(w: Wk, jid: str, alias: str, kind: str, out: str | None, prompt: str, req: str | None = None) -> ToolOut:
    cap = wait_cap(w)
    snap = snapshot(w.st, jid)
    extra = {"out": out, "req": req}
    extra = {k: v for k, v in extra.items() if v}
    job_event(w, snap, alias, prompt=prompt[:120], **extra)
    t0 = time.monotonic()
    snap = wait_for(w, jid, cap, alias, extra)
    res = outcome(w, snap, alias, out, req)
    if snap["status"] in media_svc.ACTIVE:
        return ToolOut(_still_running(snap, time.monotonic() - t0))
    return res


@work_tool("generate_image", "Make a picture and wait; returns once the PNG is in /work/artifacts/. size 512 or 768x512; source_file edits a picture.",
           '{"prompt": "<what to draw>", "model": "<image model>", "size": "512", "out": "artifacts/<name>.png"}')
def t_generate_image(tc, w: Wk, a: dict) -> ToolOut:
    prompt = a_str(a, "prompt", maxlen=media_svc.PROMPT_MAX).strip()
    model_ref = a_str(a, "model", required=False, maxlen=200).strip()
    out = norm_path(a_str(a, "out", required=False, maxlen=400), allow_empty=False) if a.get("out") else None
    src = norm_path(a_str(a, "source_file", required=False, maxlen=400), allow_empty=False) if a.get("source_file") else None
    with scope(w) as (s, p, org, ws, sess):
        rows = models_of(w.st, s, p, org, ws, "image")
        if not rows:
            raise ToolError("No image model is available to you.")
        if model_ref:
            hit = find_model(rows, model_ref)
            if hit is None:
                raise ToolError(f"'{model_ref[:60]}' is not an image model you may use. Call list_models with kind image.")
        else:
            infos = {b.id: engine_info(w.st, b) for _, b in rows if b.adapter == "sarness"}
            ready = [(m, b) for m, b in rows if readiness(m, b, live_entry(infos.get(b.id, {}), m))[0] and not media_svc.profile_info(m)]
            if not ready:
                raise ToolError("No image model you may use is ready right now. Call list_models with kind image for the reasons.")
            hit = next((x for x in ready if (live_entry(infos.get(x[1].id, {}), x[0]) or {}).get("residency", {}).get("state") == "loaded"), ready[0])
        m, b = hit
        if media_svc.profile_info(m):
            raise ToolError(f"{m.alias} is an editing profile; call generate_video with it.")
        vmax, size_default = w.st.settings.value_for(s, "media.video_max_seconds", p), w.st.settings.value_for(s, "media.default_image_size", p)
        dfl = media_svc.option_defaults("image", m, size_default, vmax)
        mi = media_svc.media_info(m)
    _live_check(w, m, b)
    width, height = _parse_size(a.get("size"), dfl.get("width", 512), dfl.get("height", 512))
    body = {"prompt": prompt, "width": width, "height": height, "steps": a.get("steps"), "seed": a.get("seed", -1),
            "guidance": a.get("guidance"), "negative_prompt": a.get("negative_prompt")}
    try:
        params = media_svc.validate_params("image", m, body, vmax)
    except media_svc.ParamError as e:
        raise ToolError(str(e)) from None
    name = w.container()
    data = mime = None
    if src:
        if not (set(mi.get("modes") or []) & {"img2img", "edit", "inpaint"}):
            raise ToolError(f"{m.alias} cannot edit a picture (modes: {', '.join(mi.get('modes') or []) or 'none'}). Pick a model with img2img or edit.")
        data, mime = _read_source(w, name, src, "source_file", kinds=("image/",))
        if len(data) > MAX_SOURCE_BYTES:
            raise ToolError("source_file is larger than 40 MB.")
        if isinstance(a.get("denoise"), (int, float)):
            params["denoise"] = float(a["denoise"])
    key = request_key("image", m.id, {**params, "out": out}, {"source": hashlib.sha256(data).hexdigest()} if data else None)
    again = same_request(w, key)
    if again is not None:
        return again
    if data is not None:
        params["reference_image"] = bridge(_upload(w, b, data, mime, src.rsplit("/", 1)[-1]), 180)
    with scope(w) as (s, p, org, ws, sess):
        jid, alias = _launch(w, "image", m, b, p, params, None, prompt)
    return _finish_tool(w, jid, alias, "image", out, prompt, key)


async def _upload(w: Wk, b: Backend, data: bytes, mime: str, fname: str) -> str:
    return await adapter_for(b, w.st).media_upload(data, mime, fname)


VIDEO_PLAIN_KEYS = ("width", "height", "frames", "fps", "steps", "guidance", "seed", "negative_prompt")


@work_tool("generate_video", "Make or edit video and wait; returns once the MP4 is in /work/artifacts/. Profiles (OpenJutsu): inputs as in list_models, files are /work paths.",
           '{"model": "<video model or profile>", "inputs": {"prompt": "<who is replaced>", "video": "<path>", "refs": "<path>"}, "out": "artifacts/<name>.mp4"}')
def t_generate_video(tc, w: Wk, a: dict) -> ToolOut:
    prompt = a_str(a, "prompt", required=False, maxlen=media_svc.PROMPT_MAX).strip()
    model_ref = (a_str(a, "model", required=False, maxlen=200) or a_str(a, "profile", required=False, maxlen=200)).strip()
    inputs = a.get("inputs") if isinstance(a.get("inputs"), dict) else {}
    if a.get("inputs") is not None and not isinstance(a.get("inputs"), dict):
        raise ToolError("inputs must be an object.")
    out = norm_path(a_str(a, "out", required=False, maxlen=400), allow_empty=False) if a.get("out") else None
    with scope(w) as (s, p, org, ws, sess):
        rows = models_of(w.st, s, p, org, ws, "video")
        if not rows:
            raise ToolError("No video model is available to you.")
        if not model_ref:
            raise ToolError("'model' is required: call list_models with kind video and pick a model or a profile.")
        hit = find_model(rows, model_ref)
        if hit is None:
            raise ToolError(f"'{model_ref[:60]}' is not a video model or profile you may use. Call list_models with kind video.")
        m, b = hit
        vmax = w.st.settings.value_for(s, "media.video_max_seconds", p)
        profile_mb = int(w.st.settings.value_for(s, "media.profile_upload_max_mb", p)) * 1048576
    _live_check(w, m, b)
    name = None
    if media_svc.profile_info(m):
        schema = (media_svc.profile_info(m) or {}).get("inputs_schema") or {}
        plain = {k: v for k, v in inputs.items() if not (isinstance(schema.get(k), dict) and schema[k].get("type") == "file")}
        if prompt and "prompt" in schema and not plain.get("prompt"):
            plain["prompt"] = prompt
        files: dict[str, list[tuple[str, int]]] = {}
        uploads: dict[str, list[tuple]] = {}
        for k, spec in schema.items():
            if not (isinstance(spec, dict) and spec.get("type") == "file"):
                continue
            v = inputs.get(k)
            paths = [v] if isinstance(v, str) and v.strip() else ([x for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else [])
            if not paths:
                continue
            name = name or w.container()
            for rel in paths:
                data, mime = _read_source(w, name, norm_path(rel, allow_empty=False), f"inputs.{k}", kinds=("video/", "image/"))
                files.setdefault(k, []).append((mime, len(data)))
                uploads.setdefault(k, []).append((data, mime, rel.rsplit("/", 1)[-1]))
        try:
            clean = media_svc.validate_profile_inputs(m, plain, files, profile_mb)
        except media_svc.ParamError as e:
            raise ToolError(str(e)) from None
        key = request_key("video", m.id, {**clean, "out": out}, {k: [hashlib.sha256(x[0]).hexdigest() for x in v] for k, v in uploads.items()})
        again = same_request(w, key)
        if again is not None:
            return again
        with scope(w) as (s, p, org, ws, sess):
            jid, alias = _launch(w, "video", m, b, p, None, (clean, uploads), str(clean.get("prompt", "")))
        return _finish_tool(w, jid, alias, "video", out, str(clean.get("prompt", "")), key)
    # a plain video model
    if not prompt:
        prompt = str(inputs.get("prompt") or "").strip()
    body = {"prompt": prompt, **{k: inputs[k] for k in VIDEO_PLAIN_KEYS if inputs.get(k) is not None}}
    with scope(w) as (s, p, org, ws, sess):
        dfl = media_svc.option_defaults("video", m, 0, vmax)
    for k in ("width", "height", "frames", "fps", "steps", "guidance"):
        if body.get(k) is None and dfl.get(k) is not None:
            body[k] = dfl[k]
    try:
        params = media_svc.validate_params("video", m, body, vmax)
    except media_svc.ParamError as e:
        raise ToolError(str(e)) from None
    start = None
    if inputs.get("start_image"):
        name = name or w.container()
        start = _read_source(w, name, norm_path(str(inputs["start_image"]), allow_empty=False), "inputs.start_image", kinds=("image/",))
    key = request_key("video", m.id, {**params, "out": out}, {"start": hashlib.sha256(start[0]).hexdigest()} if start else None)
    again = same_request(w, key)
    if again is not None:
        return again
    if start:
        params["start_image"] = bridge(_upload(w, b, start[0], start[1], str(inputs["start_image"]).rsplit("/", 1)[-1]), 180)
    with scope(w) as (s, p, org, ws, sess):
        jid, alias = _launch(w, "video", m, b, p, params, None, prompt)
    return _finish_tool(w, jid, alias, "video", out, prompt, key)


# ---- job tools ----------------------------------------------------------------------------------------------------------
def _describe(snap: dict, alias: str) -> str:
    pct = f", progress {int(snap['progress'] * 100)}%" if isinstance(snap["progress"], (int, float)) and snap["status"] == "running" else ""
    qp = f", place {snap['queue_position']} in the queue" if snap["status"] == "queued" and snap.get("queue_position") else ""
    err = f", error: {snap['error']}" if snap.get("error") else ""
    return f"Job {snap['id']} ({snap['kind']}, {alias}): {snap['status']}{pct}{qp}. Engine says: {snap['text'] or 'nothing'}{err}"


@work_tool("job_status", "State of a generation job you started.", '{"id": "mj_..."}')
def t_job_status(tc, w: Wk, a: dict) -> ToolOut:
    snap, ev = own_job(w, a_str(a, "id", maxlen=80))
    if snap["status"] in media_svc.ACTIVE:
        refresh_job(w.st, snap["id"])
        snap = snapshot(w.st, snap["id"]) or snap
    alias = ev.get("model") or _alias(w.st, snap["model_id"])
    job_event(w, snap, alias, **{k: ev[k] for k in ("out", "req") if ev.get(k)})
    return ToolOut(scrub(_describe(snap, alias)))


@work_tool("wait_job", "Wait up to timeout_s (1-600) for a job you started; when done the file is written to the workspace.", '{"id": "mj_...", "timeout_s": 120}')
def t_wait_job(tc, w: Wk, a: dict) -> ToolOut:
    snap, ev = own_job(w, a_str(a, "id", maxlen=80))
    t = a_int(a, "timeout_s", 60, 1, 600)
    alias = ev.get("model") or _alias(w.st, snap["model_id"])
    t0 = time.monotonic()
    extra = {k: ev[k] for k in ("out", "req") if ev.get(k)}
    snap = wait_for(w, snap["id"], t, alias, extra) if snap["status"] in media_svc.ACTIVE else snap
    if snap["status"] in media_svc.ACTIVE:
        return ToolOut(scrub(_still_running(snap, time.monotonic() - t0)))
    return outcome(w, snap, alias, ev.get("out"), ev.get("req"))


@work_tool("cancel_job", "Cancel a job you started.", '{"id": "mj_..."}')
def t_cancel_job(tc, w: Wk, a: dict) -> ToolOut:
    snap, ev = own_job(w, a_str(a, "id", maxlen=80))
    alias = ev.get("model") or _alias(w.st, snap["model_id"])
    if snap["status"] not in media_svc.ACTIVE:
        return ToolOut(f"Job {snap['id']} had already ended ({snap['status']}); nothing to cancel.")
    bridge(media_svc.cancel(w.st, snap["id"]), 90)
    snap = snapshot(w.st, snap["id"]) or snap
    job_event(w, snap, alias)
    return ToolOut(f"Job {snap['id']} is now {snap['status']}.")
