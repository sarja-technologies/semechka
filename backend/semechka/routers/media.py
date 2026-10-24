"""Image and video generation API. Every route enforces the mode gate, ownership and quotas itself."""
from __future__ import annotations

import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import media_svc, services
from ..adapters import AdapterError
from ..deps import Ctx, current
from ..media_models import MediaAsset, MediaJob
from ..models import Backend, Model

router = APIRouter(prefix="/api/media")


def _gate(ctx: Ctx, kind: str) -> None:
    st = services.mode_state(ctx.s, ctx.principal, ctx.st, kind)
    if st["enabled"]:
        return
    code = {"disabled": 403, "forbidden": 403, "no_model": 409, "unsupported": 501}.get(st.get("code"), 403)
    raise HTTPException(code, {"code": st.get("code", "forbidden"), "message": st["reason"]})


def _kind(kind: str) -> str:
    if kind not in media_svc.MEDIA_KINDS:
        raise HTTPException(422, {"code": "invalid", "message": "kind must be image or video."})
    return kind


def _val(ctx: Ctx, key: str):
    return ctx.st.settings.value_for(ctx.s, key, ctx.principal)


def _own_job(ctx: Ctx, jid: str) -> MediaJob:
    j = ctx.s.get(MediaJob, jid)
    if j is None or j.user_id != ctx.user.id:
        raise HTTPException(404, {"code": "not_found", "message": "Not found."})
    return j


def _backend_error(e: AdapterError) -> HTTPException:
    status = e.status if e.status in (400, 422, 501) else 502
    return HTTPException(status, {"code": e.kind, "message": str(e)})


@router.get("/options")
def options(kind: str, ctx: Ctx = Depends(current)):
    """Models this person may use, with the engine's real limits and this deployment's caps."""
    _kind(kind)
    _gate(ctx, kind)
    vmax = _val(ctx, "media.video_max_seconds")
    size = _val(ctx, "media.default_image_size")
    models = []
    for m, b in services.accessible_models(ctx.s, ctx.principal, ctx.st, kind):
        info = media_svc.media_info(m)
        models.append({"id": m.id, "alias": m.alias, "pinned": m.pinned, "external": services.is_external(m, b),
                       "description": (m.meta or {}).get("description"), "negative_prompt": bool(info.get("negative_prompt")),
                       "modes": info.get("modes") or [], "audio": bool(info.get("audio")),
                       "schedulers": info.get("schedulers") or [], "limits": info.get("limits") or {},
                       "defaults": media_svc.option_defaults(kind, m, size, vmax),
                       "loaded": (m.meta or {}).get("loaded")})
    ephemeral = bool(_val(ctx, "privacy.ephemeral_allowed") and ctx.principal.can("chat.ephemeral"))
    return {"kind": kind, "models": models, "ephemeral": ephemeral, "retention_days": _val(ctx, "privacy.chat_retention_days"),
            "prompt_max": media_svc.PROMPT_MAX, "video_max_seconds": vmax if kind == "video" else None,
            "caps": {"concurrent": _val(ctx, "media.max_concurrent_jobs"), "max_assets": _val(ctx, "media.max_assets"),
                     "daily": _val(ctx, "media.daily_generations") or None,
                     "active": media_svc.count_active(ctx.s, ctx.user.id),
                     "stored": media_svc.count_assets(ctx.s, ctx.user.id),
                     "today": media_svc.generations_today(ctx.s, ctx.user.id)}}


class JobIn(BaseModel):
    kind: Literal["image", "video"]
    model_id: str
    prompt: str = Field(max_length=media_svc.PROMPT_MAX + 1000)
    negative_prompt: str | None = None
    width: int | None = None
    height: int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    scheduler: str | None = None
    frames: int | None = None
    fps: int | None = None
    save: bool = True


def admit_job(ctx: Ctx, save: bool, kind: str) -> bool:
    """Server-side limits for one new generation. Returns whether the result will be saved."""
    s, p = ctx.s, ctx.principal
    if not _val(ctx, f"ui.{kind}_gen"):
        raise HTTPException(403, {"code": "disabled", "message": f"{kind.capitalize()} generation is turned off for you by your administrator."})
    days = _val(ctx, "privacy.chat_retention_days")
    if not save and not (_val(ctx, "privacy.ephemeral_allowed") and p.can("chat.ephemeral")):
        raise HTTPException(403, {"code": "ephemeral_forbidden", "message": "No-save generation is not available to you."})
    if days == 0:
        save = False  # the organisation stores nothing; the result is only held briefly for download
    if media_svc.count_active(s, ctx.user.id) >= _val(ctx, "media.max_concurrent_jobs"):
        raise HTTPException(429, {"code": "busy", "message": "You already have the maximum number of generations running. Wait for one to finish or cancel it."})
    daily = _val(ctx, "media.daily_generations")
    if daily and media_svc.generations_today(s, ctx.user.id) + media_svc.count_active(s, ctx.user.id) >= daily:
        raise HTTPException(429, {"code": "daily_limit", "message": f"Daily limit of {daily} generations reached."})
    cap = _val(ctx, "media.max_assets")
    if save and cap and media_svc.count_assets(s, ctx.user.id) >= cap:
        raise HTTPException(409, {"code": "gallery_full", "message": f"Your gallery holds {cap} items, the maximum. Delete some to continue."})
    return save


