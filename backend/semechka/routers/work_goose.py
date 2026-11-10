"""Work Mode on upstream Goose: REST + SSE (docs/WORK_MODE_GOOSE.md). Owner-only; the URL key is the conversation id."""
from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import goose_svc, work_runtime
from ..deps import Ctx, current

router = APIRouter(prefix="/api/work/goose")

SAFE_INLINE = {"image/png", "image/jpeg", "image/webp", "image/gif", "text/plain", "text/markdown", "text/csv", "application/json",
               "application/pdf", "text/x-python", "text/yaml", "text/javascript"}


def call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except goose_svc.GooseError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    except work_runtime.RuntimeError_ as e:
        raise HTTPException(502, {"code": "runtime", "message": str(e)}) from None


def _gate(ctx: Ctx) -> goose_svc.Cfg:
    return call(goose_svc.gate, ctx.st, ctx.s, ctx.principal)


def _row(ctx: Ctx, cid: str):
    _gate(ctx)
    return call(goose_svc.get_row, ctx.s, ctx.user.id, cid)


@router.get("/status")
def status(ctx: Ctx = Depends(current)):
    return call(goose_svc.status, ctx.st, ctx.s, ctx.user, ctx.principal)


class SessionIn(BaseModel):
    model: str | None = None
    autonomy: str | None = None
    mcp: list[str] | None = None
    skills: list[str] | None = None


@router.post("/conversations/{cid}/session")
def create_session(cid: str, body: SessionIn | None = None, ctx: Ctx = Depends(current)):
    row = call(goose_svc.ensure_session, ctx.st, ctx.s, ctx.user, ctx.principal, cid, (body.model_dump(exclude_unset=True) if body else {}), ctx.ip)
    ctx.s.flush()
    return goose_svc.session_out(ctx.st, ctx.s, row)


@router.get("/conversations/{cid}/session")
def get_session(cid: str, ctx: Ctx = Depends(current)):
    return goose_svc.session_out(ctx.st, ctx.s, _row(ctx, cid))


