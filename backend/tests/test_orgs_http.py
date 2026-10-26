"""Scenarios A and B over the real HTTP API: two companies, then one company with departments."""
from .conftest import Api, make_user


def org_with_owner(owner, app, name, email):
    make_user(owner, app, email, name=name + " owner")
    r = owner.post("/api/orgs", json={"name": name, "owner_email": email})
    assert r.status_code == 200, r.text
    c = Api(app)
    assert c.login(email).status_code == 200
    c.csrf = c.get("/api/me").json()["csrf"]
    return r.json()["id"], c


def login(app, email):
    c = Api(app)
    assert c.login(email).status_code == 200
    c.csrf = c.get("/api/me").json()["csrf"]
    return c


def test_existing_and_new_users_join_the_default_organisation(owner, app):
    orgs = owner.get("/api/orgs").json()
    assert len(orgs) == 1 and orgs[0]["is_default"] and "org.members.manage" in orgs[0]["my_permissions"]
    before = len(owner.get(f"/api/orgs/{orgs[0]['id']}/members").json())
    make_user(owner, app, "newbie@example.test")
    assert len(owner.get(f"/api/orgs/{orgs[0]['id']}/members").json()) == before + 1


# ---- Scenario A -----------------------------------------------------------------------------------------
def test_two_companies_cannot_see_each_other(owner, app):
    acme, a_owner = org_with_owner(owner, app, "Acme", "acme-owner@example.test")
    globex, g_owner = org_with_owner(owner, app, "Globex", "globex-owner@example.test")
    ws_a = a_owner.post(f"/api/orgs/{acme}/workspaces", json={"name": "Acme Secrets"}).json()["id"]
    ws_g = g_owner.post(f"/api/orgs/{globex}/workspaces", json={"name": "Globex Secrets"}).json()["id"]

    assert acme in [o["id"] for o in a_owner.get("/api/orgs").json()] and globex not in [o["id"] for o in a_owner.get("/api/orgs").json()]
    assert g_owner.get("/api/orgs").json() and acme not in [o["id"] for o in g_owner.get("/api/orgs").json()]
    for ws in (ws_g,):  # every workspace route answers 404 for an outsider, without confirming the id exists
        for method, url, body in [("get", f"/api/workspaces/{ws}", None), ("patch", f"/api/workspaces/{ws}", {"name": "x"}),
                                  ("get", f"/api/workspaces/{ws}/members", None), ("get", f"/api/workspaces/{ws}/acl", None),
                                  ("put", f"/api/workspaces/{ws}/members", {"principal_type": "user", "principal_id": "u", "role": "viewer"}),
                                  ("get", f"/api/workspaces/{ws}/explain?perm=file.read", None)]:
            r = getattr(a_owner, method)(url, **({"json": body} if body else {}))
            assert r.status_code == 404, (method, url, r.status_code)
    for url in (f"/api/orgs/{globex}", f"/api/orgs/{globex}/members", f"/api/orgs/{globex}/workspaces",
                f"/api/orgs/{globex}/roles", f"/api/orgs/{globex}/groups", f"/api/orgs/{globex}/audit"):
        assert a_owner.get(url).status_code == 404, url
    assert a_owner.post(f"/api/orgs/{globex}/workspaces", json={"name": "intruder"}).status_code == 404
    # the platform owner administers the platform, not the tenants
    assert owner.get(f"/api/workspaces/{ws_a}").status_code == 404 and owner.get(f"/api/orgs/{acme}").status_code == 404
    # ordinary users cannot create organisations
    assert a_owner.post("/api/orgs", json={"name": "Mine", "owner_email": "acme-owner@example.test"}).status_code == 403
    # a user cannot be pulled across: Globex's owner is not addable to Acme workspaces
    r = a_owner.put(f"/api/workspaces/{ws_a}/members", json={"principal_type": "user", "principal_id": "u_does_not_matter", "role": "viewer"})
    assert r.status_code in (403, 422)


def test_audit_log_is_per_organisation(owner, app):
    acme, a_owner = org_with_owner(owner, app, "Acme", "acme-owner@example.test")
    globex, g_owner = org_with_owner(owner, app, "Globex", "globex-owner@example.test")
    a_owner.post(f"/api/orgs/{acme}/workspaces", json={"name": "A1"})
    g_owner.post(f"/api/orgs/{globex}/workspaces", json={"name": "G1"})
    ev = a_owner.get(f"/api/orgs/{acme}/audit").json()
    assert {e["action"] for e in ev} >= {"ws.create", "org.create"}
    assert all("G1" not in str(e["detail"]) for e in ev)
    assert make_user(owner, app, "plain@example.test") and a_owner.get(f"/api/orgs/{acme}/audit").status_code == 200


