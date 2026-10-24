from .conftest import PW, Api, make_user


def test_setup_requires_token_and_is_one_time(app, st):
    from semechka.routers.setup import ensure_setup_token
    token = ensure_setup_token(st)
    api = Api(app)
    assert api.get("/api/public").json()["needs_setup"] is True
    body = {"owner": {"email": "a@example.test", "password": PW, "display_name": "A"}, "preset": "personal"}
    assert api.post("/api/setup/complete", json=body).status_code == 403
    assert api.post("/api/setup/complete", headers={"x-setup-token": "nope"}, json=body).status_code == 403
    weak = {**body, "owner": {**body["owner"], "password": "short"}}
    assert api.post("/api/setup/complete", headers={"x-setup-token": token}, json=weak).status_code == 422
    assert api.post("/api/setup/complete", headers={"x-setup-token": token}, json=body).status_code == 200
    # token is spent: setup endpoints disappear, token file removed
    assert api.post("/api/setup/complete", headers={"x-setup-token": token}, json=body).status_code == 404
    assert not st.cfg.setup_token_path.exists()
    assert api.get("/api/public").json()["needs_setup"] is False


def test_no_default_admin_and_unauthenticated_admin_rejected(app, st):
    api = Api(app)
    for path in ("/api/admin/users", "/api/admin/settings", "/api/admin/audit", "/api/me"):
        assert api.get(path).status_code == 401


def test_setup_settings_are_validated_atomically(app, st):
    from semechka.routers.setup import ensure_setup_token
    token = ensure_setup_token(st)
    api = Api(app)
    r = api.post("/api/setup/complete", headers={"x-setup-token": token}, json={
        "owner": {"email": "a@example.test", "password": PW, "display_name": "A"}, "preset": "personal",
        "settings": {"brand.accent_light": "#FFFF00"}})  # fails contrast
    assert r.status_code == 422 and r.json()["detail"]["setting"] == "brand.accent_light"
    assert api.get("/api/public").json()["needs_setup"] is True  # nothing was created


