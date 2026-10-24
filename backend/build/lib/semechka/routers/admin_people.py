from __future__ import annotations

import hashlib
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select

from .. import audit, perms, security, services
from ..deps import Ctx, audit_ctx, require
from ..models import (Invite, Role, SessionRow, Team, TeamMember, User, UserOverride, UserRole)

router = APIRouter(prefix="/api/admin")


def _err(status, code, message, **kw):
    return HTTPException(status, {"code": code, "message": message, **kw})


def expand(pattern: str) -> list[str]:
    if pattern == "*":
        return [p.id for p in perms.REGISTRY]
    if pattern.endswith(".*"):
        return [p.id for p in perms.REGISTRY if p.id.startswith(pattern[:-1])]
    return [pattern] if pattern in perms.BY_ID else []


def is_owner(p: perms.Principal) -> bool:
    return "owner" in p.role_ids


def check_grantable(actor: perms.Principal, patterns: list[str]):
    """No privilege escalation: you can only hand out what you hold. Sensitive content access may be
    granted by an owner by name; it is never implied by '*'."""
    for pat in patterns:
        if not perms.valid_pattern(pat):
            raise _err(422, "invalid", f"'{pat}' is not a known permission.")
        if pat == "*" and not is_owner(actor):
            raise _err(403, "escalation", "Only an owner can grant every permission.")
        for perm in expand(pat):
            if pat == "*" and perms.BY_ID[perm].sensitive:
                continue
            if actor.can(perm):
                continue
            if is_owner(actor) and perms.BY_ID[perm].sensitive and pat != "*":
                continue
            raise _err(403, "escalation", f"You cannot grant '{perm}' because you do not hold it.")


def _user_out(ctx: Ctx, u: User, roles: dict, teams: dict) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "status": u.status,
            "roles": [{"id": r.role_id, "label": roles[r.role_id].label} for r in u.roles if r.role_id in roles],
            "teams": [{"id": t.team_id, "name": teams[t.team_id].name} for t in u.teams if t.team_id in teams],
            "created_at": u.created_at, "last_login_at": u.last_login_at, "mfa": u.totp_enabled}


def _user_query(ctx: Ctx, q=None, status=None, role=None, team=None):
    stmt = select(User)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(or_(func.lower(User.email).like(like), func.lower(User.display_name).like(like)))
    if status:
        stmt = stmt.where(User.status == status)
    if role:
        stmt = stmt.where(User.id.in_(select(UserRole.user_id).where(UserRole.role_id == role)))
    if team:
        stmt = stmt.where(User.id.in_(select(TeamMember.user_id).where(TeamMember.team_id == team)))
    return stmt


@router.get("/users")
def list_users(ctx: Ctx = Depends(require("admin.users.view")), q: str | None = None, status: str | None = None,
               role: str | None = None, team: str | None = None, sort: str = "created_at", order: str = "desc",
               page: int = 1, per_page: int = 25):
    per_page = max(1, min(per_page, 200))
    col = {"created_at": User.created_at, "email": User.email, "name": User.display_name,
           "last_login": User.last_login_at, "status": User.status}.get(sort, User.created_at)
    base = _user_query(ctx, q, status, role, team)
    total = ctx.s.execute(select(func.count()).select_from(base.subquery())).scalar()
    rows = ctx.s.execute(base.order_by(col.desc() if order == "desc" else col.asc()).limit(per_page).offset((page - 1) * per_page)).scalars().all()
    roles = {r.id: r for r in ctx.s.execute(select(Role)).scalars()}
    teams = {t.id: t for t in ctx.s.execute(select(Team)).scalars()}
    return {"total": total, "page": page, "per_page": per_page, "items": [_user_out(ctx, u, roles, teams) for u in rows]}


class UserCreate(BaseModel):
    email: str = Field(max_length=320)
    display_name: str = Field(min_length=1, max_length=120)
    password: str = Field(max_length=300)
    role_id: str = "member"
    team_ids: list[str] = []


@router.post("/users")
def create_user(body: UserCreate, ctx: Ctx = Depends(require("admin.users.manage"))):
    email = body.email.strip().lower()
    if "@" not in email:
        raise _err(422, "invalid", "Enter a valid email address.")
    role = ctx.s.get(Role, body.role_id)
    if role is None:
        raise _err(422, "invalid", "Unknown role.")
    _check_role_assignable(ctx, role)
    problem = security.password_problem(body.password, email, ctx.st.settings.deployment(ctx.s, "auth.password_min_length"))
    if problem:
        raise _err(422, "weak_password", problem)
    if ctx.s.execute(select(User).where(User.email == email)).scalar_one_or_none():
        raise _err(409, "exists", "That email already has an account.")
    u = User(email=email, display_name=body.display_name.strip(), password_hash=security.hash_password(body.password), status="active")
    ctx.s.add(u)
    ctx.s.flush()
    ctx.s.add(UserRole(user_id=u.id, role_id=role.id))
    for tid in body.team_ids:
        if ctx.s.get(Team, tid):
            ctx.s.add(TeamMember(team_id=tid, user_id=u.id))
    audit_ctx(ctx, "user.create", u.id, {"role": role.id})
    return {"id": u.id}


