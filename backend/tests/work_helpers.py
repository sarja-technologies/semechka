"""Shared fixtures and helpers for the Work Mode phase B tests: the scripted agent model (fake_llm) drives the run loop, a Sarness
engine double (fake_sarness, with registry, resources and scripted chat) provides models, jobs and helper models, and the
LocalDirRuntime test double stands in for the container."""
import pytest

from semechka import jobs_svc, work_engine
from semechka.work_runtime import LocalDirRuntime

from . import fake_llm as L
from . import fake_sarness as FS
from .conftest import make_user

PROFILE = {
    "id": "openjutsu-minimax-h3", "name": "OpenJutsu - MiniMax H3", "profile": True, "family": "openjutsu", "category": "video",
    "kinds": ["video"], "official": True, "notes": "Replaces the main person of a video.",
    "readiness": {"state": "ready", "ready": True, "reason": None},
    "residency": {"state": "unloaded"}, "memory": {"estimate_mb": 86016}, "capabilities": {"operations": ["swap"], "outputs": ["video"]},
    "inputs_schema": {
        "prompt": {"type": "string", "required": True, "max_length": 500, "label": "Who is replaced"},
        "video": {"type": "file", "kind": "video", "required": True, "label": "Source video"},
        "refs": {"type": "file", "kind": "image", "required": True, "max_files": 1, "label": "Reference picture"},
        "seed": {"type": "int", "min": 0, "max": 4294967295},
        "resolution": {"type": "enum", "values": ["480p", "576p", "736p", "768p"], "default": "736p"},
        "duration": {"type": "float", "min": 2.0, "max": 15.0},
        "mask_margin": {"type": "int", "min": 0, "max": 100, "default": 40},
    },
}


def drive(st, n=30):
    return jobs_svc.run_pending(st, n)


def setting(c, key, value):
    r = c.put(f"/api/admin/settings/{key}", json={"value": value, "confirm_weaker": True})
    assert r.status_code == 200, r.text


@pytest.fixture
def wk(env, app, st, tmp_path):
    """The scripted-model install with Work Mode switched on and the test-double runtime installed."""
    st.work_runtime = LocalDirRuntime(tmp_path / "runtime")
    for k in ("work.enabled", "modes.work.enabled"):
        assert env.put(f"/api/admin/settings/{k}", json={"value": True}).status_code == 200
    return env


@pytest.fixture
def we(wk, app, st, sarness_url, monkeypatch):
    """`wk` plus a Sarness engine double as a second backend: chat models fake-chat / fake-big (vision), image fake-turbo, video
    fake-clip and the OpenJutsu profile, all enabled for everyone."""
    FS.reset()
    FS.STATE.update(registry=True, profiles=[PROFILE])
    r = wk.post("/api/admin/backends", json={"name": "Engine", "adapter": "sarness", "base_url": sarness_url, "api_key": FS.TOKEN})
    assert r.status_code == 200, r.text
    bid = r.json()["id"]
    assert wk.post(f"/api/admin/backends/{bid}/sync").status_code == 200
    items = wk.get("/api/admin/models", params={"backend": bid}).json()["items"]
    wk.engine_models = {}
    for m in items:
        if m["real_id"] in ("local:fake-chat", "local:fake-big", "image:fake-turbo", "video:fake-clip", "video:openjutsu-minimax-h3"):
            assert wk.patch(f"/api/admin/models/{m['id']}", json={"enabled": True}).status_code == 200
            assert wk.put(f"/api/admin/models/{m['id']}/grants", json={"grants": [
                {"subject_type": "everyone", "subject_id": "*", "effect": "allow"}]}).status_code == 200
            wk.engine_models[m["real_id"]] = m["id"]
    assert len(wk.engine_models) == 5, [m["real_id"] for m in items]
    for k in ("modes.image.enabled", "modes.video.enabled"):
        setting(wk, k, True)
    monkeypatch.setattr(work_engine, "POLL_S", 0.01)
    monkeypatch.setattr(work_engine, "CACHE_S", 0.0)
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 30.0)
    wk.backend_id = bid
    wk.script_model = next(m["id"] for m in wk.get("/api/work/options").json()["models"] if m["alias"] == "script-model")
    assert wk.put(f"/api/admin/models/{wk.script_model}/grants", json={"grants": [  # anyone with Work Mode may use the scripted agent model
        {"subject_type": "everyone", "subject_id": "*", "effect": "allow"}]}).status_code == 200
    yield wk
    FS.reset()


def new_session(c, **kw):
    """A session driven by the scripted model (when the engine double is installed, the default model would be one of ITS models)."""
    if getattr(c, "script_model", None) and "model_id" not in kw:
        kw["model_id"] = c.script_model
    r = c.post("/api/work/sessions", json={"title": "t", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def say_and_run(st, c, sid, text, *script):
    L.say(*script)
    r = c.post(f"/api/work/sessions/{sid}/messages", json={"content": text})
    assert r.status_code == 200, r.text
    drive(st)
    return r.json()


def events(c, sid, since=0):
    r = c.get(f"/api/work/sessions/{sid}/events", params={"since": since})
    assert r.status_code == 200, r.text
    return r.json()["events"]


def results(c, sid):
    """tool_result events: (tool name, data) in order."""
    return [e["data"] for e in events(c, sid) if e["kind"] == "tool_result"]


def last_result(c, sid):
    return results(c, sid)[-1]


def workspace_file(st, sid, rel):
    from semechka.models_work import WorkSession
    with st.db.session() as s:
        name = s.get(WorkSession, sid).container_name
    return st.work_runtime._dir(name) / rel


def worker(c, app, email="worker@acme.test"):
    """A person who holds mode.work (and chat, image, video) but NOT work.network or work.skills.manage."""
    rid = c.post("/api/admin/roles", json={"label": "Worker", "grants": ["mode.chat", "mode.work", "mode.image", "mode.video"]}).json()["id"]
    return make_user(c, app, email, role=rid)


def deny_model(c, model_id, user_id):
    r = c.put(f"/api/admin/models/{model_id}/grants", json={"grants": [
        {"subject_type": "everyone", "subject_id": "*", "effect": "allow"},
        {"subject_type": "user", "subject_id": user_id, "effect": "deny"}]})
    assert r.status_code == 200, r.text
