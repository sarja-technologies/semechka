"""Organisation, group, workspace and access-rule operations. Every function checks authority through `authz.Authz`,
refuses ids that belong to another organisation, and records an organisation-scoped audit event."""
from __future__ import annotations

import re

from sqlalchemy import delete, event, func, insert, select

from . import audit, authz
from .models import Meta, User
from .models_tenancy import (
    AclEntry,
    Group,
    GroupMember,
    Org,
    OrgMember,
    OrgRole,
    Workspace,
    WorkspaceMember,
)


class TenancyError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48] or "org"


def _audit(s, actor, org_id, action, target="", **detail):
    audit.record(s, actor, action, target, detail, org_id=org_id)


# ---- organisations -------------------------------------------------------------------------------
def seed_roles(s, org_id: str) -> dict[str, OrgRole]:
    out = {}
    for t in authz.ROLE_TEMPLATES:
        r = s.execute(select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.key == t["key"])).scalar_one_or_none()
        if r is None:
            r = OrgRole(org_id=org_id, **{k: (list(v) if isinstance(v, list) else v) for k, v in t.items()})
            s.add(r)
        out[t["key"]] = r
    s.flush()
    return out


def create_org(s, actor: str, name: str, slug: str | None = None, *, owner_id: str | None = None,
               is_default: bool = False, auto_join: bool = False) -> Org:
    name = name.strip()
    if not name:
        raise TenancyError("invalid", "An organisation needs a name.")
    slug = _slug(slug or name)
    base, n = slug, 1
    while s.execute(select(Org).where(Org.slug == slug)).scalar_one_or_none():
        n += 1
        slug = f"{base}-{n}"
    o = Org(name=name, slug=slug, is_default=is_default, auto_join=auto_join)
    s.add(o)
    s.flush()
    roles = seed_roles(s, o.id)
    if not is_default:  # a second tenant ends the single-tenant convenience: auto-joining would mix companies' directories
        for d in s.execute(select(Org).where(Org.auto_join == True, Org.id != o.id)).scalars():
            d.auto_join = False
            _audit(s, actor, d.id, "org.autojoin.disabled", d.id, reason="another organisation was created")
    if owner_id:
        s.add(OrgMember(org_id=o.id, user_id=owner_id, status="active", role_ids=[roles["org_owner"].id], added_by=actor))
    _audit(s, actor, o.id, "org.create", o.id, name=name, owner=owner_id or "")
    s.flush()
    return o


def get_role(s, org_id: str, key_or_id: str, scope: str | None = None) -> OrgRole:
    r = s.execute(select(OrgRole).where(OrgRole.org_id == org_id, (OrgRole.id == key_or_id) | (OrgRole.key == key_or_id))).scalar_one_or_none()
    if r is None or (scope and r.scope != scope):
        raise TenancyError("invalid", "That role does not exist in this organisation.")
    return r


def _owner_count(s, org_id: str, excluding: str | None = None) -> int:
    owner = s.execute(select(OrgRole.id).where(OrgRole.org_id == org_id, OrgRole.key == "org_owner")).scalar()
    n = 0
    for m in s.execute(select(OrgMember).where(OrgMember.org_id == org_id, OrgMember.status == "active")).scalars():
        if m.user_id != excluding and owner in (m.role_ids or []):
            n += 1
    return n


# ---- members -------------------------------------------------------------------------------------
def add_member(s, az: authz.Authz, org_id: str, user_id: str, role_keys: list[str] | None = None) -> OrgMember:
    az.require("org.members.manage", None, org_id=org_id)
    if s.get(User, user_id) is None:
        raise TenancyError("invalid", "No such person.")
    if s.get(OrgMember, (org_id, user_id)):
        raise TenancyError("exists", "Already a member.", 409)
    roles = [get_role(s, org_id, k, "org").id for k in (role_keys or ["member"])]
    if any(get_role(s, org_id, r).key == "org_owner" for r in roles) and not az.check_org("org.roles.manage", org_id).allowed:
        raise TenancyError("forbidden", "Only an administrator who manages roles can make an owner.", 403)
    m = OrgMember(org_id=org_id, user_id=user_id, role_ids=roles, added_by=az.user_id)
    s.add(m)
    _audit(s, az.user_id, org_id, "org.member.add", user_id, roles=roles)
    s.flush()
    return m


