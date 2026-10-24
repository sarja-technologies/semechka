"""Assistants: saved prompt + default model/parameters + optional knowledge collections.

An assistant only shapes what the model is told. It has no tools: it cannot run code, open files, browse or call
services. Autonomous tool-using agents are deliberately not part of this release.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import audit, chat_context, knowledge_svc
from ..deps import Ctx, current
from ..models import Backend, Model, Team
from ..models_knowledge import Assistant
from .chat import _need_mode

router = APIRouter(prefix="/api/assistants")


def _gate(ctx: Ctx):
    _need_mode(ctx, "agents")


def _can_edit(ctx: Ctx, a: Assistant) -> bool:
    return a.owner_id == ctx.user.id or (a.visibility != "private" and ctx.principal.can("admin.teams.manage")
                                          and (a.visibility == "org" or a.team_id in ctx.principal.team_ids))


def _out(ctx: Ctx, a: Assistant, *, full: bool = False) -> dict:
    v = ctx.st.vault
    team = ctx.s.get(Team, a.team_id) if a.team_id else None
    out = {"id": a.id, "name": v.decrypt(a.name_enc, "as.name"), "description": v.decrypt(a.description_enc, "as.name"),
           "visibility": a.visibility, "team_id": a.team_id, "team": team.name if team else None, "mine": a.owner_id == ctx.user.id,
           "can_edit": _can_edit(ctx, a), "model_id": a.model_id, "collection_ids": a.collection_ids or [], "params": a.params or {}}
    if full and out["can_edit"]:
        out["system_prompt"] = v.decrypt(a.prompt_enc, "as.prompt")  # only people who can edit it read the prompt back
    return out


class AssistantIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=500)
    system_prompt: str = Field(default="", max_length=8000)
    visibility: str = Field(default="private", pattern="^(private|team|org)$")
    team_id: str | None = None
    model_id: str | None = None
    params: dict | None = None
    collection_ids: list[str] = Field(default_factory=list, max_length=20)


def _validate(ctx: Ctx, body: AssistantIn) -> tuple[str | None, list[str]]:
    p = ctx.principal
    if body.visibility == "team":
        if not body.team_id or body.team_id not in p.team_ids:
            raise HTTPException(403, {"code": "forbidden", "message": "Choose a team you belong to."})
    elif body.visibility == "org" and not p.can("admin.teams.manage"):
        raise HTTPException(403, {"code": "forbidden", "message": "Only people who manage teams can share an assistant with everyone."})
    if body.model_id:
        from .. import services
        m = ctx.s.get(Model, body.model_id)
        b = ctx.s.get(Backend, m.backend_id) if m else None
        if not m or not services.model_access(ctx.s, p, m, b, ctx.st, "chat")[0]:
            raise HTTPException(403, {"code": "model_forbidden", "message": "You do not have access to that model."})
    ids = list(dict.fromkeys(body.collection_ids))
    if ids:
        cols = chat_context.check_collection_ids(ctx, ids)
        for c in cols:
            if body.visibility == "org":
                raise HTTPException(422, {"code": "invalid", "message": "Assistants shared with everyone cannot use knowledge collections yet; share them with a team instead."})
            if body.visibility == "team" and c.team_id != body.team_id:
                raise HTTPException(422, {"code": "invalid", "message": "A team assistant can only use that team's collections, so everyone it is shared with can read them."})
    return body.team_id if body.visibility == "team" else None, ids


@router.get("")
def list_assistants(ctx: Ctx = Depends(current)):
    _gate(ctx)
    return [_out(ctx, a) for a in chat_context.visible_assistants(ctx)]


@router.get("/{aid}")
def get_assistant(aid: str, ctx: Ctx = Depends(current)):
    _gate(ctx)
    return _out(ctx, chat_context.get_assistant_for_use(ctx, aid), full=True)


@router.post("")
def create_assistant(body: AssistantIn, ctx: Ctx = Depends(current)):
    _gate(ctx)
    team_id, ids = _validate(ctx, body)
    v = ctx.st.vault
    a = Assistant(owner_id=ctx.user.id, visibility=body.visibility, team_id=team_id, name_enc=v.encrypt(body.name.strip(), "as.name"),
                  description_enc=v.encrypt(body.description.strip(), "as.name"), prompt_enc=v.encrypt(body.system_prompt, "as.prompt"),
                  model_id=body.model_id, params=chat_context.clean_params(body.params), collection_ids=ids)
    ctx.s.add(a)
    ctx.s.flush()
    audit.record(ctx.s, ctx.user.id, "assistant.create", a.id, {"visibility": a.visibility}, ctx.ip)
    return _out(ctx, a, full=True)


@router.put("/{aid}")
def update_assistant(aid: str, body: AssistantIn, ctx: Ctx = Depends(current)):
    _gate(ctx)
    a = chat_context.get_assistant_for_use(ctx, aid)
    if not _can_edit(ctx, a):
        raise HTTPException(403, {"code": "forbidden", "message": "You can use this assistant but not change it."})
    team_id, ids = _validate(ctx, body)
    v = ctx.st.vault
    a.visibility, a.team_id, a.model_id, a.collection_ids = body.visibility, team_id, body.model_id, ids
    a.name_enc, a.description_enc = v.encrypt(body.name.strip(), "as.name"), v.encrypt(body.description.strip(), "as.name")
    a.prompt_enc, a.params = v.encrypt(body.system_prompt, "as.prompt"), chat_context.clean_params(body.params)
    import time
    a.updated_at = time.time()
    audit.record(ctx.s, ctx.user.id, "assistant.update", a.id, {"visibility": a.visibility}, ctx.ip)
    return _out(ctx, a, full=True)


@router.delete("/{aid}")
def delete_assistant(aid: str, ctx: Ctx = Depends(current)):
    _gate(ctx)
    a = chat_context.get_assistant_for_use(ctx, aid)
    if not _can_edit(ctx, a):
        raise HTTPException(403, {"code": "forbidden", "message": "You can use this assistant but not delete it."})
    ctx.s.delete(a)
    audit.record(ctx.s, ctx.user.id, "assistant.delete", aid, {}, ctx.ip)
    return {"ok": True}
