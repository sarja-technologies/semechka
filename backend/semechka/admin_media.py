"""Unified administrator explorer: every conversation and every generated or stored media item of this instance.

This module is the service behind routers/admin_media.py and the richer conversation view in routers/admin_content.py. It adds no
storage and no copies of files: it reads the existing tables (conversations, messages, media_jobs, media_assets, chat_attachments) and
streams bytes through the existing encrypted stores (media_lib / media_store / attachments_svc).

Governance is the one of admin_content.py and is not weakened here:
  * off unless the deployment policy ``privacy.admin_content_access`` is 'audited' (``gate``);
  * the sensitive permission ``admin.content.read`` by name (the router dependency);
  * every list, open, preview and download needs a stated reason and writes one audit row without any content in it;
  * what an owner can learn about it is listed by GET /api/me/content-access (``detail.owner`` / ``detail.owners``).
Scope (``Scope``): without ``admin.content.instance`` an administrator reaches only people who are active members of an organisation
the administrator is an active member of (the tenant boundary of authz.py). Anyone else answers 404, like a missing id.
Tenants of OTHER Semechka instances are not reachable at all: this reads this instance's database and data directory only.
"""
from __future__ import annotations

import base64
import heapq
import hmac
import json
import math
import re
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import and_, case, exists, false, func, or_, select, true

from . import attachments_svc, audit, media_lib, media_svc
from .media_models import MediaAsset, MediaJob
from .models import Backend, ChatAttachment, Conversation, Message, Model, User
from .models_tenancy import OrgMember

SCAN_CAP = 1500          # rows examined by one list call; a call that reaches it returns a cursor to continue from
BATCH = 100
MAX_CONV_PAGE = 100
MAX_MEDIA_PAGE = 60
TICKET_TTL = 600         # seconds a preview/download link stays valid (video players need several range requests)
THUMB_ORIGINAL_MAX = 6 * 2**20
RATE_OPS = (240, 60.0)   # list/open calls per administrator per minute
RATE_BLOBS = (1500, 60.0)
JOB_STATUSES = ("queued", "running", "completed", "failed", "cancelled")
STATUS_FILTERS = (*JOB_STATUSES, "expired")
JOB_WORKFLOWS = ("chat_image", "chat_video", "imajutsu", "openjutsu", "image_generation", "video_generation")
ASSET_WORKFLOWS = tuple(media_lib.ORIGINS)  # upload, then the derived copies (edit, prepared, export, adjusted, scaled, composited)
WORKFLOWS = (*JOB_WORKFLOWS, *ASSET_WORKFLOWS)
LOOSE_MODELS = ("upload", "edit", "prepared", "export", "adjusted", "scaled", "composited")
_ID = re.compile(r"^[A-Za-z0-9_]{1,40}$")
_SECRET_KEY = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|apikey|authorization|bearer|credential|cookie|private[_-]?key|signature|session)", re.IGNORECASE)
_SECRET_VAL = re.compile(r"(Bearer\s+[A-Za-z0-9._~+/=-]{8,}|\bsk-[A-Za-z0-9_-]{16,}|\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]*"
                         r"|(?:api[_-]?key|token|secret|password)\s*[=:]\s*\S+)", re.IGNORECASE)
PARAM_KEYS = ("negative_prompt", "width", "height", "steps", "guidance", "seed", "scheduler", "frames", "fps", "denoise", "operation", "mode",
              "batch_size", "strength", "duration", "short_edge", "subject_kind", "clip_seconds", "model")
BLOCK_KEYS = ("version", "project_id", "mode", "operation", "engine_mode", "model", "batch", "requested_output", "selection", "adjustments",
              "subject", "prepared_asset", "source_asset", "mask_asset", "references")


# ---- governance ---------------------------------------------------------------------------------------
def gate(ctx) -> None:
    """403 'disabled' unless the deployment policy allows audited administrator access (the same message as admin_content)."""
    if ctx.st.settings.deployment(ctx.s, "privacy.admin_content_access") != "audited":
        raise HTTPException(403, {"code": "disabled", "message": "Your organisation's privacy policy does not allow "
                                                                 "administrators to read conversations."})


def throttle(ctx, limits=None, bucket: str = "ops") -> None:
    limits = limits or RATE_OPS
    if not ctx.st.limiter.hit(f"adminx:{bucket}:{ctx.user.id}", int(limits[0]), limits[1]):
        raise HTTPException(429, {"code": "rate_limited", "message": "Too many requests. Wait a moment and try again."})


def begin(ctx) -> Scope:
    """Policy gate, rate limit, scope. Called first by every explorer endpoint (the permission is the router dependency)."""
    gate(ctx)
    throttle(ctx)
    return scope_for(ctx)


