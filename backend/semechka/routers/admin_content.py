"""Governed administrator access to other people's conversations.

Off unless the deployment policy `privacy.admin_content_access` is 'audited' AND the administrator holds the sensitive
permission `admin.content.read` by name. Every list and every read needs a stated reason, is written to the audit log,
and is listed to the owner (GET /api/me/content-access). There is no way to read content without leaving that trace.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import chat_svc
from ..deps import Ctx, audit_ctx, require
from ..models import Conversation, Model, User

router = APIRouter(prefix="/api/admin/content")


class ReasonIn(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


def _gate(ctx: Ctx) -> None:
    if ctx.st.settings.deployment(ctx.s, "privacy.admin_content_access") != "audited":
        raise HTTPException(403, {"code": "disabled", "message": "Your organisation's privacy policy does not allow "
                                                                 "administrators to read conversations."})


@router.post("/users/{uid}/conversations")
def list_for_user(uid: str, body: ReasonIn, ctx: Ctx = Depends(require("admin.content.read"))):
    _gate(ctx)
    u = ctx.s.get(User, uid)
    if u is None:
        raise HTTPException(404, {"code": "not_found", "message": "No such person."})
    rows = ctx.s.execute(select(Conversation).where(Conversation.user_id == uid)
                         .order_by(Conversation.updated_at.desc()).limit(500)).scalars().all()
    audit_ctx(ctx, "content.list", uid, {"owner": uid, "reason": body.reason.strip(), "count": len(rows)})
    return [{"id": c.id, "title": ctx.st.vault.decrypt(c.title_enc, "title"), "updated_at": c.updated_at,
             "mode": c.mode} for c in rows]


@router.post("/conversations/{cid}")
def read_conversation(cid: str, body: ReasonIn, ctx: Ctx = Depends(require("admin.content.read"))):
    _gate(ctx)
    c = ctx.s.get(Conversation, cid)
    if c is None:
        raise HTTPException(404, {"code": "not_found", "message": "Conversation not found."})
    audit_ctx(ctx, "content.read", cid, {"owner": c.user_id, "reason": body.reason.strip()})
    rows = chat_svc.load_rows(ctx.s, c.id)
    kids = chat_svc.children(rows)
    path = chat_svc.path_to(rows, chat_svc.leaf_of(c, rows, kids))
    models = {m.id: m.alias for m in ctx.s.execute(select(Model)).scalars()}
    owner = ctx.s.get(User, c.user_id)
    return {"id": c.id, "title": ctx.st.vault.decrypt(c.title_enc, "title"),
            "owner": {"id": c.user_id, "email": owner.email if owner else None},
            "branches": sum(1 for v in kids.values() if len(v) > 1),
            "messages": [{"id": m.id, "role": m.role, "kind": m.kind, "created_at": m.created_at,
                          "model": models.get(m.model_id), "status": m.status,
                          "content": ctx.st.vault.decrypt(m.content_enc, "content"),
                          "attachments": len((m.meta or {}).get("attachments") or [])} for m in path]}
