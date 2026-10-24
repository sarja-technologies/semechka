"""OpenID Connect single sign-on, tested end to end against a strict mock identity provider.

The mock proves protocol behaviour (PKCE, nonce, signatures, key rotation). It is not a real provider: see
docs/KNOWN_LIMITATIONS.md for what remains unverified against Keycloak, Entra ID, Okta or Google.
"""
from __future__ import annotations

import json
import sqlite3
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from semechka import sso_svc

from .conftest import PW, Api, Server, _free_port, make_user
from .mock_oidc import Idp, Key


@pytest.fixture(scope="session")
def idp():
    i = Idp()
    srv = Server(i.app, _free_port())
    srv.start()
    i.base = f"http://127.0.0.1:{srv.port}"
    yield i
    srv.stop()


@pytest.fixture(autouse=True)
def _fresh(idp, monkeypatch):
    idp.reset()
    sso_svc.clear_caches()
    monkeypatch.setattr(sso_svc, "JWKS_REFRESH_MIN", 0.0)
    yield


def provider_body(idp, **kw):
    return {"name": "Test IdP", "issuer": idp.base, "client_id": idp.CLIENT_ID, "client_secret": idp.CLIENT_SECRET,
            "enabled": True, "default_role": "member", **kw}


@pytest.fixture
def prov(owner, idp):
    r = owner.post("/api/admin/sso/providers", json=provider_body(idp))
    assert r.status_code == 200, r.text
    return r.json()


def run_flow(app, idp, pid, *, api=None, return_to="/chat", link=False, tamper_state=None, drop_cookie=False, state_only=False):
    """Drive browser -> Semechka -> IdP -> Semechka. Returns (api, callback_response)."""
    api = api or Api(app)
    q = f"?return_to={return_to}" + ("&link=1" if link else "")
    start = api.c.get(f"/api/sso/{pid}/start{q}", follow_redirects=False)
    assert start.status_code == 303, start.text
    loc = start.headers["location"]
    if not loc.startswith(idp.base):
        return api, start
    cb = httpx.get(loc, follow_redirects=False)
    assert cb.status_code == 302, cb.text
    u = urlsplit(cb.headers["location"])
    params = parse_qs(u.query)
    if tamper_state is not None:
        params["state"] = [tamper_state]
    path = u.path + "?" + "&".join(f"{k}={v[0]}" for k, v in params.items())
    if drop_cookie:
        api.c.cookies.clear()
    return api, api.c.get(path, follow_redirects=False)


def ok_login(r):
    return r.status_code == 303 and not r.headers["location"].startswith("/login")


def err_of(r) -> str:
    assert r.status_code == 303 and r.headers["location"].startswith("/login?sso_error="), (r.status_code, r.headers.get("location"))
    return r.headers["location"].split("=", 1)[1]


def audit_reasons(owner):
    return [e["detail"].get("reason") for e in owner.get("/api/admin/audit?limit=200").json()["items"] if e["action"] == "sso.login_failed"]


# ---- happy path and protocol ------------------------------------------------------------------------
def test_login_creates_user_and_session(owner, app, idp, prov):
    api, r = run_flow(app, idp, prov["id"], return_to="/chat?x=1")
    assert ok_login(r) and r.headers["location"] == "/chat?x=1"
    me = api.get("/api/me").json()
    assert me["user"]["email"] == "sso.user@example.test" and [x["id"] for x in me["roles"]] == ["member"]
    assert any(e["action"] == "sso.jit_create" for e in owner.get("/api/admin/audit?limit=50").json()["items"])
    # a second login signs the same person in; no duplicate account
    api2, r2 = run_flow(app, idp, prov["id"])
    assert ok_login(r2)
    users = [u for u in owner.get("/api/admin/users?limit=200").json()["items"] if u["email"] == "sso.user@example.test"]
    assert len(users) == 1


