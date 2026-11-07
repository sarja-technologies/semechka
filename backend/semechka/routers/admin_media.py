"""Unified administrator explorer over every conversation and every media item of this instance (see admin_media.py for the model).

Same governance as admin_content.py: policy 'audited' + the permission ``admin.content.read`` by name + a stated reason on every list,
open, preview and download, one audit row each, shown to the owner at GET /api/me/content-access. Metadata only in lists; a file is
streamed from the existing encrypted store through a short-lived link bound to the administrator who asked for it.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import admin_media as X
from .. import attachments_svc, media_lib, media_svc
from ..deps import Ctx, require
from ..media_models import MediaAsset
from ..models import ChatAttachment, Model, User

router = APIRouter(prefix="/api/admin/content/explorer")


class ConvFilters(BaseModel):
    user_id: str | None = Field(default=None, max_length=40)
    model_id: str | None = Field(default=None, max_length=40)
    mode: str | None = Field(default=None, max_length=16)
    status: str | None = Field(default=None, max_length=12)
    media: str | None = Field(default=None, max_length=8)
    q: str | None = Field(default=None, max_length=120)
    since: float | None = None
    until: float | None = None
    kind: str | None = None
    workflow: str | None = None


class MediaFilters(BaseModel):
    user_id: str | None = Field(default=None, max_length=40)
    model_id: str | None = Field(default=None, max_length=40)
    kind: str | None = Field(default=None, max_length=8)
    workflow: str | None = Field(default=None, max_length=24)
    status: str | None = Field(default=None, max_length=12)
    conversation_id: str | None = Field(default=None, max_length=40)
    since: float | None = None
    until: float | None = None


class ListIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    cursor: str | None = Field(default=None, max_length=80)
    limit: int = Field(default=30, ge=1, le=100)


class ConvListIn(ListIn):
    filters: ConvFilters = Field(default_factory=ConvFilters)


class MediaListIn(ListIn):
    filters: MediaFilters = Field(default_factory=MediaFilters)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class AccessIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)
    type: str = Field(pattern="^(asset|attachment)$")
    id: str = Field(min_length=3, max_length=40)
    mode: str = Field(default="view", pattern="^(view|download)$")


def _audit_list(ctx: Ctx, action: str, scope: X.Scope, f: X.Filters, body: ListIn, out: dict) -> None:
    owners = out["owners"][:100]
    detail = {"reason": body.reason.strip(), "count": len(out["items"]), "scope": scope.label, "filters": f.audit(), "scanned": out["scanned"], "owners": owners}
    if f.user_id:
        detail["owner"] = f.user_id
    X.record(ctx, action, f.user_id or "*", detail)


@router.get("/filters")
def filters(ctx: Ctx = Depends(require("admin.content.read"))):
    """What the filter bars offer: people in the administrator's reach and models. Directory metadata only; no content, so no reason and no audit row."""
    scope = X.begin(ctx)
    users = ctx.s.execute(select(User).where(scope.users(User.id), User.status != "deleted").order_by(User.display_name).limit(2000)).scalars().all()
    models = ctx.s.execute(select(Model).order_by(Model.alias)).scalars().all()
    return {"scope": scope.label, "users": [{"id": u.id, "label": f"{u.display_name} ({u.email})"} for u in users],
            "models": [{"id": m.id, "alias": m.alias, "kind": next((k for k in ("video", "image") if k in (m.capabilities or [])), "chat")} for m in models],
            "workflows": list(X.WORKFLOWS), "statuses": list(X.STATUS_FILTERS), "limits": {"conversations": X.MAX_CONV_PAGE, "media": X.MAX_MEDIA_PAGE, "scan": X.SCAN_CAP}}


@router.post("/conversations")
def conversations(body: ConvListIn, ctx: Ctx = Depends(require("admin.content.read"))):
    scope = X.begin(ctx)
    f = X.Filters(body.filters)
    out = X.list_conversations(ctx.st, ctx.s, scope, f, X.parse_cursor(body.cursor), body.limit)
    _audit_list(ctx, "content.list", scope, f, body, out)
    return out


@router.post("/media")
def media(body: MediaListIn, ctx: Ctx = Depends(require("admin.content.read"))):
    scope = X.begin(ctx)
    f = X.Filters(body.filters)
    out = X.list_media(ctx.st, ctx.s, scope, f, ctx.user.id, X.parse_cursor(body.cursor), body.limit)
    _audit_list(ctx, "media.admin.list", scope, f, body, out)
    return out


@router.post("/media/jobs/{jid}")
def job(jid: str, body: ReasonIn, ctx: Ctx = Depends(require("admin.content.read"))):
    scope = X.begin(ctx)
    d = X.job_detail(ctx.st, ctx.s, scope, jid, ctx.user.id)
    X.record(ctx, "media.admin.view", jid, {"owner": d["owner"]["id"], "reason": body.reason.strip(), "item": "job", "kind": d["kind"], "status": d["status"],
                                             "workflow": d["workflow"], "scope": scope.label}, d["owner"]["id"])
    return d


@router.post("/media/assets/{aid}")
def asset(aid: str, body: ReasonIn, ctx: Ctx = Depends(require("admin.content.read"))):
    scope = X.begin(ctx)
    d = X.asset_detail(ctx.st, ctx.s, scope, aid, ctx.user.id)
    X.record(ctx, "media.admin.view", aid, {"owner": d["owner"]["id"], "reason": body.reason.strip(), "item": "asset", "kind": d["kind"], "scope": scope.label},
             d["owner"]["id"])
    return d