def set_member(s, az: authz.Authz, org_id: str, user_id: str, *, role_keys: list[str] | None = None,
               status: str | None = None) -> OrgMember:
    az.require("org.members.manage", None, org_id=org_id)
    m = s.get(OrgMember, (org_id, user_id))
    if m is None:
        raise TenancyError("not_found", "No such member.", 404)
    owner_id = get_role(s, org_id, "org_owner").id
    was_owner = m.status == "active" and owner_id in (m.role_ids or [])
    new_roles = [get_role(s, org_id, k, "org").id for k in role_keys] if role_keys is not None else list(m.role_ids or [])
    new_status = status if status is not None else m.status
    if new_status not in ("active", "suspended"):
        raise TenancyError("invalid", "Status must be active or suspended.")
    still_owner = new_status == "active" and owner_id in new_roles
    if was_owner and not still_owner and _owner_count(s, org_id, excluding=user_id) == 0:
        raise TenancyError("last_owner", "An organisation must keep at least one active owner.", 409)
    m.role_ids, m.status = new_roles, new_status  # only after every check has passed
    _audit(s, az.user_id, org_id, "org.member.update", user_id, roles=m.role_ids, status=m.status)
    s.flush()
    return m


def remove_member(s, az: authz.Authz, org_id: str, user_id: str) -> None:
    az.require("org.members.manage", None, org_id=org_id)
    m = s.get(OrgMember, (org_id, user_id))
    if m is None:
        raise TenancyError("not_found", "No such member.", 404)
    if get_role(s, org_id, "org_owner").id in (m.role_ids or []) and _owner_count(s, org_id, excluding=user_id) == 0:
        raise TenancyError("last_owner", "An organisation must keep at least one active owner.", 409)
    s.delete(m)
    gids = [g for (g,) in s.execute(select(Group.id).where(Group.org_id == org_id))]
    if gids:
        s.execute(delete(GroupMember).where(GroupMember.user_id == user_id, GroupMember.group_id.in_(gids)))
    wids = [w for (w,) in s.execute(select(Workspace.id).where(Workspace.org_id == org_id))]
    if wids:
        s.execute(delete(WorkspaceMember).where(WorkspaceMember.principal_type == "user", WorkspaceMember.principal_id == user_id,
                                                WorkspaceMember.workspace_id.in_(wids)))
    s.execute(delete(AclEntry).where(AclEntry.org_id == org_id, AclEntry.principal_type == "user", AclEntry.principal_id == user_id))
    _audit(s, az.user_id, org_id, "org.member.remove", user_id)
    s.flush()


# ---- groups --------------------------------------------------------------------------------------
def create_group(s, az: authz.Authz, org_id: str, name: str, kind: str = "department", parent_id: str | None = None) -> Group:
    if parent_id:
        parent = s.get(Group, parent_id)
        if parent is None or parent.org_id != org_id:
            raise TenancyError("invalid", "That parent group is not in this organisation.")
        if not (az.check_org("org.groups.manage", org_id).allowed or az.is_group_admin(org_id, parent_id)):
            raise TenancyError("forbidden", "You do not manage that group.", 403)
    else:
        az.require("org.groups.manage", None, org_id=org_id)
    if kind not in ("department", "team", "group"):
        raise TenancyError("invalid", "Kind must be department, team or group.")
    g = Group(org_id=org_id, name=name.strip(), kind=kind, parent_id=parent_id)
    s.add(g)
    s.flush()
    _audit(s, az.user_id, org_id, "group.create", g.id, name=g.name, parent=parent_id or "")
    return g


def _group(s, org_id, group_id) -> Group:
    g = s.get(Group, group_id)
    if g is None or g.org_id != org_id:
        raise TenancyError("not_found", "No such group.", 404)
    return g


