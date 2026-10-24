from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from .. import adapters, audit, catalog_svc, perms, services, telemetry
from ..backends_svc import adapter_for, sync_models
from ..deps import Ctx, audit_ctx, current, require
from ..models import (
    AuditEvent,
    Backend,
    Conversation,
    Model,
    ModelGrant,
    Role,
    SettingValue,
    Team,
    UsageEvent,
    User,
)
from ..settings_reg import BY_ID, DEFS, SettingError
from .me import ui_config

router = APIRouter(prefix="/api/admin")


def _err(status, code, message, **kw):
    return HTTPException(status, {"code": code, "message": message, **kw})


# ---- overview (real counts only) ------------------------------------------------------------------------
@router.get("/overview")
def overview(ctx: Ctx = Depends(require("admin.overview"))):
    s = ctx.s
    since = time.time() - 86400
    out: dict[str, Any] = {}
    if ctx.principal.can("admin.users.view"):
        out["people"] = {"total": s.execute(select(func.count()).select_from(User)).scalar(),
                         "pending_approval": s.execute(select(func.count()).select_from(User).where(User.status == "pending_approval")).scalar()}
    else:
        out["people"] = None
    out["models"] = {"enabled": s.execute(select(func.count()).select_from(Model).where(Model.enabled == True)).scalar(),  # noqa: E712
                     "discovered": s.execute(select(func.count()).select_from(Model)).scalar()}
    out["backends"] = s.execute(select(func.count()).select_from(Backend)).scalar()
    out["usage_24h"] = {"requests": s.execute(select(func.count()).select_from(UsageEvent).where(UsageEvent.ts >= since)).scalar(),
                        "errors": s.execute(select(func.count()).select_from(UsageEvent).where(UsageEvent.ts >= since, UsageEvent.status == "error")).scalar()}
    if ctx.principal.can("admin.audit.view"):
        out["audit"] = audit.verify_chain(s)
        out["recent_audit"] = [{"ts": e.ts, "action": e.action, "actor": e.actor, "target": e.target}
                               for e in s.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(8)).scalars()]
    out["signup_mode"] = ctx.st.settings.deployment(s, "signup.mode")
    out["exposure"] = ctx.st.settings.deployment(s, "access.exposure")
    out["bound"] = {"host": ctx.st.cfg.host, "port": ctx.st.cfg.port}
    out["setup_hints"] = _hints(ctx)
    return out


def _hints(ctx):
    s, h = ctx.s, []
    if not s.execute(select(func.count()).select_from(Backend)).scalar():
        h.append({"id": "backend", "text": "No inference backend is connected yet.", "link": "/admin/backends"})
    elif not s.execute(select(func.count()).select_from(Model).where(Model.enabled == True)).scalar():  # noqa: E712
        h.append({"id": "models", "text": "No model is enabled for people yet.", "link": "/admin/models"})
    if not ctx.user.totp_enabled:
        h.append({"id": "mfa", "text": "Turn on multi-factor authentication for your own account.", "link": "/account"})
    if ctx.st.cfg.host not in ("127.0.0.1", "localhost", "::1"):
        h.append({"id": "tls", "text": "The service is reachable beyond this machine. Put it behind HTTPS before real use.", "link": "/admin/settings?q=exposure"})
    return h


# ---- settings -------------------------------------------------------------------------------------------
@router.get("/settings")
def list_settings(ctx: Ctx = Depends(require("admin.settings.view")), q: str | None = None, category: str | None = None):
    caps = ctx.st.settings.capabilities(ctx.s)
    rows = {(r.key, r.scope_type, r.scope_id): r for r in ctx.s.execute(select(SettingValue)).scalars()}
    out = []
    ql = (q or "").lower().strip()
    for d in DEFS:
        if category and d.category != category:
            continue
        if ql and not (ql in d.id.lower() or ql in d.label.lower() or ql in d.description.lower() or any(ql in a for a in d.aliases)):
            continue
        dep = ctx.st.settings.deployment(ctx.s, d.id)
        item = d.to_public()
        item["value"] = bool(dep) if d.secret else dep
        item["overrides"] = [{"scope_type": k[1], "scope_id": k[2]} for k in rows if k[0] == d.id and k[1] != "deployment"]
        item["is_default"] = (d.id, "deployment", "") not in rows
        item["can_edit"] = ctx.principal.can(d.edit_perm)
        if d.requires:
            item["prerequisite"] = caps.get(d.requires, {"ok": False, "why": "Unavailable."})
        out.append(item)
    cats = []
    for d in DEFS:
        if d.category not in cats:
            cats.append(d.category)
    return {"categories": cats, "settings": out}


