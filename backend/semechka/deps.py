from __future__ import annotations

import hashlib
import secrets
import time
from dataclasses import dataclass
from typing import Callable

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select

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
    if row.expires_at < now or (idle and now - row.last_seen > idle * 60):
        s.delete(row)
        s.commit()
        return None
    user = s.get(User, row.user_id)
    if user is None or user.status != "active":
        return None
    if now - row.last_seen > 30:
        row.last_seen = now
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