def test_authorization_request_uses_pkce_state_nonce_and_client_authenticates(owner, app, idp, prov):
    run_flow(app, idp, prov["id"])
    q = idp.last_authorize
    assert q["code_challenge_method"] == "S256" and len(q["code_challenge"]) >= 43
    assert len(q["state"]) >= 32 and len(q["nonce"]) >= 32 and q["client_id"] == idp.CLIENT_ID
    assert q["redirect_uri"].endswith("/api/sso/callback") and "openid" in q["scope"].split()
    assert idp.token_calls[-1]["has_secret"]  # the IdP rejects unauthenticated clients, so the login only works if it was sent


def test_es256_tokens_are_accepted(owner, app, idp, prov):
    idp.keys = [Key("ec1", "EC")]
    _, r = run_flow(app, idp, prov["id"])
    assert ok_login(r)


def test_state_is_single_use_and_bound_to_the_browser(owner, app, idp, prov):
    _, r = run_flow(app, idp, prov["id"], tamper_state="not-the-state")
    assert err_of(r) == "expired"
    _, r = run_flow(app, idp, prov["id"], drop_cookie=True)  # login CSRF: callback from a browser that did not start the flow
    assert err_of(r) == "expired"
    api, r = run_flow(app, idp, prov["id"])
    assert ok_login(r)
    replay = api.c.get(r.request.url.path + "?" + r.request.url.query.decode(), follow_redirects=False)
    assert err_of(replay) == "expired"


@pytest.mark.parametrize("override,reason", [
    ({"nonce": "attacker-nonce"}, "bad_nonce"),
    ({"exp": int(time.time()) - 3600}, "token_expired"),
    ({"aud": "someone-else"}, "bad_audience"),
    ({"aud": [Idp.CLIENT_ID, "other"]}, "bad_audience"),  # multiple audiences need azp
    ({"azp": "other-client"}, "bad_audience"),
    ({"iss": "https://evil.example"}, "bad_issuer"),
    ({"nbf": int(time.time()) + 3600}, "bad_token"),
    ({"sub": None}, "bad_token"),
])
def test_hostile_claims_are_rejected(owner, app, idp, prov, override, reason):
    idp.claims = override
    _, r = run_flow(app, idp, prov["id"])
    err_of(r)
    assert reason in audit_reasons(owner)
    assert not [u for u in owner.get("/api/admin/users?limit=200").json()["items"] if u["email"] == "sso.user@example.test"]


@pytest.mark.parametrize("mode,reason", [("none", "bad_token"), ("hs256", "bad_token"), ("tamper", "bad_signature"), ("wrongkey", "bad_signature")])
def test_forged_signatures_are_rejected(owner, app, idp, prov, mode, reason):
    idp.mode = mode
    _, r = run_flow(app, idp, prov["id"])
    err_of(r)
    assert reason in audit_reasons(owner)
    assert not [u for u in owner.get("/api/admin/users?limit=200").json()["items"] if u["email"] in ("sso.user@example.test", "admin@example.test")]


def test_key_rotation_refetches_the_key_set(owner, app, idp, prov):
    assert ok_login(run_flow(app, idp, prov["id"])[1])  # caches k1
    idp.keys = [Key("k2")]
    assert ok_login(run_flow(app, idp, prov["id"])[1])  # unknown kid -> one forced refetch


def test_token_signed_by_a_key_the_provider_does_not_publish_is_rejected(owner, app, idp, prov):
    idp.keys = [Key("k1"), Key("ghost")]
    idp.hide_last = True  # the token is signed with "ghost", which the key set never lists, even after a refetch
    _, r = run_flow(app, idp, prov["id"])
    err_of(r)
    assert "bad_signature" in audit_reasons(owner)


