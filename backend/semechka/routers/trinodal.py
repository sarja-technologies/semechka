"""Trinodal AI behind Semechka: the management UI at /trinodal/ on this origin, and what the web app may offer.

Trinodal AI is a separate service (default http://127.0.0.1:8800) with its own single-page management UI, JSON API and
event streams, written to work under any path prefix. This router serves all of it at /trinodal/, so the browser talks
to one origin only: no CORS, no second sign-in, no service token in the browser.

Every request:
  1. needs a Semechka session (a signed-out visitor gets 401, or the sign-in page when asking for a page);
  2. is allowed only if the person holds the permission its class needs, decided HERE, not in the interface:
       GET/HEAD of the UI and of the read-only API ........ trinodal.view
       /api/admin/* (any method) .......................... trinodal.admin
       any other method (POST, PUT, PATCH, DELETE), notably
       /api/training, /api/datasets, /api/jobs,
       /api/checkpoints, /api/deploy ...................... trinodal.train
  3. if it changes anything, passes Semechka's usual state-change rules: Origin must match Host, and the session's
     CSRF token must come in X-CSRF-Token (the UI reads it from /trinodal/_semechka/session, answered here);
  4. is forwarded with a fresh header set: the service token (read from a file on this server) as a bearer token,
     X-Trinodal-Actor "<instance>/<user id>" and X-Trinodal-Scopes (read, train, admin: what the person holds).
     Cookies, Authorization, Origin and everything else the browser sent are dropped, and the answer comes back without
     cookies, CSP, CORS or cache headers of Trinodal's, so Semechka's own CSP and security headers apply unchanged.
Responses are streamed (event streams are not buffered, and closing the browser's connection closes Trinodal's), with
limits on header size, body size and time. Changes and refusals are audited without bodies.
"""
from __future__ import annotations

import asyncio
import logging
import secrets
import time
import uuid
from urllib.parse import quote, urlsplit

import anyio
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

from .. import __version__, audit, trinodal_svc as T
from ..deps import Ctx, client_ip, mfa_required_for, optional_ctx

log = logging.getLogger("semechka.trinodal")

router = APIRouter()

METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]
MAX_QUERY = 8192
MAX_OPEN_PER_USER = 24  # requests (including open event streams) one person may have in flight
READ_LIMIT, WRITE_LIMIT = 1200, 120  # per person per minute; a page load fetches a few dozen files
CONNECT_TIMEOUT, IO_TIMEOUT = 5.0, 120.0
MAX_BODY_SECONDS, MAX_STREAM_SECONDS = 900.0, 4 * 3600.0
NO_BODY = {204, 304}


def _err(status: int, code: str, message: str, headers: dict | None = None, **extra) -> JSONResponse:
    return JSONResponse({"detail": {"code": code, "message": message, **extra}}, status_code=status,
                        headers={"Cache-Control": "no-store", **(headers or {})})


def _audit(st, actor: str, action: str, target: str, detail: dict, ip: str) -> None:
    with st.db.session(write=True) as s:
        audit.record(s, actor, action, target, detail, ip)


async def _audit_async(st, actor, action, target, detail, ip) -> None:
    try:
        await asyncio.to_thread(_audit, st, actor, action, target, detail, ip)
    except Exception:  # an audit hiccup must not turn into an open door or a 500 with details
        log.exception("could not write a Trinodal audit event")


async def _deny(st, user_id: str, request: Request, path: str, status: int, code: str, message: str, needed: str = "",
                **extra):
    """Refuse and leave a trace (no bodies). The trace is rate-limited per person, so refusals cannot flood the log."""
    if st.limiter.hit(f"trinodal:deny-audit:{user_id}", 20, 60):
        await _audit_async(st, user_id, "trinodal.denied", "trinodal",
                           {"method": request.method, "class": T.path_class(request.method, path), "needed": needed,
                            "reason": code, "path": path[:200]}, client_ip(request))
    return _err(status, code, message, **extra)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    return not origin or urlsplit(origin).netloc == request.headers.get("host", "")


@router.get("/api/trinodal/availability")
def availability(ctx: Ctx = Depends(optional_ctx)):
    """What this person may be offered. Permission-based, and never the address or the token. A person who holds no
    trinodal.* permission is told nothing, whether or not the feature is configured."""
    if ctx is None:
        raise HTTPException(401, {"code": "unauthenticated", "message": "Sign in to continue."})
    held = T.scopes_held(ctx.principal)
    configured = bool(T.base_url(ctx.s, ctx.st))
    on = bool(configured and held)
    return {"enabled": on, "view": on and "read" in held, "train": on and "train" in held, "admin": on and "admin" in held}


