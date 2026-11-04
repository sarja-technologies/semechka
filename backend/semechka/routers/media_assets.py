"""Uploads, previews, editing and listing of the person's media library (pictures and videos they added, edited copies, generated results).

Generated results keep their routes in media.py; this file adds what uploads need: a streaming upload, preview pictures and filmstrips, real
trim/crop/rotate (ffmpeg) into a new asset, and one listing for the Library. Every route is owner-only; a missing item and someone else's
look the same."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.requests import ClientDisconnect

from .. import media_lib, media_svc, services
from ..deps import Ctx, current
from ..media_lib import MediaError

router = APIRouter(prefix="/api/media")


def _val(ctx: Ctx, key: str):
    return ctx.st.settings.value_for(ctx.s, key, ctx.principal)


def _fail(e: MediaError) -> HTTPException:
    return HTTPException(e.status, {"code": e.code, "message": e.message})


def attach_ok(ctx: Ctx) -> bool:
    return bool(_val(ctx, "chat.attachments") and _val(ctx, "ui.file_upload") and ctx.principal.can("chat.attach"))


def mode_ok(ctx: Ctx, kind: str) -> bool:
    return bool(services.mode_state(ctx.s, ctx.principal, ctx.st, kind)["enabled"])


def library_ok(ctx: Ctx) -> bool:
    """May this person keep pictures and videos here at all: attachments are on for them, or picture or video mode is."""
    return attach_ok(ctx) or mode_ok(ctx, "image") or mode_ok(ctx, "video")


def need_library(ctx: Ctx) -> None:
    if not library_ok(ctx):
        raise HTTPException(403, {"code": "disabled", "message": "Adding pictures and videos is turned off for you by your administrator."})


def ephemeral_allowed(ctx: Ctx) -> bool:
    return bool(_val(ctx, "privacy.ephemeral_allowed") and ctx.principal.can("chat.ephemeral"))


def _expires(ctx: Ctx) -> float | None:
    days = _val(ctx, "privacy.chat_retention_days")
    return time.time() + days * 86400 if days and days > 0 else None


def asset_gate(ctx: Ctx, kind: str, origin: str) -> None:
    """Generated results follow the picture/video mode switches as before; uploaded and edited items follow the attachment switches too."""
    if origin in media_lib.ORIGINS:
        need_library(ctx)
    else:
        from .media import _gate
        _gate(ctx, kind)


def find(ctx: Ctx, aid: str):
    """-> (row or None, in-memory id or None). 404 for missing and foreign alike."""
    got = media_lib.owned(ctx.st, ctx.s, ctx.user.id, aid)
    if got is None:
        raise HTTPException(404, {"code": "not_found", "message": "Not found."})
    return (None, got) if isinstance(got, str) else (got, None)


# ---- upload --------------------------------------------------------------------------------------------
@router.post("/uploads")
async def upload(request: Request, name: str = "", ephemeral: bool = False, ctx: Ctx = Depends(current)):
    """One file per request, sent as the raw body (``Content-Type`` is ignored: the bytes decide). Streamed to disk; never held in memory.
    The browser shows real progress and can cancel by aborting the request, which leaves nothing behind."""
    need_library(ctx)
    if ephemeral and not ephemeral_allowed(ctx):
        raise HTTPException(403, {"code": "ephemeral_forbidden", "message": "No-save conversations are not available to you."})
    days = _val(ctx, "privacy.chat_retention_days")
    if days == 0:
        ephemeral = True  # this organisation stores nothing
    cap = int(_val(ctx, "media.upload_max_mb")) * 2**20
    if not ephemeral:
        quota = _val(ctx, "media.max_assets")
        if quota and media_svc.count_assets(ctx.s, ctx.user.id) >= quota:
            raise HTTPException(409, {"code": "gallery_full", "message": f"Your library holds {quota} items, the maximum. Delete some to add more."})
    expires = _expires(ctx)
    ctx.s.commit()  # release the request's transaction: the upload can take minutes
    try:
        declared = int(request.headers.get("content-length", ""))
    except ValueError:
        declared = None
    try:
        asset = await media_lib.ingest(ctx.st, user_id=ctx.user.id, name=name, body=request.stream(), declared=declared, cap=cap, ephemeral=ephemeral,
                                       expires_at=None if ephemeral else expires)
    except MediaError as e:
        raise _fail(e) from e
    except ClientDisconnect:
        return Response(status_code=499)
    return asset


# ---- listing and details -----------------------------------------------------------------------------------
@router.get("/assets")
def list_assets(kind: str | None = None, origin: str | None = None, limit: int = 60, before: float | None = None, ctx: Ctx = Depends(current)):
    """The person's library, newest first: generated results, uploads and edited copies. ``before`` is a created_at to continue from."""
    if kind not in (None, "image", "video") or origin not in (None, "generated", "upload", "edit"):
        raise HTTPException(422, {"code": "invalid", "message": "Unknown filter."})
    if not (library_ok(ctx)):
        raise HTTPException(403, {"code": "forbidden", "message": "Pictures and videos are not available to you."})
    media_svc.purge_mem(ctx.st)
    items = media_lib.list_assets(ctx.st, ctx.s, ctx.user.id, kind=kind, origin=origin, limit=max(1, min(limit, 200)), before=before)
    if before is None:  # in-memory (no-save) items of this person come first
        mem = [media_lib.mem_out(ctx.st, k) for k, v in media_lib._mem(ctx.st).items()
               if v["user_id"] == ctx.user.id and v["meta"].get("origin") in media_lib.ORIGINS and (not kind or v["meta"]["kind"] == kind)
               and (not origin or v["meta"].get("origin") == origin)]
        items = sorted(mem, key=lambda x: -x["created_at"]) + items
    return items