class SetIn(BaseModel):
    value: Any
    scope_type: str = "deployment"
    scope_id: str = ""
    confirm_weaker: bool = False


def _can_edit_setting(ctx: Ctx, key: str):
    d = BY_ID.get(key)
    if d is None:
        raise _err(404, "not_found", "Unknown setting.")
    if not ctx.principal.can(d.edit_perm):
        raise _err(403, "forbidden", "You do not have permission to change this setting.", permission=d.edit_perm)
    return d


@router.post("/settings/{key}/impact")
def setting_impact(key: str, body: SetIn, ctx: Ctx = Depends(require("admin.settings.view"))):
    _can_edit_setting(ctx, key)
    try:
        v = ctx.st.settings.validate(ctx.s, key, body.value, body.scope_type, body.scope_id)
    except SettingError as e:
        raise _err(422, "invalid_setting", str(e), setting=key)
    return ctx.st.settings.impact(ctx.s, key, v, body.scope_type, body.scope_id)


@router.put("/settings/{key}")
def put_setting(key: str, body: SetIn, ctx: Ctx = Depends(current)):
    _can_edit_setting(ctx, key)
    try:
        imp = ctx.st.settings.set(ctx.s, ctx.user.id, key, body.value, body.scope_type, body.scope_id, ip=ctx.ip,
                                  confirm_weaker=body.confirm_weaker)
    except SettingError as e:
        code = "confirm_required" if "confirm" in str(e) else "invalid_setting"
        raise _err(409 if code == "confirm_required" else 422, code, str(e), setting=key)
    _guard_lockout(ctx)
    return imp


def _guard_lockout(ctx: Ctx):
    """A setting change must never lock the acting administrator out of the settings they just used."""
    ctx.s.flush()
    if not ctx.user.totp_enabled and services.mfa_required(ctx.s, ctx.st, ctx.principal):
        ctx.s.rollback()
        raise _err(409, "would_lock_out", "Turn on multi-factor authentication for your own account first: this policy "
                                          "would require it of you.")


@router.delete("/settings/{key}")
def reset_setting(key: str, scope_type: str, scope_id: str = "", ctx: Ctx = Depends(current)):
    _can_edit_setting(ctx, key)
    try:
        ctx.st.settings.reset(ctx.s, ctx.user.id, key, scope_type, scope_id, ip=ctx.ip)
    except SettingError as e:
        raise _err(422, "invalid_setting", str(e))
    return {"ok": True}


@router.get("/settings/{key}/effective")
def setting_effective(key: str, user_id: str, ctx: Ctx = Depends(require("admin.settings.view"))):
    """Effective-value inspector for one person: which layer won, and why."""
    if key not in BY_ID:
        raise _err(404, "not_found", "Unknown setting.")
    u = ctx.s.get(User, user_id)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    p = services.load_principal(ctx.s, u)
    r = ctx.st.settings.effective(ctx.s, key, role_ids=p.role_ids, team_ids=p.team_ids, user_id=u.id)
    if BY_ID[key].secret:
        r = {**r, "value": None, "layers": []}
    return r


@router.get("/settings-history")
def settings_history(ctx: Ctx = Depends(require("admin.settings.view")), key: str | None = None, limit: int = 100):
    return ctx.st.settings.history(ctx.s, key, min(limit, 500))