def _check_role_assignable(ctx: Ctx, role: Role):
    if role.id == "owner" and not is_owner(ctx.principal):
        raise _err(403, "escalation", "Only an owner can assign the owner role.")
    check_grantable(ctx.principal, list(role.grants or []))


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    status: str | None = Field(default=None, pattern="^(active|suspended)$")
    role_ids: list[str] | None = None
    team_ids: list[str] | None = None


def _last_owner_guard(ctx: Ctx, user: User, *, removing_owner=False, suspending=False):
    owners = [x.user_id for x in ctx.s.execute(select(UserRole).where(UserRole.role_id == "owner")).scalars()]
    active_owners = [o for o in owners if (u := ctx.s.get(User, o)) and u.status == "active"]
    if user.id in active_owners and len(active_owners) <= 1 and (removing_owner or suspending):
        raise _err(409, "last_owner", "This is the only active owner. Assign another owner first.")


def _guard_target(ctx: Ctx, user: User):
    if any(r.role_id == "owner" for r in user.roles) and not is_owner(ctx.principal):
        raise _err(403, "forbidden", "Only an owner can change an owner account.")


@router.patch("/users/{uid}")
def patch_user(uid: str, body: UserPatch, ctx: Ctx = Depends(require("admin.users.manage"))):
    u = ctx.s.get(User, uid)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    _guard_target(ctx, u)
    if body.display_name:
        u.display_name = body.display_name.strip()
    if body.status:
        if body.status == "suspended":
            _last_owner_guard(ctx, u, suspending=True)
            if u.id == ctx.user.id:
                raise _err(409, "self", "You cannot suspend yourself.")
            for sess in ctx.s.execute(select(SessionRow).where(SessionRow.user_id == u.id)).scalars():
                ctx.s.delete(sess)
        u.status = body.status
        audit_ctx(ctx, f"user.{'suspend' if body.status == 'suspended' else 'activate'}", u.id)
    if body.role_ids is not None:
        new = set(body.role_ids)
        if not new:
            raise _err(422, "invalid", "A person needs at least one role.")
        roles = {r.id: r for r in ctx.s.execute(select(Role)).scalars()}
        for rid in new:
            if rid not in roles:
                raise _err(422, "invalid", "Unknown role.")
        cur = {r.role_id for r in u.roles}
        for rid in new - cur:
            _check_role_assignable(ctx, roles[rid])
        if "owner" in cur and "owner" not in new:
            if not is_owner(ctx.principal):
                raise _err(403, "forbidden", "Only an owner can remove the owner role.")
            _last_owner_guard(ctx, u, removing_owner=True)
        u.roles[:] = [UserRole(user_id=u.id, role_id=r) for r in new]
        audit_ctx(ctx, "user.roles", u.id, {"roles": sorted(new)})
    if body.team_ids is not None:
        teams = {t.id for t in ctx.s.execute(select(Team)).scalars()}
        u.teams[:] = [TeamMember(team_id=t, user_id=u.id) for t in set(body.team_ids) if t in teams]
        audit_ctx(ctx, "user.teams", u.id, {"teams": sorted(body.team_ids)})
    return {"ok": True}


class SetPassword(BaseModel):
    password: str = Field(max_length=300)


@router.post("/users/{uid}/password")
def set_password(uid: str, body: SetPassword, ctx: Ctx = Depends(require("admin.users.manage"))):
    u = ctx.s.get(User, uid)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    _guard_target(ctx, u)
    problem = security.password_problem(body.password, u.email, ctx.st.settings.deployment(ctx.s, "auth.password_min_length"))
    if problem:
        raise _err(422, "weak_password", problem)
    u.password_hash = security.hash_password(body.password)
    for sess in ctx.s.execute(select(SessionRow).where(SessionRow.user_id == u.id)).scalars():
        ctx.s.delete(sess)
    audit_ctx(ctx, "user.password_set", u.id)
    return {"ok": True}


