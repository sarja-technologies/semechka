"""Model registry: the administrator's curated catalogue and the chooser people see.

Groups, order, pinning, hiding and icons are presentation. Access is decided only by grants (services.model_access),
so a group never lets anyone use a model and hiding a model never replaces a deny rule.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import catalog_svc, services
from ..deps import Ctx, audit_ctx, current, require
from ..models import (
    Backend,
    Model,
    ModelGrant,
    ModelGroup,
    ModelGroupMember,
    Role,
    Team,
    User,
)
from .misc import save_image_asset

router = APIRouter(prefix="/api")
MANAGE = "admin.models.manage"


def _err(status, code, message):
    return HTTPException(status, {"code": code, "message": message})


def _provider_icons(ctx: Ctx) -> dict:
    return ctx.st.settings.deployment(ctx.s, "models.provider_icons") or {}


def _grant_out(g: ModelGrant) -> dict:
    return {"subject_type": g.subject_type, "subject_id": g.subject_id, "effect": g.effect, "modes": g.modes}


# ---- administrator ---------------------------------------------------------------------------------------
@router.get("/admin/catalog")
def admin_catalog(ctx: Ctx = Depends(require(MANAGE))):
    full = ctx.principal.can("tech.model.id")
    backends = {b.id: b for b in ctx.s.execute(select(Backend)).scalars()}
    grants = catalog_svc.grants_by_model(ctx.s)
    icons = _provider_icons(ctx)
    groups = catalog_svc.groups(ctx.s)
    member_of: dict[str, list[str]] = {}
    for g, mids in groups:
        for mid in mids:
            member_of.setdefault(mid, []).append(g.id)
    models = []
    for m in catalog_svc.ordered_models(ctx.s):
        b = backends.get(m.backend_id)
        prof = catalog_svc.profile_of(m, icons)
        models.append({
            "id": m.id, "alias": m.alias, "real_id": services.display_id(m.real_id) if full else None,
            "backend": b.name if b else None, "enabled": m.enabled, "available": m.available, "pinned": m.pinned,
            "hidden": m.hidden, "sort": m.sort, "tags": m.tags or [], "reported_capabilities": m.capabilities or [],
            "capabilities": catalog_svc.capabilities(m), "profile": prof, "admin_profile": m.profile or {},
            "defaults": m.defaults or {}, "groups": member_of.get(m.id, []),
            "external": bool(b and services.is_external(m, b)), "manual": bool((m.meta or {}).get("manual")),
            "media_modes": ((m.meta or {}).get("media") or {}).get("modes") or [],
            "grants": [_grant_out(g) for g in grants.get(m.id, [])]})
    return {"models": models,
            "groups": [{"id": g.id, "name": g.name, "description": g.description, "sort": g.sort,
                        "collapsed": g.collapsed, "audience": g.audience or [], "members": mids} for g, mids in groups],
            "providers": [{"id": p.id, "label": p.label, "mark": p.mark, "tone": p.tone, "icon": icons.get(p.id)}
                          for p in catalog_svc.PROVIDERS],
            "grant_modes": catalog_svc.GRANT_MODES,
            "icon_note": "Semechka ships neutral lettermarks, not vendor logos: their trademark licences do not allow "
                         "redistribution with an Apache-2.0 project. Upload a logo you are licensed to use per "
                         "provider or per model."}


class OrderIn(BaseModel):
    ids: list[str] = Field(max_length=5000)


@router.put("/admin/catalog/order")
def set_order(body: OrderIn, ctx: Ctx = Depends(require(MANAGE))):
    """The chooser's order, exactly as given (never re-sorted alphabetically). Unlisted models keep their place after."""
    rows = {m.id: m for m in ctx.s.execute(select(Model)).scalars()}
    seen = []
    for mid in dict.fromkeys(body.ids):
        if mid not in rows:
            raise _err(422, "invalid", "Unknown model in the order.")
        seen.append(mid)
    rest = sorted((m for mid, m in rows.items() if mid not in set(seen)), key=lambda m: (m.sort, m.alias))
    for i, mid in enumerate(seen):
        rows[mid].sort = i
    for j, m in enumerate(rest):
        m.sort = len(seen) + j
    audit_ctx(ctx, "catalog.order", "", {"count": len(seen)})
    return {"ok": True}


