import json
import os
import time

import httpx
import pytest
from sqlalchemy import select

from semechka import audit, cli
from semechka.models import AuditEvent, Conversation
from .conftest import PW, Api, Server, _free_port, make_user


def test_registry_contract_and_every_setting_has_a_working_validator():
    from semechka import settings_reg as R
    ids = [d.id for d in R.DEFS]
    assert len(ids) == len(set(ids))
    for d in R.DEFS:
        assert d.label and d.category and d.scopes and d.edit_perm
        from semechka import perms
        assert d.edit_perm in perms.BY_ID, d.id
        if not d.secret and d.id != "tech.presets":
            assert d.default is None or R.coerce(d, d.default) == d.default, d.id


def test_presets_meet_contrast_and_default_palette_is_not_purple():
    from semechka import theme_svc as T
    import colorsys
    for name in ("light", "dark", "graphite"):
        vals = T.preset(name)
        assert all(p["ok"] for p in T.contrast_report(vals)), name
        for tid in ("brand", "brand-2", "accent", "link", "btn-primary-bg", "selected"):
            r, g, b, _ = T.parse_color(vals[tid])
            h, sat, _v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
            assert sat < 0.12 or not 250 <= h * 360 <= 330, f"{name}.{tid}: first-party defaults must not be violet/purple"


def test_invalid_and_unsupported_settings_are_rejected(owner):
    bad = {"light": {"fg": "#EEEEEE"}, "dark": {}}  # light-grey text on the light background fails contrast
    r = owner.put("/api/admin/settings/theme.custom", json={"value": bad})
    assert r.status_code == 422 and "needs 4.5:1" in r.json()["detail"]["message"]
    assert owner.put("/api/admin/settings/auth.password_min_length", json={"value": 3}).status_code == 422
    assert owner.put("/api/admin/settings/signup.mode", json={"value": "everyone"}).status_code == 422
    assert owner.put("/api/admin/settings/no.such.setting", json={"value": 1}).status_code == 404
    # a switch the backend cannot honour is refused with the reason, not silently accepted
    r = owner.put("/api/admin/settings/signup.require_email_verification", json={"value": True, "confirm_weaker": True})
    assert r.status_code == 422 and "SMTP" in r.json()["detail"]["message"]
    r = owner.put("/api/admin/settings/modes.chat.enabled", json={"value": False, "scope_type": "model"})
    assert r.status_code == 422


def test_weakening_changes_need_confirmation_and_show_impact(owner):
    imp = owner.post("/api/admin/settings/privacy.chat_retention_days/impact", json={"value": -1}).json()
    assert imp["weakens_protection"] is True and imp["old"] == 30
    assert owner.put("/api/admin/settings/privacy.chat_retention_days", json={"value": -1}).status_code == 409
    assert owner.put("/api/admin/settings/privacy.chat_retention_days", json={"value": 7}).status_code == 200  # stricter: no confirm


def test_scope_merge_rules_and_effective_inspector(owner, app):
    team = owner.post("/api/admin/teams", json={"name": "Support"}).json()["id"]
    u = make_user(owner, app, "s@example.test", teams=[team])
    ins = lambda k: owner.get(f"/api/admin/settings/{k}/effective", params={"user_id": u.user_id}).json()
    # specific wins for presentation settings
    owner.put("/api/admin/settings/nav.home", json={"value": "usage"})
    owner.put("/api/admin/settings/nav.home", json={"value": "chat", "scope_type": "team", "scope_id": team})
    assert ins("nav.home")["value"] == "chat" and ins("nav.home")["source"]["scope_type"] == "team"
    # restrictive wins for limits: lowest non-zero quota across scopes
    owner.put("/api/admin/settings/quota.daily_messages", json={"value": 50})
    owner.put("/api/admin/settings/quota.daily_messages", json={"value": 500, "scope_type": "team", "scope_id": team})
    assert ins("quota.daily_messages")["value"] == 50
    # a team cannot switch on a mode the deployment has off
    owner.put("/api/admin/settings/modes.video.enabled", json={"value": False})
    owner.put("/api/admin/settings/modes.video.enabled", json={"value": True, "scope_type": "team", "scope_id": team})
    assert ins("modes.video.enabled")["value"] is False
    # resetting a field inherits again without touching others
    assert owner.delete("/api/admin/settings/nav.home", params={"scope_type": "team", "scope_id": team}).status_code == 200
    assert ins("nav.home")["value"] == "usage"
    assert owner.put("/api/admin/settings/brand.app_name", json={"value": "X", "scope_type": "team", "scope_id": team}).status_code == 422