def set_group_member(s, az: authz.Authz, org_id: str, group_id: str, user_id: str, *, is_admin: bool = False, present: bool = True):
    _group(s, org_id, group_id)
    if not (az.check_org("org.groups.manage", org_id).allowed or az.is_group_admin(org_id, group_id)):
        raise TenancyError("forbidden", "You do not manage that group.", 403)
    if present and s.get(OrgMember, (org_id, user_id)) is None:
        raise TenancyError("invalid", "Only members of the organisation can join its groups.")
    if is_admin and not az.check_org("org.groups.manage", org_id).allowed:
        raise TenancyError("forbidden", "Only an organisation administrator can appoint a department manager.", 403)
    row = s.get(GroupMember, (group_id, user_id))
    if present:
        if row is None:
            s.add(GroupMember(group_id=group_id, user_id=user_id, is_admin=is_admin))
        else:
            row.is_admin = is_admin
    elif row:
        s.delete(row)
    _audit(s, az.user_id, org_id, "group.member", group_id, user=user_id, present=present, admin=is_admin)
    s.flush()


# ---- workspaces ----------------------------------------------------------------------------------
def create_workspace(s, az: authz.Authz, org_id: str, name: str, *, parent_id: str | None = None,
                     visibility: str = "private", description: str = "", inherit_members: bool = True) -> Workspace:
    if visibility not in ("private", "shared"):
        raise TenancyError("invalid", "Visibility must be private or shared.")
    parent = None
    if parent_id:
        parent = s.get(Workspace, parent_id)
        if parent is None or parent.org_id != org_id:
            raise TenancyError("invalid", "That parent workspace is not in this organisation.")
        az.require("ws.members.manage", parent)  # nested workspaces are created by the parent's administrators
    else:
        az.require("org.workspaces.create", None, org_id=org_id)
    quota = (az.org(org_id).quotas or {}).get("workspaces")
    if quota and s.execute(select(func.count()).select_from(Workspace).where(Workspace.org_id == org_id)).scalar() >= quota:
        raise TenancyError("quota", f"This organisation is limited to {quota} workspaces.", 409)
    w = Workspace(org_id=org_id, name=name.strip(), description=description, visibility=visibility,
                  parent_id=parent_id, inherit_members=inherit_members, created_by=az.user_id)
    s.add(w)
    s.flush()
    admin = get_role(s, org_id, "ws_admin", "workspace")
    s.add(WorkspaceMember(workspace_id=w.id, principal_type="user", principal_id=az.user_id, role_id=admin.id, added_by=az.user_id))
    _audit(s, az.user_id, org_id, "ws.create", w.id, name=w.name, visibility=visibility, parent=parent_id or "")
    s.flush()
    return w


def get_workspace(s, ws_id: str) -> Workspace:
    w = s.get(Workspace, ws_id)
    if w is None:
        raise TenancyError("not_found", "No such workspace.", 404)
    return w


def _has_admin(s, ws: Workspace) -> bool:
    admin = get_role(s, ws.org_id, "ws_admin", "workspace").id
    return s.execute(select(func.count()).select_from(WorkspaceMember).where(
        WorkspaceMember.workspace_id == ws.id, WorkspaceMember.role_id == admin)).scalar() > 0


def _principal_ok(s, org_id: str, ptype: str, pid: str):
    if ptype == "user":
        m = s.get(OrgMember, (org_id, pid))
        if m is None:
            raise TenancyError("invalid", "That person is not a member of this organisation.")
    elif ptype == "group":
        _group(s, org_id, pid)
    else:
        raise TenancyError("invalid", "Principal type must be user or group.")


def _may_manage_ws_members(az: authz.Authz, s, ws: Workspace) -> str:
    """'ws' when the caller administers the workspace; 'recover' when it has no administrator left and the caller may
    appoint one (org.workspaces.recover); otherwise raises."""
    if az.check("ws.members.manage", ws).allowed:
        return "ws"
    if az.check_org("org.workspaces.recover", ws.org_id).allowed and not _has_admin(s, ws):
        return "recover"
    raise authz.Forbidden(az.check("ws.members.manage", ws))


def set_workspace_member(s, az: authz.Authz, ws_id: str, ptype: str, pid: str, role_key: str) -> WorkspaceMember:
    ws = get_workspace(s, ws_id)
    mode = _may_manage_ws_members(az, s, ws)
    _principal_ok(s, ws.org_id, ptype, pid)
    role = get_role(s, ws.org_id, role_key, "workspace")
    if mode == "recover" and role.key != "ws_admin":
        raise TenancyError("forbidden", "A workspace with no administrator can only be given an administrator.", 403)
    # replace any earlier role of this principal in this workspace: one role per principal per workspace
    s.execute(delete(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.principal_type == ptype,
                                            WorkspaceMember.principal_id == pid))
    row = WorkspaceMember(workspace_id=ws.id, principal_type=ptype, principal_id=pid, role_id=role.id, added_by=az.user_id)
    s.add(row)
    _audit(s, az.user_id, ws.org_id, "ws.member.set", ws.id, principal=f"{ptype}:{pid}", role=role.key, mode=mode)
    s.flush()
    return row


