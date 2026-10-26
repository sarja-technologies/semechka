"""Organisations, groups, roles, workspaces and access rules (docs/ENTERPRISE.md).

Authorisation lives in authz.Authz and tenancy_svc; handlers only translate. A workspace the caller cannot see answers
404, never 403, so identifiers cannot be probed across organisations."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import audit, authz
from .. import tenancy_svc as T
from ..deps import Ctx, cloud_gate, current, require
from ..models import AuditEvent, User
from ..models_tenancy import (
    AclEntry,
    Group,
    GroupMember,
    Org,
    OrgMember,
    OrgRole,
    Workspace,
    WorkspaceMember,
)

router = APIRouter(prefix="/api", dependencies=[Depends(cloud_gate)])


def az(ctx: Ctx) -> authz.Authz:
    return authz.Authz(ctx.s, ctx.user.id)


def _err(e: Exception) -> HTTPException:
    if isinstance(e, T.TenancyError):
        return HTTPException(e.status, {"code": e.code, "message": e.message})
    if isinstance(e, authz.Forbidden):
        return HTTPException(403, {"code": "forbidden", "permission": e.decision.perm, "message": e.decision.reason})
    raise e


def run(fn, *a, **k):
    try:
        return fn(*a, **k)
    except (T.TenancyError, authz.Forbidden) as e:
        raise _err(e) from None


def org_for(ctx: Ctx, oid: str, perm: str = "org.view") -> authz.Authz:
    a = az(ctx)
    if a.membership(oid) is None:
        raise HTTPException(404, {"code": "not_found", "message": "No such organisation."})
    if perm:
        run(a.require, perm, None, org_id=oid)
    return a


def ws_for(ctx: Ctx, wid: str, perm: str = "ws.view") -> tuple[authz.Authz, Workspace]:
    a = az(ctx)
    w = ctx.s.get(Workspace, wid)
    # Organisation administrators can list workspaces by name (org.workspaces.list_all); that gets them the metadata
    # and nothing else, because every other route re-checks its own permission below.
    if w is None or not (a.check("ws.view", w).allowed or a.check_org("org.workspaces.list_all", w.org_id).allowed):
        raise HTTPException(404, {"code": "not_found", "message": "No such workspace."})
    if perm:
        run(a.require, perm, w)
    return a, w


# ---- views -------------------------------------------------------------------------------------------
def _org(o: Org, a: authz.Authz) -> dict:
    return {"id": o.id, "slug": o.slug, "name": o.name, "status": o.status, "is_default": o.is_default,
            "auto_join": o.auto_join, "policy": o.policy, "quotas": o.quotas,
            "my_permissions": [p.id for p in authz.REGISTRY if a.check_org(p.id, o.id).allowed and p.id.startswith("org.")]}


def _ws(w: Workspace, a: authz.Authz | None = None) -> dict:
    d = {"id": w.id, "org_id": w.org_id, "parent_id": w.parent_id, "name": w.name, "description": w.description,
         "visibility": w.visibility, "inherit_members": w.inherit_members, "archived": w.archived,
         "policy": w.policy, "quotas": w.quotas, "created_at": w.created_at}
    if a:
        d["my_permissions"] = a.effective_perms(w)
    return d


def _role(r: OrgRole) -> dict:
    return {"id": r.id, "key": r.key, "scope": r.scope, "label": r.label, "description": r.description,
            "grants": r.grants, "denies": r.denies, "system": r.system}


# ---- organisations -------------------------------------------------------------------------------
@router.get("/orgs")
def my_orgs(ctx: Ctx = Depends(current)):
    a = az(ctx)
    rows = ctx.s.execute(select(Org).join(OrgMember, OrgMember.org_id == Org.id)
                         .where(OrgMember.user_id == ctx.user.id).order_by(Org.name)).scalars().all()
    return [_org(o, a) for o in rows if a.membership(o.id)]


class OrgIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    slug: str | None = Field(default=None, max_length=64)
    owner_email: str


@router.post("/orgs")
def create_org(body: OrgIn, ctx: Ctx = Depends(require("admin.orgs.manage"))):
    owner = ctx.s.execute(select(User).where(User.email == body.owner_email.strip().lower())).scalar_one_or_none()
    if owner is None:
        raise HTTPException(422, {"code": "invalid", "message": "The first owner must be an existing person."})
    o = run(T.create_org, ctx.s, ctx.user.id, body.name, body.slug, owner_id=owner.id)
    return _org(o, az(ctx)) if az(ctx).membership(o.id) else {"id": o.id, "slug": o.slug, "name": o.name}


@router.get("/orgs/{oid}")
def get_org(oid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid)
    return _org(a.org(oid), a)


class OrgPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    policy: dict | None = None
    quotas: dict | None = None
    auto_join: bool | None = None


@router.patch("/orgs/{oid}")
def patch_org(oid: str, body: OrgPatch, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.policy.edit")
    o = a.org(oid)
    if body.policy is not None:
        bad = [x for x in body.policy.get("deny", []) if not authz.valid_pattern(x)]
        if bad:
            raise HTTPException(422, {"code": "invalid", "message": f"Unknown permission pattern: {bad[0]}"})
        run(T._check_models, ctx.s, body.policy)
        o.policy = body.policy
    for k in ("name", "quotas", "auto_join"):
        if getattr(body, k) is not None:
            setattr(o, k, getattr(body, k))
    audit.record(ctx.s, ctx.user.id, "org.update", oid, {"fields": sorted(body.model_fields_set)}, ctx.ip, org_id=oid)
    ctx.s.flush()
    return _org(o, a)


# ---- members ---------------------------------------------------------------------------------------------
@router.get("/orgs/{oid}/members")
def members(oid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid)
    admin = a.check_org("org.members.manage", oid).allowed  # the directory shows names; addresses are for administrators
    rows = ctx.s.execute(select(OrgMember, User).join(User, User.id == OrgMember.user_id).where(OrgMember.org_id == oid)
                         .order_by(User.display_name)).all()
    return [{"user_id": u.id, "display_name": u.display_name, **({"email": u.email} if admin else {}),
             "status": m.status, "role_ids": m.role_ids} for m, u in rows]


class MemberIn(BaseModel):
    email: str
    roles: list[str] = ["member"]


@router.post("/orgs/{oid}/members")
def add_member(oid: str, body: MemberIn, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.members.manage")
    u = ctx.s.execute(select(User).where(User.email == body.email.strip().lower())).scalar_one_or_none()
    if u is None:
        raise HTTPException(422, {"code": "invalid", "message": "No account with that email. Create the account first."})
    run(T.add_member, ctx.s, a, oid, u.id, body.roles)
    return {"ok": True}


class MemberPatch(BaseModel):
    roles: list[str] | None = None
    status: str | None = None


@router.patch("/orgs/{oid}/members/{uid}")
def patch_member(oid: str, uid: str, body: MemberPatch, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.members.manage")
    m = run(T.set_member, ctx.s, a, oid, uid, role_keys=body.roles, status=body.status)
    return {"user_id": uid, "status": m.status, "role_ids": m.role_ids}


@router.delete("/orgs/{oid}/members/{uid}")
def del_member(oid: str, uid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.members.manage")
    run(T.remove_member, ctx.s, a, oid, uid)
    return {"ok": True}


# ---- roles -------------------------------------------------------------------------------------------------
@router.get("/orgs/{oid}/roles")
def roles(oid: str, ctx: Ctx = Depends(current)):
    org_for(ctx, oid)
    return {"roles": [_role(r) for r in ctx.s.execute(select(OrgRole).where(OrgRole.org_id == oid).order_by(OrgRole.scope, OrgRole.label)).scalars()],
            "permissions": [{"id": p.id, "label": p.label, "group": p.group, "sensitive": p.sensitive} for p in authz.REGISTRY]}


class RoleIn(BaseModel):
    key: str | None = None
    scope: str | None = None
    label: str = Field(min_length=1, max_length=80)
    description: str = ""
    grants: list[str] = []
    denies: list[str] = []


@router.post("/orgs/{oid}/roles")
def new_role(oid: str, body: RoleIn, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.roles.manage")
    return _role(run(T.save_role, ctx.s, a, oid, role_id=None, key=body.key, scope=body.scope, label=body.label,
                     description=body.description, grants=body.grants, denies=body.denies))


@router.put("/orgs/{oid}/roles/{rid}")
def edit_role(oid: str, rid: str, body: RoleIn, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.roles.manage")
    return _role(run(T.save_role, ctx.s, a, oid, role_id=rid, key=None, scope=None, label=body.label,
                     description=body.description, grants=body.grants, denies=body.denies))


@router.delete("/orgs/{oid}/roles/{rid}")
def del_role(oid: str, rid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.roles.manage")
    run(T.delete_role, ctx.s, a, oid, rid)
    return {"ok": True}


# ---- groups ------------------------------------------------------------------------------------------------
@router.get("/orgs/{oid}/groups")
def groups(oid: str, ctx: Ctx = Depends(current)):
    org_for(ctx, oid)
    gs = ctx.s.execute(select(Group).where(Group.org_id == oid).order_by(Group.name)).scalars().all()
    mem = {}
    for gm in ctx.s.execute(select(GroupMember).where(GroupMember.group_id.in_([g.id for g in gs] or [""]))).scalars():
        mem.setdefault(gm.group_id, []).append({"user_id": gm.user_id, "is_admin": gm.is_admin})
    return [{"id": g.id, "name": g.name, "kind": g.kind, "parent_id": g.parent_id, "members": mem.get(g.id, [])} for g in gs]


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: str = "department"
    parent_id: str | None = None


@router.post("/orgs/{oid}/groups")
def new_group(oid: str, body: GroupIn, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, None)
    g = run(T.create_group, ctx.s, a, oid, body.name, body.kind, body.parent_id)
    return {"id": g.id, "name": g.name, "kind": g.kind, "parent_id": g.parent_id}


class GroupMemberIn(BaseModel):
    user_id: str
    is_admin: bool = False
    present: bool = True


@router.put("/orgs/{oid}/groups/{gid}/members")
def group_member(oid: str, gid: str, body: GroupMemberIn, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, None)
    run(T.set_group_member, ctx.s, a, oid, gid, body.user_id, is_admin=body.is_admin, present=body.present)
    return {"ok": True}


# ---- workspaces ---------------------------------------------------------------------------------------------
@router.get("/orgs/{oid}/workspaces")
def list_workspaces(oid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid)
    return [_ws(w, a) for w in a.visible_workspaces(oid)]


class WsIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""
    visibility: str = "private"
    parent_id: str | None = None
    inherit_members: bool = True


@router.post("/orgs/{oid}/workspaces")
def new_workspace(oid: str, body: WsIn, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, None)
    w = run(T.create_workspace, ctx.s, a, oid, body.name, parent_id=body.parent_id, visibility=body.visibility,
            description=body.description, inherit_members=body.inherit_members)
    return _ws(w, a)


@router.get("/workspaces/{wid}")
def get_workspace(wid: str, ctx: Ctx = Depends(current)):
    a, w = ws_for(ctx, wid, None)  # metadata only; the gate already required ws.view or org.workspaces.list_all
    return _ws(w, a)


class WsPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    visibility: str | None = None
    inherit_members: bool | None = None
    policy: dict | None = None
    quotas: dict | None = None
    archived: bool | None = None


@router.patch("/workspaces/{wid}")
def patch_workspace(wid: str, body: WsPatch, ctx: Ctx = Depends(current)):
    a, w = ws_for(ctx, wid, None)  # tenancy_svc.update_workspace checks the permission each field needs
    changes = {k: getattr(body, k) for k in body.model_fields_set}
    run(T.update_workspace, ctx.s, a, wid, **changes)
    return _ws(w, a)


@router.get("/workspaces/{wid}/members")
def ws_members(wid: str, ctx: Ctx = Depends(current)):
    ws_for(ctx, wid)
    return [{"principal_type": m.principal_type, "principal_id": m.principal_id, "role_id": m.role_id}
            for m in ctx.s.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == wid)).scalars()]


class WsMemberIn(BaseModel):
    principal_type: str
    principal_id: str
    role: str


@router.put("/workspaces/{wid}/members")
def set_ws_member(wid: str, body: WsMemberIn, ctx: Ctx = Depends(current)):
    a, _ = ws_for(ctx, wid, None)  # the service decides between ordinary and recovery authority
    run(T.set_workspace_member, ctx.s, a, wid, body.principal_type, body.principal_id, body.role)
    return {"ok": True}


@router.delete("/workspaces/{wid}/members/{ptype}/{pid}")
def del_ws_member(wid: str, ptype: str, pid: str, ctx: Ctx = Depends(current)):
    a, _ = ws_for(ctx, wid, None)
    run(T.remove_workspace_member, ctx.s, a, wid, ptype, pid)
    return {"ok": True}


@router.get("/workspaces/{wid}/acl")
def get_acl(wid: str, ctx: Ctx = Depends(current)):
    _, w = ws_for(ctx, wid, "ws.members.manage")
    return [{"id": e.id, "resource_type": e.resource_type, "resource_id": e.resource_id, "principal_type": e.principal_type,
             "principal_id": e.principal_id, "effect": e.effect, "perms": e.perms}
            for e in ctx.s.execute(select(AclEntry).where(AclEntry.org_id == w.org_id)).scalars()
            if e.resource_type != "workspace" or e.resource_id == wid]


class AclIn(BaseModel):
    resource_type: str = "workspace"
    resource_id: str | None = None
    principal_type: str
    principal_id: str
    effect: str
    perms: list[str]


@router.post("/workspaces/{wid}/acl")
def add_acl(wid: str, body: AclIn, ctx: Ctx = Depends(current)):
    a, _ = ws_for(ctx, wid, None)
    e = run(T.add_acl, ctx.s, a, wid, body.resource_type, body.resource_id or wid, body.principal_type, body.principal_id,
            body.effect, body.perms)
    return {"id": e.id}


@router.delete("/workspaces/{wid}/acl/{aid}")
def del_acl(wid: str, aid: str, ctx: Ctx = Depends(current)):
    a, _ = ws_for(ctx, wid, None)
    run(T.remove_acl, ctx.s, a, wid, aid)
    return {"ok": True}


@router.get("/workspaces/{wid}/explain")
def explain(wid: str, perm: str, user_id: str | None = None, ctx: Ctx = Depends(current)):
    """Why would this person (default: you) be allowed or refused? Explaining someone else needs ws.members.manage."""
    _, w = ws_for(ctx, wid, "ws.members.manage" if user_id and user_id != ctx.user.id else "ws.view")
    target = authz.Authz(ctx.s, user_id or ctx.user.id)
    if user_id and target.membership(w.org_id) is None:
        raise HTTPException(404, {"code": "not_found", "message": "No such member."})
    d = target.check(perm, w)
    return {"allowed": d.allowed, "permission": perm, "reason": d.reason, "via": d.trace}


# ---- audit ----------------------------------------------------------------------------------------------
@router.get("/orgs/{oid}/audit")
def org_audit(oid: str, limit: int = 100, ctx: Ctx = Depends(current)):
    org_for(ctx, oid, "org.audit.view")
    rows = ctx.s.execute(select(AuditEvent).where(AuditEvent.org_id == oid).order_by(AuditEvent.id.desc())
                         .limit(max(1, min(limit, 500)))).scalars()
    return [{"id": e.id, "ts": e.ts, "actor": e.actor, "action": e.action, "target": e.target, "detail": e.detail} for e in rows]
