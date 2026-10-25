"""A write is stored before the client is told it succeeded.

Found while investigating an unexplained 401 in a deployment check that signed in and called /api/me straight away:
FastAPI (since 0.118) runs a yield dependency's exit code after the response is sent, so the session row was committed
after the client already had its cookie. Commits are slowed down here so the window is wide enough to hit every time.
"""
import time

import httpx
import pytest
from sqlalchemy import event

from .conftest import PW, Server, _free_port

SLOW = 0.4


@pytest.fixture
def slow_commits(st):
    def wait(session):
        time.sleep(SLOW)
    event.listen(st.db.Session, "before_commit", wait)
    yield
    event.remove(st.db.Session, "before_commit", wait)


@pytest.fixture
def live(app, owner):
    srv = Server(app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


def test_a_session_exists_as_soon_as_sign_in_answers(live, slow_commits):
    c = httpx.Client(base_url=live, timeout=30)
    t0 = time.monotonic()
    assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW}).status_code == 200
    assert time.monotonic() - t0 >= SLOW  # the answer waited for the commit
    me = c.get("/api/me")
    assert me.status_code == 200, me.text


def test_sign_out_has_taken_effect_when_it_answers(live, slow_commits):
    c = httpx.Client(base_url=live, timeout=30)
    c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW})
    csrf = c.get("/api/me").json()["csrf"]
    cookies = dict(c.cookies)
    assert c.post("/api/auth/logout", headers={"x-csrf-token": csrf}).status_code == 200
    replay = httpx.Client(base_url=live, cookies=cookies, timeout=30)
    assert replay.get("/api/me").status_code == 401  # the old cookie is dead the moment sign-out answered


def test_a_failed_commit_is_an_error_not_a_success(live, st):
    def boom(session):
        raise RuntimeError("disk full")
    c = httpx.Client(base_url=live, timeout=30)
    event.listen(st.db.Session, "before_commit", boom)
    try:
        r = c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW})
    finally:
        event.remove(st.db.Session, "before_commit", boom)
    assert r.status_code == 500 and "set-cookie" not in r.headers  # no cookie for a session that was never stored
    again = httpx.Client(base_url=live, cookies=dict(c.cookies), timeout=30)  # the server closes a connection after a 500
    assert again.get("/api/me").status_code == 401
