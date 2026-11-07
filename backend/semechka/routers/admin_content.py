"""Governed administrator access to other people's conversations.

Off unless the deployment policy `privacy.admin_content_access` is 'audited' AND the administrator holds the sensitive
permission `admin.content.read` by name. Every list and every read needs a stated reason, is written to the audit log,
and is listed to the owner (GET /api/me/content-access). There is no way to read content without leaving that trace.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import admin_media as X
from .. import chat_svc
from ..deps import Ctx, require
from ..models import Conversation, Model, User

router = APIRouter(prefix="/api/admin/content")


class ReasonIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


def _gate(ctx: Ctx) -> None:
    X.gate(ctx)


@router.post("/users/{uid}/conversations")
def list_for_user(uid: str, body: ReasonIn, ctx: Ctx = Depends(require("admin.content.read"))):
    _gate(ctx)
    X.throttle(ctx)
    scope = X.scope_for(ctx)
    u = ctx.s.get(User, uid)
    if u is None or not scope.allows(ctx.s, uid):  # outside the administrator's organisations: the same answer as a missing person
        raise HTTPException(404, {"code": "not_found", "message": "No such person."})
    rows = ctx.s.execute(select(Conversation).where(Conversation.user_id == uid)
                         .order_by(Conversation.updated_at.desc()).limit(500)).scalars().all()
    X.record(ctx, "content.list", uid, {"owner": uid, "reason": body.reason.strip(), "count": len(rows), "scope": scope.label}, uid)
    return [{"id": c.id, "title": ctx.st.vault.decrypt(c.title_enc, "title"), "updated_at": c.updated_at,
             "mode": c.mode} for c in rows]


@router.post("/conversations/{cid}")
def read_conversation(cid: str, body: ReasonIn, ctx: Ctx = Depends(require("admin.content.read"))):
    """One conversation: its current branch with, for each message, the model, the generation job and its results, and the attachments and
    reference pictures, so images and videos appear where they were made. Media files are opened separately (and audited again)."""
    _gate(ctx)
    X.throttle(ctx)
    scope = X.scope_for(ctx)
    c = ctx.s.get(Conversation, cid)
    if c is None or not scope.allows(ctx.s, c.user_id):
        raise HTTPException(404, {"code": "not_found", "message": "Conversation not found."})
    if c.expires_at and c.expires_at < time.time():
        raise HTTPException(410, {"code": "expired", "message": "This conversation is past its retention period and is no longer available."})
    rows = chat_svc.load_rows(ctx.s, c.id)
    kids = chat_svc.children(rows)
    path = chat_svc.path_to(rows, chat_svc.leaf_of(c, rows, kids))
    models = {m.id: m.alias for m in ctx.s.execute(select(Model)).scalars()}
    owner = ctx.s.get(User, c.user_id)
    ctxm = X.message_context(ctx.st, ctx.s, c.user_id, path, ctx.user.id)
    media_n = sum(1 for m in path if ctxm[m.id]["media"])
    X.record(ctx, "content.read", cid, {"owner": c.user_id, "reason": body.reason.strip(), "messages": len(path), "media_items": media_n, "scope": scope.label}, c.user_id)
    return {"id": c.id, "title": ctx.st.vault.decrypt(c.title_enc, "title"), "mode": c.mode, "model": X._model_ref({k: v for k, v in X._models(ctx.s).items()}, c.model_id),
            "created_at": c.created_at, "updated_at": c.updated_at, "expires_at": c.expires_at,
            "owner": {"id": c.user_id, "email": owner.email if owner else None, "display_name": owner.display_name if owner else None},
            "branches": sum(1 for v in kids.values() if len(v) > 1),
            "messages": [{"id": m.id, "role": m.role, "kind": m.kind, "created_at": m.created_at,
                          "model": models.get(m.model_id), "model_id": m.model_id, "status": m.status,
                          "content": ctx.st.vault.decrypt(m.content_enc, "content"),
                          "attachments": len((m.meta or {}).get("attachments") or []), **ctxm[m.id]} for m in path]}