@dataclass
class Scope:
    instance: bool
    org_ids: tuple[str, ...]

    @property
    def label(self) -> str:
        return "instance" if self.instance else "organisation"

    def users(self, col):
        """SQL condition: rows whose owner column is inside the administrator's reach."""
        if self.instance:
            return true()
        if not self.org_ids:
            return false()
        return col.in_(select(OrgMember.user_id).where(OrgMember.org_id.in_(self.org_ids), OrgMember.status == "active"))

    def allows(self, s, user_id: str) -> bool:
        if self.instance:
            return True
        if not self.org_ids:
            return False
        return s.execute(select(OrgMember.user_id).where(OrgMember.user_id == user_id, OrgMember.org_id.in_(self.org_ids),
                                                         OrgMember.status == "active")).first() is not None


def scope_for(ctx) -> Scope:
    if ctx.principal.can("admin.content.instance"):
        return Scope(True, ())
    from .authz import Authz
    az = Authz(ctx.s, ctx.user.id)
    mine = ctx.s.execute(select(OrgMember.org_id).where(OrgMember.user_id == ctx.user.id, OrgMember.status == "active")).scalars().all()
    return Scope(False, tuple(o for o in mine if az.membership(o) is not None))


def owner_org(s, user_id: str) -> str | None:
    """The one organisation a person belongs to (so the organisation's audit log shows the access), else None."""
    rows = s.execute(select(OrgMember.org_id).where(OrgMember.user_id == user_id, OrgMember.status == "active")).scalars().all()
    return rows[0] if len(rows) == 1 else None


def record(ctx, action: str, target: str, detail: dict, owner_id: str | None = None) -> None:
    """One audit row. ``detail`` never carries content: only ids, counts, filters that name no text, and the stated reason."""
    audit.record(ctx.s, ctx.user.id, action, target, detail, ctx.ip, org_id=owner_org(ctx.s, owner_id) if owner_id else None)


def not_found(what: str = "Not found.") -> HTTPException:
    return HTTPException(404, {"code": "not_found", "message": what})


# ---- cursors and keyset scans -------------------------------------------------------------------------
def parse_cursor(c: str | None) -> tuple[float, str] | None:
    if not c:
        return None
    ts, _, i = c.partition(":")
    try:
        t = float(ts)
    except ValueError:
        raise HTTPException(422, {"code": "invalid", "message": "Unknown cursor."}) from None
    if not _ID.match(i or "") or math.isnan(t):
        raise HTTPException(422, {"code": "invalid", "message": "Unknown cursor."})
    return t, i


def make_cursor(ts: float, i: str) -> str:
    return f"{ts!r}:{i}"


def keyset(s, base, model, ts_attr: str, after):
    """Yield ORM rows newest first by (ts, id), re-querying in small batches so a long scan never holds a whole table."""
    ts_col, id_col = getattr(model, ts_attr), model.id
    last = after
    while True:
        q = base
        if last:
            q = q.where(or_(ts_col < last[0], and_(ts_col == last[0], id_col < last[1])))
        rows = s.execute(q.order_by(ts_col.desc(), id_col.desc()).limit(BATCH)).scalars().all()
        yield from rows
        if len(rows) < BATCH:
            return
        last = (getattr(rows[-1], ts_attr), rows[-1].id)


# ---- small helpers ------------------------------------------------------------------------------------
def scrub_text(v) -> str:
    return _SECRET_VAL.sub("<removed>", " ".join(str(v or "").split()))[:400]


def plain(v, depth: int = 0):
    """A JSON-safe, bounded copy of a settings value: scalars, short lists, small dicts; no key that looks like a credential."""
    if isinstance(v, bool) or v is None or isinstance(v, (int, float)):
        return v
    if isinstance(v, str):
        return _SECRET_VAL.sub("<removed>", v)[:600]
    if depth >= 4:
        return None
    if isinstance(v, (list, tuple)):
        return [plain(x, depth + 1) for x in list(v)[:32]]
    if isinstance(v, dict):
        return {str(k)[:60]: plain(x, depth + 1) for k, x in list(v.items())[:40] if not _SECRET_KEY.search(str(k))}
    return None


def users_by_id(s, ids) -> dict[str, dict]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: {"id": u.id, "display_name": u.display_name, "email": u.email, "status": u.status}
            for u in s.execute(select(User).where(User.id.in_(ids))).scalars()}


def _owner(users: dict, uid: str) -> dict:
    return users.get(uid) or {"id": uid, "display_name": "(deleted person)", "email": None, "status": "deleted"}


def decrypt(st, token: str | None, purpose: str) -> str:
    if not token:
        return ""
    try:
        return st.vault.decrypt(token, purpose)
    except (ValueError, TypeError, KeyError):
        return ""