@router.post("/settings-history/{rid}/rollback")
def rollback(rid: int, ctx: Ctx = Depends(current)):
    from ..models import SettingRevision
    rev = ctx.s.get(SettingRevision, rid)
    if rev is None:
        raise _err(404, "not_found", "No such revision.")
    _can_edit_setting(ctx, rev.key)
    try:
        return ctx.st.settings.rollback(ctx.s, ctx.user.id, rid, ctx.ip)
    except SettingError as e:
        raise _err(422, "invalid_setting", str(e))


@router.get("/settings-export")
def export_settings(ctx: Ctx = Depends(require("admin.settings.view"))):
    audit_ctx(ctx, "settings.export")
    return ctx.st.settings.export_profile(ctx.s)


class ImportIn(BaseModel):
    profile: dict
    dry_run: bool = True
    confirm_weaker: bool = False


@router.post("/settings-import")
def import_settings(body: ImportIn, ctx: Ctx = Depends(require("admin.settings.view"))):
    # Each included key is checked against its own edit permission.
    for it in body.profile.get("settings", []):
        d = BY_ID.get(it.get("key"))
        if d and not ctx.principal.can(d.edit_perm):
            raise _err(403, "forbidden", f"You cannot change '{d.label}'.", permission=d.edit_perm)
    try:
        return ctx.st.settings.import_profile(ctx.s, ctx.user.id, body.profile, confirm_weaker=body.confirm_weaker,
                                              dry_run=body.dry_run, ip=ctx.ip)
    except SettingError as e:
        raise _err(422, "invalid_profile", str(e))


# ---- technical visibility presets -------------------------------------------------------------------------
@router.get("/tech-presets")
def tech_presets(ctx: Ctx = Depends(require("admin.users.view"))):
    stored = ctx.st.settings.deployment(ctx.s, "tech.presets")
    presets = stored or perms.TECH_PRESET_DEFAULTS
    return {"presets": presets, "fields": [{"id": p.id, "label": p.label} for p in perms.REGISTRY if p.id.startswith("tech.")],
            "defaults": perms.TECH_PRESET_DEFAULTS}


# ---- preview as role -------------------------------------------------------------------------------------
@router.get("/preview/{role_id}")
def preview_role(role_id: str, ctx: Ctx = Depends(require("admin.preview_role"))):
    """Shows the interface and exact server-enforced permissions of a role using only synthetic context."""
    try:
        p = services.principal_for_role(ctx.s, role_id)
    except KeyError:
        raise _err(404, "not_found", "No such role.")
    return {"role": role_id, "permissions": p.all_allowed(), "ui": ui_config(ctx.s, ctx.st, p, preview=True),
            "note": "Preview uses the role's rules only. Team and per-person overrides are not applied, and no one's content is shown."}


# ---- backends & models ------------------------------------------------------------------------------------
class BackendIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    adapter: str
    base_url: str = Field(max_length=500)
    api_key: str | None = Field(default=None, max_length=500)  # write-only
    external: bool = False
    enabled: bool = True


def _backend_out(b: Backend) -> dict:
    ad = adapters.REGISTRY.get(b.adapter)
    return {"id": b.id, "name": b.name, "adapter": b.adapter, "base_url": b.base_url, "has_api_key": bool(b.api_key_enc),
            "external": b.external, "enabled": b.enabled, "version": (b.options or {}).get("version"),
            "last_seen": (b.options or {}).get("last_seen"), "manifest": ad.manifest.as_dict() if ad else None}


@router.get("/backends")
def list_backends(ctx: Ctx = Depends(require("admin.backends.manage"))):
    return [_backend_out(b) for b in ctx.s.execute(select(Backend).order_by(Backend.name)).scalars()]


