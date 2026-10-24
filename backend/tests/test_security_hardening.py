"""Adversarial tests: CSRF, XSS payload handling, IDOR, route-level authorisation sweep, secret redaction,
session lifecycle, brute force, no-save and encryption-at-rest byte scans, backup round trip."""
from __future__ import annotations

import json
import re
import socket
import time

import pytest
from sqlalchemy import select

from semechka import cli, security
from semechka.config import Config
from semechka.models import Conversation, Message, SessionRow
from .conftest import PW, Api, make_user
from .test_chat_privacy_visibility import chat_once, model_id

XSS = ['<script>alert(1)</script>', '"><img src=x onerror=alert(1)>', "javascript:alert(1)", "<svg onload=alert(1)>"]


def db_bytes(cfg) -> bytes:
    out = b""
    for n in ("semechka.db", "semechka.db-wal"):
        p = cfg.data_dir / n
        if p.exists():
            out += p.read_bytes()
    return out


# ---- CSRF ------------------------------------------------------------------------------------------------
def test_csrf_missing_forged_and_cross_origin(owner, app):
    m = make_user(owner, app, "m@example.test")
    body = {"title": "t"}
    cid = m.post("/api/chat/conversations", json={"model_id": model_id(owner)}).json()["id"]
    url = f"/api/chat/conversations/{cid}"
    r = m.c.patch(url, json=body)  # no token
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    r = m.c.patch(url, json=body, headers={"x-csrf-token": "forged-" + m.csrf})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    # the other user's valid token is not valid for this session
    r = m.c.patch(url, json=body, headers={"x-csrf-token": owner.csrf})
    assert r.status_code == 403
    # valid token but cross-site origin
    r = m.c.patch(url, json=body, headers={"x-csrf-token": m.csrf, "origin": "http://evil.example"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "bad_origin"
    # same-origin request with the token succeeds
    r = m.c.patch(url, json=body, headers={"x-csrf-token": m.csrf, "origin": "http://testserver"})
    assert r.status_code == 200
    # state-changing unauthenticated endpoints also refuse cross-site origins
    for path in ("/api/auth/login", "/api/auth/signup", "/api/auth/forgot"):
        assert Api(app).c.post(path, json={}, headers={"origin": "http://evil.example"}).status_code == 403
    # safe methods never change state: GET with a hostile origin is allowed but cannot mutate
    assert m.c.get("/api/me", headers={"origin": "http://evil.example"}).status_code == 200


def test_session_cookie_flags_and_instance_specific_name(owner, app, tmp_path):
    a = Api(app)
    r = a.c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW})
    sc = r.headers["set-cookie"]
    assert "HttpOnly" in sc and "SameSite=lax" in sc and sc.startswith("smk_")
    other = Config(data_dir=tmp_path / "other")
    other.background = False
    from semechka.app import create_app
    assert create_app(other).state.sem.cookie_name != app.state.sem.cookie_name


# ---- XSS: stored payloads are inert data, and the browser is told not to run them --------------------------
def test_stored_payloads_are_returned_as_json_with_strict_headers(owner, app):
    m = make_user(owner, app, "x@example.test", name=XSS[0])
    mid = model_id(owner)
    cid, _, _ = chat_once(m, mid, XSS[1])
    assert m.patch(f"/api/chat/conversations/{cid}", json={"title": XSS[3]}).status_code == 200
    for p in ("/api/me", f"/api/chat/conversations/{cid}", "/api/chat/conversations"):
        r = m.get(p)
        assert r.headers["content-type"].startswith("application/json")
        assert r.headers["x-content-type-options"] == "nosniff"
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "unsafe-inline" not in csp.split("script-src")[1].split(";")[0]
        assert "frame-ancestors 'none'" in csp and r.headers["x-frame-options"] == "DENY"
    assert XSS[1] in json.dumps(m.get(f"/api/chat/conversations/{cid}").json())  # stored verbatim, escaped by the UI
    # export is served as text/markdown, never as HTML
    r = m.get(f"/api/chat/conversations/{cid}/export")
    assert r.headers["content-type"].startswith("text/markdown")


