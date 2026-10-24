"""Migration 6 (0.4.0) on a copy of the real 0.2.0 data directory: who receives the new model permissions, and that
rolling back to 0.3.0 removes every mention of them, including ones an administrator added after the upgrade."""
import json
import sqlite3

from semechka import migrations
from semechka.config import Config
from semechka.state import AppState

from .test_upgrade_020 import old_data  # noqa: F401  (fixture)


def roles(db):
    c = sqlite3.connect(db)
    out = {rid: (set(json.loads(g)), set(json.loads(d))) for rid, g, d in c.execute("select id, grants, denies from roles")}
    c.close()
    return out


def test_upgrade_grants_new_model_permissions_only_to_roles_that_managed_models(old_data):  # noqa: F811
    before = roles(old_data / "semechka.db")
    cfg = Config(data_dir=old_data)
    cfg.background = False
    AppState.build(cfg).db.engine.dispose()
    after = roles(old_data / "semechka.db")
    added = {rid: after[rid][0] - before[rid][0] for rid in before}
    assert added["model_operator"] == {"admin.models.present", "admin.models.defaults", "models.view_all",
                                       "models.use_all", "models.status.view", "ops.queue.view"}
    assert added["platform_admin"] == {"models.view_all", "models.use_all", "models.status.view"}  # admin.*, ops.* cover the rest
    assert added["queue_operator"] == {"ops.queue.view"}
    for rid in ("owner", "org_admin", "member", "guest", "compliance_reviewer", "team_manager"):
        assert added[rid] == set(), rid


def test_downgrade_to_030_removes_every_mention_of_04_permissions_and_settings(old_data):  # noqa: F811
    cfg = Config(data_dir=old_data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.engine.begin() as c:  # after the upgrade an administrator used the new permissions and settings
        from sqlalchemy import text
        c.execute(text("update roles set grants=:g, denies=:d where id='member'"),
                  {"g": json.dumps(["mode.chat", "models.view_all"]), "d": json.dumps(["models.use_all"])})
        c.execute(text("insert into setting_values(scope_type, scope_id, key, value, updated_at, updated_by) values "
                       "('deployment', '', 'models.display.sort', :v, 0, '')"), {"v": json.dumps({"v": "name"})})
    st.db.engine.dispose()
    migrations.downgrade(st.db, 5, old_data)
    st.db.engine.dispose()
    db = sqlite3.connect(old_data / "semechka.db")
    every = json.dumps(db.execute("select grants, denies from roles").fetchall())
    for pid in migrations._PERMS_6:
        assert pid not in every
    assert db.execute("select count(*) from setting_values where key like 'models.display.%'").fetchone()[0] == 0
    assert db.execute("select value from meta where key='schema_version'").fetchone()[0] == "5"
    db.close()
