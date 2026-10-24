"""What gets added to a chat request beyond the user's own messages: workspace prompt, assistant, retrieved passages.

Everything here is re-evaluated against the *current* permissions on every generation; a stored conversation never
keeps access that was withdrawn.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy import select

from . import knowledge_svc
from .models_knowledge import Assistant, Collection

PARAM_RANGES = {"temperature": (0.0, 2.0), "top_p": (0.0, 1.0), "top_k": (0, 1000), "min_p": (0.0, 1.0),
                "max_tokens": (16, 131072), "repeat_penalty": (0.5, 2.0), "seed": (0, 2**31 - 1)}


@dataclass
class Resolved:
    assistant: Assistant | None = None
    collections: list[Collection] = field(default_factory=list)
    skipped_collections: int = 0
    system_parts: list[str] = field(default_factory=list)
    assistant_params: dict = field(default_factory=dict)
    assistant_model_id: str | None = None


def clean_params(raw: dict | None) -> dict:
    out = {}
    for k, v in (raw or {}).items():
        if k not in PARAM_RANGES or v is None:
            continue
        lo, hi = PARAM_RANGES[k]
        try:
            n = float(v)
        except (TypeError, ValueError):
            raise HTTPException(422, {"code": "invalid", "message": f"{k} must be a number."}) from None
        if not (lo <= n <= hi):
            raise HTTPException(422, {"code": "invalid", "message": f"{k} must be between {lo} and {hi}."})
        out[k] = int(n) if k in ("top_k", "max_tokens", "seed") else n
    return out


def assistant_visible(ctx, a: Assistant) -> bool:
    if a.owner_id == ctx.user.id:
        return True
    if a.visibility == "org":
        return True
    return a.visibility == "team" and a.team_id in ctx.principal.team_ids


def get_assistant_for_use(ctx, assistant_id: str) -> Assistant:
    a = ctx.s.get(Assistant, assistant_id)
    if a is None or not assistant_visible(ctx, a):
        raise HTTPException(404, {"code": "not_found", "message": "Assistant not found."})
    return a


def check_collection_ids(ctx, ids: list[str]) -> list[Collection]:
    """Explicit selection by the user: anything not readable by them is a 404 (existence is not disclosed)."""
    out = []
    for cid in dict.fromkeys(ids or []):
        c = knowledge_svc.get_visible(ctx.s, ctx.principal, ctx.user.id, cid)
        if c is None:
            raise HTTPException(404, {"code": "not_found", "message": "Knowledge collection not found."})
        out.append(c)
    return out


def resolve(ctx, *, workspace: str, assistant_id: str | None, collection_ids: list[str] | None) -> Resolved:
    """Build the extra system text and the collections to search for this request."""
    r = Resolved()
    p = ctx.principal
    if workspace == "code":
        prompt = ctx.st.settings.value_for(ctx.s, "code.system_prompt", p)
        if prompt:
            r.system_parts.append(prompt)
    if assistant_id:
        a = get_assistant_for_use(ctx, assistant_id)
        if not p.can("mode.agents") or not ctx.st.settings.value_for(ctx.s, "modes.agents.enabled", p):
            raise HTTPException(403, {"code": "forbidden", "message": "Assistants are not available to you."})
        r.assistant = a
        r.system_parts.append(ctx.st.vault.decrypt(a.prompt_enc, "as.prompt"))
        r.assistant_params = dict(a.params or {})
        r.assistant_model_id = a.model_id
        collection_ids = list(dict.fromkeys((collection_ids or []) + list(a.collection_ids or [])))
    if collection_ids:
        if not p.can("mode.knowledge") or not ctx.st.settings.value_for(ctx.s, "modes.knowledge.enabled", p):
            raise HTTPException(403, {"code": "forbidden", "message": "Knowledge search is not available to you."})
        for cid in collection_ids:
            c = knowledge_svc.get_visible(ctx.s, p, ctx.user.id, cid)
            if c is None:
                r.skipped_collections += 1  # access was withdrawn or the collection was deleted since it was attached
            else:
                r.collections.append(c)
    return r


def retrieve(ctx, r: Resolved, query: str) -> list[dict]:
    """Run keyword search for `query`; adds the numbered-passages system text to `r` and returns source records."""
    if not r.collections or not query.strip():
        return []
    g = lambda k: ctx.st.settings.value_for(ctx.s, k, ctx.principal)  # noqa: E731
    hits = knowledge_svc.search(ctx.s, ctx.st, r.collections, query, g("knowledge.top_k"))
    text, sources = knowledge_svc.build_context(hits, g("knowledge.context_chars"))
    if text:
        r.system_parts.append(text)
    return sources


def visible_assistants(ctx) -> list[Assistant]:
    p = ctx.principal
    rows = ctx.s.execute(select(Assistant).order_by(Assistant.created_at)).scalars().all()
    return [a for a in rows if a.owner_id == ctx.user.id or a.visibility == "org"
            or (a.visibility == "team" and a.team_id in p.team_ids)]