# ---- accounts, roles, linking -----------------------------------------------------------------------
def test_unverified_email_and_existing_local_account_are_refused(owner, app, idp, prov):
    idp.claims = {"email_verified": False}
    assert err_of(run_flow(app, idp, prov["id"])[1]) == "unverified_email"
    idp.claims = {"email_verified": None}
    assert err_of(run_flow(app, idp, prov["id"])[1]) == "unverified_email"  # a missing claim is not "verified"
    # verified email that matches a password account must NOT sign in as that account (no silent takeover)
    idp.claims = {"email": "owner@example.test"}
    api, r = run_flow(app, idp, prov["id"])
    assert err_of(r) == "existing_account"
    assert api.get("/api/me").status_code == 401


def test_idp_that_omits_email_verified_can_be_trusted_by_an_explicit_owner_choice(owner, app, idp):
    pid = owner.post("/api/admin/sso/providers", json=provider_body(idp, assume_email_verified=True)).json()["id"]
    idp.claims = {"email_verified": None}
    assert ok_login(run_flow(app, idp, pid)[1])
    idp.claims = {"email_verified": False}  # an explicit false is never overridden
    idp.claims = {"email_verified": False, "sub": "other"}
    assert err_of(run_flow(app, idp, pid)[1]) == "unverified_email"


def test_explicit_link_then_sso_signs_in_as_that_account(owner, app, idp, prov):
    idp.claims = {"email": "whatever@idp.example", "sub": "owner-sub"}
    assert err_of(run_flow(app, idp, prov["id"], link=True, api=Api(app))[1]) == "failed"  # linking needs a signed-in person
    api, r = run_flow(app, idp, prov["id"], link=True, api=owner)
    assert r.status_code == 303 and r.headers["location"] == "/account"
    assert owner.get("/api/sso/identities").json()["linked"][0]["provider"] == "Test IdP"
    api2, r2 = run_flow(app, idp, prov["id"])
    assert ok_login(r2) and api2.get("/api/me").json()["user"]["email"] == "owner@example.test"
    # the same IdP identity cannot be attached to a second account
    other = make_user(owner, app, "second@example.test")
    assert err_of(run_flow(app, idp, prov["id"], link=True, api=other)[1]) == "linked_elsewhere"


def test_group_role_mapping_and_sync(owner, app, idp):
    body = provider_body(idp, role_map={"ai-admins": ["platform_admin"], "staff": ["org_admin"]})
    pid = owner.post("/api/admin/sso/providers", json=body).json()["id"]
    idp.claims = {"groups": ["ai-admins", "unrelated"]}
    api, r = run_flow(app, idp, pid)
    assert ok_login(r)
    assert {x["id"] for x in api.get("/api/me").json()["roles"]} == {"platform_admin"}
    idp.claims = {"groups": []}  # removed from the group: the SSO-given role goes, the default applies
    api, r = run_flow(app, idp, pid)
    assert {x["id"] for x in api.get("/api/me").json()["roles"]} == {"member"}
    # a role an administrator added by hand is never taken away by a sync
    uid = api.get("/api/me").json()["user"]["id"]
    owner.patch(f"/api/admin/users/{uid}", json={"role_ids": ["member", "queue_operator"]})
    api, r = run_flow(app, idp, pid)
    assert "queue_operator" in {x["id"] for x in api.get("/api/me").json()["roles"]}


def test_only_owners_can_map_people_to_privileged_roles(owner, app, idp):
    guest_grants = next(r for r in owner.get("/api/admin/roles").json() if r["id"] == "guest")["grants"]
    owner.post("/api/admin/roles", json={"label": "SSO manager", "grants": ["admin.sso.manage", "admin.overview", "admin.settings.view", *guest_grants], "denies": [], "rank": 50})
    mgr = make_user(owner, app, "ssomgr@example.test", role="sso_manager")
    for target in ("owner", "platform_admin"):
        r = mgr.post("/api/admin/sso/providers", json=provider_body(idp, role_map={"g": [target]}))
        assert r.status_code == 403, (target, r.text)
    assert mgr.post("/api/admin/sso/providers", json=provider_body(idp, allow_private_network=True)).status_code == 403
    assert mgr.post("/api/admin/sso/providers", json=provider_body(idp, default_role="guest")).status_code == 200
    # an ordinary member cannot configure SSO at all
    m = make_user(owner, app, "plain@example.test")
    assert m.get("/api/admin/sso/providers").status_code == 403
    assert Api(app).get("/api/admin/sso/providers").status_code == 401


