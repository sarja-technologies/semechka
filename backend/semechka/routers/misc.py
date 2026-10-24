from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy import select

from .. import telemetry
from ..deps import Ctx, current, get_db, get_state, require, audit_ctx
from ..models import Meta
from ..state import AppState

router = APIRouter(prefix="/api")

_MAX_ASSET = 512 * 1024
_SIGS = [(b"\x89PNG\r\n\x1a\n", "image/png", "png"), (b"RIFF", "image/webp", "webp"), (b"\xff\xd8\xff", "image/jpeg", "jpg")]


def _sniff(data: bytes):
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", "webp"
    for sig, mime, ext in _SIGS:
        if sig != b"RIFF" and data.startswith(sig):
            return mime, ext
    head = data[:512].lstrip().lower()
    if head.startswith(b"<svg") or head.startswith(b"<?xml"):
        return "image/svg+xml", "svg"
    return None


@router.post("/admin/assets")
async def upload_asset(file: UploadFile = File(...), ctx: Ctx = Depends(require("admin.branding"))):
    data = await file.read(_MAX_ASSET + 1)
    if len(data) > _MAX_ASSET:
        raise HTTPException(413, {"code": "too_large", "message": "Images are limited to 512 KB."})
    kind = _sniff(data)
    if not kind:
        raise HTTPException(415, {"code": "unsupported_type", "message": "Use a PNG, WebP, JPEG or SVG image."})
    mime, ext = kind
    if mime == "image/svg+xml" and re.search(rb"<script|onload=|onerror=|javascript:", data, re.I):
        raise HTTPException(422, {"code": "unsafe_svg", "message": "That SVG contains scripting and was rejected."})
    aid = hashlib.sha256(data).hexdigest()[:24]
    d = ctx.st.cfg.data_dir / "assets"
    d.mkdir(exist_ok=True, mode=0o700)
    (d / f"{aid}.{ext}").write_bytes(data)
    audit_ctx(ctx, "asset.upload", aid, {"type": mime, "bytes": len(data)})
    return {"id": f"{aid}.{ext}", "url": f"/api/assets/{aid}.{ext}"}


@router.get("/assets/{name}")
def get_asset(name: str, st: AppState = Depends(get_state)):
    if not re.fullmatch(r"[0-9a-f]{24}\.(png|webp|jpg|svg)", name):
        raise HTTPException(404)
    p = st.cfg.data_dir / "assets" / name
    if not p.exists():
        raise HTTPException(404)
    mime = {"png": "image/png", "webp": "image/webp", "jpg": "image/jpeg", "svg": "image/svg+xml"}[name.rsplit(".", 1)[1]]
    headers = {"X-Content-Type-Options": "nosniff", "Cache-Control": "public, max-age=3600",
               "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}
    return Response(p.read_bytes(), media_type=mime, headers=headers)


@router.get("/system/resources")
def my_resources(ctx: Ctx = Depends(current)):
    """Live resource readings, filtered to exactly what the caller's technical-visibility grants allow."""
    p = ctx.principal
    out: dict = {}
    if p.can("tech.resources.gpu"):
        g = telemetry.gpus()
        out["gpus"] = g
        out["gpu_note"] = "No NVIDIA tooling found on this host." if g is None else ""
    if p.can("tech.resources.host"):
        h = telemetry.host()
        out["host"] = {k: h.get(k) for k in ("cpu_count", "cpu_model", "memory_total_mb", "memory_available_mb", "load_1m")}
        out["storage"] = telemetry.storage(str(ctx.st.cfg.data_dir))
    if not out:
        raise HTTPException(403, {"code": "forbidden", "message": "You do not have access to resource information."})
    return out


@router.get("/health")
def health(s=Depends(get_db), st: AppState = Depends(get_state)):
    """Liveness for installers and monitors. Reveals nothing about configuration."""
    s.execute(select(Meta).limit(1)).first()
    return {"ok": True}
