"""Build the 0.2.0 upgrade fixture with the real 0.2.0 code (not a hand-written imitation of its schema).

    git worktree add --detach /tmp/sem-020 v0.2.0-or-commit
    PYTHONPATH=/tmp/sem-020/backend python scripts/make_upgrade_fixture.py backend/tests/fixtures/data-0.2.0

The result is a data directory (database + master key) holding what a lived-in 0.2.0 install has: an owner and a
member, a changed accent, the Carbon default theme, MFA required for administrators, a person who chose 'system',
a backend with models and grants, saved conversations with a branch, a no-history role and a saved media job.
tests/test_upgrade_020.py migrates a copy of it and checks that every one of those survives.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient

PW = "correct horse battery staple"


def main(out: Path) -> None:
    import semechka
    from semechka.app import create_app
    from semechka.config import Config
    from semechka.routers.setup import ensure_setup_token

    assert semechka.__version__ == "0.2.0", f"run this with the 0.2.0 code on PYTHONPATH (found {semechka.__version__})"
    out.mkdir(parents=True, exist_ok=False)
    cfg = Config(data_dir=out)
    cfg.background = False
    app = create_app(cfg)
    st = app.state.sem
    with TestClient(app, base_url="http://testserver") as c:
        r = c.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
            "owner": {"email": "owner@upgrade.test", "password": PW, "display_name": "Owner"}, "preset": "business",
            "settings": {"brand.org_name": "Upgrade Fixture Ltd", "brand.app_name": "Fixture AI",
                         "privacy.chat_retention_days": 30}})
        assert r.status_code == 200, r.text
        csrf = c.get("/api/me").json()["csrf"]
        h = {"x-csrf-token": csrf}

        def put(key, value, **kw):
            rr = c.put(f"/api/admin/settings/{key}", headers=h, json={"value": value, "confirm_weaker": True, **kw})
            assert rr.status_code == 200, (key, rr.text)

        put("brand.theme_default", "carbon")
        put("brand.accent_light", "#0B5E8A")
        r = c.post("/api/admin/users", headers=h, json={"email": "member@upgrade.test", "display_name": "Member",
                                                          "password": PW, "role_id": "member", "team_ids": []})
        assert r.status_code == 200, r.text
        member_id = r.json()["id"]
    from semechka import security
    from semechka.media_models import MediaJob
    from semechka.models import Backend, Conversation, Message, Model, ModelGrant, User
    with st.db.session() as s:
        owner = s.query(User).filter(User.email == "owner@upgrade.test").one()
        secret = security.new_totp_secret()
        owner.totp_secret_enc, owner.totp_enabled = st.vault.encrypt(secret, "totp"), True
        s.flush()
        from semechka.models import (
            SettingValue,  # the owner now has a factor, so requiring it is allowed
        )
        row = s.get(SettingValue, ("auth.mfa_required_admins", "deployment", ""))
        row.value = {"v": True}
        m = s.get(User, member_id)
        m.prefs = {"theme": "system", "density": "compact"}
        b = Backend(name="Spark engine", adapter="sarness", base_url="http://127.0.0.1:9", enabled=True)
        s.add(b)
        s.flush()
        chat = Model(backend_id=b.id, real_id="local:qwen3-4b-instruct-2507@Q4_K_M", alias="Qwen 4B", enabled=True,
                     pinned=True, capabilities=["chat"], meta={"family": "qwen3", "context": 262144}, available=True)
        vis = Model(backend_id=b.id, real_id="local:qwen3.6-35b-a3b@MXFP4_MOE", alias="Qwen Vision", enabled=True,
                    capabilities=["chat", "vision"], meta={}, available=True)
        img = Model(backend_id=b.id, real_id="image:sdxl-turbo", alias="SDXL Turbo", enabled=True,
                    capabilities=["image"], meta={"media": {"kind": "image", "modes": ["txt2img"]}}, available=True)
        s.add_all([chat, vis, img])
        s.flush()
        for mod in (chat, vis, img):
            s.add(ModelGrant(model_id=mod.id, subject_type="role", subject_id="member", effect="allow"))
        s.add(ModelGrant(model_id=vis.id, subject_type="user", subject_id=member_id, effect="deny"))
        conv = Conversation(user_id=member_id, title_enc=st.vault.encrypt("Trip planning", "title"), model_id=chat.id,
                            expires_at=time.time() + 30 * 86400, retention_note="Stored for 30 days")
        s.add(conv)
        s.flush()
        u1 = Message(conversation_id=conv.id, role="user", content_enc=st.vault.encrypt("Plan a day in Lisbon", "content"))
        s.add(u1)
        s.flush()
        a1 = Message(conversation_id=conv.id, parent_id=u1.id, role="assistant", model_id=chat.id,
                     content_enc=st.vault.encrypt("Start at Belem, then Alfama.", "content"))
        a2 = Message(conversation_id=conv.id, parent_id=u1.id, role="assistant", model_id=chat.id,
                     content_enc=st.vault.encrypt("Morning tram 28, afternoon LX Factory.", "content"))
        s.add_all([a1, a2])
        s.flush()
        conv.active_leaf = a2.id
        s.add(MediaJob(id="mj_fixture000000000000000", user_id=member_id, kind="image", model_id=img.id,
                       backend_id=b.id, remote_id="j_1", save=True, status="completed",
                       prompt_enc=st.vault.encrypt("a red bicycle", "media"), progress_text="Done",
                       finished_at=time.time()))
    st.db.engine.dispose()
    con = sqlite3.connect(out / "semechka.db")
    con.execute("pragma wal_checkpoint(truncate)")
    con.execute("pragma journal_mode=delete")
    con.close()
    for f in out.iterdir():
        if f.name not in ("semechka.db", "master.key"):
            f.unlink() if f.is_file() else None
    print("fixture written to", out, sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