class EntryPatch(BaseModel):
    alias: str | None = Field(default=None, min_length=1, max_length=120)
    pinned: bool | None = None
    hidden: bool | None = None
    profile: dict | None = None  # replaces the administrator's overrides; omitted fields fall back to detected values
    defaults: dict | None = None  # {"ctx": tokens} context window to load the model with


@router.patch("/admin/catalog/models/{mid}")
def patch_entry(mid: str, body: EntryPatch, ctx: Ctx = Depends(require(MANAGE))):
    m = ctx.s.get(Model, mid)
    if m is None:
        raise _err(404, "not_found", "No such model.")
    if body.alias:
        m.alias = body.alias.strip()
    if body.pinned is not None:
        m.pinned = body.pinned
    if body.hidden is not None:
        m.hidden = body.hidden
    if body.profile is not None:
        try:
            m.profile = catalog_svc.clean_profile(body.profile)
        except ValueError as e:
            raise _err(422, "invalid", str(e)) from e
    if body.defaults is not None:
        d = {}
        v = body.defaults.get("ctx")
        if v not in (None, "", 0):
            if isinstance(v, bool) or not isinstance(v, int) or not 512 <= v <= 2_000_000:
                raise _err(422, "invalid", "Context window is a whole number of tokens between 512 and 2,000,000.")
            d["ctx"] = v
        m.defaults = d
    audit_ctx(ctx, "catalog.model", mid, {k: ("changed" if k == "profile" else v)
                                          for k, v in body.model_dump(exclude_none=True).items()})
    return {"ok": True, "profile": catalog_svc.profile_of(m, _provider_icons(ctx))}


@router.post("/admin/catalog/icons")
async def upload_icon(file: UploadFile = File(...), ctx: Ctx = Depends(require(MANAGE))):
    return await save_image_asset(ctx, file)


class ProviderIconIn(BaseModel):
    icon: str | None = None  # an uploaded icon id, or null to return to the lettermark


@router.put("/admin/catalog/providers/{pid}/icon")
def set_provider_icon(pid: str, body: ProviderIconIn, ctx: Ctx = Depends(require(MANAGE))):
    if pid not in catalog_svc.BY_PROVIDER:
        raise _err(404, "not_found", "Unknown provider.")
    icons = dict(_provider_icons(ctx))
    if body.icon:
        icons[pid] = body.icon
    else:
        icons.pop(pid, None)
    from ..settings_svc import SettingError
    try:
        ctx.st.settings.set(ctx.s, ctx.user.id, "models.provider_icons", icons, ip=ctx.ip)
    except SettingError as e:
        raise _err(422, "invalid", str(e)) from e
    return {"ok": True, "icons": icons}


# ---- groups ---------------------------------------------------------------------------------------------
class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=300)
    collapsed: bool = False
    audience: list[dict] = Field(default_factory=list, max_length=50)


def _clean_audience(ctx: Ctx, aud: list[dict]) -> list[dict]:
    out = []
    for a in aud:
        t, i = a.get("type"), a.get("id")
        if t not in ("role", "team", "user") or ctx.s.get({"role": Role, "team": Team, "user": User}[t], i) is None:
            raise _err(422, "invalid", "Each audience entry is an existing role, team or person.")
        out.append({"type": t, "id": i})
    return out


def _own_group(ctx: Ctx, gid: str) -> ModelGroup:
    g = ctx.s.get(ModelGroup, gid)
    if g is None:
        raise _err(404, "not_found", "No such group.")
    return g


