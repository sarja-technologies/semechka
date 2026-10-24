"""Versioned schema migrations.

`Base.metadata.create_all` creates missing TABLES but never alters existing ones, so any release that adds a column
or changes a constraint needs a migration here. The database records the schema version it was last written with;
a program older than its database refuses to start (a downgrade would otherwise corrupt data silently).

Add a migration by appending to MIGRATIONS with the next integer; it runs once, inside a transaction, in order.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection

from .db import Database


class SchemaTooNew(RuntimeError):
    pass


def add_column_if_missing(conn: Connection, table: str, column: str, ddl: str) -> None:
    cols = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in cols:
        conn.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {ddl}'))


# version -> (description, function). Version 1 is the 0.1.0 schema and needs no step.
MIGRATIONS: dict[int, tuple[str, Callable[[Connection], None]]] = {}

SCHEMA_VERSION = max([1, *MIGRATIONS])


def current_version(conn: Connection) -> int | None:
    row = conn.execute(text("select value from meta where key='schema_version'")).fetchone()
    return int(row[0]) if row else None


def apply(db: Database) -> list[str]:
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
            desc, fn = MIGRATIONS[v]
            fn(conn)
            conn.execute(text("update meta set value=:v where key='schema_version'"), {"v": str(v)})
            ran.append(f"{v}: {desc}")
    return ran
