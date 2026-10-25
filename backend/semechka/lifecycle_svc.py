"""Model lifecycle: what an engine has loaded, and loading or freeing a model without disturbing anyone.

Everything here is read from the engine at the moment it is asked; nothing is simulated. The Sarness Engine shares
one chat model slot between every client (both Semechka instances, desktop apps), and asking it for another chat
model stops the current one even mid-reply. A check made before such a request cannot protect a reply another client
starts in between, and the engine offers no reservation, so only operations the engine itself guards are offered:

  load a chat model    not offered on such an engine (exclusive_chat_model); it loads when someone first uses it.
                       Engines that hold several models (Ollama) keep the checks below.
  unload a chat model  refused while it is generating a reply; the engine refuses too, inside its own process
  unload image/video   refused while the engine is running an image or video job (the engine also refuses)
  stop the worker      refused while a picture or video job runs or waits (the engine refuses while one runs)
  load image/video     not offered: the engine loads one when a job needs it

Chat requests are the other way a model gets switched. The scheduler never runs this server's replies for two
different chat models on such an engine at once, and wait_for_chat_slot() holds a switch while the engine reports
another client mid-reply (that narrows the window to one request; it cannot close it).

A load can take minutes, so it runs in the background; progress is the engine's own state (stopped, loading,
ready, error), polled, and every request and outcome is audited.
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from . import adapters, audit
from .adapters import AdapterError
from .backends_svc import adapter_for
from .media_models import MediaJob
from .models import Backend, Model

LLM_PREFIXES = ("local:",)
QUICK_SECONDS = 3.0  # how long a load request waits for the outcome before reporting it as still running
MEDIA_PREFIXES = ("image:", "video:")


def engine_id(real_id: str) -> str:
    return real_id.split(":", 1)[-1]


def kind_of(m: Model) -> str:
    if m.real_id.startswith(MEDIA_PREFIXES):
        return "media"
    if m.real_id.startswith(LLM_PREFIXES):
        return "llm"
    return "other"


async def engine_state(st, b: Backend) -> dict | None:
    """The engine's live status, or None when the backend cannot report one."""
    ad = adapter_for(b, st)
    try:
        return await ad.status()
    except AdapterError:
        return None


STATUS_TTL = 5.0  # seconds a status read serves model pickers before the engine is asked again


async def cached_state(st, b: Backend) -> dict | None:
    """engine_state() shared by everyone opening a model picker within a few seconds."""
    key = "status:" + b.id
    hit = st.health_cache.get(key)
    if hit and time.time() - hit[0] < STATUS_TTL:
        return hit[1]
    status = await engine_state(st, b)
    st.health_cache[key] = (time.time(), status)
    return status


def own_remote_ids(s, backend_id: str) -> set[str]:
    rows = s.execute(select(MediaJob.remote_id).where(MediaJob.backend_id == backend_id,
                                                      MediaJob.status.in_(("queued", "running")))).scalars()
    return {r for r in rows if r}


async def engine_queue(st, s, b: Backend) -> list[dict] | None:
    """The engine's running and queued jobs. Each is marked `own` when this instance submitted it; jobs from other
    clients show only kind, model and state."""
    ad = adapter_for(b, st)
    try:
        rows = await ad.engine_jobs()
    except AdapterError:
        return None
    if rows is None:
        return None
    mine = own_remote_ids(s, b.id)
    out = []
    for j in rows:
        own = j.get("id") in mine
        out.append({**{k: j.get(k) for k in ("kind", "status", "model", "progress", "queue_position", "started_at",
                                             "created_at")}, "own": own, "id": j.get("id") if own else None})
    return out


def loaded_ids(status: dict | None) -> set[str] | None:
    """Engine model ids the engine reports as loaded (chat model ready or loading, plus worker-held models)."""
    if not status:
        return None
    llm = status.get("llm") or {}
    out = set((status.get("worker") or {}).get("loaded") or [])
    if llm.get("model_id") and llm.get("state") in ("ready", "loading"):
        out.add(llm["model_id"])
    return out


