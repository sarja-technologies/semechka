from __future__ import annotations

import socket
import threading
import time

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
                     "brand.tab_title": "Banana AI", "signup.mode": "admin", "privacy.chat_retention_days": 30,
                     "auth.mfa_policy": "optional"},  # MFA behaviour is tested on its own, not inherited from the preset
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


# ---- Sarness job-protocol double (image/video) ----------------------------------------------------------
@pytest.fixture(scope="session")
def sarness_url():
    from . import fake_sarness as fs
    srv = Server(fs.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


@pytest.fixture
def mo(app, st, sarness_url):
    """Owner of an install whose only backend is the Sarness double, with chat + image + video models enabled."""
    from . import fake_sarness as fs
    fs.reset()
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    token = ensure_setup_token(st)
    r = api.post("/api/setup/complete", headers={"x-setup-token": token}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30,
                     "auth.mfa_policy": "optional"},
        "backend": {"adapter": "sarness", "base_url": sarness_url, "api_key": fs.TOKEN},
        "enable_models": ["local:fake-chat", "image:fake-turbo", "video:fake-clip"], "smoke_model": "local:fake-chat"})
    assert r.status_code == 200, r.text
    assert r.json()["models_enabled"] == 3 and r.json()["smoke"]["ok"]
    api.csrf = api.get("/api/me").json()["csrf"]
    return api

from . import fake_llm as _fake_llm


# ---- scripted language model (agents) --------------------------------------------------------------------
@pytest.fixture(scope="session")
def script_url():
    srv = Server(_fake_llm.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}/v1"
    srv.stop()


@pytest.fixture
def env(app, st, cfg, script_url):
    """A completed install whose only backend is the scripted model, with cloud agents switched on."""
    _fake_llm.reset()
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"signup.mode": "admin", "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "openai_compat", "base_url": script_url, "external": False},
        "enable_models": ["script-model"], "smoke_model": "script-model"})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    assert api.put("/api/admin/settings/cloud.agents.enabled", json={"value": True}).status_code == 200
    _fake_llm.reset()  # setup's smoke test already called the model once; tests start from a clean script
    return api



import os as _os
import subprocess as _subprocess
import sys as _sys
import tempfile as _tempfile
from pathlib import Path as _Path

from semechka import sandbox_svc as _sandbox_svc


# ---- local stand-in for the sandbox container (tests of everything AROUND isolation) -------------------------
@pytest.fixture
def fake_sandbox(monkeypatch):
    calls = []

    def run(host, image, spec, inputs, lim, *, job_label=""):
        calls.append({"host": host, "image": image, "spec": spec, "inputs": dict(inputs), "lim": lim})
        if spec["mode"] == "tool":
            return _sandbox_svc.Result(0, False, 0.1, "ffprobe version test\n", "", {}, False, {"peak_memory_mb": 12.5})
        with _tempfile.TemporaryDirectory() as d:
            (_Path(d) / "in").mkdir()
            (_Path(d) / "out").mkdir()
            for n, b in inputs.items():
                (_Path(d) / "in" / n).write_bytes(b)
            r = _subprocess.run([_sys.executable, "-I", "-B", "-c", spec["code"]], cwd=d, capture_output=True, timeout=30,
                               env={"IN": f"{d}/in", "OUT": f"{d}/out", "PATH": _os.environ["PATH"]}, check=False)
            files = {p.name: p.read_bytes() for p in (_Path(d) / "out").iterdir() if p.is_file()}
        return _sandbox_svc.Result(r.returncode, False, 0.2, r.stdout.decode(), r.stderr.decode(), files, False, {"peak_memory_mb": 30.0})
    monkeypatch.setattr(_sandbox_svc, "run", run)
    return calls




# ---- OpenJutsu Studio: an install whose engine double publishes the full OpenJutsu profile ---------------------------------
@pytest.fixture
def sm(app, st, sarness_url):
    """Signed-in owner, long-lived event loop (edits are background tasks), engine = the scripted OpenJutsu double."""
    from .studio_helpers import boot, profile_entry
    api = boot(app, st, sarness_url, [profile_entry()])
    with api.c:
        yield api


# ---- Imajutsu: an install whose engine double publishes the native image models (flux2-klein-4b, qwen-image-edit-2511, ...) -------------------------------
@pytest.fixture
def im(app, st, sarness_url):
    """Signed-in owner, long-lived event loop (edits are background tasks), engine = the scripted image double with every Imajutsu model enabled."""
    from .imajutsu_helpers import boot_images
    api = boot_images(app, st, sarness_url)
    with api.c:
        yield api