@router.post("/files/access")
def access(body: AccessIn, ctx: Ctx = Depends(require("admin.content.read"))):
    """Ask to preview or download one stored file. One audit row (media.admin.view or media.admin.download) is written now, and a link valid for a
    few minutes is returned; the link works only for this administrator and only while the gate, the permission and the scope still hold."""
    scope = X.begin(ctx)
    now = time.time()
    if body.type == "asset":
        row = ctx.s.get(MediaAsset, body.id) if X._ID.match(body.id) else None
        if row is None or not scope.allows(ctx.s, row.user_id):
            raise X.not_found()
        state = X.asset_summary(ctx.st, row, ctx.user.id, now, thumbs=False)
        name = f"semechka-{row.kind}-{row.id[-8:]}.{media_svc.SAFE_MIME.get(row.mime) or media_lib.SERVE_MIME.get(row.mime, 'bin')}"
    else:
        row = ctx.s.get(ChatAttachment, body.id) if X._ID.match(body.id) else None
        if row is None or not scope.allows(ctx.s, row.user_id):
            raise X.not_found()
        state = X.attachment_summary(ctx.st, row, ctx.user.id, now, thumbs=False, name=False)
        name = f"semechka-attachment-{row.id[-8:]}"
    if state["state"] == "expired":
        raise HTTPException(410, {"code": "expired", "message": "This file is past its retention period and is no longer available."})
    if not state["available"]:
        raise HTTPException(410, {"code": "gone", "message": "The file is no longer available."})
    action = "media.admin.download" if body.mode == "download" else "media.admin.view"
    X.record(ctx, action, row.id, {"owner": row.user_id, "reason": body.reason.strip(), "item": body.type, "kind": row.kind, "file": True, "scope": scope.label}, row.user_id)
    return {"url": X.blob_url(ctx.st, ctx.user.id, body.type, row.id, body.mode), "expires_in": X.TICKET_TTL, "mime": row.mime, "size": row.size,
            "filename": name if body.mode == "download" else None}


def _gone(code: str, message: str) -> HTTPException:
    return HTTPException(410, {"code": code, "message": message})


@router.get("/blob/{ticket}")
def blob(ticket: str, request: Request, ctx: Ctx = Depends(require("admin.content.read"))):
    """The bytes, streamed from the existing store with byte-range support. Everything is checked again here: the link is the administrator's own,
    unexpired, and the policy, the permission and the scope still allow it. Nothing is written to disk and nothing is cached by the browser."""
    X.gate(ctx)
    X.throttle(ctx, X.RATE_BLOBS, "blob")
    got = X.read_ticket(ctx.st, ticket, ctx.user.id)
    if got is None:
        raise HTTPException(403, {"code": "ticket", "message": "This link has expired or is not yours. Open the item again."})
    typ, rid, mode = got
    scope = X.scope_for(ctx)
    now = time.time()
    if typ == "attachment":
        row = ctx.s.get(ChatAttachment, rid)
        if row is None or not scope.allows(ctx.s, row.user_id):
            raise X.not_found()
        if X._expired(row, now):
            raise _gone("expired", "This file is past its retention period and is no longer available.")
        if not X.attachment_exists(ctx.st, row):
            raise _gone("gone", "The file is no longer available.")
        try:
            data = ctx.st.vault.decrypt_bytes(attachments_svc.path_for(ctx.st.cfg, row.user_id, row.id).read_bytes(), "attachment", row.id)
        except (OSError, ValueError) as e:
            raise _gone("gone", "The file is no longer available.") from e
        if mode == "thumb" and (row.kind != "image" or row.size > X.THUMB_ORIGINAL_MAX):
            raise HTTPException(404, {"code": "no_preview", "message": "No preview is available."})
        from .media import _ranged
        mime = row.mime if row.mime.startswith("image/") else "text/plain; charset=utf-8"
        return _ranged(data, mime, request, f"semechka-attachment-{row.id[-8:]}" if mode == "download" else None)
    a = ctx.s.get(MediaAsset, rid)
    if a is None or not scope.allows(ctx.s, a.user_id):
        raise X.not_found()
    if X._expired(a, now):
        raise _gone("expired", "This file is past its retention period and is no longer available.")
    if not X.asset_exists(ctx.st, a):
        raise _gone("gone", "The file is no longer available.")
    from .media import _ranged
    from .media_assets import stream_response
    try:
        if mode == "thumb":
            side = media_lib.read_sidecar(ctx.st, a, "thumb")
            if side:
                return Response(side, media_type="image/jpeg", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                                                                         "Content-Security-Policy": "default-src 'none'; sandbox"})
            if a.kind != "image" or a.size > X.THUMB_ORIGINAL_MAX:
                raise HTTPException(404, {"code": "no_preview", "message": "No preview is available."})
        name = f"semechka-{a.kind}-{a.id[-8:]}.{media_svc.SAFE_MIME.get(a.mime) or media_lib.SERVE_MIME.get(a.mime, 'bin')}" if mode == "download" else None
        if media_lib.is_chunked(ctx.st, a):
            return stream_response(request, ctx.st, a, suffix="", size=a.size, mime=a.mime, filename=name, cache="private, no-store")
        return _ranged(media_svc.read_asset(ctx.st, a), a.mime, request, name)
    except (OSError, ValueError) as e:
        raise _gone("gone", "The file is no longer available.") from e
