from semechka import perms
from .conftest import PW, Api, make_user


def P(grants=(), denies=(), team=None, user=None, status="active"):
    src = [perms.Source("role", "r", "R", list(grants), list(denies))]
    if team:
        src.append(perms.Source("team", "t", "Team Finance", *team))
    if user:
        src.append(perms.Source("user", "u", "direct override", *user))
    return perms.Principal("u1", status, src, ["r"], [])


def test_deny_overrides_grant_at_any_level():
    p = P(grants=["mode.chat", "mode.image"], team=([], ["mode.image"]))
    assert p.can("mode.chat") and not p.can("mode.image")
    assert "Team Finance" in p.decide("mode.image").reason
    # even a direct user grant cannot beat a team deny
    p = P(grants=["mode.chat"], team=([], ["mode.image"]), user=(["mode.image"], []))
    assert not p.can("mode.image")


def test_no_grant_means_denied_and_wildcards():
    assert not P().can("mode.chat")
    p = P(grants=["tech.*"], denies=["tech.resources.*"])
    assert p.can("tech.perf.tps") and not p.can("tech.resources.gpu")


def test_star_grants_everything_except_sensitive():
    assert all(P(grants=["*"]).can(x.id) for x in perms.REGISTRY if not x.sensitive)
    assert not P(grants=["*"]).can("admin.content.read")


def test_no_wildcard_grants_content_access_but_wildcard_denies_apply():
    assert perms.BY_ID["admin.content.read"].sensitive
    assert not P(grants=["admin.*"]).can("admin.content.read")
    assert P(grants=["admin.content.read"]).can("admin.content.read")
    assert not P(grants=["admin.content.read"], denies=["admin.*"]).can("admin.content.read")


def test_inactive_user_holds_nothing():
    assert not P(grants=["*"], status="suspended").can("mode.chat")


def test_unknown_permission_is_denied():
    assert not P(grants=["*"]).can("nonsense.perm")


def test_member_cannot_reach_admin_api(owner, app):
    m = make_user(owner, app, "m@example.test")
    for path in ("/api/admin/users", "/api/admin/settings", "/api/admin/audit", "/api/admin/models", "/api/admin/system",
                 "/api/admin/backends", "/api/admin/roles", "/api/admin/overview"):
        assert m.get(path).status_code == 403, path
    assert m.put("/api/admin/settings/brand.app_name", json={"value": "Pwned"}).status_code == 403
    assert m.post("/api/admin/users", json={"email": "z@z.test", "display_name": "z", "password": PW}).status_code == 403


def test_roles_are_renamable_without_changing_ids(owner, app):
    roles = {r["id"]: r for r in owner.get("/api/admin/roles").json()}
    r = roles["model_operator"]
    body = {"label": "AI Technician", "description": r["description"], "rank": r["rank"], "grants": r["grants"], "denies": r["denies"]}
    assert owner.put("/api/admin/roles/model_operator", json=body).status_code == 200
    roles = {r["id"]: r for r in owner.get("/api/admin/roles").json()}
    assert roles["model_operator"]["label"] == "AI Technician"
    u = make_user(owner, app, "tech@example.test", role="model_operator")
    me = u.get("/api/me").json()
    assert me["roles"] == [{"id": "model_operator", "label": "AI Technician"}]
    assert u.get("/api/admin/models").status_code == 200


def test_no_privilege_escalation_via_roles_invites_or_overrides(owner, app):
    tm = make_user(owner, app, "tm@example.test", role="team_manager")
    # a team manager can invite members but not administrators
    assert tm.post("/api/admin/invites", json={"role_id": "member"}).status_code == 200
    assert tm.post("/api/admin/invites", json={"role_id": "platform_admin"}).status_code == 403
    assert tm.post("/api/admin/invites", json={"role_id": "owner"}).status_code == 403
    # an org admin cannot mint a role holding permissions they lack
    oa = make_user(owner, app, "oa@example.test", role="org_admin")
    r = oa.post("/api/admin/roles", json={"label": "Sneaky", "grants": ["admin.models.manage"]})
    assert r.status_code == 403  # org_admin lacks admin.roles.manage anyway
    # nobody but an owner can assign owner or touch an owner account
    ownerid = owner.get("/api/me").json()["user"]["id"]
    assert oa.patch(f"/api/admin/users/{ownerid}", json={"status": "suspended"}).status_code == 403
    assert oa.post("/api/admin/users", json={"email": "x@x.test", "display_name": "x", "password": PW, "role_id": "owner"}).status_code == 403
    # an admin with roles.manage still cannot grant what they do not hold
    owner.put("/api/admin/roles/org_admin", json={"label": "Organisation Admin", "description": "", "rank": 20,
                                                  "grants": [g for g in next(r for r in owner.get("/api/admin/roles").json() if r["id"] == "org_admin")["grants"]] + ["admin.roles.manage"], "denies": []})
    r = oa.post("/api/admin/roles", json={"label": "Sneaky2", "grants": ["admin.models.manage"]})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "escalation"
    assert oa.put(f"/api/admin/users/{oa.user_id}/override", json={"grants": ["admin.backends.manage"], "denies": []}).status_code == 403


def test_last_owner_cannot_be_removed_or_suspended(owner):
    me = owner.get("/api/me").json()["user"]["id"]
    assert owner.patch(f"/api/admin/users/{me}", json={"status": "suspended"}).status_code == 409
    assert owner.patch(f"/api/admin/users/{me}", json={"role_ids": ["member"]}).status_code == 409
    assert owner.delete(f"/api/admin/users/{me}").status_code == 409
    assert owner.put("/api/admin/roles/owner", json={"label": "Owner", "description": "", "rank": 0, "grants": ["mode.chat"], "denies": []}).status_code == 409


def test_explain_permission_names_the_rule(owner, app):
    team = owner.post("/api/admin/teams", json={"name": "Finance", "denies": ["mode.image"]}).json()["id"]
    u = make_user(owner, app, "f@example.test", teams=[team])
    ex = owner.get("/api/admin/explain/permission", params={"user_id": u.user_id, "permission": "mode.image"}).json()
    assert ex["allowed"] is False and "Finance" in ex["reason"]
    ex = owner.get("/api/admin/explain/permission", params={"user_id": u.user_id, "permission": "mode.chat"}).json()
    assert ex["allowed"] is True and "Member" in ex["reason"]


def test_bulk_requires_confirmed_count_and_distinguishes_scope(owner, app):
    for i in range(3):
        make_user(owner, app, f"bulk{i}@example.test")
    body = {"action": "suspend", "all_matching": {"q": "bulk"}, "expected_count": 2}
    assert owner.post("/api/admin/users/bulk", json=body).status_code == 409
    body["expected_count"] = 3
    r = owner.post("/api/admin/users/bulk", json=body)
    assert r.status_code == 200 and r.json()["done"] == 3
    assert owner.get("/api/admin/users", params={"status": "suspended"}).json()["total"] == 3
    # suspended people cannot sign in
    assert Api(app).login("bulk0@example.test").status_code == 403


def test_view_as_role_preview_uses_no_user_data(owner):
    r = owner.get("/api/admin/preview/member").json()
    assert "mode.chat" in r["permissions"] and "admin.users.manage" not in r["permissions"]
    assert r["ui"]["tech"]["fields"] == []
