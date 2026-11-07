"""Imajutsu API (docs/IMAJUTSU.md, "Semechka backend contract"). Session cookie + CSRF as every route, the Image mode gate, owner-only access to projects, jobs and
pictures: someone else's id answers 404 exactly like a missing one. The logic is in imajutsu_svc.py. The live inspector of an Imajutsu job is the Studio's
(/api/studio/jobs/{job}/inspect|inspect/stream|graph|history|preview), which accepts image jobs too."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import imajutsu_svc as svc
from .. import media_lib, media_svc
from ..adapters import AdapterError
from ..deps import Ctx, abegin_write, current
from ..media_lib import MediaError
from ..studio_models import StudioProject
from .media import _gate

router = APIRouter(prefix="/api/imajutsu")

SAFE_HEADERS = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"}
MAX_RECIPE_QUERY = 4096


def _fail(e: MediaError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message, **(getattr(e, "extra", None) or {})})


def _gated(ctx: Ctx) -> Ctx:
    """The Image mode gate plus access to at least one image model."""
    _gate(ctx, "image")
    if not svc.image_rows(ctx.st, ctx.s, ctx.principal):
        raise HTTPException(403, {"code": "no_image_model", "message": "No image model is available to you."})
    return ctx


def _project(ctx: Ctx, pid: str) -> StudioProject:
    try:
        return svc.own_project(ctx.st, ctx.s, ctx.user.id, pid)
    except MediaError as e:
        raise _fail(e) from e


def _spec_from(ctx: Ctx, p: StudioProject, body_spec):
    """The spec in the request (validated, NOT saved) or else the project's own."""
    if body_spec is None:
        return svc.spec_of(ctx.st, p)
    return svc.normalize_spec(ctx.st, ctx.s, ctx.user.id, body_spec)


# ---- capabilities -----------------------------------------------------------------------------------------------------------------------------------
@router.get("/capabilities")
def capabilities(ctx: Ctx = Depends(current)):
    _gate(ctx, "image")
    try:
        return svc.capabilities(ctx.st, ctx.s, ctx.principal)
    except MediaError as e:
        raise _fail(e) from e


# ---- projects -----------------------------------------------------------------------------------------------------------------------------------------
class ProjectIn(BaseModel):
    mode: str | None = None
    source_asset: str | None = None
    title: str | None = Field(default=None, max_length=200)


class ProjectPut(BaseModel):
    spec: dict | None = None
    expected_updated_at: float | None = None
    title: str | None = Field(default=None, max_length=200)


class PlanIn(BaseModel):
    spec: dict | None = None


class GenerateIn(BaseModel):
    spec: dict | None = None
    count: int | None = Field(default=None, ge=1, le=svc.MAX_COUNT)


@router.post("/projects")
async def create_project(body: ProjectIn | None = None, ctx: Ctx = Depends(current)):
    _gated(ctx)
    await abegin_write(ctx)
    b = body or ProjectIn()
    try:
        p = svc.create_project(ctx.st, ctx.s, ctx.user, b.mode, b.source_asset, b.title)
        ctx.s.commit()
        return svc.project_json(ctx.st, ctx.s, ctx.user, p)
    except MediaError as e:
        raise _fail(e) from e


@router.get("/projects")
def list_projects(limit: int = 100, ctx: Ctx = Depends(current)):
    _gated(ctx)
    rows = ctx.s.execute(select(StudioProject).where(StudioProject.user_id == ctx.user.id).order_by(StudioProject.updated_at.desc()).limit(600)).scalars().all()
    out = []
    for p in rows:
        if svc.project_kind(ctx.st, p) == svc.KIND:
            out.append(svc.project_json(ctx.st, ctx.s, ctx.user, p, summary=True))
            if len(out) >= max(1, min(limit, 200)):
                break
    return out


@router.get("/projects/{pid}")
def get_project(pid: str, ctx: Ctx = Depends(current)):
    _gated(ctx)
    return svc.project_json(ctx.st, ctx.s, ctx.user, _project(ctx, pid))


@router.put("/projects/{pid}")
async def put_project(pid: str, body: ProjectPut, ctx: Ctx = Depends(current)):
    _gated(ctx)
    await abegin_write(ctx)
    p = _project(ctx, pid)
    try:
        svc.update_project(ctx.st, ctx.s, ctx.user, p, body.spec, body.expected_updated_at, body.title)
        ctx.s.commit()
        return svc.project_json(ctx.st, ctx.s, ctx.user, p)
    except MediaError as e:
        ctx.s.rollback()
        if e.status == 409:  # a stale save carries the current project so the page can show what changed
            raise HTTPException(409, {"code": e.code, "message": e.message, "project": svc.project_json(ctx.st, ctx.s, ctx.user, _project(ctx, pid))}) from e
        raise _fail(e) from e


@router.delete("/projects/{pid}")
def delete_project(pid: str, ctx: Ctx = Depends(current)):
    """Forget the project. The pictures, masks and every result stay in the library."""
    _gated(ctx)
    ctx.s.delete(_project(ctx, pid))
    return {"ok": True}


@router.post("/projects/{pid}/plan")
async def plan(pid: str, body: PlanIn | None = None, ctx: Ctx = Depends(current)):
    _gated(ctx)
    p = _project(ctx, pid)
    try:
        spec = _spec_from(ctx, p, body.spec if body else None)
        r = await svc.build_plan(ctx.st, ctx.s, ctx.user, spec)
    except MediaError as e:
        raise _fail(e) from e
    return {"plan": r["plan"]}


