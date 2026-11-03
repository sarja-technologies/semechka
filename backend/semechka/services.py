"""Shared server-side logic used identically by HTTP routes and background work."""
from __future__ import annotations

import time
from typing import Any

from sqlalchemy import func, select

from . import perms
from .models import (Backend, Meta, Model, ModelGrant, Role, Team, UsageEvent, User, UserOverride)
from .settings_reg import BY_ID as SETTING_DEFS

MODES = ["chat", "code", "image", "video", "audio", "knowledge", "agents", "work"]
# Modes whose backends are implemented in this release. Others report a truthful unsupported state.
# code / knowledge / agents(assistants) run on the chat engine; image, video and audio need their own backends.
IMPLEMENTED_MODES = {"chat", "code", "knowledge", "agents", "work"}
MODE_CAPABILITY = {"chat": "chat", "code": "chat", "image": "image", "video": "video", "audio": "audio",
                   "knowledge": "chat", "agents": "chat", "work": "chat"}


def seed(s) -> None:
    """Idempotent: create role templates on first run. Never touches existing (possibly renamed) roles."""
    from . import tenancy_svc
    tenancy_svc.ensure_default_org(s)  # a fresh install starts with one organisation; existing ones get it from migration 8
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
    from . import catalog_svc
    return catalog_svc.subject_keys(p)


# Which grant mode governs each product mode. Knowledge and assistants run on chat models.
GRANT_MODE = {"chat": "chat", "knowledge": "chat", "agents": "chat", "work": "chat", "code": "code", "image": "image", "video": "video",
              "audio": "audio", "vision": "vision"}


def model_access(s, p: perms.Principal, m: Model, backend: Backend, st, mode: str | None = None,
                 grants: list[ModelGrant] | None = None) -> tuple[bool, str]:
    """(allowed, reason). `mode` is a product mode or 'vision' (chat with an image). Deny always wins over allow; no
    grant means no access; a grant limited to other modes does not apply."""
    from . import catalog_svc
    use_all = p.can("models.use_all")  # restricted models too; an explicit deny still wins
    if not m.available or not backend.enabled or (not m.enabled and not use_all):
        return False, "This model is not available."
    mode = mode or "chat"
    caps = catalog_svc.capabilities(m)
    need = "vision" if mode == "vision" else MODE_CAPABILITY[mode]
    if need not in caps or (mode == "vision" and "chat" not in caps):
        return False, "This model cannot read images." if mode == "vision" else f"This model does not support {mode}."
    if grants is None:
        grants = list(s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id)).scalars())
    ok, why = catalog_svc.decide(grants, p, GRANT_MODE[mode])
    if not ok and not (use_all and not catalog_svc.denied(grants, p, GRANT_MODE[mode])):
        return False, why
    if is_external(m, backend) and not st.settings.deployment(s, "privacy.external_egress"):
        return False, "This model runs on an external service, which your organisation has turned off."
    return True, ""


def is_external(m: Model, backend: Backend) -> bool:
    if backend.external:
        return True
    if backend.adapter == "sarness" and (m.real_id or "").startswith("endpoint:"):
        # A remote-endpoint model of a Sarness engine is external unless the administrator declared that endpoint local
        # (backend option local_endpoints, e.g. Trinodal AI on the same machine). Read live, so removing an id from the
        # list makes its models external again at once, without waiting for the next model sync.
        from .adapters.sarness import is_local_endpoint
        return not is_local_endpoint(m.real_id, backend.options)
    return bool((m.meta or {}).get("external"))


def accessible_models(s, p: perms.Principal, st, mode: str = "chat", *, include_hidden: bool = True) -> list[tuple[Model, Backend]]:
    """Models this person may use for `mode`, in the administrator's order: those offered to them first, then the
    restricted ones they may still use because they hold models.use_all."""
    from . import catalog_svc
    backends = {b.id: b for b in s.execute(select(Backend)).scalars()}
    by_model = catalog_svc.grants_by_model(s)
    offered, restricted = [], []
    for m in catalog_svc.ordered_models(s):
        if m.hidden and not include_hidden:
            continue
        b = backends.get(m.backend_id)
        if b and model_access(s, p, m, b, st, mode, by_model.get(m.id, []))[0]:
            (restricted if is_restricted(s, p, m, mode, by_model.get(m.id, [])) else offered).append((m, b))
    return offered + restricted


# Why a model is not simply "offered" to someone who can still use it. These are ACCESS facts about the viewer's account and
# the administrator's configuration. They say nothing about the model's content, origin or safety characteristics (an
# abliterated, fine-tuned or locally hosted model is offered like any other once an administrator has enabled it), and
# nothing about whether it is loaded or runnable right now (that is the engine's `state`).
ACCESS_OFFERED = "offered"
ACCESS_NOT_ENABLED = "not_enabled"
ACCESS_NOT_GRANTED = "access_restricted"
ACCESS_TEXT = {
    ACCESS_NOT_ENABLED: ("Not enabled",
                         "An administrator has not turned this model on for users yet. You can see and use it only because of "
                         "your administrator permissions."),
    ACCESS_NOT_GRANTED: ("Access restricted",
                         "Your role has not been granted this model. You can use it only because of your administrator "
                         "permissions."),
}