@router.delete("/users/{uid}")
def delete_user(uid: str, ctx: Ctx = Depends(require("admin.users.manage"))):
    u = ctx.s.get(User, uid)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    _guard_target(ctx, u)
    if u.id == ctx.user.id:
        raise _err(409, "self", "You cannot delete yourself here.")
    _last_owner_guard(ctx, u, removing_owner=True)
    audit_ctx(ctx, "user.delete", u.id)
    ctx.s.delete(u)
    return {"ok": True}


class Bulk(BaseModel):
    action: str = Field(pattern="^(approve|suspend|activate|delete|add_role|remove_role)$")
    ids: list[str] | None = None
    all_matching: dict | None = None  # {q,status,role,team}
    expected_count: int
    role_id: str | None = None


@router.post("/users/bulk")
def bulk_users(body: Bulk, ctx: Ctx = Depends(require("admin.users.manage"))):
    """'Selected rows' and 'every match across all pages' are distinct requests; the count must be confirmed."""
    if (body.ids is None) == (body.all_matching is None):
        raise _err(422, "invalid", "Send either ids or all_matching.")
    if body.ids is not None:
        users = [u for uid in body.ids if (u := ctx.s.get(User, uid))]
    else:
        f = body.all_matching
        users = ctx.s.execute(_user_query(ctx, f.get("q"), f.get("status"), f.get("role"), f.get("team"))).scalars().all()
    if len(users) != body.expected_count:
        raise _err(409, "count_mismatch", f"This would affect {len(users)} people, not {body.expected_count}. Review and retry.")
    done, skipped = 0, []
    role = ctx.s.get(Role, body.role_id) if body.role_id else None
    if body.action in ("add_role", "remove_role"):
        if role is None:
            raise _err(422, "invalid", "Choose a role.")
        if body.action == "add_role":
            _check_role_assignable(ctx, role)
    for u in users:
        try:
            if u.id == ctx.user.id and body.action in ("suspend", "delete", "remove_role"):
                raise _err(409, "self", "Skipped yourself.")
            _guard_target(ctx, u)
            if body.action in ("approve", "activate"):
                u.status = "active"
            elif body.action == "suspend":
                _last_owner_guard(ctx, u, suspending=True)
                u.status = "suspended"
                for sess in ctx.s.execute(select(SessionRow).where(SessionRow.user_id == u.id)).scalars():
                    ctx.s.delete(sess)
            elif body.action == "delete":
                _last_owner_guard(ctx, u, removing_owner=True)
                ctx.s.delete(u)
            elif body.action == "add_role":
                if not any(r.role_id == role.id for r in u.roles):
                    u.roles.append(UserRole(user_id=u.id, role_id=role.id))
            elif body.action == "remove_role":
                if role.id == "owner":
                    _last_owner_guard(ctx, u, removing_owner=True)
                left = [r for r in u.roles if r.role_id != role.id]
                if not left:
                    raise _err(409, "invalid", "A person needs at least one role.")
                u.roles[:] = left
            done += 1
        except HTTPException as e:
            skipped.append({"id": u.id, "reason": e.detail["message"]})
    audit_ctx(ctx, f"user.bulk.{body.action}", "", {"count": done, "skipped": len(skipped), "scope": "ids" if body.ids is not None else "all_matching"})
    return {"done": done, "skipped": skipped}


# ---- roles ---------------------------------------------------------------------------------------------
@router.get("/permissions")
def permission_registry(ctx: Ctx = Depends(require("admin.users.view"))):
    return [{"id": p.id, "label": p.label, "group": p.group, "description": p.description, "sensitive": p.sensitive}
            for p in perms.REGISTRY]


@router.get("/roles")
def list_roles(ctx: Ctx = Depends(require("admin.users.view"))):
    counts = dict(ctx.s.execute(select(UserRole.role_id, func.count()).group_by(UserRole.role_id)).all())
    return [{"id": r.id, "label": r.label, "description": r.description, "rank": r.rank, "grants": r.grants, "denies": r.denies,
             "system": r.system, "members": counts.get(r.id, 0)} for r in ctx.s.execute(select(Role).order_by(Role.rank)).scalars()]


class RoleIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    rank: int = Field(default=100, ge=0, le=1000)
    grants: list[str] = []
    denies: list[str] = []


def _slug(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:30] or "role"


