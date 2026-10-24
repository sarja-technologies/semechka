"""Versioned, reversible schema migrations.

`Base.metadata.create_all` creates missing TABLES but never alters existing ones, so any release that adds a column
or changes a constraint needs a migration here. The database records the schema version it was last written with;
a program older than its database refuses to start (a downgrade would otherwise corrupt data silently).

Every step has an `up` and a `down`. `apply()` runs pending `up` steps at start-up; `downgrade(db, target)` runs `down`
steps newest-first and is what `semechka migrate --to N` calls BEFORE an older program is reinstalled (see
docs/INSTALL.md, Rollback). A `down` step removes only what its `up` added: data that exists solely because of the
newer release (for example chat attachments) is removed with it, everything else is kept.

Add a migration by appending to MIGRATIONS with the next integer; each step runs once, inside a transaction, in order.
Steps must be idempotent (safe to run against a database that create_all already brought up to date).
"""
from __future__ import annotations

import json
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .db import Database


class SchemaTooNew(RuntimeError):
    pass


def add_column_if_missing(conn: Connection, table: str, column: str, ddl: str) -> None:
    cols = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in cols:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))


def drop_column_if_present(conn: Connection, table: str, column: str) -> None:
    insp = inspect(conn)
    if table not in insp.get_table_names():
        return
    if column in {c["name"] for c in insp.get_columns(table)}:
        for ix in insp.get_indexes(table):  # SQLite refuses to drop an indexed column
            if column in (ix.get("column_names") or []):
                conn.execute(text(f'DROP INDEX IF EXISTS "{ix["name"]}"'))
        conn.execute(text(f'ALTER TABLE "{table}" DROP COLUMN "{column}"'))


def drop_table_if_present(conn: Connection, table: str) -> None:
    conn.execute(text(f'DROP TABLE IF EXISTS "{table}"'))


def _setting(conn: Connection, key: str, scope_type: str = "deployment", scope_id: str = ""):
    row = conn.execute(text("select value from setting_values where key=:k and scope_type=:t and scope_id=:i"),
                       {"k": key, "t": scope_type, "i": scope_id}).fetchone()
    if not row:
        return None
    v = row[0]
    v = json.loads(v) if isinstance(v, str) else v
    return (v or {}).get("v")


def _put_setting(conn: Connection, key: str, value, scope_type: str = "deployment", scope_id: str = "") -> None:
    conn.execute(text("delete from setting_values where key=:k and scope_type=:t and scope_id=:i"),
                 {"k": key, "t": scope_type, "i": scope_id})
    conn.execute(text("insert into setting_values(key, scope_type, scope_id, value, updated_at, updated_by) "
                      "values (:k, :t, :i, :v, :ts, 'migration')"),
                 {"k": key, "t": scope_type, "i": scope_id, "v": json.dumps({"v": value}), "ts": time.time()})


def _del_setting(conn: Connection, key: str) -> None:
    conn.execute(text("delete from setting_values where key=:k"), {"k": key})


@dataclass
class Step:
    description: str
    up: Callable[[Connection, "Ctx"], None]
    down: Callable[[Connection, "Ctx"], None]


@dataclass
class Ctx:
    data_dir: Path | None = None


# ---- 2: unified conversations -------------------------------------------------------------------
def _up_2(conn: Connection, _c: Ctx) -> None:
    add_column_if_missing(conn, "conversations", "pinned", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "conversations", "folder_id", "VARCHAR(32)")
    add_column_if_missing(conn, "messages", "kind", "VARCHAR(12) NOT NULL DEFAULT 'text'")
    add_column_if_missing(conn, "messages", "meta", "JSON")
    add_column_if_missing(conn, "media_jobs", "conversation_id", "VARCHAR(32)")
    add_column_if_missing(conn, "media_jobs", "message_id", "VARCHAR(32)")


def _down_2(conn: Connection, c: Ctx) -> None:
    # Image and video results that lived only inside a conversation stay in the gallery (media_assets is older).
    conn.execute(text("delete from messages where kind != 'text'"))
    for t, col in (("conversations", "pinned"), ("conversations", "folder_id"), ("messages", "kind"),
                   ("messages", "meta"), ("media_jobs", "conversation_id"), ("media_jobs", "message_id")):
        drop_column_if_present(conn, t, col)
    drop_table_if_present(conn, "chat_attachments")
    drop_table_if_present(conn, "conversation_folders")
    if c.data_dir:
        shutil.rmtree(c.data_dir / "attachments", ignore_errors=True)


