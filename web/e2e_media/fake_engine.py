"""The Sarness job-protocol double from the backend tests, served over HTTP for the browser tests, with the REAL OpenJutsu input schema
(copied from the engine's profile definition) and extra routes the tests use to see exactly what the engine received.

  GET  /__state    submissions (the exact inputs posted to /v1/profiles/{id}/jobs, with the name/mime/size/sha256 of every file) and every upload
  POST /__reset    forget submissions, uploads and scripted runs; restores the default control settings (clock "calls", step 1000: a job completes on its first poll)
  and the OpenJutsu Studio controls, served by tests/fake_sarness.py (see STUDIO_HARNESS.md):
  POST /__control/advance?n=1&job=   move the scripted runs forward n ticks      POST /__control/config   {clock, scenes, variant_next, ...}
  GET  /__control/runs               tick / status of every scripted run
and the Imajutsu image models (flux2-klein-4b, qwen-image-edit-2511, qwen-image-2512, z-image-turbo, qwen-image-2.1, sdxl-turbo) with their real capabilities,
native image jobs (scripted like the OpenJutsu ones, see tests/fake_image.py) and GET /__state "image_submissions" = the exact params of every image job
the engine accepted with the name/size/sha256/dimensions of every picture and the mask. See STUDIO_HARNESS.md, section "Imajutsu".
"""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

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
    fs.use_imajutsu()
    fs.STATE["profiles"] = [fs.openjutsu_entry(DEF)]
    defaults()


def defaults():
    """Compatible with the older specs: a job runs its whole script on the first status poll (so it simply completes). A Studio test that wants to look at
    the states POSTs /__control/config {"clock": "manual"} and steps with /__control/advance."""
    fs.STATE.update(clock="calls", step=1000)


setup()


@fs.app.get("/__state")
def state():
    return {"submissions": fs.STATE["profile_submissions"],
            "uploads": {k: {"size": len(v), "sha256": hashlib.sha256(v).hexdigest(), "name": (fs.STATE["upload_meta"].get(k) or {}).get("name"),
                            "mime": (fs.STATE["upload_meta"].get(k) or {}).get("mime")} for k, v in fs.STATE["uploads"].items()},
            "control": {k: fs.STATE[k] for k in fs._CTRL_KEYS}, "estimates": fs.STATE["estimates"],
            "image_submissions": fs.STATE["image_submissions"], "model_estimates": fs.STATE["model_estimates"]}


@fs.app.post("/__reset")
def reset():
    profiles = fs.STATE["profiles"]
    fs.reset()
    fs.use_imajutsu()
    fs.STATE["profiles"] = profiles
    defaults()
    return {"ok": True}


app = fs.app