def test_domain_restriction_and_approval_and_suspension(owner, app, idp):
    pid = owner.post("/api/admin/sso/providers", json=provider_body(idp, allowed_domains=["corp.example"])).json()["id"]
    assert err_of(run_flow(app, idp, pid)[1]) == "domain_not_allowed"
    owner.put(f"/api/admin/sso/providers/{pid}", json=provider_body(idp, allowed_domains=["example.test"], require_approval=True))
    assert err_of(run_flow(app, idp, pid)[1]) == "pending_approval"
    users = owner.get("/api/admin/users?limit=200").json()["items"]
    u = next(x for x in users if x["email"] == "sso.user@example.test")
    owner.patch(f"/api/admin/users/{u['id']}", json={"status": "active"})
    assert ok_login(run_flow(app, idp, pid)[1])
    owner.patch(f"/api/admin/users/{u['id']}", json={"status": "suspended"})
    api, r = run_flow(app, idp, pid)  # a disabled account cannot get in through the IdP either
    assert err_of(r) == "suspended" and api.get("/api/me").status_code == 401
    owner.put(f"/api/admin/sso/providers/{pid}", json=provider_body(idp, jit_create=False))
    idp.claims = {"sub": "new-person", "email": "new@example.test"}
    assert err_of(run_flow(app, idp, pid)[1]) == "not_registered"


# ---- policy -----------------------------------------------------------------------------------------
def test_sso_required_blocks_passwords_except_for_owners(owner, app, idp, prov):
    member = make_user(owner, app, "pw.member@example.test")
    r = owner.put("/api/admin/settings/sso.local_login", json={"value": False, "confirm_weaker": True})
    assert r.status_code == 200, r.text
    assert Api(app).login("pw.member@example.test").json()["detail"]["code"] == "sso_required"
    assert Api(app).login("owner@example.test").status_code == 200  # break-glass
    assert Api(app).post("/api/auth/signup", json={"email": "x@example.test", "password": PW, "display_name": "x"}).status_code == 403
    pub = Api(app).get("/api/sso/providers").json()
    assert pub["local_login"] is False and [p["name"] for p in pub["providers"]] == ["Test IdP"]
    # with the provider switched off nobody is locked out
    owner.put(f"/api/admin/sso/providers/{prov['id']}", json=provider_body(idp, enabled=False))
    assert Api(app).login("pw.member@example.test").status_code == 200
    assert Api(app).get("/api/sso/providers").json()["providers"] == []


@pytest.mark.parametrize("target", ["//evil.example/x", "https://evil.example", "/\\evil.example", "javascript:alert(1)", "evil", "/ok%0d%0aSet-Cookie:%20x=1", "%2f%2fevil.example"])
def test_return_path_cannot_redirect_off_site(owner, app, idp, prov, target):
    _, r = run_flow(app, idp, prov["id"], return_to=target)
    assert r.headers["location"] == "/"


