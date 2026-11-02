"""/schemra/ : an authenticated reverse proxy to a Schemra running on this machine (docs/SCHEMRA_INTEGRATION.md).

Semechka signs the person in (its session cookie), maps their permissions to a Schemra role, and forwards the request,
streaming both ways, with signed identity headers. It imports nothing from Schemra and reads none of its data.
"""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass
from urllib.parse import quote

import anyio
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.requests import ClientDisconnect

from .. import audit
from .. import schemra_proxy as sp
from ..deps import Ctx, get_db, get_state, mfa_required_for, optional_ctx
from ..state import AppState

log = logging.getLogger("semechka.schemra")
router = APIRouter()
MAX_LIVE_PER_USER = 32  # open responses (live streams, downloads) one person may hold at once
_live: dict[str, int] = {}


@dataclass
class Gate:
    """Everything the route needs once the person is allowed in. Plain values only: no database session survives it."""
    user_id: str
    user: str
    role: str
    ip: str
    target: str  # path + query below the prefix, as Schemra will receive it
    path: str  # the same without the query: what the audit log keeps
    conn_origin: str
    netloc: str
    secret: bytes
    max_bytes: int
    read_timeout: float
    stream_max: int
    audit_level: str


# ---- answers ---------------------------------------------------------------------------------------------------
def _navigation(request: Request) -> bool:
    mode = request.headers.get("sec-fetch-mode")
    return request.method == "GET" and (mode == "navigate" or (mode is None and "text/html" in request.headers.get("accept", "")))


def _deny(request: Request, status: int, code: str, message: str, headers: dict | None = None) -> Response:
    if _navigation(request):
        body = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
                f"<title>Schemra</title><main style='font:16px/1.5 system-ui,sans-serif;max-width:34rem;margin:15vh auto;padding:0 1rem'>"
                f"<h1 style='font-size:1.25rem'>Schemra</h1><p>{html.escape(message)}</p><p><a href='/'>Back to Semechka</a></p></main>")
        return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store", **(headers or {})})
    return JSONResponse({"detail": {"code": code, "message": message}}, status_code=status,
                        headers={"Cache-Control": "no-store", **(headers or {})})


def _record(st: AppState, who: str, ip: str, action: str, role: str, method: str, path: str, status: int, reason: str = "") -> None:
    """One audit row: who, which Schemra role, method, path (no query), status. Never a body, header or secret."""
    detail = {"role": role, "method": method, "status": status, **({"reason": reason} if reason else {})}
    try:
        with st.db.session(write=True) as s:
            audit.record(s, who, action, path[:300], detail, ip)
    except Exception:
        log.exception("could not write the Schemra audit record")


def _wanted(level: str, method: str, path: str, status: int) -> bool:
    if status >= 400 or method not in sp.SAFE_METHODS or level == "all":
        return True
    return level == "api" and path.startswith("/api/")


