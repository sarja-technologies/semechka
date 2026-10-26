"""Durable background jobs. State lives in the `jobs` table, so a restart, a crashed worker or a closed browser loses
nothing: a job whose lease expired is queued again (up to MAX_ATTEMPTS), and every job is authorised again when it
starts. Handlers are registered per kind and receive only ids; they re-load and re-check everything they touch.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
from collections.abc import Callable

from sqlalchemy import select, update

from . import audit, authz
from .models import User
from .models_files import Job
from .models_tenancy import OrgMember, Workspace

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
CONCURRENCY = {"ingest": 2, "embed": 1, "agent": 2}  # per kind; anything else runs one at a time
HANDLERS: dict[str, Callable] = {}
_running: dict[str, int] = {}
_lock = threading.Lock()


class JobPaused(Exception):
    """Raised by a handler that has set the job to awaiting_approval: leave the job exactly as the handler left it."""


class JobCancelled(Exception):
    """Raised by a handler (or the pre-flight) to end a job as cancelled, with a reason safe to show."""


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def enqueue(s, kind: str, *, org_id: str, ws_id: str | None, user_id: str, payload: dict, priority: int = 100) -> Job:
    j = Job(kind=kind, org_id=org_id, workspace_id=ws_id, created_by=user_id, payload=payload, priority=priority)
    s.add(j)
    s.flush()
    return j


def still_authorised(s, j: Job) -> str | None:
    """None when the person who started the job still holds a seat in the organisation; else the reason it must stop."""
    u = s.get(User, j.created_by)
    if u is None or u.status != "active":
        return "The person who started this job is no longer an active user."
    m = s.get(OrgMember, (j.org_id, j.created_by))
    if m is None or m.status != "active":
        return "The person who started this job is no longer a member of the organisation."
    if j.workspace_id:
        w = s.get(Workspace, j.workspace_id)
        if w is None:
            return "The workspace no longer exists."
        if not authz.Authz(s, j.created_by).check("ws.view", w).allowed:
            return "The person who started this job no longer has access to the workspace."
    return None


def recover_stale(st) -> int:
    """Jobs whose worker vanished (lease expired) go back to the queue, or fail after too many attempts."""
    n = 0
    with st.db.session() as s:
        for j in s.execute(select(Job).where(Job.status == "running", Job.lease_until < time.time())).scalars().all():
            if j.attempts >= MAX_ATTEMPTS:
                j.status, j.error, j.finished_at = "failed", "The worker stopped repeatedly while running this job.", time.time()
            else:
                j.status = "queued"
            n += 1
    return n


def recover_on_start(st) -> int:
    """At process start nothing is running here, so jobs the previous process left 'running' (a crash, a restart, a
    deploy) go straight back to the queue instead of waiting for their lease to expire. A job that has already been tried
    MAX_ATTEMPTS times is failed instead, so a job that crashes the server cannot crash it forever."""
    n = 0
    with st.db.session() as s:
        for j in s.execute(select(Job).where(Job.status == "running")).scalars().all():
            if j.attempts >= MAX_ATTEMPTS:
                j.status, j.error, j.finished_at = "failed", "The server stopped repeatedly while running this job.", time.time()
            else:
                j.status, j.lease_until = "queued", 0
            n += 1
    return n


def claim(st, kinds: list[str] | None = None) -> str | None:
    with st.db.session() as s:
        q = select(Job).where(Job.status == "queued").order_by(Job.priority, Job.created_at).limit(25)
        if kinds:
            q = q.where(Job.kind.in_(kinds))
        for j in s.execute(q).scalars().all():
            cap = CONCURRENCY.get(j.kind, 1)
            with _lock:
                if _running.get(j.kind, 0) >= cap:
                    continue
                r = s.execute(update(Job).where(Job.id == j.id, Job.status == "queued")
                              .values(status="running", started_at=time.time(), lease_until=time.time() + LEASE_SECONDS,
                                      attempts=Job.attempts + 1))
                if r.rowcount != 1:
                    continue
                _running[j.kind] = _running.get(j.kind, 0) + 1
            return j.id
    return None


def _preflight(st, job_id: str):
    """-> (kind, handler). Raises JobCancelled when the job must not start."""
    with st.db.session() as s:
        j = s.get(Job, job_id)
        kind = j.kind
        if j.cancel_requested:
            raise JobCancelled("Cancelled before it started.")
        why = still_authorised(s, j)
        if why:
            raise JobCancelled(why)
        fn = HANDLERS.get(kind)
        if fn is None:
            raise RuntimeError(f"No handler for '{kind}' jobs.")
        return kind, fn


def _complete(st, job_id: str, result: dict) -> None:
    with st.db.session() as s:
        j = s.get(Job, job_id)
        if j and j.status == "running":
            j.result, j.status, j.finished_at, j.error = result or {}, "succeeded", time.time(), ""
            if j.progress is not None:
                j.progress = 1.0


def run_job(st, job_id: str) -> None:
    """Run one claimed job on this thread. A coroutine handler (agents) gets its own event loop here; the application's
    pump runs those on the main loop instead (run_job_async) so they share its generation scheduler."""
    kind = None
    try:
        kind, fn = _preflight(st, job_id)
        if inspect.iscoroutinefunction(fn):
            _complete(st, job_id, asyncio.run(fn(st, job_id)))
        else:
            with st.db.session() as s:
                j = s.get(Job, job_id)
                result = fn(st, s, j) or {}
                j.result, j.status, j.finished_at, j.error = result, "succeeded", time.time(), ""
                if j.progress is not None:
                    j.progress = 1.0
    except JobPaused:
        pass
    except JobCancelled as e:
        _end(st, job_id, "cancelled", str(e))
    except Exception as e:  # noqa: BLE001  a handler bug must end the job, not the worker
        _end(st, job_id, "failed", f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        if kind:
            with _lock:
                _running[kind] = max(0, _running.get(kind, 0) - 1)


async def run_job_async(st, job_id: str) -> None:
    kind = None
    try:
        kind, fn = await asyncio.to_thread(_preflight, st, job_id)
        result = await fn(st, job_id)
        await asyncio.to_thread(_complete, st, job_id, result)
    except JobPaused:
        pass
    except JobCancelled as e:
        await asyncio.to_thread(_end, st, job_id, "cancelled", str(e))
    except Exception as e:  # noqa: BLE001
        await asyncio.to_thread(_end, st, job_id, "failed", f"{type(e).__name__}: {str(e)[:200]}")
    finally:
        if kind:
            with _lock:
                _running[kind] = max(0, _running.get(kind, 0) - 1)


def async_kinds() -> list[str]:
    return [k for k, fn in HANDLERS.items() if inspect.iscoroutinefunction(fn)]


def sync_kinds() -> list[str]:
    return [k for k, fn in HANDLERS.items() if not inspect.iscoroutinefunction(fn)]


def _end(st, job_id: str, status: str, error: str) -> None:
    with st.db.session() as s:
        j = s.get(Job, job_id)
        if j and j.status in ("running", "queued"):
            j.status, j.error, j.finished_at = status, error[:300], time.time()
            audit.record(s, "system", f"job.{status}", job_id, {"kind": j.kind, "reason": error[:120]}, org_id=j.org_id)


def run_pending(st, limit: int = 50, kinds: list[str] | None = None) -> int:
    """Run queued jobs on this thread until none are ready. The background pump and the tests both use it."""
    recover_stale(st)
    n = 0
    while n < limit:
        jid = claim(st, kinds)
        if jid is None:
            break
        run_job(st, jid)
        n += 1
    return n


async def pump(st) -> None:
    tasks: set[asyncio.Task] = set()
    while True:
        try:
            await asyncio.to_thread(recover_stale, st)
            await asyncio.to_thread(run_pending, st, 10, sync_kinds())
            kinds = async_kinds()
            while kinds:
                jid = await asyncio.to_thread(claim, st, kinds)
                if jid is None:
                    break
                t = asyncio.create_task(run_job_async(st, jid))
                tasks.add(t)
                t.add_done_callback(tasks.discard)
        except Exception:  # the pump must outlive any single failure
            log.exception("job pump iteration failed")
        await asyncio.sleep(1.0)


def cancel(s, az: authz.Authz, job_id: str) -> Job:
    j = s.get(Job, job_id)
    ws = s.get(Workspace, j.workspace_id) if j and j.workspace_id else None
    mine = j is not None and j.created_by == az.user_id and az.membership(j.org_id) is not None
    others = j is not None and ws is not None and az.check("job.manage_all", ws).allowed
    if j is None or not (mine or others):
        from .tenancy_svc import TenancyError
        raise TenancyError("not_found", "No such job.", 404)
    if j.status == "queued":
        j.status, j.error, j.finished_at = "cancelled", "Cancelled.", time.time()
    elif j.status in ("running", "awaiting_approval"):
        j.cancel_requested = True
    audit.record(s, az.user_id, "job.cancel", j.id, {"kind": j.kind}, org_id=j.org_id)
    return j


def cancelled(st, job_id: str) -> bool:
    with st.db.session() as s:
        j = s.get(Job, job_id)
        return bool(j and j.cancel_requested)