# ---- 3: model registry ---------------------------------------------------------------------------
def _up_3(conn: Connection, _c: Ctx) -> None:
    add_column_if_missing(conn, "models", "profile", "JSON")
    add_column_if_missing(conn, "models", "sort", "INTEGER NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "models", "hidden", "BOOLEAN NOT NULL DEFAULT 0")
    add_column_if_missing(conn, "models", "defaults", "JSON")
    add_column_if_missing(conn, "model_grants", "modes", "JSON")
    # Keep the order people saw before: pinned first, then by name.
    rows = conn.execute(text("select id from models order by pinned desc, alias")).fetchall()
    for i, (mid,) in enumerate(rows):
        conn.execute(text("update models set sort=:s where id=:i"), {"s": (i + 1) * 10, "i": mid})


def _down_3(conn: Connection, _c: Ctx) -> None:
    # A mode-restricted allow would become an unrestricted allow in the older release, so it is removed instead.
    insp = inspect(conn)
    if "modes" in {c["name"] for c in insp.get_columns("model_grants")}:
        conn.execute(text("delete from model_grants where modes is not null and effect='allow'"))
    conn.execute(text("delete from model_grants where subject_type='everyone'"))
    for col in ("profile", "sort", "hidden", "defaults"):
        drop_column_if_present(conn, "models", col)
    drop_column_if_present(conn, "model_grants", "modes")
    drop_table_if_present(conn, "model_group_members")
    drop_table_if_present(conn, "model_groups")


# ---- 4: accounts: MFA policy and recovery codes --------------------------------------------------
def _up_4(conn: Connection, _c: Ctx) -> None:
    add_column_if_missing(conn, "users", "mfa_recovery", "JSON")
    if _setting(conn, "auth.mfa_policy") is None:
        if _setting(conn, "auth.mfa_required_all"):
            policy = "everyone"
        elif _setting(conn, "auth.mfa_required_admins"):
            policy = "admins"
        else:
            policy = None  # the registry default (optional) applies
        if policy:
            _put_setting(conn, "auth.mfa_policy", policy)
    _del_setting(conn, "auth.mfa_required_all")
    _del_setting(conn, "auth.mfa_required_admins")


def _down_4(conn: Connection, _c: Ctx) -> None:
    policy = _setting(conn, "auth.mfa_policy")
    if policy == "everyone":
        _put_setting(conn, "auth.mfa_required_all", True)
    elif policy in ("admins", "roles"):
        _put_setting(conn, "auth.mfa_required_admins", True)  # nearest stricter equivalent the older release has
    _del_setting(conn, "auth.mfa_policy")
    _del_setting(conn, "auth.mfa_roles")
    drop_column_if_present(conn, "users", "mfa_recovery")


# ---- 5: themes: Light, Dark, Graphite, Custom ----------------------------------------------------
_THEME_UP = {"paper": "light", "carbon": "dark", "graphite": "graphite"}
_THEME_DOWN = {"light": "paper", "dark": "carbon", "graphite": "graphite", "custom": "paper"}


def _users_prefs(conn: Connection):
    for uid, prefs in conn.execute(text("select id, prefs from users")).fetchall():
        p = json.loads(prefs) if isinstance(prefs, str) else (prefs or {})
        yield uid, (p or {})


def _up_5(conn: Connection, _c: Ctx) -> None:
    accent_l, accent_d = _setting(conn, "brand.accent_light"), _setting(conn, "brand.accent_dark")
    old = _setting(conn, "brand.theme_default")
    # what the older release had, so a rollback restores it exactly even after the Custom theme is edited
    conn.execute(text("delete from meta where key='m5_theme'"))
    conn.execute(text("insert into meta(key, value) values ('m5_theme', :v)"),
                 {"v": json.dumps({"default": old, "accent_light": accent_l, "accent_dark": accent_d})})
    # 0.2.x showed its default theme regardless of the device unless 'system' was chosen. Keep exactly that.
    _put_setting(conn, "theme.follow_system", old == "system")
    if old is not None:
        _put_setting(conn, "brand.theme_default", "light" if old == "system" else _THEME_UP.get(old, "light"))
    if accent_l or accent_d:
        # An administrator had changed the accent. Keep that look: a published Custom theme built on the presets with
        # the same accents, used as the default (the older release applied the accent on top of every preset).
        from .theme_svc import custom_from_legacy_accent
        base = _THEME_UP.get(old or "paper", "light")
        _put_setting(conn, "theme.custom", custom_from_legacy_accent(accent_l, accent_d, base))
        _put_setting(conn, "brand.theme_default", "custom")
    _del_setting(conn, "brand.accent_light")
    _del_setting(conn, "brand.accent_dark")
    # 0.2.x also allowed a per-person default theme as a setting; 0.3.0 keeps a person's theme in their preferences.
    prefs = dict(_users_prefs(conn))
    for uid, val in conn.execute(text("select scope_id, value from setting_values where key='brand.theme_default' "
                                      "and scope_type='user'")).fetchall():
        v = (json.loads(val) if isinstance(val, str) else val or {}).get("v")
        if uid in prefs and v and "theme" not in prefs[uid]:
            prefs[uid]["theme"] = v
            conn.execute(text("update users set prefs=:p where id=:i"), {"p": json.dumps(prefs[uid]), "i": uid})
    conn.execute(text("delete from setting_values where key='brand.theme_default' and scope_type='user'"))
    for uid, p in _users_prefs(conn):
        if p.get("theme") in _THEME_UP:  # 'system' keeps its meaning and its name
            p["theme"] = _THEME_UP[p["theme"]]
            conn.execute(text("update users set prefs=:p where id=:i"), {"p": json.dumps(p), "i": uid})


def _down_5(conn: Connection, _c: Ctx) -> None:
    cur = _setting(conn, "brand.theme_default")
    follow = _setting(conn, "theme.follow_system")
    row = conn.execute(text("select value from meta where key='m5_theme'")).fetchone()
    saved = json.loads(row[0]) if row else {}
    if cur == "custom":  # the theme 0.2.x had (with its accents) when this one came from the upgrade
        if saved.get("default"):
            _put_setting(conn, "brand.theme_default", saved["default"])
        else:
            _del_setting(conn, "brand.theme_default")
        for k in ("accent_light", "accent_dark"):
            if saved.get(k):
                _put_setting(conn, f"brand.{k}", saved[k])
    elif cur is not None or follow:
        _put_setting(conn, "brand.theme_default", "system" if follow and cur in (None, "light", "dark") else
                     _THEME_DOWN.get(cur or "light", "paper"))
    conn.execute(text("delete from setting_values where key='brand.theme_default' and scope_type in ('role','team')"))
    conn.execute(text("delete from meta where key='m5_theme'"))
    for k in [r[0] for r in conn.execute(text("select distinct key from setting_values where key like 'theme.%'")).fetchall()]:
        _del_setting(conn, k)
    for uid, p in _users_prefs(conn):
        if p.get("theme") in _THEME_DOWN:
            p["theme"] = _THEME_DOWN[p["theme"]]
            conn.execute(text("update users set prefs=:p where id=:i"), {"p": json.dumps(p), "i": uid})


# ---- 6: model permissions split, model display settings ------------------------------------------
# Permissions 0.4.0 introduces. Whoever could already edit the catalogue and its access rules (admin.models.manage)
# could give themselves all of these, so the upgrade grants them to exactly those roles: nobody gains power.
# new permission -> the 0.3.0 permissions whose holders receive it on upgrade (any one suffices)
_GRANTS_6 = {"admin.models.present": ("admin.models.manage",), "admin.models.defaults": ("admin.models.manage",),
             "models.view_all": ("admin.models.manage",), "models.use_all": ("admin.models.manage",),
             "models.status.view": ("admin.models.manage",),
             "ops.queue.view": ("ops.jobs.cancel_others", "ops.model.load")}
_PERMS_6 = list(_GRANTS_6)
# Settings keys (or prefixes ending in '.') that only 0.4.0 understands; removed on downgrade.
_SETTINGS_6 = ["models.display.", "models.developer_logos", "deployment.preset", "chat.enter_sends",
               "chat.prompt_history"]
_PREFS_6 = ("favourite_models", "picker", "enter_sends", "prompt_history")  # personal preferences 0.3.0 lacks


def _rows(conn: Connection, table: str):
    for rid, grants, denies in conn.execute(text(f'select {"id" if table != "user_overrides" else "user_id"}, grants, '
                                                 f'denies from "{table}"')).fetchall():
        g = json.loads(grants) if isinstance(grants, str) else (grants or [])
        d = json.loads(denies) if isinstance(denies, str) else (denies or [])
        yield rid, g or [], d or []


def _up_6(conn: Connection, _c: Ctx) -> None:
    from . import perms
    added: dict[str, list[str]] = {}
    for rid, grants, denies in _rows(conn, "roles"):
        def holds(pid, grants=grants, denies=denies):
            return any(perms._matches(x, pid, for_grant=True) for x in grants) and \
                not any(perms._matches(x, pid, for_grant=False) for x in denies)
        new = [pid for pid, needs in _GRANTS_6.items() if any(map(holds, needs)) and not holds(pid)]
        if new:
            conn.execute(text("update roles set grants=:g where id=:i"), {"g": json.dumps(grants + new), "i": rid})
            added[rid] = new
    conn.execute(text("delete from meta where key='m6_roles'"))
    conn.execute(text("insert into meta(key, value) values ('m6_roles', :v)"), {"v": json.dumps(added)})


def _down_6(conn: Connection, _c: Ctx) -> None:
    # 0.3.0 does not know these permission ids: remove every mention, wherever an administrator put them since.
    gone = set(_PERMS_6)
    for table, key in (("roles", "id"), ("teams", "id"), ("user_overrides", "user_id")):
        for rid, grants, denies in _rows(conn, table):
            g2, d2 = [x for x in grants if x not in gone], [x for x in denies if x not in gone]
            if (g2, d2) != (grants, denies):
                conn.execute(text(f'update "{table}" set grants=:g, denies=:d where {key}=:i'),
                             {"g": json.dumps(g2), "d": json.dumps(d2), "i": rid})
    conn.execute(text("delete from meta where key='m6_roles'"))
    for k in _SETTINGS_6:
        conn.execute(text("delete from setting_values where key like :k" if k.endswith(".") else
                          "delete from setting_values where key = :k"), {"k": k + "%" if k.endswith(".") else k})
        conn.execute(text("delete from setting_revisions where key like :k" if k.endswith(".") else
                          "delete from setting_revisions where key = :k"), {"k": k + "%" if k.endswith(".") else k})
    for uid, p in _users_prefs(conn):  # 0.4.0 preferences 0.3.0 would carry around unused
        if any(k in p for k in _PREFS_6):
            for k in _PREFS_6:
                p.pop(k, None)
            conn.execute(text("update users set prefs=:p where id=:i"), {"p": json.dumps(p), "i": uid})


# version -> step. Version 1 is the 0.1.0/0.2.0 schema and needs no step.
MIGRATIONS: dict[int, Step] = {
    2: Step("0.3.0: pinned conversations, folders, message kinds, attachments, media in conversations", _up_2, _down_2),
    3: Step("0.3.0: model registry (profiles, order, visibility, groups, mode-scoped access)", _up_3, _down_3),
    4: Step("0.3.0: MFA policy and recovery codes", _up_4, _down_4),
    5: Step("0.3.0: themes Light, Dark, Graphite and Custom", _up_5, _down_5),
    6: Step("0.4.0: model permissions (see all, use restricted, presentation, defaults) and model display settings",
            _up_6, _down_6),
}

SCHEMA_VERSION = max([1, *MIGRATIONS])

# Tables a step's `up` relies on create_all for; listed so `down` can be checked against them in tests.
NEW_TABLES = {2: ["conversation_folders", "chat_attachments"], 3: ["model_groups", "model_group_members"]}


def current_version(conn: Connection) -> int | None:
    row = conn.execute(text("select value from meta where key='schema_version'")).fetchone()
    return int(row[0]) if row else None


def apply(db: Database, data_dir: Path | None = None) -> list[str]:
    """Bring the database to SCHEMA_VERSION. Returns descriptions of the steps that ran."""
    ran: list[str] = []
    with db.engine.begin() as conn:
        have = current_version(conn)
        if have is None:
            fresh = conn.execute(text("select count(*) from users")).scalar() == 0 \
                and conn.execute(text("select count(*) from meta")).scalar() == 0
            have = SCHEMA_VERSION if fresh else 1  # a pre-versioning 0.1.0 database is version 1
            conn.execute(text("insert into meta(key, value) values ('schema_version', :v)"), {"v": str(have)})
        if have > SCHEMA_VERSION:
            raise SchemaTooNew(
                f"This data was written by a newer Semechka (schema {have}; this program understands up to {SCHEMA_VERSION}). "
                "Install the newer version, or restore the backup taken before the update (see docs/INSTALL.md, Rollback).")
        for v in range(have + 1, SCHEMA_VERSION + 1):
            MIGRATIONS[v].up(conn, Ctx(data_dir))
            conn.execute(text("update meta set value=:v where key='schema_version'"), {"v": str(v)})
            ran.append(f"{v}: {MIGRATIONS[v].description}")
    return ran


def downgrade(db: Database, target: int, data_dir: Path | None = None) -> list[str]:
    """Run `down` steps until the database is at `target`. The program must be the newer one (it knows the steps)."""
    if target < 1:
        raise ValueError("The oldest schema is 1.")
    ran: list[str] = []
    with db.engine.begin() as conn:
        have = current_version(conn) or 1
        if have > SCHEMA_VERSION:
            raise SchemaTooNew(f"Schema {have} is newer than this program ({SCHEMA_VERSION}); use the newer program to downgrade.")
        for v in range(have, target, -1):
            MIGRATIONS[v].down(conn, Ctx(data_dir))
            conn.execute(text("update meta set value=:v where key='schema_version'"), {"v": str(v - 1)})
            ran.append(f"{v} -> {v - 1}: undo {MIGRATIONS[v].description}")
    return ran
