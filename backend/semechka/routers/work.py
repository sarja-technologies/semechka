"""Work Mode REST API (docs/WORK_MODE.md section 6). Every route: session-cookie auth + CSRF (`current`), the Work mode gate, and
owner-only access to sessions, files and artifacts; someone else's session answers 404 exactly like a missing one."""
from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import audit, services, work_runtime, work_svc
from ..deps import Ctx, current
from ..models_work import WorkArtifact, WorkSession
from ..tenancy_svc import TenancyError

def mode_gate(ctx: Ctx = Depends(current)) -> Ctx:
    """Router-level guard, so it also answers before a request body is parsed (an upload without a file is 403, not 422)."""
    state = services.mode_state(ctx.s, ctx.principal, ctx.st, "work")
    if not state["enabled"]:
        code = {"disabled": 403, "forbidden": 403, "no_model": 409, "unsupported": 501}.get(state.get("code"), 403)
        raise HTTPException(code, {"code": state.get("code", "forbidden"), "message": state["reason"]})
    return ctx


router = APIRouter(prefix="/api/work", dependencies=[Depends(mode_gate)])

SAFE_INLINE = {"image/png", "image/jpeg", "image/webp", "image/gif", "video/mp4", "video/webm", "text/plain", "text/markdown", "text/csv",
               "application/json", "application/pdf", "text/x-python", "text/yaml", "text/javascript"}


def _err(e: Exception) -> HTTPException:
    if isinstance(e, TenancyError):
        return HTTPException(e.status, {"code": e.code, "message": e.message})
    if isinstance(e, work_runtime.PathRefused):
        return HTTPException(422, {"code": "path", "message": str(e)})
    if isinstance(e, work_runtime.RuntimeError_):
        return HTTPException(502, {"code": "runtime", "message": work_svc_scrub(str(e))})
    raise e


def work_svc_scrub(text: str) -> str:
    from ..work_tools import scrub
    return scrub(text)


def call(fn, *a, **k):
    try:
        return fn(*a, **k)
    except (TenancyError, work_runtime.RuntimeError_) as e:
        raise _err(e) from None


def gate(ctx: Ctx) -> None:
    """Every route is already behind `mode_gate`; kept as an explicit call where a handler re-checks after a long wait."""
    mode_gate(ctx)


def own(ctx: Ctx, sid: str) -> WorkSession:
    gate(ctx)
    return call(work_svc.get_session, ctx.s, ctx.user.id, sid)


# ---- options ------------------------------------------------------------------------------------------------
def _image_ready(ctx: Ctx) -> tuple[bool, str]:
    cache = ctx.st.__dict__.setdefault("work_image_cache", {})
    hit = cache.get("v")
    if hit and time.time() - hit[0] < 30:
        return hit[1], hit[2]
    c = work_svc.cfg(ctx.st, ctx.s)
    try:
        ok, why = work_svc.runtime(ctx.st).image_ready(c.image)
    except work_runtime.RuntimeError_ as e:
        ok, why = False, str(e)
    cache["v"] = (time.time(), ok, why if not ok else "")
    return ok, why if not ok else ""


@router.get("/options")
def options(ctx: Ctx = Depends(current)):
    gate(ctx)
    from .. import catalog_svc, chat_svc
    c = work_svc.cfg(ctx.st, ctx.s)
    models = []
    for m, b in services.accessible_models(ctx.s, ctx.principal, ctx.st, "work", include_hidden=False):
        caps = catalog_svc.capabilities(m)
        n_ctx, _ = chat_svc.window_for(ctx.st, m)
        models.append({"id": m.id, "alias": m.alias, "tools": True, "vision": "vision" in caps, "context": n_ctx,
                       "ready": bool(m.available and b.enabled), "loaded": (m.meta or {}).get("loaded"),
                       "external": services.is_external(m, b)})
    ok, why = _image_ready(ctx)
    ceil = {"memory_mb": c.memory_mb, "cpus": c.cpus, "workspace_mb": c.workspace_mb, "tool_timeout_s": c.tool_timeout_s, "max_steps": c.max_steps,
            "pids": c.pids, "artifact_mb": c.artifact_mb, "upload_mb": c.upload_mb}
    return {"models": models, "skills": [], "limits": ceil, "network_allowed": c.network_allowed,
            "perms": {"network": ctx.principal.can("work.network"), "skills_manage": ctx.principal.can("work.skills.manage")},
            "autonomy": list(work_svc.AUTONOMY), "image_ready": ok, "image_problem": why, "image": c.image}