def test_history_and_rollback(owner):
    owner.put("/api/admin/settings/brand.tab_title", json={"value": "One"})
    owner.put("/api/admin/settings/brand.tab_title", json={"value": "Two"})
    h = owner.get("/api/admin/settings-history", params={"key": "brand.tab_title"}).json()
    assert [x["new"]["v"] for x in h[:2]] == ["Two", "One"]
    assert owner.post(f"/api/admin/settings-history/{h[0]['id']}/rollback").status_code == 200
    assert owner.get("/api/public").json()["brand"]["tab_title"] == "One"


def test_export_import_excludes_secrets_and_validates(owner):
    owner.put("/api/admin/settings/smtp.host", json={"value": "smtp.example.test"})
    owner.put("/api/admin/settings/smtp.password", json={"value": "hunter2hunter2"})
    prof = owner.get("/api/admin/settings-export").json()
    assert "hunter2" not in json.dumps(prof)
    assert all(s["key"] != "smtp.password" for s in prof["settings"])
    listing = owner.get("/api/admin/settings").json()["settings"]
    pw = next(s for s in listing if s["id"] == "smtp.password")
    assert pw["value"] is True and pw["default"] is None  # read back as a boolean, never the value
    bad = {"schema": "semechka.settings/1", "settings": [{"key": "smtp.password", "value": "x"}]}
    assert owner.post("/api/admin/settings-import", json={"profile": bad, "dry_run": False}).json()["applied"] is False
    prof["settings"] = [s for s in prof["settings"] if s["key"] == "brand.org_name"]
    prof["settings"][0]["value"] = "Orange Labs"
    assert owner.post("/api/admin/settings-import", json={"profile": prof, "dry_run": True}).json()["applied"] is False
    assert owner.get("/api/public").json()["brand"]["org_name"] == "Banana Corporation"
    assert owner.post("/api/admin/settings-import", json={"profile": prof, "dry_run": False}).json()["applied"] is True
    assert owner.get("/api/public").json()["brand"]["org_name"] == "Orange Labs"


def test_brand_edits_propagate_to_public_page_and_search_finds_settings(owner):
    owner.put("/api/admin/settings/brand.app_name", json={"value": "Orange Labs AI"})
    owner.put("/api/admin/settings/brand.tab_title", json={"value": "Orange AI"})
    pub = Api(owner.c.app).get("/api/public").json()
    assert pub["brand"]["app_name"] == "Orange Labs AI" and pub["brand"]["tab_title"] == "Orange AI"
    hits = owner.get("/api/admin/settings", params={"q": "registration"}).json()["settings"]
    assert "signup.mode" in [h["id"] for h in hits]
    hits = owner.get("/api/admin/settings", params={"q": "rename platform"}).json()["settings"]
    assert "brand.app_name" in [h["id"] for h in hits]


def test_asset_upload_validates_type_and_blocks_script_svg(owner):
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = owner.c.post("/api/admin/assets", files={"file": ("l.png", png, "image/png")}, headers={"x-csrf-token": owner.csrf})
    assert r.status_code == 200
    got = owner.get(r.json()["url"])
    assert got.headers["content-type"] == "image/png" and got.headers["x-content-type-options"] == "nosniff"
    evil = b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>"
    assert owner.c.post("/api/admin/assets", files={"file": ("x.svg", evil, "image/svg+xml")}, headers={"x-csrf-token": owner.csrf}).status_code == 422
    assert owner.c.post("/api/admin/assets", files={"file": ("x.exe", b"MZ\x00", "application/octet-stream")}, headers={"x-csrf-token": owner.csrf}).status_code == 415
    assert owner.get("/api/assets/../../etc/passwd").status_code in (404, 422)