def check_params(ctx: Ctx, kind: str, m: Model, body: dict) -> dict:
    try:
        return media_svc.validate_params(kind, m, body, _val(ctx, "media.video_max_seconds"))
    except media_svc.ParamError as e:
        raise HTTPException(422, {"code": "invalid", "message": str(e)}) from e


def backend_error(e: AdapterError) -> HTTPException:
    return _backend_error(e)


@router.post("/jobs")
async def create_job(body: JobIn, ctx: Ctx = Depends(current)):
    _gate(ctx, body.kind)
    s, st, p = ctx.s, ctx.st, ctx.principal
    m = s.get(Model, body.model_id)
    b = s.get(Backend, m.backend_id) if m else None
    if m is None or b is None:
        raise HTTPException(404, {"code": "not_found", "message": "Model not found."})
    ok, why = services.model_access(s, p, m, b, st, body.kind)
    if not ok:
        raise HTTPException(403, {"code": "model_forbidden", "message": why})
    days = _val(ctx, "privacy.chat_retention_days")
    save = admit_job(ctx, body.save, body.kind)
    params = check_params(ctx, body.kind, m, body.model_dump())
    s.commit()  # release the request transaction before the service opens its own
    try:
        jid = await media_svc.submit(st, ctx.user, body.kind, m, b, params, save,
                                     time.time() + days * 86400 if days and days > 0 else None)
    except AdapterError as e:
        raise _backend_error(e) from e
    await media_svc.refresh(st, jid)
    s.expire_all()
    return {**media_svc.job_out(st, s, p, s.get(MediaJob, jid)), "forced_unsaved": days == 0 and body.save}


@router.get("/jobs")
async def list_jobs(kind: str | None = None, ctx: Ctx = Depends(current)):
    if kind:
        _kind(kind)
        _gate(ctx, kind)
    else:
        if not any(services.mode_state(ctx.s, ctx.principal, ctx.st, k)["enabled"] for k in media_svc.MEDIA_KINDS):
            raise HTTPException(403, {"code": "forbidden", "message": "Image and video generation are not available to you."})
    media_svc.purge_mem(ctx.st)
    ctx.s.commit()
    q = select(MediaJob).where(MediaJob.user_id == ctx.user.id)
    if kind:
        q = q.where(MediaJob.kind == kind)
    rows = ctx.s.execute(q.order_by(MediaJob.created_at.desc()).limit(100)).scalars().all()
    for j in rows:
        if j.status in media_svc.ACTIVE:
            await media_svc.refresh(ctx.st, j.id)
    ctx.s.expire_all()
    models = {m.id: m for m in ctx.s.execute(select(Model)).scalars()}
    out = []
    for j in ctx.s.execute(q.order_by(MediaJob.created_at.desc()).limit(100)).scalars().all():
        o = media_svc.job_out(ctx.st, ctx.s, ctx.principal, j, models)
        if not j.save and j.status == "completed" and not o["assets"]:
            continue  # a no-save result that has expired from memory is simply gone
        out.append(o)
    return out


@router.get("/jobs/{jid}")
async def get_job(jid: str, ctx: Ctx = Depends(current)):
    j = _own_job(ctx, jid)
    _gate(ctx, j.kind)
    ctx.s.commit()
    await media_svc.refresh(ctx.st, jid)
    ctx.s.expire_all()
    return media_svc.job_out(ctx.st, ctx.s, ctx.principal, ctx.s.get(MediaJob, jid))


@router.post("/jobs/{jid}/cancel")
async def cancel_job(jid: str, ctx: Ctx = Depends(current)):
    j = _own_job(ctx, jid)
    _gate(ctx, j.kind)
    ctx.s.commit()
    try:
        await media_svc.cancel(ctx.st, jid)
    except AdapterError as e:
        raise _backend_error(e) from e
    ctx.s.expire_all()
    return media_svc.job_out(ctx.st, ctx.s, ctx.principal, ctx.s.get(MediaJob, jid))


@router.delete("/jobs/{jid}")
def delete_job(jid: str, ctx: Ctx = Depends(current)):
    j = _own_job(ctx, jid)
    if j.status in media_svc.ACTIVE:
        raise HTTPException(409, {"code": "active", "message": "Cancel the generation before removing it."})
    for a in ctx.s.execute(select(MediaAsset).where(MediaAsset.job_id == jid, MediaAsset.user_id == ctx.user.id)).scalars().all():
        media_svc.remove_asset_file(ctx.st, a.user_id, a.id)
        ctx.s.delete(a)
    mem = media_svc._mem(ctx.st)
    for k in [k for k, v in mem.items() if v["job_id"] == jid and v["user_id"] == ctx.user.id]:
        mem.pop(k, None)
    ctx.s.delete(j)
    return {"ok": True}


