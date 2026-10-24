"""A Sarness Engine test double for the image/video job protocol (and just enough chat for setup).

Mirrors the shapes read from the engine source: POST /v1/jobs with client_key idempotency, GET /v1/jobs/{id} with
status/progress/generations, queue_position in the list view, bearer-protected /v1/files/{id}, permanent generation
delete. Behaviour is driven by prompt keywords: 'slow' never finishes until cancelled, 'boom' fails with an error that
contains a server path, anything else queues, runs, then completes on the following polls.
"""
import json
import struct
import zlib

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

TOKEN = "fake-sarness-token"
MARKER = b"SECRET-PIXELS-7731"

app = FastAPI()
STATE: dict = {}


def reset():
    STATE.clear()
    STATE.update(jobs={}, by_key={}, files={}, discards=[], submissions=[], n=0, reject_files=False, uploads={})


reset()


def auth(request: Request):
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        raise HTTPException(401, "bad token")


def make_png() -> bytes:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x10\x80\xf0" * 4 for _ in range(4))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0))
            + chunk(b"tEXt", b"Comment\x00" + MARKER) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def make_mp4() -> bytes:
    return b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom" + MARKER + bytes(range(256)) * 8


MODELS = [
    {"id": "fake-turbo", "name": "Fake Turbo", "category": "image", "family": "fakediffusion", "supported": True,
     "license": "Apache-2.0", "description": "Test image model.", "loaded": False, "vram_mb": 1000, "size": 123,
     "capabilities": {"modes": ["txt2img", "img2img"], "negative_prompt": True, "schedulers": ["euler", "ddim"]},
     "defaults": {"steps": 4, "guidance": 0.0, "width": 512, "height": 512},
     "limits": {"steps": [1, 8], "guidance": [0, 2], "min_side": 256, "max_side": 768, "multiple": 8}},
    {"id": "fake-clip", "name": "Fake Clip", "category": "video", "family": "fakevideo", "supported": True,
     "license": "Apache-2.0", "description": "Test video model.", "loaded": False, "vram_mb": 2000, "size": 456,
     "capabilities": {"modes": ["t2v", "i2v"], "negative_prompt": True, "audio": False},
     "defaults": {"steps": 20, "guidance": 4.0, "width": 512, "height": 320, "frames": 17, "fps": 16},
     "limits": {"steps": [4, 40], "guidance": [1, 8], "frames": [5, 49], "frame_step": 4, "fps": [8, 24],
                "resolutions": [[512, 320], [320, 512]], "multiple": 32}},
    {"id": "unsupported-one", "name": "Nope", "category": "image", "supported": False},
    {"id": "some-lora", "name": "LoRA", "category": "lora", "supported": True},
]


@app.get("/v1/health")
def health():
    return {"status": "ok", "service": "sarness-engine", "api_version": "1.1", "engine_version": "0.4.0-fake"}


@app.get("/v1/system", dependencies=[Depends(auth)])
def system():
    return {"engine": {"version": "0.4.0-fake"}}


@app.get("/v1/agent/models", dependencies=[Depends(auth)])
def agent_models():
    return [{"ref": "local:fake-chat", "name": "Fake chat", "local": True, "context_max": 4096, "size": 1}]


@app.get("/v1/llm/status", dependencies=[Depends(auth)])
def llm_status():
    return {}


@app.post("/v1/agent/completions", dependencies=[Depends(auth)])
async def completions(request: Request):
    async def gen():
        yield "data: " + json.dumps({"type": "delta", "content": "ready"}) + "\n\n"
        yield "data: " + json.dumps({"type": "done", "finish_reason": "stop"}) + "\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/v1/models", dependencies=[Depends(auth)])
def models():
    return MODELS


@app.post("/v1/uploads", dependencies=[Depends(auth)])
async def upload(request: Request):
    form = await request.form()
    f = form["file"]
    data = await f.read()
    uid = f"up_{len(STATE['uploads']) + 1:04d}"
    STATE["uploads"][uid] = data
    return {"id": uid, "name": f.filename, "size": len(data)}