# ---- sessions -------------------------------------------------------------------------------------------------
class SessionIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    objective: str | None = Field(default=None, max_length=20000)
    model_id: str | None = None
    skill_ids: list[str] | None = None
    network: str | None = None
    autonomy: str | None = None
    limits: dict | None = None


@router.post("/sessions")
def create(body: SessionIn, ctx: Ctx = Depends(current)):
    gate(ctx)
    sess = call(work_svc.create_session, ctx.st, ctx.s, ctx.user, ctx.principal, title=body.title or "", objective=body.objective or "",
                model_id=body.model_id, skill_ids=body.skill_ids, network=body.network or "none", autonomy=body.autonomy or "contained",
                limits=body.limits, ip=ctx.ip)
    return work_svc.session_out(ctx.st, ctx.s, sess)


@router.get("/sessions")
def list_sessions(limit: int = Query(50, ge=1, le=200), ctx: Ctx = Depends(current)):
    gate(ctx)
    rows = ctx.s.execute(select(WorkSession).where(WorkSession.user_id == ctx.user.id, WorkSession.deleted_at.is_(None))
                         .order_by(WorkSession.updated_at.desc()).limit(limit)).scalars().all()
    c = work_svc.cfg(ctx.st, ctx.s)
    return [work_svc.session_out(ctx.st, ctx.s, x, c) for x in rows]


@router.get("/sessions/{sid}")
def get(sid: str, ctx: Ctx = Depends(current)):
    return work_svc.session_out(ctx.st, ctx.s, own(ctx, sid))


