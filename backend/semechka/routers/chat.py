from __future__ import annotations

import asyncio
import json
import re
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import audit, chat_context, services
from ..adapters import AdapterError
from ..backends_svc import adapter_for
from ..deps import Ctx, current
from ..models import Backend, Conversation, Message, Model, Role, UsageEvent, uid
from ..models_knowledge import ConversationContext, MessageSources
from ..scheduler import QueueError
from ..state import AppState

router = APIRouter(prefix="/api/chat")

PARAM_KEYS = ("temperature", "top_p", "top_k", "min_p", "max_tokens", "seed", "repeat_penalty")


def _need_mode(ctx: Ctx, mode: str = "chat"):
    """Mode gate used by every chat route: UI hiding is never the boundary."""
    st = services.mode_state(ctx.s, ctx.principal, ctx.st, mode)
    if st["enabled"]:
        return
    code = {"disabled": 403, "forbidden": 403, "no_model": 409, "unsupported": 501}.get(st.get("code"), 403)
    raise HTTPException(code, {"code": st.get("code", "forbidden"), "message": st["reason"]})


def _title(ctx: Ctx, c: Conversation) -> str:
    return ctx.st.vault.decrypt(c.title_enc, "title")


def _text(ctx: Ctx, m: Message) -> str:
    return ctx.st.vault.decrypt(m.content_enc, "content")


def _msg_out(ctx: Ctx, m: Message, models: dict, sources: dict | None = None) -> dict:
    mod = models.get(m.model_id) if m.model_id else None
    out = {"id": m.id, "parent_id": m.parent_id, "role": m.role, "content": _text(ctx, m), "status": m.status,
           "created_at": m.created_at, "model": mod.alias if mod else None,
           "tech": services.filter_tech(ctx.principal, m.stats or {}) if m.role == "assistant" else {}}
    if sources and m.id in sources:  # knowledge: passages that were given to the model for this reply
        out["sources"] = json.loads(ctx.st.vault.decrypt(sources[m.id], "sources"))
    return out


def _sources_for(ctx: Ctx, conv_id: str) -> dict:
    rows = ctx.s.execute(select(MessageSources.message_id, MessageSources.sources_enc)
                         .join(Message, Message.id == MessageSources.message_id).where(Message.conversation_id == conv_id)).all()
    return {r[0]: r[1] for r in rows}


def _own_conv(ctx: Ctx, cid: str) -> Conversation:
    c = ctx.s.get(Conversation, cid)
    # Same 404 for missing and for someone else's: existence is not disclosed.
    if c is None or c.user_id != ctx.user.id:
        raise HTTPException(404, {"code": "not_found", "message": "Conversation not found."})
    return c


@router.get("/models")
def list_models(ctx: Ctx = Depends(current), mode: str = "chat"):
    if mode not in services.MODES:
        raise HTTPException(422, {"code": "invalid", "message": "Unknown mode."})
    _need_mode(ctx, mode)
    out = []
    for m, b in services.accessible_models(ctx.s, ctx.principal, ctx.st, mode):
        tagged = "code" in [t.lower() for t in (m.tags or [])]
        named = any(w in m.alias.lower() for w in ("code", "coder"))
        out.append({"id": m.id, "alias": m.alias, "pinned": m.pinned, "tags": m.tags or [],
                    "code_hint": "tagged for code by your administrator" if tagged else ("the name suggests code" if named else None),
                    "capabilities": m.capabilities or [], "external": services.is_external(m, b),
                    "tech": services.filter_tech(ctx.principal, _model_tech(m, b))})
    return out


def _model_tech(m: Model, b: Backend) -> dict:
    meta = m.meta or {}
    return {"model_real_id": services.display_id(m.real_id), "family": meta.get("family"), "params": meta.get("params_label") or meta.get("params"),
            "quantization": meta.get("quantization"), "format": meta.get("format"), "context": meta.get("context"),
            "license": meta.get("license"), "runtime": b.adapter, "runtime_version": (b.options or {}).get("version"),
            "node": b.name}


class NewConv(BaseModel):
    model_id: str | None = None
    mode: str = Field(default="chat", pattern="^(chat|code)$")
    assistant_id: str | None = None
    collection_ids: list[str] = Field(default_factory=list, max_length=20)