def test_branding_fields_reject_script_urls_and_bad_asset_ids(owner):
    for bad in ("javascript:alert(1)", "data:text/html,<script>1</script>", "//evil.example", "ftp://x"):
        assert owner.put("/api/admin/settings/brand.support_url", json={"value": bad}).status_code == 422
    for bad in ("../../etc/passwd", "x.png?x=1", "<script>"):
        assert owner.put("/api/admin/settings/brand.logo_asset", json={"value": bad}).status_code == 422
    assert owner.put("/api/admin/settings/brand.app_name", json={"value": XSS[0]}).status_code == 200
    pub = owner.c.get("/api/public")
    assert pub.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("svg", [b'<svg xmlns="http://www.w3.org/2000/svg" onclick="alert(1)"><rect/></svg>',
                                 b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><iframe src="x"/></foreignObject></svg>',
                                 b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
                                 b'<svg xmlns="http://www.w3.org/2000/svg"><a href="javascript:alert(1)"><rect/></a></svg>'])
def test_active_svg_uploads_rejected(owner, svg):
    r = owner.post("/api/admin/assets", files={"file": ("l.svg", svg, "image/svg+xml")})
    assert r.status_code == 422, r.text


def test_asset_route_is_sandboxed_and_traversal_proof(owner):
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
    aid = owner.post("/api/admin/assets", files={"file": ("l.png", png, "image/png")}).json()["id"]
    r = owner.c.get(f"/api/assets/{aid}")
    assert r.status_code == 200 and "sandbox" in r.headers["content-security-policy"]
    for p in ("../master.key", "..%2fmaster.key", "%2e%2e/semechka.db", "a" * 24 + ".png/../../x"):
        assert owner.c.get(f"/api/assets/{p}").status_code in (404, 405, 307, 422)


# ---- IDOR -----------------------------------------------------------------------------------------------
def test_user_cannot_touch_another_users_conversations(owner, app):
    a = make_user(owner, app, "a@example.test")
    b = make_user(owner, app, "b@example.test")
    mid = model_id(owner)
    cid, _, _ = chat_once(b, mid, "private canary B")
    conv = b.get(f"/api/chat/conversations/{cid}").json()
    b_msg = conv["messages"][0]["id"]
    base = f"/api/chat/conversations/{cid}"
    assert a.get(base).status_code == 404
    assert a.patch(base, json={"title": "pwn"}).status_code == 404
    assert a.get(base + "/export").status_code == 404
    assert a.delete(base).status_code == 404
    status, _ = a.stream(base + "/generate", {"mode": "send", "content": "hi", "model_id": mid})
    assert status == 404
    # a message id from B's conversation cannot be used as a parent, edit or regenerate target in A's own conversation
    acid = a.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    for mode in ("send", "edit", "regenerate"):
        status, _ = a.stream(f"/api/chat/conversations/{acid}/generate",
                             {"mode": mode, "content": "x", "parent_id": b_msg, "model_id": mid})
        assert status == 404, mode
    assert a.patch(f"/api/chat/conversations/{acid}", json={"active_leaf": b_msg}).status_code == 404
    assert "private canary B" not in json.dumps(a.get("/api/chat/conversations").json())
    assert "private canary B" not in json.dumps(a.get("/api/chat/conversations", params={"q": "canary"}).json())
    # B still has everything
    assert b.get(base).status_code == 200
    # usage is per-person
    assert a.get("/api/usage").json()["messages_30d"] == 0 and b.get("/api/usage").json()["messages_30d"] >= 1


def test_member_cannot_manage_invites_users_or_read_others_data(owner, app):
    a = make_user(owner, app, "a@example.test")
    inv = owner.post("/api/admin/invites", json={"role_id": "member"}).json()
    assert a.get("/api/admin/invites").status_code == 403
    assert a.delete(f"/api/admin/invites/{inv['id']}").status_code == 403
    assert a.post("/api/admin/invites", json={"role_id": "member"}).status_code == 403
    assert a.get(f"/api/admin/users/{owner.setup_result.get('owner_id', 'x')}").status_code == 403
    # the privilege cannot be self-granted through the profile route
    r = a.patch("/api/me", json={"role_id": "owner", "roles": ["owner"], "status": "active"})
    assert r.status_code in (200, 422)
    assert a.get("/api/admin/users").status_code == 403


PUBLIC = {"/api/health", "/api/public", "/api/setup/status", "/api/auth/login", "/api/auth/signup", "/api/auth/forgot",
          "/api/auth/reset", "/api/auth/verify", "/api/assets/{name}", "/api/setup/complete", "/api/setup/hardware",
          "/api/setup/backend-test"}


def _fill(path: str, real: dict | None = None) -> str:
    real = real or {}
    return re.sub(r"\{([^}:]+)[^}]*\}", lambda m: str(real.get(m.group(1), "nonexistent")), path)


def _routes(app):
    spec = app.openapi()
    for path, ops in spec["paths"].items():
        for method in ops:
            yield method.upper(), path