@router.post("/roles")
def create_role(body: RoleIn, ctx: Ctx = Depends(require("admin.roles.manage"))):
    check_grantable(ctx.principal, body.grants)
    for d in body.denies:
        if not perms.valid_pattern(d):
            raise _err(422, "invalid", f"'{d}' is not a known permission.")
    rid = _slug(body.label)
    while ctx.s.get(Role, rid):
        rid = f"{_slug(body.label)}_{secrets.token_hex(2)}"
    ctx.s.add(Role(id=rid, label=body.label.strip(), description=body.description, rank=body.rank, grants=body.grants, denies=body.denies))
    audit_ctx(ctx, "role.create", rid, {"grants": body.grants, "denies": body.denies})
    return {"id": rid}


@router.put("/roles/{rid}")
def update_role(rid: str, body: RoleIn, ctx: Ctx = Depends(require("admin.roles.manage"))):
    r = ctx.s.get(Role, rid)
    if r is None:
        raise _err(404, "not_found", "No such role.")
    if rid == "owner" and not is_owner(ctx.principal):
        raise _err(403, "forbidden", "Only an owner can edit the owner role.")
    if rid == "owner" and ("*" not in body.grants or body.denies):
        raise _err(409, "protected", "The owner role must keep full administration so the platform cannot be locked out.")
    # Only newly added grants are checked; keeping existing ones never needs the editor to hold them.
    added = [g for g in body.grants if g not in (r.grants or [])]
    check_grantable(ctx.principal, added)
    for d in body.denies:
        if not perms.valid_pattern(d):
            raise _err(422, "invalid", f"'{d}' is not a known permission.")
    # Editing your own role may not remove your ability to edit roles.
    old = {"label": r.label, "grants": r.grants, "denies": r.denies}
    r.label, r.description, r.rank, r.grants, r.denies = body.label.strip(), body.description, body.rank, body.grants, body.denies
    audit_ctx(ctx, "role.update", rid, {"old": old, "new": {"label": r.label, "grants": r.grants, "denies": r.denies}})
    return {"ok": True}


@router.delete("/roles/{rid}")
def delete_role(rid: str, ctx: Ctx = Depends(require("admin.roles.manage"))):
    r = ctx.s.get(Role, rid)
    if r is None:
        raise _err(404, "not_found", "No such role.")
    if r.system:
        raise _err(409, "protected", "Built-in roles can be renamed but not deleted.")
    if ctx.s.execute(select(func.count()).select_from(UserRole).where(UserRole.role_id == rid)).scalar():
        raise _err(409, "in_use", "Move the people in this role to another role first.")
    audit_ctx(ctx, "role.delete", rid)
    ctx.s.delete(r)
    return {"ok": True}


# ---- teams ---------------------------------------------------------------------------------------------
@router.get("/teams")
def list_teams(ctx: Ctx = Depends(require("admin.users.view"))):
    counts = dict(ctx.s.execute(select(TeamMember.team_id, func.count()).group_by(TeamMember.team_id)).all())
    return [{"id": t.id, "name": t.name, "grants": t.grants, "denies": t.denies, "members": counts.get(t.id, 0)}
            for t in ctx.s.execute(select(Team).order_by(Team.name)).scalars()]


class TeamIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    grants: list[str] = []
    denies: list[str] = []


@router.post("/teams")
def create_team(body: TeamIn, ctx: Ctx = Depends(require("admin.teams.manage"))):
    check_grantable(ctx.principal, body.grants)
    if ctx.s.execute(select(Team).where(Team.name == body.name.strip())).scalar_one_or_none():
        raise _err(409, "exists", "A team with that name already exists.")
    t = Team(name=body.name.strip(), grants=body.grants, denies=body.denies)
    ctx.s.add(t)
    ctx.s.flush()
    audit_ctx(ctx, "team.create", t.id)
    return {"id": t.id}


@router.put("/teams/{tid}")
def update_team(tid: str, body: TeamIn, ctx: Ctx = Depends(require("admin.teams.manage"))):
    t = ctx.s.get(Team, tid)
    if t is None:
        raise _err(404, "not_found", "No such team.")
    check_grantable(ctx.principal, [g for g in body.grants if g not in (t.grants or [])])
    t.name, t.grants, t.denies = body.name.strip(), body.grants, body.denies
    audit_ctx(ctx, "team.update", tid, {"grants": body.grants, "denies": body.denies})
    return {"ok": True}


@router.delete("/teams/{tid}")
def delete_team(tid: str, ctx: Ctx = Depends(require("admin.teams.manage"))):
    t = ctx.s.get(Team, tid)
    if t is None:
        raise _err(404, "not_found", "No such team.")
    audit_ctx(ctx, "team.delete", tid)
    ctx.s.delete(t)
    return {"ok": True}