@router.post("/backends")
def create_backend(body: BackendIn, ctx: Ctx = Depends(require("admin.backends.manage"))):
    if body.adapter not in adapters.REGISTRY:
        raise _err(422, "invalid", "Unknown adapter.")
    if not body.base_url.startswith(("http://", "https://")):
        raise _err(422, "invalid", "The address must start with http:// or https://")
    if ctx.s.execute(select(Backend).where(Backend.name == body.name.strip())).scalar_one_or_none():
        raise _err(409, "exists", "A backend with that name exists.")
    b = Backend(name=body.name.strip(), adapter=body.adapter, base_url=body.base_url.rstrip("/"), external=body.external,
                enabled=body.enabled, api_key_enc=ctx.st.vault.encrypt(body.api_key, "backend") if body.api_key else None)
    ctx.s.add(b)
    ctx.s.flush()
    audit_ctx(ctx, "backend.create", b.id, {"adapter": b.adapter, "external": b.external, "url": b.base_url})
    return _backend_out(b)


@router.put("/backends/{bid}")
def update_backend(bid: str, body: BackendIn, ctx: Ctx = Depends(require("admin.backends.manage"))):
    b = ctx.s.get(Backend, bid)
    if b is None:
        raise _err(404, "not_found", "No such backend.")
    if body.external is False and b.external:
        pass  # stricter -> looser is allowed but audited below
    b.name, b.base_url, b.external, b.enabled = body.name.strip(), body.base_url.rstrip("/"), body.external, body.enabled
    if body.api_key:
        b.api_key_enc = ctx.st.vault.encrypt(body.api_key, "backend")
    audit_ctx(ctx, "backend.update", bid, {"external": b.external, "enabled": b.enabled})
    return _backend_out(b)


@router.delete("/backends/{bid}")
def delete_backend(bid: str, ctx: Ctx = Depends(require("admin.backends.manage"))):
    b = ctx.s.get(Backend, bid)
    if b is None:
        raise _err(404, "not_found", "No such backend.")
    audit_ctx(ctx, "backend.delete", bid)
    ctx.s.delete(b)
    return {"ok": True}


@router.post("/backends/{bid}/test")
async def test_backend(bid: str, ctx: Ctx = Depends(require("admin.backends.manage"))):
    b = ctx.s.get(Backend, bid)
    if b is None:
        raise _err(404, "not_found", "No such backend.")
    ad = adapter_for(b, ctx.st)
    ctx.s.commit()
    return await ad.health()


@router.post("/backends/{bid}/sync")
async def sync_backend(bid: str, ctx: Ctx = Depends(require("admin.backends.manage"))):
    ctx.s.commit()
    try:
        r = await sync_models(ctx.st.db.session, ctx.st, bid)
    except adapters.AdapterError as e:
        raise _err(e.status, e.kind, str(e))
    with ctx.st.db.session() as s:
        audit.record(s, ctx.user.id, "backend.sync", bid, r, ctx.ip)
    return r


def _model_out(m: Model, b: Backend | None, grants: list[ModelGrant], full_id: bool = False) -> dict:
    # Backends such as llama.cpp report a server file path as the model id. Everyone sees the file name;
    # the raw value is a technical field and is returned only to holders of tech.model.id.
    out_id = {"real_id_full": m.real_id} if full_id and m.real_id != services.display_id(m.real_id) else {}
    return {**out_id, "id": m.id, "backend_id": m.backend_id, "backend": b.name if b else None, "runtime": b.adapter if b else None,
            "real_id": services.display_id(m.real_id), "alias": m.alias, "enabled": m.enabled, "pinned": m.pinned, "tags": m.tags or [],
            "capabilities": m.capabilities or [], "meta": m.meta or {}, "available": m.available,
            "system_prompt_set": bool(m.system_prompt), "gen_limits": m.gen_limits or {}, "allow_tools": m.allow_tools,
            "external": bool(b and (b.external or (m.meta or {}).get("external"))),
            "grants": [{"subject_type": g.subject_type, "subject_id": g.subject_id, "effect": g.effect, "modes": g.modes}
                       for g in grants],
            "hidden": m.hidden, "sort": m.sort, "profile": m.profile or {}, "defaults": m.defaults or {}}