def decrypt_json(st, token: str | None, purpose: str = "media") -> dict:
    if not token:
        return {}
    try:
        d = json.loads(st.vault.decrypt(token, purpose))
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def _models(s) -> dict[str, Model]:
    return {m.id: m for m in s.execute(select(Model)).scalars()}


def _model_ref(models: dict, mid: str | None) -> dict | None:
    if not mid:
        return None
    m = models.get(mid)
    return {"id": mid, "alias": m.alias if m else mid}


def _has_profile(m: Model | None) -> bool:
    return bool(m and media_svc.profile_info(m))


# ---- preview/download tickets -------------------------------------------------------------------------
def make_ticket(st, admin_id: str, typ: str, rid: str, mode: str, ttl: int = TICKET_TTL) -> str:
    body = f"{admin_id}|{typ}|{rid}|{mode}|{int(time.time()) + ttl}"
    raw = base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")
    return f"{raw}.{st.vault.mac(raw, 'admin-media-ticket')[:32]}"


def read_ticket(st, token: str, admin_id: str) -> tuple[str, str, str] | None:
    """-> (type, id, mode) for a ticket issued to THIS administrator and not yet expired, else None."""
    raw, _, mac = (token or "").partition(".")
    if not raw or not mac or not hmac.compare_digest(mac, st.vault.mac(raw, "admin-media-ticket")[:32]):
        return None
    try:
        who, typ, rid, mode, exp = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)).decode().split("|")
    except (ValueError, UnicodeDecodeError):
        return None
    if who != admin_id or float(exp) < time.time() or typ not in ("asset", "attachment") or mode not in ("thumb", "view", "download") or not _ID.match(rid):
        return None
    return typ, rid, mode


def blob_url(st, admin_id: str, typ: str, rid: str, mode: str) -> str:
    return f"/api/admin/content/explorer/blob/{make_ticket(st, admin_id, typ, rid, mode)}"


# ---- storage checks (never leave the instance's data directory) ---------------------------------------
def _inside(root: Path, p: Path) -> bool:
    try:
        return p.resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def asset_exists(st, a: MediaAsset) -> bool:
    if not _ID.match(a.id or "") or not _ID.match(a.user_id or ""):
        return False
    p = media_svc.asset_path(st.cfg, a.user_id, a.id)
    return _inside(media_svc.assets_dir(st.cfg), p) and p.is_file()


def attachment_exists(st, a: ChatAttachment) -> bool:
    if not _ID.match(a.id or "") or not _ID.match(a.user_id or ""):
        return False
    p = attachments_svc.path_for(st.cfg, a.user_id, a.id)
    return _inside(st.cfg.data_dir / "attachments", p) and p.is_file()


def _expired(row, now: float) -> bool:
    return bool(row.expires_at and row.expires_at < now)


def _thumbable(st, a, kind: str, mime: str, size: int, sidecar: bool) -> bool:
    return sidecar or (kind == "image" and mime in ("image/png", "image/jpeg", "image/webp", "image/gif") and size <= THUMB_ORIGINAL_MAX)


def asset_summary(st, a: MediaAsset, admin_id: str, now: float, *, thumbs: bool, meta: dict | None = None) -> dict:
    expired = _expired(a, now)
    avail = (not expired) and asset_exists(st, a)
    meta = meta if meta is not None else (media_lib.meta_of(st, a) if a.model_id in LOOSE_MODELS else {})
    sidecar = avail and media_lib.asset_file(st, a.user_id, a.id, "thumb").is_file()
    out = {"id": a.id, "type": "asset", "kind": a.kind, "mime": a.mime, "size": a.size, "width": a.width, "height": a.height, "duration": a.duration,
           "seed": a.seed, "created_at": a.created_at, "expires_at": a.expires_at, "origin": meta.get("origin") or "generated",
           "state": "expired" if expired else ("available" if avail else "missing"), "available": avail, "job_id": a.job_id}
    if thumbs and avail and _thumbable(st, a, a.kind, a.mime, a.size, sidecar):
        out["thumb_url"] = blob_url(st, admin_id, "asset", a.id, "thumb")
    return out


def attachment_summary(st, a: ChatAttachment, admin_id: str, now: float, *, thumbs: bool, name: bool = True) -> dict:
    expired = _expired(a, now)
    avail = (not expired) and attachment_exists(st, a)
    out = {"id": a.id, "type": "attachment", "kind": a.kind, "mime": a.mime, "size": a.size, "width": a.width, "height": a.height,
           "created_at": a.created_at, "expires_at": a.expires_at, "conversation_id": a.conversation_id,
           "state": "expired" if expired else ("available" if avail else "missing"), "available": avail}
    if name:
        out["name"] = decrypt(st, a.name_enc, "attachment-name")
    if thumbs and avail and _thumbable(st, a, a.kind, a.mime, a.size, False):
        out["thumb_url"] = blob_url(st, admin_id, "attachment", a.id, "thumb")
    return out