def test_audit_chain_detects_tampering_and_records_sensitive_changes(owner, st):
    owner.put("/api/admin/settings/brand.tab_title", json={"value": "Audited"})
    with st.db.session() as s:
        assert audit.verify_chain(s)["ok"] is True
        actions = [e.action for e in s.execute(select(AuditEvent)).scalars()]
        assert "setup.complete" in actions and "setting.change" in actions
    assert owner.get("/api/admin/audit/verify").json()["ok"] is True
    with st.db.session() as s:
        ev = s.execute(select(AuditEvent).where(AuditEvent.action == "setting.change")).scalars().first()
        ev.detail = {**ev.detail, "new": "forged"}
    assert owner.get("/api/admin/audit/verify").json()["ok"] is False


def test_secrets_never_reach_the_audit_log_or_backend_listing(owner):
    owner.post("/api/admin/backends", json={"name": "Keyed", "adapter": "openai_compat", "base_url": "http://127.0.0.1:1/v1", "api_key": "sk-supersecret"})
    text = json.dumps(owner.get("/api/admin/audit", params={"per_page": 200}).json()) + json.dumps(owner.get("/api/admin/backends").json())
    assert "sk-supersecret" not in text
    assert any(b["has_api_key"] for b in owner.get("/api/admin/backends").json())


def test_backup_restore_roundtrip(owner, app, st, cfg, monkeypatch, tmp_path):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    cid = m.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "remember the walrus", "model_id": mid})
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "a long backup passphrase")
    out = tmp_path / "b.smck"
    cli.cmd_backup(type("A", (), {"output": str(out)}), cfg)
    blob = out.read_bytes()
    assert b"walrus" not in blob and b"SQLite" not in blob  # encrypted
    assert oct(out.stat().st_mode & 0o777) == "0o600"
    from semechka.config import Config
    fresh = Config(data_dir=tmp_path / "restored")
    monkeypatch.setattr(cli, "_port_free", lambda *a: True)
    with pytest.raises(SystemExit):
        monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "wrong passphrase entirely")
        cli.cmd_restore(type("A", (), {"file": str(out), "force": False}), fresh)
    assert not (fresh.data_dir / "semechka.db").exists()
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "a long backup passphrase")
    cli.cmd_restore(type("A", (), {"file": str(out), "force": False}), fresh)
    fresh.background = False
    from semechka.app import create_app
    app2 = create_app(fresh)
    a2 = Api(app2)
    assert a2.login("m@example.test").status_code == 200
    conv = a2.get(f"/api/chat/conversations/{cid}").json()  # decrypts: the key came with the backup
    assert conv["messages"][0]["content"] == "remember the walrus"


def test_stop_generation_keeps_partial_text_and_frees_the_slot(owner, app, st, fake_url):
    """Real server + real socket close: stopping must persist what was generated."""
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    port = _free_port()
    srv = Server(app, port)
    srv.start()
    try:
        base = f"http://127.0.0.1:{port}"
        with httpx.Client(base_url=base) as c:
            assert c.post("/api/auth/login", json={"email": "m@example.test", "password": PW}).status_code == 200
            csrf = c.get("/api/me").json()["csrf"]
            h = {"x-csrf-token": csrf}
            cid = c.post("/api/chat/conversations", json={"model_id": mid}, headers=h).json()["id"]
            got = ""
            with c.stream("POST", f"/api/chat/conversations/{cid}/generate", json={"mode": "send", "content": "slow please", "model_id": mid}, headers=h) as r:
                for line in r.iter_lines():
                    if line.startswith("data: ") and '"delta"' in line:
                        got += json.loads(line[6:])["content"]
                    if len(got) >= 12:
                        break  # the user pressed Stop: the connection closes
            time.sleep(1.0)
            conv = c.get(f"/api/chat/conversations/{cid}").json()
            asst = conv["messages"][-1]
            assert asst["status"] == "stopped" and asst["content"].startswith("tick") and len(asst["content"]) < 60 * 4
            assert st.active.get(m.user_id, 0) == 0
    finally:
        srv.stop()


def test_cli_doctor_and_status_run(cfg, capsys, monkeypatch):
    with pytest.raises(SystemExit) as e:
        cli.cmd_doctor(None, cfg)
    out = capsys.readouterr().out
    assert "Python" in out and "TLS:" in out and e.value.code in (0, 1)
