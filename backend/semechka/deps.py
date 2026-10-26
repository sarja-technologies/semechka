from __future__ import annotations

import asyncio
import hashlib
import inspect
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from . import audit, services
from .models import SessionRow, User
from .perms import Principal
from .state import AppState


def get_state(request: Request) -> AppState:
    return request.app.state.sem


def get_db(request: Request, st: AppState = Depends(get_state)):
    """One session per request, committed when the endpoint returns.

    Every use is `Depends(get_db, scope="function")`: with the default request scope FastAPI runs the commit after the
    response has been sent, so a client could be told "signed in" (or "saved") and read the row before it exists, and
    a failed commit would still have answered 200. Streaming endpoints commit before they return and open their own
    sessions inside the stream."""
    endpoint = request.scope.get("endpoint")
    # A short synchronous handler that changes data starts as a writer and queues for the lock. A handler that waits on
    # something else while it holds its session (a coroutine: a client's upload, an engine call; or one marked
    # @slow_db) must not make every other writer wait with it: it reads first and takes the lock itself, if it
    # needs to, with begin_write() once the slow part is over.
    slow = inspect.iscoroutinefunction(endpoint) or getattr(endpoint, "__slow_db__", False)
    s = st.db.Session() if request.method in ("GET", "HEAD", "OPTIONS") or slow else st.db.writer()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def flush_seen(st: AppState) -> int:
    """Save the sessions' last-activity times. Best effort and in one short transaction: a busy database only means the
    next batch carries them; no request ever fails, or waits, because of it."""
    batch = dict(st.seen)
    if not batch:
        return 0
    try:
        with st.db.session(write=True) as s:
            for h, ts in batch.items():
                row = s.get(SessionRow, h)
                if row is not None and row.last_seen < ts:
                    row.last_seen = ts
    except OperationalError:
        return 0
    for h, ts in batch.items():
        if st.seen.get(h) == ts:
            st.seen.pop(h, None)
    return len(batch)


def slow_db(fn):
    """Mark a synchronous endpoint that does slow work (network, hashing many passwords) while it holds its session."""
    fn.__slow_db__ = True
    return fn


def begin_write(ctx) -> None:
    """After the slow part of a handler: end the read transaction and continue as a writer (see Database.upgrade)."""
    ctx.s.rollback()
    ctx.st.db.upgrade(ctx.s)


async def abegin_write(ctx) -> None:
    """begin_write for a coroutine handler. Waiting for the write lock on the event loop would stop the very request that
    holds it from finishing, so the wait happens on a worker thread."""
    await asyncio.to_thread(begin_write, ctx)


def get_db_deferred(st: AppState = Depends(get_state)):
    """Like get_db, but never starts as a writer: for an endpoint that does slow work first (a password hash) and calls
    `st.db.upgrade(s)` itself, after `s.rollback()`, just before it writes."""
    s = st.db.Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def is_https(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


@dataclass
class Ctx:
    user: User
    principal: Principal
    session: SessionRow
    s: object
    st: AppState
    ip: str

    def need(self, perm: str):
        d = self.principal.decide(perm)
        if not d.allowed:
            raise HTTPException(403, {"code": "forbidden", "permission": perm, "message": "You do not have access to this."})


def issue_session(s, st: AppState, request: Request, response: Response, user: User, mfa_ok: bool) -> SessionRow:
    token = secrets.token_urlsafe(32)
    hours = st.settings.deployment(s, "auth.session_hours")
    row = SessionRow(id_hash=hashlib.sha256(token.encode()).hexdigest(), user_id=user.id,
                     csrf=secrets.token_urlsafe(24), expires_at=time.time() + hours * 3600,
                     ip=client_ip(request), ua=(request.headers.get("user-agent") or "")[:200], mfa_ok=mfa_ok)
    s.add(row)
    secure = st.cfg.cookie_mode == "1" or (st.cfg.cookie_mode == "auto" and is_https(request))
    response.set_cookie(st.cookie_name, token, httponly=True, samesite="lax", secure=secure, path="/",
                        max_age=int(hours * 3600))
    return row


def _load(request: Request, s, st: AppState, *, require_mfa=True) -> Ctx | None:
    token = request.cookies.get(st.cookie_name)
    if not token:
        return None
    row = s.get(SessionRow, hashlib.sha256(token.encode()).hexdigest())
    if row is None:
        return None
    now = time.time()
    idle = st.settings.deployment(s, "auth.idle_minutes")
    seen = max(row.last_seen, st.seen.get(row.id_hash, 0.0))  # activity is kept in memory and saved in batches (flush_seen)
    if row.expires_at < now or (idle and now - seen > idle * 60):
        s.delete(row)
        s.commit()
        return None
    user = s.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    if now - seen > 30:
        st.seen[row.id_hash] = now
    return Ctx(user, services.load_principal(s, user), row, s, st, client_ip(request))


def optional_ctx(request: Request, s=Depends(get_db, scope="function"), st: AppState = Depends(get_state)) -> Ctx | None:
    return _load(request, s, st)


def current(request: Request, s=Depends(get_db, scope="function"), st: AppState = Depends(get_state)) -> Ctx:
    ctx = _load(request, s, st)
    if ctx is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Sign in to continue."})
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sent = request.headers.get("x-csrf-token", "")
        if not secrets.compare_digest(sent, ctx.session.csrf):
            raise HTTPException(403, {"code": "csrf", "message": "Missing or invalid CSRF token. Reload the page."})
    # MFA enforcement is server-side: until enrolled, only /api/auth/* and /api/me are reachable.
    path = request.url.path
    if mfa_required_for(ctx) and not ctx.user.totp_enabled and not (path.startswith("/api/auth/") or path == "/api/me"):
        raise HTTPException(403, {"code": "mfa_enrolment_required",
                                  "message": "Your organisation requires multi-factor authentication. Set it up to continue."})
    return ctx


def cloud_gate(ctx: Ctx = Depends(current)) -> Ctx:
    """Router-level guard for every cloud endpoint. The same answer for a signed-in person outside the rollout as for a feature
    that does not exist for them, so the rollout itself discloses nothing."""
    if not services.cloud_open_to(ctx.st, ctx.s, ctx.principal):
        raise HTTPException(403, {"code": "rollout", "message": "This feature is not available to your account yet."})
    return ctx


def require(perm: str) -> Callable:
    def dep(ctx: Ctx = Depends(current)) -> Ctx:
        ctx.need(perm)
        if perm.startswith("admin.") and mfa_required_for(ctx) and not ctx.user.totp_enabled:
            raise HTTPException(403, {"code": "mfa_required",
                                      "message": "Set up multi-factor authentication before using admin features."})
        return ctx
    return dep


def require_any(*perm_ids: str) -> Callable:
    """Holding any one of the permissions is enough (e.g. a page shared by two kinds of administrator)."""
    def dep(ctx: Ctx = Depends(current)) -> Ctx:
        held = [p for p in perm_ids if ctx.principal.can(p)]
        if not held:
            ctx.need(perm_ids[0])
        if any(p.startswith("admin.") for p in held) and mfa_required_for(ctx) and not ctx.user.totp_enabled:
            raise HTTPException(403, {"code": "mfa_required",
                                      "message": "Set up multi-factor authentication before using admin features."})
        return ctx
    return dep


def mfa_required_for(ctx: Ctx) -> bool:
    return services.mfa_required(ctx.s, ctx.st, ctx.principal)


def audit_ctx(ctx: Ctx, action: str, target: str = "", detail: dict | None = None):
    audit.record(ctx.s, ctx.user.id, action, target, detail, ctx.ip)
