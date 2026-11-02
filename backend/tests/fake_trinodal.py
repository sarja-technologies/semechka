"""A Trinodal AI test double: the management UI, JSON API and event streams that Semechka serves at /trinodal/.

It is a test double for the network protocol only. It requires `Authorization: Bearer <TOKEN>`, records every request
(method, path, query, headers, body size and a body digest, never used for anything else) so tests can prove what crossed
the proxy, and sends the kinds of response headers Semechka must not pass on (cookies, CSP, CORS, server banners).
"""
import asyncio
import hashlib

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

TOKEN = "tnl-service-token-5f3a9c0d17e24b68"

app = FastAPI()
STATE: dict = {}


def reset():
    STATE.clear()
    STATE.update(calls=[], sse_started=0, sse_closed=0, upload_closed=0)


reset()


async def auth(request: Request):
    # recorded before the check, so a test can also see what an unauthenticated caller would have sent
    STATE["calls"].append({"method": request.method, "path": request.url.path, "query": request.url.query,
                           "headers": {k.lower(): v for k, v in request.headers.items()}, "size": None, "sha": None})
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad service token")


def last() -> dict:
    return STATE["calls"][-1]


async def read_body(request: Request) -> bytes:
    data = b""
    async for chunk in request.stream():
        data += chunk
    last()["size"], last()["sha"] = len(data), hashlib.sha256(data).hexdigest()
    return data


HOSTILE = {"Set-Cookie": "trinodal=1; Path=/", "Content-Security-Policy": "default-src *; script-src 'unsafe-inline' *",
           "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Credentials": "true", "Server": "trinodal/9",
           "X-Frame-Options": "SAMEORIGIN", "Strict-Transport-Security": "max-age=1", "X-Secret-Internal": "nope",
           "X-Trinodal-Build": "7"}


@app.get("/", dependencies=[Depends(auth)])
def index():
    return HTMLResponse('<!doctype html><title>Trinodal</title><script src="app.js"></script><div id="app"></div>',
                        headers={**HOSTILE, "ETag": '"idx1"', "Cache-Control": "public, max-age=3600"})


@app.head("/", dependencies=[Depends(auth)])
def index_head():
    return Response(headers={"content-length": "321", "content-type": "text/html; charset=utf-8", "ETag": '"idx1"'})


@app.get("/app.js", dependencies=[Depends(auth)])
def js():
    return Response("console.log('trinodal');", media_type="text/javascript", headers={"Cache-Control": "max-age=60"})


@app.get("/api/network", dependencies=[Depends(auth)])
def network():
    return {"nodes": [{"id": "conversation"}, {"id": "mathematics"}], "edges": [["conversation", "mathematics"]]}


@app.get("/api/training/jobs", dependencies=[Depends(auth)])
def jobs():
    return {"jobs": [{"id": "j1", "state": "running"}]}


async def echo(request: Request):
    data = await read_body(request)
    return JSONResponse({"received": len(data)}, status_code=202)


for _method, _path in (("post", "/api/training/jobs"), ("post", "/api/datasets"), ("put", "/api/checkpoints/ck1"),
                       ("delete", "/api/jobs/j1"), ("post", "/api/deploy"), ("post", "/api/evaluate"),
                       ("patch", "/api/other"), ("post", "/api/admin/settings"), ("delete", "/api/admin/tokens/t1")):
    getattr(app, _method)(_path, dependencies=[Depends(auth)])(echo)


@app.get("/api/admin/tokens", dependencies=[Depends(auth)])
def tokens():
    return {"tokens": [{"id": "t1", "scopes": ["read"]}]}


@app.get("/api/reject", dependencies=[Depends(auth)])
def reject():
    return JSONResponse({"detail": "token rejected"}, status_code=401)


@app.get("/api/forbidden", dependencies=[Depends(auth)])
def forbidden():
    return JSONResponse({"detail": "scope missing"}, status_code=403)


@app.get("/redir-in", dependencies=[Depends(auth)])
def redir_in():
    return Response(status_code=302, headers={"Location": "/api/network"})


@app.get("/redir-out", dependencies=[Depends(auth)])
def redir_out():
    return Response(status_code=302, headers={"Location": "https://evil.example/steal"})


@app.get("/big", dependencies=[Depends(auth)])
def big():
    return Response(b"x" * (2 * 1024 * 1024), media_type="application/octet-stream")


@app.get("/big-stream", dependencies=[Depends(auth)])
def big_stream():
    async def gen():
        for _ in range(64):
            yield b"y" * 65536  # 4 MB with no Content-Length
    return StreamingResponse(gen(), media_type="application/octet-stream")


@app.get("/api/events", dependencies=[Depends(auth)])
async def events(request: Request):
    """Server-sent events: one at once, then one every 0.2 s until the reader goes away (recorded in sse_closed)."""
    STATE["sse_started"] += 1

    async def gen():
        try:
            yield "event: hello\ndata: {\"n\": 0}\n\n"
            for i in range(1, 600):
                await asyncio.sleep(0.2)
                yield f"data: {{\"n\": {i}}}\n\n"
        finally:
            STATE["sse_closed"] += 1
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "max-age=999", **HOSTILE})


@app.get("/api/sse-gate", dependencies=[Depends(auth)])
async def sse_gate():
    """First event at once, the second only after 2 s: a proxy that buffers delivers both at the end."""
    async def gen():
        yield "data: first\n\n"
        await asyncio.sleep(2.0)
        yield "data: second\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
