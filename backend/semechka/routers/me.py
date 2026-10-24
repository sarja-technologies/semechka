from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import services
from ..deps import Ctx, current, mfa_required_for
from ..models import Role, Team

LABELS = {"agents": "Assistants", "knowledge": "Knowledge", "code": "Code"}  # 'agents' is the stored id; it has no tools

router = APIRouter(prefix="/api")

THEME_CHOICES = ["light", "dark", "graphite", "custom", "system"]  # system: Light or Dark following the device
UI_FLAGS = ["sidebar", "search", "folders", "pinning", "history_grouping", "model_chooser", "model_names", "model_meta",
            "quantization", "context_size", "gen_params", "tool_indicators", "image_gen", "video_gen", "file_upload",
            "retention_notice", "help_links", "org_branding", "mode_selector", "technical_drawer"]
PAGES = ("knowledge", "agents", "code", "usage", "help")  # secondary pages; chat, image and video share one thread


def retention_text(days: int) -> str:
    """The notice says exactly what the retention policy does (the sweep deletes at this age)."""
    if days == 0:
        return "Conversations are not saved."
    if days < 0:
        return "Conversations are kept until you delete them."
    return f"Conversations are deleted automatically {days} day{'s' if days != 1 else ''} after they start."


def operations(ctx_s, st, p, g, modes: dict) -> dict:
    """Which kinds of turn the composer may offer, enforced again by the API on every request."""
    chat = modes["chat"]
    vision_ok = chat["enabled"] and bool(services.accessible_models(ctx_s, p, st, "vision"))
    out = {"text": {"enabled": chat["enabled"], "reason": chat["reason"]},
           "vision": {"enabled": vision_ok and bool(g("chat.attachments") and p.can("chat.attach") and g("ui.file_upload")),
                      "reason": "" if vision_ok else "No model available to you can read images."}}
    for k in ("image", "video"):
        st_k = modes[k]
        shown = g(f"ui.{k}_gen")
        out[k] = {"enabled": bool(st_k["enabled"] and shown),
                  "reason": st_k["reason"] if not st_k["enabled"] else ("" if shown else "Hidden by your administrator.")}
    out["code"] = {"enabled": modes["code"]["enabled"], "reason": modes["code"]["reason"]}
    return out


def theme_config(ctx_s, st, g) -> dict:
    from .. import theme_svc
    t = theme_svc.bundle_from_settings(g)
    choices = ["light", "dark", "graphite"] + (["custom"] if t.get("custom") else [])
    t["choices"] = choices
    t["tokens"] = theme_svc.token_ids()
    if t["default"] == "custom" and not t.get("custom"):
        t["default"] = "light"
    return t


def ui_config(ctx_s, st, p, *, preview=False, prefs: dict | None = None) -> dict:
    """What this principal's interface looks like. Computed from the same rules the API enforces."""
    g = lambda k: st.settings.value_for(ctx_s, k, p)  # noqa: E731
    pub = st.settings.public_values(ctx_s)
    modes = {m: services.mode_state(ctx_s, p, st, m) for m in services.MODES}
    flags = {k: bool(g(f"ui.{k}")) for k in UI_FLAGS}
    pages = []
    for it in g("nav.items"):
        iid = it["id"]
        if iid not in PAGES or not it.get("enabled", True):
            continue
        if iid in modes:
            state = modes[iid]
            if state.get("code") in ("disabled", "forbidden"):
                continue  # hidden entirely; the API rejects it too
            pages.append({"id": iid, "label": it.get("label") or LABELS.get(iid, iid.title()),
                          "available": state["enabled"], "reason": state["reason"]})
        elif iid == "usage" or (iid == "help" and flags["help_links"]):
            pages.append({"id": iid, "label": it.get("label") or LABELS.get(iid, iid.title()), "available": True, "reason": ""})
    if modes["image"]["enabled"] or modes["video"]["enabled"]:
        pages.insert(0, {"id": "library", "label": "Library", "available": True, "reason": ""})
    ops = operations(ctx_s, st, p, g, modes)
    days = g("privacy.chat_retention_days")
    theme = theme_config(ctx_s, st, g)
    prefs = prefs or {}
    defaults = {}
    for op, key in (("text", "chat"), ("vision", "vision"), ("code", "code"), ("image", "image"), ("video", "video")):
        mid = g(f"models.default.{key}")
        if mid:
            defaults[op] = mid
    brand = {k.split(".", 1)[1]: v for k, v in pub.items() if k.startswith("brand.")}
    if not flags["org_branding"]:
        brand = {**brand, "header_content": "none", "sidebar_brand": False}
    return {
        "brand": brand,
        "theme": {**theme, "active": (prefs.get("theme") if theme["user_choice"] and prefs.get("theme") in
                                      (theme["choices"] + ["system"]) else None) or theme["default"]},
        "ui": flags,
        "pages": pages,
        "nav": pages,  # 0.2.x name, kept for custom interfaces
        "home": "chat",
        "modes": modes,
        "operations": ops,
        "default_models": defaults,
        "sidebar_collapsed": g("nav.sidebar_collapsed"), "suggested_prompts": g("nav.suggested_prompts"),
        "density": g("brand.density"),
        "composer": {"attachments": bool(g("chat.attachments") and p.can("chat.attach") and flags["file_upload"]),
                     "attachment_max_mb": g("privacy.attachment_max_mb"),
                     "branching": g("chat.branching"), "export": g("chat.export") and p.can("chat.export"),
                     "max_chars": g("chat.max_input_chars"),
                     "ephemeral": bool(g("privacy.ephemeral_allowed") and p.can("chat.ephemeral")),
                     "edit_params": p.can("ops.gen.edit_params") and flags["gen_params"],
                     "delete": bool(g("privacy.user_delete_conversations")),
                     # personal choice first, then the organisation's default
                     "enter_sends": prefs["enter_sends"] if isinstance(prefs.get("enter_sends"), bool) else bool(g("chat.enter_sends")),
                     "prompt_history": prefs["prompt_history"] if isinstance(prefs.get("prompt_history"), bool)
                     else bool(g("chat.prompt_history"))},
        "tech": {"drawer": bool(g("tech.drawer_enabled") and flags["technical_drawer"] and services.has_any_tech(p)),
                 "fields": services.tech_fields_allowed(p) if g("tech.drawer_enabled") else []},
        "retention_days": days,
        "retention": {"days": days, "text": retention_text(days), "show": flags["retention_notice"],
                      "place": g("ui.retention_notice_place")},
        "privacy_notice": pub.get("privacy.notice") or "",
    }


