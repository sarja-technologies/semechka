"""Model registry: the administrator's curated catalogue and the chooser people see.

Groups, order, pinning, hiding and icons are presentation. Access is decided only by grants (services.model_access),
so a group never lets anyone use a model and hiding a model never replaces a deny rule.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import catalog_svc, services
from ..deps import Ctx, audit_ctx, current, require, require_any
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
MANAGE = "admin.models.manage"  # enabling models and who may use them
PRESENT = "admin.models.present"  # names, order, groups, pins, icons


def _err(status, code, message):
    return HTTPException(status, {"code": code, "message": message})


def _provider_icons(ctx: Ctx) -> dict:
    return ctx.st.settings.deployment(ctx.s, "models.provider_icons") or {}


def _logos(ctx: Ctx) -> bool:
    return bool(ctx.st.settings.deployment(ctx.s, "models.developer_logos"))


def _grant_out(g: ModelGrant) -> dict:
    return {"subject_type": g.subject_type, "subject_id": g.subject_id, "effect": g.effect, "modes": g.modes}


# ---- administrator ---------------------------------------------------------------------------------------
@router.get("/admin/catalog")
def admin_catalog(ctx: Ctx = Depends(require_any(MANAGE, PRESENT))):
    full = ctx.principal.can("tech.model.id")
    backends = {b.id: b for b in ctx.s.execute(select(Backend)).scalars()}
    grants = catalog_svc.grants_by_model(ctx.s)
    icons, logos = _provider_icons(ctx), _logos(ctx)
    groups = catalog_svc.groups(ctx.s)
    member_of: dict[str, list[str]] = {}
    for g, mids in groups:
        for mid in mids:
            member_of.setdefault(mid, []).append(g.id)
    models = []
    for m in catalog_svc.ordered_models(ctx.s):
        b = backends.get(m.backend_id)
        prof = catalog_svc.profile_of(m, icons, logos)
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
            "providers": [{"id": p.id, "label": p.label, "mark": p.mark, "tone": p.tone, "icon": icons.get(p.id),
                           "logo": catalog_svc.logo_icon(p.id),
                           "logo_source": ({"owner": lg.owner, "source": lg.source, "licence": lg.licence}
                                           if (lg := catalog_svc.LOGOS.get(p.id)) else None)}
                          for p in catalog_svc.PROVIDERS],
            "developer_logos": logos,
            "grant_modes": catalog_svc.GRANT_MODES,
            "icon_note": "Developer logos are shown only where the developer's published terms allow a mark to "
                         "identify its own model; the others use lettermarks. Each mark belongs to its owner and implies "
                         "no endorsement. Turn them off under Model display, or upload a logo you are licensed to use "
                         "per developer or per model."}


class OrderIn(BaseModel):
    ids: list[str] = Field(max_length=5000)


@router.put("/admin/catalog/order")
def set_order(body: OrderIn, ctx: Ctx = Depends(require(PRESENT))):
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
def patch_entry(mid: str, body: EntryPatch, ctx: Ctx = Depends(require_any(PRESENT, MANAGE))):
    # names, pins, visibility and descriptions are presentation; the context window a model loads with is operation
    if (body.alias is not None or body.pinned is not None or body.hidden is not None or body.profile is not None):
        ctx.need(PRESENT)
    if body.defaults is not None:
        ctx.need(MANAGE)
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
    return {"ok": True, "profile": catalog_svc.profile_of(m, _provider_icons(ctx), _logos(ctx))}


@router.post("/admin/catalog/icons")
async def upload_icon(file: UploadFile = File(...), ctx: Ctx = Depends(require(PRESENT))):
    return await save_image_asset(ctx, file)


class ProviderIconIn(BaseModel):
    icon: str | None = None  # an uploaded icon id, or null to return to the lettermark


@router.put("/admin/catalog/providers/{pid}/icon")
def set_provider_icon(pid: str, body: ProviderIconIn, ctx: Ctx = Depends(require(PRESENT))):
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
def create_group(body: GroupIn, ctx: Ctx = Depends(require(PRESENT))):
    n = len(catalog_svc.groups(ctx.s))
    g = ModelGroup(name=body.name.strip(), description=body.description.strip(), collapsed=body.collapsed,
                   audience=_clean_audience(ctx, body.audience), sort=n)
    ctx.s.add(g)
    ctx.s.flush()
    audit_ctx(ctx, "catalog.group.create", g.id, {"name": g.name})
    return {"id": g.id}


@router.patch("/admin/catalog/groups/{gid}")
def update_group(gid: str, body: GroupIn, ctx: Ctx = Depends(require(PRESENT))):
    g = _own_group(ctx, gid)
    g.name, g.description, g.collapsed = body.name.strip(), body.description.strip(), body.collapsed
    g.audience = _clean_audience(ctx, body.audience)
    audit_ctx(ctx, "catalog.group.update", g.id, {"name": g.name})
    return {"ok": True}


@router.delete("/admin/catalog/groups/{gid}")
def delete_group(gid: str, ctx: Ctx = Depends(require(PRESENT))):
    """Removes the heading only. Its models stay in the catalogue and in any other group."""
    g = _own_group(ctx, gid)
    for mm in ctx.s.execute(select(ModelGroupMember).where(ModelGroupMember.group_id == gid)).scalars():
        ctx.s.delete(mm)
    ctx.s.delete(g)
    audit_ctx(ctx, "catalog.group.delete", gid, {"name": g.name})
    return {"ok": True}


@router.put("/admin/catalog/groups-order")
def order_groups(body: OrderIn, ctx: Ctx = Depends(require(PRESENT))):
    gs = {g.id: g for g, _ in catalog_svc.groups(ctx.s)}
    if set(body.ids) - set(gs):
        raise _err(422, "invalid", "Unknown group in the order.")
    for i, gid in enumerate(dict.fromkeys(body.ids)):
        gs[gid].sort = i
    audit_ctx(ctx, "catalog.groups.order", "", {"count": len(body.ids)})
    return {"ok": True}


@router.put("/admin/catalog/groups/{gid}/members")
def set_members(gid: str, body: OrderIn, ctx: Ctx = Depends(require(PRESENT))):
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
def catalog_bulk(body: CatalogBulk, ctx: Ctx = Depends(require(PRESENT))):
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


# ---- the picker people see ------------------------------------------------------------------------------
TECH_FIELDS = [  # profile/meta field -> the technical-visibility permission that reveals it
    ("family", "tech.model.family"), ("architecture", "tech.model.family"), ("parameters", "tech.model.params"),
    ("quantization", "tech.model.quant"), ("format", "tech.model.quant"), ("context_length", "tech.model.context"),
    ("license", "tech.model.license"), ("memory_mb", "tech.resources.gpu"), ("size_bytes", "tech.model.quant"),
    ("source_repo", "tech.model.id"),
]


def _row(ctx: Ctx, m: Model, b: Backend, prof: dict, opts: dict, *, restricted: bool, usable: bool) -> dict:
    p, meta = ctx.principal, m.meta or {}
    real = meta.get("engine_name") or services.display_id(m.real_id)
    names = opts["names"]
    label, sub = (real, None) if names == "real" else (m.alias, real if names == "both" and real != m.alias else None)
    tech = {"family": prof.get("family"), "architecture": meta.get("architecture"),
            "parameters": prof.get("parameters"), "quantization": prof.get("quantization"), "format": meta.get("format"),
            "context_length": prof.get("context_length"), "license": meta.get("license"),
            "memory_mb": meta.get("vram_estimate_mb"), "size_bytes": meta.get("size_bytes"),
            "source_repo": meta.get("source_repo")}
    if not opts["show_quant"]:
        tech.pop("quantization", None)
    if not opts["show_context"]:
        tech.pop("context_length", None)
    details = {k: tech[k] for k, perm in TECH_FIELDS if tech.get(k) not in (None, "") and p.can(perm)}
    if meta.get("reasoning"):
        details["reasoning"] = True
    info = meta.get("media") or {}
    row = {"id": m.id, "label": label, "sub": sub, "alias": m.alias, "pinned": m.pinned, "restricted": restricted,
           "usable": usable, "capabilities": catalog_svc.capabilities(m), "external": services.is_external(m, b),
           "provider": prof.get("provider"), "provider_label": prof.get("provider_label"), "icon": prof.get("icon"),
           "based_on": prof.get("based_on"), "modality": prof.get("modality"), "description": prof.get("description"),
           "media_modes": info.get("modes") or [], "details": details}
    if p.can("tech.model.id"):
        row["real_id"] = services.display_id(m.real_id)
    if p.can("tech.runtime.name") or opts["group_by"] == "engine":
        row["engine"] = b.name
    # private sort keys (removed before sending)
    row["sort"], row["size_b"] = m.sort, catalog_svc.size_b(prof.get("parameters"))
    row["context"] = prof.get("context_length")
    return row


@router.get("/models/catalog")
async def chooser(operation: str = "text", ctx: Ctx = Depends(current)):
    """Models this person may use for one kind of turn, arranged the way the administrator (and, where allowed, the
    person) chose. With models.view_all, installed models they may not use are listed too, marked unusable: seeing is
    not using, and every use is checked again by services.model_access."""
    if operation not in catalog_svc.OPERATIONS:
        raise _err(422, "invalid", "Unknown operation.")
    mode = catalog_svc.OPERATIONS[operation][0] if operation != "vision" else "vision"
    gate = "chat" if operation == "vision" else mode
    state = services.mode_state(ctx.s, ctx.principal, ctx.st, gate)
    if not state["enabled"]:
        code = {"disabled": 403, "forbidden": 403, "no_model": 409, "unsupported": 501}.get(state.get("code"), 403)
        raise HTTPException(code, {"code": state.get("code", "forbidden"), "message": state["reason"]})
    p = ctx.principal
    g = lambda k: ctx.st.settings.value_for(ctx.s, k, p)  # noqa: E731
    mine = (ctx.user.prefs or {}).get("picker") or {}
    opts = {"names": g("models.display.names"), "pinned_first": bool(g("models.display.pinned_first")),
            "group_by": mine.get("group_by") or g("models.display.group_by"),
            "sort": mine.get("sort") or g("models.display.sort"),
            "view": mine.get("view") or g("models.display.view"),
            "show_quant": bool(g("ui.quantization")), "show_context": bool(g("ui.context_size"))}
    usable = services.accessible_models(ctx.s, p, ctx.st, mode, include_hidden=False)
    grants = catalog_svc.grants_by_model(ctx.s)
    icons, logos = _provider_icons(ctx), _logos(ctx)
    rows = [_row(ctx, m, b, catalog_svc.profile_of(m, icons, logos), opts, usable=True,
                 restricted=services.is_restricted(ctx.s, p, m, mode, grants.get(m.id, []))) for m, b in usable]
    others = []
    if p.can("models.view_all"):  # every installed model for this operation, even ones they cannot use
        have = {r["id"] for r in rows}
        backends = {b.id: b for b in ctx.s.execute(select(Backend)).scalars()}
        need = "vision" if mode == "vision" else services.MODE_CAPABILITY[mode]
        for m in catalog_svc.ordered_models(ctx.s):
            b = backends.get(m.backend_id)
            if m.id in have or m.hidden or not m.available or b is None or need not in catalog_svc.capabilities(m):
                continue
            ok, why = services.model_access(ctx.s, p, m, b, ctx.st, mode, grants.get(m.id, []))
            r = _row(ctx, m, b, catalog_svc.profile_of(m, icons, logos), opts, usable=False, restricted=True)
            r["reason"] = why
            others.append(r)
    status_ok = p.can("models.status.view")
    if status_ok:
        from .. import lifecycle_svc
        states = {}
        for b in {b.id: b for _, b in usable}.values():
            states[b.id] = await lifecycle_svc.cached_state(ctx.st, b)
        by_id = {m.id: (m, b) for m, b in usable}
        for r in rows:
            m, b = by_id[r["id"]]
            r["state"] = lifecycle_svc.model_state(m, states.get(b.id))
    rows = catalog_svc.sort_rows(rows, opts["sort"])
    others = catalog_svc.sort_rows(others, opts["sort"])
    ids = [r["id"] for r in rows]
    favs = [x for x in (ctx.user.prefs or {}).get("favourite_models") or [] if x in ids]
    admin_groups = [{"id": grp.id, "name": grp.name, "description": grp.description, "collapsed": grp.collapsed,
                     "models": mids} for grp, mids in catalog_svc.groups(ctx.s) if catalog_svc.audience_ok(grp, p)]
    lead: list[dict] = []
    taken: set[str] = set()
    if favs:
        lead.append({"id": "favourites", "title": "Favourites", "models": favs, "collapsed": False})
        taken.update(favs)
    pinned = [r["id"] for r in rows if r["pinned"] and r["id"] not in taken]
    if opts["pinned_first"] and pinned:
        lead.append({"id": "pinned", "title": "Recommended", "models": pinned, "collapsed": False})
        taken.update(pinned)
    custom = opts["sort"] == "custom"
    secs = lead + catalog_svc.sections([r for r in rows if r["id"] not in taken], opts["group_by"], admin_groups,
                                       custom=custom)
    order = list(dict.fromkeys(x for sec in secs for x in sec["models"]))
    rank = {x: i for i, x in enumerate(order)}
    rows.sort(key=lambda r: rank.get(r["id"], len(rank)))
    key = {"text": "chat", "code": "code", "vision": "vision", "image": "image", "video": "video"}[operation]
    default = g(f"models.default.{key}")
    if default not in ids:
        default = next((r["id"] for r in rows if r["pinned"] and not r["restricted"]),
                       next((r["id"] for r in rows if not r["restricted"]), ids[0] if ids else None))
    legacy = catalog_svc.sections(rows, "groups", admin_groups, custom=custom)
    for r in rows + others:
        for k in ("sort", "size_b", "context"):
            r.pop(k, None)
    return {"operation": operation, "models": rows, "sections": secs, "unusable": others, "favourites": favs,
            "default": default, "status": status_ok, "show_names": bool(g("ui.model_names")),
            "display": {k: opts[k] for k in ("names", "group_by", "sort", "view", "pinned_first")},
            "choices": {"group_by": list(catalog_svc.GROUP_BYS), "sort": list(catalog_svc.SORTS)},
            # 0.3.0 shape, kept for custom interfaces
            "groups": [{"id": x["id"], "name": x["title"], "description": x.get("description", ""),
                        "collapsed": x["collapsed"], "models": x["models"]}
                       for x in legacy if x["id"] != "other"],
            "ungrouped": next((x["models"] for x in legacy if x["id"] == "other"), []),
            "pinned": [r["id"] for r in rows if r["pinned"]]}