# ---- classification -----------------------------------------------------------------------------------
def classify_job(j: MediaJob, model: Model | None, params: dict) -> tuple[str, str]:
    """-> (workflow, operation)."""
    if isinstance(params.get("imajutsu"), dict):
        return "imajutsu", str(params["imajutsu"].get("operation") or params["imajutsu"].get("engine_mode") or "generate")
    if isinstance(params.get("studio"), dict):
        return "openjutsu", str(params["studio"].get("operation") or "studio")
    if _has_profile(model):
        return "openjutsu", str(params.get("operation") or "run")
    if j.conversation_id:
        return f"chat_{j.kind}", "generate"
    return f"{j.kind}_generation", str(params.get("operation") or "generate")


def display_status(row, now: float, active: tuple = media_svc.ACTIVE) -> str:
    if row.expires_at and row.expires_at < now and getattr(row, "status", "completed") not in active:
        return "expired"
    return getattr(row, "status", "completed")


def safe_params(st, model: Model | None, params: dict) -> dict:
    """Generation parameters an administrator may see: known generation keys plus the inputs the model's profile declares. Nothing else."""
    allow = set(PARAM_KEYS)
    prof = media_svc.profile_info(model) if model else None
    for k, spec in ((prof or {}).get("inputs_schema") or {}).items():
        if isinstance(spec, dict) and spec.get("type") != "file":
            allow.add(k)
    allow.discard("prompt")
    return {k: plain(v) for k, v in params.items() if k in allow and not _SECRET_KEY.search(k)}


def workflow_block(params: dict) -> tuple[str | None, dict]:
    for name in ("imajutsu", "studio"):
        b = params.get(name)
        if isinstance(b, dict):
            return name, {k: plain(b[k]) for k in BLOCK_KEYS if k in b}
    return None, {}


def input_refs(params: dict, message_meta: dict | None) -> list[tuple[str, str]]:
    """(role, asset or attachment id) of everything handed to the engine as an input: source, mask, references, prepared video."""
    refs: list[tuple[str, str]] = []

    def add(role: str, v):
        if isinstance(v, str) and _ID.match(v):
            refs.append((role, v))

    name, _b = workflow_block(params)
    block = params.get(name) if name else None
    if isinstance(block, dict):
        add("source", block.get("source_asset"))
        add("mask", block.get("mask_asset"))
        add("prepared", block.get("prepared_asset"))
        for r in block.get("references") or []:
            if isinstance(r, dict):
                add("reference", r.get("asset"))
    for k, v in (params.get("sources") or {}).items() if isinstance(params.get("sources"), dict) else []:
        for x in v if isinstance(v, list) else [v]:
            add({"video": "source", "refs": "reference"}.get(k, str(k)[:24]), x)
    if message_meta:
        add("source", message_meta.get("source"))
        for x in message_meta.get("attachments") or []:
            add("attachment", x)
    seen, out = set(), []
    for role, i in refs:
        if i not in seen:
            seen.add(i)
            out.append((role, i))
    return out


def resolve_refs(st, s, owner_id: str, refs: list[tuple[str, str]], admin_id: str, now: float, *, thumbs: bool) -> list[dict]:
    """Inputs as summaries. Only the job owner's own rows resolve: an id naming anyone else's file is reported as not retained."""
    aids = [i for _, i in refs if i.startswith("ma_")]
    tids = [i for _, i in refs if i.startswith("at_")]
    assets = {a.id: a for a in s.execute(select(MediaAsset).where(MediaAsset.id.in_(aids), MediaAsset.user_id == owner_id)).scalars()} if aids else {}
    atts = {a.id: a for a in s.execute(select(ChatAttachment).where(ChatAttachment.id.in_(tids), ChatAttachment.user_id == owner_id)).scalars()} if tids else {}
    out = []
    for role, i in refs:
        if i in assets:
            d = asset_summary(st, assets[i], admin_id, now, thumbs=thumbs)
        elif i in atts:
            d = attachment_summary(st, atts[i], admin_id, now, thumbs=thumbs)
        else:
            d = {"id": i, "type": "asset" if i.startswith("ma_") else "attachment" if i.startswith("at_") else "unknown", "state": "missing",
                 "available": False, "note": "Not retained, or a no-save item that was never stored."}
        out.append({"role": role, **d})
    return out