@router.get("/me")
def me(ctx: Ctx = Depends(current)):
    s, st, p = ctx.s, ctx.st, ctx.principal
    roles = {r.id: r for r in s.execute(select(Role)).scalars()}
    teams = {t.id: t for t in s.execute(select(Team)).scalars()}
    return {
        "user": {"id": ctx.user.id, "email": ctx.user.email, "display_name": ctx.user.display_name,
                 "mfa": ctx.user.totp_enabled, "prefs": ctx.user.prefs or {}},
        "roles": [{"id": r, "label": roles[r].label} for r in p.role_ids if r in roles],
        "teams": [{"id": t, "name": teams[t].name} for t in p.team_ids if t in teams],
        "permissions": p.all_allowed(),
        "admin": services.is_admin_like(p),
        "mfa_enrolment_required": bool(mfa_required_for(ctx) and not ctx.user.totp_enabled),
        "can_delete_account": bool(st.settings.deployment(s, "auth.allow_user_delete")),
        "csrf": ctx.session.csrf,
        "ui": ui_config(s, st, p, prefs=ctx.user.prefs or {}),
    }


class PrefsIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    theme: str | None = None
    show_tech: bool | None = None  # only effective when the admin granted tech fields
    density: str | None = None
    enter_sends: bool | None = None
    prompt_history: bool | None = None
    picker: dict | None = None  # {"view": compact|detailed, "group_by": ..., "sort": ...}; a null value clears one
    favourite_models: list[str] | None = Field(default=None, max_length=200)


PICKER_CHOICES = {"view": ("compact", "detailed"), "group_by": ("groups", "family", "capability", "engine", "size", "none"),
                  "sort": ("custom", "name", "size_desc", "size_asc", "context")}


@router.patch("/me")
def update_me(body: PrefsIn, ctx: Ctx = Depends(current)):
    if body.display_name:
        ctx.user.display_name = body.display_name.strip()
    prefs = dict(ctx.user.prefs or {})
    if body.theme is not None:
        if body.theme not in THEME_CHOICES:
            raise HTTPException(422, {"code": "invalid", "message": "Unknown theme."})
        if not ctx.st.settings.value_for(ctx.s, "theme.user_choice", ctx.principal):
            raise HTTPException(403, {"code": "disabled", "message": "Your organisation sets one theme for everyone."})
        prefs["theme"] = body.theme
    if body.density is not None:
        if body.density not in ("comfortable", "compact"):
            raise HTTPException(422, {"code": "invalid", "message": "Unknown density."})
        prefs["density"] = body.density
    if body.show_tech is not None:
        prefs["show_tech"] = bool(body.show_tech)
    for k in ("enter_sends", "prompt_history"):
        if getattr(body, k) is not None:
            prefs[k] = getattr(body, k)
    if body.picker is not None:
        pk = dict(prefs.get("picker") or {})
        for k, v in body.picker.items():
            if k not in PICKER_CHOICES or (v is not None and v not in PICKER_CHOICES[k]):
                raise HTTPException(422, {"code": "invalid", "message": f"Unknown model picker option: {k}."})
            if v is None:
                pk.pop(k, None)
            else:
                pk[k] = v
        prefs["picker"] = pk
    if body.favourite_models is not None:  # model ids only; whether one may be used is decided at use, as always
        if any(not isinstance(x, str) or len(x) > 32 for x in body.favourite_models):
            raise HTTPException(422, {"code": "invalid", "message": "Favourites are model ids."})
        prefs["favourite_models"] = list(dict.fromkeys(body.favourite_models))
    ctx.user.prefs = prefs
    return {"ok": True}


