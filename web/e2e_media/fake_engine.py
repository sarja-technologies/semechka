"""The Sarness job-protocol double from the backend tests, served over HTTP for the browser tests, with the REAL OpenJutsu input schema
(copied from the engine's profile definition) and extra routes the tests use to see exactly what the engine received.

  GET  /__state    submissions and, for every upload, its size and sha256
  POST /__reset    forget submissions and uploads
"""
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from fastapi import Request

from tests import fake_sarness as fs

HERE = Path(__file__).parent
DEF = json.loads((HERE / "openjutsu-minimax-h3.json").read_text())

# a result file that really plays
_tmp = Path(tempfile.mkdtemp(prefix="fake-engine-"))
_out = _tmp / "result.mp4"
subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=24:duration=2", "-c:v", "libx264",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(_out)], check=True)
RESULT = _out.read_bytes()
fs.make_mp4 = lambda: RESULT


def setup():
    fs.reset()
    fs.STATE["profiles"] = [{"id": "openjutsu-minimax-h3", "name": DEF["name"], "category": "video", "family": "openjutsu", "official": True,
                             "readiness": {"state": "ready", "ready": True, "reason": None}, "residency": {"state": "unloaded"}, "memory": {"estimate_mb": DEF["estimate_mb"]},
                             "inputs_schema": DEF["inputs_schema"], "capabilities": {"operations": DEF["operations"]}}]


setup()


@fs.app.get("/__state")
def state():
    return {"submissions": fs.STATE["profile_submissions"],
            "uploads": {k: {"size": len(v), "sha256": hashlib.sha256(v).hexdigest()} for k, v in fs.STATE["uploads"].items()}}


@fs.app.post("/__reset")
def reset():
    fs.STATE["profile_submissions"].clear()
    fs.STATE["uploads"].clear()
    return {"ok": True}


app = fs.app
