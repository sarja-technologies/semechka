from __future__ import annotations

import socket
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from semechka.app import create_app
from semechka.config import Config

from . import fakebackend

PW = "correct horse battery staple"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, app, port):
        self.config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
        self.server = uvicorn.Server(self.config)
        self.port = port
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                return
            time.sleep(0.05)
        raise RuntimeError("server did not start")

    def stop(self):
        self.server.should_exit = True
        self.thread.join(timeout=5)


@pytest.fixture(scope="session")
def fake_url():
    srv = Server(fakebackend.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}/v1"
    srv.stop()


@pytest.fixture
def cfg(tmp_path):
    c = Config(data_dir=tmp_path / "data")
    c.background = False
    return c


@pytest.fixture
def app(cfg):
    return create_app(cfg)


@pytest.fixture
def st(app):
    return app.state.sem


class Api:
    """A cookie-keeping client that adds the CSRF header automatically."""

    def __init__(self, app):
        self.c = TestClient(app, base_url="http://testserver")
        self.csrf = ""

    def req(self, method, url, **kw):
        headers = kw.pop("headers", {})
        if self.csrf and method != "GET":
            headers["x-csrf-token"] = self.csrf
        return self.c.request(method, url, headers=headers, **kw)

    def get(self, url, **kw):
        return self.req("GET", url, **kw)

    def post(self, url, **kw):
        return self.req("POST", url, **kw)

    def put(self, url, **kw):
        return self.req("PUT", url, **kw)

    def patch(self, url, **kw):
        return self.req("PATCH", url, **kw)

    def delete(self, url, **kw):
        return self.req("DELETE", url, **kw)

    def login(self, email, password=PW, totp=None):
        r = self.post("/api/auth/login", json={"email": email, "password": password, **({"totp": totp} if totp else {})})
        if r.status_code == 200:
            self.csrf = self.get("/api/me").json()["csrf"]
        return r

    def stream(self, url, json_body):
        """POST and return (status, events)."""
        events = []
        with self.c.stream("POST", url, json=json_body, headers={"x-csrf-token": self.csrf}) as r:
            if r.status_code != 200:
                r.read()
                return r.status_code, r.json()
            for line in r.iter_lines():
                if line.startswith("data: "):
                    import json
                    events.append(json.loads(line[6:]))
        return 200, events


@pytest.fixture
def owner(app, st, cfg, fake_url):
    """A completed install with an owner, one backend (fake OpenAI-compatible) and one enabled model."""
    api = Api(app)
    token = None
    from semechka.routers.setup import ensure_setup_token
    token = ensure_setup_token(st)
    r = api.post("/api/setup/complete", headers={"x-setup-token": token}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"},
        "preset": "business",
        "settings": {"brand.org_name": "Banana Corporation", "brand.app_name": "Banana Corporation Private AI",
                     "brand.tab_title": "Banana AI", "signup.mode": "admin", "privacy.chat_retention_days": 30},
        "backend": {"adapter": "openai_compat", "base_url": fake_url, "external": False},
        "enable_models": ["fake-small"], "smoke_model": "fake-small"})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    api.setup_result = r.json()
    return api


def make_user(owner: Api, app, email, role="member", teams=(), name=None):
    r = owner.post("/api/admin/users", json={"email": email, "display_name": name or email.split("@")[0],
                                             "password": PW, "role_id": role, "team_ids": list(teams)})
    assert r.status_code == 200, r.text
    c = Api(app)
    assert c.login(email).status_code == 200
    c.user_id = r.json()["id"]
    return c