@router.post("/admin/catalog/groups")
def create_group(body: GroupIn, ctx: Ctx = Depends(require(MANAGE))):
    n = len(catalog_svc.groups(ctx.s))
    g = ModelGroup(name=body.name.strip(), description=body.description.strip(), collapsed=body.collapsed,
                   audience=_clean_audience(ctx, body.audience), sort=n)
    ctx.s.add(g)
    ctx.s.flush()
    audit_ctx(ctx, "catalog.group.create", g.id, {"name": g.name})
    return {"id": g.id}


@router.patch("/admin/catalog/groups/{gid}")
def update_group(gid: str, body: GroupIn, ctx: Ctx = Depends(require(MANAGE))):
    g = _own_group(ctx, gid)
    g.name, g.description, g.collapsed = body.name.strip(), body.description.strip(), body.collapsed
    g.audience = _clean_audience(ctx, body.audience)
    audit_ctx(ctx, "catalog.group.update", g.id, {"name": g.name})
    return {"ok": True}


@router.delete("/admin/catalog/groups/{gid}")
def delete_group(gid: str, ctx: Ctx = Depends(require(MANAGE))):
    """Removes the heading only. Its models stay in the catalogue and in any other group."""
    g = _own_group(ctx, gid)
    for mm in ctx.s.execute(select(ModelGroupMember).where(ModelGroupMember.group_id == gid)).scalars():
        ctx.s.delete(mm)
    ctx.s.delete(g)
    audit_ctx(ctx, "catalog.group.delete", gid, {"name": g.name})
    return {"ok": True}


@router.put("/admin/catalog/groups-order")
def order_groups(body: OrderIn, ctx: Ctx = Depends(require(MANAGE))):
    gs = {g.id: g for g, _ in catalog_svc.groups(ctx.s)}
    if set(body.ids) - set(gs):
        raise _err(422, "invalid", "Unknown group in the order.")
    for i, gid in enumerate(dict.fromkeys(body.ids)):
        gs[gid].sort = i
    return {"ok": True}


@router.put("/admin/catalog/groups/{gid}/members")
def set_members(gid: str, body: OrderIn, ctx: Ctx = Depends(require(MANAGE))):
    """The group's models in display order. A model may belong to any number of groups."""
    _own_group(ctx, gid)
    known = set(ctx.s.execute(select(Model.id)).scalars())
    ids = list(dict.fromkeys(body.ids))
    if set(ids) - known:
        raise _err(422, "invalid", "Unknown model.")
    for mm in ctx.s.execute(select(ModelGroupMember).where(ModelGroupMember.group_id == gid)).scalars():
        ctx.s.delete(mm)
    ctx.s.flush()
    for i, mid in enumerate(ids):
        ctx.s.add(ModelGroupMember(group_id=gid, model_id=mid, sort=i))
    audit_ctx(ctx, "catalog.group.members", gid, {"count": len(ids)})
    return {"ok": True}


class CatalogBulk(BaseModel):
    action: str = Field(pattern="^(hide|show|pin|unpin|add_to_group|remove_from_group)$")
    ids: list[str] = Field(min_length=1, max_length=5000)
    group_id: str | None = None


@router.post("/admin/catalog/bulk")
def catalog_bulk(body: CatalogBulk, ctx: Ctx = Depends(require(MANAGE))):
    models = [m for i in dict.fromkeys(body.ids) if (m := ctx.s.get(Model, i))]
    if len(models) != len(set(body.ids)):
        raise _err(422, "invalid", "Unknown model.")
    if body.action in ("add_to_group", "remove_from_group"):
        _own_group(ctx, body.group_id or "")
        have = {mm.model_id: mm for mm in ctx.s.execute(select(ModelGroupMember)
                                                        .where(ModelGroupMember.group_id == body.group_id)).scalars()}
        n = len(have)
        for m in models:
            if body.action == "add_to_group" and m.id not in have:
                ctx.s.add(ModelGroupMember(group_id=body.group_id, model_id=m.id, sort=n))
                n += 1
            elif body.action == "remove_from_group" and m.id in have:
                ctx.s.delete(have[m.id])
    else:
        for m in models:
            if body.action in ("hide", "show"):
                m.hidden = body.action == "hide"
            else:
                m.pinned = body.action == "pin"
    audit_ctx(ctx, f"catalog.bulk.{body.action}", body.group_id or "", {"count": len(models)})
    return {"done": len(models)}


