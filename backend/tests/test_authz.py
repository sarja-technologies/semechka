"""The authorisation engine and tenancy service, attacked directly (docs/ENTERPRISE.md, D3)."""
import pytest
from sqlalchemy import select

from semechka import authz
from semechka import tenancy_svc as T
from semechka.authz import Authz, Forbidden, Node
from semechka.db import Database
from semechka.models import AuditEvent, User
from semechka.models_tenancy import AclEntry, OrgMember, WorkspaceMember


@pytest.fixture()
def s(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/t.db")
    db.create_all()
    with db.session() as sess:
        yield sess


def person(s, name):
    u = User(email=f"{name}@example.test", display_name=name, password_hash="x", status="active")
    s.add(u)
    s.flush()
    return u


class World:
    """Two companies. Acme: owner/admin/alice(ws admin)/bob(viewer)/carol(plain member). Globex: gina (owner+ws admin)."""

    def __init__(self, s):
        self.s = s
        self.u = {n: person(s, n) for n in ("owner", "admin", "alice", "bob", "carol", "gina", "platform")}
        self.acme = T.create_org(s, "", "Acme", owner_id=self.u["owner"].id)
        self.globex = T.create_org(s, "", "Globex", owner_id=self.u["gina"].id)
        o = self.az("owner")
        T.add_member(s, o, self.acme.id, self.u["admin"].id, ["org_admin"])
        T.add_member(s, o, self.acme.id, self.u["alice"].id, ["dept_admin"])
        for n in ("bob", "carol"):
            T.add_member(s, o, self.acme.id, self.u[n].id, ["member"])
        self.mkt = T.create_workspace(s, self.az("alice"), self.acme.id, "Marketing")  # alice creates => ws admin
        T.set_workspace_member(s, self.az("alice"), self.mkt.id, "user", self.u["bob"].id, "viewer")
        self.gws = T.create_workspace(s, self.az("gina"), self.globex.id, "Secret Globex")

    def az(self, name):
        return Authz(self.s, self.u[name].id)


@pytest.fixture()
def w(s):
    return World(s)


# ---- tenant boundary --------------------------------------------------------------------------------------
def test_outsiders_and_platform_roles_reach_nothing(w):
    for who in ("alice", "owner", "admin", "platform"):  # even Acme's owner has no say in Globex
        for perm in ("ws.view", "file.read", "agent.use"):
            assert not w.az(who).check(perm, w.gws).allowed, (who, perm)
    assert w.az("gina").check("file.read", w.gws)
    assert not w.az("gina").check("file.read", w.mkt)


def test_a_role_from_another_organisation_grants_nothing(w, s):
    foreign = T.get_role(s, w.globex.id, "ws_admin", "workspace")
    s.add(WorkspaceMember(workspace_id=w.mkt.id, principal_type="user", principal_id=w.u["carol"].id, role_id=foreign.id))
    s.flush()
    assert not w.az("carol").check("file.read", w.mkt)


def test_service_calls_refuse_foreign_ids(w, s):
    a = w.az("alice")
    with pytest.raises(T.TenancyError):  # a Globex role in an Acme workspace
        T.set_workspace_member(s, a, w.mkt.id, "user", w.u["carol"].id, T.get_role(s, w.globex.id, "viewer").id)
    with pytest.raises(T.TenancyError):  # a Globex person in an Acme workspace
        T.set_workspace_member(s, a, w.mkt.id, "user", w.u["gina"].id, "viewer")
    with pytest.raises(T.TenancyError):
        T.add_acl(s, a, w.mkt.id, "workspace", w.mkt.id, "user", w.u["gina"].id, "allow", ["file.read"])
    with pytest.raises(Forbidden):  # Globex's workspace by id, from Acme
        T.set_workspace_member(s, a, w.gws.id, "user", w.u["carol"].id, "viewer")


def test_suspended_user_membership_and_organisation_hold_nothing(w, s):
    assert w.az("bob").check("file.read", w.mkt)
    w.u["bob"].status = "suspended"
    assert not w.az("bob").check("file.read", w.mkt)
    w.u["bob"].status = "active"
    s.get(OrgMember, (w.acme.id, w.u["bob"].id)).status = "suspended"
    assert not w.az("bob").check("file.read", w.mkt)
    s.get(OrgMember, (w.acme.id, w.u["bob"].id)).status = "active"
    w.acme.status = "suspended"
    assert not w.az("bob").check("file.read", w.mkt) and not w.az("alice").check("file.read", w.mkt)


# ---- administrators do not read content -----------------------------------------------------------------
def test_organisation_administrators_manage_people_not_content(w):
    for who in ("owner", "admin"):
        a = w.az(who)
        assert a.check_org("org.members.manage", w.acme.id)
        assert not a.check("file.read", w.mkt) and not a.check("ctx.read", w.mkt) and not a.check("mem.read", w.mkt)
        assert not a.check("ws.view", w.mkt)  # private workspace: not even its existence
        assert w.mkt.id in [x.id for x in a.visible_workspaces(w.acme.id)]  # names only (org.workspaces.list_all)


def test_wildcards_never_grant_content_but_always_deny_it(w, s):
    r = T.save_role(s, w.az("owner"), w.acme.id, role_id=None, key="everything", scope="org", label="Everything",
                    description="", grants=["*", "file.*"], denies=[])
    T.set_member(s, w.az("owner"), w.acme.id, w.u["carol"].id, role_keys=["everything"])
    c = w.az("carol")
    assert c.check_org("org.audit.view", w.acme.id)  # the wildcard covers administration
    assert not c.check("file.read", w.mkt) and not c.check("file.write", w.mkt)
    named = T.save_role(s, w.az("owner"), w.acme.id, role_id=r.id, key=None, scope=None, label="Everything", description="",
                        grants=["*", "file.read"], denies=[])
    assert named
    s.expire_all()
    assert w.az("carol").check("file.read", w.mkt)  # naming it grants it (an auditor role)
    T.save_role(s, w.az("owner"), w.acme.id, role_id=r.id, key=None, scope=None, label="Everything", description="",
                grants=["*", "file.read"], denies=["file.*"])
    assert not w.az("carol").check("file.read", w.mkt)  # a wildcard deny wins


def test_admins_cannot_put_themselves_into_a_private_workspace(w, s):
    for who in ("owner", "admin"):
        with pytest.raises(Forbidden):
            T.set_workspace_member(s, w.az(who), w.mkt.id, "user", w.u[who].id, "ws_admin")
    with pytest.raises(Forbidden):
        T.add_acl(s, w.az("admin"), w.mkt.id, "workspace", w.mkt.id, "user", w.u["admin"].id, "allow", ["file.read"])


def test_orphaned_workspace_can_only_be_given_an_administrator_by_an_owner(w, s):
    # alice is the only administrator; the owner may appoint a replacement but cannot grant themselves reading rights
    s.execute(WorkspaceMember.__table__.delete().where(WorkspaceMember.principal_id == w.u["alice"].id))
    with pytest.raises(Forbidden):
        T.set_workspace_member(s, w.az("admin"), w.mkt.id, "user", w.u["carol"].id, "ws_admin")  # admin lacks recover
    with pytest.raises(T.TenancyError):
        T.set_workspace_member(s, w.az("owner"), w.mkt.id, "user", w.u["carol"].id, "viewer")  # only an admin role
    T.set_workspace_member(s, w.az("owner"), w.mkt.id, "user", w.u["carol"].id, "ws_admin")
    assert w.az("carol").check("file.read", w.mkt)
    assert not w.az("owner").check("file.read", w.mkt)  # the owner still reads nothing
    with pytest.raises(Forbidden):  # recovery is closed once an administrator exists
        T.set_workspace_member(s, w.az("owner"), w.mkt.id, "user", w.u["owner"].id, "ws_admin")


# ---- workspace roles -----------------------------------------------------------------------------------
def test_viewer_contributor_editor_admin_ladder(w, s):
    a = w.az("alice")
    expect = {"viewer": ("file.read", "file.download"), "contributor": ("file.write", "agent.use"),
              "editor": ("file.delete", "ctx.write"), "ws_admin": ("ws.members.manage", "agent.manage")}
    order = ["viewer", "contributor", "editor", "ws_admin"]
    for i, role in enumerate(order):
        T.set_workspace_member(s, a, w.mkt.id, "user", w.u["carol"].id, role)
        c = w.az("carol")
        for j, r2 in enumerate(order):
            for perm in expect[r2]:
                assert c.check(perm, w.mkt).allowed == (j <= i), (role, perm)
    T.remove_workspace_member(s, a, w.mkt.id, "user", w.u["carol"].id)
    assert not w.az("carol").check("file.read", w.mkt)


def test_shared_workspace_is_visible_to_members_but_not_readable(w, s):
    T.update_workspace(s, w.az("alice"), w.mkt.id, visibility="shared")
    c = w.az("carol")
    assert c.check("ws.view", w.mkt) and not c.check("file.read", w.mkt)
    assert w.mkt.id in [x.id for x in c.visible_workspaces(w.acme.id)]
    assert w.gws.id not in [x.id for x in c.visible_workspaces(w.acme.id)]


def test_groups_nest_and_membership_inherits_down_the_workspace_tree(w, s):
    o = w.az("owner")
    dept = T.create_group(s, o, w.acme.id, "Marketing")
    team = T.create_group(s, o, w.acme.id, "Video", "team", dept.id)
    T.set_group_member(s, o, w.acme.id, team.id, w.u["carol"].id)
    T.set_workspace_member(s, w.az("alice"), w.mkt.id, "group", dept.id, "editor")  # a sub-team member counts as the department
    assert w.az("carol").check("file.write", w.mkt)
    sub = T.create_workspace(s, w.az("alice"), w.acme.id, "Campaign", parent_id=w.mkt.id)
    assert w.az("carol").check("file.write", sub)  # inherited
    T.update_workspace(s, w.az("alice"), sub.id, inherit_members=False)
    s.expire_all()
    assert not w.az("carol").check("file.write", sub) and w.az("alice").check("file.write", sub)


def test_group_from_another_organisation_cannot_be_used(w, s):
    g = T.create_group(s, w.az("gina"), w.globex.id, "Everyone")
    with pytest.raises(T.TenancyError):
        T.set_workspace_member(s, w.az("alice"), w.mkt.id, "group", g.id, "viewer")


# ---- deny wins, ceilings, restricted items --------------------------------------------------------------
def test_acl_deny_beats_role_allow_at_any_level(w, s):
    f = [Node("folder", "fo_1"), Node("folder", "fo_root")]
    assert w.az("bob").check("file.read", w.mkt, f)
    T.add_acl(s, w.az("alice"), w.mkt.id, "folder", "fo_root", "user", w.u["bob"].id, "deny", ["file.*"])
    assert not w.az("bob").check("file.read", w.mkt, f)
    assert w.az("bob").check("file.read", w.mkt)  # the rule is on the folder only
    assert w.az("alice").check("file.read", w.mkt, f)


def test_workspace_and_organisation_policy_are_ceilings_for_everyone(w, s):
    T.update_workspace(s, w.az("alice"), w.mkt.id, policy={"deny": ["agent.tool.network"]})
    assert not w.az("alice").check("agent.tool.network", w.mkt)  # not even the workspace admin
    assert w.az("alice").check("agent.tool.python", w.mkt)
    w.acme.policy = {"deny": ["agent.tool.*"]}
    assert not w.az("alice").check("agent.tool.python", w.mkt)
    with pytest.raises(T.TenancyError):
        T.update_workspace(s, w.az("alice"), w.mkt.id, policy={"deny": ["not.a.perm"]})
    with pytest.raises(Forbidden):
        T.update_workspace(s, w.az("bob"), w.mkt.id, policy={"deny": []})


def test_restricted_items_need_an_access_rule_or_the_restricted_permission(w, s):
    T.set_workspace_member(s, w.az("alice"), w.mkt.id, "user", w.u["carol"].id, "editor")
    node = [Node("file", "fi_1"), Node("folder", "fo_board", restricted=True)]
    assert not w.az("bob").check("file.read", w.mkt, node) and not w.az("carol").check("file.read", w.mkt, node)
    assert w.az("alice").check("file.read", w.mkt, node)  # ws_admin holds ws.restricted.access by name
    T.add_acl(s, w.az("alice"), w.mkt.id, "workspace", w.mkt.id, "user", w.u["bob"].id, "allow", ["file.read"])
    assert not w.az("bob").check("file.read", w.mkt, node)  # an allow above the restriction does not open it
    T.add_acl(s, w.az("alice"), w.mkt.id, "folder", "fo_board", "user", w.u["bob"].id, "allow", ["file.read"])
    assert w.az("bob").check("file.read", w.mkt, node)  # at the restricted node it does
    assert not w.az("bob").check("file.write", w.mkt, node)
    assert not w.az("carol").check("file.read", w.mkt, node)


def test_owner_only_files_are_denied_to_everyone_else_by_default(w, s):
    """file_access == "owner": roles, folder rules, workspace rules, restricted access and org admins never reach another
    person's file. Only an allow rule naming that file does, and a deny still wins."""
    T.set_workspace_member(s, w.az("alice"), w.mkt.id, "user", w.u["carol"].id, "editor")
    mine = [Node("file", "fi_c", owner=w.u["carol"].id), Node("folder", "fo_1")]
    shared = lambda who, perm="file.read": w.az(who).check(perm, w.mkt, mine).allowed
    assert shared("bob") and shared("alice") and shared("carol")  # default: the workspace's roles decide
    T.update_workspace(s, w.az("alice"), w.mkt.id, policy={"file_access": "owner"})
    for perm in ("file.read", "file.download", "file.write", "file.delete", "file.restore"):
        assert shared("carol", perm) == w.az("carol").check(perm, w.mkt).allowed  # the owner keeps what her role gave her
        assert not shared("bob", perm) and not shared("alice", perm) and not shared("owner", perm) and not shared("admin", perm)
    assert "owner-only" in w.az("alice").check("file.read", w.mkt, mine).trace[-1]
    # a file of unknown owner belongs to nobody: deny by default
    assert not w.az("alice").check("file.read", w.mkt, [Node("file", "fi_x")])
    # broader rules do not open it
    T.add_acl(s, w.az("alice"), w.mkt.id, "workspace", w.mkt.id, "user", w.u["bob"].id, "allow", ["file.read"])
    T.add_acl(s, w.az("alice"), w.mkt.id, "folder", "fo_1", "user", w.u["bob"].id, "allow", ["file.read"])
    assert not shared("bob")
    # a rule naming the file opens exactly the permission it names
    T.add_acl(s, w.az("alice"), w.mkt.id, "file", "fi_c", "user", w.u["bob"].id, "allow", ["file.read"])
    assert shared("bob") and not shared("bob", "file.download") and not shared("bob", "file.write")
    T.add_acl(s, w.az("alice"), w.mkt.id, "file", "fi_c", "user", w.u["bob"].id, "deny", ["file.read"])
    assert not shared("bob")
    # the owner's own files are unaffected, and other workspaces keep the default
    own = [Node("file", "fi_o", owner=w.u["bob"].id)]
    assert w.az("bob").check("file.read", w.mkt, own).allowed
    assert not w.az("bob").check("file.read", w.mkt, [Node("file", "fi_a", owner=w.u["alice"].id)]).allowed
    # turning the rule off restores role-based access, and a bad value is refused
    with pytest.raises(T.TenancyError):
        T.update_workspace(s, w.az("alice"), w.mkt.id, policy={"file_access": "everyone"})
    T.update_workspace(s, w.az("alice"), w.mkt.id, policy={})
    assert w.az("alice").check("file.read", w.mkt, [Node("file", "fi_z", owner=w.u["carol"].id)]).allowed


def test_archived_workspace_is_read_only(w, s):
    T.update_workspace(s, w.az("alice"), w.mkt.id, archived=True)
    assert w.az("bob").check("file.read", w.mkt) and not w.az("alice").check("file.write", w.mkt)


# ---- agents ---------------------------------------------------------------------------------------------
def test_an_agent_acts_with_the_intersection_of_authority(w, s):
    """User authority, agent policy and workspace policy must all allow; a revoked user stops the agent."""
    user_says = w.az("bob").check("file.write", w.mkt)
    agent_says = authz.Decision(True, "file.write", "agent policy allows")
    assert not authz.intersect(user_says, agent_says)  # a viewer cannot lend an agent write access
    T.set_workspace_member(s, w.az("alice"), w.mkt.id, "user", w.u["bob"].id, "editor")
    assert authz.intersect(w.az("bob").check("file.write", w.mkt), agent_says)
    T.remove_workspace_member(s, w.az("alice"), w.mkt.id, "user", w.u["bob"].id)
    assert not authz.intersect(w.az("bob").check("file.write", w.mkt), agent_says)  # recomputed at execution time


# ---- safety rails and audit ----------------------------------------------------------------------------
def test_last_owner_and_last_workspace_administrator_cannot_be_removed(w, s):
    o = w.az("owner")
    with pytest.raises(T.TenancyError) as e:
        T.remove_member(s, o, w.acme.id, w.u["owner"].id)
    assert e.value.code == "last_owner"
    with pytest.raises(T.TenancyError):
        T.set_member(s, o, w.acme.id, w.u["owner"].id, role_keys=["member"])
    with pytest.raises(T.TenancyError):
        T.set_member(s, o, w.acme.id, w.u["owner"].id, status="suspended")
    with pytest.raises(T.TenancyError) as e:
        T.remove_workspace_member(s, w.az("alice"), w.mkt.id, "user", w.u["alice"].id)
    assert e.value.code == "last_admin"


def test_removing_a_member_removes_their_access_everywhere(w, s):
    T.add_acl(s, w.az("alice"), w.mkt.id, "workspace", w.mkt.id, "user", w.u["bob"].id, "allow", ["file.write"])
    T.remove_member(s, w.az("owner"), w.acme.id, w.u["bob"].id)
    assert not w.az("bob").check("file.read", w.mkt)
    assert s.execute(select(AclEntry).where(AclEntry.principal_id == w.u["bob"].id)).first() is None


def test_every_change_is_audited_with_its_organisation(w, s):
    T.set_workspace_member(s, w.az("alice"), w.mkt.id, "user", w.u["carol"].id, "viewer")
    ev = s.execute(select(AuditEvent).where(AuditEvent.action == "ws.member.set")).scalars().all()
    assert ev and all(e.org_id == w.acme.id for e in ev)
    assert not s.execute(select(AuditEvent).where(AuditEvent.org_id == w.globex.id, AuditEvent.action == "ws.member.set")).first()


def test_unknown_permissions_and_quota(w, s):
    assert not w.az("alice").check("nope.x", w.mkt).allowed
    with pytest.raises(T.TenancyError):
        T.add_acl(s, w.az("alice"), w.mkt.id, "workspace", w.mkt.id, "user", w.u["bob"].id, "allow", ["nope.x"])
    w.acme.quotas = {"workspaces": 1}
    with pytest.raises(T.TenancyError) as e:
        T.create_workspace(s, w.az("alice"), w.acme.id, "Second")
    assert e.value.code == "quota"
