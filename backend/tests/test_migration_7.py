"""Migration 7 (0.4.1): image and video permissions reach the everyday roles, but only on instances that enabled a
media model; the downgrade removes exactly what the upgrade added and leaves later administrator edits alone."""
import json
import sqlite3
import time

from semechka import migrations
from semechka.config import Config
from semechka.state import AppState
from sqlalchemy import text

from .test_migration_6 import roles
from .test_upgrade_020 import old_data  # noqa: F401  (fixture)

MEDIA = {"mode.image", "mode.video"}


def at_six(old_data):  # noqa: F811
    cfg = Config(data_dir=old_data)
    cfg.background = False
    st = AppState.build(cfg)
    migrations.downgrade(st.db, 6, old_data)
    return st


def add_model(st, caps, enabled=True):
    with st.db.engine.begin() as c:
        c.execute(text("insert into backends(id, name, adapter, base_url, external, enabled, options, created_at) "
                       "values ('b1', 'b', 'sarness', 'http://x', 0, 1, '{}', :t)"), {"t": time.time()})
        c.execute(text("insert into models(id, backend_id, real_id, alias, enabled, pinned, tags, capabilities, meta, "
                       "system_prompt, gen_limits, allow_tools, available, sort, hidden, created_at) values "
                       "('m1', 'b1', 'r', 'a', :e, 0, '[]', :c, '{}', '', '{}', 0, 1, 0, 0, :t)"),
                  {"e": enabled, "c": json.dumps(caps), "t": time.time()})


def test_instances_with_an_enabled_media_model_give_everyday_roles_image_and_video(old_data):  # noqa: F811
    st = at_six(old_data)
    add_model(st, ["image"])
    before = roles(old_data / "semechka.db")
    migrations.apply(st.db, old_data)
    st.db.engine.dispose()
    after = roles(old_data / "semechka.db")
    for rid in ("platform_admin", "org_admin", "team_manager", "member"):
        assert after[rid][0] - before[rid][0] == MEDIA, rid
    for rid in ("guest", "model_operator", "queue_operator", "compliance_reviewer"):
        assert after[rid] == before[rid], rid


def test_instances_without_media_models_are_left_alone(old_data):  # noqa: F811
    st = at_six(old_data)
    with st.db.engine.begin() as c:  # the 0.2.0 fixture ships an enabled image model; switch it off
        c.execute(text("update models set enabled=0"))
    add_model(st, ["image"], enabled=False)
    before = roles(old_data / "semechka.db")
    migrations.apply(st.db, old_data)
    st.db.engine.dispose()
    assert roles(old_data / "semechka.db") == before


def test_downgrade_removes_only_what_the_upgrade_added(old_data):  # noqa: F811
    st = at_six(old_data)
    add_model(st, ["video"])
    with st.db.engine.begin() as c:  # an administrator already gave the owner-like org_admin role image by hand
        g = json.loads(c.execute(text("select grants from roles where id='org_admin'")).scalar())
        c.execute(text("update roles set grants=:g where id='org_admin'"), {"g": json.dumps(g + ["mode.image"])})
    before = roles(old_data / "semechka.db")
    migrations.apply(st.db, old_data)
    migrations.downgrade(st.db, 6, old_data)
    st.db.engine.dispose()
    assert roles(old_data / "semechka.db") == before
    db = sqlite3.connect(old_data / "semechka.db")
    assert db.execute("select count(*) from meta where key='m7_roles'").fetchone()[0] == 0
    db.close()
