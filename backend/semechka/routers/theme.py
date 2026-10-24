"""Theme editor workflow (draft, preview, publish, revert, export, import) and capability discovery.

The published theme is ordinary settings, written in one batch, so it shows in settings history and a publish can be
reverted as a unit. The draft is a single setting only the editor reads. Contrast is enforced on publish.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import __version__, services, theme_svc
from ..deps import Ctx, audit_ctx, current, require
from ..models import SettingRevision
from ..settings_reg import BY_ID
from ..settings_svc import SettingError

router = APIRouter(prefix="/api")
EDIT = "admin.branding"


def _err(status, code, message, **kw):
    return HTTPException(status, {"code": code, "message": message, **kw})


def _published(ctx: Ctx) -> dict:
    return theme_svc.bundle_from_settings(lambda k: ctx.st.settings.deployment(ctx.s, k))


def _check(ctx: Ctx, bundle: dict, *, enforce_contrast: bool) -> dict:
    """Validate a bundle field by field with each setting's own rules. Returns {setting id: value}."""
    if not isinstance(bundle, dict) or bundle.get("schema") not in (None, "semechka.theme-bundle/1"):
        raise _err(422, "invalid", "This is not a Semechka theme bundle.")
    out, errors = {}, []
    for field, sid in theme_svc.BUNDLE_KEYS.items():
        if field not in bundle:
            continue
        v = bundle[field]
        try:
            if field == "custom" and v is not None:
                v = theme_svc.validate_custom(v, enforce_contrast=enforce_contrast)
            elif not (field == "custom" and v is None):
                v = ctx.st.settings.validate(ctx.s, sid, v)
        except SettingError as e:
            errors.append({"field": field, "error": str(e)})
            continue
        perm = BY_ID[sid].edit_perm
        if perm and not ctx.principal.can(perm):
            errors.append({"field": field, "error": "You may not change this."})
            continue
        out[sid] = v
    if out.get("brand.theme_default") == "custom" and not (out.get("theme.custom") or
                                                           ctx.st.settings.deployment(ctx.s, "theme.custom")):
        errors.append({"field": "default", "error": "Build the Custom theme before making it the default."})
    if errors:
        raise _err(422, "invalid", errors[0]["error"], errors=errors)
    return out


def _report(bundle: dict) -> dict:
    custom = bundle.get("custom") if isinstance(bundle, dict) else None
    if not isinstance(custom, dict):
        return {}
    out = {}
    for variant in ("light", "dark"):
        try:
            out[variant] = theme_svc.contrast_report(theme_svc.complete(custom.get(variant), variant))
        except SettingError as e:
            out[variant] = [{"error": str(e)}]
    return out


@router.get("/admin/theme")
def get_theme(ctx: Ctx = Depends(require(EDIT))):
    revs = ctx.s.execute(select(SettingRevision).where(SettingRevision.key.in_(list(theme_svc.BUNDLE_KEYS.values())))
                         .order_by(SettingRevision.id.desc()).limit(300)).scalars().all()
    batches: dict[str, dict] = {}
    for r in revs:
        if not r.batch:
            continue
        b = batches.setdefault(r.batch, {"batch": r.batch, "ts": r.ts, "actor": r.actor, "keys": []})
        b["keys"].append(r.key)
    draft = ctx.st.settings.deployment(ctx.s, "theme.draft")
    return {"published": _published(ctx), "draft": draft, "spec": theme_svc.spec(),
            "contrast": {"published": _report(_published(ctx)), "draft": _report(draft or {})},
            "history": list(batches.values())[:30]}


class BundleIn(BaseModel):
    bundle: dict


@router.put("/admin/theme/draft")
def save_draft(body: BundleIn, ctx: Ctx = Depends(require(EDIT))):
    """Work in progress. Contrast is reported, not enforced, so an unfinished draft can be saved."""
    _check(ctx, body.bundle, enforce_contrast=False)
    bundle = {"schema": "semechka.theme-bundle/1", **{k: v for k, v in body.bundle.items() if k in theme_svc.BUNDLE_KEYS}}
    ctx.st.settings.set(ctx.s, ctx.user.id, "theme.draft", bundle, ip=ctx.ip)
    return {"ok": True, "contrast": _report(bundle)}


@router.delete("/admin/theme/draft")
def discard_draft(ctx: Ctx = Depends(require(EDIT))):
    ctx.st.settings.set(ctx.s, ctx.user.id, "theme.draft", None, ip=ctx.ip)
    return {"ok": True}


class PublishIn(BaseModel):
    bundle: dict | None = None  # omitted: publish the saved draft