# ---- the chooser people see ------------------------------------------------------------------------------
def _visible_profile(ctx: Ctx, prof: dict, flags: dict) -> dict:
    out = {"provider": prof.get("provider"), "provider_label": prof.get("provider_label"), "icon": prof.get("icon"),
           "modality": prof.get("modality"), "description": prof.get("description")}
    if flags["model_meta"]:
        out["family"] = prof.get("family")
        out["parameters"] = prof.get("parameters")
        if flags["quantization"]:
            out["quantization"] = prof.get("quantization")
        if flags["context_size"]:
            out["context_length"] = prof.get("context_length")
    return {k: v for k, v in out.items() if v not in (None, "")}


@router.get("/models/catalog")
def chooser(operation: str = "text", ctx: Ctx = Depends(current)):
    """Models this person may use for one kind of turn, in the administrator's groups and order."""
    if operation not in catalog_svc.OPERATIONS:
        raise _err(422, "invalid", "Unknown operation.")
    mode = catalog_svc.OPERATIONS[operation][0] if operation != "vision" else "vision"
    gate = "chat" if operation == "vision" else mode
    state = services.mode_state(ctx.s, ctx.principal, ctx.st, gate)
    if not state["enabled"]:
        code = {"disabled": 403, "forbidden": 403, "no_model": 409, "unsupported": 501}.get(state.get("code"), 403)
        raise HTTPException(code, {"code": state.get("code", "forbidden"), "message": state["reason"]})
    g = lambda k: ctx.st.settings.value_for(ctx.s, k, ctx.principal)  # noqa: E731
    flags = {k: bool(g(f"ui.{k}")) for k in ("model_meta", "quantization", "context_size", "model_names")}
    usable = services.accessible_models(ctx.s, ctx.principal, ctx.st, mode, include_hidden=False)
    ids = [m.id for m, _ in usable]
    icons = _provider_icons(ctx)
    models = []
    for m, b in usable:
        prof = catalog_svc.profile_of(m, icons)
        info = (m.meta or {}).get("media") or {}
        models.append({"id": m.id, "alias": m.alias, "pinned": m.pinned, "capabilities": catalog_svc.capabilities(m),
                       "external": services.is_external(m, b), "profile": _visible_profile(ctx, prof, flags),
                       "media_modes": info.get("modes") or [], "loaded": (m.meta or {}).get("loaded")})
    groups = []
    grouped: set[str] = set()
    for grp, mids in catalog_svc.groups(ctx.s):
        mine = [mid for mid in mids if mid in ids]
        if not mine or not catalog_svc.audience_ok(grp, ctx.principal):
            continue
        groups.append({"id": grp.id, "name": grp.name, "description": grp.description, "collapsed": grp.collapsed,
                       "models": mine})
        grouped.update(mine)
    rest = [i for i in ids if i not in grouped]
    key = {"text": "chat", "code": "code", "vision": "vision", "image": "image", "video": "video"}[operation]
    default = g(f"models.default.{key}")
    if default not in ids:
        default = next((m["id"] for m in models if m["pinned"]), ids[0] if ids else None)
    return {"operation": operation, "models": models, "groups": groups, "ungrouped": rest,
            "pinned": [m["id"] for m in models if m["pinned"]], "default": default,
            "show_names": flags["model_names"]}
