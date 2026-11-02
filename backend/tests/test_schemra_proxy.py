"""The /schemra/ reverse proxy (docs/SCHEMRA_INTEGRATION.md): who gets in, what is forwarded, what is signed, what comes back.

The lab is `fake_schemra.py`, a real HTTP server on loopback that records each request exactly as it arrived. The
signature is checked here by a separate, deliberately plain re-implementation of the contract (not by the proxy's code).
Streaming, disconnect and upload tests run against a real Semechka server (uvicorn), because an in-process test client
buffers.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import socket
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from semechka import audit
from semechka import schemra_proxy as sp
from semechka.models import AuditEvent, Role, SettingValue
from semechka.settings_reg import BY_ID, SettingError

from . import fake_schemra
from .conftest import PW, Api, Server, _free_port, make_user

SECRET = "unit-test-secret-" + "k" * 40


# ---- helpers ----------------------------------------------------------------------------------------------------
def set_setting(st, key, value):
    with st.db.session(write=True) as s:
        st.settings.set(s, "test", key, value, confirm_weaker=True)


def mk_role(st, rid, grants, denies=()):
    with st.db.session(write=True) as s:
        s.add(Role(id=rid, label=rid, description="", rank=150, grants=list(grants), denies=list(denies)))


def person(owner, app, st, email, grants, denies=()):
    rid = "r_" + email.split("@")[0]
    mk_role(st, rid, grants, denies)
    return make_user(owner, app, email, role=rid)


def hdrs(rec) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for k, v in rec["headers"]:
        out.setdefault(k.lower(), []).append(v)
    return out


def check_signature(rec, secret=SECRET, now=None, skew=60) -> bool:
    """The contract, written out independently of semechka.schemra_proxy (LAB_API.md section 9)."""
    h = hdrs(rec)
    try:
        ts, nonce, user, role, sig = (h[f"x-schemra-{n}"][0] for n in ("ts", "nonce", "user", "role", "sig"))
    except KeyError:
        return False
    msg = "v1\n" + rec["method"] + "\n" + rec["target"] + "\n" + ts + "\n" + nonce + "\n" + user + "\n" + role
    good = hmac.compare_digest(hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest(), sig)
    return good and abs((now or time.time()) - int(ts)) <= skew


@pytest.fixture
def lab(owner, app, st, cfg):
    """The fake lab on a free port, the secret file in place, and the feature switched on and pointed at it."""
    fake_schemra.reset()
    srv = Server(fake_schemra.app, _free_port())
    srv.start()
    fake_schemra.STATE["port"] = srv.port
    path = cfg.schemra_secret_path
    path.write_text(SECRET + "\n")
    os.chmod(path, 0o600)
    set_setting(st, "schemra.enabled", True)
    set_setting(st, "schemra.upstream", f"http://127.0.0.1:{srv.port}")
    yield SimpleNamespace(port=srv.port, state=fake_schemra.STATE, secret_path=path, server=srv)
    srv.stop()


@pytest.fixture
def live(owner, app, st, lab):
    """Semechka itself on a real socket, for tests where streaming matters."""
    srv = Server(app, _free_port())
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"

    def client(email="owner@example.test", **kw):
        c = httpx.Client(base_url=base, timeout=20, **kw)
        assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
        c.csrf = c.get("/api/me").json()["csrf"]
        return c

    yield SimpleNamespace(base=base, port=srv.port, client=client, st=st)
    srv.stop()


def raw_get(port, target, cookie="", extra=()):
    """GET with a request target sent exactly as given (HTTP libraries tidy `..` away)."""
    head = [f"GET {target} HTTP/1.1", f"Host: 127.0.0.1:{port}", "Connection: close", *([f"Cookie: {cookie}"] if cookie else []), *extra]
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(("\r\n".join(head) + "\r\n\r\n").encode("latin-1"))
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
    line = buf.split(b"\r\n", 1)[0].decode()
    return int(line.split()[1]), buf


# ---- who gets in ---------------------------------------------------------------------------------------------------
def test_unauthenticated_is_denied_like_the_rest_of_semechka(lab, app):
    anon = Api(app)
    r = anon.get("/schemra/api/lab/whoami")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "unauthenticated"
    r = anon.c.get("/schemra/boards?x=1", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/schemra/boards%3Fx%3D1"
    r = anon.post("/schemra/api/lab/boards", json={})
    assert r.status_code == 401
    assert lab.state["received"] == []


def test_disabled_feature_is_a_404_for_everyone(owner, app, st, lab):
    set_setting(st, "schemra.enabled", False)
    assert owner.get("/schemra/api/lab/whoami").status_code == 404
    assert owner.get("/schemra/").status_code == 404
    assert Api(app).get("/schemra/api/lab/whoami").status_code == 404  # signed out: no hint that it exists
    assert lab.state["received"] == []
    assert BY_ID["schemra.enabled"].default is False


def test_new_install_has_it_off_and_upstream_is_loopback():
    assert BY_ID["schemra.enabled"].default is False
    assert BY_ID["schemra.upstream"].default == "http://127.0.0.1:8820"


@pytest.mark.parametrize("grants,denies,expected", [
    (["schemra.view"], [], "viewer"),
    (["schemra.operate"], [], "operator"),
    (["schemra.admin"], [], "admin"),
    (["schemra.view", "schemra.operate"], [], "operator"),
    (["schemra.view", "schemra.operate", "schemra.admin"], [], "admin"),
    (["schemra.*"], [], "admin"),
    (["schemra.view", "schemra.operate", "schemra.admin"], ["schemra.admin"], "operator"),
    (["schemra.view", "schemra.operate"], ["schemra.operate"], "viewer"),
])
def test_role_mapping(owner, app, st, lab, grants, denies, expected):
    u = person(owner, app, st, "alice@example.test", grants, denies)
    r = u.get("/schemra/api/lab/whoami")
    assert r.status_code == 200, r.text
    rec = lab.state["received"][-1]
    assert hdrs(rec)["x-schemra-role"] == [expected]
    assert hdrs(rec)["x-schemra-user"] == ["alice@example.test"]
    assert check_signature(rec)


def test_owner_is_admin_and_ordinary_members_are_refused(owner, app, st, lab):
    assert owner.get("/schemra/api/lab/whoami").status_code == 200
    assert hdrs(lab.state["received"][-1])["x-schemra-role"] == ["admin"]
    member = make_user(owner, app, "m@example.test")  # the built-in Member role
    n = len(lab.state["received"])
    r = member.get("/schemra/api/lab/whoami")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden" and "schemra" in r.json()["detail"]["message"].lower()
    page = member.c.get("/schemra/", headers={"accept": "text/html"})
    assert page.status_code == 403 and "text/html" in page.headers["content-type"] and "no access to Schemra" in page.text
    assert len(lab.state["received"]) == n  # nothing reached the lab
    guest = person(owner, app, st, "g@example.test", ["mode.chat", "admin.*"])  # administration of Semechka is not Schemra
    assert guest.get("/schemra/api/lab/whoami").status_code == 403


def test_a_suspended_account_loses_access(owner, app, st, lab):
    u = person(owner, app, st, "bob@example.test", ["schemra.operate"])
    assert u.get("/schemra/api/lab/whoami").status_code == 200
    from semechka.models import User
    with st.db.session(write=True) as s:
        s.get(User, u.user_id).status = "suspended"
    assert u.get("/schemra/api/lab/whoami").status_code == 401


def test_mfa_enrolment_is_required_where_semechka_requires_it(owner, app, st, lab):
    u = person(owner, app, st, "carol@example.test", ["schemra.view"])
    set_setting(st, "auth.mfa_policy", "everyone")
    r = u.get("/schemra/api/lab/whoami")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "mfa_enrolment_required"


def test_bare_prefix_redirects_to_the_slash_form(owner, app, lab):
    r = owner.c.get("/schemra?x=1", follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == "/schemra/?x=1"
    assert Api(app).c.get("/schemra", follow_redirects=False, headers={"accept": "application/json"}).status_code == 401


# ---- what the lab receives -----------------------------------------------------------------------------------------
def test_client_supplied_identity_and_credentials_never_reach_the_lab(owner, st, lab):
    spoof = {"X-Schemra-User": "root", "X-Schemra-Role": "admin", "X-Schemra-Sig": "00", "X-Schemra-Ts": "1",
             "X-Schemra-Nonce": "n", "X-Schemra-Extra": "1", "X-Forwarded-For": "6.6.6.6", "X-Forwarded-Host": "evil.example",
             "X-Forwarded-Proto": "https", "X-Forwarded-Prefix": "/evil", "Forwarded": "for=6.6.6.6", "Authorization": "Bearer abc",
             "X-Real-IP": "6.6.6.6", "Via": "1.1 evil", "Proxy-Authorization": "Basic eA==", "Origin": "http://testserver",
             "Referer": "http://testserver/x", "Connection": "keep-alive, X-Hop", "X-Hop": "1", "X-Csrf-Token": "stolen",
             "X-HTTP-Method-Override": "DELETE", "Sec-Fetch-Site": "same-origin", "Cookie": f"{st.cookie_name}={owner.c.cookies.get(st.cookie_name)}; evil=1; extra=2",
             "X-Original-URL": "/admin", "CF-Connecting-IP": "6.6.6.6", "Accept": "application/json", "X-Custom": "keep"}
    r = owner.get("/schemra/api/lab/echo?q=1", headers=spoof)
    assert r.status_code == 200
    rec = lab.state["received"][-1]
    h = hdrs(rec)
    assert h["x-schemra-user"] == ["owner@example.test"] and h["x-schemra-role"] == ["admin"]
    assert h["x-forwarded-prefix"] == ["/schemra"]
    for gone in ("x-schemra-extra", "x-forwarded-for", "x-forwarded-host", "x-forwarded-proto", "forwarded", "authorization",
                 "x-real-ip", "via", "proxy-authorization", "origin", "referer", "x-hop", "x-csrf-token", "x-http-method-override",
                 "sec-fetch-site", "cookie", "x-original-url", "cf-connecting-ip"):
        assert gone not in h, gone
    assert not any(v in str(rec["headers"]) for v in ("evil", "stolen", "6.6.6.6", "Bearer"))
    assert h["host"] == [f"127.0.0.1:{lab.port}"] and h["accept-encoding"] == ["identity"]
    assert h["accept"] == ["application/json"] and h["x-custom"] == ["keep"]
    assert all(len(h[f"x-schemra-{n}"]) == 1 for n in ("user", "role", "ts", "nonce", "sig"))
    assert check_signature(rec)
    assert "stolen" not in str(rec["headers"]) and owner.csrf not in str(rec["headers"])  # Semechka's own token stays here


def test_session_cookie_is_not_forwarded(owner, st, lab):
    assert owner.c.cookies.get(st.cookie_name)
    owner.get("/schemra/api/lab/echo")
    assert "cookie" not in hdrs(lab.state["received"][-1])
    assert st.cookie_name not in str(lab.state["received"][-1]["headers"])


def test_signature_matches_an_independent_implementation_and_detects_tampering(owner, lab):
    owner.get("/schemra/api/lab/boards?x=1&y=%20z")
    rec = lab.state["received"][-1]
    assert rec["target"] == "/api/lab/boards?x=1&y=%20z" and rec["method"] == "GET"
    assert check_signature(rec)
    assert not check_signature(rec, secret="another-secret-" + "z" * 40)
    assert not check_signature({**rec, "target": "/api/lab/boards?x=2&y=%20z"})
    assert not check_signature({**rec, "target": "/api/lab/other?x=1&y=%20z"})
    assert not check_signature({**rec, "method": "POST"})
    assert not check_signature(rec, now=time.time() + 120)  # clock skew beyond 60 s
    assert not check_signature(rec, now=time.time() - 120)
    ts = int(hdrs(rec)["x-schemra-ts"][0])
    assert abs(time.time() - ts) <= 3
    # the role and the user are covered too
    forged = {**rec, "headers": [(k, "admin" if k.lower() == "x-schemra-role" else v) for k, v in rec["headers"]]}
    owner.get("/schemra/api/lab/boards")
    h = hdrs(lab.state["received"][-1])
    assert h["x-schemra-nonce"] != hdrs(rec)["x-schemra-nonce"]  # a fresh nonce every time
    assert len(h["x-schemra-nonce"][0]) >= 32 and len(h["x-schemra-sig"][0]) == 64
    assert check_signature(forged)  # the owner already is admin; now make it differ
    forged = {**rec, "headers": [(k, "viewer" if k.lower() == "x-schemra-role" else v) for k, v in rec["headers"]]}
    assert not check_signature(forged)


def test_methods_bodies_and_encoded_paths_arrive_as_sent(owner, lab):
    r = owner.post("/schemra/api/lab/echo", json={"a": 1})
    assert r.json()["body_len"] == len('{"a":1}') and r.json()["method"] == "POST"
    assert check_signature(lab.state["received"][-1])
    for m in ("put", "patch", "delete"):
        assert getattr(owner, m)("/schemra/api/lab/echo").status_code == 200
        assert lab.state["received"][-1]["method"] == m.upper()
    owner.get("/schemra/api/lab/files/name%20with%20space%C3%A9.txt?q=a%2Fb&r=%E2%82%AC")
    rec = lab.state["received"][-1]
    assert rec["target"] == "/api/lab/files/name%20with%20space%C3%A9.txt?q=a%2Fb&r=%E2%82%AC" and check_signature(rec)
    owner.get("/schemra/")
    assert lab.state["received"][-1]["target"] == "/"
    assert owner.c.options("/schemra/api/lab/echo").status_code == 405


@pytest.mark.parametrize("target", [
    "/schemra/../api/me", "/schemra/a/../b", "/schemra/a/..", "/schemra/%2e%2e/x", "/schemra/%2E%2e/x", "/schemra/.%2e/x",
    "/schemra/a%2f..%2fb", "/schemra/a%2Fb", "/schemra//x", "/schemra/a//b", "/schemra/a%5cb", "/schemra/a%5Cb", "/schemra/a\\b",
    "/schemra/%00", "/schemra/./x", "/schemra/a/./b", "/schemra/%zz", "/schemra/%2", "/schemra/a%0d%0ab", "/schemra/x?y=%0d%0aInjected: 1",
    "/schemra/x?y=%zz", "/schemra/%7f"])
def test_traversal_and_ambiguous_paths_are_refused(live, lab, target):
    c = live.client()
    cookie = f"{live.st.cookie_name}={c.cookies.get(live.st.cookie_name)}"
    status, raw = raw_get(live.port, target, cookie)
    # (an encoded line break in the path never even matches the route's pattern: 404 instead of 400, nothing forwarded)
    assert status == (404 if "%0d%0a" in target and "?" not in target else 400), (target, raw[:200])
    assert lab.state["received"] == []


def test_non_ascii_or_space_in_the_target_is_refused(live, lab):
    c = live.client()
    cookie = f"{live.st.cookie_name}={c.cookies.get(live.st.cookie_name)}"
    for t in ("/schemra/café", "/schemra/x?y=€"):
        s = socket.create_connection(("127.0.0.1", live.port), timeout=10)
        s.sendall((f"GET {t.encode('utf-8').decode('latin-1')} HTTP/1.1\r\nHost: x\r\nCookie: {cookie}\r\nConnection: close\r\n\r\n").encode("latin-1"))
        buf = b""
        while chunk := s.recv(65536):
            buf += chunk
        s.close()
        assert b" 400 " in buf.split(b"\r\n", 1)[0], buf[:100]
    assert lab.state["received"] == []


def test_upgrade_requests_are_politely_refused(owner, lab):
    r = owner.get("/schemra/ws", headers={"Upgrade": "websocket", "Connection": "Upgrade"})
    assert r.status_code == 501 and "WebSocket" in r.json()["detail"]["message"]
    assert lab.state["received"] == []


# ---- CSRF -------------------------------------------------------------------------------------------------------------
def test_state_changing_requests_need_a_csrf_proof(owner, st, lab):
    url = "/schemra/api/lab/boards"
    bare = owner.c.post(url, json={})  # session cookie only: what a third-party form would send
    assert bare.status_code == 403 and bare.json()["detail"]["code"] == "csrf"
    forged = owner.c.post(url, json={}, headers={"x-csrf-token": "forged-" + owner.csrf})
    assert forged.status_code == 403 and forged.json()["detail"]["code"] == "csrf"
    cross = owner.c.post(url, json={}, headers={"x-csrf-token": owner.csrf, "origin": "http://evil.example"})
    assert cross.status_code == 403 and cross.json()["detail"]["code"] == "bad_origin"
    cross2 = owner.c.post(url, json={}, headers={"origin": "http://evil.example"})
    assert cross2.status_code == 403
    site = owner.c.post(url, json={}, headers={"origin": "http://testserver", "sec-fetch-site": "cross-site"})
    assert site.status_code == 403
    samesite = owner.c.post(url, json={}, headers={"origin": "http://testserver", "sec-fetch-site": "same-site"})
    assert samesite.status_code == 403  # a sibling subdomain is not this page
    assert owner.c.post(url, json={}, headers={"origin": "null"}).status_code == 403
    assert lab.state["received"] == []
    assert owner.c.get(url).status_code == 200  # reading needs no token
    ok = owner.c.post(url, json={}, headers={"x-csrf-token": owner.csrf})  # Semechka's own mechanism (scripts, the check tool)
    assert ok.status_code == 200
    page = owner.c.post(url, json={}, headers={"origin": "http://testserver", "sec-fetch-site": "same-origin"})  # Schemra's own pages
    assert page.status_code == 200
    for m in ("put", "patch", "delete"):
        assert getattr(owner.c, m)(url).status_code == 403
    assert len([r for r in lab.state["received"] if r["method"] != "GET"]) == 2


def test_strict_csrf_mode_demands_the_token(owner, st, lab):
    set_setting(st, "schemra.csrf", "token")
    url = "/schemra/api/lab/boards"
    assert owner.c.post(url, json={}, headers={"origin": "http://testserver"}).status_code == 403
    assert owner.c.post(url, json={}, headers={"origin": "http://testserver", "x-csrf-token": owner.csrf}).status_code == 200


# ---- what comes back --------------------------------------------------------------------------------------------------
def test_response_headers_are_filtered_and_the_lab_port_is_never_shown(owner, st, lab):
    r = owner.get("/schemra/noisy")
    assert r.status_code == 200
    names = {k.lower() for k in r.headers}
    for gone in ("set-cookie", "access-control-allow-origin", "access-control-allow-credentials", "x-powered-by", "via", "link", "refresh"):
        assert gone not in names, gone
    assert not owner.c.cookies.get("smk_hijack") and "other" not in owner.c.cookies
    assert str(lab.port) not in str(r.headers.multi_items())
    assert r.headers["x-schemra-version"] == "2.0"
    assert r.headers["x-frame-options"] == "SAMEORIGIN"  # the lab's own, stricter-than-nothing choice for its framed previews
    assert "private" in r.headers["cache-control"] and "public" not in r.headers["cache-control"]
    assert r.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"  # Semechka's, not the lab's
    csps = r.headers.get_list("content-security-policy")
    assert "default-src 'self'; script-src 'self'" in csps and any("object-src 'none'" in c for c in csps)


def test_defaults_apply_when_the_lab_sends_no_security_headers(owner, lab):
    r = owner.get("/schemra/")
    assert r.status_code == 200 and "<title>lab</title>" in r.text
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers.get_list("content-security-policy")
    assert any("script-src 'self'" in c and "frame-ancestors 'none'" in c for c in csp)
    assert r.headers["cache-control"] == "private, no-cache"


def test_data_endpoints_are_sandboxed_so_an_uploaded_page_cannot_use_the_session(owner, lab):
    r = owner.get("/schemra/api/lab/files/fil_1/content")
    assert "sandbox" in r.headers.get_list("content-security-policy") and r.headers["x-content-type-options"] == "nosniff"
    page = owner.get("/schemra/")
    assert "sandbox" not in page.headers.get_list("content-security-policy")  # the lab's own pages are not sandboxed


def test_redirects_stay_below_the_prefix(owner, lab):
    r = owner.c.get("/schemra/r/rel", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/schemra/api/lab/boards?x=1"
    r = owner.c.get("/schemra/r/own", follow_redirects=False)
    assert r.headers["location"] == "/schemra/api/lab/boards" and str(lab.port) not in str(r.headers)
    r = owner.c.get("/schemra/r/prefixed", follow_redirects=False)
    assert r.headers["location"] == "/schemra/already"
    r = owner.c.get("/schemra/r/external", follow_redirects=False)
    assert r.status_code == 502 and "location" not in r.headers


def test_location_rewriting_rules():
    n, c = "127.0.0.1:8820", "127.0.0.1:8820"
    f = lambda v: sp.rewrite_location(v, n, c)
    assert f("/x") == "/schemra/x" and f("x/y") == "x/y" and f("?a=1") == "?a=1"
    assert f("http://127.0.0.1:8820/a?b=1#c") == "/schemra/a?b=1#c" and f("http://127.0.0.1:8820") == "/schemra/"
    assert f("/schemra") == "/schemra" and f("/schemra/x") == "/schemra/x"
    for bad in ("https://evil.example/", "//evil.example/x", "http://127.0.0.1:9999/x", "http://127.0.0.1:8820@evil.example/",
                "javascript:alert(1)", "mailto:a@b", "/x\\y", "http://127.0.0.1:8820\\@evil.example", "", "/a\r\nSet-Cookie: x=1"):
        assert f(bad) is None, bad


def test_upstream_errors_are_generic(owner, st, lab, monkeypatch):
    r = owner.get("/schemra/api/lab/status/503")
    assert r.status_code == 503 and r.json() == {"detail": "nope"}  # the lab's own answers pass through untouched
    set_setting(st, "schemra.upstream", f"http://127.0.0.1:{_free_port()}")
    r = owner.get("/schemra/api/lab/echo")
    assert r.status_code == 502 and r.json()["detail"]["code"] == "upstream_unreachable"
    assert "127.0.0.1" not in r.text and "8820" not in r.text
    set_setting(st, "schemra.upstream", f"http://127.0.0.1:{lab.port}")
    monkeypatch.setattr(sp, "make_timeout", lambda read_s: sp.httpx.Timeout(connect=5, read=0.5, write=5, pool=5))
    t0 = time.monotonic()
    r = owner.get("/schemra/hang")
    assert r.status_code == 504 and time.monotonic() - t0 < 5 and r.json()["detail"]["code"] == "upstream_timeout"


def test_timeouts_have_the_values_the_contract_needs():
    t = sp.make_timeout(120)
    assert t.connect == 5.0 and t.read == 120 and sp.CONNECT_TIMEOUT_S == 5.0
    assert BY_ID["schemra.read_timeout_s"].min > 30 and BY_ID["schemra.read_timeout_s"].default > 30


# ---- settings -------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["http://10.1.2.3:8820", "http://192.168.1.5", "http://0.0.0.0:8820", "http://example.com:8820",
                                 "http://127.0.0.1.evil.example:8820", "http://localhost.evil.example", "https://127.0.0.1:8820",
                                 "http://127.0.0.1:8820/api", "http://user:pw@127.0.0.1:8820", "http://127.0.0.1@evil.example",
                                 "http://[::2]:8820", "http://127.0.0.1:99999", "http://127.0.0.1:8820?x=1", "ftp://127.0.0.1", "",
                                 "127.0.0.1:8820", "http://127.1.2.3.4", "http://2130706433"])
def test_non_loopback_upstreams_are_refused(owner, st, bad):
    r = owner.put("/api/admin/settings/schemra.upstream", json={"value": bad})
    assert r.status_code == 422, (bad, r.text)
    with st.db.session() as s:
        assert st.settings.deployment(s, "schemra.upstream") == "http://127.0.0.1:8820"


@pytest.mark.parametrize("good,norm", [("http://127.0.0.1:8820", "http://127.0.0.1:8820"), ("http://127.5.5.5", "http://127.5.5.5"),
                                       ("http://[::1]:8820/", "http://[::1]:8820"), ("http://localhost:8820", "http://localhost:8820")])
def test_loopback_upstreams_are_accepted(owner, st, good, norm):
    r = owner.put("/api/admin/settings/schemra.upstream", json={"value": good})
    assert r.status_code == 200, r.text
    with st.db.session() as s:
        assert st.settings.deployment(s, "schemra.upstream") == norm


def test_a_bad_upstream_that_bypassed_the_validator_is_still_refused_at_request_time(owner, st, lab):
    with st.db.session(write=True) as s:
        row = s.get(SettingValue, ("schemra.upstream", "deployment", ""))
        row.value = {"v": "http://10.9.9.9:8820"}
    r = owner.get("/schemra/api/lab/echo")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "misconfigured"
    assert lab.state["received"] == []


def test_localhost_is_connected_to_as_127_0_0_1():
    assert sp.upstream_origin("http://localhost:8820") == ("http://127.0.0.1:8820", "localhost:8820")
    assert sp.upstream_origin("http://[::1]:8820") == ("http://[::1]:8820", "[::1]:8820")
    with pytest.raises(SettingError):
        sp.upstream_origin("http://lab.internal:8820")


def test_enabling_goes_through_the_audited_settings_service(owner, app, st, cfg):
    r = owner.put("/api/admin/settings/schemra.enabled", json={"value": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required"  # it widens what is reachable
    r = owner.put("/api/admin/settings/schemra.enabled", json={"value": True, "confirm_weaker": True})
    assert r.status_code == 200
    with st.db.session() as s:
        ev = s.execute(select(AuditEvent).where(AuditEvent.action == "setting.change", AuditEvent.target == "schemra.enabled")).scalars().all()
        assert ev and ev[-1].detail["new"] is True
    assert owner.put("/api/admin/settings/schemra.read_timeout_s", json={"value": 10}).status_code == 422
    assert owner.put("/api/admin/settings/schemra.audit", json={"value": "nothing"}).status_code == 422
    member = make_user(owner, app, "x@example.test")
    assert member.put("/api/admin/settings/schemra.enabled", json={"value": False, "confirm_weaker": True}).status_code == 403


def test_the_secret_is_never_a_setting():
    assert not any(d.id.startswith("schemra.") and ("secret" in d.id or d.secret) for d in BY_ID.values())
    assert not any("file" in d.id for d in BY_ID.values() if d.id.startswith("schemra."))


# ---- the secret file ------------------------------------------------------------------------------------------------
def test_secret_file_problems_fail_closed_with_a_generic_answer(owner, lab, caplog):
    p = lab.secret_path
    os.chmod(p, 0o640)
    r = owner.get("/schemra/api/lab/echo")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "misconfigured" and SECRET not in r.text
    assert "chmod 600" in caplog.text and SECRET not in caplog.text
    os.chmod(p, 0o600)
    assert owner.get("/schemra/api/lab/echo").status_code == 200
    p.write_text("short\n")
    assert owner.get("/schemra/api/lab/echo").status_code == 503
    p.unlink()
    assert owner.get("/schemra/api/lab/echo").status_code == 503
    assert len(lab.state["received"]) == 1  # only the one request made while the file was fine


def test_secret_rotation_needs_no_restart(owner, lab):
    owner.get("/schemra/api/lab/echo")
    assert check_signature(lab.state["received"][-1])
    new = "rotated-secret-" + "r" * 40
    lab.secret_path.write_text(new)
    os.chmod(lab.secret_path, 0o600)
    owner.get("/schemra/api/lab/echo")
    rec = lab.state["received"][-1]
    assert check_signature(rec, secret=new) and not check_signature(rec, secret=SECRET)


def test_secret_never_appears_in_responses_audit_or_database(owner, st, cfg, lab):
    r = owner.get("/schemra/api/lab/echo")
    assert SECRET not in r.text and SECRET not in str(r.headers)
    owner.post("/schemra/api/lab/echo", json={"x": 1}, headers={"x-csrf-token": owner.csrf})
    blob = b""
    for n in ("semechka.db", "semechka.db-wal"):
        f = cfg.data_dir / n
        if f.exists():
            blob += f.read_bytes()
    assert SECRET.encode() not in blob
    sig = hdrs(lab.state["received"][-1])["x-schemra-sig"][0].encode()
    assert sig not in blob


# ---- audit and limits ---------------------------------------------------------------------------------------------------
def events(st):
    with st.db.session() as s:
        return [e for e in s.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars() if e.action.startswith("schemra.")]


def test_requests_and_refusals_are_audited_without_bodies_or_queries(owner, app, st, lab):
    u = person(owner, app, st, "dave@example.test", ["schemra.operate"])
    u.post("/schemra/api/lab/boards?token=abc123", json={"title": "very private text"})
    u.get("/schemra/api/lab/status/500")
    u.get("/schemra/api/lab/boards?q=1")
    make_user(owner, app, "plain@example.test").get("/schemra/api/lab/boards")
    u.c.post("/schemra/api/lab/boards", json={})  # no CSRF proof
    evs = events(st)
    got = [(e.action, e.actor, e.target, e.detail.get("role"), e.detail["method"], e.detail["status"]) for e in evs]
    assert ("schemra.request", u.user_id, "/api/lab/boards", "operator", "POST", 200) in got
    assert ("schemra.request", u.user_id, "/api/lab/status/500", "operator", "GET", 500) in got
    assert ("schemra.request", u.user_id, "/api/lab/boards", "operator", "GET", 200) in got  # default level "api"
    assert any(e.action == "schemra.denied" and e.detail["status"] == 403 and e.detail["reason"] == "forbidden" for e in evs)
    assert any(e.action == "schemra.denied" and e.detail["reason"] == "csrf" and e.actor == u.user_id for e in evs)
    dump = str([(e.target, e.detail) for e in evs])
    assert "abc123" not in dump and "very private text" not in dump and "token" not in dump
    assert all(e.ip for e in evs)
    with st.db.session() as s:
        assert audit.verify_chain(s)["ok"]


def test_audit_level_controls_what_reads_are_recorded(owner, st, lab):
    set_setting(st, "schemra.audit", "writes")
    owner.get("/schemra/api/lab/boards")
    owner.get("/schemra/")
    assert events(st) == []
    owner.post("/schemra/api/lab/boards", json={}, headers={"x-csrf-token": owner.csrf})
    assert len(events(st)) == 1
    set_setting(st, "schemra.audit", "all")
    owner.get("/schemra/")
    assert len(events(st)) == 2


def test_per_user_rate_limit(owner, app, st, lab):
    set_setting(st, "schemra.rate_per_minute", 10)
    codes = [owner.get("/schemra/api/lab/echo").status_code for _ in range(12)]
    assert codes[:10] == [200] * 10 and codes[10:] == [429, 429]
    other = person(owner, app, st, "erin@example.test", ["schemra.view"])
    assert other.get("/schemra/api/lab/echo").status_code == 200  # the limit is per person
    assert owner.get("/schemra/api/lab/echo").headers.get("retry-after") == "10"


# ---- streaming ---------------------------------------------------------------------------------------------------------
def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_event_stream_arrives_incrementally_uncompressed_and_closes_upstream_on_disconnect(live, lab):
    c = live.client()
    with c.stream("GET", "/schemra/api/lab/boards/brd_1/events?after=0", headers={"accept-encoding": "gzip, br"}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        assert "content-encoding" not in r.headers and "no-cache" in r.headers["cache-control"] and r.headers["x-accel-buffering"] == "no"
        assert "content-length" not in r.headers
        t0, seen = time.monotonic(), []
        for line in r.iter_lines():
            if line.startswith("data:"):
                seen.append(time.monotonic() - t0)
            if len(seen) >= 4:
                break
        assert seen[-1] < 3 and seen[0] < 1.5  # the stream is still open and events arrive as they are produced
        assert lab.state["sse_open"] == 1 and lab.state["sse_closed"] == 0
        assert hdrs(lab.state["received"][-1])["x-schemra-role"] == ["admin"]
    # leaving the block closes the browser's connection: the lab's generator must be told promptly
    assert wait_for(lambda: lab.state["sse_closed"] == 1, 3), "upstream stream was not closed after the client left"
    sent = lab.state["sse_sent"]
    time.sleep(0.5)
    assert lab.state["sse_sent"] <= sent + 1  # and it stopped producing


def test_many_disconnects_leave_nothing_open(live, lab):
    c = live.client()
    for _ in range(5):
        with c.stream("GET", "/schemra/api/lab/boards/brd_1/events") as r:
            next(r.iter_lines())
    assert wait_for(lambda: lab.state["sse_closed"] == 5, 5)
    from semechka.routers import schemra as route
    assert wait_for(lambda: not route._live, 3)


def test_a_stream_is_cut_after_its_time_limit_so_the_session_is_checked_again(live, lab):
    set_setting(live.st, "schemra.stream_max_seconds", 1)
    c = live.client()
    t0 = time.monotonic()
    with c.stream("GET", "/schemra/api/lab/boards/brd_1/events") as r:
        n = sum(1 for line in r.iter_lines() if line.startswith("data:"))  # ends by itself
    assert 0.8 < time.monotonic() - t0 < 4 and n >= 3
    assert wait_for(lambda: lab.state["sse_closed"] == 1, 3)


def test_signed_out_during_a_stream_cannot_reopen_it(live, lab):
    c = live.client()
    assert c.post("/api/auth/logout", headers={"x-csrf-token": c.csrf}).status_code in (200, 204)
    assert c.get("/schemra/api/lab/boards/brd_1/events").status_code == 401


def test_uploads_are_streamed_not_buffered(live, lab):
    c = live.client()
    n, size = 20, 1024 * 1024
    sent = {}

    def body():
        for _ in range(n):
            yield b"u" * size
            time.sleep(0.05)
        sent["done"] = time.monotonic()

    r = c.post("/schemra/api/lab/upload?name=big.bin", content=body(), headers={"x-csrf-token": c.csrf, "content-type": "application/octet-stream"})
    assert r.status_code == 200 and r.json()["bytes"] == n * size
    assert lab.state["upload_bytes"] == n * size
    rec = lab.state["received"][-1]
    assert "content-length" not in hdrs(rec) and hdrs(rec).get("transfer-encoding") == ["chunked"]
    assert check_signature(rec) and rec["target"] == "/api/lab/upload?name=big.bin"
    assert len(lab.state["upload_chunks"]) >= 5  # it arrived in pieces, as it was sent


def test_upload_chunks_reach_the_lab_while_the_client_is_still_sending(live, lab):
    c = live.client()
    arrived = {}

    def body():
        yield b"a" * 65536
        # wait until the lab has the first chunk; if the proxy buffered the body this would never happen
        arrived["early"] = wait_for(lambda: lab.state["upload_bytes"] >= 65536, 5)
        yield b"b" * 65536

    r = c.post("/schemra/api/lab/upload", content=body(), headers={"x-csrf-token": c.csrf})
    assert r.status_code == 200 and arrived["early"] is True and r.json()["bytes"] == 131072


def test_a_large_upload_with_a_declared_length_goes_through(live, lab):
    c = live.client()
    n = 64
    chunk = b"z" * (1024 * 1024)
    r = c.post("/schemra/api/lab/upload", content=(chunk for _ in range(n)),
               headers={"x-csrf-token": c.csrf, "content-length": str(n * len(chunk))})
    assert r.status_code == 200 and r.json()["bytes"] == n * len(chunk)
    assert hdrs(lab.state["received"][-1])["content-length"] == [str(n * len(chunk))]


def test_upload_size_limit_declared_and_undeclared(live, lab):
    set_setting(live.st, "schemra.max_upload_mb", 1)
    c = live.client()
    chunk = b"q" * (256 * 1024)
    r = c.post("/schemra/api/lab/upload", content=b"q" * (3 * 1024 * 1024), headers={"x-csrf-token": c.csrf})
    assert r.status_code == 413 and lab.state["received"] == []  # refused before anything is forwarded
    r = c.post("/schemra/api/lab/upload", content=(chunk for _ in range(16)), headers={"x-csrf-token": c.csrf})  # chunked, 4 MB
    assert r.status_code == 413
    assert lab.state["upload_bytes"] <= 2 * 1024 * 1024  # cut off while sending, not after
    ok = c.post("/schemra/api/lab/upload", content=b"q" * 1000, headers={"x-csrf-token": c.csrf})
    assert ok.status_code == 200


def test_large_downloads_stream_with_their_length(live, lab):
    c = live.client()
    with c.stream("GET", "/schemra/api/lab/big?mb=30") as r:
        assert r.headers["content-length"] == str(30 * 1048576) and r.headers["content-type"] == "application/octet-stream"
        total = sum(len(x) for x in r.iter_bytes())
    assert total == 30 * 1048576


def test_leaving_a_download_closes_it_upstream(live, lab):
    c = live.client()
    with c.stream("GET", "/schemra/api/lab/big?mb=500") as r:
        next(r.iter_bytes())
    assert wait_for(lambda: lab.state["big_closed"] == 1, 3), "upstream download was not closed after the client left"
    assert lab.state["big_sent"] < 400


def test_head_and_conditional_requests(owner, lab):
    r = owner.c.head("/schemra/api/lab/echo")
    assert r.status_code == 200 and r.content == b""


# ---- units ----------------------------------------------------------------------------------------------------------
def test_request_header_filter_unit():
    out = dict(sp.request_headers([("Cookie", "a=b"), ("Accept", "x/y"), ("X-Schemra-Role", "admin"), ("Connection", "close, X-Gone"),
                                   ("X-Gone", "1"), ("Forwarded", "for=1"), ("User-Agent", "ua")], upstream_netloc="h:1"))
    assert out == {"accept": "x/y", "user-agent": "ua", "host": "h:1", "accept-encoding": "identity"}


def test_target_unit():
    f = sp.upstream_target
    assert f(b"/schemra", b"") == "/" and f(b"/schemra/", b"") == "/" and f(b"/schemra/a/b", b"x=1") == "/a/b?x=1"
    assert f(b"/schemra/a%20b", b"") == "/a%20b" and f(b"/schemra/a%25b", b"") == "/a%25b"
    assert f(b"/schemra/a.b/.hidden/..x", b"") == "/a.b/.hidden/..x"
    with pytest.raises(sp.PathRefused):
        f(b"/schemrax", b"")
    with pytest.raises(sp.PathRefused):
        f(b"/other", b"")


def test_signing_unit_matches_the_documented_vector():
    # a fixed vector, computed by hand from the contract; the lab-side implementation must reproduce it
    key = b"k" * 32
    msg = "v1\nGET\n/api/lab/boards?x=1\n1700000000\nabcd\nalice@example.test\noperator"
    want = hmac.new(key, msg.encode(), hashlib.sha256).hexdigest()
    assert sp.sign(key, "GET", "/api/lab/boards?x=1", "1700000000", "abcd", "alice@example.test", "operator") == want
    h = dict(sp.identity_headers(key, "GET", "/a", "u", "viewer", now=1700000000.9, nonce="n1"))
    assert h["X-Schemra-Ts"] == "1700000000" and h["X-Forwarded-Prefix"] == "/schemra" and h["X-Schemra-Nonce"] == "n1"
    assert h["X-Schemra-Sig"] == hmac.new(key, b"v1\nGET\n/a\n1700000000\nn1\nu\nviewer", hashlib.sha256).hexdigest()


def test_identity_name_falls_back_to_the_id_for_unsafe_addresses():
    u = SimpleNamespace(email="  Alice@Example.Test ", id="u_1")
    assert sp.identity_name(u) == "alice@example.test"
    for bad in ("a b@example.test", "ünï@example.test", "a\nb@x", 'a"b@x', "a+tag@example.test", "a%40b@x", "x" * 70 + "@example.test"):
        assert sp.identity_name(SimpleNamespace(email=bad, id="u_1")) == "u_1"


def test_independent_modules():
    """Semechka does not import Schemra and the integration module imports nothing of it."""
    import pathlib
    root = pathlib.Path(sp.__file__).parent
    for f in (root / "schemra_proxy.py", root / "routers" / "schemra.py"):
        src = f.read_text()
        assert "import schemra\n" not in src and "from schemra " not in src and "from schemra." not in src


# ---- the deployment check script -----------------------------------------------------------------------------------------
def _check_script():
    import importlib.util
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "schemra_proxy_check.py"
    spec = importlib.util.spec_from_file_location("schemra_proxy_check", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_check_script_reports_the_role_and_prints_no_secrets(live, lab, monkeypatch, capsys):
    mod = _check_script()
    monkeypatch.setenv("SEMECHKA_CHECK_EMAIL", "owner@example.test")
    monkeypatch.setenv("SEMECHKA_CHECK_PASSWORD", PW)
    assert mod.main([live.base, "--expect-role", "admin", "--lab-port", str(lab.port)]) == 0
    out = capsys.readouterr()
    assert "ALL CHECKS PASSED" in out.out and "role Schemra assigned" in out.out
    assert PW not in out.out + out.err and "smk_" not in out.out + out.err and SECRET not in out.out + out.err
    assert mod.main([live.base, "--expect-role", "viewer", "--lab-port", str(lab.port)]) == 1
    assert "FAIL" in capsys.readouterr().out
    monkeypatch.setenv("SEMECHKA_CHECK_PASSWORD", "wrong password entirely")
    assert mod.main([live.base]) == 2
    monkeypatch.delenv("SEMECHKA_CHECK_EMAIL")
    assert mod.main([live.base]) == 2


def test_check_script_says_so_when_the_proxy_is_off(live, st, lab, monkeypatch, capsys):
    set_setting(st, "schemra.enabled", False)
    monkeypatch.setenv("SEMECHKA_CHECK_EMAIL", "owner@example.test")
    monkeypatch.setenv("SEMECHKA_CHECK_PASSWORD", PW)
    assert _check_script().main([live.base]) == 1
    assert "schemra.enabled is off" in capsys.readouterr().out