@router.post("/projects/{pid}/generate")
async def generate(pid: str, body: GenerateIn | None = None, ctx: Ctx = Depends(current)):
    """Validate, prepare the pictures (outpaint canvas, mask, references), upload them once, and start ONE engine image job per variation (count 1 to 4, seeds
    seed+i). Returns the first job id, all job ids and the accounting of every input; the references beyond the model's limit are listed as not used."""
    _gated(ctx)
    p = _project(ctx, pid)
    b = body or GenerateIn()
    try:
        return await svc.generate(ctx.st, ctx.s, ctx.user, p, {"spec": b.spec, "count": b.count})
    except MediaError as e:
        raise _fail(e) from e
    except AdapterError as e:
        raise HTTPException(422 if e.status == 422 else 502, {"code": e.kind, "message": str(e)}) from e


# ---- jobs ------------------------------------------------------------------------------------------------------------------------------------------------
async def _refresh_active(ctx: Ctx) -> None:
    for j in ctx.s.execute(select(media_svc.MediaJob).where(media_svc.MediaJob.user_id == ctx.user.id, media_svc.MediaJob.kind == "image",
                                                            media_svc.MediaJob.status.in_(media_svc.ACTIVE))).scalars().all():
        ctx.s.commit()
        await media_svc.refresh(ctx.st, j.id)
    ctx.s.expire_all()


@router.get("/jobs")
async def jobs(limit: int = 100, ctx: Ctx = Depends(current)):
    _gated(ctx)
    await _refresh_active(ctx)
    return svc.list_jobs(ctx.st, ctx.s, ctx.user, max(1, min(limit, 200)))


@router.get("/jobs/{jid}")
async def job(jid: str, ctx: Ctx = Depends(current)):
    _gated(ctx)
    ctx.s.commit()
    j = ctx.s.get(media_svc.MediaJob, jid)
    if j is None or j.user_id != ctx.user.id or j.kind != "image":
        raise HTTPException(404, {"code": "not_found", "message": "Not found."})
    await media_svc.refresh(ctx.st, jid)
    ctx.s.expire_all()
    try:
        return svc.one_job(ctx.st, ctx.s, ctx.user, jid)
    except MediaError as e:
        raise _fail(e) from e


@router.post("/jobs/{jid}/reopen")
async def reopen(jid: str, ctx: Ctx = Depends(current)):
    """The project of this job, set back to what the job was made from. Calling it twice changes nothing."""
    _gated(ctx)
    await abegin_write(ctx)
    try:
        return await svc.reopen(ctx.st, ctx.s, ctx.user, jid)
    except MediaError as e:
        raise _fail(e) from e


# ---- photographic work on one picture ---------------------------------------------------------------------------------------------------------------
class AdjustIn(BaseModel):
    recipe: dict
    name: str | None = Field(default=None, max_length=120)


class UpscaleIn(BaseModel):
    width: int
    height: int
    name: str | None = Field(default=None, max_length=120)


class KeepOutsideIn(BaseModel):
    feather: float | None = None
    name: str | None = Field(default=None, max_length=120)


@router.get("/assets/{aid}/adjust/preview")
async def adjust_preview(aid: str, recipe: str = Query("{}", max_length=MAX_RECIPE_QUERY), ctx: Ctx = Depends(current)):
    """A small JPEG (long edge at most 1024) of the picture with the recipe applied by the same code that makes the final file. Cached by picture and recipe."""
    _gated(ctx)
    try:
        raw = json.loads(recipe or "{}")
    except ValueError:
        raise HTTPException(422, {"code": "invalid", "message": "recipe must be JSON."}) from None
    try:
        data, key = await svc.adjust_preview(ctx.st, ctx.s, ctx.user, aid, raw)
    except MediaError as e:
        raise _fail(e) from e
    return Response(data, media_type="image/jpeg", headers={**SAFE_HEADERS, "ETag": f'"{key}"', "X-Recipe-Key": key})


@router.post("/assets/{aid}/adjust")
async def adjust(aid: str, body: AdjustIn, ctx: Ctx = Depends(current)):
    """Write a NEW picture (origin 'adjusted', parent = the original, the recipe in its metadata). The original is not touched. Returns a task polled at
    GET /api/media/edits/{task}."""
    _gated(ctx)
    try:
        t = await svc.start_adjust(ctx.st, ctx.s, ctx.user, aid, body.recipe, body.name)
    except MediaError as e:
        raise _fail(e) from e
    return {"task": t.id, "task_status": media_lib.task_out(t)}


@router.post("/assets/{aid}/upscale")
async def upscale(aid: str, body: UpscaleIn, ctx: Ctx = Depends(current)):
    """Scale a picture (Lanczos and a light unsharp mask) to a size of the same shape. Labelled 'Scaled, no new detail'; never AI. Also scales down."""
    _gated(ctx)
    try:
        t, info = await svc.start_upscale(ctx.st, ctx.s, ctx.user, aid, body.width, body.height, body.name)
    except MediaError as e:
        raise _fail(e) from e
    return {"task": t.id, "task_status": media_lib.task_out(t), **info}


@router.post("/assets/{aid}/keep-outside")
async def keep_outside(aid: str, body: KeepOutsideIn | None = None, ctx: Ctx = Depends(current)):
    """For a result of Edit, Replace Subject, Inpaint or Outpaint: a new picture that is the generated one inside the selection and the SOURCE exactly everywhere
    else (ffmpeg maskedmerge with a feathered mask). Labelled; the generated picture is untouched."""
    _gated(ctx)
    b = body or KeepOutsideIn()
    try:
        t = await svc.start_keep_outside(ctx.st, ctx.s, ctx.user, aid, b.feather, b.name)
    except MediaError as e:
        raise _fail(e) from e
    return {"task": t.id, "task_status": media_lib.task_out(t)}