# ---- jobs ---------------------------------------------------------------------------------------------
def _job_entry(st, j: MediaJob, ctx_maps: dict, outputs: list[dict], now: float, params: dict) -> dict:
    model = ctx_maps["models"].get(j.model_id)
    wf, op = classify_job(j, model, params)
    status = display_status(j, now)
    end = j.finished_at or now
    elapsed = max(0.0, end - (j.started_at or j.created_at)) if j.status not in media_svc.ACTIVE or j.started_at else None
    return {"entry": "job", "id": j.id, "kind": j.kind, "workflow": wf, "operation": op, "status": status, "raw_status": j.status,
            "error": scrub_text(j.error) if j.error else None, "progress": j.progress, "cancel_requested": j.cancel_requested,
            "model": _model_ref(ctx_maps["models"], j.model_id), "backend": (ctx_maps["backends"].get(j.backend_id) or {}).get("name"),
            "owner": _owner(ctx_maps["users"], j.user_id), "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at,
            "duration_s": round(elapsed, 1) if elapsed is not None else None, "expires_at": j.expires_at,
            "retained": bool(j.save and status != "expired"), "saved": bool(j.save), "conversation_id": j.conversation_id, "message_id": j.message_id,
            "outputs": outputs}


def _maps(s, user_ids) -> dict:
    return {"models": _models(s), "backends": {b.id: {"name": b.name} for b in s.execute(select(Backend)).scalars()},
            "users": users_by_id(s, user_ids)}


def job_entries(st, s, jobs: list[MediaJob], admin_id: str, *, thumbs: bool = True, with_outputs: bool = True) -> list[dict]:
    """List entries for jobs (metadata only: no prompt, no parameters)."""
    if not jobs:
        return []
    now = time.time()
    maps = _maps(s, {j.user_id for j in jobs})
    outs: dict[str, list[dict]] = {}
    if with_outputs:
        for a in s.execute(select(MediaAsset).where(MediaAsset.job_id.in_([j.id for j in jobs])).order_by(MediaAsset.created_at)).scalars():
            outs.setdefault(a.job_id, []).append(asset_summary(st, a, admin_id, now, thumbs=thumbs, meta={}))
    return [_job_entry(st, j, maps, outs.get(j.id, []), now, decrypt_json(st, j.params_enc) if j.params_enc and not _expired(j, now) else {}) for j in jobs]


def asset_entry(st, a: MediaAsset, users: dict, admin_id: str, now: float, *, thumbs: bool = True) -> dict:
    meta = media_lib.meta_of(st, a)
    summ = asset_summary(st, a, admin_id, now, thumbs=thumbs, meta=meta)
    origin = meta.get("origin") or "upload"
    return {"entry": "asset", "id": a.id, "kind": a.kind, "workflow": origin, "operation": origin, "status": "expired" if summ["state"] == "expired" else
            "completed" if summ["available"] else "missing", "raw_status": None, "error": None, "model": {"id": a.model_id, "alias": a.model_id},
            "backend": None, "owner": _owner(users, a.user_id), "created_at": a.created_at, "started_at": None, "finished_at": a.created_at,
            "duration_s": None, "expires_at": a.expires_at, "retained": summ["state"] != "expired", "saved": True, "conversation_id": None,
            "message_id": None, "outputs": [summ]}


class Filters:
    """Validated filter values shared by both lists (pydantic models in the router parse the request, then hand over a plain object)."""

    def __init__(self, f):
        self.user_id = f.user_id
        self.model_id = f.model_id
        self.kind = f.kind
        self.workflow = f.workflow
        self.status = f.status
        self.since = f.since
        self.until = f.until
        self.conversation_id = getattr(f, "conversation_id", None)
        self.q = (getattr(f, "q", None) or "").strip().lower()
        self.mode = getattr(f, "mode", None)
        self.media = getattr(f, "media", None)

    def audit(self) -> dict:
        """The filters worth recording: identifiers and enumerations only, never the free-text query (only that one was used)."""
        d = {k: v for k, v in {"user_id": self.user_id, "model_id": self.model_id, "kind": self.kind, "workflow": self.workflow, "status": self.status,
                               "since": self.since, "until": self.until, "conversation_id": self.conversation_id, "mode": self.mode,
                               "media": self.media}.items() if v not in (None, "")}
        if self.q:
            d["text_query"] = True
        return d


def _job_query(scope: Scope, f: Filters, now: float):
    q = select(MediaJob).where(scope.users(MediaJob.user_id))
    for col, v in ((MediaJob.user_id, f.user_id), (MediaJob.model_id, f.model_id), (MediaJob.kind, f.kind), (MediaJob.conversation_id, f.conversation_id)):
        if v:
            q = q.where(col == v)
    if f.since is not None:
        q = q.where(MediaJob.created_at >= f.since)
    if f.until is not None:
        q = q.where(MediaJob.created_at < f.until)
    if f.status == "expired":
        q = q.where(MediaJob.expires_at != None, MediaJob.expires_at < now, MediaJob.status.notin_(media_svc.ACTIVE))
    elif f.status:
        q = q.where(MediaJob.status == f.status, or_(MediaJob.expires_at == None, MediaJob.expires_at >= now, MediaJob.status.in_(media_svc.ACTIVE)))
    w = f.workflow
    if w in ("chat_image", "chat_video"):
        q = q.where(MediaJob.conversation_id != None, MediaJob.kind == w[5:])
    elif w in ("image_generation", "video_generation"):
        q = q.where(MediaJob.kind == w.split("_")[0], MediaJob.conversation_id == None)
    elif w == "imajutsu":
        q = q.where(MediaJob.kind == "image")
    elif w == "openjutsu":
        q = q.where(MediaJob.kind == "video")
    return q


