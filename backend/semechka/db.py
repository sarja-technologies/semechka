from __future__ import annotations

import threading
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, url: str):
        kwargs = {}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        self._gate = threading.Lock()
        self.engine = create_engine(url, future=True, pool_pre_ping=True, **kwargs)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def _pragmas(conn, _):
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
        self.upgrade(s)
        return s

    def upgrade(self, s) -> None:
        """Make this session's NEXT transaction a writer: it takes the write lock up front and waits its turn.

        Writers first queue on an in-process gate, holding no connection while they wait. Waiting on SQLite's own lock with a
        pooled connection in hand deadlocks under load: with more waiters than pool connections, the writer that holds the
        lock needs one more connection for a nested read, and every connection is held by a waiter. A session that has been
        reading (to check a password, say) must end that transaction first: `s.rollback()`; loaded objects are reloaded."""
        if self.engine.dialect.name != "sqlite":
            return
        if not self._gate.acquire(timeout=30):
            raise OperationalError("BEGIN IMMEDIATE", {}, Exception("database is locked (write queue timed out)"))
        done = threading.Event()

        def release(*_):
            if not done.is_set():
                done.set()
                self._gate.release()
        event.listen(s, "after_transaction_end", lambda _s, tx: tx.parent is None and release())
        try:
            s.connection(execution_options={"immediate": True})
        except BaseException:
            s.close()
            release()
            raise

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