# ---- the gate: everything decided before a byte is forwarded --------------------------------------------------------
def gate(request: Request, st: AppState = Depends(get_state), s=Depends(get_db, scope="function"),
         ctx: Ctx | None = Depends(optional_ctx)) -> Gate | Response:
    dep = st.settings.deployment
    if not dep(s, "schemra.enabled"):
        return _deny(request, 404, "not_found", "Not found.")  # the same answer as for a path that does not exist
    if ctx is None:
        if _navigation(request):
            nxt = request.url.path + (f"?{request.url.query}" if request.url.query else "")
            return RedirectResponse("/login?next=" + quote(nxt, safe="/"), status_code=302, headers={"Cache-Control": "no-store"})
        return _deny(request, 401, "unauthenticated", "Sign in to continue.")
    method = request.method
    who, ip = ctx.user.id, ctx.ip
    try:
        target = sp.upstream_target(request.scope.get("raw_path") or quote(request.url.path).encode(),
                                    request.scope.get("query_string", b""))
    except sp.PathRefused as e:
        _record(st, who, ip, "schemra.denied", "", method, request.url.path, 400, "path")
        return _deny(request, 400, "bad_path", str(e))
    path = target.split("?", 1)[0]

    def refuse(status: int, code: str, message: str, role: str = "", headers: dict | None = None) -> Response:
        _record(st, who, ip, "schemra.denied", role, method, path, status, code)
        return _deny(request, status, code, message, headers)

    if mfa_required_for(ctx) and not ctx.user.totp_enabled:
        return refuse(403, "mfa_enrolment_required", "Your organisation requires multi-factor authentication. Set it up to continue.")
    role = sp.schemra_role(ctx.principal)
    if role is None:
        return refuse(403, "forbidden", "Your account has no access to Schemra. Ask an administrator for the "
                      "'Open Schemra' permission (schemra.view, schemra.operate or schemra.admin).")
    if request.headers.get("upgrade"):
        return refuse(501, "no_upgrade", "Schemra is served over plain requests and streams; WebSocket upgrades are not supported.", role)
    bad = sp.csrf_check(method, request.headers, ctx.session.csrf, dep(s, "schemra.csrf"))
    if bad:
        return refuse(403, bad, "Cross-site request refused." if bad == "bad_origin" else
                      "Missing or invalid CSRF token. Reload the page.", role)
    if not st.limiter.hit(f"schemra:{who}", dep(s, "schemra.rate_per_minute"), 60):
        return refuse(429, "rate_limited", "Too many requests to Schemra. Wait a moment.", role, {"Retry-After": "10"})
    try:
        conn_origin, netloc = sp.upstream_origin(dep(s, "schemra.upstream"))
    except ValueError:  # a value that bypassed the settings validator
        log.error("schemra.upstream is not a loopback address; refusing to forward")
        return refuse(503, "misconfigured", "Schemra is not configured correctly. Tell an administrator.", role)
    try:
        secret = sp.load_secret(st.cfg.schemra_secret_path)
    except sp.SecretError as e:
        log.error("%s", e)  # names the file, never its content
        return refuse(503, "misconfigured", "Schemra is not configured correctly. Tell an administrator.", role)
    return Gate(who, sp.identity_name(ctx.user), role, ip, target, path, conn_origin, netloc, secret,
                dep(s, "schemra.max_upload_mb") * 1024 * 1024, float(dep(s, "schemra.read_timeout_s")),
                int(dep(s, "schemra.stream_max_seconds")), dep(s, "schemra.audit"))


# ---- streaming ---------------------------------------------------------------------------------------------------
class _TooLarge(Exception):
    pass


async def _limited_body(request: Request, limit: int):
    """The request body as it arrives, counted. Nothing is held back: the first chunk goes to Schemra before the last
    one has been sent by the browser."""
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            raise _TooLarge
        yield chunk


class ProxyResponse(Response):
    """Relays Schemra's response body chunk by chunk and ties the upstream request to the browser's connection: if the
    browser goes away, or the stream outlives its limit, the upstream response is closed straight away (Schemra frees
    what that stream held)."""

    def __init__(self, upstream: httpx.Response, client: httpx.AsyncClient, headers: list[tuple[str, str]], *,
                 live_key: str, max_seconds: int, head: bool):
        self.upstream, self.client, self.max_seconds, self.live_key, self.head = upstream, client, max_seconds, live_key, head
        self.status_code = upstream.status_code
        self.raw_headers = [(k.encode("latin-1"), v.encode("latin-1")) for k, v in headers]
        self.background = None

    async def __call__(self, scope, receive, send):
        _live[self.live_key] = _live.get(self.live_key, 0) + 1
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            if self.head or self.status_code in (204, 304):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
                return
            async with anyio.create_task_group() as tg:
                async def watch_disconnect():
                    while (await receive())["type"] != "http.disconnect":
                        pass
                    tg.cancel_scope.cancel()

                tg.start_soon(watch_disconnect)
                limit = anyio.move_on_after(self.max_seconds if self.max_seconds > 0 else None)
                try:
                    with limit:
                        async for chunk in self.upstream.aiter_raw():
                            await send({"type": "http.response.body", "body": chunk, "more_body": True})
                    if tg.cancel_scope.cancel_called:
                        return  # the browser is gone
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
                except OSError:
                    pass  # the browser is gone (servers that raise on send instead of sending http.disconnect)
                finally:
                    tg.cancel_scope.cancel()
        finally:
            with anyio.CancelScope(shield=True):
                await self.upstream.aclose()
                await self.client.aclose()
            n = _live.get(self.live_key, 1) - 1
            if n > 0:
                _live[self.live_key] = n
            else:
                _live.pop(self.live_key, None)