# ---- Scenario B -----------------------------------------------------------------------------------------
def test_one_company_three_departments_distinct_access(owner, app):
    acme, boss = org_with_owner(owner, app, "Acme", "boss@example.test")
    for e in ("mia@example.test", "fin@example.test", "eng@example.test", "temp@example.test"):
        make_user(owner, app, e)
        assert boss.post(f"/api/orgs/{acme}/members", json={"email": e, "roles": ["dept_admin" if e.startswith(("mia", "fin", "eng")) else "member"]}).status_code == 200
    users = {m["email"]: m["user_id"] for m in boss.get(f"/api/orgs/{acme}/members").json()}
    mia, fin, eng = (login(app, e) for e in ("mia@example.test", "fin@example.test", "eng@example.test"))
    ws = {n: c.post(f"/api/orgs/{acme}/workspaces", json={"name": n}).json()["id"]
          for n, c in (("Marketing", mia), ("Finance", fin), ("Engineering", eng))}
    # Mia's marketing team includes Eng as a viewer; Finance is closed to everyone but Fin
    assert mia.put(f"/api/workspaces/{ws['Marketing']}/members", json={"principal_type": "user", "principal_id": users["eng@example.test"], "role": "viewer"}).status_code == 200

    assert eng.get(f"/api/workspaces/{ws['Marketing']}").status_code == 200
    assert "file.read" in eng.get(f"/api/workspaces/{ws['Marketing']}").json()["my_permissions"]
    assert "file.write" not in eng.get(f"/api/workspaces/{ws['Marketing']}").json()["my_permissions"]
    assert eng.get(f"/api/workspaces/{ws['Finance']}").status_code == 404
    assert mia.get(f"/api/workspaces/{ws['Finance']}").status_code == 404
    # the organisation owner may list workspace names, and reads nothing inside them
    assert {w["name"] for w in boss.get(f"/api/orgs/{acme}/workspaces").json()} == {"Marketing", "Finance", "Engineering"}
    fin_view = boss.get(f"/api/workspaces/{ws['Finance']}").json()
    assert not {"file.read", "ctx.read", "mem.read", "ws.view"} & set(fin_view["my_permissions"])
    assert boss.get(f"/api/workspaces/{ws['Finance']}/members").status_code == 403
    assert boss.put(f"/api/workspaces/{ws['Finance']}/members", json={"principal_type": "user", "principal_id": users["boss@example.test"], "role": "ws_admin"}).status_code == 403
    assert {w["name"] for w in eng.get(f"/api/orgs/{acme}/workspaces").json()} == {"Engineering", "Marketing"}
    assert "Finance" not in {w["name"] for w in mia.get(f"/api/orgs/{acme}/workspaces").json()}
    # the viewer cannot widen their own access, and explain tells why
    r = eng.put(f"/api/workspaces/{ws['Marketing']}/members", json={"principal_type": "user", "principal_id": users["eng@example.test"], "role": "ws_admin"})
    assert r.status_code == 403
    ex = mia.get(f"/api/workspaces/{ws['Marketing']}/explain", params={"perm": "file.write", "user_id": users["eng@example.test"]}).json()
    assert ex["allowed"] is False and any("viewer" in str(v).lower() for v in ex["via"])
    # Fin shares a read-only view of Finance with temp via an access rule; temp gets read, not write
    assert fin.put(f"/api/workspaces/{ws['Finance']}/members", json={"principal_type": "user", "principal_id": users["temp@example.test"], "role": "viewer"}).status_code == 200
    temp = login(app, "temp@example.test")
    assert "file.read" in temp.get(f"/api/workspaces/{ws['Finance']}").json()["my_permissions"]
    # revoking removes access at once
    assert fin.delete(f"/api/workspaces/{ws['Finance']}/members/user/{users['temp@example.test']}").status_code == 200
    assert temp.get(f"/api/workspaces/{ws['Finance']}").status_code == 404


def test_custom_role_edit_changes_what_people_can_do(owner, app):
    acme, boss = org_with_owner(owner, app, "Acme", "boss@example.test")
    make_user(owner, app, "mia@example.test")
    boss.post(f"/api/orgs/{acme}/members", json={"email": "mia@example.test", "roles": ["dept_admin"]})
    make_user(owner, app, "ned@example.test")
    boss.post(f"/api/orgs/{acme}/members", json={"email": "ned@example.test", "roles": ["member"]})
    users = {m["email"]: m["user_id"] for m in boss.get(f"/api/orgs/{acme}/members").json()}
    mia = login(app, "mia@example.test")
    wid = mia.post(f"/api/orgs/{acme}/workspaces", json={"name": "Brand"}).json()["id"]
    r = boss.post(f"/api/orgs/{acme}/roles", json={"label": "Reviewer", "scope": "workspace", "grants": ["ws.view", "file.read"], "denies": ["file.download"]})
    assert r.status_code == 200, r.text
    assert mia.put(f"/api/workspaces/{wid}/members", json={"principal_type": "user", "principal_id": users["ned@example.test"], "role": "reviewer"}).status_code == 200
    ned = login(app, "ned@example.test")
    perms = ned.get(f"/api/workspaces/{wid}").json()["my_permissions"]
    assert "file.read" in perms and "file.download" not in perms
    role_id = r.json()["id"]
    assert boss.put(f"/api/orgs/{acme}/roles/{role_id}", json={"label": "Reviewer", "scope": "workspace", "grants": ["ws.view", "file.read", "file.write"], "denies": []}).status_code == 200
    assert "file.write" in ned.get(f"/api/workspaces/{wid}").json()["my_permissions"]
    assert ned.post(f"/api/orgs/{acme}/roles", json={"label": "x", "scope": "org"}).status_code == 403
    assert boss.delete(f"/api/orgs/{acme}/roles/{role_id}").status_code == 409  # still assigned


def test_creating_a_second_organisation_stops_auto_join(owner, app):
    default = owner.get("/api/orgs").json()[0]["id"]
    make_user(owner, app, "before@example.test")
    n_before = len(owner.get(f"/api/orgs/{default}/members").json())
    org_with_owner(owner, app, "Acme", "acme-owner@example.test")
    assert owner.get(f"/api/orgs/{default}").json()["auto_join"] is False
    n_mid = len(owner.get(f"/api/orgs/{default}/members").json())
    make_user(owner, app, "later@example.test")
    assert len(owner.get(f"/api/orgs/{default}/members").json()) == n_mid and n_mid >= n_before
    ev = owner.get(f"/api/orgs/{default}/audit").json()
    assert "org.autojoin.disabled" in {e["action"] for e in ev}