@router.post("/admin/theme/publish")
def publish(body: PublishIn, ctx: Ctx = Depends(require(EDIT))):
    bundle = body.bundle if body.bundle is not None else ctx.st.settings.deployment(ctx.s, "theme.draft")
    if not bundle:
        raise _err(409, "no_draft", "There is no draft to publish.")
    values = _check(ctx, bundle, enforce_contrast=True)
    import uuid
    batch = "theme:" + uuid.uuid4().hex[:10]
    try:
        for sid, v in values.items():
            if ctx.st.settings.deployment(ctx.s, sid) != v:
                ctx.st.settings.set(ctx.s, ctx.user.id, sid, v, batch=batch, ip=ctx.ip, confirm_weaker=True)
        ctx.st.settings.set(ctx.s, ctx.user.id, "theme.draft", None, batch=batch, ip=ctx.ip)
    except SettingError as e:
        ctx.s.rollback()
        raise _err(422, "invalid", str(e)) from e
    audit_ctx(ctx, "theme.publish", batch, {"fields": len(values)})
    return {"ok": True, "batch": batch, "published": _published(ctx)}


class RevertIn(BaseModel):
    batch: str


@router.post("/admin/theme/revert")
def revert(body: RevertIn, ctx: Ctx = Depends(require(EDIT))):
    """Undo one publish: every value it changed goes back to what it was before (as a new, revertible change)."""
    revs = ctx.s.execute(select(SettingRevision).where(SettingRevision.batch == body.batch)
                         .order_by(SettingRevision.id.desc())).scalars().all()
    revs = [r for r in revs if r.key in theme_svc.BUNDLE_KEYS.values()]
    if not revs:
        raise _err(404, "not_found", "No theme change with that id.")
    for r in revs:
        ctx.st.settings.rollback(ctx.s, ctx.user.id, r.id, ctx.ip)
    audit_ctx(ctx, "theme.revert", body.batch, {"fields": len(revs)})
    return {"ok": True, "published": _published(ctx)}


@router.get("/admin/theme/export")
def export_theme(ctx: Ctx = Depends(require(EDIT)), source: str = "published"):
    if source == "draft":
        d = ctx.st.settings.deployment(ctx.s, "theme.draft")
        if not d:
            raise _err(404, "no_draft", "There is no draft.")
        return d
    return _published(ctx)


@router.post("/admin/theme/import")
def import_theme(body: BundleIn, ctx: Ctx = Depends(require(EDIT))):
    """An imported bundle becomes the draft: preview it, then publish. Nothing goes live on import."""
    return save_draft(body, ctx)


class ContrastIn(BaseModel):
    values: dict


@router.post("/admin/theme/contrast")
def contrast(body: ContrastIn, variant: str = "light", ctx: Ctx = Depends(require(EDIT))):
    if variant not in ("light", "dark"):
        raise _err(422, "invalid", "variant is light or dark.")
    try:
        return theme_svc.contrast_report(theme_svc.complete(body.values, variant))
    except SettingError as e:
        raise _err(422, "invalid", str(e)) from e


@router.get("/theme/tokens")
def tokens(ctx: Ctx = Depends(current)):
    """The design-token contract every interface (including a replacement frontend) themes against."""
    return theme_svc.spec()


@router.get("/capabilities")
def capabilities(ctx: Ctx = Depends(current)):
    """What this server offers and what this person may do, for interfaces that adapt instead of assuming."""
    from .me import ui_config
    ui = ui_config(ctx.s, ctx.st, ctx.principal, prefs=ctx.user.prefs or {})
    return {"server": {"product": "semechka", "version": __version__, "api": "semechka/0.3"},
            "operations": ui["operations"], "ui": ui["ui"], "composer": ui["composer"],
            "pages": [p["id"] for p in ui["pages"]],
            "features": {"branching": True, "folders": ui["ui"]["folders"], "attachments": ui["composer"]["attachments"],
                         "no_save_chats": ui["composer"]["ephemeral"], "mixed_media_threads": True,
                         "admin_content_access": ctx.st.settings.deployment(ctx.s, "privacy.admin_content_access"),
                         "mfa_policy": services.mfa_policy(ctx.s, ctx.st)},
            "theme": {"tokens": "/api/theme/tokens", "active": ui["theme"]["active"], "choices": ui["theme"]["choices"]},
            "extension_points": ["theme tokens", "custom CSS (chat interface only, bounded)", "replacement frontend via "
                                 "SEMECHKA_WEB_DIR", "OpenAPI schema at /api/openapi.json (when SEMECHKA_API_DOCS=1)"]}
