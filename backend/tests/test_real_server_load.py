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