METHODS = list(sp.PROXY_METHODS)


@router.api_route("/schemra", methods=METHODS, include_in_schema=False)
async def schemra_root(request: Request, g=Depends(gate)):
    if isinstance(g, Response):
        return g
    return RedirectResponse("/schemra/" + (f"?{request.url.query}" if request.url.query else ""), status_code=308)


@router.api_route("/schemra/{path:path}", methods=METHODS, include_in_schema=False)
async def schemra_proxy(request: Request, g=Depends(gate), st: AppState = Depends(get_state)):
    if isinstance(g, Response):
        return g
    method = request.method

    async def done(status: int, reason: str = "") -> None:
        if reason or _wanted(g.audit_level, method, g.path, status):
            await asyncio.to_thread(_record, st, g.user_id, g.ip, "schemra.request", g.role, method, g.path, status, reason)

    def fail(status: int, code: str, message: str) -> Response:
        return _deny(request, status, code, message)

    if _live.get(g.user_id, 0) >= MAX_LIVE_PER_USER:
        await done(429, "too_many_open")
        return fail(429, "too_many_open", "Too many Schemra connections are open for your account.")
    cl, has_body = request.headers.get("content-length"), False
    if cl is not None:
        if not cl.isdigit():
            await done(400, "bad_length")
            return fail(400, "bad_request", "Invalid Content-Length.")
        if int(cl) > g.max_bytes:
            await done(413, "too_large")
            return fail(413, "too_large", f"The upload is larger than the {g.max_bytes // 1048576} MB limit.")
        has_body = int(cl) > 0
    elif "transfer-encoding" in request.headers:
        has_body = True

    headers = sp.request_headers(request.headers.items(), upstream_netloc=g.netloc)
    if has_body and cl is not None:
        headers.append(("content-length", cl))
    headers += sp.identity_headers(g.secret, method, g.target, g.user, g.role)
    client = httpx.AsyncClient(timeout=sp.make_timeout(g.read_timeout), trust_env=False, follow_redirects=False,
                               limits=httpx.Limits(max_connections=4, max_keepalive_connections=0))
    url = httpx.URL(g.conn_origin).copy_with(raw_path=g.target.encode("ascii"))
    req = client.build_request(method, url, headers=headers, content=_limited_body(request, g.max_bytes) if has_body else None)
    try:
        resp = await client.send(req, stream=True)
    except _TooLarge:
        await client.aclose()
        await done(413, "too_large")
        return fail(413, "too_large", f"The upload is larger than the {g.max_bytes // 1048576} MB limit.")
    except ClientDisconnect:
        await client.aclose()
        return Response(status_code=499)
    except (httpx.TimeoutException, httpx.HTTPError) as e:
        await client.aclose()
        timed_out = isinstance(e, httpx.TimeoutException) and not isinstance(e, httpx.ConnectTimeout)
        log.warning("schemra upstream %s (%s)", "timed out" if timed_out else "unreachable", type(e).__name__)
        status = 504 if timed_out else 502
        await done(status, "upstream_timeout" if timed_out else "upstream_unreachable")
        return fail(status, "upstream_timeout" if timed_out else "upstream_unreachable",
                    "Schemra did not answer in time." if timed_out else "Schemra is not reachable right now.")
    except BaseException:
        await client.aclose()
        raise
    stream = sp.is_event_stream(resp.headers.get("content-type", ""))
    out = sp.response_headers(list(resp.headers.multi_items()), upstream_netloc=g.netloc, conn_netloc=g.conn_origin[7:], stream=stream,
                             api=g.path.startswith("/api/"))
    if out is None:
        await resp.aclose()
        await client.aclose()
        await done(502, "bad_redirect")
        return fail(502, "bad_redirect", "Schemra answered with a redirect that leaves this address.")
    await done(resp.status_code)
    return ProxyResponse(resp, client, out, live_key=g.user_id, max_seconds=g.stream_max if stream else 0, head=method == "HEAD")
