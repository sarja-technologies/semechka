"""Tenant-aware authorisation: the single place that answers "may this user do X to this resource".

Order of evaluation (docs/ENTERPRISE.md, D3), restrictions first:
  1. Tenant boundary. No active membership of the resource's organisation (or a suspended user, organisation or
     membership) means no access, whatever platform role the person holds.
  2. Deny wins. A deny from any source - an organisation role, a workspace role, an ACL entry on any node of the
     resource chain, or the policy ceiling of the organisation or any workspace in the chain - refuses the action.
  3. Allow needs a source. Sources are the organisation roles of the membership, workspace role assignments (to the
     user or to a group the user belongs to, inherited from parent workspaces while `inherit_members` holds) and ACL
     allows. Nothing else grants anything: new resources are default-deny.
  4. Content permissions are `sensitive`: no wildcard grant (`*`, `file.*`) covers them, they must be named. An
     organisation administrator therefore administers people and policy without reading confidential content.
  5. A `restricted` folder/file/context stops role-derived access. Only ACL entries at or below the restricted node
     and roles that hold `ws.restricted.access` reach it.
The same evaluator runs in HTTP handlers, background jobs and agent tools: authority is recomputed from the database
each time, never carried over from when a job was queued.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select

from .models import User
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


@dataclass(frozen=True)
class Perm:
    id: str
    label: str
    group: str
    sensitive: bool = False


def _p(id, label, group, sensitive=False):
    return Perm(id, label, group, sensitive)


REGISTRY: list[Perm] = [
    # organisation administration (never content)
    _p("org.view", "See the organisation and its directory", "Organisation"),
    _p("org.members.manage", "Add, suspend and remove members", "Organisation"),
    _p("org.groups.manage", "Manage departments and groups", "Organisation"),
    _p("org.roles.manage", "Edit organisation and workspace roles", "Organisation"),
    _p("org.workspaces.create", "Create workspaces", "Organisation"),
    _p("org.workspaces.list_all", "List every workspace (names only)", "Organisation"),
    _p("org.workspaces.recover", "Appoint an administrator to a workspace that has none", "Organisation"),
    _p("org.policy.edit", "Edit organisation policy and quotas", "Organisation"),
    _p("org.audit.view", "View the organisation audit log", "Organisation"),
    _p("org.models.manage", "Choose which models the organisation may use", "Organisation"),
    # workspace administration
    _p("ws.view", "See that a workspace exists and its description", "Workspace"),
    _p("ws.members.manage", "Manage workspace members and access", "Workspace"),
    _p("ws.policy.edit", "Edit workspace policy and quotas", "Workspace"),
    _p("ws.delete", "Archive or delete the workspace", "Workspace"),
    _p("ws.share", "Share resources inside the organisation", "Workspace"),
    _p("ws.restricted.access", "Open restricted folders, files and contexts", "Workspace", True),
    # content (sensitive: always granted by name)
    _p("file.read", "Read files and folders", "Content", True),
    _p("file.download", "Download files and artifacts", "Content", True),
    _p("file.write", "Upload and edit files", "Content", True),
    _p("file.delete", "Delete files", "Content", True),
    _p("file.restore", "Restore earlier versions and deleted files", "Content", True),
    _p("ctx.read", "Search and read contexts", "Content", True),
    _p("ctx.write", "Add to and edit contexts", "Content", True),
    _p("ctx.manage", "Create, attach, detach and delete contexts", "Content", True),
    _p("mem.read", "Read agent memory", "Content", True),
    _p("mem.write", "Create and edit agent memory", "Content", True),
    # agents, tools and jobs
    _p("agent.view", "See agents", "Agents"),
    _p("agent.use", "Talk to and run agents", "Agents"),
    _p("agent.manage", "Create and configure agents", "Agents"),
    _p("agent.tool.python", "Agents may run Python", "Agents"),
    _p("agent.tool.shell", "Agents may run command-line tools", "Agents"),
    _p("agent.tool.network", "Agents may use the network", "Agents"),
    _p("agent.tool.media", "Agents may generate images and video", "Agents"),
    _p("agent.approve", "Approve sensitive agent actions", "Agents"),
    _p("job.view_own", "See own jobs", "Jobs"),
    _p("job.manage_all", "See and cancel anyone's jobs in the workspace", "Jobs"),
]
BY_ID = {p.id: p for p in REGISTRY}
FILE_PERMS = frozenset({"file.read", "file.download", "file.write", "file.delete", "file.restore"})


def valid_pattern(pattern: str) -> bool:
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        return any(p.id.startswith(pattern[:-1]) for p in REGISTRY)
    return pattern in BY_ID


def matches(pattern: str, perm: str, *, grant: bool) -> bool:
    p = BY_ID.get(perm)
    if p is None:
        return False
    if pattern == "*" or (pattern.endswith(".*") and perm.startswith(pattern[:-1])):
        return not (grant and p.sensitive)
    return pattern == perm


def holds(patterns, perm: str, *, grant: bool) -> bool:
    return any(matches(x, perm, grant=grant) for x in patterns or [])


# Role templates, copied into each organisation when it is created and editable there afterwards.
_VIEW = ["ws.view", "file.read", "file.download", "ctx.read", "agent.view", "job.view_own"]
_CONTRIB = _VIEW + ["file.write", "agent.use", "mem.read"]
_EDIT = _CONTRIB + ["file.delete", "file.restore", "ctx.write", "mem.write", "agent.tool.python", "agent.tool.media"]
_WSADMIN = _EDIT + ["ctx.manage", "ws.members.manage", "ws.policy.edit", "ws.delete", "ws.share", "ws.restricted.access",
                    "agent.manage", "agent.tool.shell", "agent.tool.network", "agent.approve", "job.manage_all"]
_ORGADMIN = ["org.view", "org.members.manage", "org.groups.manage", "org.roles.manage", "org.workspaces.create",
             "org.workspaces.list_all", "org.policy.edit", "org.audit.view", "org.models.manage"]
ROLE_TEMPLATES: list[dict] = [
    {"key": "org_owner", "scope": "org", "label": "Organisation Owner", "system": True,
         "description": "Owns the organisation: people, policy and quotas. No content access by default.",
         "grants": [*_ORGADMIN, "org.workspaces.recover"], "denies": []},
    {"key": "org_admin", "scope": "org", "label": "Organisation Administrator", "system": True,
         "description": "Manages people, groups, roles, policy and the audit log. No content access by default.",
         "grants": list(_ORGADMIN), "denies": []},
    {"key": "dept_admin", "scope": "org", "label": "Department Manager", "system": True,
         "description": "Sees the directory and creates workspaces; manages only the groups they administer.",
         "grants": ["org.view", "org.workspaces.create"], "denies": []},
    {"key": "member", "scope": "org", "label": "Member", "system": True,
         "description": "An employee. Sees the directory; reaches content only through workspace roles.",
         "grants": ["org.view"], "denies": []},
    {"key": "ws_admin", "scope": "workspace", "label": "Workspace Administrator", "system": True,
         "description": "Runs one workspace: members, policy, agents, contexts and all its content.", "grants": list(_WSADMIN), "denies": []},
    {"key": "editor", "scope": "workspace", "label": "Editor", "system": True,
         "description": "Creates, edits and deletes files and contexts; runs agents.", "grants": list(_EDIT), "denies": []},
    {"key": "contributor", "scope": "workspace", "label": "Contributor", "system": True,
         "description": "Adds files and runs agents; cannot delete.", "grants": list(_CONTRIB), "denies": []},
    {"key": "viewer", "scope": "workspace", "label": "Viewer", "system": True,
         "description": "Reads and downloads; cannot change anything.", "grants": list(_VIEW), "denies": []},
]


@dataclass
class Node:
    """One step of a resource's ancestry below its workspace (folder, file, context...)."""
    type: str
    id: str
    restricted: bool = False
    owner: str = ""  # files: who created it (used by the per-owner rule below)