def reconcile(s, b: Backend, status: dict | None) -> None:
    """Write the engine's loaded state onto the catalogue, so lists show what is true now."""
    ids = loaded_ids(status)
    if ids is None:
        return
    for m in s.execute(select(Model).where(Model.backend_id == b.id)).scalars():
        if kind_of(m) == "other":
            continue
        val = engine_id(m.real_id) in ids
        if (m.meta or {}).get("loaded") != val:
            m.meta = {**(m.meta or {}), "loaded": val}


def model_state(m: Model, status: dict | None) -> str | None:
    """loaded | loading | idle | error, or None when unknown."""
    if not status or kind_of(m) == "other":
        return None
    eid, llm = engine_id(m.real_id), status.get("llm") or {}
    if kind_of(m) == "llm" and llm.get("model_id") == eid:
        return {"ready": "loaded", "loading": "loading", "error": "error"}.get(llm.get("state"), "idle")
    return "loaded" if eid in ((status.get("worker") or {}).get("loaded") or []) else "idle"


def check(st, m: Model, b: Backend, op: str, status: dict | None) -> dict:
    """{"allowed": bool, "blockers": [...], "warnings": [...], "noop": str|None} for load/unload of one model."""
    blockers: list[str] = []
    warnings: list[str] = []
    noop, code = None, "busy"
    kind = kind_of(m)
    ad_cls = adapters.REGISTRY.get(b.adapter)
    man = ad_cls.manifest if ad_cls else None
    if not b.enabled:
        blockers.append("This model's engine is turned off in Semechka.")
    if not m.available:
        blockers.append("The engine no longer reports this model.")
    if op == "load" and man is not None and man.exclusive_chat_model and kind == "llm":
        from .adapters.sarness import PRELOAD_OFF
        return {"allowed": False, "blockers": [PRELOAD_OFF], "warnings": warnings, "noop": None, "code": "unsupported"}
    if man is None or not (man.model_load if op == "load" else man.model_unload):
        blockers.insert(0, f"{man.name if man else 'This backend'} cannot {op} models from Semechka.")
        return {"allowed": False, "blockers": blockers, "warnings": warnings, "noop": None, "code": "unsupported"}
    if kind == "other":
        blockers.insert(0, "This model is on a remote endpoint; the engine does not hold it in memory.")
        code = "unsupported"
    if op == "load" and kind == "media":
        blockers.insert(0, "Image and video models load when a job needs them; there is nothing to load ahead of time.")
        code = "unsupported"
    if status is None:
        blockers.append("The engine is not answering, so its current state cannot be checked.")
        return {"allowed": False, "blockers": blockers, "warnings": warnings, "noop": None,
                "code": code if code == "unsupported" else "unreachable"}
    llm, jobs = status.get("llm") or {}, status.get("jobs") or {}
    eid = engine_id(m.real_id)
    here = st.scheduler.model_load(b.id)
    mine = here.get(m.id, {"running": 0, "waiting": 0})
    if kind == "llm":
        current, streams = llm.get("model_id"), int(llm.get("active_streams") or 0)
        if op == "load":
            if current == eid and llm.get("state") == "ready":
                noop = "Already loaded."
            if llm.get("state") == "loading":
                blockers.append(f"The engine is already loading {current or 'a model'}.")
            elif current and current != eid and streams:
                blockers.append(f"{current} is writing {streams} repl{'y' if streams == 1 else 'ies'} right now. "
                                "Loading another chat model would cut them off.")
            if current and current != eid:
                busy_here = sum(v["running"] for k, v in here.items() if k != m.id)
                if busy_here and not streams:
                    blockers.append("Someone on this server is about to receive a reply from the loaded model.")
                waiting = sum(v["waiting"] for k, v in here.items() if k != m.id)
                if waiting:
                    warnings.append(f"{waiting} request{'s are' if waiting != 1 else ' is'} waiting for another model "
                                    "and will load it again when their turn comes.")
                warnings.append(f"{current} will be unloaded first: the engine holds one chat model at a time.")
            need = (m.meta or {}).get("vram_estimate_mb")
            mem = status.get("memory") or {}
            if isinstance(need, (int, float)) and mem.get("total_mb") and need > mem["total_mb"]:
                blockers.append(f"Needs about {need:,} MB; the engine has {mem['total_mb']:,} MB in total.")
            elif isinstance(need, (int, float)) and mem.get("available_mb") is not None:
                freed = (llm.get("vram_estimate_mb") or 0) if current and current != eid else 0
                if need > mem["available_mb"] + freed:
                    warnings.append(f"Needs about {need:,} MB and about {mem['available_mb'] + freed:,} MB is "
                                    "free. The engine will stop an idle image or video model first, or place part "
                                    "of the model on the CPU, which is slower.")
            if jobs.get("running"):
                warnings.append("The engine is running an image or video job; if memory is short the load is refused.")
        else:
            if current != eid or llm.get("state") == "stopped":
                noop = "Not loaded."
            elif streams:
                blockers.append(f"It is writing {streams} repl{'y' if streams == 1 else 'ies'} right now.")
            elif llm.get("state") == "loading":
                blockers.append("It is still loading.")
            if mine["running"] and not streams and noop is None:
                blockers.append("Someone on this server is about to receive a reply from it.")
            if mine["waiting"]:
                warnings.append(f"{mine['waiting']} request{'s are' if mine['waiting'] != 1 else ' is'} waiting for it "
                                "and will load it again.")
    elif kind == "media" and op == "unload":
        if eid not in ((status.get("worker") or {}).get("loaded") or []):
            noop = "Not loaded."
        elif jobs.get("running"):
            blockers.append("The engine is running an image or video job. Wait for it to finish or cancel it first.")
    cur = st.lifecycle.get(b.id)
    if cur and cur.get("state") == "running":
        blockers.append(f"A {cur['op']} of another model on this engine is in progress.")
    return {"allowed": not blockers, "blockers": blockers, "warnings": warnings, "noop": noop, "code": code}