def _asset_query(scope: Scope, f: Filters, now: float):
    q = select(MediaAsset).where(scope.users(MediaAsset.user_id), MediaAsset.job_id == None, MediaAsset.model_id.in_(LOOSE_MODELS))
    for col, v in ((MediaAsset.user_id, f.user_id), (MediaAsset.kind, f.kind)):
        if v:
            q = q.where(col == v)
    if f.model_id:
        q = q.where(MediaAsset.model_id == f.model_id)
    if f.since is not None:
        q = q.where(MediaAsset.created_at >= f.since)
    if f.until is not None:
        q = q.where(MediaAsset.created_at < f.until)
    if f.status == "expired":
        q = q.where(MediaAsset.expires_at != None, MediaAsset.expires_at < now)
    elif f.status:
        q = q.where(or_(MediaAsset.expires_at == None, MediaAsset.expires_at >= now))
    if f.workflow in ASSET_WORKFLOWS:
        q = q.where(MediaAsset.model_id == f.workflow)
    return q


def list_media(st, s, scope: Scope, f: Filters, admin_id: str, cursor, limit: int) -> dict:
    """One page of the central media library: generation jobs (every status, including failed and cancelled) merged with stored items that
    no job produced (uploads and edited copies), newest first. Python-side filters (workflow, operation) mean a call examines at most
    SCAN_CAP rows; when it stops early it says so and returns the cursor to continue."""
    now = time.time()
    limit = max(1, min(limit, MAX_MEDIA_PAGE))
    if f.status and f.status not in STATUS_FILTERS:
        raise HTTPException(422, {"code": "invalid", "message": "Unknown status."})
    if f.workflow and f.workflow not in WORKFLOWS:
        raise HTTPException(422, {"code": "invalid", "message": "Unknown workflow."})
    if f.kind not in (None, "image", "video"):
        raise HTTPException(422, {"code": "invalid", "message": "kind must be image or video."})
    streams = []
    if f.workflow is None or f.workflow in JOB_WORKFLOWS:
        streams.append(keyset(s, _job_query(scope, f, now), MediaJob, "created_at", cursor))
    if (f.workflow is None or f.workflow in ASSET_WORKFLOWS) and not f.conversation_id and f.status not in ("queued", "running", "failed", "cancelled"):
        streams.append(keyset(s, _asset_query(scope, f, now), MediaAsset, "created_at", cursor))
    maps = {"models": _models(s)}
    picked: list = []
    scanned = 0
    last = None
    truncated = False
    for row in heapq.merge(*streams, key=lambda r: (r.created_at, r.id), reverse=True):
        scanned += 1
        last = row
        if isinstance(row, MediaJob):
            params = decrypt_json(st, row.params_enc) if (row.params_enc and not _expired(row, now)) else {}
            wf, _op = classify_job(row, maps["models"].get(row.model_id), params)
            if f.workflow and wf != f.workflow:
                pass
            else:
                picked.append(row)
        else:
            if f.workflow and f.workflow != (media_lib.meta_of(st, row).get("origin") or "upload"):
                pass
            else:
                picked.append(row)
        if len(picked) >= limit:
            break
        if scanned >= SCAN_CAP:
            truncated = True
            break
    more = len(picked) >= limit or truncated
    if more and last is not None:
        nxt = make_cursor(last.created_at, last.id)
    else:
        nxt = None
    jobs = [r for r in picked if isinstance(r, MediaJob)]
    job_out = {e["id"]: e for e in job_entries(st, s, jobs, admin_id)}
    users = users_by_id(s, {r.user_id for r in picked if isinstance(r, MediaAsset)})
    items = [job_out[r.id] if isinstance(r, MediaJob) else asset_entry(st, r, users, admin_id, now) for r in picked]
    return {"items": items, "next_cursor": nxt, "scanned": scanned, "truncated": truncated, "limit": limit, "scope": scope.label,
            "owners": sorted({i["owner"]["id"] for i in items})}