@dataclass
class Decision:
    allowed: bool
    perm: str
    reason: str
    trace: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:  # `if authz.check(...)` reads naturally
        return self.allowed


class Authz:
    """Authority of one user, recomputed from the database. Cheap to build; caches per instance."""

    def __init__(self, s, user_id: str):
        self.s, self.user_id = s, user_id
        u = s.get(User, user_id)
        self.active = bool(u and u.status == "active")
        self._members: dict[str, OrgMember | None] = {}
        self._orgs: dict[str, Org | None] = {}
        self._roles: dict[str, OrgRole | None] = {}
        self._group_ids: dict[str, set[str]] = {}

    # -- loading ---------------------------------------------------------------------------------
    def org(self, org_id: str) -> Org | None:
        if org_id not in self._orgs:
            self._orgs[org_id] = self.s.get(Org, org_id)
        return self._orgs[org_id]

    def membership(self, org_id: str) -> OrgMember | None:
        if org_id not in self._members:
            m = self.s.get(OrgMember, (org_id, self.user_id))
            o = self.org(org_id)
            self._members[org_id] = m if (m and m.status == "active" and o and o.status == "active" and self.active) else None
        return self._members[org_id]

    def role(self, role_id: str, org_id: str) -> OrgRole | None:
        if role_id not in self._roles:
            self._roles[role_id] = self.s.get(OrgRole, role_id)
        r = self._roles[role_id]
        return r if r and r.org_id == org_id else None  # a role id from another organisation grants nothing

    def groups(self, org_id: str) -> set[str]:
        """Ids of the groups the user is in, with every ancestor group."""
        if org_id not in self._group_ids:
            parent = {g.id: g.parent_id for g in self.s.execute(select(Group).where(Group.org_id == org_id)).scalars()}
            direct = {r for (r,) in self.s.execute(select(GroupMember.group_id).where(GroupMember.user_id == self.user_id))
                      if r in parent}
            out = set()
            for g in direct:
                while g and g not in out:
                    out.add(g)
                    g = parent.get(g)
            self._group_ids[org_id] = out
        return self._group_ids[org_id]

    def is_group_admin(self, org_id: str, group_id: str) -> bool:
        """Administrator of the group or of any group above it."""
        parent = {g.id: g.parent_id for g in self.s.execute(select(Group).where(Group.org_id == org_id)).scalars()}
        admin = {r for (r,) in self.s.execute(select(GroupMember.group_id).where(
            GroupMember.user_id == self.user_id, GroupMember.is_admin == True))}
        g, seen = group_id, set()
        while g and g not in seen:
            if g in admin:
                return self.membership(org_id) is not None
            seen.add(g)
            g = parent.get(g)
        return False

    # -- organisation-level -------------------------------------------------------------------------
    def _org_sources(self, org_id: str, m: OrgMember):
        grants, denies, names = [], [], []
        for rid in m.role_ids or []:
            r = self.role(rid, org_id)
            if r and r.scope == "org":
                grants += r.grants or []
                denies += r.denies or []
                names.append(r.label)
        return grants, denies, names

    def check_org(self, perm: str, org_id: str) -> Decision:
        if perm not in BY_ID:
            return Decision(False, perm, f"Unknown permission '{perm}'.")
        m = self.membership(org_id)
        if m is None:
            return Decision(False, perm, "You are not an active member of this organisation.")
        grants, denies, names = self._org_sources(org_id, m)
        o = self.org(org_id)
        ceiling = (o.policy or {}).get("deny", [])
        if holds(ceiling, perm, grant=False):
            return Decision(False, perm, "The organisation's policy does not allow this.", ["org policy"])
        if holds(denies, perm, grant=False):
            return Decision(False, perm, "A role you hold denies this.", names)
        if holds(grants, perm, grant=True):
            return Decision(True, perm, "Allowed by your organisation role.", names)
        return Decision(False, perm, "No role you hold grants this.", names)

    # -- workspace-level ------------------------------------------------------------------------------
    def workspace_chain(self, ws: Workspace) -> list[Workspace]:
        """The workspace and each ancestor whose members it inherits."""
        out, cur = [ws], ws
        while cur.inherit_members and cur.parent_id and len(out) < 32:
            cur = self.s.get(Workspace, cur.parent_id)
            if cur is None or cur.org_id != ws.org_id:
                break
            out.append(cur)
        return out

    def _matches_me(self, ptype: str, pid: str, groups: set[str]) -> bool:
        return (ptype == "user" and pid == self.user_id) or (ptype == "group" and pid in groups)

    def check(self, perm: str, ws: Workspace | None, nodes: list[Node] | tuple = (), *, org_id: str | None = None) -> Decision:
        """May this user exercise `perm` on the resource at the end of `nodes` (most specific first) in `ws`?"""
        if perm not in BY_ID:
            return Decision(False, perm, f"Unknown permission '{perm}'.")
        oid = ws.org_id if ws else org_id
        if oid is None:
            return Decision(False, perm, "No organisation given.")
        m = self.membership(oid)
        if m is None:  # tenant boundary: nothing below this line is reached by an outsider
            return Decision(False, perm, "You are not an active member of this organisation.", ["tenant boundary"])
        if ws is None:
            return self.check_org(perm, oid)
        if ws.archived and perm not in ("ws.view", "file.read", "file.download", "ws.delete", "ws.members.manage"):
            return Decision(False, perm, "This workspace is archived.", ["archived"])

        org = self.org(oid)
        chain = self.workspace_chain(ws)
        grants, denies, trace = [], [], []
        # organisation roles (explicit names can reach content, e.g. a read-only compliance role)
        og, od, onames = self._org_sources(oid, m)
        denies += [("organisation role", x) for x in od]
        # ceilings
        ceilings = [("organisation policy", x) for x in (org.policy or {}).get("deny", [])]
        for w in chain:
            ceilings += [(f"workspace policy ({w.name})", x) for x in (w.policy or {}).get("deny", [])]
        for src, pat in ceilings:
            if holds([pat], perm, grant=False):
                return Decision(False, perm, f"Not allowed by {src}.", [src])

        # per-owner file rule: where a workspace in the chain says file_access == "owner", another person's file is reachable only
        # through an access rule naming that very file. Roles, folder and workspace rules and restricted-access never reach it.
        owner_only = any((w.policy or {}).get("file_access") == "owner" for w in chain)
        foreign_file = owner_only and perm in FILE_PERMS and any(
            n.type == "file" and n.owner != self.user_id for n in nodes)  # an unknown owner is nobody's: deny by default

        # nodes run leaf-first, so the highest index is the restriction nearest the workspace; it governs everything below
        flagged = [i for i, n in enumerate(nodes) if n.restricted]
        restricted_at = max(flagged) if flagged else None

        groups = self.groups(oid)
        ws_ids = [w.id for w in chain]
        role_grants = []
        for wm in self.s.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id.in_(ws_ids))).scalars():
            if not self._matches_me(wm.principal_type, wm.principal_id, groups):
                continue
            r = self.role(wm.role_id, oid)
            if r is None or r.scope != "workspace":
                continue
            denies += [(f"role {r.label}", x) for x in r.denies or []]
            role_grants.append(r)
            trace.append(r.label)

        if holds(og, perm, grant=True) and not foreign_file:
            grants.append(("organisation role", ", ".join(onames)))
        for r in role_grants:
            if foreign_file:
                break
            if holds(r.grants, perm, grant=True):
                if restricted_at is not None and not holds(r.grants, "ws.restricted.access", grant=True):
                    continue  # a restricted item is not reached through an ordinary role
                grants.append((f"workspace role {r.label}", r.label))
        if ws.visibility == "shared" and perm == "ws.view":
            grants.append(("shared workspace", "visible to the organisation"))

        # ACL entries: workspaces in the chain and every node of the resource
        keys = [("workspace", w.id, None) for w in chain]
        for i, n in enumerate(nodes):
            keys.append((n.type, n.id, i))
        for atype, aid, idx in keys:
            for a in self.s.execute(select(AclEntry).where(AclEntry.org_id == oid, AclEntry.resource_type == atype,
                                                           AclEntry.resource_id == aid)).scalars():
                if not self._matches_me(a.principal_type, a.principal_id, groups) or not holds(a.perms, perm, grant=(a.effect == "allow")):
                    continue
                if a.effect == "deny":
                    return Decision(False, perm, "An access rule denies this.", trace + [f"deny on {atype}"])
                if foreign_file and atype != "file":
                    continue
                # an allow above a restricted node does not open it; at or below it does
                if restricted_at is not None and (idx is None or idx > restricted_at):
                    continue
                grants.append((f"access rule on {atype}", a.principal_id))

        for src, pat in denies:
            if holds([pat], perm, grant=False):
                return Decision(False, perm, f"Denied by {src}.", trace)
        if grants:
            return Decision(True, perm, f"Allowed by {grants[0][0]}.", trace)
        if foreign_file:
            return Decision(False, perm, "This workspace keeps each person's files to their owner unless a rule names the file.",
                            trace + ["owner-only files"])
        return Decision(False, perm, "Nothing grants this to you." if restricted_at is None
                        else "This item is restricted; no access rule or role opens it for you.", trace)

    def require(self, perm: str, ws, nodes=(), *, org_id=None):
        d = self.check(perm, ws, nodes, org_id=org_id)
        if not d.allowed:
            raise Forbidden(d)
        return d

    # -- listing ----------------------------------------------------------------------------------------
    def visible_workspaces(self, org_id: str) -> list[Workspace]:
        """Workspaces the user may see exist: ws.view through any source, or org.workspaces.list_all."""
        if self.membership(org_id) is None:
            return []
        list_all = self.check_org("org.workspaces.list_all", org_id).allowed
        out = []
        for w in self.s.execute(select(Workspace).where(Workspace.org_id == org_id).order_by(Workspace.name)).scalars():
            if list_all or self.check("ws.view", w).allowed:
                out.append(w)
        return out

    def effective_perms(self, ws: Workspace, nodes=()) -> list[str]:
        return [p.id for p in REGISTRY if self.check(p.id, ws, nodes).allowed]


class Forbidden(Exception):
    def __init__(self, decision: Decision):
        super().__init__(decision.reason)
        self.decision = decision


def intersect(*decisions: Decision) -> Decision:
    """Authority of an agent acting for a user: every party must allow."""
    for d in decisions:
        if not d.allowed:
            return d
    return decisions[0] if decisions else Decision(False, "", "No decision.")