def public_op(op: dict | None) -> dict | None:
    return {k: v for k, v in op.items() if not k.startswith("_")} if op else None


async def run(st, b: Backend, m: Model, op: str, actor: str, ip: str) -> dict:
    """Start a load/unload. Unload finishes before returning; load continues in the background."""
    rec = {"op": op, "model_id": m.id, "model": engine_id(m.real_id), "state": "running", "started_at": time.time(),
           "finished_at": None, "error": None, "error_kind": None, "actor": actor}
    st.lifecycle[b.id] = rec
    ad = adapter_for(b, st)
    real_id = m.real_id

    async def go():
        try:
            await (ad.load if op == "load" else ad.unload)(real_id)
            rec.update(state="done")
        except AdapterError as e:
            rec.update(state="failed", error=str(e), error_kind=e.kind)
        except Exception as e:  # noqa: BLE001 - recorded, never raised into the event loop
            rec.update(state="failed", error=f"Unexpected error: {type(e).__name__}")
        rec["finished_at"] = time.time()
        status = await engine_state(st, b)
        st.health_cache["status:" + b.id] = (time.time(), status)
        with st.db.session() as s:
            bb = s.get(Backend, b.id)
            if bb is not None:
                reconcile(s, bb, status)
            audit.record(s, actor, f"model.{op}.{'done' if rec['state'] == 'done' else 'failed'}", m.id,
                         {"model": rec["model"], "seconds": round(rec["finished_at"] - rec["started_at"], 1),
                          **({"error": rec["error"][:200]} if rec["error"] else {})}, ip)

    if op == "load":
        task = rec["_task"] = asyncio.get_running_loop().create_task(go())
        try:  # a small model is ready within moments: answer with the outcome rather than "started"
            await asyncio.wait_for(asyncio.shield(task), QUICK_SECONDS)
        except asyncio.TimeoutError:
            pass
    else:
        await go()
    return public_op(rec)