def remove_workspace_member(s, az: authz.Authz, ws_id: str, ptype: str, pid: str) -> None:
    ws = get_workspace(s, ws_id)
    az.require("ws.members.manage", ws)
    rows = s.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id, WorkspaceMember.principal_type == ptype,
                                                   WorkspaceMember.principal_id == pid)).scalars().all()
    if not rows:
        raise TenancyError("not_found", "No such member.", 404)
    admin = get_role(s, ws.org_id, "ws_admin", "workspace").id
    if any(r.role_id == admin for r in rows):
        others = s.execute(select(func.count()).select_from(WorkspaceMember).where(
            WorkspaceMember.workspace_id == ws.id, WorkspaceMember.role_id == admin,
            ~((WorkspaceMember.principal_type == ptype) & (WorkspaceMember.principal_id == pid)))).scalar()
        if not others:
            raise TenancyError("last_admin", "A workspace must keep at least one administrator.", 409)
    for r in rows:
        s.delete(r)
    _audit(s, az.user_id, ws.org_id, "ws.member.remove", ws.id, principal=f"{ptype}:{pid}")
    s.flush()


def _check_models(s, policy) -> None:
    from . import gateway_svc
    if policy and "models" in policy:
        try:
            policy["models"] = gateway_svc.validate_policy_models(s, policy["models"])
        except ValueError as e:
            raise TenancyError("invalid", str(e)) from None


def update_workspace(s, az: authz.Authz, ws_id: str, **changes) -> Workspace:
    ws = get_workspace(s, ws_id)
    for key, val in changes.items():
        if key in ("policy", "quotas"):
            az.require("ws.policy.edit", ws)
        elif key == "archived" and val:
            az.require("ws.delete", ws)
        else:
            az.require("ws.members.manage", ws)
    if "archived" in changes and not changes["archived"]:
        az.require("ws.delete", ws)
    allowed = ("name", "description", "visibility", "inherit_members", "policy", "quotas", "archived")
    for key, val in changes.items():  # validate everything before changing anything
        if key not in allowed:
            raise TenancyError("invalid", f"Cannot change {key}.")
        if key == "visibility" and val not in ("private", "shared"):
            raise TenancyError("invalid", "Visibility must be private or shared.")
        if key == "policy":
            bad = [x for x in (val or {}).get("deny", []) if not authz.valid_pattern(x)]
            if bad:
                raise TenancyError("invalid", f"Unknown permission pattern: {bad[0]}")
            _check_models(s, val)
    for key, val in changes.items():
        setattr(ws, key, val)
    _audit(s, az.user_id, ws.org_id, "ws.update", ws.id, fields=sorted(changes))
    s.flush()
    return ws


# ---- access rules and roles ----------------------------------------------------------------------
def add_acl(s, az: authz.Authz, ws_id: str, rtype: str, rid: str, ptype: str, pid: str, effect: str, perms: list[str],
            nodes: list[authz.Node] | None = None) -> AclEntry:
    ws = get_workspace(s, ws_id)
    az.require("ws.members.manage", ws, nodes or [])
    _principal_ok(s, ws.org_id, ptype, pid)
    if effect not in ("allow", "deny"):
        raise TenancyError("invalid", "Effect must be allow or deny.")
    bad = [p for p in perms if not authz.valid_pattern(p)]
    if bad or not perms:
        raise TenancyError("invalid", f"Unknown permission: {bad[0]}" if bad else "Name at least one permission.")
    if rtype == "workspace" and rid != ws.id:
        raise TenancyError("invalid", "A workspace rule must name this workspace.")
    a = AclEntry(org_id=ws.org_id, resource_type=rtype, resource_id=rid, principal_type=ptype, principal_id=pid,
                 effect=effect, perms=perms, created_by=az.user_id)
    s.add(a)
    _audit(s, az.user_id, ws.org_id, "acl.add", f"{rtype}:{rid}", principal=f"{ptype}:{pid}", effect=effect, perms=perms)
    s.flush()
    return a