@router.get("/assets/{aid}")
def get_asset(aid: str, ctx: Ctx = Depends(current)):
    a, mem = find(ctx, aid)
    if mem:
        return media_lib.mem_out(ctx.st, mem)
    out = media_lib.asset_json(ctx.st, a)
    asset_gate(ctx, a.kind, out["origin"])
    return out


# ---- bytes -------------------------------------------------------------------------------------------------
def _range(header: str, size: int) -> tuple[int, int, bool] | None:
    """-> (start, end, partial) or None when not satisfiable."""
    if header.startswith("bytes=") and "," not in header:
        a, _, b = header[6:].partition("-")
        try:
            start = int(a) if a else max(0, size - int(b))
            end = int(b) if (a and b) else size - 1
        except ValueError:
            return 0, size - 1, False
        end = min(end, size - 1)
        if start > end or start >= size:
            return None
        return start, end, True
    return 0, size - 1, False


def stream_response(request: Request, st, a, *, suffix: str, size: int, mime: str, filename: str | None, cache: str) -> Response:
    r = _range(request.headers.get("range", ""), size)
    base = {"Accept-Ranges": "bytes", "Cache-Control": cache, "Content-Security-Policy": "default-src 'none'; sandbox", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'{"attachment" if filename else "inline"}{f"; filename={filename}" if filename else ""}'}
    if r is None:
        return Response(status_code=416, headers={"Content-Range": f"bytes */{size}", **base})
    start, end, partial = r
    headers = {**base, "Content-Length": str(end - start + 1), **({"Content-Range": f"bytes {start}-{end}/{size}"} if partial else {})}
    return StreamingResponse(media_lib.iter_asset(st, a, start, end, suffix), status_code=206 if partial else 200, media_type=mime, headers=headers)


def _safe_filename(n: str) -> str:
    return "".join(c if c.isalnum() or c in " ._-()" else "_" for c in n).strip() or "download"


