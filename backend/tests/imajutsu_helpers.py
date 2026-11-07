"""Shared pieces of the Imajutsu tests: real pictures made with numpy + ffmpeg, and a signed-in install whose engine double speaks the native image runtime
(real capabilities per model, scripted runs). Only the inference engine is a double."""
import shutil
import struct
import time
from pathlib import Path

import numpy as np
import pytest

from semechka import imajutsu_pix as pix

from . import fake_sarness as fs
from .conftest import PW, Api

pytestmark_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
IMAGE_MODELS = ["flux2-klein-4b", "qwen-image-edit-2511", "qwen-image-2512", "z-image-turbo", "qwen-image-2.1", "sdxl-turbo"]


def boot_images(app, st, sarness_url, models=None, extra_settings=None, profiles=None, engine_state=None):
    """A signed-in owner on an install whose engine publishes the Imajutsu image models. The engine's scripted runs are stepped by the test (clock manual)."""
    fs.reset()
    fs.use_imajutsu()
    if profiles:
        fs.STATE["profiles"] = profiles
    fs.STATE.update(engine_state or {})
    fs.configure(clock="manual", step=1)
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    ids = [f"image:{m}" for m in (IMAGE_MODELS if models is None else models)]
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional", **(extra_settings or {})},
        "backend": {"adapter": "sarness", "base_url": sarness_url, "api_key": fs.TOKEN},
        "enable_models": ["local:fake-chat", *ids, *[f"video:{p['id']}" for p in profiles or []]], "smoke_model": "local:fake-chat"})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    assert api.put("/api/admin/settings/media.max_concurrent_jobs", json={"value": 16}).status_code == 200          # the tests start several jobs; quotas have their own tests
    return api


# ---- real pictures -----------------------------------------------------------------------------------------------------------------------------
def gradient(w: int, h: int, seed: int = 0) -> np.ndarray:
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :]
    return np.stack([np.broadcast_to(x, (h, w)), np.broadcast_to(y, (h, w)), np.full((h, w), (seed * 40) % 256, np.float32)], axis=-1).astype(np.uint8)


def solid(w: int, h: int, rgb=(120, 120, 120)) -> np.ndarray:
    return np.broadcast_to(np.array(rgb, np.uint8), (h, w, 3)).copy()


def png_of(arr: np.ndarray) -> bytes:
    return pix.encode(arr, "png")


def jpeg_of(arr: np.ndarray) -> bytes:
    return pix.encode(arr, "jpg", 2)


def with_orientation(jpeg: bytes, orientation: int) -> bytes:
    """The same JPEG with an EXIF orientation tag (what a phone writes), inserted right after the start marker."""
    tiff = b"MM\x00\x2a\x00\x00\x00\x08" + struct.pack(">H", 1) + struct.pack(">HHI", 0x0112, 3, 1) + struct.pack(">HH", orientation, 0) + b"\x00\x00\x00\x00"
    body = b"Exif\x00\x00" + tiff
    return jpeg[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + jpeg[2:]


def decode(data: bytes, tmp: Path, name="d.bin") -> np.ndarray:
    """H x W x 3 uint8 of any picture file, EXIF orientation NOT applied (raw pixels as stored)."""
    p = tmp / name
    p.write_bytes(data)
    mime = "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"
    stored = pix.probe_size(p, pix.demuxer_of(mime))
    raw = pix.run_ff(["-nostdin", "-loglevel", "error", "-noautorotate", "-f", pix.demuxer_of(mime), "-i", f"file:{p}", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    return np.frombuffer(raw, np.uint8).reshape(stored[1], stored[0], 3)


def up(api, data: bytes, name="pic.png"):
    r = api.post("/api/media/uploads", params={"name": name}, content=data, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def content(api, aid) -> bytes:
    r = api.get(f"/api/media/assets/{aid}/content")
    assert r.status_code == 200, r.text
    return r.content


def mkproject(api, mode="generate", spec=None, **kw):
    r = api.post("/api/imajutsu/projects", json={"mode": mode, **kw})
    assert r.status_code == 200, r.text
    p = r.json()
    if spec is not None:
        r = api.put(f"/api/imajutsu/projects/{p['id']}", json={"spec": {"mode": mode, **spec}})
        assert r.status_code == 200, r.text
        p = r.json()
    return p


def wait_task(api, tid, tries=600):
    for _ in range(tries):
        t = api.get(f"/api/media/edits/{tid}").json()
        if t["status"] != "running":
            return t
        time.sleep(0.05)
    raise AssertionError("task never finished")


def finish_jobs(api, ids, ticks=100):
    """Run the scripted engine jobs to their end and let Semechka collect the results."""
    fs.advance(ticks)
    for jid in ids:
        api.get(f"/api/media/jobs/{jid}")
    return [api.get(f"/api/media/jobs/{jid}").json() for jid in ids]


def model_id(api, alias):
    """Semechka's id of an installed image model, by the engine's name for it."""
    for m in api.get("/api/media/options", params={"kind": "image"}).json()["models"]:
        if m["alias"] == alias:
            return m["id"]
    raise AssertionError(f"{alias} is not installed")


def last_submission():
    return fs.STATE["image_submissions"][-1]


def uploaded(meta: dict) -> bytes:
    """The exact bytes the engine received for one picture of a recorded submission."""
    return fs.STATE["uploads"][meta["upload"]]


def solid_png(rgb, w=32, h=32) -> bytes:
    return png_of(solid(w, h, rgb))
