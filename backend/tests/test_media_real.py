"""Image/video generation against a REAL Sarness Engine. Skipped unless configured:

  SEMECHKA_REAL_SARNESS_URL=http://127.0.0.1:18752  SEMECHKA_REAL_SARNESS_TOKEN=...
  SEMECHKA_REAL_IMAGE_MODEL=image:sdxl-turbo          (default)
  SEMECHKA_REAL_VIDEO_MODEL=video:wan2.2-ti2v-5b      (optional; slow, only run when you mean to)
"""
import os
import time

import pytest

from semechka import adapters

from .conftest import PW, Api

URL = os.environ.get("SEMECHKA_REAL_SARNESS_URL")
TOKEN = os.environ.get("SEMECHKA_REAL_SARNESS_TOKEN")
IMAGE = os.environ.get("SEMECHKA_REAL_IMAGE_MODEL", "image:sdxl-turbo")
VIDEO = os.environ.get("SEMECHKA_REAL_VIDEO_MODEL")
pytestmark = pytest.mark.skipif(not (URL and TOKEN), reason="no real Sarness Engine configured")


async def test_discovery_lists_real_media_models_with_real_limits():
    ad = adapters.make("sarness", URL, TOKEN)
    found = {m.real_id: m for m in await ad.discover()}
    assert IMAGE in found and found[IMAGE].capabilities == ["image"]
    media = found[IMAGE].meta["media"]
    assert media["limits"]["max_side"] >= 512 and media["defaults"]["steps"] >= 1
    assert not any(k.startswith("lora:") for k in found)
    assert any(m.capabilities == ["chat"] or "chat" in m.capabilities for m in found.values())


def _stack(app, st, models):
    from semechka.routers.setup import ensure_setup_token
    api = Api(app)
    tok = ensure_setup_token(st)
    r = api.post("/api/setup/complete", headers={"x-setup-token": tok}, json={
        "owner": {"email": "o@example.test", "password": PW, "display_name": "O"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True},
        "backend": {"adapter": "sarness", "base_url": URL, "api_key": TOKEN}, "enable_models": models})
    assert r.status_code == 200 and r.json()["models_enabled"] == len(models), r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    return api


def _wait(api, jid, limit_s):
    t0, seen = time.time(), []
    while time.time() - t0 < limit_s:
        j = api.get(f"/api/media/jobs/{jid}").json()
        if not seen or seen[-1] != (j["status"], j["progress_text"]):
            seen.append((j["status"], j["progress_text"]))
        if j["status"] in ("completed", "failed", "cancelled"):
            return j, seen, time.time() - t0
        time.sleep(1.5)
    raise AssertionError(f"job did not finish in {limit_s}s: {seen}")


def test_real_image_generation_through_the_whole_stack(app, st, cfg):
    api = _stack(app, st, [IMAGE])
    opts = api.get("/api/media/options", params={"kind": "image"}).json()
    m = opts["models"][0]
    r = api.post("/api/media/jobs", json={"kind": "image", "model_id": m["id"], "width": 512, "height": 512, "steps": 2,
                                          "seed": 11, "prompt": "a small red bicycle leaning on a white wall, photo"})
    assert r.status_code == 200, r.text
    j, seen, took = _wait(api, r.json()["id"], 900)
    print(f"\nreal image job: {took:.1f}s, states: {seen}")
    assert j["status"] == "completed", j
    a = j["assets"][0]
    assert a["mime"] == "image/png" and a["width"] == 512 and a["seed"] == 11
    png = api.get(f"/api/media/assets/{a['id']}/content").content
    assert png.startswith(b"\x89PNG") and len(png) > 20_000
    files = list((cfg.data_dir / "assets").rglob("*.bin"))
    assert len(files) == 1 and png[:64] not in files[0].read_bytes()
    # the same seed and settings are reproducible: the engine echoes the seed we asked for
    assert j["params"]["seed"] == 11


@pytest.mark.skipif(not VIDEO, reason="set SEMECHKA_REAL_VIDEO_MODEL to run a real (slow) video job")
def test_real_video_generation(app, st):
    api = _stack(app, st, [VIDEO])
    m = api.get("/api/media/options", params={"kind": "video"}).json()["models"][0]
    lim = m["limits"]
    w, h = lim["resolutions"][-1] if lim.get("resolutions") else (m["defaults"]["width"], m["defaults"]["height"])
    frames = lim["frames"][0]
    r = api.post("/api/media/jobs", json={"kind": "video", "model_id": m["id"], "width": w, "height": h, "frames": frames,
                                          "fps": lim["fps"][0], "steps": lim["steps"][0], "prompt": "waves on a beach"})
    assert r.status_code == 200, r.text
    j, seen, took = _wait(api, r.json()["id"], 2400)
    print(f"\nreal video job: {took:.1f}s, states: {seen}")
    assert j["status"] == "completed", j
    assert j["assets"][0]["mime"] == "video/mp4" and j["assets"][0]["size"] > 1000