def serve_content(request: Request, ctx: Ctx, aid: str, download: bool, preview: bool) -> Response:
    from .media import _ranged
    a, mem = find(ctx, aid)
    if mem:
        v = media_lib._mem(ctx.st)[mem]
        asset_gate(ctx, v["meta"]["kind"], v["meta"].get("origin") or "generated")
        data, mime = v["data"], v["mime"]
        name = _safe_filename(v["meta"].get("name") or f"semechka-{aid[-8:]}") if download else None
        return _ranged(data, mime, request, name)
    meta = media_lib.meta_of(ctx.st, a)
    origin = meta.get("origin") or "generated"
    asset_gate(ctx, a.kind, origin)
    nm = (_safe_filename(media_lib.display_name(meta, a.kind, a.id)) if origin in media_lib.ORIGINS
          else f"semechka-{a.kind}-{a.id[-8:]}.{media_svc.SAFE_MIME.get(a.mime, 'bin')}") if download else None
    try:
        if preview and not (meta.get("media") or {}).get("playable", True) and media_lib.preview_state(ctx.st, a.id, meta.get("media")) == "proxy":
            p = media_lib.asset_file(ctx.st, a.user_id, a.id, "proxy")
            return stream_response(request, ctx.st, a, suffix="proxy", size=media_lib._sealed_len(p), mime="video/mp4", filename=nm, cache="private, no-store")
        if media_lib.is_chunked(ctx.st, a):
            return stream_response(request, ctx.st, a, suffix="", size=a.size, mime=a.mime, filename=nm, cache="private, no-store")
        return _ranged(media_svc.read_asset(ctx.st, a), a.mime, request, nm)
    except (OSError, ValueError) as e:
        raise HTTPException(410, {"code": "gone", "message": "The file is no longer available."}) from e


async def _derived(ctx: Ctx, aid: str, suffix: str, make):
    """A preview picture or filmstrip, made once and kept (sealed like the file itself). ``make(path, probe)`` writes the JPEG."""
    a, mem = find(ctx, aid)
    st = ctx.st
    if mem:
        v = media_lib._mem(st)[mem]
        asset_gate(ctx, v["meta"]["kind"], v["meta"].get("origin") or "generated")
        cache = v.setdefault("derived", {})
        if suffix in cache:
            return cache[suffix]
        info = media_lib.mem_info(st, mem)
        target = mem
    else:
        meta = media_lib.meta_of(st, a)
        asset_gate(ctx, a.kind, meta.get("origin") or "generated")
        got = media_lib.read_sidecar(st, a, suffix)
        if got is not None:
            return got
        info = {"id": a.id, "kind": a.kind, "mime": a.mime, "size": a.size, "width": a.width, "height": a.height, "duration": a.duration,
                "media": meta.get("media") or {}}
        target = a
    if info["mime"] not in media_lib.SERVE_MIME:
        return None
    ctx.s.commit()
    locks = st.__dict__.setdefault("media_derive_locks", {})
    lock = locks.setdefault((aid, suffix), asyncio.Lock())
    async with lock:
        if not mem:
            got = media_lib.read_sidecar(st, a, suffix)
            if got is not None:
                return got
        try:
            media_lib.need_ffmpeg()
        except MediaError:
            return None
        with media_lib.scratch(st, bool(mem)) as tmp, media_lib.materialise(st, [target]) as [(fh, *_)]:
            fh.close()
            m = info["media"]
            sn = media_lib.Sniffed(info["kind"], info["mime"], m.get("demuxer") or media_lib.default_demuxer(info["mime"]))
            pr = media_lib.Probe(info["kind"], info["mime"], sn.demuxer, info["width"], info["height"], info["duration"], m.get("fps"), m.get("codec"),
                                 m.get("audio_codec"), int(m.get("orientation") or 1))
            out = tmp / "out.jpg"
            ok = await make(pr, Path(fh.name), out)
            data = out.read_bytes() if ok else None
    locks.pop((aid, suffix), None)
    if data:
        if mem:
            media_lib._mem(st)[mem].setdefault("derived", {})[suffix] = data
        else:
            media_lib.write_sidecar(st, a.user_id, a.id, suffix, data)
    return data