def _model_query(q=None, tag=None, capability=None, backend=None, enabled=None, search_raw_id=False):
    stmt = select(Model)
    if q:
        like = f"%{q.lower()}%"
        # Searching the raw id would let a caller probe server paths they may not see.
        stmt = stmt.where(or_(func.lower(Model.alias).like(like), func.lower(Model.real_id).like(like)) if search_raw_id
                          else func.lower(Model.alias).like(like))
    if backend:
        stmt = stmt.where(Model.backend_id == backend)
    if enabled is not None:
        stmt = stmt.where(Model.enabled == enabled)
    return stmt, tag, capability


@router.get("/models")
def list_admin_models(ctx: Ctx = Depends(require("admin.models.manage")), q: str | None = None, tag: str | None = None,
                      capability: str | None = None, backend: str | None = None, enabled: bool | None = None,
                      sort: str = "alias", page: int = 1, per_page: int = 25):
    full = ctx.principal.can("tech.model.id")
    stmt, tag, capability = _model_query(q, tag, capability, backend, enabled, full)
    rows = ctx.s.execute(stmt.order_by(Model.alias)).scalars().all()
    if tag:
        rows = [m for m in rows if tag in (m.tags or [])]
    if capability:
        rows = [m for m in rows if capability in (m.capabilities or [])]
    total = len(rows)
    if sort == "backend":
        rows.sort(key=lambda m: m.backend_id)
    page_rows = rows[(page - 1) * per_page: page * per_page]
    backends = {b.id: b for b in ctx.s.execute(select(Backend)).scalars()}
    grants: dict[str, list] = {}
    for g in ctx.s.execute(select(ModelGrant)).scalars():
        grants.setdefault(g.model_id, []).append(g)
    icons = ctx.st.settings.deployment(ctx.s, "models.provider_icons") or {}
    logos = bool(ctx.st.settings.deployment(ctx.s, "models.developer_logos"))
    items = []
    for m in page_rows:  # the icon the picker shows, so an administrator recognises the same model
        out = _model_out(m, backends.get(m.backend_id), grants.get(m.id, []), full)
        out["icon"] = catalog_svc.profile_of(m, icons, logos).get("icon")
        items.append(out)
    return {"total": total, "page": page, "per_page": per_page, "items": items}