@router.get("/conversations")
def list_conversations(ctx: Ctx = Depends(current), q: str | None = None, archived: bool = False, mode: str = "chat"):
    if mode not in ("chat", "code"):
        raise HTTPException(422, {"code": "invalid", "message": "Unknown workspace."})
    _need_mode(ctx, mode)
    rows = ctx.s.execute(select(Conversation).where(Conversation.user_id == ctx.user.id,
                                                    Conversation.archived == archived, Conversation.mode == mode)
                         .order_by(Conversation.updated_at.desc()).limit(500)).scalars().all()
    out = [{"id": c.id, "title": _title(ctx, c), "updated_at": c.updated_at, "model_id": c.model_id,
            "expires_at": c.expires_at} for c in rows]
    if q:
        ql = q.lower()
        # Titles and message text are encrypted at rest, so search decrypts in process (bounded to 500 titles).
        hits = []
        for c, row in zip(rows, out):
            if ql in row["title"].lower():
                hits.append(row)
                continue
            for m in ctx.s.execute(select(Message).where(Message.conversation_id == c.id).limit(200)).scalars():
                if ql in _text(ctx, m).lower():
                    hits.append(row)
                    break
        out = hits
    return out


@router.post("/conversations")
def new_conversation(body: NewConv, ctx: Ctx = Depends(current)):
    _need_mode(ctx, body.mode)
    days = ctx.st.settings.value_for(ctx.s, "privacy.chat_retention_days", ctx.principal)
    if days == 0:
        raise HTTPException(409, {"code": "no_history", "message": "Your organisation does not store chat history. Use a no-save conversation."})
    c = Conversation(user_id=ctx.user.id, title_enc=ctx.st.vault.encrypt("New conversation", "title"), model_id=body.model_id,
                     mode=body.mode, expires_at=time.time() + days * 86400 if days > 0 else None,
                     retention_note=f"Stored for {days} days" if days > 0 else "Stored until you delete it")
    ctx.s.add(c)
    ctx.s.flush()
    if body.assistant_id or body.collection_ids:
        _set_context(ctx, c, body.assistant_id, body.collection_ids)
    return {"id": c.id, "title": "New conversation", "expires_at": c.expires_at, "mode": c.mode}


def _set_context(ctx: Ctx, c: Conversation, assistant_id: str | None, collection_ids: list[str]):
    """Attach an assistant / knowledge collections to a saved conversation (validated against the caller's access)."""
    chat_context.resolve(ctx, workspace=c.mode, assistant_id=assistant_id, collection_ids=collection_ids)
    chat_context.check_collection_ids(ctx, collection_ids)
    row = ctx.s.get(ConversationContext, c.id)
    if row is None:
        row = ConversationContext(conversation_id=c.id)
        ctx.s.add(row)
    row.assistant_id, row.collection_ids = assistant_id, list(dict.fromkeys(collection_ids))


@router.get("/conversations/{cid}")
def get_conversation(cid: str, ctx: Ctx = Depends(current)):
    c = _own_conv(ctx, cid)
    _need_mode(ctx, c.mode)
    models = {m.id: m for m in ctx.s.execute(select(Model)).scalars()}
    msgs = ctx.s.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at, Message.id)).scalars().all()
    srcs = _sources_for(ctx, c.id)
    cc = ctx.s.get(ConversationContext, c.id)
    return {"id": c.id, "title": _title(ctx, c), "active_leaf": c.active_leaf, "model_id": c.model_id, "mode": c.mode,
            "expires_at": c.expires_at, "retention_note": c.retention_note,
            "context": {"assistant_id": cc.assistant_id if cc else None, "collection_ids": (cc.collection_ids if cc else [])},
            "messages": [_msg_out(ctx, m, models, srcs) for m in msgs]}


