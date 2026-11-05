"""OpenJutsu Studio: the live inspector. Semechka proxies the engine's trace of one job for the person who started it, and only for them.

* Ownership: MediaJob.user_id must be the caller; anything else answers like a missing job.
* The engine's documents (contract 2.2) are passed on with server file paths removed; the job id shown is Semechka's own.
* A job that finished stays inspectable after the engine forgot its trace (restart, cleaned-up data): the answer is then a minimal document built from
  Semechka's own record with ``trace_available: false``; nothing is invented (no node states, no segments).
* A hiccup of the engine is reported as ``stale`` (the last thing Semechka knows plus a reason), never as an error that ends the stream.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass

from sqlalchemy import select

from . import media_svc, studio_svc
from .adapters import AdapterError
from .adapters.sarness_media import clean_error
from .backends_svc import adapter_for
from .media_models import MediaAsset, MediaJob
from .models import Backend

TERMINAL = ("completed", "failed", "cancelled")
POLL_S = 1.0               # how often the stream asks the engine (tests shorten it)
HEARTBEAT_S = 15.0
RESEND_S = 5.0             # an unchanged running document is re-sent at least this often (elapsed time moves)
MAX_STREAM_S = 6 * 3600
_PATH = re.compile(r"(?:/[\w.@+\-]+){3,}")
NOT_FOUND = {"code": "not_found", "message": "Not found."}


def scrub(v, depth: int = 0):
    """JSON data from the engine with server file paths replaced and strings bounded; nothing but plain data survives."""
    if isinstance(v, str):
        return " ".join(_PATH.sub("<path>", v).split())[:2000] if "\n" not in v or len(v) < 2000 else _PATH.sub("<path>", v)[:2000]
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    if depth > 8:
        return None
    if isinstance(v, list):
        return [scrub(x, depth + 1) for x in v[:5000]]
    if isinstance(v, dict):
        return {str(k)[:80]: scrub(x, depth + 1) for k, x in list(v.items())[:5000]}
    return None


@dataclass
class JobInfo:
    id: str
    user_id: str
    remote: str | None
    status: str
    error: str | None
    progress: float | None
    progress_text: str
    started_at: float | None
    finished_at: float | None
    created_at: float
    backend_id: str
    studio: dict | None
    outputs: list[dict]


def load_job(st, jid: str, user_id: str) -> JobInfo | None:
    """The caller's job, or None (a foreign id and a missing one are the same)."""
    with st.db.session() as s:
        j = s.get(MediaJob, jid) if isinstance(jid, str) else None
        if j is None or j.user_id != user_id:
            return None
        outs = [{"width": a.width, "height": a.height, "duration": a.duration, "size": a.size, "kind": a.kind, "id": a.id}
                for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == j.id).order_by(MediaAsset.created_at)).scalars()]
        return JobInfo(j.id, j.user_id, j.remote_id, j.status, j.error, j.progress, j.progress_text, j.started_at, j.finished_at, j.created_at, j.backend_id,
                       studio_svc.studio_block(st, j), outs)


def adapter(st, ji: JobInfo):
    with st.db.session() as s:
        b = s.get(Backend, ji.backend_id)
        return adapter_for(b, st) if b is not None else None


def fallback_doc(ji: JobInfo, after: int, *, stale: bool = False, reason: str | None = None) -> dict:
    """What Semechka itself knows about a job, in the shape of an inspect document. Empty where it knows nothing."""
    done = ji.status == "completed"
    stage = {"queued": "queued", "completed": "done", "failed": "failed", "cancelled": "failed"}.get(ji.status, "unknown")
    label = {"completed": "Done", "failed": "Failed", "cancelled": "Cancelled"}.get(ji.status) or clean_error(ji.progress_text or "Working")
    studio = ji.studio or {}
    acc = studio.get("accounting") or {}
    sent = {x.get("asset"): x for x in acc.get("sent_to_engine") or []}
    why = {x.get("asset"): x.get("reason") for x in acc.get("not_used") or []}
    refs = []
    for i, r in enumerate(studio.get("references") or []):
        used = r.get("asset") in sent
        refs.append({"position": i + 1, "node_id": None, "file": None, "used": used, "name": r.get("label") or None, "asset": r.get("asset"),
                     **({} if used else {"reason": why.get(r.get("asset")) or "Not used in this run."})})
    out_file = None
    if done and ji.outputs:
        o = ji.outputs[0]
        out_file = {"width": o["width"], "height": o["height"], "duration_s": o["duration"], "bytes": o["size"], "has_audio": None}
    end = ji.finished_at or time.time()
    doc = {"job_id": ji.id, "backend": None, "profile": None, "operation": studio.get("operation"), "status": ji.status, "stage": stage, "stage_label": label,
           "prompt_id": None, "started_at": ji.started_at, "updated_at": end, "elapsed_s": round(max(0.0, end - (ji.started_at or ji.created_at)), 1),
           "progress": {"fraction": 1.0 if done else ji.progress, "indeterminate": not done and ji.progress is None, "basis": "job_state" if (done or ji.progress is not None) else None},
           "sampling": None, "scenes": {"total": None, "completed": None, "current": None, "segments": []}, "loop": None, "nodes": None, "node_states": {},
           "conditioning": {"video": None, "references": refs, "agree": None}, "memory": None,
           "output": {"state": "ready" if out_file else "none", "file": out_file},
           "error": {"message": clean_error(ji.error), "node_id": None, "node_title": None, "class_type": None} if ji.error else None,
           "events": [], "next_seq": after, "last_seq": max(0, after - 1), "trace_available": False, "stale": stale, "comfy": {"available": False}, "user_status": ji.status}
    if stale:
        doc["stale_reason"] = reason
        doc["trace_available"] = None
    return doc