def check_worker_stop(st, b: Backend, status: dict | None) -> dict:
    """Stopping the picture/video worker returns all of its memory, including what stays held after freeing a model.
    The next picture or video starts it again, which makes that one job slower."""
    blockers: list[str] = []
    ad_cls = adapters.REGISTRY.get(b.adapter)
    if not (ad_cls and ad_cls.manifest.worker_stop):
        return {"allowed": False, "blockers": ["This engine has no picture and video worker to stop."], "warnings": [],
                "noop": None, "code": "unsupported"}
    if status is None:
        return {"allowed": False, "blockers": ["The engine is not answering, so its current state cannot be checked."],
                "warnings": [], "noop": None, "code": "unreachable"}
    worker, jobs = status.get("worker") or {}, status.get("jobs") or {}
    if not worker.get("alive"):
        return {"allowed": True, "blockers": [], "warnings": [], "noop": "The worker is not running.", "code": "busy"}
    if worker.get("busy") or jobs.get("running"):
        blockers.append("The worker is making a picture or video. Wait for it to finish or cancel it first.")
    if jobs.get("queued"):
        blockers.append(f"{jobs['queued']} picture or video job{'s are' if jobs['queued'] != 1 else ' is'} waiting; "
                        "the worker would start again at once.")
    cur = st.lifecycle.get(b.id)
    if cur and cur.get("state") == "running":
        blockers.append(f"A {cur['op']} on this engine is in progress.")
    return {"allowed": not blockers, "blockers": blockers, "warnings": [], "noop": None, "code": "busy"}


async def stop_worker(st, b: Backend, actor: str, ip: str) -> dict:
    rec = {"op": "stop_worker", "model_id": None, "model": None, "state": "running", "started_at": time.time(),
           "finished_at": None, "error": None, "error_kind": None, "actor": actor}
    st.lifecycle[b.id] = rec
    try:
        await adapter_for(b, st).stop_worker()
        rec.update(state="done")
    except AdapterError as e:
        rec.update(state="failed", error=str(e), error_kind=e.kind)
    rec["finished_at"] = time.time()
    status = await engine_state(st, b)
    st.health_cache["status:" + b.id] = (time.time(), status)
    with st.db.session() as s:
        bb = s.get(Backend, b.id)
        if bb is not None:
            reconcile(s, bb, status)
        audit.record(s, actor, f"engine.worker_stop.{'done' if rec['state'] == 'done' else 'failed'}", b.id,
                     {"seconds": round(rec["finished_at"] - rec["started_at"], 1),
                      **({"error": rec["error"][:200]} if rec["error"] else {})}, ip)
    return public_op(rec)


# ---- chat requests on an engine with one chat model for every client ------------------------------------------
SLOT_POLL_SECONDS = 1.0


def want_ctx(m: Model, params: dict) -> int | None:
    """The context window the engine will load `m` with for this request, as the engine works it out: the request's
    own window, else the model's default, never above its maximum. None when the engine reported neither."""
    meta = m.meta or {}
    ctx = params.get("ctx") or meta.get("default_ctx")
    if not ctx:
        return None
    top = meta.get("context")
    return min(int(ctx), int(top)) if top else int(ctx)


def would_switch(slot: dict, eid: str, want_ctx: int | None) -> bool:
    """True when a request for `eid` makes the engine (re)load its chat model: another model, nothing ready, or the
    same model with another context window. `want_ctx` is the window the engine will use (the request's, else the
    model's reported default); when Semechka cannot work it out, a ready same model counts as no switch."""
    if slot.get("state") != "ready" or slot.get("model_id") != eid:
        return True
    return want_ctx is not None and slot.get("ctx") not in (None, want_ctx)


def someone_else_busy(slot: dict) -> bool:
    """Another client is mid-reply or loading. When this is asked, this server runs no reply on a different chat
    model of this engine (the scheduler sees to that), so every stream counted here belongs to someone else."""
    return int(slot.get("active_streams") or 0) > 0 or slot.get("state") == "loading"


async def wait_for_chat_slot(ad, eid: str, want_ctx: int | None, deadline: float, clock=time.monotonic):
    """Async generator: yields once per poll while switching the engine's chat model now would cut off another
    client's reply; returns as soon as it would not (or when the engine cannot say). Raises TimeoutError at the
    deadline. Between the last poll and the request there is still one network round trip in which another client
    can start a reply; the engine offers nothing that would close it."""
    while True:
        slot = await ad.chat_slot()
        if slot is None or not would_switch(slot, eid, want_ctx) or not someone_else_busy(slot):
            return
        if clock() >= deadline:
            raise TimeoutError
        yield slot
        await asyncio.sleep(SLOT_POLL_SECONDS)