class Rename(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    archived: bool | None = None
    active_leaf: str | None = None
    model_id: str | None = None
    assistant_id: str | None = None  # send null with collection_ids/assistant_id in the body to clear
    collection_ids: list[str] | None = None


@router.patch("/conversations/{cid}")
def patch_conversation(cid: str, body: Rename, ctx: Ctx = Depends(current)):
    c = _own_conv(ctx, cid)
    _need_mode(ctx, c.mode)
    if "assistant_id" in body.model_fields_set or body.collection_ids is not None:
        cc = ctx.s.get(ConversationContext, c.id)
        _set_context(ctx, c, body.assistant_id if "assistant_id" in body.model_fields_set else (cc.assistant_id if cc else None),
                     body.collection_ids if body.collection_ids is not None else (list(cc.collection_ids) if cc else []))
    if body.title:
        c.title_enc = ctx.st.vault.encrypt(body.title.strip(), "title")
    if body.archived is not None:
        c.archived = body.archived
    if body.active_leaf:
        m = ctx.s.get(Message, body.active_leaf)
        if not m or m.conversation_id != c.id:
            raise HTTPException(404, {"code": "not_found", "message": "Message not found."})
        c.active_leaf = m.id
    if body.model_id:
        c.model_id = body.model_id
    return {"ok": True}


@router.delete("/conversations/{cid}")
def delete_conversation(cid: str, ctx: Ctx = Depends(current)):
    c = _own_conv(ctx, cid)
    ctx.s.delete(c)
    audit.record(ctx.s, ctx.user.id, "chat.delete", cid, {}, ctx.ip)
    return {"ok": True}


@router.get("/conversations/{cid}/export")
def export_conversation(cid: str, ctx: Ctx = Depends(current), format: str = "md"):
    if not ctx.st.settings.value_for(ctx.s, "chat.export", ctx.principal):
        raise HTTPException(403, {"code": "disabled", "message": "Export is turned off by your administrator."})
    ctx.need("chat.export")
    c = _own_conv(ctx, cid)
    models = {m.id: m for m in ctx.s.execute(select(Model)).scalars()}
    msgs = [_msg_out(ctx, m, models) for m in ctx.s.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at)).scalars()]
    if format == "json":
        return {"schema": "semechka.conversation/1", "title": _title(ctx, c), "messages": msgs}
    lines = [f"# {_title(ctx, c)}", ""]
    for m in msgs:
        lines += [f"**{'You' if m['role'] == 'user' else (m['model'] or 'Assistant')}**", "", m["content"], ""]
    return PlainTextResponse("\n".join(lines), media_type="text/markdown")


# ---- generation ---------------------------------------------------------------------------------------
def _queue_args(ctx: Ctx, b: Backend) -> dict:
    """Scheduling inputs for one request: the person's best role rank and the configured limits."""
    g = lambda k: ctx.st.settings.value_for(ctx.s, k, ctx.principal)  # noqa: E731
    ranks = ctx.s.execute(select(Role.rank).where(Role.id.in_(ctx.principal.role_ids or [""]))).scalars().all()
    return {"rank": min(ranks) if ranks else 100, "user_label": ctx.user.display_name,
            "backend_limit": int((b.options or {}).get("max_concurrency") or g("queue.backend_concurrency")),
            "user_limit": g("queue.user_slots_per_backend"), "max_wait": g("queue.wait_timeout_seconds"),
            "max_depth": g("queue.max_depth")}


_FILE_BLOCK = re.compile(r"<file name=.*?</file>", re.S)


def _prepare_extras(ctx: Ctx, workspace: str, assistant_id: str | None, collection_ids: list[str], last_user_text: str):
    """-> (resolved context, system parts, sources). Retrieval is read-only and never stored for no-save chats."""
    r = chat_context.resolve(ctx, workspace=workspace, assistant_id=assistant_id, collection_ids=collection_ids)
    query = _FILE_BLOCK.sub(" ", last_user_text)[:2000]
    sources = chat_context.retrieve(ctx, r, query)
    return r, list(r.system_parts), sources


def _apply_assistant_params(params: dict, requested: dict | None, r) -> dict:
    """Assistant defaults sit under what the person asked for and never exceed the administrator's token cap."""
    for k, v in (r.assistant_params or {}).items():
        if k in PARAM_KEYS and k not in (requested or {}):
            params[k] = min(v, params["max_tokens"]) if k == "max_tokens" else v
    return params


class GenIn(BaseModel):
    mode: str = Field(default="send", pattern="^(send|regenerate|edit)$")
    content: str | None = Field(default=None, max_length=1_000_000)
    parent_id: str | None = None  # send: parent message; edit: the user message being replaced
    model_id: str | None = None
    params: dict | None = None
    no_save_history: list[dict] | None = None  # ephemeral only: the client-held transcript


class EphemeralIn(BaseModel):
    messages: list[dict] = Field(max_length=200)
    model_id: str
    params: dict | None = None
    workspace: str = Field(default="chat", pattern="^(chat|code)$")
    assistant_id: str | None = None
    collection_ids: list[str] = Field(default_factory=list, max_length=20)


