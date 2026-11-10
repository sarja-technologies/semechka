"""Experimental tools (speech, music, picture tools, 3D): list and run. Results are ordinary media jobs (GET /api/media/jobs/{id}, cancel, Library)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from starlette.datastructures import UploadFile as StarUploadFile

from .. import catalog_svc, media_extras as mx, media_lib, media_svc
from ..adapters import AdapterError
from ..deps import Ctx, current
from ..media_models import MediaJob
from ..models import Backend, Model
from .media import _backend_error, _size, _val

router = APIRouter(prefix="/api/media/extras")


@router.get("")
def list_tools(group: str | None = None, ctx: Ctx = Depends(current)):
    """The tools this person may use (optionally one group), with the engine's input schema and this deployment's limits."""
    mx.gate(ctx)
    if group is not None and group not in mx.GROUPS:
        raise HTTPException(422, {"code": "invalid", "message": "group must be audio, tools or 3d."})
    by_model = catalog_svc.grants_by_model(ctx.s)
    tools = [mx.tool_out(ctx.s, ctx.principal, m, b, by_model.get(m.id, [])) for m, b in mx.accessible_tools(ctx.s, ctx.principal, ctx.st)]
    if group:
        tools = [t for t in tools if t["group"] == group]
    ephemeral = bool(_val(ctx, "privacy.ephemeral_allowed") and ctx.principal.can("chat.ephemeral"))
    return {"tools": tools, "ephemeral": ephemeral, "retention_days": _val(ctx, "privacy.chat_retention_days"),
            "caps": {"concurrent": _val(ctx, "media.max_concurrent_jobs"), "upload_max_mb": _val(ctx, "media.profile_upload_max_mb"),
                     "daily": _val(ctx, "media.daily_generations") or None, "active": media_svc.count_active(ctx.s, ctx.user.id),
                     "today": media_svc.generations_today(ctx.s, ctx.user.id)}}


@router.post("/jobs")
async def create_tool_job(request: Request, ctx: Ctx = Depends(current)):
    """multipart/form-data: model_id, save, inputs (JSON of plain settings) and ``file:<input>`` parts or ``asset:<input>`` = a Library asset id."""
    limit_kind = mx.gate(ctx)
    s, st, p = ctx.s, ctx.st, ctx.principal
    form = await request.form()
    m = s.get(Model, str(form.get("model_id") or ""))
    b = s.get(Backend, m.backend_id) if m else None
    if m is None or b is None or not mx.is_tool(m):
        raise HTTPException(404, {"code": "not_found", "message": "That tool was not found."})
    ok, why = mx.tool_access(s, p, m, b, st)
    if not ok:
        raise HTTPException(403, {"code": "model_forbidden", "message": why})
    try:
        inputs = json.loads(str(form.get("inputs") or "{}"))
        if not isinstance(inputs, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(422, {"code": "invalid", "message": "inputs must be a JSON object."}) from None
    uploads: dict[str, list[tuple]] = {}
    for key, val in form.multi_items():
        if key.startswith("file:") and isinstance(val, StarUploadFile):
            uploads.setdefault(key[5:], []).append((val.file, val.content_type or "", val.filename or "upload"))
    sizes = {n: [((mime or ""), _size(fo)) for fo, mime, _ in items] for n, items in uploads.items()}
    picked: dict[str, list] = {}
    for key, val in form.multi_items():
        if key.startswith("asset:") and isinstance(val, str):
            got = media_lib.owned(st, s, ctx.user.id, val.strip())
            if got is None:
                raise HTTPException(422, {"code": "asset_missing", "message": "A selected file is no longer in your library. Add it again."})
            picked.setdefault(key[6:], []).append((val.strip(), got))
            info = media_lib.mem_info(st, got) if isinstance(got, str) else {"mime": got.mime, "size": got.size}
            sizes.setdefault(key[6:], []).append((info["mime"], info["size"]))
    if sum(len(v) for v in picked.values()) > 4:
        raise HTTPException(422, {"code": "invalid", "message": "At most 4 library files can be used at once."})
    days = _val(ctx, "privacy.chat_retention_days")
    asked_save = str(form.get("save", "true")).lower() != "false"
    try:
        save = media_svc.admit(st, s, p, ctx.user.id, asked_save, limit_kind)
    except media_svc.AdmitError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None
    try:
        clean = mx.validate_inputs(m, inputs, sizes, int(_val(ctx, "media.profile_upload_max_mb")) * 2**20)
    except media_svc.ParamError as e:
        raise HTTPException(422, {"code": "invalid", "message": str(e)}) from e
    s.commit()
    try:
        with media_lib.materialise(st, [g for lst in picked.values() for _, g in lst]) as opened:
            it = iter(opened)
            for name, lst in picked.items():
                for _aid, _g in lst:
                    fh, mime, nm, _k = next(it)
                    uploads.setdefault(name, []).append((fh, mime, nm))
            jid = await mx.submit_tool(st, ctx.user, m, b, clean, uploads, save, mx.expires_for(days),
                                       sources={n: [i for i, _ in lst] for n, lst in picked.items()} or None)
    except AdapterError as e:
        raise _backend_error(e) from e
    await media_svc.refresh(st, jid)
    s.expire_all()
    return {**media_svc.job_out(st, s, p, s.get(MediaJob, jid)), "forced_unsaved": days == 0 and asked_save}


@router.get("/jobs")
async def list_tool_jobs(ctx: Ctx = Depends(current)):
    """This person's recent Experimental tool jobs (the Images and Videos pages do not list them)."""
    from sqlalchemy import select
    mx.gate(ctx)
    media_svc.purge_mem(ctx.st)
    ctx.s.commit()
    q = select(MediaJob).where(MediaJob.user_id == ctx.user.id, MediaJob.kind == mx.JOB_KIND).order_by(MediaJob.created_at.desc()).limit(50)
    for j in ctx.s.execute(q).scalars().all():
        if j.status in media_svc.ACTIVE:
            await media_svc.refresh(ctx.st, j.id)
    ctx.s.expire_all()
    out = []
    for j in ctx.s.execute(q).scalars().all():
        o = media_svc.job_out(ctx.st, ctx.s, ctx.principal, j)
        if not j.save and j.status == "completed" and not o["assets"]:
            continue
        out.append(o)
    return out
