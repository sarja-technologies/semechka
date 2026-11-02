"""Trinodal AI: the /trinodal/ same-origin proxy (permissions enforced in the backend, CSRF, headers, token secrecy,
streaming, limits, audit), the new permissions, and the Sarness-side pieces (local endpoints, run metadata filtered by
technical permission, cancellation). Runs against tests/fake_trinodal.py (the management service) and
tests/fake_sarness.py (an engine with Trinodal registered as a remote endpoint)."""
import asyncio
import json
import logging
import time

import httpx
import pytest
from semechka import perms
from semechka.adapters.sarness import SarnessAdapter, clean_trinodal
from semechka.models import AuditEvent, Message, Model, ModelGrant
from sqlalchemy import select

from . import fake_sarness as fs
from . import fake_trinodal as ft
from .conftest import PW, Api, Server, _free_port, make_user

TOKEN = ft.TOKEN
CSP = "default-src 'self'"


# ---- fixtures ---------------------------------------------------------------------------------------------------
@pytest.fixture(scope="session")
def tnl_url():
    srv = Server(ft.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


@pytest.fixture
def tnl(owner, st, tnl_url, tmp_path):
    """The owner of an install with Trinodal configured: address set, token file in place."""
    ft.reset()
    tok = tmp_path / "trinodal.token"
    tok.write_text(TOKEN + "\n")
    tok.chmod(0o600)
    st.cfg.trinodal_token_file = tok
    r = owner.put("/api/admin/settings/trinodal.url", json={"value": tnl_url, "confirm_weaker": True})
    assert r.status_code == 200, r.text
    return owner


def person(owner, app, name, grants):
    rid = owner.post("/api/admin/roles", json={"label": f"R {name}", "grants": grants}).json()["id"]
    return make_user(owner, app, f"{name}@example.test", role=rid)


def calls():
    return ft.STATE["calls"]


def up_calls():  # requests that reached the service, ignoring nothing
    return list(ft.STATE["calls"])


# ---- permissions ------------------------------------------------------------------------------------------------
def P(grants=(), denies=()):
    return perms.Principal("u1", "active", [perms.Source("role", "r", "R", list(grants), list(denies))], ["r"], [])


def test_trinodal_permissions_are_sensitive_and_no_wildcard_grants_them():
    for pid in ("trinodal.view", "trinodal.train", "trinodal.admin"):
        assert perms.BY_ID[pid].sensitive
        assert not P(grants=["*"]).can(pid)
        assert not P(grants=["*", "trinodal.*"]).can(pid)
        assert P(grants=[pid]).can(pid)
        assert not P(grants=[pid], denies=["trinodal.*"]).can(pid)  # a wildcard deny does cover them
    assert P(grants=["trinodal.view"]).can("trinodal.view") and not P(grants=["trinodal.view"]).can("trinodal.train")


def test_role_templates_grant_trinodal_to_owner_and_platform_admin_only():
    held = {t["id"]: {p for p in ("trinodal.view", "trinodal.train", "trinodal.admin")
                      if P(grants=t["grants"], denies=t["denies"]).can(p)} for t in perms.ROLE_TEMPLATES}
    assert held["owner"] == {"trinodal.view", "trinodal.train", "trinodal.admin"}
    assert held["platform_admin"] == {"trinodal.view", "trinodal.train"}
    assert all(not v for k, v in held.items() if k not in ("owner", "platform_admin"))


def test_fresh_install_owner_and_platform_admin_hold_them(owner, app):
    mine = set(owner.get("/api/me").json()["permissions"])
    assert {"trinodal.view", "trinodal.train", "trinodal.admin"} <= mine
    pa = make_user(owner, app, "pa@example.test", role="platform_admin")
    got = set(pa.get("/api/me").json()["permissions"])
    assert {"trinodal.view", "trinodal.train"} <= got and "trinodal.admin" not in got
    member = make_user(owner, app, "m@example.test")
    assert not {p for p in member.get("/api/me").json()["permissions"] if p.startswith("trinodal.")}


def test_upgrade_changes_nobody_s_access_and_owner_grants_through_the_audited_role_edit(owner, app, st, cfg):
    """An instance created before 0.5.8 has built-in roles without the new permissions. Starting 0.5.8 on it adds nothing:
    roles are seeded once. The owner then grants them by name through the ordinary role edit, which is audited."""
    make_user(owner, app, "pa@example.test", role="platform_admin")
    with st.db.session(write=True) as s:  # what a 0.5.7 database holds
        from semechka.models import Role
        for rid in ("owner", "platform_admin"):
            r = s.get(Role, rid)
            r.grants = [g for g in r.grants if not g.startswith("trinodal.")]
    from semechka.app import create_app
    app2 = create_app(cfg)  # "start the new version on the old data"
    o2 = Api(app2)
    assert o2.login("owner@example.test").status_code == 200
    roles = {r["id"]: r for r in o2.get("/api/admin/roles").json()}
    assert not any(g.startswith("trinodal.") for g in roles["owner"]["grants"] + roles["platform_admin"]["grants"])
    assert not {p for p in o2.get("/api/me").json()["permissions"] if p.startswith("trinodal.")}
    p2 = Api(app2)
    assert p2.login("pa@example.test").status_code == 200
    assert not {p for p in p2.get("/api/me").json()["permissions"] if p.startswith("trinodal.")}
    # the explicit path
    body = roles["platform_admin"]
    r = o2.put("/api/admin/roles/platform_admin", json={"label": body["label"], "description": body["description"],
                                                         "rank": body["rank"], "grants": body["grants"] + ["trinodal.view"],
                                                         "denies": body["denies"]})
    assert r.status_code == 200, r.text
    assert "trinodal.view" in p2.get("/api/me").json()["permissions"]
    own = roles["owner"]
    assert o2.put("/api/admin/roles/owner", json={"label": own["label"], "description": own["description"], "rank": own["rank"],
                                                   "grants": own["grants"] + ["trinodal.view", "trinodal.train", "trinodal.admin"],
                                                   "denies": []}).status_code == 200
    assert "trinodal.admin" in o2.get("/api/me").json()["permissions"]
    with st.db.session() as s:
        ev = [e for e in s.execute(select(AuditEvent).where(AuditEvent.action == "role.update")).scalars()]
    assert {e.target for e in ev} >= {"platform_admin", "owner"}
    assert any("trinodal.view" in json.dumps(e.detail) for e in ev)


def test_settings_for_trinodal_need_trinodal_admin(owner, app):
    pa = make_user(owner, app, "pa@example.test", role="platform_admin")  # settings.edit but not trinodal.admin
    r = pa.put("/api/admin/settings/trinodal.url", json={"value": "http://127.0.0.1:1", "confirm_weaker": True})
    assert r.status_code == 403 and r.json()["detail"]["permission"] == "trinodal.admin"
    for bad in ("http://user:pw@127.0.0.1:8800", "http://127.0.0.1:8800/path", "ftp://x", "http://h?x=1"):
        r = owner.put("/api/admin/settings/trinodal.url", json={"value": bad, "confirm_weaker": True})
        assert r.status_code == 422, bad
    assert owner.put("/api/admin/settings/trinodal.url", json={"value": "http://127.0.0.1:8800"}).status_code == 409  # needs confirmation
    r = owner.put("/api/admin/settings/trinodal.url", json={"value": "http://127.0.0.1:8800/", "confirm_weaker": True})
    assert r.status_code == 200
    assert owner.get("/api/admin/settings", params={"q": "trinodal.url"}).json()["settings"][0]["value"] == "http://127.0.0.1:8800"


# ---- availability and the feature switch ------------------------------------------------------------------------
def test_feature_off_is_404_for_everyone_signed_in(owner, app):
    for path in ("/trinodal/", "/trinodal", "/trinodal/api/network", "/trinodal/_semechka/session"):
        assert owner.get(path, follow_redirects=False).status_code == 404, path
    assert owner.post("/trinodal/api/training/jobs", json={}).status_code == 404
    assert owner.get("/api/trinodal/availability").json() == {"enabled": False, "view": False, "train": False, "admin": False}


def test_availability_is_permission_based_and_reveals_no_address(tnl, app, tnl_url):
    owner = tnl
    a = owner.get("/api/trinodal/availability")
    assert a.json() == {"enabled": True, "view": True, "train": True, "admin": True}
    assert tnl_url not in a.text and TOKEN not in a.text and "127.0.0.1" not in a.text
    v = person(owner, app, "viewer", ["trinodal.view"])
    assert v.get("/api/trinodal/availability").json() == {"enabled": True, "view": True, "train": False, "admin": False}
    t = person(owner, app, "trainer", ["trinodal.train"])
    assert t.get("/api/trinodal/availability").json() == {"enabled": True, "view": False, "train": True, "admin": False}
    nobody = make_user(owner, app, "nobody@example.test")
    assert nobody.get("/api/trinodal/availability").json() == {"enabled": False, "view": False, "train": False, "admin": False}
    assert Api(app).get("/api/trinodal/availability").status_code == 401


def test_signed_out_gets_401_or_the_sign_in_page(tnl, app):
    anon = Api(app)
    r = anon.get("/trinodal/api/network")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthenticated"
    r = anon.get("/trinodal/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=")
    assert anon.post("/trinodal/api/training/jobs", json={}).status_code == 401
    assert calls() == []


def test_bare_address_redirects_to_the_slash_form(tnl):
    r = tnl.get("/trinodal?x=1", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/trinodal/?x=1"


# ---- the permission matrix, enforced by the backend alone ---------------------------------------------------------
REQUESTS = [  # method, path, class
    ("GET", "/trinodal/", "read"), ("HEAD", "/trinodal/", "read"), ("GET", "/trinodal/app.js", "read"),
    ("GET", "/trinodal/api/network", "read"), ("GET", "/trinodal/api/training/jobs", "read"),
    ("POST", "/trinodal/api/training/jobs", "train"), ("POST", "/trinodal/api/datasets", "train"),
    ("PUT", "/trinodal/api/checkpoints/ck1", "train"), ("DELETE", "/trinodal/api/jobs/j1", "train"),
    ("POST", "/trinodal/api/deploy", "train"), ("POST", "/trinodal/api/evaluate", "train"),
    ("PATCH", "/trinodal/api/other", "train"),
    ("GET", "/trinodal/api/admin/tokens", "admin"), ("POST", "/trinodal/api/admin/settings", "admin"),
    ("DELETE", "/trinodal/api/admin/tokens/t1", "admin"),
]
HOLDERS = {"viewer": ["trinodal.view"], "trainer": ["trinodal.train"], "operator": ["trinodal.admin"],
           "viewtrain": ["trinodal.view", "trinodal.train"], "everything": ["trinodal.view", "trinodal.train", "trinodal.admin"]}
NEEDS = {"read": "trinodal.view", "train": "trinodal.train", "admin": "trinodal.admin"}


@pytest.mark.parametrize("who", [*HOLDERS, "nobody"])
def test_every_request_class_is_allowed_or_denied_by_the_backend(tnl, app, who):
    owner = tnl
    api = make_user(owner, app, "nobody@example.test") if who == "nobody" else person(owner, app, who, HOLDERS[who])
    held = set(HOLDERS.get(who, []))
    for method, path, klass in REQUESTS:
        before = len(calls())
        r = api.req(method, path, **({"json": {"x": 1}} if method in ("POST", "PUT", "PATCH") else {}))
        if NEEDS[klass] in held:
            assert r.status_code in (200, 202), (who, method, path, r.status_code, r.text)
            assert len(calls()) == before + 1
        else:
            assert r.status_code == 403, (who, method, path, r.status_code)
            if method != "HEAD":
                assert r.json()["detail"]["permission"] == NEEDS[klass]
            assert len(calls()) == before, "a refused request must never reach the service"


def test_a_deny_rule_beats_the_grant_and_a_team_can_grant(tnl, app):
    owner = tnl
    p = person(owner, app, "mixed", ["trinodal.view", "trinodal.train"])
    owner.put(f"/api/admin/users/{p.user_id}/override", json={"grants": [], "denies": ["trinodal.train"]})
    assert p.get("/trinodal/api/network").status_code == 200
    assert p.post("/trinodal/api/training/jobs", json={}).status_code == 403
    team = owner.post("/api/admin/teams", json={"name": "Researchers", "grants": ["trinodal.view"], "denies": []}).json()["id"]
    q = make_user(owner, app, "team@example.test", teams=[team])
    assert q.get("/trinodal/api/network").status_code == 200


def test_ordinary_roles_cannot_grant_what_they_do_not_hold(tnl, app):
    owner = tnl
    mgr = person(owner, app, "rolemgr", ["admin.overview", "admin.users.view", "admin.roles.manage", "trinodal.view"])
    r = mgr.post("/api/admin/roles", json={"label": "X", "grants": ["trinodal.train"]})
    assert r.status_code == 403
    assert mgr.post("/api/admin/roles", json={"label": "Y", "grants": ["trinodal.view"]}).status_code == 200


def test_odd_paths_are_refused_before_any_decision_is_made(tnl):
    owner = tnl
    for path in ("/trinodal/api/%2e%2e/api/admin/tokens", "/trinodal/api/%2e/network", "/trinodal//api/admin/tokens",
                 "/trinodal/api%5Cadmin", "/trinodal/api/%00"):
        assert owner.get(path).status_code == 400, path
    assert calls() == []


def test_admin_class_covers_reading_and_odd_spellings_for_everyone_but_the_holder(tnl, app):
    owner = tnl
    v = person(owner, app, "viewer", ["trinodal.view", "trinodal.train"])
    for path in ("/trinodal/api/admin/tokens", "/trinodal/api/Admin/tokens", "/trinodal/api/admin", "/trinodal/api/admin%2Ftokens",
                 "/trinodal/api/admin;x/tokens", "/trinodal/API/ADMIN/tokens"):
        r = v.get(path)
        assert r.status_code in (403, 404), (path, r.status_code)
        assert not [c for c in calls() if "admin" in c["path"].lower()], path


# ---- CSRF and same-origin on every change ---------------------------------------------------------------------------
def test_changes_need_the_csrf_token_and_a_same_origin_request(tnl):
    owner = tnl
    url = "/trinodal/api/training/jobs"
    n = len(calls())
    r = owner.c.post(url, json={"a": 1})  # no token
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    r = owner.c.post(url, json={"a": 1}, headers={"x-csrf-token": "wrong"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    r = owner.c.post(url, json={"a": 1}, headers={"x-csrf-token": owner.csrf, "origin": "https://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin"
    r = owner.c.post(url, json={"a": 1}, headers={"x-csrf-token": owner.csrf, "origin": "null"})
    assert r.status_code == 403
    assert len(calls()) == n, "nothing reached the service"
    other = Api(owner.c.app)
    assert other.login("owner@example.test").status_code == 200
    r = owner.c.post(url, json={"a": 1}, headers={"x-csrf-token": other.csrf})  # another session's token
    assert r.status_code == 403
    r = owner.c.post(url, json={"a": 1}, headers={"x-csrf-token": owner.csrf, "origin": "http://testserver"})
    assert r.status_code == 202 and len(calls()) == n + 1
    assert owner.c.get("/trinodal/api/network").status_code == 200  # reads need no token
    assert owner.c.get("/trinodal/api/network", headers={"origin": "https://evil.example"}).status_code == 200  # same as every GET


def test_the_ui_reads_its_csrf_token_and_scopes_from_semechka_itself(tnl, app):
    owner = tnl
    v = person(owner, app, "viewer", ["trinodal.view"])
    me = v.get("/api/me").json()
    s = v.get("/trinodal/_semechka/session")
    assert s.status_code == 200
    j = s.json()
    assert j["csrf"] == me["csrf"] and j["scopes"] == ["read"] and j["user"]["id"] == v.user_id and j["instance"]
    assert not calls(), "answered by Semechka, never forwarded"
    assert make_user(owner, app, "n@example.test").get("/trinodal/_semechka/session").status_code == 403
    assert Api(app).get("/trinodal/_semechka/session").status_code == 401
    assert v.get("/trinodal/_semechka/other").status_code == 404 and not calls()


# ---- what crosses the proxy ---------------------------------------------------------------------------------------------
def test_service_headers_are_set_from_server_side_facts_and_nothing_else_crosses(tnl, app, st):
    owner = tnl
    pa = make_user(owner, app, "pa@example.test", role="platform_admin")
    forged = {"Authorization": "Bearer attacker", "X-Trinodal-Actor": "evil/root", "X-Trinodal-Scopes": "admin",
              "X-CSRF-Token": pa.csrf, "Origin": "http://testserver", "Referer": "http://x/",
              "X-Forwarded-For": "6.6.6.6", "Proxy-Authorization": "Basic zzz", "Connection": "keep-alive, x-private",
              "X-Private": "1", "Accept": "application/json"}
    r = pa.req("POST", "/trinodal/api/training/jobs?name=a%20b&x=1", json={"hello": "world"}, headers=forged)
    assert r.status_code == 202 and r.json()["received"] > 0, r.text
    h = calls()[-1]["headers"]
    assert h["authorization"] == f"Bearer {TOKEN}"
    assert h["x-trinodal-actor"] == f"{pa_instance(st)}/{pa.user_id}"
    assert h["x-trinodal-scopes"] == "read,train"
    assert h["accept"] == "application/json" and h["content-type"].startswith("application/json")
    for dropped in ("cookie", "x-csrf-token", "origin", "referer", "x-forwarded-for", "proxy-authorization", "x-private"):
        assert dropped not in h, dropped
    assert calls()[-1]["query"] == "name=a%20b&x=1"
    owner_scopes = owner.get("/trinodal/api/network") and calls()[-1]["headers"]["x-trinodal-scopes"]
    assert owner_scopes == "read,train,admin"
    v = person(owner, app, "viewer", ["trinodal.view"])
    v.get("/trinodal/api/network")
    assert calls()[-1]["headers"]["x-trinodal-scopes"] == "read"
    assert calls()[-1]["headers"]["x-trinodal-actor"].endswith("/" + v.user_id)


def pa_instance(st):
    from semechka import trinodal_svc
    return trinodal_svc.instance_name(st)


def test_instance_name_comes_from_the_deployment_or_the_instance_key(tnl, st):
    from semechka import trinodal_svc
    derived = trinodal_svc.instance_name(st)
    assert derived.startswith("smk-") and len(derived) == 12 and derived == trinodal_svc.instance_name(st)
    st.cfg.instance_name = "roasted"
    tnl.get("/trinodal/api/network")
    assert calls()[-1]["headers"]["x-trinodal-actor"].startswith("roasted/")
    st.cfg.instance_name = "bad name/with:odd*chars"
    assert trinodal_svc.instance_name(st) == "badnamewithoddchars"


def test_responses_lose_cookies_csp_cors_and_banners_and_keep_semechkas_security_headers(tnl):
    r = tnl.get("/trinodal/")
    assert r.status_code == 200 and "Trinodal" in r.text
    h = {k.lower(): v for k, v in r.headers.items()}
    for gone in ("set-cookie", "access-control-allow-origin", "access-control-allow-credentials", "server",
                 "strict-transport-security", "x-secret-internal"):
        assert gone not in h or (gone == "server" and "trinodal" not in h[gone]), gone
    assert "trinodal=1" not in r.headers.get("set-cookie", "")
    assert tnl.c.cookies.get("trinodal") is None
    assert h["x-trinodal-build"] == "7"  # an explicitly allowed header
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    csp = h["content-security-policy"]
    assert csp == tnl.get("/api/me").headers["content-security-policy"], "Semechka's own CSP, unchanged"
    assert "unsafe-inline' *" not in csp and "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert h["cache-control"].startswith("private")  # never "public" for a page behind a sign-in
    assert r.headers["etag"] == '"idx1"'
    api = tnl.get("/trinodal/api/network")
    assert api.headers["cache-control"] == "no-store" and api.json()["nodes"][0]["id"] == "conversation"
    head = tnl.req("HEAD", "/trinodal/")
    assert head.status_code == 200 and head.headers["content-length"] == "321" and head.content == b""


def test_redirects_stay_inside_trinodal(tnl):
    r = tnl.get("/trinodal/redir-in", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/trinodal/api/network"
    r = tnl.get("/trinodal/redir-out", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/trinodal/"


def test_the_service_refusing_the_token_is_not_a_signed_out_person(tnl):
    r = tnl.get("/trinodal/api/reject")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "trinodal_auth"
    assert TOKEN not in r.text
    r = tnl.get("/trinodal/api/forbidden")
    assert r.status_code == 403  # the service's own refusal passes through as it is


def test_missing_or_malformed_token_file_stops_the_proxy_without_naming_the_path(tnl, st, tmp_path):
    st.cfg.trinodal_token_file = tmp_path / "does-not-exist"
    r = tnl.get("/trinodal/api/network")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "trinodal_not_configured"
    assert "does-not-exist" not in r.text and not calls()
    bad = tmp_path / "bad"
    bad.write_text("two words\nsecond line\n")
    st.cfg.trinodal_token_file = bad
    assert tnl.get("/trinodal/api/network").status_code == 503
    good = tmp_path / "good"
    good.write_text("  " + TOKEN + "  \n")
    st.cfg.trinodal_token_file = good
    assert tnl.get("/trinodal/api/network").status_code == 200
    good.write_text("rotated-token-value-1\n")  # rotation takes effect without a restart
    assert tnl.get("/trinodal/api/network").status_code == 502  # the service no longer accepts it: reported, not leaked


def test_default_token_file_is_in_the_data_directory(tnl, st, cfg):
    st.cfg.trinodal_token_file = None
    assert st.cfg.trinodal_token_path == cfg.data_dir / "trinodal-token"
    assert tnl.get("/trinodal/api/network").status_code == 503
    (cfg.data_dir / "trinodal-token").write_text(TOKEN)
    assert tnl.get("/trinodal/api/network").status_code == 200


def test_unreachable_service_is_a_clean_502(tnl, st):
    tnl.put("/api/admin/settings/trinodal.url", json={"value": "http://127.0.0.1:9", "confirm_weaker": True})
    r = tnl.get("/trinodal/api/network")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "trinodal_unreachable"
    assert "127.0.0.1" not in r.text


# ---- the token never leaks --------------------------------------------------------------------------------------------
def test_the_service_token_appears_nowhere(tnl, app, st, cfg, caplog):
    caplog.set_level(logging.DEBUG)
    owner = tnl
    seen = []
    for method, path, _k in REQUESTS:
        r = owner.req(method, path, **({"json": {"x": 1}} if method in ("POST", "PUT", "PATCH") else {}))
        seen.append(r.text + json.dumps(dict(r.headers)))
    for path in ("/api/me", "/api/trinodal/availability", "/api/admin/settings", "/api/admin/settings-export",
                 "/api/admin/audit", "/api/admin/settings-history", "/api/admin/backends", "/api/admin/roles",
                 "/trinodal/_semechka/session", "/trinodal/api/reject"):
        seen.append(owner.get(path).text)
    assert not any(TOKEN in x for x in seen)
    with st.db.session() as s:
        rows = json.dumps([[e.actor, e.action, e.target, e.detail] for e in s.execute(select(AuditEvent)).scalars()])
    assert TOKEN not in rows and str(cfg.data_dir) not in rows
    for f in cfg.data_dir.iterdir():
        if f.is_file() and f.name != "trinodal-token":
            assert TOKEN.encode() not in f.read_bytes(), f.name
    assert TOKEN not in caplog.text
    # the token is not a setting of any kind
    assert not [d for d in __import__("semechka.settings_reg", fromlist=["DEFS"]).DEFS if "token" in d.id and d.id.startswith("trinodal")]


# ---- limits, rate limit, audit ----------------------------------------------------------------------------------------
def test_upload_and_download_limits(tnl):
    owner = tnl
    assert owner.put("/api/admin/settings/trinodal.max_upload_mb", json={"value": 1}).status_code == 200
    assert owner.put("/api/admin/settings/trinodal.max_download_mb", json={"value": 1}).status_code == 200
    n = len(calls())
    r = owner.post("/trinodal/api/datasets", content=b"z" * (1024 * 1024 + 1), headers={"content-type": "application/octet-stream"})
    assert r.status_code == 413 and len(calls()) == n, "declared size over the limit: refused before any request"
    r = owner.post("/trinodal/api/datasets", content=b"z" * 1024 * 1024, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 202 and r.json()["received"] == 1024 * 1024

    def chunks():  # no Content-Length: counted as it arrives
        for _ in range(40):
            yield b"z" * 65536
    r = owner.post("/trinodal/api/datasets", content=chunks(), headers={"content-type": "application/octet-stream"})
    assert r.status_code == 413
    r = owner.get("/trinodal/big")  # 2 MB declared
    assert r.status_code == 502 and r.json()["detail"]["code"] == "too_large"
    try:
        got = owner.get("/trinodal/big-stream")  # 4 MB with no Content-Length: cut off at the limit
        assert len(got.content) <= 1024 * 1024 + 65536
    except httpx.HTTPError:  # the connection may be dropped instead; either way the whole body is not delivered
        pass


def test_rate_limit_on_changes(tnl, monkeypatch):
    from semechka.routers import trinodal as R
    monkeypatch.setattr(R, "WRITE_LIMIT", 3)
    codes = [tnl.post("/trinodal/api/training/jobs", json={}).status_code for _ in range(5)]
    assert codes == [202, 202, 202, 429, 429]
    r = tnl.post("/trinodal/api/training/jobs", json={})
    assert r.headers["retry-after"]
    assert tnl.get("/trinodal/api/network").status_code == 200  # reads have their own budget


def test_changes_and_refusals_are_audited_without_bodies(tnl, app, st):
    owner = tnl
    secret = "PRIVATE-DATASET-ROW-4711"
    assert owner.post("/trinodal/api/datasets", json={"row": secret}).status_code == 202
    assert owner.get("/trinodal/api/network").status_code == 200  # reads are not audited
    v = person(owner, app, "viewer", ["trinodal.view"])
    assert v.post("/trinodal/api/training/jobs", json={"row": secret}).status_code == 403
    assert v.c.post("/trinodal/api/training/jobs", json={}).status_code == 403  # forbidden comes before csrf
    o2 = owner.c.post("/trinodal/api/training/jobs", json={"row": secret})  # csrf missing
    assert o2.status_code == 403
    with st.db.session() as s:
        ev = list(s.execute(select(AuditEvent).where(AuditEvent.action.like("trinodal.%"))).scalars())
        from semechka import audit
        assert audit.verify_chain(s)["ok"]
    proxied = [e for e in ev if e.action == "trinodal.proxy"]
    denied = [e for e in ev if e.action == "trinodal.denied"]
    assert len(proxied) == 1 and proxied[0].actor and proxied[0].detail == {
        "method": "POST", "class": "api/datasets", "path": "api/datasets", "status": 202, "scope": "train"}
    reasons = sorted(e.detail["reason"] for e in denied)
    assert reasons == ["csrf", "forbidden", "forbidden"]
    assert {e.actor for e in denied} == {v.user_id, owner.get("/api/me").json()["user"]["id"]}
    assert all(e.detail["needed"] for e in denied)
    assert secret not in json.dumps([e.detail for e in ev])


def test_denied_audit_is_throttled_per_person(tnl, app, st):
    v = person(tnl, app, "viewer", ["trinodal.view"])
    for _ in range(40):
        assert v.post("/trinodal/api/training/jobs", json={}).status_code == 403
    with st.db.session() as s:
        n = len(list(s.execute(select(AuditEvent).where(AuditEvent.action == "trinodal.denied")).scalars()))
    assert 15 <= n <= 25


# ---- streaming: not buffered; closing the browser's connection closes Trinodal's -----------------------------------------
@pytest.fixture
def live(tnl, app, st):
    """The same app served on a real socket, with a signed-in httpx client (the in-process test client cannot show timing)."""
    srv = Server(app, _free_port())
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    c = httpx.Client(base_url=base, headers={"Origin": base}, timeout=30)
    assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW}).status_code == 200
    c.csrf = c.get("/api/me").json()["csrf"]
    yield c
    c.close()
    srv.stop()


def test_event_streams_are_delivered_as_they_arrive(live):
    t0 = time.monotonic()
    with live.stream("GET", "/trinodal/api/sse-gate") as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert r.headers["cache-control"] == "no-cache, no-transform" and r.headers["x-accel-buffering"] == "no"
        lines = r.iter_lines()
        first = next(line for line in lines if line.startswith("data:"))
        t_first = time.monotonic() - t0
        assert first == "data: first" and t_first < 1.5, "the first event must not wait for the second"
        second = next(line for line in lines if line.startswith("data:"))
        assert second == "data: second" and time.monotonic() - t0 >= 1.9


def test_closing_the_browser_connection_closes_the_upstream_stream(live):
    with live.stream("GET", "/trinodal/api/events") as r:
        got = [next(line for line in r.iter_lines() if line.startswith("data:"))]
        assert got and ft.STATE["sse_started"] == 1 and ft.STATE["sse_closed"] == 0
    deadline = time.time() + 5
    while ft.STATE["sse_closed"] < 1 and time.time() < deadline:
        time.sleep(0.05)
    assert ft.STATE["sse_closed"] == 1, "Trinodal's stream must end when the reader goes away"


def test_open_streams_are_counted_and_released(live, st):
    with live.stream("GET", "/trinodal/api/events") as r:
        next(line for line in r.iter_lines() if line.startswith("data:"))
        assert sum(st.trinodal_open.values()) == 1
    deadline = time.time() + 5
    while st.trinodal_open and time.time() < deadline:
        time.sleep(0.05)
    assert st.trinodal_open == {}


# ---- Sarness: Trinodal as a local remote endpoint ---------------------------------------------------------------------
@pytest.fixture
def tri(mo, st, app):
    """A Sarness install where the engine also exposes Trinodal AI (and one genuinely external endpoint)."""
    owner = mo
    fs.STATE["trinodal"] = True
    b = owner.get("/api/admin/backends").json()[0]
    return owner, b


def _sync_and_enable(owner, st, backend_id):
    assert owner.post(f"/api/admin/backends/{backend_id}/sync").status_code == 200
    with st.db.session(write=True) as s:
        for m in s.execute(select(Model)).scalars():
            if m.real_id.startswith("endpoint:"):
                m.enabled = True
                for rid in ("owner", "member"):
                    s.add(ModelGrant(model_id=m.id, subject_type="role", subject_id=rid, effect="allow"))


def _put_backend(owner, b, **extra):
    return owner.put(f"/api/admin/backends/{b['id']}", json={"name": b["name"], "adapter": "sarness", "base_url": b["base_url"],
                                                              "external": False, "enabled": True, **extra})


def _models(owner):
    return {m["real_id"]: m for m in owner.get("/api/admin/models").json()["items"]}


async def test_discover_marks_only_declared_endpoints_local(sarness_url):
    fs.reset()
    fs.STATE["trinodal"] = True
    for opts, want in (({}, True), ({"local_endpoints": ["trinodal"]}, False), ({"local_endpoints": ["other"]}, True),
                       ({"local_endpoints": ["trino"]}, True), ({"local_endpoints": "trinodal"}, True)):
        got = {m.real_id: m for m in await SarnessAdapter(sarness_url, fs.TOKEN, opts).discover()}
        assert got["endpoint:trinodal:trinodal"].external is want, opts
        assert got["endpoint:other:big"].external is (opts.get("local_endpoints") != ["other"]), opts  # only the listed id is local
        assert got["local:fake-chat"].external is False
    # a backend marked external stays external, whatever the list says
    got = {m.real_id: m for m in await SarnessAdapter(sarness_url, fs.TOKEN, {"local_endpoints": ["trinodal"], "external": True}).discover()}
    assert got["endpoint:trinodal:trinodal"].external is True and got["local:fake-chat"].external is True
    assert got["endpoint:trinodal:trinodal"].meta["endpoint"] == "Trinodal AI"


def test_local_endpoint_is_not_external_and_others_still_are(tri, st, app):
    owner, b = tri
    assert _put_backend(owner, b).status_code == 200
    _sync_and_enable(owner, st, b["id"])
    ms = _models(owner)
    assert ms["endpoint:trinodal:trinodal"]["external"] is True and ms["endpoint:other:big"]["external"] is True
    r = _put_backend(owner, b, local_endpoints=["trinodal"])
    assert r.status_code == 200 and r.json()["local_endpoints"] == ["trinodal"]
    ms = _models(owner)
    assert ms["endpoint:trinodal:trinodal"]["external"] is False, "effective at once, without a new sync"
    assert ms["endpoint:other:big"]["external"] is True
    m = make_user(owner, app, "m@example.test")
    picker = {x["alias"]: x for x in m.get("/api/chat/models").json()}
    assert "trinodal" in picker and "other:big" not in picker, "the egress policy still blocks the external endpoint"
    assert picker["trinodal"]["external"] is False
    # a sync keeps it, and re-externalising is immediate
    assert owner.post(f"/api/admin/backends/{b['id']}/sync").status_code == 200
    assert _models(owner)["endpoint:trinodal:trinodal"]["external"] is False
    assert _put_backend(owner, b).json()["local_endpoints"] == ["trinodal"], "omitted: unchanged"
    assert _put_backend(owner, b, local_endpoints=[]).status_code == 200
    assert _models(owner)["endpoint:trinodal:trinodal"]["external"] is True
    assert "trinodal" not in {x["alias"] for x in m.get("/api/chat/models").json()}
    # the option is audited and validated
    assert _put_backend(owner, b, local_endpoints=["bad id!"]).status_code == 422
    assert _put_backend(owner, b, local_endpoints=["trinodal"]).status_code == 200
    with st.db.session() as s:
        ev = [e.detail for e in s.execute(select(AuditEvent).where(AuditEvent.action == "backend.update")).scalars()]
    assert {"old": [], "new": ["trinodal"]} in [d.get("local_endpoints") for d in ev]


def test_local_endpoints_apply_to_sarness_only(owner):
    b = owner.get("/api/admin/backends").json()[0]
    r = owner.put(f"/api/admin/backends/{b['id']}", json={"name": b["name"], "adapter": b["adapter"], "base_url": b["base_url"],
                                                            "local_endpoints": ["trinodal"]})
    assert r.status_code == 422


def _chat(api, real_id, models=None):
    mid = next(m["id"] for m in api.get("/api/chat/models").json() if m["alias"] == real_id)
    cid = api.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    status, ev = api.stream(f"/api/chat/conversations/{cid}/generate", {"content": "what is 2+2?", "model_id": mid})
    assert status == 200, ev
    return cid, ev


def _local_trinodal(tri, st):
    owner, b = tri
    assert _put_backend(owner, b, local_endpoints=["trinodal"]).status_code == 200
    _sync_and_enable(owner, st, b["id"])
    return owner


FULL = {"nodes": ["conversation", "mathematics", "conversation"], "route": "math.exact", "trained_components": ["router-v3"],
        "llm_calls": 2, "refused": 0, "errors": 0, "trace_id": "tr-0123456789abcdef",
        "language_core": {"model": "lc-secret-model", "backend": "llama-cpp"}, "ms": 412.5}


def test_a_trinodal_reply_works_in_the_normal_chat_and_a_plain_member_sees_none_of_the_run(tri, st, app):
    owner = _local_trinodal(tri, st)
    m = make_user(owner, app, "m@example.test")
    cid, ev = _chat(m, "trinodal")
    done = ev[-1]
    assert done["content"] == "Hello from Trinodal" and done["status"] == "complete"
    assert done["tech"] == {} and done["message"]["tech"] == {}
    blob = json.dumps([ev, m.get(f"/api/chat/conversations/{cid}").json(), m.get(f"/api/chat/conversations/{cid}/thread").json(),
                       m.get(f"/api/chat/conversations/{cid}/export", params={"format": "json"}).json()])
    for leak in ("mathematics", "math.exact", "tr-0123456789abcdef", "lc-secret-model", "llama-cpp", "router-v3", "412.5",
                 "PRIVATE-TEXT-IN-TRINODAL-USAGE"):
        assert leak not in blob, leak
    # the full, cleaned record is what is stored; unknown fields from the service never are
    with st.db.session() as s:
        stats = [x.stats for x in s.execute(select(Message).where(Message.role == "assistant")).scalars()][-1]
    assert stats["trinodal"] == FULL and stats["prompt_tokens"] == 5 and stats["completion_tokens"] == 3


PERM_FIELDS = [  # permission -> what it alone reveals
    ("tech.node.name", {"nodes", "route", "trained_components", "llm_calls", "refused", "errors"}),
    ("tech.job.trace", {"trace_id"}),
    ("tech.runtime.name", {"language_core"}),
    ("tech.perf.duration", {"ms"}),
]


@pytest.mark.parametrize("perm,fields", PERM_FIELDS)
def test_each_technical_permission_reveals_exactly_its_part_everywhere_stats_are_returned(tri, st, app, perm, fields):
    owner = _local_trinodal(tri, st)
    m = make_user(owner, app, "m@example.test")
    owner.put(f"/api/admin/users/{m.user_id}/override", json={"grants": [perm], "denies": []})
    cid, ev = _chat(m, "trinodal")
    mid = ev[-1]["message"]["id"]
    want = {k: v for k, v in FULL.items() if k in fields}
    thread = m.get(f"/api/chat/conversations/{cid}/thread").json()["messages"]
    full_conv = m.get(f"/api/chat/conversations/{cid}").json()["messages"]
    export = m.get(f"/api/chat/conversations/{cid}/export", params={"format": "json"}).json()["messages"]
    places = {"stream done": ev[-1]["tech"], "stream stored message": ev[-1]["message"]["tech"],
              "thread": next(x for x in thread if x["id"] == mid)["tech"],
              "conversation": next(x for x in full_conv if x["id"] == mid)["tech"],
              "export": next(x for x in export if x["id"] == mid)["tech"]}
    for where, tech in places.items():
        assert tech.get("trinodal") == want, (where, perm, tech)
    blob = json.dumps(places)
    for k, v in FULL.items():
        if k not in fields and isinstance(v, str):
            assert v not in blob, (perm, k)
    # regenerate / other conversation views of the same message: still filtered the same way
    status, ev2 = m.stream(f"/api/chat/conversations/{cid}/generate",
                           {"mode": "regenerate", "parent_id": mid, "model_id": next(
                               x["id"] for x in m.get("/api/chat/models").json() if x["alias"] == "trinodal")})
    assert status == 200 and ev2[-1]["tech"].get("trinodal") == want


def test_all_tech_permissions_show_everything_and_a_denied_one_hides_its_part(tri, st, app):
    owner = _local_trinodal(tri, st)
    m = make_user(owner, app, "m@example.test")
    owner.put(f"/api/admin/users/{m.user_id}/override", json={"grants": ["tech.*"], "denies": ["tech.runtime.name"]})
    _, ev = _chat(m, "trinodal")
    t = ev[-1]["tech"]["trinodal"]
    assert t == {k: v for k, v in FULL.items() if k != "language_core"}
    owner.put(f"/api/admin/users/{m.user_id}/override", json={"grants": ["tech.*"], "denies": []})
    _, ev = _chat(m, "trinodal")
    assert ev[-1]["tech"]["trinodal"] == FULL


def test_other_models_never_get_a_trinodal_record(tri, st, app):
    owner = _local_trinodal(tri, st)
    owner.put("/api/admin/settings/privacy.external_egress", json={"value": True, "confirm_weaker": True})
    me = owner.get("/api/me").json()["user"]["id"]
    owner.put(f"/api/admin/users/{me}/override", json={"grants": ["tech.*"], "denies": []})
    _, ev = _chat(owner, "fake-chat")
    assert "trinodal" not in ev[-1]["tech"]
    # an endpoint NOT declared local cannot add run metadata, even if it sends the same object
    owner.put(f"/api/admin/backends/{tri[1]['id']}", json={"name": tri[1]["name"], "adapter": "sarness", "base_url": tri[1]["base_url"],
                                                           "local_endpoints": []})
    _, ev = _chat(owner, "trinodal")
    assert "trinodal" not in ev[-1]["tech"]


def test_the_service_cannot_smuggle_content_through_the_run_record():
    dirty = {"trace_id": "x\ny", "nodes": ["ok", "has\nnewline", 5, {"a": 1}, "x" * 100], "route": "r" * 500,
             "language_core": {"model": {"a": 1}, "backend": "b"}, "ms": "12", "llm_calls": True, "errors": -1,
             "content": "text", "extra": {"deep": 1}}
    got = clean_trinodal(dirty)
    assert got == {"nodes": ["ok"], "route": "r" * 120, "language_core": {"backend": "b"}}
    assert clean_trinodal("nope") is None and clean_trinodal({}) is None and clean_trinodal({"content": "x"}) is None
    assert len(clean_trinodal({"nodes": [f"n{i}" for i in range(500)]})["nodes"]) == 64


async def test_closing_the_stream_cancels_the_upstream_request(sarness_url):
    fs.reset()
    fs.STATE.update(trinodal=True, trinodal_delay=0.2)
    ad = SarnessAdapter(sarness_url, fs.TOKEN, {"local_endpoints": ["trinodal"]})
    agen = ad.chat_stream("endpoint:trinodal:trinodal", [{"role": "user", "content": "hi"}], {})
    first = await agen.__anext__()
    assert first["type"] == "delta"
    await agen.aclose()
    for _ in range(50):
        if fs.STATE["trinodal_closed"]:
            break
        await asyncio.sleep(0.1)
    assert fs.STATE["trinodal_closed"] == 1 and fs.STATE["trinodal_finished"] == 0, "upstream must see the request cancelled"


async def test_a_finished_stream_yields_the_cleaned_run_record_only_for_local_endpoints(sarness_url):
    fs.reset()
    fs.STATE["trinodal"] = True
    msgs = [{"role": "user", "content": "hi"}]
    for opts, has in (({"local_endpoints": ["trinodal"]}, True), ({}, False)):
        evs = [e async for e in SarnessAdapter(sarness_url, fs.TOKEN, opts).chat_stream("endpoint:trinodal:trinodal", msgs, {})]
        done = evs[-1]
        assert done["type"] == "done" and done["usage"] == {"prompt_tokens": 5, "completion_tokens": 3}
        assert (done.get("trinodal") == FULL) is has and ("trinodal" in done) is has


def test_cancelling_a_reply_in_the_chat_stops_the_trinodal_request_and_stores_a_stopped_message(tri, st, app):
    _local_trinodal(tri, st)
    fs.STATE["trinodal_delay"] = 0.3
    srv = Server(app, _free_port())
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        c = httpx.Client(base_url=base, headers={"Origin": base}, timeout=30)
        assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW}).status_code == 200
        csrf = c.get("/api/me").json()["csrf"]
        mid = next(m["id"] for m in c.get("/api/chat/models").json() if m["alias"] == "trinodal")
        cid = c.post("/api/chat/conversations", json={"model_id": mid}, headers={"x-csrf-token": csrf}).json()["id"]
        with c.stream("POST", f"/api/chat/conversations/{cid}/generate", json={"content": "slow one", "model_id": mid},
                      headers={"x-csrf-token": csrf}) as r:
            for line in r.iter_lines():
                if '"delta"' in line:
                    break
        deadline = time.time() + 6
        while not fs.STATE["trinodal_closed"] and time.time() < deadline:
            time.sleep(0.05)
        assert fs.STATE["trinodal_closed"] == 1 and fs.STATE["trinodal_finished"] == 0
        deadline = time.time() + 5
        status = None
        while time.time() < deadline:
            rows = c.get(f"/api/chat/conversations/{cid}").json()["messages"]
            a = [x for x in rows if x["role"] == "assistant"]
            if a:
                status = a[0]["status"]
                break
            time.sleep(0.1)
        assert status == "stopped"
    finally:
        srv.stop()