def _sse(ev: dict) -> str:
    return f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"


def _resolve_model(ctx: Ctx, model_id: str | None, fallback: str | None):
    mid = model_id or fallback
    if not mid:
        raise HTTPException(422, {"code": "no_model", "message": "Choose a model first."})
    m = ctx.s.get(Model, mid)
    # Not found and not permitted look identical, so model identifiers cannot be probed.
    if m is None:
        raise HTTPException(403, {"code": "model_forbidden", "message": "You do not have access to this model."})
    b = ctx.s.get(Backend, m.backend_id)
    ok, reason = services.model_access(ctx.s, ctx.principal, m, b, ctx.st, "chat")
    if not ok:
        raise HTTPException(403, {"code": "model_forbidden", "message": reason or "You do not have access to this model."})
    return m, b


def _effective_params(ctx: Ctx, m: Model, requested: dict | None) -> dict:
    requested = {k: v for k, v in (requested or {}).items() if k in PARAM_KEYS and v is not None}
    if requested and not ctx.principal.can("ops.gen.edit_params"):
        raise HTTPException(403, {"code": "forbidden", "permission": "ops.gen.edit_params",
                                  "message": "You are not allowed to change generation settings."})
    cap = ctx.st.settings.value_for(ctx.s, "quota.max_tokens", ctx.principal)
    cap = min(cap, (m.gen_limits or {}).get("max_tokens", cap))
    params = dict(requested)
    for k, lim in (m.gen_limits or {}).items():
        if k != "max_tokens" and k in PARAM_KEYS and k not in params:
            params[k] = lim
    params["max_tokens"] = min(int(params.get("max_tokens") or cap), cap)
    return params


def _admit(ctx: Ctx):
    """Quota and concurrency admission, server-side."""
    s, st, p = ctx.s, ctx.st, ctx.principal
    daily = st.settings.value_for(s, "quota.daily_messages", p)
    if daily and services.messages_today(s, ctx.user.id) >= daily:
        raise HTTPException(429, {"code": "quota_exceeded", "message": f"You have reached your limit of {daily} messages in 24 hours."})
    limit = st.settings.value_for(s, "quota.concurrent_requests", p)
    if st.active.get(ctx.user.id, 0) >= limit:
        raise HTTPException(429, {"code": "too_many_active", "message": "You already have the maximum number of replies generating."})


def _queue_stat(q: dict | None) -> dict:
    q = q or {}
    waited = q.get("waited_s", 0.0)
    state = q.get("state", "immediate")
    note = {"immediate": "A slot was free: the request started at once.",
            "waited": f"The request waited {waited:.1f} s in the queue (position {q.get('position_on_arrival')} on arrival)."}.get(
        state, "The request did not run: " + state.replace("_", " ") + ".")
    return {"state": state, "waited_s": waited, "position_on_arrival": q.get("position_on_arrival"), "note": note}


def _stats(m: Model, b: Backend, params: dict, t0: float, t_first: float | None, t_end: float, ev_done: dict | None,
           out_chars: int, queue_info: dict | None = None) -> dict:
    usage = (ev_done or {}).get("usage") or {}
    tim = (ev_done or {}).get("timings") or {}
    ct = usage.get("completion_tokens")
    elapsed = t_end - t0
    stats = {**_model_tech(m, b), "gen_params": params, "elapsed_s": round(elapsed, 3),
             "ttft_s": round(t_first - t0, 3) if t_first else None,
             "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": ct,
             "tokens_source": "backend" if ct is not None else None,
             "queue": _queue_stat(queue_info),
             "trace_id": uuid.uuid4().hex[:16], "finish_reason": (ev_done or {}).get("finish_reason")}
    if tim.get("predicted_per_second"):
        stats["tokens_per_s"], stats["tps_source"] = round(tim["predicted_per_second"], 2), "backend"
    elif ct and t_first and t_end > t_first:
        stats["tokens_per_s"], stats["tps_source"] = round(ct / (t_end - t_first), 2), "measured by Semechka"
    return {k: v for k, v in stats.items() if v is not None}


HEARTBEAT_SECONDS = 2.0