def _ranged(data: bytes, mime: str, request: Request, filename: str | None) -> Response:
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "private, no-store",
               "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff",
               "Content-Disposition": f'{"attachment" if filename else "inline"}{f"; filename={filename}" if filename else ""}'}
    rng = request.headers.get("range", "")
    if rng.startswith("bytes=") and "," not in rng:
        a, _, b = rng[6:].partition("-")
        try:
            start = int(a) if a else max(0, len(data) - int(b))
            end = int(b) if (a and b) else len(data) - 1
        except ValueError:
            start, end = 0, len(data) - 1
        end = min(end, len(data) - 1)
        if start > end or start >= len(data):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}", **headers})
        return Response(data[start:end + 1], status_code=206, media_type=mime,
                        headers={"Content-Range": f"bytes {start}-{end}/{len(data)}", **headers})
    return Response(data, media_type=mime, headers=headers)


@router.get("/assets/{aid}/content")
def asset_content(aid: str, request: Request, download: bool = False, ctx: Ctx = Depends(current)):
    # Same 404 for missing and for someone else's: existence is not disclosed.
    miss = HTTPException(404, {"code": "not_found", "message": "Not found."})
    if aid.startswith("me_"):
        media_svc.purge_mem(ctx.st)
        v = media_svc._mem(ctx.st).get(aid)
        if v is None or v["user_id"] != ctx.user.id:
            raise miss
        data, mime, kind = v["data"], v["mime"], v["meta"]["kind"]
    else:
        a = ctx.s.get(MediaAsset, aid)
        if a is None or a.user_id != ctx.user.id:
            raise miss
        try:
            data, mime, kind = media_svc.read_asset(ctx.st, a), a.mime, a.kind
        except (OSError, ValueError) as e:
            raise HTTPException(410, {"code": "gone", "message": "The file is no longer available."}) from e
    _gate(ctx, kind)
    name = f"semechka-{kind}-{aid[-8:]}.{media_svc.SAFE_MIME.get(mime, 'bin')}" if download else None
    return _ranged(data, mime, request, name)


@router.delete("/assets/{aid}")
def delete_asset(aid: str, ctx: Ctx = Depends(current)):
    if aid.startswith("me_"):
        v = media_svc._mem(ctx.st).get(aid)
        if v is None or v["user_id"] != ctx.user.id:
            raise HTTPException(404, {"code": "not_found", "message": "Not found."})
        media_svc._mem(ctx.st).pop(aid, None)
        return {"ok": True}
    a = ctx.s.get(MediaAsset, aid)
    if a is None or a.user_id != ctx.user.id:
        raise HTTPException(404, {"code": "not_found", "message": "Not found."})
    media_svc.remove_asset_file(ctx.st, a.user_id, a.id)
    ctx.s.delete(a)
    return {"ok": True}


@router.get("/protection")
def protection(kind: str, ctx: Ctx = Depends(current)):
    """What is and is not stored for generated media, from the live configuration."""
    _kind(kind)
    _gate(ctx, kind)
    days = _val(ctx, "privacy.chat_retention_days")
    ephemeral_ok = bool(_val(ctx, "privacy.ephemeral_allowed") and ctx.principal.can("chat.ephemeral"))
    lines = [
        {"topic": "Where it is processed", "status": "info",
         "text": "Your prompt is sent in readable form to the inference backend that generates the result. "
                 "This is not end-to-end encryption."},
        {"topic": "Stored results", "status": "info" if days != 0 else "ok",
         "text": ("This organisation stores no generated files. Results are held in server memory for "
                  f"{media_svc.EPHEMERAL_TTL // 60} minutes so you can download them, then discarded." if days == 0 else
                  "Results are saved to your gallery, encrypted on the server with a key held on the server, and "
                  + (f"deleted automatically after {days} days." if days > 0 else "kept until you delete them."))},
        {"topic": "Prompts", "status": "info",
         "text": "Saved prompts and settings are encrypted with your results. The inference engine also keeps a record of "
                 "each job (including the prompt) in its own job history, which Semechka cannot remove; the "
                 "result file is deleted from the engine as soon as Semechka has copied it."},
    ]
    if ephemeral_ok:
        lines.append({"topic": "No-save", "status": "ok",
                      "text": "With no-save on, the prompt, settings and result are not written to Semechka's database "
                              f"or disk. The result lives in server memory for {media_svc.EPHEMERAL_TTL // 60} minutes. "
                              "The engine's own job record (see above) still exists, and usage counts (no text) are kept."})
    lines.append({"topic": "Who can see it", "status": "info",
                  "text": "Only you can open your results through Semechka. Administrator screens do not show them."})
    return {"lines": lines, "ephemeral_available": ephemeral_ok}