class ModelPatch(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    pinned: bool | None = None
    tags: list[str] | None = None
    system_prompt: str | None = Field(default=None, max_length=8000)
    gen_limits: dict | None = None
    allow_tools: bool | None = None


@router.patch("/models/{mid}")
def patch_model(mid: str, body: ModelPatch, ctx: Ctx = Depends(require("admin.models.manage"))):
    m = ctx.s.get(Model, mid)
    if m is None:
        raise _err(404, "not_found", "No such model.")
    if body.alias:
        m.alias = body.alias.strip()
    if body.enabled is not None:
        m.enabled = body.enabled
    if body.pinned is not None:
        m.pinned = body.pinned
    if body.tags is not None:
        m.tags = sorted({t.strip().lower()[:30] for t in body.tags if t.strip()})
    if body.system_prompt is not None:
        m.system_prompt = body.system_prompt
    if body.gen_limits is not None:
        allowed = {"max_tokens": int, "temperature": float, "top_p": float, "top_k": int, "thinking": bool}
        clean = {}
        for k, v in body.gen_limits.items():
            if k == "thinking":  # on/off default for reasoning models (honoured by engines that support it)
                if not isinstance(v, bool):
                    raise _err(422, "invalid", "'thinking' must be true or false.")
                clean[k] = v
                continue
            if k not in allowed or isinstance(v, bool) or not isinstance(v, (int, float)):
                raise _err(422, "invalid", f"'{k}' is not a valid generation limit.")
            clean[k] = allowed[k](v)
        m.gen_limits = clean
    if body.allow_tools is not None:
        m.allow_tools = body.allow_tools
    audit_ctx(ctx, "model.update", mid, {k: v for k, v in body.model_dump(exclude_none=True).items() if k != "system_prompt"}
              | ({"system_prompt": "changed"} if body.system_prompt is not None else {}))
    return {"ok": True}


class GrantsIn(BaseModel):
    grants: list[dict]  # [{subject_type, subject_id, effect}]


@router.put("/models/{mid}/grants")
def set_grants(mid: str, body: GrantsIn, ctx: Ctx = Depends(require("admin.models.manage"))):
    m = ctx.s.get(Model, mid)
    if m is None:
        raise _err(404, "not_found", "No such model.")
    clean = _clean_grants(ctx, body.grants)
    for g in ctx.s.execute(select(ModelGrant).where(ModelGrant.model_id == mid)).scalars():
        ctx.s.delete(g)
    ctx.s.flush()
    for g in clean:
        ctx.s.add(ModelGrant(model_id=mid, **g))
    audit_ctx(ctx, "model.grants", mid, {"count": len(clean)})
    return {"ok": True}


def _clean_grants(ctx, grants):
    """Rules: subject (everyone, role, team or person), allow/deny, and optionally the modes the rule covers
    (chat, code, vision, image, video); no modes means every mode. One rule per subject."""
    seen, clean = set(), []
    for g in grants:
        t, i, e, modes = g.get("subject_type"), g.get("subject_id"), g.get("effect"), g.get("modes")
        if t not in ("everyone", "role", "team", "user") or e not in ("allow", "deny"):
            raise _err(422, "invalid", "Each rule needs a subject and allow/deny.")
        if t == "everyone":
            i = "*"
        elif ctx.s.get({"role": Role, "team": Team, "user": User}[t], i) is None:
            raise _err(422, "invalid", f"Unknown {t}.")
        if modes is not None:
            if not isinstance(modes, list) or not modes or any(m not in catalog_svc.GRANT_MODES for m in modes):
                raise _err(422, "invalid", "Modes must be chosen from " + ", ".join(catalog_svc.GRANT_MODES) + ".")
            modes = sorted(set(modes))
        if (t, i) in seen:
            continue
        seen.add((t, i))
        clean.append({"subject_type": t, "subject_id": i, "effect": e, "modes": modes})
    return clean


class ManualModelIn(BaseModel):
    backend_id: str
    real_id: str = Field(min_length=1, max_length=300, pattern=r"^[^\x00-\x1f\x7f]+$")
    alias: str | None = Field(default=None, min_length=1, max_length=120)
    capabilities: list[str] = Field(default_factory=lambda: ["chat"], max_length=6)


@router.post("/models")
def register_model(body: ManualModelIn, ctx: Ctx = Depends(require("admin.models.manage"))):
    """Register a model the backend does not list (some hosted APIs serve more than they report). Like a discovered
    model it starts disabled and with no grants; sync never marks it missing because it was never discovered."""
    from ..backends_svc import friendly_alias
    b = ctx.s.get(Backend, body.backend_id)
    if b is None:
        raise _err(404, "not_found", "No such backend.")
    rid = body.real_id.strip()
    if ctx.s.execute(select(Model).where(Model.backend_id == b.id, Model.real_id == rid)).scalar_one_or_none():
        raise _err(409, "exists", "That model is already in the catalogue for this backend.")
    caps = sorted(set(body.capabilities))
    if not caps or any(c not in ("chat", "vision", "tools", "image", "video", "embeddings") for c in caps):
        raise _err(422, "invalid", "Capabilities must be chosen from chat, vision, tools, image, video, embeddings.")
    m = Model(backend_id=b.id, real_id=rid, alias=(body.alias or friendly_alias(rid)).strip(), capabilities=caps,
              meta={"manual": True, **({"external": True} if b.external else {})}, enabled=False)
    ctx.s.add(m)
    ctx.s.flush()
    audit_ctx(ctx, "model.register", m.id, {"backend": b.id, "capabilities": caps})
    return {"id": m.id}


@router.delete("/models/{mid}")
def unregister_model(mid: str, ctx: Ctx = Depends(require("admin.models.manage"))):
    """Remove a manually registered model that nothing has used yet. Discovered or used models are hidden instead,
    so conversations and usage keep naming the model they ran on."""
    from ..media_models import MediaJob
    from ..models import Message, ModelGroupMember
    m = ctx.s.get(Model, mid)
    if m is None:
        raise _err(404, "not_found", "No such model.")
    if not (m.meta or {}).get("manual"):
        raise _err(409, "discovered", "Only models you registered by hand can be removed. Hide or disable this one instead.")
    used = any(ctx.s.execute(select(t.id).where(t.model_id == mid).limit(1)).first() for t in (Message, UsageEvent, MediaJob))
    if used:
        raise _err(409, "in_use", "This model has already been used, so it is kept for the record. Hide or disable it instead.")
    for row in [*ctx.s.execute(select(ModelGrant).where(ModelGrant.model_id == mid)).scalars(),
                *ctx.s.execute(select(ModelGroupMember).where(ModelGroupMember.model_id == mid)).scalars()]:
        ctx.s.delete(row)
    ctx.s.delete(m)
    audit_ctx(ctx, "model.unregister", mid, {})
    return {"ok": True}


class ModelBulk(BaseModel):
    action: str = Field(pattern="^(enable|disable|add_tag|remove_tag|grant_role|revoke_role|deny_team)$")
    ids: list[str] | None = None
    all_matching: dict | None = None
    expected_count: int
    value: str | None = None


@router.post("/models/bulk")
def bulk_models(body: ModelBulk, ctx: Ctx = Depends(require("admin.models.manage"))):
    if (body.ids is None) == (body.all_matching is None):
        raise _err(422, "invalid", "Send either ids or all_matching.")
    if body.ids is not None:
        models = [m for i in body.ids if (m := ctx.s.get(Model, i))]
    else:
        f = body.all_matching
        stmt, tag, cap = _model_query(f.get("q"), f.get("tag"), f.get("capability"), f.get("backend"), f.get("enabled"), ctx.principal.can("tech.model.id"))
        models = ctx.s.execute(stmt).scalars().all()
        if tag:
            models = [m for m in models if tag in (m.tags or [])]
        if cap:
            models = [m for m in models if cap in (m.capabilities or [])]
    if len(models) != body.expected_count:
        raise _err(409, "count_mismatch", f"This would affect {len(models)} models, not {body.expected_count}.")
    for m in models:
        if body.action == "enable":
            m.enabled = True
        elif body.action == "disable":
            m.enabled = False
        elif body.action in ("add_tag", "remove_tag"):
            tags = set(m.tags or [])
            (tags.add if body.action == "add_tag" else tags.discard)((body.value or "").strip().lower())
            m.tags = sorted(t for t in tags if t)
        else:
            if not body.value:
                raise _err(422, "invalid", "Choose a role or team.")
            st = "team" if body.action == "deny_team" else "role"
            if ctx.s.get(Team if st == "team" else Role, body.value) is None:
                raise _err(422, "invalid", f"Unknown {st}.")
            row = ctx.s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id, ModelGrant.subject_type == st,
                                                         ModelGrant.subject_id == body.value)).scalar_one_or_none()
            if body.action == "revoke_role":
                if row:
                    ctx.s.delete(row)
            else:
                eff = "deny" if body.action == "deny_team" else "allow"
                if row:
                    row.effect = eff
                else:
                    ctx.s.add(ModelGrant(model_id=m.id, subject_type=st, subject_id=body.value, effect=eff))
    audit_ctx(ctx, f"model.bulk.{body.action}", "", {"count": len(models), "scope": "ids" if body.ids is not None else "all_matching"})
    return {"done": len(models)}