def test_every_non_public_route_rejects_anonymous_callers(owner, app):
    anon = Api(app)
    bad = []
    seen = 0
    for method, path in _routes(app):
        if path in PUBLIC or path == "/api/auth/logout":  # logout of no session is a harmless no-op
            continue
        seen += 1
        r = anon.c.request(method, _fill(path), json={} if method != "GET" else None)
        if r.status_code != 401:
            bad.append((method, path, r.status_code))
    assert seen > 60
    assert not bad, bad


def test_every_admin_route_rejects_a_plain_member(owner, app):
    m = make_user(owner, app, "m@example.test")
    bad, seen = [], 0
    real = {"key": "brand.app_name", "rid": 1, "mid": model_id(owner), "op": "unload", "uid": m.user_id, "bid": "x"}
    bodies = {"/api/admin/settings/{key}": {"value": "pwned"}}
    for method, path in _routes(app):
        if not path.startswith("/api/admin/"):
            continue
        seen += 1
        body = bodies.get(path, {}) if method != "GET" else None
        r = m.req(method, _fill(path, real), json=body, params={"scope_type": "user"} if method == "DELETE" else None)
        if r.status_code != 403:
            bad.append((method, path, r.status_code))
    assert seen > 50
    assert not bad, bad


def test_setup_endpoints_are_closed_after_install(owner, app):
    anon = Api(app)
    for path, body in (("/api/setup/complete", {}), ("/api/setup/hardware", None), ("/api/setup/backend-test", {})):
        r = anon.c.request("GET" if body is None else "POST", path, json=body)
        assert r.status_code in (401, 403, 404, 409, 422), (path, r.status_code)
    r = anon.post("/api/setup/complete", headers={"x-setup-token": "guess"}, json={"owner": {"email": "a@b.test", "password": PW,
                  "display_name": "x"}, "preset": "personal", "settings": {}})
    assert r.status_code in (401, 403, 404, 409)
    assert anon.get("/api/setup/status").json().get("needs_setup") in (False, None)


def test_a_member_with_a_tech_grant_still_cannot_operate(owner, app):
    m = make_user(owner, app, "m@example.test")
    owner.put(f"/api/admin/users/{m.user_id}/override", json={"grants": ["tech.resources.gpu", "tech.perf.tps"], "denies": []})
    assert m.get("/api/system/resources").status_code == 200
    assert m.post(f"/api/admin/models/{model_id(owner)}/unload").status_code == 403
    assert m.get("/api/admin/system").status_code == 403


# ---- secret redaction ---------------------------------------------------------------------------------------
def test_secrets_absent_from_exports_audit_database_and_backup(owner, app, cfg, st, monkeypatch, tmp_path):
    KEY, CANARY = "sk-canary-9f31c7", "walrus-canary-7788"
    m = make_user(owner, app, "m@example.test")
    b = owner.post("/api/admin/backends", json={"name": "K", "adapter": "openai_compat", "base_url": "http://127.0.0.1:1/v1",
                                                "api_key": KEY}).json()
    enrol = m.post("/api/auth/mfa/begin").json()
    secret = enrol["secret"]
    assert m.post("/api/auth/mfa/confirm", json={"code": security.totp_at(secret, time.time())}).status_code == 200
    cid, _, _ = chat_once(m, model_id(owner), CANARY)
    m.patch(f"/api/chat/conversations/{cid}", json={"title": CANARY + " title"})
    surfaces = [owner.get("/api/admin/settings-export").text, owner.get("/api/admin/audit", params={"per_page": 200}).text,
                owner.get("/api/admin/backends").text, owner.get("/api/admin/settings-history").text,
                owner.get("/api/admin/users").text, owner.get("/api/admin/overview").text, json.dumps(b),
                owner.get(f"/api/admin/users/{m.user_id}").text]
    for text in surfaces:
        for secret_value in (KEY, secret, PW, st.vault.mac("x", "y")[:0] or "NEVER"):
            assert secret_value not in text
        assert "password_hash" not in text and "$argon2" not in text and "totp_secret" not in text
    raw = db_bytes(cfg)
    for needle in (KEY.encode(), CANARY.encode(), secret.encode(), PW.encode()):
        assert needle not in raw, needle
    master = cfg.key_path.read_bytes()
    assert master not in raw
    assert oct(cfg.key_path.stat().st_mode & 0o777) == "0o600"
    # sealed backup: ciphertext only
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "a long backup passphrase")
    out = tmp_path / "b.smck"
    cli.cmd_backup(type("A", (), {"output": str(out)}), cfg)
    blob = out.read_bytes()
    for needle in (KEY.encode(), CANARY.encode(), secret.encode(), master, b"SQLite format"):
        assert needle not in blob