def decorate(ji: JobInfo, doc: dict) -> dict:
    doc = scrub(doc)
    doc["job_id"] = ji.id
    last = doc.get("last_seq")
    if not isinstance(last, int):                     # an engine that only says next_seq: that is "last + 1" too
        last = max(0, int(doc.get("next_seq") or 1) - 1)
    doc["last_seq"], doc["next_seq"] = last, last + 1
    doc["comfy"] = {"available": bool(doc.get("backend") == "comfyui" and doc.get("prompt_id") and doc.get("nodes") is not None)}
    doc["user_status"] = ji.status
    doc["trace_available"] = True
    doc["stale"] = False
    return doc


async def get_doc(st, uid: str, jid: str, after: int) -> dict | None:
    """The inspect document for one job (None when it is not the caller's). Advances the job first so the status is current."""
    ji = load_job(st, jid, uid)
    if ji is None:
        return None
    if ji.status in media_svc.ACTIVE:
        await media_svc.refresh(st, jid)
        ji = load_job(st, jid, uid)
    ad = adapter(st, ji)
    if ad is None or not ji.remote:
        return fallback_doc(ji, after)
    try:
        code, d = await ad.media_inspect(ji.remote, max(0, after - 1))     # Semechka's cursor is next_seq; the engine wants the last seq the client has
    except AdapterError as e:
        if ji.status in TERMINAL:
            return fallback_doc(ji, after)
        return fallback_doc(ji, after, stale=True, reason=clean_error(e))
    if code == 200 and isinstance(d, dict):
        return decorate(ji, d)
    return fallback_doc(ji, after)  # 404 (no trace) or anything else the engine refuses: what Semechka knows


def finished(doc: dict) -> bool:
    """The final document was produced: the job is over for the engine AND for Semechka (its result is collected)."""
    if doc.get("stale"):
        return False
    return doc.get("user_status") in TERMINAL and (doc.get("status") in TERMINAL or not doc.get("trace_available"))


def _sig(doc: dict) -> str:
    skip = {"elapsed_s", "updated_at", "memory", "events"}
    return json.dumps({k: v for k, v in doc.items() if k not in skip}, sort_keys=True, default=str)


def sse(event: str, data, eid: int | None = None) -> bytes:
    head = f"id: {eid}\n" if eid is not None else ""
    return f"{head}event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode()


async def stream(st, request, uid: str, jid: str, after: int):
    """text/event-stream of inspect documents: ``event: inspect`` (id = next_seq, so Last-Event-ID resumes after the last event the client has), ``event:
    stale`` while the engine is not answering, a comment every 15 s, and the end of the stream once the final document went out."""
    t0 = time.monotonic()
    last_sig, last_sent, last_beat = None, 0.0, time.monotonic()
    cursor = after
    stale_sent = False
    yield b"retry: 3000\n\n"
    while time.monotonic() - t0 < MAX_STREAM_S:
        if await request.is_disconnected():
            return
        try:
            doc = await get_doc(st, uid, jid, cursor)
        except Exception:  # noqa: BLE001 - whatever went wrong, the stream stays up and says so
            doc = {"stale": True, "stale_reason": "Semechka could not read the job just now."}
        if doc is None:
            yield sse("gone", NOT_FOUND)
            return
        now = time.monotonic()
        if doc.get("stale"):
            if not stale_sent:
                yield sse("stale", {"reason": doc.get("stale_reason") or "The inference engine is not answering."})
                stale_sent = True
            await asyncio.sleep(min(5.0, POLL_S * 2))
            continue
        stale_sent = False
        sig = _sig(doc)
        if sig != last_sig or doc.get("events") or now - last_sent >= RESEND_S or finished(doc):
            nxt = int(doc.get("next_seq") or cursor)
            yield sse("inspect", doc, nxt)
            cursor = nxt
            last_sig, last_sent, last_beat = sig, now, now
            if finished(doc):
                return
        elif now - last_beat >= HEARTBEAT_S:
            yield b": heartbeat\n\n"
            last_beat = now
        await asyncio.sleep(POLL_S)