@router.get("/explain/model")
def explain_model(user_id: str, model_id: str, ctx: Ctx = Depends(require("admin.users.view"))):
    """'Why can't they use Model X?' — deterministic answer naming the rule."""
    u, m = ctx.s.get(User, user_id), ctx.s.get(Model, model_id)
    if u is None or m is None:
        raise _err(404, "not_found", "No such person or model.")
    p = services.load_principal(ctx.s, u)
    b = ctx.s.get(Backend, m.backend_id)
    steps = []
    if not m.enabled or not m.available or not b.enabled:
        steps.append({"rule": "model status", "result": "deny", "detail": "The model is disabled, missing from its backend, or its backend is disabled."})
    mode_dec = p.decide("mode.chat")
    steps.append({"rule": "permission mode.chat", "result": "allow" if mode_dec.allowed else "deny", "detail": mode_dec.reason})
    names = {r.id: r.label for r in ctx.s.execute(select(Role)).scalars()}
    names.update({t.id: t.name for t in ctx.s.execute(select(Team)).scalars()})
    keys = services.subject_keys(p)
    grants = ctx.s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id)).scalars().all()
    mine = [g for g in grants if (g.subject_type, g.subject_id) in keys]
    for g in mine:
        steps.append({"rule": f"{g.subject_type} '{names.get(g.subject_id, 'direct')}' {g.effect}s this model", "result": g.effect,
                      "detail": "A deny anywhere overrides every allow." if g.effect == "deny" else ""})
    if not mine:
        steps.append({"rule": "model grants", "result": "deny", "detail": "No role, team or person has been given access to this model."})
    if services.is_external(m, b) and not ctx.st.settings.deployment(ctx.s, "privacy.external_egress"):
        steps.append({"rule": "privacy.external_egress", "result": "deny", "detail": "The model is on an external service and external inference is off."})
    ok, reason = services.model_access(ctx.s, p, m, b, ctx.st, "chat")
    return {"allowed": ok and mode_dec.allowed, "reason": "Allowed." if ok and mode_dec.allowed else (reason or mode_dec.reason), "steps": steps}


