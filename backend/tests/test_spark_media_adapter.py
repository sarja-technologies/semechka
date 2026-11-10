"""The Spark media engine adapter (adapters/spark_media.py) through the real app, database, vault and disk. Only the engine is a double
(fake_spark_media.py), and the catalog it serves is a real capture of the engine's /v1/capabilities."""
import asyncio

import pytest
from semechka import adapters, media_svc
from semechka.adapters import AdapterError
from semechka.adapters.spark_media import SparkMediaAdapter, discovered_from_capabilities
from semechka.models import Backend, Model
from sqlalchemy import select

from . import fake_spark_media as fm
from .conftest import PW, Api, Server, _free_port

IMAGE = ["z-image-turbo", "qwen-image-2512", "flux2-klein-4b", "qwen-image-edit-2511", "flux1-fill-dev"]
VIDEO = ["minimax-h3", "ltx-2.3", "wan2.2-ti2v-5b", "wan2.2-i2v-a14b"]
PROFILES = ["opensfield-video-edit", "reverse-video"]


@pytest.fixture(scope="module")
def spark_url():
    srv = Server(fm.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


@pytest.fixture
def sp(app, st, spark_url):
    fm.reset()
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "spark-media", "base_url": spark_url, "api_key": fm.TOKEN},
        "enable_models": [f"image:{m}" for m in IMAGE] + [f"video:{m}" for m in VIDEO + PROFILES]})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    assert api.put("/api/admin/settings/media.max_concurrent_jobs", json={"value": 16}).status_code == 200
    return api


def models(api, kind):
    return {m["alias"]: m for m in api.get("/api/media/options", params={"kind": kind}).json()["models"]}


