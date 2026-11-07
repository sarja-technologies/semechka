"""OpenJutsu Studio API (docs/OPENJUTSU_STUDIO.md section 3). Session cookie + CSRF as every route, the Video mode gate, owner-only access to
projects and jobs: someone else's id answers 404 exactly like a missing one. The logic is in studio_svc.py / studio_inspect.py."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.background import BackgroundTask

from .. import media_svc, studio_inspect, studio_svc
from ..adapters import AdapterError
from ..deps import Ctx, abegin_write, current
from ..media_lib import MediaError
from ..studio_models import StudioProject
from .media import _gate

router = APIRouter(prefix="/api/studio")

NOT_FOUND = {"code": "not_found", "message": "Not found."}
SAFE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}


def _fail(e: MediaError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message, **(getattr(e, "extra", None) or {})})


def _gated(ctx: Ctx) -> Ctx:
    """The Video mode gate plus access to at least one editing profile model."""
    _gate(ctx, "video")
    return ctx


def _editing_gate(ctx: Ctx) -> None:
    _gated(ctx)
    if not studio_svc.editing_rows(ctx.st, ctx.s, ctx.principal):
        raise HTTPException(403, {"code": "no_editing_model", "message": "No video-editing model (OpenJutsu) is available to you."})


def _project(ctx: Ctx, pid: str) -> StudioProject:
    try:
        return studio_svc.own_project(ctx.st, ctx.s, ctx.user.id, pid)
    except MediaError as e:
        raise _fail(e) from e


def _spec_from(ctx: Ctx, p: StudioProject, body_spec):
    """The spec in the request (validated, NOT saved) or else the project's own."""
    if body_spec is None:
        return studio_svc.spec_of(ctx.st, p)
    return studio_svc.normalize_spec(ctx.st, ctx.s, ctx.user.id, body_spec)


def _own_job_info(ctx: Ctx, jid: str):
    """The caller's job (video or image: Imajutsu's jobs are inspected here too), behind the mode gate of ITS kind; anyone else's id is a plain 404."""
    ji = studio_inspect.load_job(ctx.st, jid, ctx.user.id)
    if ji is None:
        try:
            _gated(ctx)
        except HTTPException:
            _gate(ctx, "image")          # an Imajutsu-only person: the id is simply not theirs. (Someone with neither mode gets the video gate's answer.)
        raise HTTPException(404, NOT_FOUND)
    if ji.kind == "image":
        _gate(ctx, "image")
    else:
        _gated(ctx)
    return ji


# ---- capabilities ----------------------------------------------------------------------------------------------------------------
@router.get("/capabilities")
def capabilities(ctx: Ctx = Depends(current)):
    _gated(ctx)
    return studio_svc.capabilities(ctx.st, ctx.s, ctx.principal)


# ---- projects ----------------------------------------------------------------------------------------------------------------------
class ProjectIn(BaseModel):
    source_asset: str
    title: str | None = Field(default=None, max_length=200)


class ProjectPut(BaseModel):
    spec: dict | None = None
    expected_updated_at: float | None = None
    title: str | None = Field(default=None, max_length=200)


class PlanIn(BaseModel):
    spec: dict | None = None


class GenerateIn(BaseModel):
    spec: dict | None = None
    accept_experimental: bool | None = None


class ExportIn(BaseModel):
    width: int
    height: int
    fit: str = "scale"


@router.post("/projects")
async def create_project(body: ProjectIn, ctx: Ctx = Depends(current)):
    _editing_gate(ctx)
    await abegin_write(ctx)
    try:
        p = studio_svc.create_project(ctx.st, ctx.s, ctx.user, body.source_asset, body.title)
        ctx.s.commit()
        return await studio_svc.project_json(ctx.st, ctx.s, ctx.user, p)
    except MediaError as e:
        raise _fail(e) from e


@router.get("/projects")
async def list_projects(limit: int = 100, ctx: Ctx = Depends(current)):
    _editing_gate(ctx)
    rows = ctx.s.execute(select(StudioProject).where(StudioProject.user_id == ctx.user.id).order_by(StudioProject.updated_at.desc())).scalars().all()
    rows = [p for p in rows if studio_svc.project_kind(ctx.st, p) == "video"][:max(1, min(limit, 200))]      # Imajutsu's image projects live in the same table
    return [await studio_svc.project_json(ctx.st, ctx.s, ctx.user, p, summary=True) for p in rows]


@router.get("/projects/{pid}")
async def get_project(pid: str, ctx: Ctx = Depends(current)):
    _editing_gate(ctx)
    p = _project(ctx, pid)
    try:
        return await studio_svc.project_json(ctx.st, ctx.s, ctx.user, p)
    except MediaError as e:
        raise _fail(e) from e