@router.patch("/sessions/{sid}")
def patch(sid: str, body: SessionIn, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    call(work_svc.update_session, ctx.st, ctx.s, ctx.user, ctx.principal, sess, body.model_dump(exclude_unset=True), ctx.ip)
    return work_svc.session_out(ctx.st, ctx.s, sess)


@router.delete("/sessions/{sid}")
def delete(sid: str, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    name = call(work_svc.delete_session, ctx.st, ctx.s, ctx.user, sess, ctx.ip, remove_now=False)
    ctx.s.commit()  # the database lock is released before the container runtime is asked to do its (slow) work
    removed = work_svc.remove_container(ctx.st, sid, name)
    return {"ok": True, "container_removed": removed}


# ---- messages and run control ---------------------------------------------------------------------------------
class MessageIn(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


@router.post("/sessions/{sid}/messages")
def message(sid: str, body: MessageIn, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    return call(work_svc.post_message, ctx.st, ctx.s, ctx.user, ctx.principal, sess, body.content, ctx.ip)


@router.post("/sessions/{sid}/pause")
def pause(sid: str, ctx: Ctx = Depends(current)):
    return work_svc.session_out(ctx.st, ctx.s, call(work_svc.pause, ctx.st, ctx.s, ctx.user, own(ctx, sid)))


@router.post("/sessions/{sid}/resume")
def resume(sid: str, ctx: Ctx = Depends(current)):
    return work_svc.session_out(ctx.st, ctx.s, call(work_svc.resume, ctx.st, ctx.s, ctx.user, own(ctx, sid)))


@router.post("/sessions/{sid}/cancel")
def cancel(sid: str, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    call(work_svc.cancel, ctx.st, ctx.s, ctx.user, sess)
    out = work_svc.session_out(ctx.st, ctx.s, sess)
    ctx.s.commit()
    work_svc.interrupt_commands(ctx.st, sess)  # after the cancel is durable, stop whatever the container is running
    return out


class ApproveIn(BaseModel):
    approve: bool


@router.post("/sessions/{sid}/approvals/{step}")
def approve(sid: str, step: int, body: ApproveIn, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    stp = call(work_svc.decide_approval, ctx.st, ctx.s, ctx.user, sess, step, body.approve)
    return {"step": stp.n, "status": stp.status}


# ---- timeline and state ----------------------------------------------------------------------------------------
def _poll(st, user_id: str, sid: str, since: int, limit: int) -> dict:
    with st.db.session() as s:
        sess = work_svc.get_session(s, user_id, sid)
        evs = work_svc.events_since(st, s, sess, since, limit)
        return {"events": evs, "last_id": evs[-1]["id"] if evs else since, "status": sess.status, "container_state": sess.container_state}


@router.get("/sessions/{sid}/events")
async def events(sid: str, since: int = Query(0, ge=0), wait: float = Query(0, ge=0, le=25), limit: int = Query(200, ge=1, le=500),
                 ctx: Ctx = Depends(current)):
    own(ctx, sid)
    st, uid = ctx.st, ctx.user.id
    ctx.s.commit()  # a long poll must not hold a connection for 25 seconds
    deadline = time.monotonic() + wait
    while True:
        try:
            out = await asyncio.to_thread(_poll, st, uid, sid, since, limit)
        except TenancyError as e:
            raise _err(e) from None
        if out["events"] or time.monotonic() >= deadline:
            return out
        await asyncio.sleep(0.4)


@router.get("/sessions/{sid}/state")
def state(sid: str, live: bool = False, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    return call(work_svc.state_of, ctx.st, ctx.s, sess, live=live)


# ---- files -----------------------------------------------------------------------------------------------------
def _norm(path: str) -> str:
    from ..agent_svc import ToolError
    from ..work_tools import norm_path
    try:
        return norm_path(path)
    except ToolError as e:
        raise HTTPException(422, {"code": "path", "message": str(e)}) from None


def _container(ctx: Ctx, sess: WorkSession, create: bool = True) -> str | None:
    return call(work_svc.ensure_running, ctx.st, sess.id, create=create and (sess.has_workspace or create))


@router.get("/sessions/{sid}/files")
def files(sid: str, path: str = "", ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    rel = _norm(path)
    if not sess.has_workspace:
        return {"path": rel, "entries": [], "container_state": sess.container_state}
    sid_, uid = sess.id, ctx.user.id
    ctx.s.commit()
    name = call(work_svc.ensure_running, ctx.st, sid_)
    try:
        ents = work_svc.runtime(ctx.st).list(name, rel)
    except (work_runtime.RuntimeError_,) as e:
        raise _err(e) from None
    prefix = (rel + "/") if rel else ""
    return {"path": rel, "container_state": "running", "entries": [
        {"name": e.name, "type": e.type, "size": e.size, "mtime": e.mtime, "path": prefix + e.name, "link_ok": e.link_ok} for e in ents
        if not (not rel and e.name == ".home")]}  # the account's home folder (tool caches) is not part of the person's files


def _file_response(data: bytes, mime: str, name: str, download: bool) -> Response:
    inline = (not download) and mime in SAFE_INLINE
    safe_name = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name)[:100] or "file"
    return Response(data, media_type=mime if inline else "application/octet-stream" if mime not in SAFE_INLINE else mime,
                    headers={"Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe_name}"',
                             "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff",
                             "Cache-Control": "private, no-store"})


@router.get("/sessions/{sid}/files/content")
def file_content(sid: str, path: str, download: bool = False, ctx: Ctx = Depends(current)):
    from ..work_tools import guess_mime
    sess = own(ctx, sid)
    rel = _norm(path)
    if not rel:
        raise HTTPException(422, {"code": "path", "message": "A file path is required."})
    if not sess.has_workspace:
        raise HTTPException(404, {"code": "not_found", "message": "No such file."})
    c = work_svc.cfg(ctx.st, ctx.s)
    ctx.s.commit()
    name = call(work_svc.ensure_running, ctx.st, sess.id)
    rt = work_svc.runtime(ctx.st)
    try:
        info = rt.stat(name, rel)
        if not info.get("exists"):
            raise HTTPException(404, {"code": "not_found", "message": "No such file."})
        if info.get("type") != "file":
            raise HTTPException(422, {"code": "path", "message": "That is not a file."})
        cap = c.artifact_mb * 1048576 * 2
        if info["size"] > cap:
            raise HTTPException(413, {"code": "too_large", "message": f"Files over {cap // 1048576} MB cannot be downloaded here."})
        data, _ = rt.get_file(name, rel, limit=cap)
    except work_runtime.RuntimeError_ as e:
        raise _err(e) from None
    return _file_response(data, guess_mime(rel), rel.rsplit("/", 1)[-1], download)


@router.put("/sessions/{sid}/files")
def upload(sid: str, path: str, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    from ..work_tools import Wk, check_quota
    sess = own(ctx, sid)
    rel = _norm(path)
    if not rel or rel.endswith("/"):
        raise HTTPException(422, {"code": "path", "message": "Give the file's path inside /work."})
    c = work_svc.cfg(ctx.st, ctx.s)
    lim = work_svc.effective(c, sess)
    cap = c.upload_mb * 1048576
    data = file.file.read(cap + 1)
    if len(data) > cap:
        raise HTTPException(413, {"code": "too_large", "message": f"Uploads can be at most {c.upload_mb} MB."})
    sid_ = sess.id
    ctx.s.commit()
    name = call(work_svc.ensure_running, ctx.st, sid_)
    w = Wk(ctx.st, sid_, name, lim, "", sess.org_id, ctx.user.id, None, sess.network, None)
    try:
        check_quota(w, name, len(data))
        head = w.rt.put_file(name, rel, data)
    except work_runtime.RuntimeError_ as e:
        raise _err(e) from None
    except Exception as e:  # ToolError: the quota message is the answer
        from ..agent_svc import ToolError
        if isinstance(e, ToolError):
            raise HTTPException(413, {"code": "quota", "message": str(e)}) from None
        raise
    ctx.st.__dict__.get("work_usage", {}).pop(sid_, None)
    return {"path": rel, "size": len(data), "sha256": head["sha256"]}


@router.delete("/sessions/{sid}/files")
def delete_file(sid: str, path: str, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    rel = _norm(path)
    if not rel:
        raise HTTPException(422, {"code": "path", "message": "Refusing to delete the workspace root."})
    if not sess.has_workspace:
        raise HTTPException(404, {"code": "not_found", "message": "No such file."})
    sid_ = sess.id
    ctx.s.commit()
    name = call(work_svc.ensure_running, ctx.st, sid_)
    try:
        n = work_svc.runtime(ctx.st).delete(name, rel)
    except work_runtime.PathRefused as e:
        raise HTTPException(404 if "no such" in str(e) else 422, {"code": "path", "message": str(e)}) from None
    except work_runtime.RuntimeError_ as e:
        raise _err(e) from None
    ctx.st.__dict__.get("work_usage", {}).pop(sid_, None)
    return {"deleted": n}


# ---- artifacts -------------------------------------------------------------------------------------------------
@router.get("/sessions/{sid}/artifacts")
def artifacts(sid: str, ctx: Ctx = Depends(current)):
    sess = own(ctx, sid)
    rows = ctx.s.execute(select(WorkArtifact).where(WorkArtifact.session_id == sess.id).order_by(WorkArtifact.created_at)).scalars()
    return [work_svc.artifact_out(a) for a in rows]


def _own_artifact(ctx: Ctx, aid: str) -> tuple[WorkArtifact, WorkSession]:
    gate(ctx)
    art = ctx.s.get(WorkArtifact, aid)
    sess = ctx.s.get(WorkSession, art.session_id) if art else None
    if art is None or sess is None or sess.user_id != ctx.user.id or sess.deleted_at:
        raise HTTPException(404, {"code": "not_found", "message": "No such artifact."})
    return art, sess


@router.get("/artifacts/{aid}/content")
def artifact_content(aid: str, download: bool = False, ctx: Ctx = Depends(current)):
    art, sess = _own_artifact(ctx, aid)
    try:
        data = work_svc.read_artifact(ctx.st, art, sess.user_id)
    except (OSError, ValueError):
        raise HTTPException(410, {"code": "gone", "message": "The stored copy of this artifact is no longer available."}) from None
    return _file_response(data, art.mime, art.name, download)


@router.post("/artifacts/{aid}/save-to-library")
def save_to_library(aid: str, ctx: Ctx = Depends(current)):
    art, _sess = _own_artifact(ctx, aid)
    return call(work_svc.save_to_library, ctx.st, ctx.s, ctx.user, ctx.principal, art)
