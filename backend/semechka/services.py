"""Shared server-side logic used identically by HTTP routes and background work."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select

from . import perms
from .models import (Backend, Meta, Model, ModelGrant, Role, Team, UsageEvent, User, UserOverride)
from .settings_reg import BY_ID as SETTING_DEFS

MODES = ["chat", "code", "image", "video", "audio", "knowledge", "agents"]
# Modes whose backends are implemented in this release. Others report a truthful unsupported state.
# code / knowledge / agents(assistants) run on the chat engine; image, video and audio need their own backends.
IMPLEMENTED_MODES = {"chat", "code", "knowledge", "agents"}
MODE_CAPABILITY = {"chat": "chat", "code": "chat", "image": "image", "video": "video", "audio": "audio",
                   "knowledge": "chat", "agents": "chat"}


def seed(s) -> None:
    """Idempotent: create role templates on first run. Never touches existing (possibly renamed) roles."""
    if s.get(Meta, "seeded_roles"):
        return
    for t in perms.ROLE_TEMPLATES:
        s.add(Role(id=t["id"], label=t["label"], description=t["description"], rank=t["rank"],
                   grants=t["grants"], denies=t["denies"], system=t.get("system", False)))
    s.add(Meta(key="seeded_roles", value="1"))


def load_principal(s, user: User) -> perms.Principal:
    roles = {r.id: r for r in s.execute(select(Role)).scalars()}
    teams = {t.id: t for t in s.execute(select(Team)).scalars()}
    ov = s.get(UserOverride, user.id)
    return perms.build_principal(user, roles, teams, ov)


def principal_for_role(s, role_id: str) -> perms.Principal:
    """A synthetic principal for 'View as role' previews. Carries no user identity or content."""
    r = s.get(Role, role_id)
    if r is None:
        raise KeyError(role_id)
    src = perms.Source("role", r.id, r.label, list(r.grants or []), list(r.denies or []))
    return perms.Principal("preview:" + role_id, "active", [src], [role_id], [], "", f"Preview: {r.label}")


# ---- models ---------------------------------------------------------------------------------------
def subject_keys(p: perms.Principal) -> set[tuple[str, str]]:
    return {("user", p.user_id)} | {("role", r) for r in p.role_ids} | {("team", t) for t in p.team_ids}


def model_access(s, p: perms.Principal, m: Model, backend: Backend, st, mode: str | None = None) -> tuple[bool, str]:
    """(allowed, reason). Deny always wins over allow; no grant means no access."""
    if not m.enabled or not m.available or not backend.enabled:
        return False, "This model is not available."
    mode = mode or "chat"
    cap = MODE_CAPABILITY[mode]
    if cap not in (m.capabilities or []):
        return False, f"This model does not support {mode}."
    grants = s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id)).scalars().all()
    keys = subject_keys(p)
    mine = [g for g in grants if (g.subject_type, g.subject_id) in keys]
    if any(g.effect == "deny" for g in mine):
        return False, "Your group does not have access to this model."
    if not any(g.effect == "allow" for g in mine):
        return False, "Your group does not have access to this model."
    if is_external(m, backend) and not st.settings.deployment(s, "privacy.external_egress"):
        return False, "This model runs on an external service, which your organisation has turned off."
    return True, ""


def is_external(m: Model, backend: Backend) -> bool:
    return bool(backend.external or (m.meta or {}).get("external"))


def accessible_models(s, p: perms.Principal, st, mode: str = "chat") -> list[tuple[Model, Backend]]:
    out = []
    backends = {b.id: b for b in s.execute(select(Backend)).scalars()}
    for m in s.execute(select(Model).order_by(Model.pinned.desc(), Model.alias)).scalars():
        b = backends.get(m.backend_id)
        if b and model_access(s, p, m, b, st, mode)[0]:
            out.append((m, b))
    return out


# ---- modes ----------------------------------------------------------------------------------------
def mode_state(s, p: perms.Principal, st, mode: str) -> dict:
    """The single source of truth for 'can this person use this mode right now', with a truthful reason."""
    if mode not in MODES:
        return {"enabled": False, "reason": "Unknown mode."}
    if not st.settings.value_for(s, f"modes.{mode}.enabled", p):
        return {"enabled": False, "reason": "Turned off by your administrator.", "code": "disabled"}
    if not p.can(f"mode.{mode}"):
        return {"enabled": False, "reason": "Your group has no access.", "code": "forbidden"}
    if mode not in IMPLEMENTED_MODES:
        return {"enabled": False, "reason": "Not available in this release.", "code": "unsupported"}
    if not accessible_models(s, p, st, mode):
        any_installed = s.execute(select(func.count()).select_from(Model).where(Model.enabled == True)).scalar()  # noqa: E712
        why = ("No compatible model installed." if not any_installed
               else "No compatible model is available to you.")
        return {"enabled": False, "reason": why, "code": "no_model"}
    return {"enabled": True, "reason": ""}


# ---- technical visibility --------------------------------------------------------------------------
def filter_tech(p: perms.Principal, full: dict) -> dict:
    """Reduce a full stats record to the fields this principal may read. Server-side; the UI never decides."""
    f = full or {}
    out: dict[str, Any] = {}
    unavailable: list[str] = []

    def put(perm, mapping):
        if not p.can(perm):
            return
        for key in mapping:
            if f.get(key) is not None:
                out[key] = f[key]
            else:
                unavailable.append(key)

    put("tech.model.id", ["model_real_id"])
    put("tech.model.family", ["family"])
    put("tech.model.params", ["params"])
    put("tech.model.quant", ["quantization", "format"])
    put("tech.model.context", ["context"])
    put("tech.model.license", ["license"])
    put("tech.runtime.name", ["runtime"])
    put("tech.runtime.version", ["runtime_version"])
    put("tech.gen.params", ["gen_params"])
    put("tech.usage.tokens", ["prompt_tokens", "completion_tokens", "tokens_source"])
    put("tech.perf.ttft", ["ttft_s"])
    put("tech.perf.tps", ["tokens_per_s", "tps_source"])
    put("tech.perf.duration", ["elapsed_s"])
    put("tech.queue.position", ["queue"])
    put("tech.node.name", ["node"])
    put("tech.job.trace", ["trace_id"])
    if out or unavailable:
        out["_unavailable"] = sorted(set(unavailable))
    return out


def has_any_tech(p: perms.Principal) -> bool:
    return any(p.can(t) for t in perms.TECH_PERMS)


def tech_fields_allowed(p: perms.Principal) -> list[str]:
    return [t for t in perms.TECH_PERMS if p.can(t)]


# ---- usage / quotas ---------------------------------------------------------------------------------
def messages_today(s, user_id: str) -> int:
    since = time.time() - 86400
    return s.execute(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.user_id == user_id, UsageEvent.ts >= since, UsageEvent.kind == "chat")).scalar() or 0


def is_admin_like(p: perms.Principal) -> bool:
    return any(x.id.startswith("admin.") and p.can(x.id) for x in perms.REGISTRY)


def setting_default(key: str):
    return SETTING_DEFS[key].default


def display_id(real_id: str) -> str:
    """Technical-visibility form of a model id: never a server file path."""
    if "/" in real_id and (real_id.startswith(("/", "~")) or "\\" in real_id or real_id.count("/") > 2):
        return real_id.replace("\\", "/").rsplit("/", 1)[-1]
    return real_id
