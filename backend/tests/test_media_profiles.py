"""Composite editing profiles (OpenJutsu ...) through Semechka: discovered from the engine's registry, validated against the schema the
engine publishes, sent as multipart with source files, run as ordinary durable jobs and stored like any generation."""
import io
import json

import pytest

from . import fake_sarness as fs
from .conftest import Api, PW
from .test_media import finish

SCHEMA = {"prompt": {"type": "string", "required": True, "max_length": 2000},
          "video": {"type": "file", "kind": "video", "required": True},
          "refs": {"type": "file", "kind": "image", "multi": True, "max_files": 2},
          "strength": {"type": "float", "min": 0, "max": 1, "default": 0.5},
          "operation": {"type": "enum", "values": ["swap", "remove"], "default": "swap"}}


def profile(ready=True, reason=None):
    return {"id": "oj-h3", "name": "OpenJutsu - MiniMax H3", "category": "video", "family": "openjutsu", "official": True,
            "readiness": {"state": "ready" if ready else "not_ready", "ready": ready, "reason": reason},
            "residency": {"state": "unloaded"}, "memory": {"estimate_mb": 60000}, "inputs_schema": SCHEMA,
            "capabilities": {"operations": ["swap", "remove"]}}


@pytest.fixture
def mp(app, st, sarness_url):
    fs.reset()
    fs.STATE["profiles"] = [profile()]
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "sarness", "base_url": sarness_url, "api_key": fs.TOKEN},
        "enable_models": ["local:fake-chat", "video:fake-clip", "video:oj-h3"], "smoke_model": "local:fake-chat"})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    return api


def oj(api):
    return next(m for m in api.get("/api/media/options", params={"kind": "video"}).json()["models"] if m["alias"].lower().startswith("openjutsu") or m.get("profile"))


def send(api, mid, inputs, files, **form):
    data = {"model_id": mid, "inputs": json.dumps(inputs), **form}
    parts = [(f"file:{name}", (fn, io.BytesIO(body), mime)) for name, fn, body, mime in files]
    return api.post("/api/media/profile-jobs", data=data, files=parts or None)


MP4 = fs.make_mp4()
PNG = fs.make_png()


def test_profile_is_offered_as_a_video_model_with_the_engines_form(mp):
    m = oj(mp)
    assert m["profile"]["ready"] is True and m["profile"]["inputs_schema"]["video"]["required"] is True
    assert m["profile"]["operations"] == ["swap", "remove"]
    assert "fake-clip" in [x["alias"] for x in mp.get("/api/media/options", params={"kind": "video"}).json()["models"]]


def test_profile_job_uploads_the_files_runs_as_a_durable_job_and_stores_the_result(mp):
    mid = oj(mp)["id"]
    r = send(mp, mid, {"prompt": "replace the man with a robot", "strength": 0.7},
             [("video", "in.mp4", MP4, "video/mp4"), ("refs", "a.png", PNG, "image/png"), ("refs", "b.png", PNG, "image/png")])
    assert r.status_code == 200, r.text
    got = fs.STATE["profile_submissions"][0]
    assert got["profile"] == "oj-h3" and got["inputs"]["prompt"] == "replace the man with a robot" and got["inputs"]["strength"] == 0.7
    assert got["inputs"]["video"].startswith("up_") and len(got["inputs"]["refs"]) == 2 and all(x.startswith("up_") for x in got["inputs"]["refs"])
    assert fs.STATE["uploads"][got["inputs"]["video"]] == MP4                     # the engine received the real bytes
    j = finish(mp, r.json()["id"])
    assert j["status"] == "completed" and len(j["assets"]) == 1 and j["assets"][0]["kind"] == "video"
    a = j["assets"][0]
    assert a["prompt"] == "replace the man with a robot" and a["mime"] == "video/mp4"
    assert mp.get(f"/api/media/assets/{a['id']}/content").content[:12] == MP4[:12]


def test_missing_required_file_and_bad_inputs_are_refused_before_anything_is_sent(mp):
    mid = oj(mp)["id"]
    for inputs, files, why in (
        ({"prompt": "x"}, [], "'video' is required"),
        ({"prompt": "x"}, [("video", "a.png", PNG, "image/png")], "must be a video"),
        ({"prompt": "x", "strength": 3}, [("video", "v.mp4", MP4, "video/mp4")], "between 0 and 1"),
        ({"prompt": "x", "operation": "burn"}, [("video", "v.mp4", MP4, "video/mp4")], "one of: swap, remove"),
        ({"prompt": "x", "nonsense": 1}, [("video", "v.mp4", MP4, "video/mp4")], "Unknown setting"),
        ({"prompt": "x"}, [("video", "v.mp4", MP4, "video/mp4")] + [("refs", f"{i}.png", PNG, "image/png") for i in range(3)], "at most 2"),
        ({"prompt": "x"}, [("video", "v.mp4", MP4, "application/zip")], "must be a video or a picture"),
        ({"prompt": ""}, [("video", "v.mp4", MP4, "video/mp4")], "'prompt' is required"),
    ):
        r = send(mp, mid, inputs, files)
        assert r.status_code == 422 and why in json.dumps(r.json()), (inputs, r.text)
    assert fs.STATE["uploads"] == {} and fs.STATE["profile_submissions"] == []


def test_oversized_source_file_is_refused_using_the_deployments_limit(mp, st):
    mid = oj(mp)["id"]
    assert mp.put("/api/admin/settings/media.profile_upload_max_mb", json={"value": 1}).status_code == 200
    r = send(mp, mid, {"prompt": "x"}, [("video", "big.mp4", MP4 + b"\0" * (2 * 2**20), "video/mp4")])
    assert r.status_code == 422 and "larger than the 1 MB" in json.dumps(r.json())


def test_a_profile_the_engine_reports_not_ready_is_refused_with_the_engines_reason(st, mp):
    import asyncio

    from semechka import backends_svc
    from semechka.models import Backend
    fs.STATE["profiles"] = [profile(ready=False, reason="missing file ~/sarness-comfyui/ComfyUI/models/vae/x.safetensors")]
    with st.db.session() as s:
        bid = s.query(Backend).first().id
    asyncio.run(backends_svc.sync_models(st.db.session, st, bid))                       # the next discovery sees the engine's new verdict
    m = oj(mp)
    assert m["profile"]["ready"] is False
    r = send(mp, m["id"], {"prompt": "x"}, [("video", "v.mp4", MP4, "video/mp4")])
    assert r.status_code == 422 and "missing file" in json.dumps(r.json()) and fs.STATE["uploads"] == {}


def test_a_profile_cannot_be_started_through_the_plain_generation_endpoint(mp):
    r = mp.post("/api/media/jobs", json={"kind": "video", "model_id": oj(mp)["id"], "prompt": "x", "width": 512, "height": 320, "frames": 17, "fps": 16})
    assert r.status_code == 422 and "profile-jobs" in json.dumps(r.json())


def test_an_engine_without_a_registry_simply_has_no_profiles(app, st, sarness_url):
    fs.reset()                                                                      # profiles=None -> /v1/registry answers 404
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.video.enabled": True, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "sarness", "base_url": sarness_url, "api_key": fs.TOKEN},
        "enable_models": ["local:fake-chat", "video:fake-clip"], "smoke_model": "local:fake-chat"})
    assert r.status_code == 200
    api.csrf = api.get("/api/me").json()["csrf"]
    assert all(not m.get("profile") for m in api.get("/api/media/options", params={"kind": "video"}).json()["models"])