def job_detail(st, s, scope: Scope, jid: str, admin_id: str) -> dict:
    if not _ID.match(jid):
        raise not_found()
    j = s.get(MediaJob, jid)
    if j is None or not scope.allows(s, j.user_id):
        raise not_found()
    now = time.time()
    [entry] = job_entries(st, s, [j], admin_id)
    expired = entry["status"] == "expired"
    model = s.get(Model, j.model_id)
    params = decrypt_json(st, j.params_enc) if j.params_enc and not expired else {}
    prompt = decrypt(st, j.prompt_enc, "media") if j.prompt_enc and not expired else ""
    msg = s.get(Message, j.message_id) if j.message_id else None
    wf_name, block = workflow_block(params)
    refs = input_refs(params, msg.meta if msg is not None and msg.conversation_id == j.conversation_id else None)
    out_ids = [o["id"] for o in entry["outputs"]]
    derived = []
    if out_ids:
        for a in s.execute(select(MediaAsset).where(MediaAsset.user_id == j.user_id, MediaAsset.model_id.in_(("export", "adjusted", "scaled", "composited", "edit")))).scalars():
            meta = media_lib.meta_of(st, a)
            if meta.get("parent") in out_ids:
                derived.append({**asset_summary(st, a, admin_id, now, thumbs=True, meta=meta), "parent": meta.get("parent")})
    note = None
    if expired:
        note = "Past its retention period: the prompt, settings and files are no longer available."
    elif not j.save:
        note = "A no-save generation: the prompt, settings and result were never stored."
    inputs = resolve_refs(st, s, j.user_id, refs, admin_id, now, thumbs=True)
    inputs_note = None
    if not inputs and not expired and entry["workflow"] in ("openjutsu", "imajutsu"):
        inputs_note = "No input files are recorded for this run. Files uploaded straight with a request are handed to the engine and not kept; only library files are listed."
    return {**entry, "prompt": prompt, "params": safe_params(st, model, params), "workflow_detail": {"source": wf_name, **block} if wf_name else None,
            "inputs": inputs, "inputs_note": inputs_note, "derived": derived, "note": note,
            "progress_text": scrub_text(j.progress_text)}


def asset_detail(st, s, scope: Scope, aid: str, admin_id: str) -> dict:
    if not _ID.match(aid):
        raise not_found()
    a = s.get(MediaAsset, aid)
    if a is None or not scope.allows(s, a.user_id):
        raise not_found()
    now = time.time()
    meta = media_lib.meta_of(st, a) if a.model_id in LOOSE_MODELS else {}
    users = users_by_id(s, {a.user_id})
    if a.job_id and s.get(MediaJob, a.job_id) is not None:
        d = job_detail(st, s, scope, a.job_id, admin_id)
        return {**d, "focus": a.id}
    e = asset_entry(st, a, users, admin_id, now)
    orig = None
    if meta.get("parent") and isinstance(meta["parent"], str) and _ID.match(meta["parent"]):
        p = s.get(MediaAsset, meta["parent"])
        if p is not None and p.user_id == a.user_id:
            orig = asset_summary(st, p, admin_id, now, thumbs=True)
    expired = e["status"] == "expired"
    info = {k: plain(meta[k]) for k in ("origin", "edit", "media", "export", "imajutsu", "prep") if k in meta and not expired}
    return {**e, "name": media_lib.display_name(meta, a.kind, a.id) if not expired else None, "prompt": "", "params": info, "workflow_detail": None,
            "inputs": [], "original": orig, "derived": [], "note": "Past its retention period; the file is no longer available." if expired else None}