def test_logout_returns_provider_end_session_url_only_when_advertised(owner, app, idp, prov):
    api, r = run_flow(app, idp, prov["id"])
    api.csrf = api.get("/api/me").json()["csrf"]
    out = api.post("/api/sso/logout").json()
    assert out["redirect"] is None and api.get("/api/me").status_code == 401  # no end_session_endpoint advertised
    idp.discovery = {"end_session_endpoint": idp.base + "/logout"}
    sso_svc.clear_caches()
    api, r = run_flow(app, idp, prov["id"])
    api.csrf = api.get("/api/me").json()["csrf"]
    out = api.post("/api/sso/logout").json()
    q = parse_qs(urlsplit(out["redirect"]).query)
    assert out["redirect"].startswith(idp.base + "/logout") and q["client_id"] == [idp.CLIENT_ID] and q["id_token_hint"][0].count(".") == 2
    assert api.get("/api/me").status_code == 401
    # logout needs the CSRF token like every other state change
    api, _ = run_flow(app, idp, prov["id"])
    api.csrf = "wrong"
    assert api.post("/api/sso/logout").status_code == 403


# ---- SSRF -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("issuer", ["https://169.254.169.254", "https://10.0.0.5", "https://192.168.1.10", "https://[fe80::1]", "https://0.0.0.0"])
def test_discovery_refuses_metadata_and_private_addresses(owner, issuer, idp):
    pid = owner.post("/api/admin/sso/providers", json=provider_body(idp, issuer=issuer)).json()["id"]
    r = owner.post(f"/api/admin/sso/providers/{pid}/test").json()
    assert r["ok"] is False and r["error"] == "blocked_address"


def test_plain_http_issuer_rejected_for_real_hosts_and_discovery_links_are_checked(owner, idp, prov):
    assert owner.post("/api/admin/sso/providers", json=provider_body(idp, name="b", issuer="http://idp.example.org")).status_code == 422
    idp.discovery = {"jwks_uri": "http://169.254.169.254/latest/meta-data/"}
    r = owner.post(f"/api/admin/sso/providers/{prov['id']}/test").json()
    assert r["ok"] is False and r["error"] in ("blocked_address", "insecure_url")
    idp.discovery = {"issuer": "https://other.example"}
    assert owner.post(f"/api/admin/sso/providers/{prov['id']}/test").json()["error"] == "issuer_mismatch"
    with pytest.raises(sso_svc.SsoError):
        sso_svc.check_url("https://user:pw@example.org/")
    with pytest.raises(sso_svc.SsoError):
        sso_svc.check_url("file:///etc/passwd")


def test_discovery_test_reports_what_it_really_found(owner, idp, prov):
    r = owner.post(f"/api/admin/sso/providers/{prov['id']}/test").json()
    assert r["ok"] and r["jwks_keys"] == 1 and r["pkce_s256"] is True and r["supported_algorithms"] == ["RS256"]
    assert r["issuer"] == idp.base and r["end_session_endpoint"] is False


# ---- secrets ----------------------------------------------------------------------------------------
def test_client_secret_is_write_only_encrypted_and_never_exported_or_audited(owner, app, idp, prov, cfg, caplog):
    secret = idp.CLIENT_SECRET
    assert secret not in json.dumps(owner.get("/api/admin/sso/providers").json()) and prov["has_secret"] is True
    owner.put(f"/api/admin/sso/providers/{prov['id']}", json=provider_body(idp, client_secret=None))  # omitted = kept
    assert ok_login(run_flow(app, idp, prov["id"])[1])
    owner.post(f"/api/admin/sso/providers/{prov['id']}/test")
    assert secret not in owner.get("/api/admin/settings-export").text
    assert secret not in json.dumps(owner.get("/api/admin/audit?limit=500").json())
    assert secret not in caplog.text
    for f in cfg.data_dir.glob("semechka.db*"):  # encrypted at rest, not just hidden in the API
        assert secret.encode() not in f.read_bytes()
    with sqlite3.connect(cfg.data_dir / "semechka.db") as con:
        enc = con.execute("select client_secret_enc from sso_providers").fetchone()[0]
    assert enc and secret not in enc


def test_callback_is_rate_limited(owner, app, idp, prov):
    api = Api(app)
    last = None
    for _ in range(35):
        last = api.c.get("/api/sso/callback?code=x&state=y", follow_redirects=False)
    assert err_of(last) == "rate_limited"