@router.api_route("/trinodal", methods=METHODS, include_in_schema=False)
@router.api_route("/trinodal/{path:path}", methods=METHODS, include_in_schema=False)
async def proxy(request: Request, path: str = "", ctx: Ctx | None = Depends(optional_ctx)):
    st = request.app.state.sem
    method = request.method
    # ---- who -------------------------------------------------------------------------------------------------
    if ctx is None:
        if method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login?next=" + quote(request.url.path), status_code=302)
        return _err(401, "unauthenticated", "Sign in to continue.")
    if mfa_required_for(ctx) and not ctx.user.totp_enabled:
        return _err(403, "mfa_enrolment_required",
                    "Your organisation requires multi-factor authentication. Set it up to continue.")
    principal, user_id = ctx.principal, ctx.user.id
    base = T.base_url(ctx.s, ctx.st)
    max_up = int(st.settings.deployment(ctx.s, "trinodal.max_upload_mb")) * 1024 * 1024
    max_down = int(st.settings.deployment(ctx.s, "trinodal.max_download_mb")) * 1024 * 1024
    csrf, display = ctx.session.csrf, ctx.user.display_name
    ctx.s.close()  # nothing below touches this session, and the request may take a long time
    if not base:
        return _err(404, "not_found", "Not found.")
    if request.url.path == "/trinodal":  # the UI's relative addresses need the trailing slash
        return RedirectResponse("/trinodal/" + (("?" + request.url.query) if request.url.query else ""), status_code=308)
    try:
        rel = T.clean_path(path)
    except T.BadPath as e:
        return _err(400, "bad_path", str(e))
    if len(request.url.query) > MAX_QUERY:
        return _err(414, "uri_too_long", "The address is too long.")
    ip = client_ip(request)

    # ---- may they ----------------------------------------------------------------------------------------------
    need = T.required_scope(method, rel)
    perm = T.SCOPE_PERM[need]
    if rel.split("/", 1)[0] == T.RESERVED:  # answered here; only for someone who may use Trinodal at all
        if not T.holds_any(principal):
            return await _deny(st, user_id, request, rel, 403, "forbidden", "You do not have access to this.", perm,
                               permission=perm)
        if method == "GET" and rel == f"{T.RESERVED}/session":
            return JSONResponse({"csrf": csrf, "scopes": T.scopes_held(principal), "instance": T.instance_name(st),
                                 "user": {"id": user_id, "display_name": display}}, headers={"Cache-Control": "no-store"})
        return _err(404, "not_found", "Not found.")
    if not principal.can(perm):
        return await _deny(st, user_id, request, rel, 403, "forbidden", "You do not have access to this.", perm,
                           permission=perm)
    if method != "GET" and method != "HEAD":  # state changes: the same rules as every other route
        if not _same_origin(request):
            return await _deny(st, user_id, request, rel, 403, "bad_origin", "Cross-site request refused.", perm)
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), csrf):
            return await _deny(st, user_id, request, rel, 403, "csrf", "Missing or invalid CSRF token. Reload the page.", perm)
    if not st.limiter.hit(f"trinodal:{'r' if method in ('GET', 'HEAD') else 'w'}:{user_id}",
                          READ_LIMIT if method in ("GET", "HEAD") else WRITE_LIMIT, 60):
        return _err(429, "rate_limited", "Too many requests. Wait a moment and try again.", headers={"Retry-After": "30"})

    # ---- forward -----------------------------------------------------------------------------------------------
    token = T.read_token(st.cfg.trinodal_token_path)
    if token is None:
        log.warning("Trinodal request refused: no usable service token file is configured")
        return _err(503, "trinodal_not_configured",
                    "The Trinodal service token is not set up on this server. Ask your administrator.")
    sent = request.headers.get("content-length")
    if sent is not None and (not sent.isdigit() or int(sent) > max_up):
        return await _deny(st, user_id, request, rel, 413, "too_large", "That upload is larger than this server allows.", perm)
    if st.trinodal_open.get(user_id, 0) >= MAX_OPEN_PER_USER:
        return _err(429, "too_many_open", "Too many Trinodal requests are open at once.", headers={"Retry-After": "5"})

    url = base + "/" + quote(rel, safe="/:@!$&'()*+,;=-._~") + (("?" + request.url.query) if request.url.query else "")
    headers = T.request_headers(request.headers, token=token, actor=f"{T.instance_name(st)}/{user_id}",
                                scopes=T.scopes_held(principal), request_id=uuid.uuid4().hex, version=__version__)
    has_body = method in ("POST", "PUT", "PATCH", "DELETE") and (
        sent not in (None, "0") or "transfer-encoding" in request.headers)

    class TooLarge(Exception):
        pass

    async def body():
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > max_up:
                raise TooLarge
            yield chunk

    st.trinodal_open[user_id] = st.trinodal_open.get(user_id, 0) + 1
    released = False

    def release():
        nonlocal released
        if not released:
            released = True
            n = st.trinodal_open.get(user_id, 1) - 1
            if n > 0:
                st.trinodal_open[user_id] = n
            else:
                st.trinodal_open.pop(user_id, None)

    client = httpx.AsyncClient(timeout=httpx.Timeout(IO_TIMEOUT, connect=CONNECT_TIMEOUT), follow_redirects=False,
                               trust_env=False)
    if has_body and sent is not None:
        headers["content-length"] = sent  # keeps the upload a plain, sized request for a server that cannot read chunked
    out: Response | None = None
    status = 0
    try:
        req = client.build_request(method, url, headers=headers, content=body() if has_body else None)
        up = await client.send(req, stream=True)
    except TooLarge:
        await client.aclose()
        release()
        return await _deny(st, user_id, request, rel, 413, "too_large", "That upload is larger than this server allows.", perm)
    except httpx.TimeoutException:
        await client.aclose()
        release()
        out, status = _err(504, "trinodal_timeout", "Trinodal did not answer in time."), 504
    except (httpx.HTTPError, OSError, RuntimeError):
        await client.aclose()
        release()
        out, status = _err(502, "trinodal_unreachable", "Trinodal could not be reached."), 502
    except BaseException:  # the browser went away mid-upload, or the request was cancelled
        await client.aclose()
        release()
        raise
    if out is None:
        async def close_upstream():
            with anyio.CancelScope(shield=True):  # runs even while the request is being cancelled
                try:
                    await up.aclose()
                finally:
                    await client.aclose()
                    release()

        status = up.status_code
        sse = up.headers.get("content-type", "").lower().startswith("text/event-stream")
        clen = up.headers.get("content-length", "")
        problem = None
        if status in (401, 407):  # Trinodal refused the service token: that is not the person's session expiring
            problem = (502, "trinodal_auth", "Trinodal did not accept this server's service token.")
        elif sum(len(k) + len(v) for k, v in up.headers.items()) > T.MAX_RESPONSE_HEADER_BYTES:
            problem = (502, "trinodal_headers", "Trinodal sent headers that are too large.")
        elif not sse and method != "HEAD" and clen.isdigit() and int(clen) > max_down:
            problem = (502, "too_large", "Trinodal's answer is larger than this server allows.")
        elif status < 200:
            problem = (502, "trinodal_status", "Trinodal sent an unexpected answer.")
        if problem:
            await close_upstream()
            out = _err(*problem)
        else:
            out_headers = T.response_headers(up.headers, sse=sse, api=rel.startswith("api/"))
            if (loc := up.headers.get("location")) is not None:  # a redirect stays inside /trinodal/
                out_headers["location"] = _rewrite_location(loc, base)
            if method != "HEAD" and status not in NO_BODY:
                out = _Proxied(_chunks(up, close_upstream, sse, max_down), close_upstream, status_code=status, headers=out_headers)
            else:
                await close_upstream()
                if status in NO_BODY and status == 204:
                    out_headers.pop("content-length", None)
                out = Response(status_code=status, headers=out_headers)
    if method not in ("GET", "HEAD"):
        await _audit_async(st, user_id, "trinodal.proxy", "trinodal",
                           {"method": method, "class": T.path_class(method, rel), "path": rel[:200], "status": status,
                            "scope": need}, ip)
    return out