@app.post("/v1/jobs", dependencies=[Depends(auth)])
async def submit(request: Request):
    body = await request.json()
    kind, simple, key = body.get("kind"), body.get("simple") or {}, body.get("client_key")
    if kind not in ("image", "video"):
        raise HTTPException(400, "kind must be 'image' or 'video'")
    if key and key in STATE["by_key"]:
        return STATE["jobs"][STATE["by_key"][key]]
    if not any(m["id"] == simple.get("model") and m["category"] == kind for m in MODELS):
        raise HTTPException(422, "unknown model")
    STATE["n"] += 1
    jid = f"j_{STATE['n']:04d}"
    STATE["jobs"][jid] = {"id": jid, "kind": kind, "status": "queued", "progress": 0.0, "progress_text": "Queued",
                          "simple": simple, "polls": 0, "error": None, "generations": []}
    STATE["submissions"].append({"kind": kind, **simple})
    if key:
        STATE["by_key"][key] = jid
    return STATE["jobs"][jid]


def _advance(j):
    if j["status"] in ("completed", "failed", "cancelled"):
        return
    j["polls"] += 1
    prompt = j["simple"].get("prompt", "")
    if "slow" in prompt:
        j.update(status="running", progress=0.3, progress_text="Sampling 3/10")
    elif "boom" in prompt and j["polls"] >= 2:
        j.update(status="failed", error={"message": "CUDA out of memory loading /home/x/models/image/big.safetensors"})
    elif j["polls"] == 1:
        j.update(status="running", progress=0.5, progress_text="Sampling 2/4")
    elif j["polls"] >= 2 and "boom" not in prompt:
        fid = f"f_{j['id']}"
        STATE["files"][fid] = make_png() if j["kind"] == "image" else make_mp4()
        s = j["simple"]
        j["generations"] = [{"id": f"g_{j['id']}", "job_id": j["id"], "file_id": fid, "url": f"/v1/files/{fid}",
                             "type": j["kind"], "width": s.get("width"), "height": s.get("height"),
                             "seed": 4242 if s.get("seed", -1) == -1 else s["seed"],
                             "duration": (s["frames"] / s["fps"]) if j["kind"] == "video" else None,
                             "fps": s.get("fps"), "frames": s.get("frames")}]
        j.update(status="completed", progress=1.0, progress_text="Completed")


@app.get("/v1/jobs", dependencies=[Depends(auth)])
def list_jobs(status: str | None = None, limit: int = 100):
    out = []
    for j in STATE["jobs"].values():
        if status and j["status"] not in status.split(","):
            continue
        out.append({**j, "queue_position": 1 if j["status"] == "queued" else None})
    return out[:limit]


@app.get("/v1/jobs/{jid}", dependencies=[Depends(auth)])
def get_job(jid: str):
    j = STATE["jobs"].get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    _advance(j)
    return j


@app.post("/v1/jobs/{jid}/cancel", dependencies=[Depends(auth)])
def cancel(jid: str):
    j = STATE["jobs"].get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    if j["status"] in ("queued", "running"):
        j.update(status="cancelled", progress_text="Cancelled")
    return j


@app.get("/v1/files/{fid}", dependencies=[Depends(auth)])
def get_file(fid: str):
    if STATE["reject_files"]:
        raise HTTPException(500, "disk error")
    data = STATE["files"].get(fid)
    if data is None:
        raise HTTPException(404, "file not found")
    return Response(data, media_type="image/png" if data.startswith(b"\x89PNG") else "video/mp4")


@app.delete("/v1/generations/{gid}", dependencies=[Depends(auth)])
def delete_generation(gid: str, permanent: bool = False):
    STATE["discards"].append({"id": gid, "permanent": permanent})
    STATE["files"].pop("f_" + gid[2:], None)
    return {"ok": True}
