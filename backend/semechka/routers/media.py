"""Image and video generation API. Every route enforces the mode gate, ownership and quotas itself."""
from __future__ import annotations

import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.datastructures import UploadFile as StarUploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import imajutsu_presets as presets
from .. import imajutsu_svc as isvc
from .. import media_lib, media_svc, services
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
                       "references": int(info.get("references") or 0),
                       "schedulers": info.get("schedulers") or [], "limits": info.get("limits") or {},
                       "defaults": media_svc.option_defaults(kind, m, size, vmax),
                       "loaded": (m.meta or {}).get("loaded"),
                       "profile": media_svc.profile_info(m)})
    ephemeral = bool(_val(ctx, "privacy.ephemeral_allowed") and ctx.principal.can("chat.ephemeral"))
    return {"kind": kind, "models": models, "ephemeral": ephemeral, "retention_days": _val(ctx, "privacy.chat_retention_days"),
            "prompt_max": media_svc.PROMPT_MAX, "video_max_seconds": vmax if kind == "video" else None,
            "caps": {"concurrent": _val(ctx, "media.max_concurrent_jobs"), "profile_upload_max_mb": _val(ctx, "media.profile_upload_max_mb"), "max_assets": _val(ctx, "media.max_assets"),
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
    preset: Literal["iphone_photo"] | None = None  # image only: the shared preset (prompt builder, 9:16, routed wording); see preset_params
    scenes: list[str] = Field(default_factory=list, max_length=12)


def admit_job(ctx: Ctx, save: bool, kind: str) -> bool:
    """Server-side limits for one new generation. Returns whether the result will be saved."""
    try:
        return media_svc.admit(ctx.st, ctx.s, ctx.principal, ctx.user.id, save, kind)
    except media_svc.AdmitError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from None


def check_params(ctx: Ctx, kind: str, m: Model, body: dict) -> dict:
    try:
        return media_svc.validate_params(kind, m, body, _val(ctx, "media.video_max_seconds"))
    except media_svc.ParamError as e:
        raise HTTPException(422, {"code": "invalid", "message": str(e)}) from e


async def preset_params(ctx: Ctx, kind: str, m: Model, prompt: str, media_in: dict, scenes: list[str] | None, *, keep_size: bool = False) -> tuple[dict, dict]:
    """The iPhone Photo preset for a picture made from a chat (or the media API): the SAME builder, 9:16 size, steps and wording rules as Imajutsu (it runs
    Imajutsu's plan for the model that was chosen), so there is one implementation. -> (the request for ``check_params``, what to remember about it: id, scenes,
    label, size, model). ``keep_size``: a regenerate keeps the size of the first picture instead of insisting on one in ``media_in``."""
    if kind != "image":
        raise HTTPException(422, {"code": "invalid", "message": "iPhone Photo makes pictures, not videos."})
    raw: dict = {"mode": "iphone_photo", "prompt": prompt, "negative": str(media_in.get("negative_prompt") or ""), "model": m.id,
                 "preset": {"id": presets.PRESET_ID, "scenes": scenes or [], "finish": False, "tier": None}}
    seed = media_in.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool) and seed >= 0:
        raw["seed"] = seed
    w, h = media_in.get("width"), media_in.get("height")
    if isinstance(w, int) and isinstance(h, int) and not isinstance(w, bool) and not isinstance(h, bool) and w > 0 and h > 0:
        if abs(w / h - 9 / 16) <= 0.015:
            raw["size"] = {"native": {"width": w, "height": h}}
        elif not keep_size:
            raise HTTPException(422, {"code": "invalid", "message": f"iPhone Photo is 9:16 (for example 720×1280); {w}×{h} is another shape. Choose a 9:16 size or switch the preset off."})
    try:
        spec = isvc.normalize_spec(ctx.st, ctx.s, ctx.user.id, raw)
        r = await isvc.build_plan(ctx.st, ctx.s, ctx.user, spec, estimate=False)
    except isvc.ImajutsuError as e:
        raise HTTPException(e.status, {"code": e.code, "message": e.message}) from e
    pln = r["plan"]
    if pln["errors"]:
        raise HTTPException(422, {"code": "plan_errors", "message": " ".join(pln["errors"])})
    body = {"prompt": pln["prompt"]["final"], "width": pln["work"]["width"], "height": pln["work"]["height"], "steps": pln["steps"], "guidance": pln["guidance"],
            "seed": media_in.get("seed"), **({"negative_prompt": pln["prompt"]["negative"]} if pln["prompt"]["negative_sent"] else {})}
    return body, {"id": presets.PRESET_ID, "label": presets.PRESET_LABEL, "scenes": list((pln["prompt"].get("preset") or {}).get("scenes") or []),
                  "width": pln["work"]["width"], "height": pln["work"]["height"], "warnings": pln["warnings"]}


def backend_error(e: AdapterError) -> HTTPException:
    return _backend_error(e)


@router.post("/profile-jobs")
async def create_profile_job(request: Request, ctx: Ctx = Depends(current)):
    """Edit or transform a video with a composite profile (OpenJutsu ...). multipart/form-data: model_id, save, inputs (JSON of plain
    settings incl. the prompt) and file parts named ``file:<input>`` (repeat the name for several files). The engine runs it as a durable
    job; the result is collected and stored exactly like any other generation."""
    _gate(ctx, "video")
    s, st, p = ctx.s, ctx.st, ctx.principal
    form = await request.form()
    m = s.get(Model, str(form.get("model_id") or ""))
    b = s.get(Backend, m.backend_id) if m else None
    if m is None or b is None or not media_svc.profile_info(m):
        raise HTTPException(404, {"code": "not_found", "message": "That editing profile was not found."})
    ok, why = services.model_access(s, p, m, b, st, "video")
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
    # Files already in the person's library (uploaded, edited or generated) are named by id: ``asset:<input>`` = <asset id>, in order.
    picked: dict[str, list] = {}
    for key, val in form.multi_items():
        if key.startswith("asset:") and isinstance(val, str):
            got = media_lib.owned(st, s, ctx.user.id, val.strip())
            if got is None:
                raise HTTPException(422, {"code": "asset_missing", "message": "A selected file is no longer in your library. Remove it and add it again."})
            picked.setdefault(key[6:], []).append((val.strip(), got))
            info = media_lib.mem_info(st, got) if isinstance(got, str) else {"mime": got.mime, "size": got.size}
            sizes.setdefault(key[6:], []).append((info["mime"], info["size"]))
    if sum(len(v) for v in picked.values()) > 12:
        raise HTTPException(422, {"code": "invalid", "message": "At most 12 library files can be used at once."})
    days = _val(ctx, "privacy.chat_retention_days")
    save = admit_job(ctx, str(form.get("save", "true")).lower() != "false", "video")
    try:
        clean = media_svc.validate_profile_inputs(m, inputs, sizes, int(_val(ctx, "media.profile_upload_max_mb")) * 2**20)
    except media_svc.ParamError as e:
        raise HTTPException(422, {"code": "invalid", "message": str(e)}) from e
    s.commit()
    expires = time.time() + days * 86400 if days and days > 0 else None
    try:
        with media_lib.materialise(st, [g for lst in picked.values() for _, g in lst]) as opened:
            it = iter(opened)
            for name, lst in picked.items():
                for _aid, _g in lst:
                    fh, mime, nm, _k = next(it)
                    uploads.setdefault(name, []).append((fh, mime, nm))
            jid = await media_svc.submit_profile(st, ctx.user, m, b, clean, uploads, save, expires,
                                                 sources={n: [i for i, _ in lst] for n, lst in picked.items()} or None)
    except AdapterError as e:
        raise _backend_error(e) from e
    await media_svc.refresh(st, jid)
    s.expire_all()
    return {**media_svc.job_out(st, s, p, s.get(MediaJob, jid)), "forced_unsaved": days == 0 and str(form.get("save", "true")).lower() != "false"}


def _size(fo) -> int:
    pos = fo.tell()
    fo.seek(0, 2)
    n = fo.tell()
    fo.seek(pos)
    return n


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
    if media_svc.profile_info(m):
        raise HTTPException(422, {"code": "invalid", "message": "This is an editing profile; send it with its source files (profile-jobs)."})
    days = _val(ctx, "privacy.chat_retention_days")
    save = admit_job(ctx, body.save, body.kind)
    req = body.model_dump()
    if body.preset:
        req, _meta = await preset_params(ctx, body.kind, m, body.prompt, req, body.scenes)
    params = check_params(ctx, body.kind, m, req)
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
def asset_content(aid: str, request: Request, download: bool = False, preview: bool = False, ctx: Ctx = Depends(current)):
    """The file, by byte range. Same 404 for missing and for someone else's: existence is not disclosed. ``preview`` asks for the converted
    copy of a video the browser cannot play, when there is one."""
    from . import media_assets
    return media_assets.serve_content(request, ctx, aid, download, preview)


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
    lines.append({"topic": "Files you add",
                  "text": "Pictures and videos you upload are encrypted on the server in the same way as results (kept for "
                          + (f"{days} days" if days and days > 0 else "as long as you keep them" if days != 0 else "a few minutes only: this organisation stores no files")
                          + "). While one is being checked, previewed or edited, a temporary plain copy exists on the server's disk for a few seconds "
                            "(in memory for no-save items) and is then deleted. Only you can open them; they are sent to the inference backend only "
                            "when you use one in a request."})
    lines.append({"topic": "Who can see it", "status": "info",
                  "text": "Only you can open your results through Semechka. Administrator screens do not show them."})
    return {"lines": lines, "ephemeral_available": ephemeral_ok}