async def _with_heartbeat(agen):
    """Pass through an async generator, emitting an SSE comment while it is quiet.

    The server only learns that a client has gone when a send fails, so without periodic writes a closed tab would keep
    its place in the queue until its turn came. Comments are ignored by the client's SSE reader."""
    it = agen.__aiter__()
    pending = None
    try:
        while True:
            pending = asyncio.ensure_future(it.__anext__())
            while True:
                done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
                if done:
                    break
                yield ": waiting\n\n"
            try:
                item = pending.result()
            except StopAsyncIteration:
                return
            pending = None
            yield item
    finally:
        if pending is not None and not pending.done():
            pending.cancel()


QUEUE_MESSAGES = {
    "queue_timeout": "Your request waited too long in the queue and was cancelled. Try again in a moment.",
    "queue_cancelled": "Your request was cancelled while it was waiting in the queue.",
}


async def _stream(ctx_args, model_pk, messages, params, *, save, conv_id, user_msg_id, parent_for_asst, request):
    """Shared generation loop. Persists the assistant message (even partial on stop) when `save` is set.

    Every generation first takes a ticket from the scheduler (a per-backend queue), so concurrency limits, priority
    and fairness apply identically to every request. The wait is reported to the client as `queue` events."""
    st: AppState = ctx_args["st"]
    user_id, principal = ctx_args["user_id"], ctx_args["principal"]
    with st.db.session() as s:
        m = s.get(Model, model_pk)
        b = s.get(Backend, m.backend_id)
        adapter = adapter_for(b, st)
        sys_prompt = m.system_prompt
        m_copy, b_copy = m, b
    # Model instructions, workspace prompt, assistant prompt and retrieved passages travel as ONE system message
    # (several chat templates only accept a single leading system message).
    sys_parts = [x for x in [sys_prompt, *ctx_args.get("extra_system", [])] if x]
    if sys_parts:
        messages = [{"role": "system", "content": "\n\n".join(sys_parts)}] + messages
    sources = ctx_args.get("sources") or []
    asst_id = uid("g_")
    yield _sse({"type": "start", "assistant_id": asst_id, "user_message_id": user_msg_id,
                "model": m_copy.alias, "saved": save})
    if sources or ctx_args.get("skipped_collections"):
        yield _sse({"type": "sources", "sources": sources, "skipped": ctx_args.get("skipped_collections", 0)})
    st.active[user_id] = st.active.get(user_id, 0) + 1
    t0 = time.monotonic()
    t_first = None
    text, reasoning = [], []
    done_ev = None
    status = "complete"
    err = None
    ticket = None
    queue_info = {"state": "immediate", "waited_s": 0.0}
    admin_stop = False
    try:
        try:
            qa = ctx_args["queue"]
            ticket = st.scheduler.submit(backend_id=b_copy.id, user_id=user_id, rank=qa["rank"], kind="chat",
                                         label=m_copy.alias, user_label=qa["user_label"], backend_limit=qa["backend_limit"],
                                         user_limit=qa["user_limit"], max_wait=qa["max_wait"], max_depth=qa["max_depth"])
            queue_info["position_on_arrival"] = ticket.position_on_arrival
            if ticket.state == "waiting":
                queue_info["state"] = "waited"
                async for w in _with_heartbeat(ticket.updates()):
                    yield w if isinstance(w, str) else _sse({"type": "queue", **w})
                yield _sse({"type": "queue", "position": 0, "running": True})
            queue_info["waited_s"] = round(ticket.waited, 3)
        except QueueError as e:
            code = getattr(e, "code", "queue_error")
            if code == "queue_cancelled" and ticket is not None and ticket.ended_by == "admin":
                err = "An administrator cancelled your request while it was waiting."
            else:
                err = QUEUE_MESSAGES.get(code) or str(e) or "The queue could not take your request."
            status = "error"
            queue_info["state"] = code
        if status != "error":
            t0 = time.monotonic()  # timings measure generation, not time spent waiting for a slot
            try:
                async for ev in adapter.chat_stream(m_copy.real_id, messages, params):
                    if ev["type"] == "delta":
                        if t_first is None and (ev["content"] or ev["reasoning"]):
                            t_first = time.monotonic()
                        if ev["content"]:
                            text.append(ev["content"])
                        if ev["reasoning"]:
                            reasoning.append(ev["reasoning"])
                        yield _sse({"type": "delta", "content": ev["content"], "reasoning": ev["reasoning"]})
                    elif ev["type"] == "done":
                        done_ev = ev
                    if ticket is not None and ticket.abort.is_set():  # an operator asked this request to stop
                        status, admin_stop = "stopped", True
                        break
            except AdapterError as e:
                status, err = "error", str(e)
    except (asyncio.CancelledError, GeneratorExit):
        status = "stopped"
        _finish(st, ctx_args, m_copy, b_copy, params, messages, text, status, save, conv_id, asst_id, parent_for_asst,
                t0, t_first, done_ev, queue_info, sources)
        st.active[user_id] = max(0, st.active.get(user_id, 1) - 1)
        if ticket is not None:
            ticket.release() if ticket.state == "running" else ticket.cancel("client")
        raise
    st.active[user_id] = max(0, st.active.get(user_id, 1) - 1)
    if ticket is not None and ticket.state in ("running", "waiting"):
        ticket.release()
    if admin_stop:
        err = "An administrator stopped this request."
    stats = _finish(st, ctx_args, m_copy, b_copy, params, messages, text, status, save, conv_id, asst_id, parent_for_asst,
                    t0, t_first, done_ev, queue_info, sources)
    if err:
        yield _sse({"type": "error", "message": err or "The model backend failed.", "assistant_id": asst_id})
    yield _sse({"type": "done", "assistant_id": asst_id, "status": status, "content": "".join(text),
                "reasoning": "".join(reasoning), "tech": services.filter_tech(principal, stats), "sources": sources})