@router.put("/projects/{pid}")
async def put_project(pid: str, body: ProjectPut, ctx: Ctx = Depends(current)):
    _editing_gate(ctx)
    await abegin_write(ctx)
    p = _project(ctx, pid)
    try:
        studio_svc.update_project(ctx.st, ctx.s, ctx.user, p, body.spec, body.expected_updated_at, body.title)
        ctx.s.commit()
        return await studio_svc.project_json(ctx.st, ctx.s, ctx.user, p)
    except MediaError as e:
        ctx.s.rollback()
        if e.status == 409:  # a stale save carries the current project so the page can show what changed
            cur = await studio_svc.project_json(ctx.st, ctx.s, ctx.user, _project(ctx, pid))
            raise HTTPException(409, {"code": e.code, "message": e.message, "project": cur}) from e
        raise _fail(e) from e


@router.delete("/projects/{pid}")
def delete_project(pid: str, ctx: Ctx = Depends(current)):
    """Forget the project. The source video, the prepared copy, the pictures and every result stay in the library."""
    _editing_gate(ctx)
    p = _project(ctx, pid)
    ctx.s.delete(p)
    return {"ok": True}


@router.post("/projects/{pid}/plan")
async def plan(pid: str, body: PlanIn | None = None, ctx: Ctx = Depends(current)):
    _editing_gate(ctx)
    p = _project(ctx, pid)
    try:
        spec = _spec_from(ctx, p, body.spec if body else None)
        r = await studio_svc.build_plan(ctx.st, ctx.s, ctx.user, spec)
    except MediaError as e:
        raise _fail(e) from e
    return {"plan": r["plan"]}


@router.post("/projects/{pid}/prepare")
async def prepare(pid: str, body: PlanIn | None = None, ctx: Ctx = Depends(current)):
    """Start (or reuse) the prepared copy. ``task`` is polled at GET /api/media/edits/{task} like any edit; ``reused`` is true when an identical
    prepared file already existed (nothing is encoded again) and ``asset`` then holds it."""
    _editing_gate(ctx)
    p = _project(ctx, pid)
    try:
        spec = _spec_from(ctx, p, body.spec if body else None)
        return await studio_svc.prepare(ctx.st, ctx.s, ctx.user, spec)
    except MediaError as e:
        raise _fail(e) from e


@router.post("/projects/{pid}/generate")
async def generate(pid: str, body: GenerateIn | None = None, ctx: Ctx = Depends(current)):
    """Validate, prepare the video if needed (waits up to 90 s; after that answers 202 with the prepare task and the caller asks again), send ONLY the
    active reference pictures the operation can use, and start the job. Returns the job id, the prepared asset and the accounting of every input."""
    _editing_gate(ctx)
    p = _project(ctx, pid)
    b = body or GenerateIn()
    try:
        out = await studio_svc.generate(ctx.st, ctx.s, ctx.user, p, {"spec": b.spec, **({"accept_experimental": b.accept_experimental} if b.accept_experimental is not None else {})})
    except studio_svc.StudioError as e:
        if e.status == 202:
            return JSONResponse({"code": e.code, "message": e.message, **e.extra}, status_code=202)
        raise _fail(e) from e
    except MediaError as e:
        raise _fail(e) from e
    except AdapterError as e:
        raise HTTPException(422 if e.status == 422 else 502, {"code": e.kind, "message": str(e)}) from e
    return out


# ---- jobs ------------------------------------------------------------------------------------------------------------------------
@router.get("/jobs")
async def jobs(ctx: Ctx = Depends(current)):
    _gated(ctx)
    for j in ctx.s.execute(select(media_svc.MediaJob).where(media_svc.MediaJob.user_id == ctx.user.id, media_svc.MediaJob.status.in_(media_svc.ACTIVE))).scalars().all():
        ctx.s.commit()
        await media_svc.refresh(ctx.st, j.id)
    ctx.s.expire_all()
    return studio_svc.list_jobs(ctx.st, ctx.s, ctx.user)


@router.get("/jobs/{jid}")
async def job(jid: str, ctx: Ctx = Depends(current)):
    _gated(ctx)
    ctx.s.commit()
    if studio_inspect.load_job(ctx.st, jid, ctx.user.id) is None:
        raise HTTPException(404, NOT_FOUND)
    await media_svc.refresh(ctx.st, jid)
    ctx.s.expire_all()
    row = next((r for r in studio_svc.list_jobs(ctx.st, ctx.s, ctx.user) if r["job"] == jid), None)
    if row is None:
        raise HTTPException(404, NOT_FOUND)
    return row


@router.post("/jobs/{jid}/export")
async def export(jid: str, body: ExportIn, ctx: Ctx = Depends(current)):
    _gated(ctx)
    media_lib_gate(ctx)
    try:
        return await studio_svc.export(ctx.st, ctx.s, ctx.user, jid, body.model_dump())
    except MediaError as e:
        raise _fail(e) from e


def media_lib_gate(ctx: Ctx) -> None:
    from .media_assets import need_library
    need_library(ctx)


# ---- the inspector ------------------------------------------------------------------------------------------------------------------
@router.get("/jobs/{jid}/inspect")
async def inspect(jid: str, after: int = Query(0, ge=0), ctx: Ctx = Depends(current)):
    _own_job_info(ctx, jid)
    ctx.s.commit()
    doc = await studio_inspect.get_doc(ctx.st, ctx.user.id, jid, after)
    if doc is None:
        raise HTTPException(404, NOT_FOUND)
    return JSONResponse(doc, headers={"Cache-Control": "private, no-store"})