def _rewrite_location(loc: str, base: str) -> str:
    """Keep a redirect from Trinodal on this origin and under /trinodal/. An address outside it is replaced by the root of
    the UI rather than followed or exposed."""
    u = urlsplit(loc)
    if u.scheme or u.netloc:
        if f"{u.scheme}://{u.netloc}" != base:
            return "/trinodal/"
        loc = (u.path or "/") + (("?" + u.query) if u.query else "") + (("#" + u.fragment) if u.fragment else "")
        return "/trinodal" + loc if loc.startswith("/") else "/trinodal/" + loc
    if loc.startswith("/"):
        return "/trinodal" + loc if not loc.startswith("//") else "/trinodal/"
    return loc  # relative: resolved by the browser against /trinodal/...


class _Proxied(StreamingResponse):
    """Makes sure Trinodal's connection is closed however the response ends: finished, browser gone, cancelled, or never
    started (the browser left before the first byte, so the body generator never ran and its own clean-up cannot)."""

    def __init__(self, content, close, **kw):
        super().__init__(content, **kw)
        self._close = close

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                await self.body_iterator.aclose()
            except Exception:  # noqa: BLE001 - already finished or running; the explicit close below is what matters
                pass
            await self._close()


async def _chunks(up: httpx.Response, close, sse: bool, limit: int):
    """The upstream body as it arrives. Ends (and closes Trinodal's connection) when the browser goes away, when a limit is
    reached or when Trinodal finishes."""
    sent, started = 0, time.monotonic()
    ceiling = MAX_STREAM_SECONDS if sse else MAX_BODY_SECONDS
    try:
        async for chunk in up.aiter_raw():
            sent += len(chunk)
            if (not sse and sent > limit) or time.monotonic() - started > ceiling:
                break
            yield chunk
    except httpx.HTTPError:
        pass  # Trinodal dropped the connection: end the body
    finally:
        await close()