def _jpeg(data: bytes | None, cache: str = "private, max-age=600") -> Response:
    if not data:
        raise HTTPException(404, {"code": "no_preview", "message": "No preview is available."})
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff",
                                                          "Content-Security-Policy": "default-src 'none'; sandbox"})


@router.get("/assets/{aid}/thumb")
async def thumb(aid: str, ctx: Ctx = Depends(current)):
    """A small preview picture (about 480 px) of any picture or video; made on first request for older items."""
    st = ctx.st
    return _jpeg(await _derived(ctx, aid, "thumb", lambda pr, src, out: media_lib.make_thumb(st, pr, src, out)))


@router.get("/assets/{aid}/strip")
async def strip(aid: str, n: int = 12, h: int = 64, ctx: Ctx = Depends(current)):
    """Frames spread evenly across a video, side by side in one JPEG (the trim timeline). Each frame is ``image width / n`` wide."""
    n, h = max(6, min(media_lib.MAX_STRIP, n)), max(40, min(120, h))
    st = ctx.st
    a, mem = find(ctx, aid)
    kind = media_lib._mem(st)[mem]["meta"]["kind"] if mem else a.kind
    if kind != "video":
        raise HTTPException(422, {"code": "invalid", "message": "Only videos have a filmstrip."})
    return _jpeg(await _derived(ctx, aid, f"strip{n}x{h}", lambda pr, src, out: media_lib.make_strip(st, pr, src, out, n, h)), "private, max-age=3600")


# ---- editing ---------------------------------------------------------------------------------------------
class EditIn(BaseModel):
    trim: dict | None = None            # {start, end} seconds
    crop: dict | None = None            # {x, y, w, h} pixels, in the picture or frame as shown after rotating
    rotate: int = 0
    flip: bool = False
    mute: bool = False
    name: str | None = Field(default=None, max_length=120)


@router.post("/assets/{aid}/edits")
async def start_edit(aid: str, body: EditIn, ctx: Ctx = Depends(current)):
    """Trim, crop and rotate for real: ffmpeg writes a new file from the stored original, which is left untouched. Returns a task to follow."""
    need_library(ctx)
    a, mem = find(ctx, aid)
    if a is not None:
        asset_gate(ctx, a.kind, media_lib.origin_of(ctx.st, a))
    if not mem:
        quota = _val(ctx, "media.max_assets")
        if quota and media_svc.count_assets(ctx.s, ctx.user.id) >= quota:
            raise HTTPException(409, {"code": "gallery_full", "message": f"Your library holds {quota} items, the maximum. Delete some to make room."})
    spec = media_lib.EditSpec(start=(body.trim or {}).get("start"), end=(body.trim or {}).get("end"), crop=body.crop, rotate=body.rotate, flip=body.flip, mute=body.mute)
    expires = _expires(ctx)
    cap = int(_val(ctx, "media.upload_max_mb")) * 2**20
    ctx.s.commit()
    try:
        t = await media_lib.start_edit(ctx.st, user_id=ctx.user.id, aid=aid, spec=spec, name=body.name, expires_at=expires, ephemeral=bool(mem), cap=cap)
    except MediaError as e:
        raise _fail(e) from e
    return media_lib.task_out(t)


@router.get("/edits/{eid}")
def edit_status(eid: str, ctx: Ctx = Depends(current)):
    t = media_lib.tasks(ctx.st).get(eid)
    if t is None or t.user_id != ctx.user.id:
        raise HTTPException(404, {"code": "not_found", "message": "Not found."})
    return media_lib.task_out(t)


@router.post("/edits/{eid}/cancel")
def edit_cancel(eid: str, ctx: Ctx = Depends(current)):
    try:
        return media_lib.task_out(media_lib.cancel_edit(ctx.st, ctx.user.id, eid))
    except MediaError as e:
        raise _fail(e) from e
