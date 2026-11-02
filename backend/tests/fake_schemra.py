"""A stand-in for the Schemra lab, as far as the Semechka proxy can see it: plain HTTP on loopback.

It records every request exactly as received (raw request target, headers, body size) and offers routes that exercise
the proxy: an echo, a live stream that notices when its reader goes away, an upload sink that notes when each chunk
arrived, redirects, a response full of headers the proxy must not pass on, and a route that never answers.
It holds no Schemra code and verifies nothing itself: the tests re-implement the signature check independently.
"""
from __future__ import annotations

import asyncio
import json
import time

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

STATE: dict = {}


def reset():
    STATE.clear()
    STATE.update(received=[], sse_open=0, sse_closed=0, sse_sent=0, upload_chunks=[], upload_bytes=0, big_sent=0, big_closed=0, port=0)


reset()


def _record(request: Request) -> dict:
    rec = {"method": request.method, "target": request.scope["raw_path"].decode() + (
        "?" + request.scope["query_string"].decode() if request.scope["query_string"] else ""),
        "headers": [(k.decode("latin-1"), v.decode("latin-1")) for k, v in request.scope["headers"]]}
    STATE["received"].append(rec)
    return rec


async def echo(request: Request):
    rec = _record(request)
    body = await request.body()
    return JSONResponse({**rec, "body_len": len(body)})


async def whoami(request: Request):
    """What a lab in proxy mode would answer: the identity it was handed (not verified here; the tests verify)."""
    _record(request)
    role = request.headers.get("x-schemra-role", "")
    perms = {"viewer": ["read"], "operator": ["read", "chat", "board.write"], "admin": ["read", "chat", "board.write", "admin"]}
    return JSONResponse({"user": request.headers.get("x-schemra-user", ""), "role": role, "via": "proxy", "permissions": perms.get(role, [])})


async def events(request: Request):
    _record(request)
    STATE["sse_open"] += 1

    async def gen():
        try:
            yield b": hello\n\n"
            n = 0
            while True:
                n += 1
                STATE["sse_sent"] = n
                yield f"id: {n}\ndata: {json.dumps({'n': n})}\n\n".encode()
                await asyncio.sleep(0.1)
        finally:  # runs when the reader (the proxy) disconnects
            STATE["sse_closed"] += 1

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


async def upload(request: Request):
    rec = _record(request)
    t0 = time.monotonic()
    async for chunk in request.stream():
        STATE["upload_chunks"].append((time.monotonic() - t0, len(chunk)))
        STATE["upload_bytes"] += len(chunk)
    return JSONResponse({"bytes": STATE["upload_bytes"], "target": rec["target"]})


async def big(request: Request):
    _record(request)
    n = int(request.query_params.get("mb", "20"))

    async def gen():
        try:
            for _ in range(n):
                STATE["big_sent"] += 1
                yield b"x" * 1048576
                await asyncio.sleep(0.01)
        finally:
            STATE["big_closed"] += 1

    return StreamingResponse(gen(), media_type="application/octet-stream", headers={"Content-Length": str(n * 1048576)})


async def redirect_rel(request: Request):
    _record(request)
    return Response(status_code=302, headers={"Location": "/api/lab/boards?x=1"})


async def redirect_own(request: Request):
    _record(request)
    return Response(status_code=302, headers={"Location": f"http://127.0.0.1:{STATE['port']}/api/lab/boards"})


async def redirect_prefixed(request: Request):
    _record(request)
    return Response(status_code=302, headers={"Location": "/schemra/already"})


async def redirect_external(request: Request):
    _record(request)
    return Response(status_code=302, headers={"Location": "https://evil.example/phish"})


async def noisy(request: Request):
    """Everything a lab must not be able to do to Semechka's origin."""
    _record(request)
    resp = Response("ok")
    resp.raw_headers = [(k.lower().encode(), v.encode()) for k, v in [
        ("Content-Length", "2"), ("Set-Cookie", "smk_hijack=1; Path=/"), ("Set-Cookie", "other=2"), ("Access-Control-Allow-Origin", "*"),
        ("Access-Control-Allow-Credentials", "true"), ("X-Powered-By", "fake-lab:8820"), ("Via", "1.1 lab"),
        ("Link", "<http://127.0.0.1:8820/x>; rel=preload"), ("Refresh", "0; url=http://127.0.0.1:8820/"),
        ("Cache-Control", "public, max-age=600"), ("X-Frame-Options", "SAMEORIGIN"), ("X-Schemra-Version", "2.0"),
        ("Permissions-Policy", "camera=*"), ("Content-Security-Policy", "default-src 'self'; script-src 'self'")]]
    return resp


async def plain_html(request: Request):
    _record(request)
    return Response("<!doctype html><title>lab</title>", media_type="text/html")


async def hang(request: Request):
    _record(request)
    await asyncio.sleep(30)
    return Response("late")


async def status(request: Request):
    _record(request)
    return JSONResponse({"detail": "nope"}, status_code=int(request.path_params["code"]))


app = Starlette(routes=[
    Route("/", plain_html, methods=["GET"]),
    Route("/api/lab/echo", echo, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]),
    Route("/api/lab/whoami", whoami, methods=["GET"]),
    Route("/api/lab/boards", echo, methods=["GET", "POST"]),
    Route("/api/lab/boards/{bid}/events", events, methods=["GET"]),
    Route("/api/lab/upload", upload, methods=["POST", "PUT"]),
    Route("/api/lab/big", big, methods=["GET"]),
    Route("/api/lab/status/{code:int}", status, methods=["GET"]),
    Route("/r/rel", redirect_rel), Route("/r/own", redirect_own), Route("/r/prefixed", redirect_prefixed),
    Route("/r/external", redirect_external),
    Route("/noisy", noisy), Route("/hang", hang),
    Route("/{rest:path}", echo, methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"]),
])