@router.get("/usage")
def usage(ctx: Ctx = Depends(current)):
    import time

    from ..models import UsageEvent
    s = ctx.s
    since = time.time() - 30 * 86400
    rows = s.execute(select(UsageEvent).where(UsageEvent.user_id == ctx.user.id, UsageEvent.ts >= since)).scalars().all()
    quota = ctx.st.settings.value_for(s, "quota.daily_messages", ctx.principal)
    return {"messages_30d": len(rows), "messages_24h": services.messages_today(s, ctx.user.id),
            "daily_limit": quota or None,
            "tokens_30d": {"prompt": sum(r.prompt_tokens or 0 for r in rows), "completion": sum(r.completion_tokens or 0 for r in rows),
                           "note": "Counts only requests where the backend reported token usage."} if ctx.principal.can("tech.usage.tokens") else None}


@router.get("/me/content-access")
def content_access(ctx: Ctx = Depends(current)):
    """Every time an administrator listed or opened this person's conversations, with who and why."""
    from ..models import AuditEvent, User
    rows = ctx.s.execute(select(AuditEvent).where(AuditEvent.action.in_(("content.list", "content.read")))
                         .order_by(AuditEvent.id.desc()).limit(5000)).scalars().all()
    mine = [r for r in rows if (r.detail or {}).get("owner") == ctx.user.id]
    names = {u.id: u.display_name for u in ctx.s.execute(select(User).where(User.id.in_({r.actor for r in mine}))).scalars()}
    return {"policy": ctx.st.settings.deployment(ctx.s, "privacy.admin_content_access"),
            "events": [{"ts": r.ts, "action": "listed your conversations" if r.action == "content.list" else "opened a conversation",
                        "conversation_id": r.target if r.action == "content.read" else None,
                        "by": names.get(r.actor, "a deleted administrator"), "reason": (r.detail or {}).get("reason")}
                       for r in mine[:200]]}


@router.get("/me/export")
def export_my_data(ctx: Ctx = Depends(current)):
    """Personal data export: account, preferences, every conversation (all branches), folders, knowledge collections
    owned, generated-media records and usage. File contents of attachments and media are listed, not embedded;
    each can be downloaded from its own link."""
    import time

    from .. import audit, media_svc
    from ..media_models import MediaJob
    from ..models import (
        ChatAttachment,
        Conversation,
        ConversationFolder,
        Message,
        Model,
        UsageEvent,
    )
    s, st, u = ctx.s, ctx.st, ctx.user
    dec = st.vault.decrypt
    models = {m.id: m.alias for m in s.execute(select(Model)).scalars()}
    convs = []
    for c in s.execute(select(Conversation).where(Conversation.user_id == u.id).order_by(Conversation.created_at)).scalars():
        msgs = s.execute(select(Message).where(Message.conversation_id == c.id).order_by(Message.created_at, Message.id)).scalars()
        convs.append({"id": c.id, "title": dec(c.title_enc, "title"), "mode": c.mode, "created_at": c.created_at,
                      "updated_at": c.updated_at, "expires_at": c.expires_at, "pinned": c.pinned, "folder_id": c.folder_id,
                      "messages": [{"id": m.id, "parent_id": m.parent_id, "role": m.role, "kind": m.kind,
                                    "content": dec(m.content_enc, "content"), "model": models.get(m.model_id),
                                    "created_at": m.created_at, "status": m.status,
                                    "attachments": (m.meta or {}).get("attachments") or [],
                                    "media_job": (m.meta or {}).get("job_id")} for m in msgs]})
    atts = [{"id": a.id, "name": dec(a.name_enc, "attachment-name"), "kind": a.kind, "mime": a.mime, "size": a.size,
             "conversation_id": a.conversation_id, "download": f"/api/chat/attachments/{a.id}/content"}
            for a in s.execute(select(ChatAttachment).where(ChatAttachment.user_id == u.id)).scalars()]
    jobs = [media_svc.job_out(st, s, ctx.principal, j) for j in
            s.execute(select(MediaJob).where(MediaJob.user_id == u.id, MediaJob.save == True)).scalars()]  # noqa: E712
    usage = s.execute(select(UsageEvent).where(UsageEvent.user_id == u.id)).scalars().all()
    audit.record(s, u.id, "account.export", u.id, {"conversations": len(convs)}, ctx.ip)
    return {"schema": "semechka.personal-export/1", "exported_at": time.time(),
            "account": {"id": u.id, "email": u.email, "display_name": u.display_name, "created_at": u.created_at,
                        "last_login_at": u.last_login_at, "mfa": u.totp_enabled, "prefs": u.prefs or {}},
            "folders": [{"id": f.id, "name": dec(f.name_enc, "folder")} for f in
                        s.execute(select(ConversationFolder).where(ConversationFolder.user_id == u.id)).scalars()],
            "conversations": convs, "attachments": atts, "generated_media": jobs,
            "usage": {"events": len(usage), "note": "Usage rows hold counts and timings only, never text."}}