def test_owner_login_session_and_csrf(owner, app):
    me = owner.get("/api/me").json()
    assert me["user"]["email"] == "owner@example.test" and me["admin"] is True
    assert me["ui"]["brand"]["app_name"] == "Banana Corporation Private AI"
    # unsafe request without CSRF header is refused even with a valid session
    r = owner.c.post("/api/auth/password", json={"current": PW, "new": "another long passphrase"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"
    # cross-site Origin is refused
    r = owner.c.post("/api/auth/login", json={"email": "x@y.z", "password": "x"}, headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_login_errors_do_not_reveal_accounts_and_lockout(owner, app):
    a = Api(app)
    r1 = a.login("owner@example.test", "wrong password here")
    r2 = a.login("nobody@example.test", "wrong password here")
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()
    for _ in range(9):
        a.login("owner@example.test", "wrong password here")
    r = a.login("owner@example.test", PW)
    assert r.status_code == 429  # locked or throttled; correct password does not bypass it


def test_signup_closed_in_admin_mode_ui_and_api(owner, app):
    a = Api(app)
    pub = a.get("/api/public").json()
    assert pub["signup"]["open"] is False and "administrator" in pub["signup"]["message"]
    r = a.post("/api/auth/signup", json={"email": "new@example.test", "password": PW, "display_name": "N"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "signup_closed"


def test_public_signup_approval_and_no_enumeration(owner, app):
    owner.put("/api/admin/settings/signup.mode", json={"value": "public", "confirm_weaker": True})
    owner.put("/api/admin/settings/signup.require_approval", json={"value": True, "confirm_weaker": True})
    a = Api(app)
    assert a.get("/api/public").json()["signup"]["open"] is True
    body = {"email": "new@example.test", "password": PW, "display_name": "N"}
    first = a.post("/api/auth/signup", json=body)
    assert first.status_code == 200
    again = a.post("/api/auth/signup", json=body)
    existing = a.post("/api/auth/signup", json={**body, "email": "owner@example.test"})
    assert existing.status_code == 200 and existing.json()["ok"] is True
    # pending until approved; approval never grants more than the default role
    r = a.login("new@example.test")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "pending_approval"
    users = owner.get("/api/admin/users", params={"status": "pending_approval"}).json()["items"]
    assert len(users) == 1 and [x["id"] for x in users[0]["roles"]] == ["member"]
    assert owner.patch(f"/api/admin/users/{users[0]['id']}", json={"status": "active"}).status_code == 200
    assert a.login("new@example.test").status_code == 200
    assert a.get("/api/admin/users").status_code == 403


def test_switching_registration_off_closes_endpoint_but_keeps_sessions(owner, app):
    owner.put("/api/admin/settings/signup.mode", json={"value": "public", "confirm_weaker": True})
    owner.put("/api/admin/settings/signup.require_approval", json={"value": False, "confirm_weaker": True})
    a = Api(app)
    assert a.post("/api/auth/signup", json={"email": "p@example.test", "password": PW, "display_name": "P"}).status_code == 200
    assert a.login("p@example.test").status_code == 200
    owner.put("/api/admin/settings/signup.mode", json={"value": "admin"})
    b = Api(app)
    assert b.get("/api/public").json()["signup"]["open"] is False
    assert b.post("/api/auth/signup", json={"email": "q@example.test", "password": PW, "display_name": "Q"}).status_code == 403
    assert a.get("/api/me").status_code == 200  # existing session untouched


def test_invite_only_and_domain_modes(owner, app):
    owner.put("/api/admin/settings/signup.mode", json={"value": "invite", "confirm_weaker": True})
    a = Api(app)
    body = {"email": "i@example.test", "password": PW, "display_name": "I"}
    assert a.post("/api/auth/signup", json=body).status_code == 403
    inv = owner.post("/api/admin/invites", json={"role_id": "member"}).json()
    assert a.post("/api/auth/signup", json={**body, "invite": inv["token"]}).status_code == 200
    # single use
    assert a.post("/api/auth/signup", json={**body, "email": "j@example.test", "invite": inv["token"]}).status_code == 403
    owner.put("/api/admin/settings/signup.mode", json={"value": "domain", "confirm_weaker": True})
    owner.put("/api/admin/settings/signup.allowed_domains", json={"value": ["lab.example"]})
    assert a.post("/api/auth/signup", json={**body, "email": "x@other.example"}).status_code == 403
    assert a.post("/api/auth/signup", json={**body, "email": "x@lab.example"}).status_code == 200


def test_signup_default_role_cannot_be_admin_role(owner):
    r = owner.put("/api/admin/settings/signup.default_role", json={"value": "platform_admin"})
    assert r.status_code == 422


def test_mfa_enrolment_and_login(owner, app):
    import time
    from semechka import security
    b = owner.post("/api/auth/mfa/begin").json()
    code = security.totp_at(b["secret"], time.time())
    assert owner.post("/api/auth/mfa/confirm", json={"code": "000000"}).status_code == 422
    assert owner.post("/api/auth/mfa/confirm", json={"code": code}).status_code == 200
    a = Api(app)
    r = a.login("owner@example.test")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_required"
    assert a.login("owner@example.test", totp=security.totp_at(b["secret"], time.time())).status_code == 200


def test_mfa_required_for_admins_blocks_until_enrolled(owner, app):
    m = make_user(owner, app, "ops@example.test", role="platform_admin")
    # cannot require MFA while the acting admin has none (would lock them out)
    assert owner.put("/api/admin/settings/auth.mfa_required_admins", json={"value": True, "confirm_weaker": True}).status_code == 409
    import time
    from semechka import security
    b = owner.post("/api/auth/mfa/begin").json()
    owner.post("/api/auth/mfa/confirm", json={"code": security.totp_at(b["secret"], time.time())})
    assert owner.put("/api/admin/settings/auth.mfa_required_admins", json={"value": True, "confirm_weaker": True}).status_code == 200
    assert m.get("/api/admin/users").status_code == 403  # not enrolled: admin API closed
    assert m.get("/api/chat/models").status_code == 403 or m.get("/api/chat/models").json()["detail"]["code"] == "mfa_enrolment_required"
    assert m.get("/api/me").status_code == 200  # can still reach enrolment


def test_password_change_revokes_other_sessions(owner, app):
    other = Api(app)
    other.login("owner@example.test")
    r = owner.post("/api/auth/password", json={"current": PW, "new": "a brand new long passphrase"})
    assert r.status_code == 200
    assert other.get("/api/me").status_code == 401
