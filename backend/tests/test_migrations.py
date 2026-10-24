import pytest
from sqlalchemy import text

from semechka import migrations
from semechka.db import Database
from semechka.state import AppState


def test_fresh_database_is_stamped_with_current_schema_version(cfg):
    st = AppState.build(cfg)
    with st.db.engine.connect() as c:
        assert migrations.current_version(c) == migrations.SCHEMA_VERSION


def test_pre_versioning_database_is_adopted_as_version_one_then_upgraded(cfg, monkeypatch):
    st = AppState.build(cfg)
    with st.db.engine.begin() as c:  # what a 0.1.0 database looks like: used, with no version row
        c.execute(text("delete from meta where key='schema_version'"))
        c.execute(text("insert into meta(key,value) values ('setup_complete','1')"))
    top = migrations.SCHEMA_VERSION
    monkeypatch.setattr(migrations, "MIGRATIONS", {**migrations.MIGRATIONS, top + 1: migrations.Step("later step", lambda conn, c: None, lambda conn, c: None)})
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", top + 1)
    ran = migrations.apply(st.db)
    assert ran[0].startswith("2:") if top == 1 else ran  # version 1 adopted, every later step ran
    with st.db.engine.connect() as c:
        assert migrations.current_version(c) == top + 1


def test_program_older_than_its_data_refuses_to_start(cfg):
    st = AppState.build(cfg)
    with st.db.engine.begin() as c:
        c.execute(text("update meta set value=:v where key='schema_version'"), {"v": str(migrations.SCHEMA_VERSION + 1)})
    with pytest.raises(migrations.SchemaTooNew) as e:
        AppState.build(cfg)
    assert "newer Semechka" in str(e.value)


def test_migrations_run_once_in_order_and_are_recorded(cfg, monkeypatch):
    st = AppState.build(cfg)
    calls = []
    top = migrations.SCHEMA_VERSION
    noop = lambda conn, c: None  # noqa: E731
    steps = {top + 1: migrations.Step("add demo column", lambda conn, c: (calls.append(1), migrations.add_column_if_missing(conn, "meta", "demo", "TEXT")), noop),
             top + 2: migrations.Step("second", lambda conn, c: calls.append(2), noop)}
    monkeypatch.setattr(migrations, "MIGRATIONS", {**migrations.MIGRATIONS, **steps})
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", top + 2)
    assert [r.split(":")[0] for r in migrations.apply(st.db)] == [str(top + 1), str(top + 2)]
    assert migrations.apply(st.db) == [] and calls == [1, 2]
    with st.db.engine.connect() as c:
        assert "demo" in [r[1] for r in c.execute(text("pragma table_info(meta)"))]
