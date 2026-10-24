"""A Sarness Engine test double for the image/video job protocol, model inventory and load/unload.

Mirrors the shapes read from the engine source: POST /v1/jobs with client_key idempotency, GET /v1/jobs/{id} with
status/progress/generations, queue_position in the list view, bearer-protected /v1/files/{id}, permanent generation
delete. Behaviour is driven by prompt keywords: 'slow' never finishes until cancelled, 'boom' fails with an error that
contains a server path, anything else queues, runs, then completes on the following polls.
Inventory and lifecycle follow engine 0.4.0: /v1/capabilities, /v1/models (with server paths that must never leak),
/v1/models/catalog, /v1/llm/status|load|unload (load replaces the loaded chat model; unload refuses mid-reply) and
/v1/models/{id}/unload. STATE["legacy"] hides the 0.4.0-only endpoints, as an older engine would.
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
    STATE.update(jobs={}, by_key={}, files={}, discards=[], submissions=[], n=0, reject_files=False, uploads={},
                 legacy=False, llm={"state": "stopped", "model_id": None, "active_streams": 0, "error": None},
                 worker_loaded=set(), loads=[], unloads=[], fail_load=False)


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
    {"id": "fake-chat", "catalog_id": "fake-chat", "name": "Fake chat · Q8_0", "category": "llm", "family": "gguf",
     "format": "gguf", "path": "/srv/private/models/llm/fake-chat-Q8_0.gguf", "size": 1, "supported": True,
     "meta": {"architecture": "qwen35", "layers": 25, "context_length": 262144, "mmproj": "/srv/private/mm.gguf"},
     "description": "Tiny test model.", "license": "Apache-2.0", "tags": ["small"], "vram_mb": 1500,
     "quantization": "Q8_0", "capabilities": {"vision": False, "context_max": 4096, "default_ctx": 2048}},
    {"id": "fake-big", "catalog_id": "fake-big", "name": "Fake big · Q4_K_M", "category": "llm", "family": "gguf",
     "format": "gguf", "path": "/srv/private/models/llm/fake-big.gguf", "size": 2, "supported": True, "meta": {},
     "vram_mb": 90000, "quantization": "Q4_K_M", "capabilities": {"vision": True, "context_max": 8192}},
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
    return [{"ref": "local:fake-chat", "name": "Fake chat · Q8_0", "local": True, "context_max": 4096, "size": 1},
            {"ref": "local:fake-big", "name": "Fake big · Q4_K_M", "local": True, "context_max": 8192, "size": 2,
             "vision": True}]


def _legacy():
    if STATE["legacy"]:
        raise HTTPException(404, "Not Found")


@app.get("/v1/capabilities", dependencies=[Depends(auth)])
def capabilities():
    _legacy()
    rows = []
    for m in MODELS:
        llm = m["category"] == "llm"
        rows.append({"id": m["id"], "name": m["name"], "category": m["category"], "family": m.get("family"),
                     "runtime": "llama.cpp" if llm else "diffusers",
                     "kinds": (["chat", "reasoning"] if llm else [m["category"]]),
                     "modes": (m.get("capabilities") or {}).get("modes") or [], "quantization": m.get("quantization"),
                     "size_bytes": m.get("size"), "memory_mb": m.get("vram_mb"), "supported": m.get("supported", True),
                     "loaded": (m["id"] == STATE["llm"]["model_id"]) if llm else m["id"] in STATE["worker_loaded"],
                     "chat_ref": f"local:{m['id']}" if llm else None})
    running = sum(1 for j in STATE["jobs"].values() if j["status"] == "running")
    queued = sum(1 for j in STATE["jobs"].values() if j["status"] == "queued")
    return {"engine_version": "0.4.0-fake", "api_version": "1.1", "hostname": "fake-host",
            "gpu": {"name": "Fake GPU", "unified_memory": True, "memory_total_mb": 120000, "memory_free_mb": 100000},
            "memory": {"total_mb": 120000, "used_mb": 20000, "available_mb": 100000},
            "models": rows, "llm": {k: STATE["llm"][k] for k in ("state", "model_id", "active_streams")},
            "jobs": {"running": running, "queued": queued, "concurrency": 1, "queue_soft_limit": 8,
                     "busy_reason": None},
            "health": {"status": "ok", "issues": []}}


@app.get("/v1/models/catalog", dependencies=[Depends(auth)])
def catalog():
    _legacy()
    return [{"id": "fake-chat", "repo": "someone/Fake-Chat-GGUF",
             "quantizations": [{"id": "Q8_0", "model_id": "fake-chat", "installed": True}]},
            {"id": "fake-turbo", "repo": "fakelab/fake-turbo"}]


@app.get("/v1/llm/status", dependencies=[Depends(auth)])
def llm_status():
    return {**STATE["llm"], "ctx": 2048, "loaded_at": 1.0 if STATE["llm"]["model_id"] else None,
            "vram_estimate_mb": 1500 if STATE["llm"]["model_id"] else None}


@app.post("/v1/llm/load", dependencies=[Depends(auth)])
async def llm_load(request: Request):
    body = await request.json()
    mid = body["model_id"]
    if not any(m["id"] == mid and m["category"] == "llm" for m in MODELS):
        raise HTTPException(404, f"LLM '{mid}' is not installed")
    STATE["loads"].append(mid)
    if STATE["fail_load"]:
        STATE["llm"].update(state="error", model_id=None, error="llama-server exited (1) see /srv/private/log")
        raise HTTPException(500, f"Could not load {mid}: llama-server exited (1)")
    # like the engine: whatever chat model is loaded is stopped first, even mid-reply
    STATE["llm"].update(state="ready", model_id=mid, error=None, active_streams=0)
    return llm_status()


@app.post("/v1/llm/unload", dependencies=[Depends(auth)])
def llm_unload():
    if STATE["llm"]["active_streams"]:
        raise HTTPException(409, "the model is generating a response")
    STATE["llm"].update(state="stopped", model_id=None)
    return llm_status()


@app.post("/v1/models/{model_id:path}/unload", dependencies=[Depends(auth)])
def unload_model(model_id: str):
    _legacy()
    STATE["unloads"].append(model_id)
    if STATE["llm"]["model_id"] == model_id:
        if STATE["llm"]["active_streams"]:
            raise HTTPException(409, "the model is generating a response")
        STATE["llm"].update(state="stopped", model_id=None)
    elif model_id in STATE["worker_loaded"]:
        if any(j["status"] == "running" for j in STATE["jobs"].values()):
            raise HTTPException(409, "an image or video job is running; wait for it or cancel it first")
        STATE["worker_loaded"].discard(model_id)
    return {"ok": True}


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
