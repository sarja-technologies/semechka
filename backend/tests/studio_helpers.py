"""Shared pieces of the OpenJutsu Studio tests: real ffmpeg media made on the fly, and a signed-in install whose engine double speaks the full
OpenJutsu profile (modes, limits, reference policy, scripted runs). Only the inference engine is a double."""
import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from . import fake_sarness as fs
from .conftest import PW, Api

pytestmark_ffmpeg = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg not installed")

SAFE = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y"]


def ffm(*args, out: Path) -> bytes:
    subprocess.run([*SAFE, *args, str(out)], check=True)
    return out.read_bytes()


def make_video(d: Path, name="v.mp4", w=320, h=240, dur=4.0, fps=24, audio=True, gop=None, vcodec="libx264", acodec="aac", extra_in=(), vf=None) -> bytes:
    """A real clip whose picture changes (testsrc2), with a tone when ``audio``."""
    args = ["-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={dur}"]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=44100:duration={dur}"]
    args += list(extra_in)
    if vf:
        args += ["-vf", vf]
    args += ["-c:v", vcodec, *(["-pix_fmt", "yuv420p"] if vcodec == "libx264" else [])]
    if vcodec == "libx264":
        args += ["-preset", "ultrafast", *(["-g", str(gop)] if gop else [])]
    if audio:
        args += ["-c:a", acodec]
    return ffm(*args, out=d / name)


def make_rotated(d: Path, rot=90) -> bytes:
    """A phone-style clip: stored 320x180 (landscape) with a display-matrix rotation, so it is SHOWN as 180x320. The stored picture has its left half blue and
    its right half red; turned upright (90 degrees counter-clockwise) the top of the picture is red and the bottom blue."""
    plain = ffm("-f", "lavfi", "-i", "color=c=blue:size=160x180:rate=24:duration=2", "-f", "lavfi", "-i", "color=c=red:size=160x180:rate=24:duration=2",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:duration=2", "-filter_complex", "[0:v][1:v]hstack=2[v]", "-map", "[v]", "-map", "2:a",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "ultrafast", "-c:a", "aac", out=d / "plain.mp4")
    (d / "plain_in.mp4").write_bytes(plain)
    return ffm("-display_rotation:v:0", str(rot), "-i", str(d / "plain_in.mp4"), "-c", "copy", out=d / "rotated.mp4")


def probe(data: bytes, tmp: Path) -> dict:
    p = tmp / "probe.bin"
    p.write_bytes(data)
    j = json.loads(subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(p)], capture_output=True, check=True).stdout)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    a = next((s for s in j["streams"] if s["codec_type"] == "audio"), None)
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = round(float(sd["rotation"]))
    w, h = int(v["width"]), int(v["height"])
    fr = v.get("avg_frame_rate", "0/1").split("/")
    fps = float(fr[0]) / float(fr[1] or 1) if float(fr[1] or 1) else 0
    return {"w": w, "h": h, "rot": rot, "vcodec": v["codec_name"], "acodec": a["codec_name"] if a else None, "fps": fps,
            "vdur": float(v.get("duration") or j["format"]["duration"]), "adur": float(a["duration"]) if a and a.get("duration") else None,
            "vstart": float(v.get("start_time") or 0), "astart": float(a.get("start_time") or 0) if a else None, "fdur": float(j["format"]["duration"]),
            "nframes": int(v.get("nb_frames") or 0)}


def avg_color(data: bytes, tmp: Path, at=0.3) -> tuple[int, int, int]:
    """The average RGB of the frame at ``at`` seconds, as it is displayed (ffmpeg turns rotated clips upright)."""
    p, o = tmp / "col.bin", tmp / "col.rgb"
    p.write_bytes(data)
    subprocess.run([*SAFE, "-ss", str(at), "-i", str(p), "-frames:v", "1", "-vf", "scale=1:1:flags=area", "-f", "rawvideo", "-pix_fmt", "rgb24", str(o)], check=True)
    b = o.read_bytes()
    return b[0], b[1], b[2]


def up(api, data, name="clip.mp4", **q):
    r = api.post("/api/media/uploads", params={"name": name, **q}, content=data, headers={"content-type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    return r.json()


def wait_edit(api, eid, tries=600):
    for _ in range(tries):
        t = api.get(f"/api/media/edits/{eid}").json()
        if t["status"] != "running":
            return t
        time.sleep(0.05)
    raise AssertionError("edit never finished")


def content(api, aid) -> bytes:
    r = api.get(f"/api/media/assets/{aid}/content")
    assert r.status_code == 200
    return r.content


def mkproject(api, asset_id, spec=None, **kw):
    r = api.post("/api/studio/projects", json={"source_asset": asset_id, **kw})
    assert r.status_code == 200, r.text
    p = r.json()
    if spec is not None:
        r = api.put(f"/api/studio/projects/{p['id']}", json={"spec": {"source_asset": asset_id, **spec}})
        assert r.status_code == 200, r.text
        p = r.json()
    return p


def profile_entry(**kw):
    return fs.openjutsu_entry(**kw)


def boot(app, st, sarness_url, profiles):
    """A signed-in owner on an install whose engine publishes ``profiles`` (registry entries)."""
    fs.reset()
    fs.STATE["profiles"] = profiles
    fs.configure(clock="manual", step=1)          # the Studio tests look at every state and step the run themselves
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    models = ["local:fake-chat", "video:fake-clip"] + [f"video:{p['id']}" for p in profiles]
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "sarness", "base_url": sarness_url, "api_key": fs.TOKEN},
        "enable_models": models, "smoke_model": "local:fake-chat"})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    return api


def oj_model(api):
    return next(m for m in api.get("/api/media/options", params={"kind": "video"}).json()["models"] if m.get("profile"))["id"]


def png(color="red", w=64, h=64, d=None) -> bytes:
    assert d is not None
    return ffm("-f", "lavfi", "-i", f"color=c={color}:size={w}x{h}:rate=1:duration=1", "-frames:v", "1", out=d / f"{color}{w}x{h}.png")


def sha(b: bytes) -> str:
    import hashlib
    return hashlib.sha256(b).hexdigest()