def _finish(st, ctx_args, m, b, params, messages, text, status, save, conv_id, asst_id, parent, t0, t_first, done_ev,
            queue_info=None, sources=None):
    stats = _stats(m, b, params, t0, t_first, time.monotonic(), done_ev, sum(len(t) for t in text), queue_info)
    with st.db.session() as s:
        if save:
            s.add(Message(id=asst_id, conversation_id=conv_id, parent_id=parent, role="assistant", model_id=m.id,
                          content_enc=st.vault.encrypt("".join(text), "content"), stats=stats, status=status))
            if sources:
                s.flush()
                s.add(MessageSources(message_id=asst_id, sources_enc=st.vault.encrypt(json.dumps(sources, ensure_ascii=False), "sources")))
            c = s.get(Conversation, conv_id)
            if c:
                c.active_leaf, c.updated_at = asst_id, time.time()
        ct = stats.get("completion_tokens")
        s.add(UsageEvent(user_id=ctx_args["user_id"], model_id=m.id, kind="chat", prompt_tokens=stats.get("prompt_tokens"),
                         completion_tokens=ct, tokens_source="backend" if ct is not None else "unavailable",
                         duration_s=stats.get("elapsed_s"), ephemeral=not save, status=status))
    return stats


def _thread(s, conv_id, leaf):
    """Root-to-leaf path of the message tree."""
    rows = {m.id: m for m in s.execute(select(Message).where(Message.conversation_id == conv_id)).scalars()}
    path = []
    cur = rows.get(leaf)
    while cur:
        path.append(cur)
        cur = rows.get(cur.parent_id) if cur.parent_id else None
    return list(reversed(path)), rows