# ---- per-user overrides --------------------------------------------------------------------------------
@router.get("/users/{uid}/override")
def get_override(uid: str, ctx: Ctx = Depends(require("admin.users.view"))):
    o = ctx.s.get(UserOverride, uid)
    return {"grants": o.grants if o else [], "denies": o.denies if o else []}


class OverrideIn(BaseModel):
    grants: list[str] = []
    denies: list[str] = []


@router.put("/users/{uid}/override")
def put_override(uid: str, body: OverrideIn, ctx: Ctx = Depends(require("admin.roles.manage"))):
    u = ctx.s.get(User, uid)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    _guard_target(ctx, u)
    o = ctx.s.get(UserOverride, uid)
    check_grantable(ctx.principal, [g for g in body.grants if g not in (o.grants if o else [])])
    for d in body.denies:
        if not perms.valid_pattern(d):
            raise _err(422, "invalid", f"'{d}' is not a known permission.")
    if o is None:
        ctx.s.add(UserOverride(user_id=uid, grants=body.grants, denies=body.denies))
    else:
        o.grants, o.denies = body.grants, body.denies
    audit_ctx(ctx, "user.override", uid, {"grants": body.grants, "denies": body.denies})
    return {"ok": True}


@router.get("/explain/permission")
def explain_permission(user_id: str, permission: str, ctx: Ctx = Depends(require("admin.users.view"))):
    """'Why can't they do this?' — names the matching role/team/user rule."""
    u = ctx.s.get(User, user_id)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    d = services.load_principal(ctx.s, u).decide(permission)
    return {"allowed": d.allowed, "permission": permission, "reason": d.reason, "denies": d.matched_denies, "grants": d.matched_grants}


# ---- invitations ---------------------------------------------------------------------------------------
class InviteIn(BaseModel):
    email: str | None = Field(default=None, max_length=320)
    role_id: str = "member"
    team_id: str | None = None
    max_uses: int = Field(default=1, ge=1, le=1000)
    expires_hours: int | None = Field(default=None, ge=1, le=24 * 365)


@router.post("/invites")
def create_invite(body: InviteIn, ctx: Ctx = Depends(require("admin.users.invite"))):
    role = ctx.s.get(Role, body.role_id)
    if role is None:
        raise _err(422, "invalid", "Unknown role.")
    _check_role_assignable(ctx, role)  # an inviter cannot grant more than they hold
    hours = body.expires_hours or ctx.st.settings.deployment(ctx.s, "signup.invite_expiry_hours")
    token = secrets.token_urlsafe(24)
    inv = Invite(token_hash=hashlib.sha256(token.encode()).hexdigest(), email=(body.email or "").strip().lower() or None,
                 role_id=role.id, team_id=body.team_id, created_by=ctx.user.id, expires_at=time.time() + hours * 3600,
                 max_uses=body.max_uses)
    ctx.s.add(inv)
    ctx.s.flush()
    base = ctx.st.settings.deployment(ctx.s, "access.public_url").rstrip("/")
    audit_ctx(ctx, "invite.create", inv.id, {"role": role.id, "max_uses": body.max_uses})
    return {"id": inv.id, "token": token, "path": f"/signup?invite={token}", "url": f"{base}/signup?invite={token}" if base else None,
            "expires_at": inv.expires_at, "note": "The invitation link is shown once."}


@router.get("/invites")
def list_invites(ctx: Ctx = Depends(require("admin.users.invite"))):
    now = time.time()
    return [{"id": i.id, "email": i.email, "role_id": i.role_id, "uses": i.uses, "max_uses": i.max_uses, "expires_at": i.expires_at,
             "state": "revoked" if i.revoked else "expired" if i.expires_at < now else "used" if i.uses >= i.max_uses else "active"}
            for i in ctx.s.execute(select(Invite).order_by(Invite.created_at.desc()).limit(200)).scalars()]


@router.delete("/invites/{iid}")
def revoke_invite(iid: str, ctx: Ctx = Depends(require("admin.users.invite"))):
    inv = ctx.s.get(Invite, iid)
    if inv is None:
        raise _err(404, "not_found", "No such invitation.")
    inv.revoked = True
    audit_ctx(ctx, "invite.revoke", iid)
    return {"ok": True}


@router.get("/users/{uid}")
def get_user(uid: str, ctx: Ctx = Depends(require("admin.users.view"))):
    u = ctx.s.get(User, uid)
    if u is None:
        raise _err(404, "not_found", "No such person.")
    roles = {r.id: r for r in ctx.s.execute(select(Role)).scalars()}
    teams = {t.id: t for t in ctx.s.execute(select(Team)).scalars()}
    return _user_out(ctx, u, roles, teams)