def remove_acl(s, az: authz.Authz, ws_id: str, acl_id: str) -> None:
    ws = get_workspace(s, ws_id)
    az.require("ws.members.manage", ws)
    a = s.get(AclEntry, acl_id)
    if a is None or a.org_id != ws.org_id:
        raise TenancyError("not_found", "No such rule.", 404)
    s.delete(a)
    _audit(s, az.user_id, ws.org_id, "acl.remove", f"{a.resource_type}:{a.resource_id}", principal=f"{a.principal_type}:{a.principal_id}")
    s.flush()


def save_role(s, az: authz.Authz, org_id: str, *, role_id: str | None, key: str | None, scope: str | None, label: str,
              description: str, grants: list[str], denies: list[str]) -> OrgRole:
    az.require("org.roles.manage", None, org_id=org_id)
    bad = [p for p in [*grants, *denies] if not authz.valid_pattern(p)]
    if bad:
        raise TenancyError("invalid", f"Unknown permission: {bad[0]}")
    if role_id:
        r = get_role(s, org_id, role_id)
        r.label, r.description, r.grants, r.denies = label, description, grants, denies
    else:
        if scope not in ("org", "workspace"):
            raise TenancyError("invalid", "Scope must be org or workspace.")
        key = _slug(key or label)
        if s.execute(select(OrgRole).where(OrgRole.org_id == org_id, OrgRole.key == key)).scalar_one_or_none():
            raise TenancyError("exists", "A role with that key already exists.", 409)
        r = OrgRole(org_id=org_id, key=key, scope=scope, label=label, description=description, grants=grants, denies=denies)
        s.add(r)
    s.flush()
    _audit(s, az.user_id, org_id, "role.save", r.id, key=r.key, grants=r.grants, denies=r.denies)
    return r


def delete_role(s, az: authz.Authz, org_id: str, role_id: str) -> None:
    az.require("org.roles.manage", None, org_id=org_id)
    r = get_role(s, org_id, role_id)
    if r.system:
        raise TenancyError("system", "Shipped roles can be edited but not deleted.", 409)
    in_use = s.execute(select(func.count()).select_from(WorkspaceMember).where(WorkspaceMember.role_id == r.id)).scalar() or \
        any(r.id in (m.role_ids or []) for m in s.execute(select(OrgMember).where(OrgMember.org_id == org_id)).scalars())
    if in_use:
        raise TenancyError("in_use", "That role is still assigned.", 409)
    s.delete(r)
    _audit(s, az.user_id, org_id, "role.delete", role_id, key=r.key)
    s.flush()


# ---- the default organisation (single-tenant installs) ---------------------------------------------
def ensure_default_org(s, name: str = "Default organisation") -> Org | None:
    """Create the default organisation once, on a fresh install. Existing installs get theirs from migration 8."""
    if s.get(Meta, "seeded_default_org") or s.execute(select(func.count()).select_from(Org)).scalar():
        return None
    s.add(Meta(key="seeded_default_org", value="1"))
    return create_org(s, "", name, "default", is_default=True, auto_join=True)


@event.listens_for(User, "after_insert")
def _auto_join(_mapper, conn, user) -> None:
    """New platform users join the auto-join organisation; the first person to join becomes its owner."""
    org = conn.execute(select(Org.__table__.c.id).where(Org.__table__.c.auto_join == True,
                                                       Org.__table__.c.status == "active")
                       .order_by(Org.__table__.c.created_at).limit(1)).first()
    if org is None:
        return
    members = conn.execute(select(func.count()).select_from(OrgMember.__table__)
                           .where(OrgMember.__table__.c.org_id == org[0])).scalar()
    key = "member" if members else "org_owner"
    role = conn.execute(select(OrgRole.__table__.c.id).where(OrgRole.__table__.c.org_id == org[0],
                                                             OrgRole.__table__.c.key == key)).scalar()
    if role:
        conn.execute(insert(OrgMember.__table__).values(org_id=org[0], user_id=user.id, status="active",
                                                        role_ids=[role], added_by="", created_at=user.created_at))