# ---- audit ---------------------------------------------------------------------------------------------
@router.get("/audit")
def audit_log(ctx: Ctx = Depends(require("admin.audit.view")), action: str | None = None, actor: str | None = None,
              page: int = 1, per_page: int = 50):
    stmt = select(AuditEvent)
    if action:
        stmt = stmt.where(AuditEvent.action.like(f"{action}%"))
    if actor:
        stmt = stmt.where(AuditEvent.actor == actor)
    total = ctx.s.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = ctx.s.execute(stmt.order_by(AuditEvent.id.desc()).limit(min(per_page, 200)).offset((page - 1) * per_page)).scalars().all()
    users = {u.id: u.email for u in ctx.s.execute(select(User)).scalars()}
    return {"total": total, "page": page, "items": [{"id": e.id, "ts": e.ts, "actor": e.actor, "actor_label": users.get(e.actor, e.actor or "system"),
                                                        "action": e.action, "target": e.target, "detail": e.detail, "ip": e.ip} for e in rows]}


@router.get("/audit/verify")
def audit_verify(ctx: Ctx = Depends(require("admin.audit.view"))):
    return audit.verify_chain(ctx.s)


# ---- system / telemetry -----------------------------------------------------------------------------------
@router.get("/system")
async def system(ctx: Ctx = Depends(require("admin.system.view"))):
    backends = ctx.s.execute(select(Backend)).scalars().all()
    ads = [(b, adapter_for(b, ctx.st)) for b in backends]
    ctx.s.commit()
    out = []
    for b, ad in ads:
        h = await ad.health()
        out.append({"id": b.id, "name": b.name, "adapter": b.adapter, "external": b.external, "health": h,
                    "system": await ad.system_info() if ad.manifest.system_telemetry and h["ok"] else None,
                    "telemetry_supported": ad.manifest.system_telemetry})
    return {"host": telemetry.host(), "storage": telemetry.storage(str(ctx.st.cfg.data_dir)), "gpus": telemetry.gpus(),
            "gpu_note": "No NVIDIA tooling found; other accelerators are not detected in this release." if telemetry.gpus() is None else "",
            "backends": out, "uptime_s": round(time.time() - ctx.st.started)}


@router.get("/conversation-counts")
def conv_counts(ctx: Ctx = Depends(require("admin.system.view"))):
    """Metadata only: counts, never titles or text."""
    return {"conversations": ctx.s.execute(select(func.count()).select_from(Conversation)).scalar()}
