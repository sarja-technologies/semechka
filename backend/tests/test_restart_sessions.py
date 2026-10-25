"""Sign-in, sign-out and sessions across real restarts of the `semechka serve` process.

Each restart is a new process on the same data directory, the way systemd restarts the service. Signing in happens
the moment /api/health answers, which is when deployment scripts start talking to a freshly started server.
"""
import os
import signal
import subprocess
import sys
import time

import httpx
import pytest

from .conftest import PW, _free_port

CYCLES = 4


class Proc:
    def __init__(self, data, port, stdout=subprocess.DEVNULL):
        self.data, self.port, self.p, self.stdout = data, port, None, stdout

    def start(self):
        env = {**os.environ, "SEMECHKA_DATA_DIR": str(self.data), "SEMECHKA_HOST": "127.0.0.1"}
        env.pop("SEMECHKA_WEB_DIR", None)
        self.p = subprocess.Popen([sys.executable, "-c", "from semechka.cli import main; main()", "serve", "--port",
                                   str(self.port)], env=env, stdout=self.stdout, stderr=subprocess.PIPE)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.p.poll() is not None:
                raise RuntimeError(self.p.stderr.read().decode()[-2000:])
            try:
                if httpx.get(f"http://127.0.0.1:{self.port}/api/health", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self):
        self.p.send_signal(signal.SIGTERM)  # what systemctl stop sends
        self.p.wait(timeout=20)


@pytest.fixture
def server(tmp_path):
    proc = Proc(tmp_path / "data", _free_port())
    proc.start()
    base = f"http://127.0.0.1:{proc.port}"
    token = (tmp_path / "data" / "setup-token").read_text().strip()
    r = httpx.post(base + "/api/setup/complete", headers={"x-setup-token": token}, timeout=30, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Owner"}, "preset": "personal"})
    assert r.status_code == 200, r.text
    yield proc, base
    if proc.p.poll() is None:
        proc.stop()


def signed_in(base):
    c = httpx.Client(base_url=base, timeout=30)
    r = c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW})
    assert r.status_code == 200, r.text
    me = c.get("/api/me")
    assert me.status_code == 200, me.text
    c.headers["x-csrf-token"] = me.json()["csrf"]
    return c


def test_sessions_survive_restarts_and_sign_in_works_the_moment_the_server_answers(server):
    proc, base = server
    kept = signed_in(base)
    for i in range(CYCLES):
        proc.stop()
        proc.start()
        fresh = signed_in(base)  # immediately after /api/health answers
        assert kept.get("/api/me").status_code == 200, f"cycle {i}: a session from before the restart was lost"
        assert kept.patch("/api/me", json={"density": "compact"}).status_code == 200  # its CSRF token still works
        assert fresh.get("/api/me").status_code == 200
        fresh.post("/api/auth/logout")
        assert fresh.get("/api/me").status_code == 401


def test_sign_out_before_a_restart_stays_signed_out_after_it(server):
    proc, base = server
    c = signed_in(base)
    cookies = dict(c.cookies)
    assert c.post("/api/auth/logout").status_code == 200
    proc.stop()
    proc.start()
    replay = httpx.Client(base_url=base, cookies=cookies, timeout=30)
    assert replay.get("/api/me").status_code == 401
    assert signed_in(base).get("/api/me").status_code == 200


def test_a_wrong_password_right_after_a_restart_is_a_plain_401_and_the_right_one_works(server):
    proc, base = server
    proc.stop()
    proc.start()
    r = httpx.post(base + "/api/auth/login", json={"email": "owner@example.test", "password": PW + "x"}, timeout=30)
    assert r.status_code == 401 and r.json()["detail"]["code"] == "bad_credentials"
    assert signed_in(base).get("/api/me").status_code == 200


def test_the_setup_token_is_never_written_to_a_log(tmp_path):
    """Under systemd, stdout is the journal: the server names the owner-only file instead of printing the token."""
    proc = Proc(tmp_path / "data", _free_port(), stdout=subprocess.PIPE)
    proc.start()
    proc.stop()
    out = proc.p.stdout.read().decode() + proc.p.stderr.read().decode()
    token = (tmp_path / "data" / "setup-token").read_text().strip()
    assert len(token) >= 16 and token not in out
    assert str(tmp_path / "data" / "setup-token") in out