@router.post("/conversations/{cid}/generate")
async def generate(cid: str, body: GenIn, request: Request, ctx: Ctx = Depends(current)):
    c = _own_conv(ctx, cid)
    _need_mode(ctx, c.mode)
    cc = ctx.s.get(ConversationContext, c.id)
    try:
        r0 = chat_context.resolve(ctx, workspace=c.mode, assistant_id=cc.assistant_id if cc else None,
                                  collection_ids=list(cc.collection_ids) if cc else [])
    except HTTPException as e:
        if e.status_code == 404:
            raise HTTPException(409, {"code": "assistant_gone", "message": "The assistant used by this conversation is no longer available to you. Remove it from the conversation to continue."}) from None
        raise
    if body.mode in ("regenerate", "edit") and not ctx.st.settings.value_for(ctx.s, "chat.branching", ctx.principal):
        raise HTTPException(403, {"code": "disabled", "message": "Editing and regenerating are turned off by your administrator."})
    m, b = _resolve_model(ctx, body.model_id, c.model_id or r0.assistant_model_id)
    params = _apply_assistant_params(_effective_params(ctx, m, body.params), body.params, r0)
    _admit(ctx)
    maxc = ctx.st.settings.value_for(ctx.s, "chat.max_input_chars", ctx.principal)
    user_msg_id = None
    if body.mode == "send":
        if not body.content or not body.content.strip():
            raise HTTPException(422, {"code": "invalid", "message": "Write a message first."})
        if len(body.content) > maxc:
            raise HTTPException(413, {"code": "too_long", "message": f"Messages are limited to {maxc} characters."})
        parent = body.parent_id or c.active_leaf
        if parent and ctx.s.get(Message, parent) is None:
            raise HTTPException(404, {"code": "not_found", "message": "Message not found."})
        um = Message(conversation_id=c.id, parent_id=parent, role="user", content_enc=ctx.st.vault.encrypt(body.content, "content"))
        ctx.s.add(um)
        ctx.s.flush()
        user_msg_id, leaf = um.id, um.id
        if parent is None:
            c.title_enc = ctx.st.vault.encrypt(" ".join(body.content.split()[:7])[:80] or "Conversation", "title")
    elif body.mode == "edit":
        if not body.parent_id or not body.content or not body.content.strip():
            raise HTTPException(422, {"code": "invalid", "message": "Provide the edited message and its id."})
        if len(body.content) > maxc:
            raise HTTPException(413, {"code": "too_long", "message": f"Messages are limited to {maxc} characters."})
        orig = ctx.s.get(Message, body.parent_id)
        if orig is None or orig.conversation_id != c.id or orig.role != "user":
            raise HTTPException(404, {"code": "not_found", "message": "Message not found."})
        um = Message(conversation_id=c.id, parent_id=orig.parent_id, role="user", content_enc=ctx.st.vault.encrypt(body.content, "content"))
        ctx.s.add(um)
        ctx.s.flush()
        user_msg_id, leaf = um.id, um.id
    else:  # regenerate: new sibling of the assistant message under the same user message
        target = ctx.s.get(Message, body.parent_id or "")
        if target is None or target.conversation_id != c.id or target.role != "assistant":
            raise HTTPException(404, {"code": "not_found", "message": "Message not found."})
        leaf = target.parent_id
        user_msg_id = leaf
    path, _ = _thread(ctx.s, c.id, leaf)
    messages = [{"role": x.role, "content": ctx.st.vault.decrypt(x.content_enc, "content")} for x in path]
    c.model_id = m.id
    last_user = next((x["content"] for x in reversed(messages) if x["role"] == "user"), "")
    r, extra, sources = _prepare_extras(ctx, c.mode, cc.assistant_id if cc else None,
                                        list(cc.collection_ids) if cc else [], last_user)
    qargs = _queue_args(ctx, b)
    ctx.s.commit()
    args = {"st": ctx.st, "user_id": ctx.user.id, "principal": ctx.principal, "extra_system": extra, "sources": sources,
            "skipped_collections": r.skipped_collections, "queue": qargs}
    return StreamingResponse(
        _stream(args, m.id, messages, params, save=True, conv_id=c.id, user_msg_id=user_msg_id, parent_for_asst=leaf, request=request),
        media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/ephemeral/generate")
async def ephemeral_generate(body: EphemeralIn, request: Request, ctx: Ctx = Depends(current)):
    """No-save conversation: the transcript is held by the client; nothing is written to the chat tables."""
    _need_mode(ctx, body.workspace)
    ctx.need("chat.ephemeral")
    if not ctx.st.settings.value_for(ctx.s, "privacy.ephemeral_allowed", ctx.principal):
        raise HTTPException(403, {"code": "ephemeral_disabled", "message": "No-save conversations are turned off for you by your organisation's policy."})
    chat_context.check_collection_ids(ctx, body.collection_ids)
    r0 = chat_context.resolve(ctx, workspace=body.workspace, assistant_id=body.assistant_id, collection_ids=body.collection_ids)
    m, b = _resolve_model(ctx, body.model_id, r0.assistant_model_id)
    params = _apply_assistant_params(_effective_params(ctx, m, body.params), body.params, r0)
    _admit(ctx)
    maxc = ctx.st.settings.value_for(ctx.s, "chat.max_input_chars", ctx.principal)
    msgs = []
    for x in body.messages:
        if x.get("role") not in ("user", "assistant") or not isinstance(x.get("content"), str):
            raise HTTPException(422, {"code": "invalid", "message": "Each message needs a role and text."})
        if len(x["content"]) > maxc:
            raise HTTPException(413, {"code": "too_long", "message": f"Messages are limited to {maxc} characters."})
        msgs.append({"role": x["role"], "content": x["content"]})
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(422, {"code": "invalid", "message": "Send a message first."})
    r, extra, sources = _prepare_extras(ctx, body.workspace, body.assistant_id, body.collection_ids, msgs[-1]["content"])
    qargs = _queue_args(ctx, b)
    ctx.s.commit()
    args = {"st": ctx.st, "user_id": ctx.user.id, "principal": ctx.principal, "extra_system": extra, "sources": sources,
            "skipped_collections": r.skipped_collections, "queue": qargs}
    return StreamingResponse(
        _stream(args, m.id, msgs, params, save=False, conv_id=None, user_msg_id=None, parent_for_asst=None, request=request),
        media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.get("/protection")
def protection(request: Request, ctx: Ctx = Depends(current), model_id: str | None = None, ephemeral: bool = False):
    """'How is this chat protected?' — statements derived from the live configuration, not marketing."""
    s, st, p = ctx.s, ctx.st, ctx.principal
    g = lambda k: st.settings.value_for(s, k, p)  # noqa: E731
    from ..deps import is_https
    m = b = None
    if model_id:
        m, b = _resolve_model(ctx, model_id, None)
    external = bool(m and services.is_external(m, b))
    days = g("privacy.chat_retention_days")
    lines = []
    https = is_https(request)
    lines.append({"topic": "In transit", "status": "ok" if https else "warn",
                  "text": "This connection to Semechka is encrypted (HTTPS)." if https else
                          "This connection to Semechka is not encrypted (plain HTTP). Use it only on a network you trust, or ask your administrator to enable HTTPS."})
    if m:
        local_text = ("runs on an external service outside this organisation's network. Your messages are sent there in readable form."
                      if external else
                      f"is processed by the '{b.name}' backend ({b.adapter}) configured by your administrator.")
        lines.append({"topic": "Where it is processed", "status": "warn" if external else "info",
                      "text": f"The model '{m.alias}' {local_text} While it answers, that backend must read your messages in plain text; this is not end-to-end encryption."})
    else:
        lines.append({"topic": "Where it is processed", "status": "info",
                      "text": "The model backend must read your messages in plain text in order to answer. This is not end-to-end encryption."})
    if ephemeral:
        lines.append({"topic": "Stored history", "status": "ok",
                      "text": "Not saved. This conversation is not written to the chat database; it disappears when you close or reload the page. Usage counts (no text) are still recorded for quotas."})
    elif days == 0:
        lines.append({"topic": "Stored history", "status": "info", "text": "Chat history is not stored in this deployment."})
    else:
        lines.append({"topic": "Stored history", "status": "info",
                      "text": (f"Saved for {days} days, then deleted automatically." if days > 0 else "Saved until you delete it.")
                              + " Stored text is encrypted with a key held on the server; backups carry the same encryption and expire on their own schedule."})
    lines.append({"topic": "Who can read it", "status": "info",
                  "text": "No administrator screen in Semechka shows other people's conversations, and technical or admin permissions never "
                          "include reading them. This release has no content-review feature. A person with direct access to the "
                          "server's files and its encryption key could still read stored text, so host access should be limited."})
    if p.can("mode.knowledge") and g("modes.knowledge.enabled"):
        lines.append({"topic": "Knowledge", "status": "info",
                      "text": "Documents in your knowledge collections are stored encrypted. To search one, its passages are decrypted into the "
                              "server's memory only (never written to disk readable) and kept there briefly. The best-matching passages are then sent to "
                              "the model backend together with your question, in readable form, like any other part of your message. Search is by keyword, "
                              "not by meaning."})
    lines.append({"topic": "Logs", "status": "ok", "text": "Message text is never written to the audit log or application logs."})
    mode = g("privacy.mode")
    mode_text = {"standard": "Standard private deployment.", "user_controlled": "You control your own history within the limits above.",
                 "enterprise": "This is an organisation-managed deployment. Your organisation may set retention and review rules that apply to you."}[mode]
    notice = st.settings.deployment(s, "privacy.notice")
    return {"mode": mode, "summary": mode_text, "lines": lines, "organisation_notice": notice,
            "external": external, "ephemeral": ephemeral}


@router.get("/stats-fields")
def stats_fields(ctx: Ctx = Depends(current)):
    return {"fields": services.tech_fields_allowed(ctx.principal)}
