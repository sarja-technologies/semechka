from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import perms, services
from ..deps import Ctx, current, mfa_required_for
from ..models import Role, Team

router = APIRouter(prefix="/api")

THEME_CHOICES = ["paper", "carbon", "graphite", "system"]


def ui_config(ctx_s, st, p, *, preview=False) -> dict:
    """What this principal's interface looks like. Computed from the same rules the API enforces."""
    g = lambda k: st.settings.value_for(ctx_s, k, p)  # noqa: E731
    pub = st.settings.public_values(ctx_s)
    modes = {m: services.mode_state(ctx_s, p, st, m) for m in services.MODES}
    nav = []
    for it in g("nav.items"):
        iid = it["id"]
        if iid in modes:
            state = modes[iid]
            if state.get("code") in ("disabled", "forbidden"):
                continue  # hidden entirely; the API rejects it too
            nav.append({"id": iid, "label": it.get("label") or iid.title(), "enabled": it.get("enabled", True),
                        "available": state["enabled"], "reason": state["reason"]})
        elif it.get("enabled", True):
            if iid == "usage" or iid == "help":
                nav.append({"id": iid, "label": it.get("label") or iid.title(), "enabled": True, "available": True, "reason": ""})
    nav = [n for n in nav if n["enabled"]]
    home = g("nav.home")
    if not any(n["id"] == home and n["available"] for n in nav):
        home = next((n["id"] for n in nav if n["available"]), "help")
    return {
        "brand": {k.split(".", 1)[1]: v for k, v in pub.items() if k.startswith("brand.")},
        "nav": nav, "home": home, "modes": modes,
        "sidebar_collapsed": g("nav.sidebar_collapsed"), "suggested_prompts": g("nav.suggested_prompts"),
        "theme": g("brand.theme_default"), "density": g("brand.density"),
        "composer": {"attachments": g("chat.attachments") and p.can("chat.attach"),
                     "branching": g("chat.branching"), "export": g("chat.export") and p.can("chat.export"),
                     "max_chars": g("chat.max_input_chars"),
                     "ephemeral": bool(g("privacy.ephemeral_allowed") and p.can("chat.ephemeral")),
                     "edit_params": p.can("ops.gen.edit_params")},
        "tech": {"drawer": bool(g("tech.drawer_enabled") and services.has_any_tech(p)),
                 "fields": services.tech_fields_allowed(p) if g("tech.drawer_enabled") else []},
        "retention_days": g("privacy.chat_retention_days"),
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
        "csrf": ctx.session.csrf,
        "ui": ui_config(s, st, p),
    }


class PrefsIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    theme: str | None = None
    show_tech: bool | None = None  # only effective when the admin granted tech fields
    density: str | None = None


@router.patch("/me")
def update_me(body: PrefsIn, ctx: Ctx = Depends(current)):
    if body.display_name:
        ctx.user.display_name = body.display_name.strip()
    prefs = dict(ctx.user.prefs or {})
    if body.theme is not None:
        if body.theme not in THEME_CHOICES:
            raise HTTPException(422, {"code": "invalid", "message": "Unknown theme."})
        prefs["theme"] = body.theme
    if body.density is not None:
        if body.density not in ("comfortable", "compact"):
            raise HTTPException(422, {"code": "invalid", "message": "Unknown density."})
        prefs["density"] = body.density
    if body.show_tech is not None:
        prefs["show_tech"] = bool(body.show_tech)
    ctx.user.prefs = prefs
    return {"ok": True}


@router.get("/usage")
def usage(ctx: Ctx = Depends(current)):
    from sqlalchemy import func
    from ..models import UsageEvent
    import time
    s = ctx.s
    since = time.time() - 30 * 86400
    rows = s.execute(select(UsageEvent).where(UsageEvent.user_id == ctx.user.id, UsageEvent.ts >= since)).scalars().all()
    quota = ctx.st.settings.value_for(s, "quota.daily_messages", ctx.principal)
    return {"messages_30d": len(rows), "messages_24h": services.messages_today(s, ctx.user.id),
            "daily_limit": quota or None,
            "tokens_30d": {"prompt": sum(r.prompt_tokens or 0 for r in rows), "completion": sum(r.completion_tokens or 0 for r in rows),
                           "note": "Counts only requests where the backend reported token usage."} if ctx.principal.can("tech.usage.tokens") else None}