@router.patch("/conversations/{cid}/session")
def patch_session(cid: str, body: SessionIn, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    call(goose_svc.update_session, ctx.st, ctx.s, ctx.user, ctx.principal, row, body.model_dump(exclude_unset=True), ctx.ip)
    return goose_svc.session_out(ctx.st, ctx.s, row)


@router.post("/conversations/{cid}/session/stop")
def stop_session(cid: str, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    ctx.s.commit()
    call(goose_svc.stop, ctx.st, ctx.s, ctx.user, row, ctx.ip)
    return goose_svc.session_out(ctx.st, ctx.s, row)


@router.delete("/conversations/{cid}/session")
def delete_session(cid: str, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    ctx.s.commit()
    call(goose_svc.delete, ctx.st, ctx.s, ctx.user, row, ctx.ip)
    return {"ok": True}


class PromptIn(BaseModel):
    text: str = Field(min_length=1, max_length=goose_svc.MAX_PROMPT)
    attachments: list[str] | None = None


@router.post("/conversations/{cid}/prompt", status_code=202)
def prompt(cid: str, body: PromptIn, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    return call(goose_svc.prompt, ctx.st, ctx.s, ctx.user, ctx.principal, row, body.text, ctx.ip)


@router.post("/conversations/{cid}/cancel")
def cancel(cid: str, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    ctx.s.commit()
    call(goose_svc.cancel, ctx.st, ctx.s, ctx.user, row, ctx.ip)
    return goose_svc.session_out(ctx.st, ctx.s, row)


class ApprovalIn(BaseModel):
    decision: str


@router.post("/conversations/{cid}/approvals/{approval_id}")
def approval(cid: str, approval_id: str, body: ApprovalIn, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    ctx.s.commit()   # the turn thread is waiting on this answer and writes events: do not hold the database lock meanwhile
    call(goose_svc.approve, ctx.st, ctx.s, ctx.user, row, approval_id, body.decision, ctx.ip)
    return {"ok": True}


# ---- events -----------------------------------------------------------------------------------------------------
def _poll(st, org_user: tuple[str, str], cid: str, since: int, limit: int = 500):
    with st.db.session() as s:
        row = goose_svc.get_row(s, org_user[0], cid)
        return goose_svc.events_since(st, s, row, since, limit), goose_svc.session_out(st, s, row)["state"]


@router.get("/conversations/{cid}/events")
async def events(cid: str, request: Request, since: int = Query(0, ge=0), format: str | None = None, wait: float = Query(0, ge=0, le=25),
                 ctx: Ctx = Depends(current)):
    _row(ctx, cid)
    st, uid = ctx.st, ctx.user.id
    ctx.s.commit()  # a stream must not hold a connection
    last = since
    lid = request.headers.get("last-event-id")
    if lid and lid.isdigit():
        last = max(last, int(lid))
    accept = request.headers.get("accept", "")
    as_json = format == "json" or ("application/json" in accept and "text/event-stream" not in accept)
    if as_json:
        deadline = time.monotonic() + wait
        while True:
            try:
                evs, state = await asyncio.to_thread(_poll, st, (uid, ""), cid, last)
            except goose_svc.GooseError as e:
                raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
            if evs or time.monotonic() >= deadline:
                return {"events": evs, "last_seq": evs[-1]["seq"] if evs else last, "state": state}
            await asyncio.sleep(0.3)

    async def gen():
        pos, ping = last, time.monotonic()
        yield "retry: 2000\n\n"
        while True:
            if await request.is_disconnected():
                return
            try:
                evs, _state = await asyncio.to_thread(_poll, st, (uid, ""), cid, pos)
            except goose_svc.GooseError:
                return
            for e in evs:
                pos = e["seq"]
                yield f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e, separators=(',', ':'), default=str)}\n\n"
            if evs:
                ping = time.monotonic()
                continue
            if time.monotonic() - ping > 15:
                yield ": ping\n\n"
                ping = time.monotonic()
            await asyncio.sleep(0.25)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/conversations/{cid}/transcript")
def transcript(cid: str, ctx: Ctx = Depends(current)):
    return goose_svc.transcript(ctx.st, ctx.s, _row(ctx, cid))


@router.get("/conversations/{cid}/changes/{turn_id}")
def changes(cid: str, turn_id: str, ctx: Ctx = Depends(current)):
    row = _row(ctx, cid)
    d = goose_svc.read_changes(ctx.st, row, turn_id)
    if d is None:
        raise HTTPException(404, {"code": "not_found", "message": "No changes recorded for that prompt."})
    return {"turn_id": turn_id, **d}


# ---- files ----------------------------------------------------------------------------------------------------------
def _norm(path: str) -> str:
    from ..agent_svc import ToolError
    from ..work_tools import norm_path
    try:
        return norm_path(path)
    except ToolError as e:
        raise HTTPException(422, {"code": "path", "message": str(e)}) from None


def _container(ctx: Ctx, cid: str):
    row = _row(ctx, cid)
    c = goose_svc.cfg(ctx.st, ctx.s)
    ctx.s.commit()
    live = goose_svc.get_live(ctx.st, row)
    name = call(live.ensure_container, c)
    return goose_svc.runtime(ctx.st, c), name, c


@router.get("/conversations/{cid}/files")
def files(cid: str, path: str = "", ctx: Ctx = Depends(current)):
    rel = _norm(path)
    rt, name, _c = _container(ctx, cid)
    try:
        ents = rt.list(name, rel)
    except work_runtime.RuntimeError_ as e:
        raise HTTPException(502, {"code": "runtime", "message": str(e)}) from None
    prefix = (rel + "/") if rel else ""
    return {"path": rel, "entries": [{"name": e.name, "path": prefix + e.name, "type": "dir" if e.type == "dir" else "file", "size": e.size,
                                       "modified": e.mtime} for e in ents if not (not rel and e.name == ".home")]}


@router.get("/conversations/{cid}/files/content")
def file_content(cid: str, path: str, download: bool = False, ctx: Ctx = Depends(current)):
    from ..work_tools import guess_mime
    rel = _norm(path)
    if not rel:
        raise HTTPException(422, {"code": "path", "message": "A file path is required."})
    rt, name, c = _container(ctx, cid)
    try:
        info = rt.stat(name, rel)
        if not info.get("exists"):
            raise HTTPException(404, {"code": "not_found", "message": "No such file."})
        if info.get("type") != "file":
            raise HTTPException(422, {"code": "path", "message": "That is not a file."})
        cap = 32 * 1048576
        if info["size"] > cap:
            raise HTTPException(413, {"code": "too_large", "message": "Files over 32 MB cannot be downloaded here."})
        data, _ = rt.get_file(name, rel, limit=cap)
    except work_runtime.RuntimeError_ as e:
        raise HTTPException(502, {"code": "runtime", "message": str(e)}) from None
    mime = guess_mime(rel)
    inline = (not download) and mime in SAFE_INLINE
    safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in rel.rsplit("/", 1)[-1])[:100] or "file"
    return Response(data, media_type=mime if inline else "application/octet-stream",
                    headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe}"',
                             "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})


@router.put("/conversations/{cid}/files")
def upload(cid: str, path: str, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    rel = _norm(path)
    if not rel or rel.endswith("/"):
        raise HTTPException(422, {"code": "path", "message": "Give the file's path inside the workspace."})
    c = goose_svc.cfg(ctx.st, ctx.s)
    cap = c.upload_mb * 1048576
    data = file.file.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(413, {"code": "too_large", "message": f"Uploads can be at most {c.upload_mb} MB."})
    rt, name, _c = _container(ctx, cid)
    try:
        head = rt.put_file(name, rel, data)
    except work_runtime.RuntimeError_ as e:
        raise HTTPException(502, {"code": "runtime", "message": str(e)}) from None
    return {"path": rel, "size": len(data), "sha256": head["sha256"]}


@router.delete("/conversations/{cid}/files")
def delete_file(cid: str, path: str, ctx: Ctx = Depends(current)):
    rel = _norm(path)
    if not rel:
        raise HTTPException(422, {"code": "path", "message": "Refusing to delete the workspace root."})
    rt, name, _c = _container(ctx, cid)
    try:
        return {"deleted": rt.delete(name, rel)}
    except work_runtime.PathRefused as e:
        raise HTTPException(404 if "no such" in str(e) else 422, {"code": "path", "message": str(e)}) from None
    except work_runtime.RuntimeError_ as e:
        raise HTTPException(502, {"code": "runtime", "message": str(e)}) from None