# ---- conversations ----------------------------------------------------------------------------------
def list_conversations(st, s, scope: Scope, f: Filters, cursor, limit: int) -> dict:
    """Search conversations of every person in scope. Titles are encrypted at rest, so the text query decrypts the titles of the rows it
    examines (never message bodies) and stops at SCAN_CAP rows; it reports when it did."""
    now = time.time()
    limit = max(1, min(limit, MAX_CONV_PAGE))
    if f.status not in (None, "active", "archived", "expired"):
        raise HTTPException(422, {"code": "invalid", "message": "Unknown status."})
    q = select(Conversation).where(scope.users(Conversation.user_id))
    if f.user_id:
        q = q.where(Conversation.user_id == f.user_id)
    if f.mode:
        q = q.where(Conversation.mode == f.mode)
    if f.model_id:
        q = q.where(or_(Conversation.model_id == f.model_id, exists(select(1).where(Message.conversation_id == Conversation.id, Message.model_id == f.model_id))))
    if f.since is not None:
        q = q.where(Conversation.updated_at >= f.since)
    if f.until is not None:
        q = q.where(Conversation.updated_at < f.until)
    if f.status == "expired":
        q = q.where(Conversation.expires_at != None, Conversation.expires_at < now)
    elif f.status:
        q = q.where(or_(Conversation.expires_at == None, Conversation.expires_at >= now), Conversation.archived == (f.status == "archived"))
    if f.media in ("image", "video"):
        q = q.where(exists(select(1).where(Message.conversation_id == Conversation.id, Message.kind == f.media)))
    elif f.media == "any":
        q = q.where(exists(select(1).where(Message.conversation_id == Conversation.id, Message.kind.in_(("image", "video")))))
    elif f.media not in (None, ""):
        raise HTTPException(422, {"code": "invalid", "message": "media must be image, video or any."})
    picked, titles = [], {}
    scanned, last, truncated = 0, None, False
    for c in keyset(s, q, Conversation, "updated_at", cursor):
        scanned += 1
        last = c
        if f.q:
            t = decrypt(st, c.title_enc, "title")
            if f.q not in t.lower():
                if scanned >= SCAN_CAP:
                    truncated = True
                    break
                continue
            titles[c.id] = t
        picked.append(c)
        if len(picked) >= limit:
            break
        if scanned >= SCAN_CAP:
            truncated = True
            break
    nxt = make_cursor(last.updated_at, last.id) if last is not None and (len(picked) >= limit or truncated) else None
    ids = [c.id for c in picked]
    counts: dict[str, tuple] = {}
    used: dict[str, set] = {}
    if ids:
        for cid, n, media, errs in s.execute(select(Message.conversation_id, func.count(), func.sum(case((Message.kind.in_(("image", "video")), 1), else_=0)),
                                                     func.sum(case((Message.status == "error", 1), else_=0)))
                                             .where(Message.conversation_id.in_(ids)).group_by(Message.conversation_id)):
            counts[cid] = (n, int(media or 0), int(errs or 0))
        for cid, mid in s.execute(select(Message.conversation_id, Message.model_id).where(Message.conversation_id.in_(ids), Message.model_id != None).distinct()):
            used.setdefault(cid, set()).add(mid)
    models = _models(s)
    users = users_by_id(s, {c.user_id for c in picked})
    items = []
    for c in picked:
        n, media, errs = counts.get(c.id, (0, 0, 0))
        mids = sorted(used.get(c.id, set()) | ({c.model_id} if c.model_id else set()))
        expired = _expired(c, now)
        items.append({"id": c.id, "title": titles.get(c.id) or decrypt(st, c.title_enc, "title"), "owner": _owner(users, c.user_id), "mode": c.mode,
                      "model": _model_ref(models, c.model_id), "models": [_model_ref(models, m) for m in mids], "messages": n, "media_items": media,
                      "failed_messages": errs, "created_at": c.created_at, "updated_at": c.updated_at, "expires_at": c.expires_at, "archived": bool(c.archived),
                      "status": "expired" if expired else "archived" if c.archived else "active"})
    return {"items": items, "next_cursor": nxt, "scanned": scanned, "truncated": truncated, "limit": limit, "scope": scope.label,
            "owners": sorted({i["owner"]["id"] for i in items}), "searched": "titles" if f.q else None}


def message_context(st, s, owner_id: str, msgs: list[Message], admin_id: str) -> dict[str, dict]:
    """For the messages of one conversation: the generation job (with outputs) and the attachments each one carries, thumbnails included.
    Only the conversation owner's own rows resolve."""
    now = time.time()
    job_ids = [(m.meta or {}).get("job_id") for m in msgs if m.kind in ("image", "video")]
    job_ids = [j for j in job_ids if isinstance(j, str) and _ID.match(j)]
    jobs = [j for j in s.execute(select(MediaJob).where(MediaJob.id.in_(job_ids), MediaJob.user_id == owner_id)).scalars()] if job_ids else []
    entries = {e["id"]: e for e in job_entries(st, s, jobs, admin_id)}
    for j in jobs:
        e = entries[j.id]
        params = decrypt_json(st, j.params_enc) if j.params_enc and e["status"] != "expired" else {}
        e["params"] = safe_params(st, s.get(Model, j.model_id), params)
    out: dict[str, dict] = {}
    all_refs: dict[str, list[tuple[str, str]]] = {}
    for m in msgs:
        meta = m.meta or {}
        refs = [("attachment", x) for x in (meta.get("attachments") or []) if isinstance(x, str) and _ID.match(x)]
        if isinstance(meta.get("source"), str) and _ID.match(meta["source"]) and meta["source"] not in [i for _, i in refs]:
            refs.append(("source", meta["source"]))
        all_refs[m.id] = refs
    flat = [r for v in all_refs.values() for r in v]
    resolved = {(r["role"], r["id"]): r for r in resolve_refs(st, s, owner_id, list(dict.fromkeys(flat)), admin_id, now, thumbs=True)}
    for m in msgs:
        meta = m.meta or {}
        job = entries.get(meta.get("job_id")) if m.kind in ("image", "video") else None
        out[m.id] = {"media": job, "attachment_items": [resolved[r] for r in all_refs[m.id] if r in resolved]}
    return out
