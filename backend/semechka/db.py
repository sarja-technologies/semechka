from __future__ import annotations

from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self.engine = create_engine(url, future=True, pool_pre_ping=True, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _):  # noqa: ANN001
                conn.isolation_level = None  # we emit BEGIN ourselves (below), so a writer can say BEGIN IMMEDIATE
                cur = conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()
            @event.listens_for(self.engine, "begin")
            def _begin(conn):
                # A deferred transaction that reads and then writes fails at once with "database is locked" (the busy timeout
                # does not apply) whenever another connection committed in between. Transactions that will write take the
                # write lock up front instead, and wait their turn for it.
                conn.exec_driver_sql("BEGIN IMMEDIATE" if conn.get_execution_options().get("immediate") else "BEGIN")
        self.Session = sessionmaker(self.engine, expire_on_commit=False, future=True)

    def writer(self):
        """A session whose transaction starts by taking the write lock (no-op on other databases)."""
        s = self.Session()
        if self.engine.dialect.name == "sqlite":
            s.connection(execution_options={"immediate": True})
        return s

    def create_all(self) -> None:
        from . import models  # noqa: F401  (register tables)
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self, write: bool = False):
        s = self.writer() if write else self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()
