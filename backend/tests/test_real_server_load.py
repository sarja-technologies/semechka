"""Concurrency over real sockets. The in-process TestClient runs requests one at a time and cannot show lock contention."""
import concurrent.futures as cf
import time

import httpx

from .conftest import PW, Server, _free_port, make_user


def test_many_people_signing_in_and_uploading_at_once_over_a_real_server(owner, app, st):
    people = [f"u{i}@real.test" for i in range(24)]
    for e in people:
        make_user(owner, app, e)
    st.cfg.background = True  # the real service runs the job pump, sweepers and backfills alongside requests
    srv = Server(app, _free_port())
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        def work(email):
            c = httpx.Client(base_url=base, headers={"Origin": base}, timeout=60)
            codes = []
            import os
            mode = os.environ.get("BISECT", "all")
            for _ in range(2):
                codes.append(c.post("/api/auth/login", json={"email": email, "password": PW}).status_code)
            if mode == "login":
                return codes
            csrf = c.get("/api/me").json()["csrf"]
            codes.append(c.post("/api/orgs", json={"name": email}, headers={"x-csrf-token": csrf}).status_code)  # 403 for a member: still a request
            return codes
        t = time.time()
        with cf.ThreadPoolExecutor(16) as ex:
            res = list(ex.map(work, people))
        flat = {c for r in res for c in r}
        assert flat <= {200, 403}, (sorted(flat), time.time() - t)
    finally:
        srv.stop()


def test_only_short_synchronous_writers_take_the_write_lock_at_the_start(owner, app, st, monkeypatch):
    """A handler that waits on something else (a client's upload, an engine call) must not hold the lock while it waits."""
    from semechka.db import Database

    from .test_files_http import company, up
    acme, c = company(owner, app, "Gate", "boss@gate.test", {})
    boss = c["boss@gate.test"]
    calls = []
    real_writer, real_upgrade = Database.writer, Database.upgrade
    monkeypatch.setattr(Database, "writer", lambda self: (calls.append("writer"), real_writer(self))[1])
    monkeypatch.setattr(Database, "upgrade", lambda self, s: (calls.append("upgrade"), real_upgrade(self, s))[1])
    wid = boss.post(f"/api/orgs/{acme}/workspaces", json={"name": "W"}).json()["id"]  # a short synchronous writer
    assert calls == ["writer", "upgrade"], calls  # writer() itself upgrades; one gate acquisition
    calls.clear()
    assert up(boss, wid, "a.txt", b"hello").status_code == 200  # a coroutine: reads the body first
    assert calls == ["upgrade"], calls  # ...and takes the lock only after the body is in
    calls.clear()
    assert boss.get(f"/api/workspaces/{wid}/files").status_code == 200  # a read
    assert calls == []


def test_session_activity_is_kept_in_memory_and_saved_in_batches(owner, app, st):
    """Every request used to write sessions.last_seen: a write on a read, which could fail with 'locked'. Now it is a dict."""
    from sqlalchemy import select

    from semechka.deps import flush_seen
    from semechka.models import SessionRow
    with st.db.session() as s:
        before = {r.id_hash: r.last_seen for r in s.execute(select(SessionRow)).scalars()}
    owner.get("/api/me")
    with st.db.session() as s:
        assert {r.id_hash: r.last_seen for r in s.execute(select(SessionRow)).scalars()} == before  # nothing written yet
    # a session that was last seen 10 minutes ago and is used now
    h = next(iter(before))
    with st.db.session(write=True) as s:
        s.get(SessionRow, h).last_seen -= 600
    owner.get("/api/me")
    assert h in st.seen
    assert flush_seen(st) >= 1 and h not in st.seen
    with st.db.session() as s:
        assert s.get(SessionRow, h).last_seen > before[h] - 5


def test_concurrent_uploads_over_a_real_server_do_not_deadlock_the_event_loop(owner, app, st):
    """An upload is a coroutine; waiting for the write lock on the event loop would stop the request that holds the lock
    from finishing (seen live: every writer timed out together after 30 seconds)."""
    from .test_files_http import company
    acme, c = company(owner, app, "Up", "boss@up.test", {})
    boss = c["boss@up.test"]
    people = [f"w{i}@up.test" for i in range(16)]
    for e in people:
        make_user(owner, app, e)
        assert boss.post(f"/api/orgs/{acme}/members", json={"email": e, "roles": ["dept_admin"]}).status_code == 200
    st.cfg.background = True
    srv = Server(app, _free_port())
    srv.start()
    base = f"http://127.0.0.1:{srv.port}"
    try:
        def work(email):
            cl = httpx.Client(base_url=base, headers={"Origin": base}, timeout=60)
            assert cl.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
            h = {"x-csrf-token": cl.get("/api/me").json()["csrf"]}
            wid = cl.post(f"/api/orgs/{acme}/workspaces", json={"name": email}, headers=h).json()["id"]
            return [cl.post(f"/api/workspaces/{wid}/files", files={"file": (f"f{n}.txt", b"hello " * 400)}, headers=h).status_code for n in range(6)]
        t = time.time()
        with cf.ThreadPoolExecutor(16) as ex:
            res = list(ex.map(work, people))
        assert {c for r in res for c in r} == {200}
        assert time.time() - t < 25  # a deadlock shows as 30-second timeouts
    finally:
        srv.stop()
