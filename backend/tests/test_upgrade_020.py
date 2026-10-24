"""Upgrading a real 0.2.0 data directory (built by the 0.2.0 code: scripts/make_upgrade_fixture.py), and rolling back.

The fixture's key and accounts exist only for this test."""
import shutil
import sqlite3
from pathlib import Path

import pytest
from semechka import migrations, theme_svc
from semechka.app import create_app
from semechka.config import Config
from semechka.models import Conversation, ModelGrant, User
from semechka.state import AppState
from sqlalchemy import select

from .conftest import Api

FIXTURE = Path(__file__).parent / "fixtures" / "data-0.2.0"


@pytest.fixture
def old_data(tmp_path):
    d = tmp_path / "data"
    shutil.copytree(FIXTURE, d)
    (d / "master.key").chmod(0o600)
    return d


def schema(db: Path) -> dict:
    c = sqlite3.connect(db)
    out = {t: sorted(r[1] for r in c.execute(f"pragma table_info('{t}')"))
           for (t,) in c.execute("select name from sqlite_master where type='table'")}
    c.close()
    return out


def setting(st, key):
    with st.db.session() as s:
        return st.settings.deployment(s, key)


def test_upgrade_keeps_accounts_settings_models_and_conversations(old_data):
    before = schema(old_data / "semechka.db")
    cfg = Config(data_dir=old_data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.engine.connect() as c:
        assert migrations.current_version(c) == migrations.SCHEMA_VERSION
    # look and feel: Carbon with a changed accent becomes a published Custom theme with that accent
    assert setting(st, "brand.theme_default") == "custom"
    custom = setting(st, "theme.custom")
    # the accent is the site's own; the rest comes from the release's Dark preset
    assert custom["light"]["accent"] == "#0B5E8A" and custom["dark"]["bg"] == theme_svc.preset("dark")["bg"]
    assert setting(st, "theme.follow_system") is False
    assert setting(st, "auth.mfa_policy") == "admins"
    assert setting(st, "brand.app_name") == "Fixture AI" and setting(st, "privacy.chat_retention_days") == 30
    with st.db.session() as s:
        member = s.execute(select(User).where(User.email == "member@upgrade.test")).scalar_one()
        assert member.prefs == {"theme": "system", "density": "compact"}
        conv = s.execute(select(Conversation).where(Conversation.user_id == member.id)).scalar_one()
        assert st.vault.decrypt(conv.title_enc, "title") == "Trip planning"
        assert {(g.subject_type, g.subject_id, g.effect, g.modes) for g in s.execute(select(ModelGrant)).scalars()} >= {
            ("user", member.id, "deny", None), ("role", "member", "allow", None)}
    # through the API, as the person
    app = create_app(cfg)
    api = Api(app)
    assert api.login("member@upgrade.test").status_code == 200
    rows = api.get("/api/chat/conversations").json()
    assert rows[0]["title"] == "Trip planning" and rows[0]["pinned"] is False
    t = api.get(f"/api/chat/conversations/{rows[0]['id']}/thread").json()
    assert [m["content"] for m in t["messages"]] == ["Plan a day in Lisbon", "Morning tram 28, afternoon LX Factory."]
    assert t["messages"][1]["siblings"]["count"] == 2 and t["messages"][1]["kind"] == "text"
    me = api.get("/api/me").json()
    assert me["ui"]["theme"]["active"] == "system" and me["ui"]["retention"]["show"] is False
    cat = api.get("/api/models/catalog", params={"operation": "text"}).json()
    assert cat["models"][0]["alias"] == "Qwen 4B"  # pinned first, as before
    assert "Qwen Vision" not in [m["alias"] for m in cat["models"]]  # the per-person deny still holds
    owner = Api(app)
    r = owner.login("owner@upgrade.test")
    assert r.status_code == 401 and r.json()["detail"]["code"] == "mfa_required"  # their factor still applies
    assert set(before) <= set(schema(old_data / "semechka.db"))


def test_downgrade_restores_the_020_schema_and_values(old_data):
    before = schema(old_data / "semechka.db")
    cfg = Config(data_dir=old_data)
    cfg.background = False
    st = AppState.build(cfg)
    st.db.engine.dispose()
    ran = migrations.downgrade(st.db, 1, old_data)
    assert len(ran) == migrations.SCHEMA_VERSION - 1
    st.db.engine.dispose()
    assert schema(old_data / "semechka.db") == before
    c = sqlite3.connect(old_data / "semechka.db")
    vals = dict(c.execute("select key, value from setting_values where scope_type='deployment'").fetchall())
    assert vals["brand.theme_default"] == '{"v": "carbon"}' and vals["brand.accent_light"] == '{"v": "#0B5E8A"}'
    assert vals["auth.mfa_required_admins"] == '{"v": true}'
    assert not [k for k in vals if k.startswith(("theme.", "ui.", "auth.mfa_policy"))]
    assert c.execute("select value from meta where key='schema_version'").fetchone()[0] == "1"
    assert '"theme": "system"' in c.execute("select prefs from users where email='member@upgrade.test'").fetchone()[0]
    c.close()
    # and forward again
    st2 = AppState.build(cfg)
    with st2.db.engine.connect() as conn:
        assert migrations.current_version(conn) == migrations.SCHEMA_VERSION
    assert setting(st2, "brand.theme_default") == "custom"