def access_state(s, p: perms.Principal, m: Model, mode: str, grants: list[ModelGrant]) -> str:
    """ACCESS_OFFERED, or the one configured reason the person reaches this model only through models.use_all."""
    from . import catalog_svc
    if not m.enabled:
        return ACCESS_NOT_ENABLED
    if not catalog_svc.decide(grants, p, GRANT_MODE.get(mode, mode))[0]:
        return ACCESS_NOT_GRANTED
    return ACCESS_OFFERED


def is_restricted(s, p: perms.Principal, m: Model, mode: str, grants: list[ModelGrant]) -> bool:
    """True when the person can use the model only because of models.use_all (it is not offered to them)."""
    return access_state(s, p, m, mode, grants) != ACCESS_OFFERED


# ---- modes ----------------------------------------------------------------------------------------
def mode_state(s, p: perms.Principal, st, mode: str) -> dict:
    """The single source of truth for 'can this person use this mode right now', with a truthful reason."""
    if mode not in MODES:
        return {"enabled": False, "reason": "Unknown mode."}
    if not st.settings.value_for(s, f"modes.{mode}.enabled", p):
        return {"enabled": False, "reason": "Turned off by your administrator.", "code": "disabled"}
    if not p.can(f"mode.{mode}"):
        return {"enabled": False, "reason": "Your group has no access.", "code": "forbidden"}
    if mode == "work":  # the Work Mode feature switch is separate from the mode switch: both must be on
        if not st.settings.deployment(s, "work.enabled"):
            return {"enabled": False, "reason": "Work Mode is not switched on for this server.", "code": "disabled"}
    if mode in ("image", "video"):  # media block: implemented exactly when a media-capable backend is connected
        from . import media_svc
        why = media_svc.unavailable_reason(s, mode)
        if why:
            return {"enabled": False, "reason": why, "code": "unsupported"}
    elif mode not in IMPLEMENTED_MODES:
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
    tri = _filter_trinodal(p, f.get("trinodal"))
    if tri:
        out["trinodal"] = tri
    if out or unavailable:
        out["_unavailable"] = sorted(set(unavailable))
    return out


def _filter_trinodal(p: perms.Principal, full) -> dict:
    """The Trinodal AI run record (which nodes took part, timings, trace id), reduced to what this person may read. It
    is run metadata only, and it reaches no one without the matching technical permission: the nodes and route need
    tech.node.name, the trace id tech.job.trace, the language core's model and backend tech.runtime.name, and the
    time spent tech.perf.duration."""
    if not isinstance(full, dict) or not full:
        return {}
    out: dict[str, Any] = {}

    def take(perm, keys):
        if p.can(perm):
            out.update({k: full[k] for k in keys if full.get(k) is not None})

    take("tech.node.name", ["nodes", "route", "trained_components", "llm_calls", "refused", "errors"])
    take("tech.job.trace", ["trace_id"])
    take("tech.runtime.name", ["language_core"])
    take("tech.perf.duration", ["ms"])
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


def mfa_policy(s, st) -> str:
    return st.settings.deployment(s, "auth.mfa_policy")


def mfa_required(s, st, p: perms.Principal) -> bool:
    """Whether this person must have a second factor, from the deployment's MFA policy."""
    policy = mfa_policy(s, st)
    if policy == "everyone":
        return True
    if policy == "admins":
        return is_admin_like(p)
    if policy == "roles":
        return bool(set(p.role_ids) & set(st.settings.deployment(s, "auth.mfa_roles") or []))
    return False


def is_admin_like(p: perms.Principal) -> bool:
    return any(x.id.startswith("admin.") and p.can(x.id) for x in perms.REGISTRY)


def setting_default(key: str):
    return SETTING_DEFS[key].default


def display_id(real_id: str) -> str:
    """Technical-visibility form of a model id: never a server file path."""
    if "/" in real_id and (real_id.startswith(("/", "~")) or "\\" in real_id or real_id.count("/") > 2):
        return real_id.replace("\\", "/").rsplit("/", 1)[-1]
    return real_id


def purge_user_files(st, user_id: str) -> None:
    """Remove a deleted person's encrypted files at once (attachments and generated media). Their database rows go by
    cascade; without this the files would linger until the next retention sweep."""
    import shutil
    for sub in ("attachments", "assets"):
        d = st.cfg.data_dir / sub / user_id
        if d.is_dir() and d.parent == st.cfg.data_dir / sub:
            shutil.rmtree(d, ignore_errors=True)
    for store in ("attachment_mem", "media_mem"):
        mem = st.__dict__.get(store) or {}
        for k in [k for k, v in mem.items() if v.get("user_id") == user_id]:
            mem.pop(k, None)


def cloud_open_to(st, s, principal) -> bool:
    """The rollout rule for the cloud features (workspaces, files, contexts, agents): everyone, or platform administrators only."""
    if st.settings.deployment(s, "cloud.rollout") != "admins":
        return True
    return bool(principal.user_id) and principal.can("admin.settings.edit")