def _last_event_id(request: Request, after: int | None) -> int:
    raw = request.headers.get("last-event-id")
    for v in (raw, after):
        try:
            if v not in (None, ""):
                return max(0, int(v))
        except (TypeError, ValueError):
            continue
    return 0


@router.get("/jobs/{jid}/inspect/stream")
async def inspect_stream(jid: str, request: Request, after: int | None = None, ctx: Ctx = Depends(current)):
    """Server-sent events: ``inspect`` documents (id = the engine's cursor, so Last-Event-ID resumes), ``stale`` when the engine does not answer, a comment
    every 15 s. The stream ends after the final document of a finished job."""
    _own_job_info(ctx, jid)
    ctx.s.commit()
    return StreamingResponse(studio_inspect.stream(ctx.st, request, ctx.user.id, jid, _last_event_id(request, after)), media_type="text/event-stream",
                             headers={**SAFE_HEADERS, "X-Accel-Buffering": "no", "Connection": "keep-alive"})


async def _engine_json(ctx: Ctx, jid: str, which: str, missing_code: str):
    ji = _own_job_info(ctx, jid)
    ctx.s.commit()
    ad = studio_inspect.adapter(ctx.st, ji)
    if ji.kind == "image" and which == "media_history":
        missing_code = "no_graph"          # a native workflow job has no ComfyUI history; its graph is the workflow
    if ad is None or not ji.remote:
        raise HTTPException(404, {"code": missing_code, "message": "The engine has no record of this."})
    try:
        code, d = await getattr(ad, which)(ji.remote)
    except AdapterError as e:
        raise HTTPException(502, {"code": "engine_unreachable", "message": media_svc.clean_text(str(e))}) from e
    if code == 404:
        raise HTTPException(404, {"code": missing_code, "message": "The engine has no record of this."})
    if code != 200 or d is None:
        raise HTTPException(502, {"code": "engine_error", "message": "The engine could not answer."})
    d = studio_inspect.scrub(d)
    if which == "media_graph" and isinstance(d, dict):
        d["graph_kind"] = studio_inspect.graph_kind(d)
    return JSONResponse(d, headers={"Cache-Control": "private, no-store"})


@router.get("/jobs/{jid}/graph")
async def graph(jid: str, ctx: Ctx = Depends(current)):
    return await _engine_json(ctx, jid, "media_graph", "no_graph")


@router.get("/jobs/{jid}/history")
async def history(jid: str, ctx: Ctx = Depends(current)):
    return await _engine_json(ctx, jid, "media_history", "no_history")


@router.get("/jobs/{jid}/segments/{n}")
async def segment(jid: str, n: int, request: Request, ctx: Ctx = Depends(current)):
    """A completed scene of the run as video/mp4, streamed from the engine with the browser's Range header passed through (never held in memory)."""
    ji = _own_job_info(ctx, jid)
    ctx.s.commit()
    if n < 1 or n > 10000:
        raise HTTPException(404, {"code": "not_saved", "message": "That scene is not saved (yet)."})
    ad = studio_inspect.adapter(ctx.st, ji)
    if ad is None or not ji.remote:
        raise HTTPException(404, {"code": "not_saved", "message": "That scene is not saved (yet)."})
    try:
        client, r = await ad.media_segment_open(ji.remote, n, request.headers.get("range"))
    except AdapterError as e:
        raise HTTPException(502, {"code": "engine_unreachable", "message": media_svc.clean_text(str(e))}) from e
    if r.status_code not in (200, 206, 416):
        await r.aclose()
        await client.aclose()
        raise HTTPException(404, {"code": "not_saved", "message": "That scene is not saved (yet)."})
    headers = dict(SAFE_HEADERS)
    for k in ("content-length", "content-range", "accept-ranges"):
        if k in r.headers:
            headers[k] = r.headers[k]

    async def close():
        await r.aclose()
        await client.aclose()
    if r.status_code == 416:
        await close()
        return Response(status_code=416, headers=headers)
    return StreamingResponse(r.aiter_raw(1 << 20), status_code=r.status_code, media_type="video/mp4", headers=headers, background=BackgroundTask(close))


@router.get("/jobs/{jid}/preview")
async def preview(jid: str, ctx: Ctx = Depends(current)):
    ji = _own_job_info(ctx, jid)
    ctx.s.commit()
    ad = studio_inspect.adapter(ctx.st, ji)
    if ad is None or not ji.remote:
        raise HTTPException(404, {"code": "no_preview", "message": "No preview yet."})
    try:
        code, data = await ad.media_preview(ji.remote)
    except AdapterError as e:
        raise HTTPException(502, {"code": "engine_unreachable", "message": media_svc.clean_text(str(e))}) from e
    if code != 200 or not data:
        raise HTTPException(404, {"code": "no_preview", "message": "No preview yet."})
    return Response(data, media_type="image/jpeg", headers=SAFE_HEADERS)