def finish(api, jid, tries=12):
    for _ in range(tries):
        j = api.get(f"/api/media/jobs/{jid}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
    return j


def test_the_adapter_is_registered_and_media_only():
    assert adapters.REGISTRY["spark-media"] is SparkMediaAdapter
    mf = SparkMediaAdapter.manifest
    assert mf.media and mf.worker_stop and mf.model_unload and not mf.model_load


def test_discovery_publishes_real_limits_in_the_shape_the_pages_read(sp, st):
    with st.db.session() as s:
        b = s.execute(select(Backend)).scalars().one()
        assert b.adapter == "spark-media"
        rows = {m.real_id: m for m in s.execute(select(Model).where(Model.backend_id == b.id)).scalars()}
    assert {"image:z-image-turbo", "video:minimax-h3", "video:reverse-video"} <= set(rows)
    h3 = rows["video:minimax-h3"].meta["media"]
    assert h3["modes"] == ["t2v", "i2v", "r2v"] and h3["audio"] is True and h3["limits"]["fps"] == [24, 24] and 6.583 in h3["limits"]["durations"]
    assert rows["image:qwen-image-edit-2511"].meta["media"]["references"] == 3 and rows["image:flux2-klein-4b"].meta["media"]["mode_pictures"]["inpaint"] == 4
    assert rows["video:opensfield-video-edit"].meta["media"]["profile"]["ready"] is True
    assert "sdxl-turbo" not in " ".join(rows)                                # the engine does not offer it, so there is no row to enable


def test_video_length_is_the_real_clip_length(sp):
    h3 = models(sp, "video")["minimax-h3"]
    d = h3["duration"]
    assert d["fps"] == 24 and d["options"] and 5.167 in d["options"] and 6.583 in d["options"] and d["declared"] is True
    wan = models(sp, "video")["wan2.2-i2v-a14b"]["duration"]
    assert wan["fps"] == 16 and wan["options"][0] == 1.062
    # a request for 6 s becomes the 158-frame clip, and the engine receives exactly that
    r = sp.post("/api/media/jobs", json={"kind": "video", "model_id": h3["id"], "prompt": "waves", "width": 864, "height": 480, "seconds": 6})
    assert r.status_code == 200, r.text
    sub = fm.STATE["submissions"][-1]
    assert (sub["frames"], sub["fps"]) == (158, 24) and sub["model"] == "minimax-h3" and "steps" not in sub and "negative_prompt" not in sub
    j = finish(sp, r.json()["id"])
    assert j["status"] == "completed" and j["assets"][0]["duration"] == round(158 / 24, 4)


def test_an_image_job_runs_stores_the_asset_and_discards_the_engine_copy(sp):
    m = models(sp, "image")["z-image-turbo"]
    assert "steps" not in m["defaults"] and m["limits"]["multiple"] == 16 and m["negative_prompt"] is False
    r = sp.post("/api/media/jobs", json={"kind": "image", "model_id": m["id"], "prompt": "a red bicycle", "width": 720, "height": 1280, "seed": 5})
    assert r.status_code == 200, r.text
    j = finish(sp, r.json()["id"])
    assert j["status"] == "completed" and len(j["assets"]) == 1
    assert fm.STATE["submissions"][-1]["width"] == 720 and fm.STATE["submissions"][-1]["seed"] == 5
    assert fm.STATE["discards"] and fm.STATE["discards"][0]["permanent"] is True
    a = sp.get(f"/api/media/assets/{j['assets'][0]['id']}/content")
    assert a.status_code == 200 and a.content.startswith(b"\x89PNG")


def test_a_negative_prompt_is_refused_where_the_model_has_none(sp):
    m = models(sp, "image")["z-image-turbo"]
    r = sp.post("/api/media/jobs", json={"kind": "image", "model_id": m["id"], "prompt": "x", "width": 512, "height": 512, "negative_prompt": "blur"})
    assert r.status_code == 422 and "negative" in r.text


def test_progress_text_and_errors_are_clean_and_cancel_reaches_the_engine(sp):
    m = models(sp, "image")["z-image-turbo"]
    boom = sp.post("/api/media/jobs", json={"kind": "image", "model_id": m["id"], "prompt": "boom", "width": 512, "height": 512}).json()["id"]
    j = finish(sp, boom)
    assert j["status"] == "failed" and "/home/spark" not in j["error"] and "<path>" in j["error"]
    slow = sp.post("/api/media/jobs", json={"kind": "image", "model_id": m["id"], "prompt": "slow", "width": 512, "height": 512}).json()["id"]
    cur = sp.get(f"/api/media/jobs/{slow}").json()
    assert cur["status"] == "running" and cur["progress"] == 0.2 and "step 3 of 8" in cur["progress_text"]
    c = sp.post(f"/api/media/jobs/{slow}/cancel").json()
    assert c["status"] == "cancelled" and fm.STATE["cancels"]


def test_an_engine_refusal_is_shown_as_the_engines_own_sentence(sp):
    m = models(sp, "video")["minimax-h3"]
    r = sp.post("/api/media/jobs", json={"kind": "video", "model_id": m["id"], "prompt": "noon", "width": 864, "height": 480, "seconds": 5})
    assert r.status_code in (422, 502) and "clip lengths" in r.text and "Sarness" not in r.text


def test_a_profile_job_uploads_files_and_submits_the_operation(sp):
    prof = models(sp, "video")["opensfield-video-edit"]
    assert prof["profile"]["inputs_schema"]["operation"]["values"] == ["replace_object", "motion_transfer", "restyle", "edit_video"]
    files = [("file:video", ("v.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 64, "video/mp4")), ("file:refs", ("r.png", b"\x89PNG" + b"0" * 64, "image/png"))]
    r = sp.c.post("/api/media/profile-jobs", data={"model_id": prof["id"], "inputs": '{"operation":"replace_object","prompt":"a robot","target":"the man"}'},
                  files=files, headers={"x-csrf-token": sp.csrf})
    assert r.status_code == 200, r.text
    sub = fm.STATE["profile_submissions"][-1]
    assert sub["profile"] == "opensfield-video-edit" and sub["operation"] == "replace_object" and sub["target"] == "the man"
    assert sub["video"] in fm.STATE["uploads"] and sub["refs"][0] in fm.STATE["uploads"]
    bad = sp.c.post("/api/media/profile-jobs", data={"model_id": prof["id"], "inputs": '{"operation":"replace_object","prompt":"a robot"}'}, files=files, headers={"x-csrf-token": sp.csrf})
    assert bad.status_code == 422 and "target" in bad.text                    # the schema the engine published is enforced before anything is sent


def test_health_says_when_comfyui_is_down_and_a_wrong_token_is_reported(sp, spark_url):
    ad = SparkMediaAdapter(spark_url, fm.TOKEN)
    assert asyncio.run(ad.health())["ok"] is True
    fm.STATE["comfy"] = False
    h = asyncio.run(ad.health())
    assert h["ok"] is False and "ComfyUI" in h["detail"]
    fm.STATE["comfy"] = True
    assert asyncio.run(SparkMediaAdapter(spark_url, "wrong").health())["ok"] is False
    with pytest.raises(AdapterError):
        asyncio.run(SparkMediaAdapter(spark_url, "wrong").media_status("000000000001"))


def test_release_frees_memory_and_refuses_while_a_job_runs(sp, spark_url):
    ad = SparkMediaAdapter(spark_url, fm.TOKEN)
    out = asyncio.run(ad.media_release())
    assert out["ok"] and out["freed_gb"] == 40.0
    m = models(sp, "image")["z-image-turbo"]
    sp.post("/api/media/jobs", json={"kind": "image", "model_id": m["id"], "prompt": "slow", "width": 512, "height": 512})
    for _ in range(3):
        sp.get("/api/media/jobs")
    with pytest.raises(AdapterError) as e:
        asyncio.run(ad.media_release())
    assert e.value.kind == "busy"


def test_unsupported_models_are_not_discovered():
    doc = fm.CAPS
    found = {d.real_id for d in discovered_from_capabilities(doc)}
    assert "image:z-image-turbo" in found and not any("sdxl" in f or "hunyuan" in f for f in found)
    broken = {**doc, "models": [{**m, "supported": False} if m["id"] == "minimax-h3" else m for m in doc["models"]]}
    assert "video:minimax-h3" not in {d.real_id for d in discovered_from_capabilities(broken)}


def test_reverse_analysis_is_read_from_the_engine_for_reverse_jobs_only(sp):
    rv = models(sp, "video")["reverse-video"]
    files = [("file:video", ("v.mp4", b"\x00\x00\x00\x18ftypmp42" + b"0" * 64, "video/mp4"))]
    r = sp.c.post("/api/media/profile-jobs", data={"model_id": rv["id"], "inputs": "{}"}, files=files, headers={"x-csrf-token": sp.csrf})
    assert r.status_code == 200, r.text
    jid = r.json()["id"]
    assert sp.get(f"/api/media/jobs/{jid}/analysis").status_code == 404           # not finished yet
    finish(sp, jid)
    d = sp.get(f"/api/media/jobs/{jid}/analysis")
    assert d.status_code == 200 and d.json()["analysis"]["shots"] == [{"index": 0}]
    img = models(sp, "image")["z-image-turbo"]
    j = sp.post("/api/media/jobs", json={"kind": "image", "model_id": img["id"], "prompt": "x", "width": 512, "height": 512}).json()["id"]
    finish(sp, j)
    assert sp.get(f"/api/media/jobs/{j}/analysis").status_code == 404