def test_logs_never_contain_credentials(owner, app, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    a = Api(app)
    a.login("owner@example.test", "wrong-password-canary-5521")
    a.login("owner@example.test", PW)
    owner.post("/api/admin/backends", json={"name": "K2", "adapter": "openai_compat", "base_url": "http://127.0.0.1:1/v1",
                                            "api_key": "sk-log-canary-1177"})
    text = caplog.text
    for needle in ("wrong-password-canary-5521", PW, "sk-log-canary-1177"):
        assert needle not in text


# ---- sessions ------------------------------------------------------------------------------------------------
def test_logout_invalidates_the_cookie_even_if_replayed(owner, app):
    m = make_user(owner, app, "m@example.test")
    stolen = dict(m.c.cookies)
    assert m.post("/api/auth/logout").status_code == 200
    replay = Api(app)
    for k, v in stolen.items():
        replay.c.cookies.set(k, v)
    assert replay.get("/api/me").status_code == 401


def test_expired_and_idle_sessions_are_refused(owner, app, st):
    m = make_user(owner, app, "m@example.test")
    assert m.get("/api/chat/models").status_code == 200
    with st.db.session() as s:
        row = s.execute(select(SessionRow).where(SessionRow.user_id == m.user_id)).scalars().first()
        row.expires_at = time.time() - 5
    assert m.get("/api/chat/models").status_code == 401
    m2 = make_user(owner, app, "m2@example.test")
    owner.put("/api/admin/settings/auth.idle_minutes", json={"value": 5, "confirm_weaker": True})
    with st.db.session() as s:
        row = s.execute(select(SessionRow).where(SessionRow.user_id == m2.user_id)).scalars().first()
        row.last_seen = time.time() - 3600
    assert m2.get("/api/chat/models").status_code == 401


def test_disabled_account_loses_access_immediately(owner, app):
    m = make_user(owner, app, "m@example.test")
    assert m.get("/api/me").status_code == 200
    assert owner.patch(f"/api/admin/users/{m.user_id}", json={"status": "suspended"}).status_code == 200
    assert m.get("/api/me").status_code == 401
    assert Api(app).login("m@example.test").status_code in (401, 403)


def test_deleted_account_cannot_reuse_its_session(owner, app):
    m = make_user(owner, app, "m@example.test")
    assert owner.delete(f"/api/admin/users/{m.user_id}").status_code == 200
    assert m.get("/api/me").status_code == 401


# ---- brute force -----------------------------------------------------------------------------------------------
def test_lockout_blocks_correct_password_and_is_audited(owner, app):
    make_user(owner, app, "v@example.test")
    a = Api(app)
    codes = [a.login("v@example.test", "bad bad bad bad").status_code for _ in range(10)]
    assert codes[0] == 401 and 429 in codes
    assert a.login("v@example.test", PW).status_code == 429
    actions = {e["action"] for e in owner.get("/api/admin/audit", params={"per_page": 200}).json()["items"]}
    assert "account.locked" in actions


def test_per_address_login_rate_limit(owner, app):
    a = Api(app)
    codes = [a.login(f"nobody{i}@example.test", "bad bad bad bad").status_code for i in range(70)]
    assert 429 in codes and codes[0] == 401


def test_signup_and_forgot_are_rate_limited(owner, app):
    owner.put("/api/admin/settings/signup.mode", json={"value": "public", "confirm_weaker": True})
    a = Api(app)
    codes = [a.post("/api/auth/forgot", json={"email": "owner@example.test"}).status_code for _ in range(14)]
    assert 429 in codes
    codes = [a.post("/api/auth/signup", json={"email": "same@example.test", "password": PW, "display_name": "s"}).status_code
             for _ in range(13)]
    assert 429 in codes


# ---- no-save and encryption at rest -----------------------------------------------------------------------------
def test_no_save_conversation_leaves_no_trace_in_the_database_file(owner, app, cfg, st):
    m = make_user(owner, app, "m@example.test")
    mid = model_id(owner)
    owner.put("/api/admin/roles/member", json={"label": "Member", "description": "", "rank": 100,
                                               "grants": ["mode.chat", "chat.ephemeral"], "denies": []})
    owner.put("/api/admin/settings/privacy.ephemeral_allowed", json={"value": True, "confirm_weaker": True})
    CAN = "ephemeral-canary-4242"
    before = len(db_bytes(cfg))
    with m.c.stream("POST", "/api/chat/ephemeral/generate", headers={"x-csrf-token": m.csrf},
                    json={"model_id": mid, "messages": [{"role": "user", "content": CAN}]}) as r:
        assert r.status_code == 200
        body = "".join(r.iter_text())
    assert "Hello from the fake model" in body
    raw = db_bytes(cfg)
    assert CAN.encode() not in raw and b"Hello from the fake model" not in raw
    with st.db.session() as s:
        assert s.execute(select(Conversation)).scalars().first() is None
        assert s.execute(select(Message)).scalars().first() is None
    assert m.get("/api/chat/conversations").json() in ([], {"items": []}) or not m.get("/api/chat/conversations").json()
    assert len(db_bytes(cfg)) >= before  # sanity: file is readable


def test_saved_chat_is_encrypted_at_rest(owner, app, cfg, st):
    m = make_user(owner, app, "m@example.test")
    CAN = "persisted-canary-9090"
    cid, _, _ = chat_once(m, model_id(owner), CAN)
    m.patch(f"/api/chat/conversations/{cid}", json={"title": CAN + "-title"})
    with st.db.session() as s:  # force the rows into the main file
        s.connection().exec_driver_sql("pragma wal_checkpoint(full)")
    raw = db_bytes(cfg)
    assert CAN.encode() not in raw and b"Hello from the fake model" not in raw
    with st.db.session() as s:
        row = s.execute(select(Message).where(Message.conversation_id == cid, Message.role == "user")).scalars().first()
        assert CAN not in row.content_enc and st.vault.decrypt(row.content_enc, "content") == CAN
        # ciphertext is bound to its purpose: a title key cannot open message content
        with pytest.raises(Exception):
            st.vault.decrypt(row.content_enc, "title")


def test_retention_expiry_removes_saved_chats(owner, app, st):
    from semechka.app import sweep_once
    m = make_user(owner, app, "m@example.test")
    cid, _, _ = chat_once(m, model_id(owner), "short lived")
    with st.db.session() as s:
        s.get(Conversation, cid).expires_at = time.time() - 1
    assert sweep_once(st)["conversations"] == 1
    assert m.get(f"/api/chat/conversations/{cid}").status_code == 404


# ---- backup / restore ------------------------------------------------------------------------------------------
def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_backup_restore_roundtrip_wrong_passphrase_and_tamper(owner, app, cfg, monkeypatch, tmp_path):
    m = make_user(owner, app, "m@example.test")
    cid, _, _ = chat_once(m, model_id(owner), "restore canary")
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "a long backup passphrase")
    out = tmp_path / "b.smck"
    cli.cmd_backup(type("A", (), {"output": str(out)}), cfg)
    target = Config(data_dir=tmp_path / "restored")
    target.port = _free_port()
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "the wrong passphrase!")
    with pytest.raises(SystemExit):
        cli.cmd_restore(type("A", (), {"file": str(out), "force": False}), target)
    assert not (target.data_dir / "semechka.db").exists()  # nothing written on failure
    tampered = tmp_path / "t.smck"
    blob = bytearray(out.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    tampered.write_bytes(bytes(blob))
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "a long backup passphrase")
    with pytest.raises(SystemExit):
        cli.cmd_restore(type("A", (), {"file": str(tampered), "force": False}), target)
    cli.cmd_restore(type("A", (), {"file": str(out), "force": False}), target)
    target.background = False
    from semechka.app import create_app
    app2 = create_app(target)
    api2 = Api(app2)
    assert api2.login("m@example.test").status_code == 200
    conv = api2.get(f"/api/chat/conversations/{cid}").json()
    assert conv["messages"][0]["content"] == "restore canary"
    short = tmp_path / "short.smck"
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "short")
    with pytest.raises(SystemExit):
        cli.cmd_backup(type("A", (), {"output": str(short)}), cfg)


def test_two_instances_do_not_share_accounts_or_sessions(owner, app, tmp_path, fake_url):
    other_cfg = Config(data_dir=tmp_path / "second")
    other_cfg.background = False
    from semechka.app import create_app
    app2 = create_app(other_cfg)
    assert Api(app2).login("owner@example.test").status_code == 401  # account exists only in the first instance
    assert app.state.sem.vault.mac("a", "b") != app2.state.sem.vault.mac("a", "b")
    assert (tmp_path / "second" / "master.key").read_bytes() != app.state.sem.cfg.key_path.read_bytes()


def test_api_reference_is_not_published_by_default(owner, app):
    a = Api(app)
    assert a.get("/api/openapi.json").status_code in (404, 405) and a.get("/api/docs").status_code in (404, 405)
