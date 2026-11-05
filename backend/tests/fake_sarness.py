"""A Sarness Engine test double for the image/video job protocol, model inventory and load/unload.

Mirrors the shapes read from the engine source: POST /v1/jobs with client_key idempotency, GET /v1/jobs/{id} with
status/progress/generations, queue_position in the list view, bearer-protected /v1/files/{id}, permanent generation
delete. Behaviour is driven by prompt keywords: 'slow' never finishes until cancelled, 'boom' fails with an error that
contains a server path, anything else queues, runs, then completes on the following polls.
Inventory and lifecycle follow engine 0.4.0: /v1/capabilities, /v1/models (with server paths that must never leak),
/v1/models/catalog, /v1/llm/status|load|unload (load replaces the loaded chat model; unload refuses mid-reply) and
/v1/models/{id}/unload. STATE["legacy"] hides the 0.4.0-only endpoints, as an older engine would.
Completions behave like the engine's `ensure_local`: a request for another chat model, or for the same one with
another context window, switches the one chat model even while other replies stream. A reply whose model is switched
away mid-stream ends with an error and is counted in STATE["cut_off"]; STATE["switches"] lists every switch, and
STATE["stream_delay"] makes each reply take a while. /v1/worker/stop stops the picture/video worker (409 mid-job).
"""
import asyncio
import hashlib
import json
import math
import re
import struct
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

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
                 worker_loaded=set(), loads=[], unloads=[], fail_load=False, cut_off=0, switches=[], stream_delay=0.0,
                 worker_alive=False, worker_stops=0, completions=[], profiles=None, profile_submissions=[],
                 # Trinodal AI registered in the engine as a remote endpoint (opt-in: STATE["trinodal"] = True)
                 trinodal=False, trinodal_delay=0.0, trinodal_closed=0, trinodal_finished=0,
                 # Work Mode: the engine registry and resources, and scripted chat replies (see /v1/registry, /v1/resources, completions)
                 registry=False, not_ready={}, free_mb=100000, holders=[], chat_script=[], chat_log=[], chat_fail={}, resource_hits=0,
                 # OpenJutsu Studio: scripted profile runs (see the "scripted OpenJutsu runs" section)
                 runs={}, upload_meta={}, clock="calls", tick_s=1.0, step=1000, scenes=None, variant_next=None, inspect_down=False, forget_traces=False,
                 forget_history=False, break_agree=False, estimates=[], inspect_calls=0)


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
    busy = any(j["status"] == "running" for j in STATE["jobs"].values())
    return {"engine": {"version": "0.4.0-fake"},
            "runtime": {"worker": {"alive": STATE["worker_alive"] or bool(STATE["worker_loaded"]), "busy": busy}}}


@app.post("/v1/worker/stop", dependencies=[Depends(auth)])
def worker_stop():
    if any(j["status"] == "running" for j in STATE["jobs"].values()):
        raise HTTPException(409, "the GPU worker is running a job")
    STATE.update(worker_alive=False, worker_stops=STATE["worker_stops"] + 1)
    STATE["worker_loaded"].clear()
    return {"ok": True}


@app.get("/v1/agent/models", dependencies=[Depends(auth)])
def agent_models():
    extra = [{"ref": "endpoint:trinodal:trinodal", "name": "trinodal", "local": False, "provider": "Trinodal AI",
              "context_max": 8192},
             # a genuinely external endpoint, to show that only the declared one is treated as local
             {"ref": "endpoint:other:big", "name": "big", "local": False, "provider": "Other Cloud",
              "context_max": 8192}] if STATE["trinodal"] else []
    return extra + [{"ref": "local:fake-chat", "name": "Fake chat · Q8_0", "local": True, "context_max": 4096, "size": 1},
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
    return {**STATE["llm"], "ctx": STATE["llm"].get("ctx", 2048) if STATE["llm"]["model_id"] else None,
            "loaded_at": 1.0 if STATE["llm"]["model_id"] else None,
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


def _ensure_local(mid: str, ctx: int | None):
    """The engine's ensure_local: switch (stopping whatever streams on the old model) unless already right."""
    info = next(m for m in MODELS if m["id"] == mid)
    caps = info.get("capabilities") or {}
    ctx = min(int(ctx or caps.get("default_ctx") or 4096), int(caps.get("context_max") or 10**9))
    llm = STATE["llm"]
    if llm["state"] == "ready" and llm["model_id"] == mid and llm.get("ctx", 2048) == ctx:
        return
    STATE["switches"].append({"to": mid, "ctx": ctx, "from": llm["model_id"], "streams": llm["active_streams"]})
    llm.update(state="ready", model_id=mid, ctx=ctx, error=None, active_streams=0, gen=llm.get("gen", 0) + 1)


TRINODAL_USAGE = {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8,
                  "trinodal": {"trace_id": "tr-0123456789abcdef", "nodes": ["conversation", "mathematics", "conversation"],
                               "route": "math.exact", "language_core": {"model": "lc-secret-model", "backend": "llama-cpp"},
                               "trained_components": ["router-v3"], "ms": 412.5, "llm_calls": 2, "refused": 0, "errors": 0,
                               # not part of the contract: must never get through
                               "content": "PRIVATE-TEXT-IN-TRINODAL-USAGE", "nodes_detail": {"x": 1}}}


async def _trinodal_completion(body):
    """Trinodal AI reached through the engine's remote-endpoint support: the upstream `usage` goes through untouched."""
    async def gen():
        try:
            for word in ("Hello", " from", " Trinodal"):
                yield "data: " + json.dumps({"type": "delta", "content": word}) + "\n\n"
                if STATE["trinodal_delay"]:
                    await asyncio.sleep(STATE["trinodal_delay"])
            yield "data: " + json.dumps({"type": "done", "finish_reason": "stop", "usage": TRINODAL_USAGE}) + "\n\n"
            STATE["trinodal_finished"] += 1
        except (asyncio.CancelledError, GeneratorExit):
            STATE["trinodal_closed"] += 1  # the client went away: this is where a real engine would drop the upstream call
            raise
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/agent/completions", dependencies=[Depends(auth)])
async def completions(request: Request):
    body = await request.json()
    ref, params = body.get("model_ref", ""), body.get("params") or {}
    STATE["completions"].append(ref)
    STATE["chat_log"].append({"ref": ref, "messages": body.get("messages"), "params": params})
    if STATE["chat_fail"].get(ref):
        status, message = STATE["chat_fail"][ref].pop(0)
        raise HTTPException(status, message)
    if ref.startswith("endpoint:trinodal:"):
        return await _trinodal_completion(body)
    local = ref.startswith("local:")
    if local:
        _ensure_local(ref.split(":", 1)[1], params.get("ctx"))
        STATE["llm"]["active_streams"] += 1
    gen_at_start = STATE["llm"].get("gen")

    script = STATE["chat_script"].pop(0) if STATE["chat_script"] else None
    reply = script(ref, body.get("messages")) if callable(script) else script
    words = ("ready", " and", " done") if reply is None else tuple(reply[i:i + 12] for i in range(0, len(reply), 12)) or ("",)

    async def gen():
        try:
            for word in words:
                if local and STATE["llm"].get("gen") != gen_at_start:  # the model was switched under this reply
                    STATE["cut_off"] += 1
                    yield "data: " + json.dumps({"type": "error", "message": "llama-server stopped"}) + "\n\n"
                    return
                yield "data: " + json.dumps({"type": "delta", "content": word}) + "\n\n"
                if STATE["stream_delay"]:
                    await asyncio.sleep(STATE["stream_delay"])
            yield "data: " + json.dumps({"type": "done", "finish_reason": "stop"}) + "\n\n"
        finally:
            if local and STATE["llm"].get("gen") == gen_at_start:
                STATE["llm"]["active_streams"] = max(0, STATE["llm"]["active_streams"] - 1)
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
    STATE["upload_meta"][uid] = {"name": f.filename, "mime": f.content_type, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
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
                          "simple": simple, "polls": 0, "error": None, "generations": [], "title": body.get("title")}
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


@app.get("/v1/registry", dependencies=[Depends(auth)])
def registry():
    if STATE["profiles"] is None and not STATE["registry"]:
        raise HTTPException(404, "not found")                       # an engine that predates the registry
    models = []
    if STATE["registry"]:
        for m in MODELS:
            if m["category"] not in ("image", "video", "llm") or not m.get("supported", True):
                continue
            llm = m["category"] == "llm"
            loaded = (m["id"] == STATE["llm"]["model_id"]) if llm else m["id"] in STATE["worker_loaded"]
            why = STATE["not_ready"].get(m["id"])
            models.append({"id": m["id"], "name": m["name"], "category": m["category"], "installed": True,
                           "residency": {"state": "loaded" if loaded else "unloaded", "where": None, "error": None},
                           "readiness": {"state": "not_ready" if why else "ready", "ready": not why, "reason": why},
                           "workload": {"state": "idle", "active_jobs": []},
                           "memory": {"estimate_mb": m.get("vram_mb"), "actual_mb": None, "reserved_mb": None},
                           "capabilities": {"modes": (m.get("capabilities") or {}).get("modes") or [], "vision": bool((m.get("capabilities") or {}).get("vision"))}})
    return {"schema": "sarness.registry/1", "models": models, "profiles": STATE["profiles"] or [], "external": []}


@app.get("/v1/resources", dependencies=[Depends(auth)])
def resources():
    if not STATE["registry"]:
        raise HTTPException(404, "not found")
    llm = STATE["llm"]
    STATE["resource_hits"] += 1
    return {"enabled": True, "engine_id": "engine-fake", "error": None,
            "ledger": {"memory": {"total_mb": 120000, "available_mb": 110000, "usable_mb": 112000, "headroom_mb": 6000, "system_reserve_mb": 8000},
                       "unconsumed_mb": 0, "free_mb": STATE["free_mb"], "holders": STATE["holders"], "ts": 1.0},
            "this_engine": {"llm": {"state": llm["state"], "model_id": llm["model_id"], "busy": bool(llm["active_streams"])},
                            "gpu_worker": {"alive": STATE["worker_alive"] or bool(STATE["worker_loaded"]), "busy": False,
                                           "loaded": sorted(STATE["worker_loaded"])}}}


@app.post("/v1/profiles/{pid}/jobs", dependencies=[Depends(auth)])
async def submit_profile(pid: str, request: Request):
    body = await request.json()
    prof = next((p for p in STATE["profiles"] or [] if p["id"] == pid), None)
    if not prof:
        raise HTTPException(422, f"profile '{pid}' does not exist")
    if not prof["readiness"]["ready"]:
        raise HTTPException(409, f"{prof['name']} is not ready: {prof['readiness']['reason']}")
    key = body.get("client_key")
    if key and key in STATE["by_key"]:
        return STATE["jobs"][STATE["by_key"][key]]
    inputs = body.get("inputs") or {}
    scripted = _scripted(prof)
    if scripted:
        _check_requirements(prof, inputs)
    STATE["n"] += 1
    jid = f"j_{STATE['n']:04d}"
    STATE["jobs"][jid] = {"id": jid, "kind": "video", "status": "queued", "progress": 0.0, "progress_text": "Queued", "polls": 0,
                          "title": body.get("title"), "error": None, "generations": [], "simple": {"prompt": inputs.get("prompt", ""), "width": 832, "height": 480,
                                                                       "frames": 33, "fps": 16}}
    STATE["profile_submissions"].append({"profile": pid, "inputs": inputs, "client_key": key, "job": jid,
                                         "files": {k: [STATE["upload_meta"].get(i) for i in _as_list(inputs[k])] for k in ("video", "refs") if inputs.get(k)}})
    if scripted:
        STATE["runs"][jid] = _make_run(jid, prof, inputs)
    if key:
        STATE["by_key"][key] = jid
    return STATE["jobs"][jid]


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
    run = STATE["runs"].get(jid)
    if run:
        _tick_on_call(run)
        _sync_job(j, run)
        return j
    _advance(j)
    return j


@app.post("/v1/jobs/{jid}/cancel", dependencies=[Depends(auth)])
def cancel(jid: str):
    j = STATE["jobs"].get(jid)
    if not j:
        raise HTTPException(404, "job not found")
    if j["status"] in ("queued", "running"):
        j.update(status="cancelled", progress_text="Cancelled")
        run = STATE["runs"].get(jid)
        if run and run["cancelled"] is None:
            run["cancelled"] = _now_tick(run)
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


# ======================================================================================================================
# Scripted OpenJutsu runs (OpenJutsu Studio contract 2.1-2.4): profile jobs of the "openjutsu*" profiles follow a deterministic script
#   queued -> starting -> loading models -> tracking the subject -> N scenes (sampler progress, then a saved segment + a Loop End event)
#   -> assembling -> saving output -> done          (variants: failure inside scene K, cancel, engine restart that forgot the trace)
# The run's position is a TICK counter. The DEFAULT is instant (clock "calls", step 1000: a job completes at its first status poll, as older tests expect); Studio
# tests call configure(clock="manual", step=1) (studio_helpers.boot does) and step with advance(). clock="manual": it moves only through advance() / POST /__control/advance. clock="calls": every
# GET /v1/jobs/{id} (the status poll Semechka makes) moves it by STATE["step"] (default 1; a huge step makes a job finish at its first poll). clock="timed": one tick per STATE["tick_s"] seconds of wall time.
# Everything an inspect document says is folded from the run's events up to the current tick, so the same script always gives the same answers.
# ======================================================================================================================
T0 = 1793700000.0           # fixed epoch so documents are identical between runs
TICK_SECONDS = 7.5          # pretend seconds per tick (elapsed_s, event times)
SAMPLER = {"id": "73", "title": "Sample Video + Audio", "class": "SamplerCustomAdvanced", "steps": 6}
NODES = [  # (id, class_type, title, group) in execution order; one LoadImage per USED reference is added per run
    ("1", "UNETLoader", "Load H3 Diffusion Model", "load"), ("2", "CLIPLoader", "Load Text Encoder", "load"), ("3", "VAELoader", "Load VAE", "load"),
    ("5", "VHS_LoadVideo", "Load Source Video", "load"), ("20", "SAM3_Detect", "Detect Subject (SAM 3.1)", "track"),
    ("21", "SAM3_Track", "Track Subject", "track"), ("30", "CLIPTextEncode", "Swap Plan", "scene"), ("40", "H3ReferenceConditioning", "Reference Conditioning", "scene"),
    ("63", "TrimOverlap", "Trim Overlap", "scene"), (SAMPLER["id"], SAMPLER["class"], SAMPLER["title"], "sample"), ("74", "VAEDecode", "Decode Scene", "scene"),
    ("80", "LoopEnd", "Loop End", "loop"), ("90", "VHS_VideoCombine", "Final Video", "final")]
NODE_BY_ID = {n[0]: n for n in NODES}
LOOP_MEANING = ("Each 'Loop End' means one clip (scene) was saved as a checkpoint and the next clip is being set up. It is not the end of the job: the final video "
                "exists only after 'Final Video' has run and the output file was verified.")
_MEDIA = Path(tempfile.mkdtemp(prefix="fake-sarness-media-"))
_MEDIA_CACHE: dict = {}
_CTRL_KEYS = ("clock", "tick_s", "step", "scenes", "variant_next", "inspect_down", "forget_traces", "forget_history", "break_agree")


def media_file(w: int, h: int, seconds: float = 1.0, fps: int = 8, audio: bool = True, hue: int = 0) -> bytes:
    """A REAL tiny H.264/AAC MP4 (ffmpeg), cached. Deterministic for the same arguments."""
    key = (w, h, seconds, fps, audio, hue)
    if key not in _MEDIA_CACHE:
        out = _MEDIA / ("m_%d_%d_%s_%d_%d_%d.mp4" % (w, h, str(seconds).replace(".", "p"), fps, int(audio), hue))
        cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={w}x{h}:rate={fps}:duration={seconds}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency={330 + hue}:sample_rate=44100:duration={seconds}"]
        cmd += ["-vf", f"hue=h={hue},format=yuv420p", "-c:v", "libx264", "-preset", "ultrafast", "-g", "4", *(["-c:a", "aac", "-shortest"] if audio else []),
                "-movflags", "+faststart", "-map_metadata", "-1", str(out)]
        subprocess.run(cmd, check=True)
        _MEDIA_CACHE[key] = out.read_bytes()
    return _MEDIA_CACHE[key]


def jpeg_frame(mp4: bytes) -> bytes:
    h = hashlib.sha1(mp4).hexdigest()
    if ("jpg", h) not in _MEDIA_CACHE:
        src, out = _MEDIA / f"{h}.mp4", _MEDIA / f"{h}.jpg"
        src.write_bytes(mp4)
        subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src), "-frames:v", "1", "-q:v", "5", str(out)], check=True)
        _MEDIA_CACHE[("jpg", h)] = out.read_bytes()
    return _MEDIA_CACHE[("jpg", h)]


def probe_upload(data: bytes) -> dict | None:
    """Display width/height/duration of an uploaded video, or None when it is not a decodable video (several tests upload placeholder bytes)."""
    key = ("probe", hashlib.sha1(data).hexdigest())
    if key in _MEDIA_CACHE:
        return _MEDIA_CACHE[key]
    f = _MEDIA / ("up_" + key[1])
    f.write_bytes(data)
    res = None
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", str(f)], capture_output=True, timeout=30, check=False)
        d = json.loads(r.stdout or b"{}")
        v = next(s for s in d.get("streams", []) if s.get("codec_type") == "video")
        rot = 0
        for sd in v.get("side_data_list") or []:
            if "rotation" in sd:
                rot = round(float(sd["rotation"]))
        w, h = int(v["width"]), int(v["height"])
        if rot % 180:
            w, h = h, w
        res = {"width": w, "height": h, "duration": float(d["format"]["duration"])}
    except (StopIteration, KeyError, ValueError, subprocess.SubprocessError):
        pass
    _MEDIA_CACHE[key] = res
    return res


def r32(v: float, step: int = 32) -> int:
    return max(step, int(round(v / step)) * step)


def processing_size(w: int, h: int, short_edge: int, step: int = 32) -> tuple[int, int]:
    """The engine's formula: scale = short_edge / min(w, h); both sides rounded to a multiple of 32."""
    sc = short_edge / min(w, h)
    return r32(w * sc, step), r32(h * sc, step)


def _scripted(prof: dict) -> bool:
    return str(prof.get("id", "")).startswith("openjutsu") and bool(prof.get("modes"))      # only a Studio-era entry (see openjutsu_entry) gets a scripted run


def _op_of(prof: dict, inputs: dict) -> str:
    return inputs.get("operation") or ((prof.get("inputs_schema") or {}).get("operation") or {}).get("default") or "swap"


def _short_edge_of(prof: dict, inputs: dict) -> int:
    if inputs.get("short_edge") is not None:
        return int(inputs["short_edge"])
    res = inputs.get("resolution") or ((prof.get("inputs_schema") or {}).get("resolution") or {}).get("default") or "736p"
    return int(str(res).rstrip("p"))


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v] if v else []


def _check_requirements(prof: dict, inputs: dict) -> None:
    """The engine's requirements(): fewer pictures than the operation needs fails; MORE is fine (the first active_max are used, the rest recorded unused)."""
    op = _op_of(prof, inputs)
    pol = (prof.get("reference_policy") or {}).get(op) or {"min": 0, "active_max": 64}
    refs = _as_list(inputs.get("refs"))
    if len(refs) > 64:
        raise HTTPException(422, "at most 64 reference pictures can be sent in one request")
    if len(refs) < pol["min"]:
        raise HTTPException(422, f"{op} needs at least {pol['min']} reference picture(s); {len(refs)} given")
    if op != "reference_video" and not inputs.get("video"):
        raise HTTPException(422, f"{op} needs a source video")
    lim = (prof.get("limits") or {}).get("resolutions") or {}
    se = _short_edge_of(prof, inputs)
    if inputs.get("short_edge") is not None:
        if se % (lim.get("step") or 32):
            raise HTTPException(422, f"short_edge must be a multiple of {lim.get('step') or 32}")
        if lim.get("max_short_edge") and se > lim["max_short_edge"]:
            raise HTTPException(422, f"short_edge {se} is above the largest size tested on this machine ({lim['max_short_edge']})")
    sel = prof.get("subject_selection") or {}
    if inputs.get("subject_box") is not None and not sel.get("box"):
        raise HTTPException(422, "this engine does not advertise subject_box")
    if inputs.get("subject_points") is not None and not sel.get("points"):
        raise HTTPException(422, "this engine does not advertise subject_points")
    for k in ("video", "refs"):
        for i in _as_list(inputs.get(k)):
            if i not in STATE["upload_meta"]:
                raise HTTPException(422, f"unknown upload '{i}'")


def _make_run(jid: str, prof: dict, inputs: dict) -> dict:
    op = _op_of(prof, inputs)
    pol = (prof.get("reference_policy") or {}).get(op) or {"min": 0, "active_max": 64}
    refs = _as_list(inputs.get("refs"))
    vid = inputs.get("video")
    info = probe_upload(STATE["uploads"][vid]) if vid else None
    src_w, src_h = (info["width"], info["height"]) if info else (1280, 720)
    se = _short_edge_of(prof, inputs)
    pw, ph = processing_size(src_w, src_h, se)
    clip = float(inputs.get("clip_seconds") or 6)
    secs = min(float(inputs.get("duration") or 1e9), (info["duration"] if info else 12.0) - float(inputs.get("start") or 0))
    n = STATE["scenes"] or max(1, min(12, math.ceil(secs / clip)))
    text = " ".join(str(inputs.get(k) or "") for k in ("prompt", "target", "object_target", "change_to"))
    variant = STATE["variant_next"] or ("fail" if "boom" in text else "ok")
    STATE["variant_next"] = None
    meta = lambda u: STATE["upload_meta"].get(u) or {}  # noqa: E731
    run = {"id": jid, "profile": prof["id"], "operation": op, "inputs": dict(inputs), "scenes": n, "variant": variant, "fail_scene": min(2, n),
           "processing": (pw, ph), "tick": 0, "cancelled": None, "wall0": time.time(),
           "refs": [{"position": i + 1, "upload": u, "name": meta(u).get("name"), "used": i < pol["active_max"],
                     "reason": None if i < pol["active_max"] else f"{op} conditions on {pol['active_max']} picture{'s' if pol['active_max'] != 1 else ''}"}
                    for i, u in enumerate(refs)],
           "video": {"upload": vid, "name": meta(vid).get("name")} if vid else None, "source": (src_w, src_h)}
    run["nodes"] = _run_nodes(run)
    run["events"], run["last_tick"] = _script(run)
    return run


def _run_nodes(run: dict) -> list[tuple]:
    out = list(NODES[:4])
    for r in run["refs"]:
        if r["used"]:
            out.append((str(100 + r["position"]), "LoadImage", f"Reference {r['position']}", "load"))
    return out + NODES[4:]


def _script(run: dict) -> tuple[list[dict], int]:
    ev: list[dict] = []

    def add(tick, type_, **kw):
        ev.append({"seq": len(ev) + 1, "tick": tick, "t": round(T0 + tick * TICK_SECONDS, 3), "type": type_, **kw})

    def node(nid, type_, tick, **kw):
        _, ct, title, _g = NODE_BY_ID[nid] if nid in NODE_BY_ID else next(n for n in run["nodes"] if n[0] == nid)
        add(tick, type_, node_id=nid, title=title, class_type=ct, **kw)

    n = run["scenes"]
    add(0, "stage", stage="queued", message="Queued")
    add(1, "stage", stage="starting_backend", message="Starting ComfyUI")
    add(2, "stage", stage="loading_models", message="Loading models and preparing inputs")
    loaders = [x for x in run["nodes"] if x[3] == "load"]
    for nid, *_ in loaders:
        node(nid, "node_start", 2)
    for nid, *_ in loaders:
        node(nid, "node_done", 3, ms=500 + int(nid))
    add(3, "stage", stage="tracking_subject", message="Tracking the subject with SAM 3.1")
    node("20", "node_start", 3)
    node("20", "node_done", 4, ms=2100)
    node("21", "node_start", 4)
    node("21", "node_done", 5, ms=4800)
    t = 5
    for k in range(1, n + 1):
        add(t, "stage", stage="generating_scene", scene=k, message=f"Generating scene {k} of {n}")
        for nid in ("30", "40"):
            node(nid, "node_cached" if k > 1 else "node_done", t, scene=k, ms=90)
        node(SAMPLER["id"], "node_start", t, scene=k)
        add(t, "progress", node_id=SAMPLER["id"], title=SAMPLER["title"], scene=k, value=3, max=SAMPLER["steps"])
        t += 1
        if run["variant"] == "fail" and k == run["fail_scene"]:
            add(t, "error", node_id="63", title="Trim Overlap", class_type="TrimOverlap", scene=k,
                message="Trim Overlap: allocation on device 0 would exceed allowed memory (out of memory) while joining scene "
                        f"{k} /home/x/ComfyUI/output/x.mp4")
            add(t, "stage", stage="failed", scene=k, message="Failed in Trim Overlap")
            return ev, t
        add(t, "progress", node_id=SAMPLER["id"], title=SAMPLER["title"], scene=k, value=SAMPLER["steps"], max=SAMPLER["steps"])
        node(SAMPLER["id"], "node_done", t, scene=k, ms=38100)
        if k >= 2:
            node("63", "node_done", t, scene=k, ms=640)       # joins this scene to the previous one
        node("74", "node_done", t, scene=k, ms=900)
        add(t, "scene_saved", scene=k, node_id="74", title="Decode Scene", message=f"Scene {k} saved")
        node("80", "node_done", t, scene=k, ms=12)
        add(t, "loop_end", scene=k, node_id="80", title="Loop End", message=f"Loop End {k}")
    add(t, "stage", stage="assembling", message="Joining the scenes")
    if n == 1:
        node("63", "node_cached", t, ms=0)                    # a single scene has nothing to join
    node("90", "node_start", t)
    t += 1
    add(t, "stage", stage="saving_output", message="Saving the output")
    add(t, "output", state="writing")
    t += 1
    node("90", "node_done", t, ms=3300)
    add(t, "output", state="ready")
    add(t, "stage", stage="done", message="Done")
    return ev, t


def _now_tick(run: dict) -> int:
    if STATE["clock"] == "timed":
        run["tick"] = max(run["tick"], int((time.time() - run["wall0"]) / max(0.05, STATE["tick_s"])))
    return min(run["tick"], run["last_tick"])


def _tick_on_call(run: dict) -> None:
    if STATE["clock"] == "calls" and run["cancelled"] is None:
        run["tick"] += STATE["step"]
    _now_tick(run)


def advance(n: int = 1, job: str | None = None) -> dict:
    """Move scripted runs forward by ``n`` ticks (all unfinished runs, or one). Returns {job: tick}."""
    out = {}
    for jid, run in STATE["runs"].items():
        if job and jid != job:
            continue
        if run["cancelled"] is None:
            run["tick"] = min(run["tick"] + n, run["last_tick"])
        out[jid] = run["tick"]
    for jid in out:
        _sync_job(STATE["jobs"][jid], STATE["runs"][jid])
    return out


def configure(**kw) -> dict:
    for k, v in kw.items():
        if k not in _CTRL_KEYS:
            raise KeyError(k)
        STATE[k] = v
    return {k: STATE[k] for k in _CTRL_KEYS}


def _clean_path(msg: str) -> str:
    return re.sub(r"(?:/[\w.@+\-]+){3,}", "<path>", msg)


def _comfy_name(x) -> str | None:
    return f"{x['upload']}_{x['name']}" if x and x.get("upload") else None


def _segment_bytes(run: dict, k: int) -> bytes:
    return media_file(64, 112, 1.0, 8, False, hue=k * 40)


def _fold(run: dict) -> dict:
    """The inspect document at the run's current tick."""
    t = _now_tick(run)
    cancel = run["cancelled"]
    evs = [e for e in run["events"] if e["tick"] <= t and (cancel is None or e["tick"] <= cancel)]
    n = run["scenes"]
    stage, label = "queued", "Queued"
    nodes = {nid: {"state": "pending", "class_type": ct, "title": title, "runs": 0, "total_ms": 0, "last_ms": None} for nid, ct, title, _ in run["nodes"]}
    sampling, segs, loops, error, out_state = None, [], 0, None, "none"
    for e in evs:
        ty, nid = e["type"], e.get("node_id")
        if ty == "stage":
            stage, label = e["stage"], e["message"]
        elif ty == "node_start" and nid in nodes:
            nodes[nid]["state"] = "running"
        elif ty in ("node_done", "node_cached") and nid in nodes:
            nd = nodes[nid]
            nd["state"] = "cached" if ty == "node_cached" else "done"
            nd["runs"] += 1
            nd["last_ms"] = e.get("ms")
            nd["total_ms"] += e.get("ms") or 0
            if nid == SAMPLER["id"]:
                sampling = None
        elif ty == "progress":
            sampling = {"node_id": nid, "title": e["title"], "step": e["value"], "max": e["max"], "scene": e["scene"]}
        elif ty == "scene_saved":
            mp4 = _segment_bytes(run, e["scene"])
            pr = probe_upload(mp4) or {}
            segs.append({"index": e["scene"], "state": "saved", "frames": 8, "duration_s": round(pr.get("duration", 1.0), 3), "width": pr.get("width"),
                         "height": pr.get("height"), "bytes": len(mp4), "seconds_to_make": 38.1 if e["scene"] == 1 else 36.4})
        elif ty == "loop_end":
            loops += 1
        elif ty == "error":
            error = {"message": _clean_path(e["message"]), "node_id": nid, "node_title": e["title"], "class_type": e["class_type"]}
            if nid in nodes:
                nodes[nid]["state"] = "failed"
            sampling = None
        elif ty == "output":
            out_state = e["state"]
    status = "queued" if stage == "queued" else "completed" if out_state == "ready" else "failed" if error else "running"
    if cancel is not None:
        status, stage, label, sampling = "cancelled", "failed", "Cancelled", None
    if status in ("failed", "cancelled"):
        for nd in nodes.values():
            if nd["state"] == "running":
                nd["state"] = "pending"
    counts = {"total": len(nodes), "done": sum(1 for x in nodes.values() if x["state"] in ("done", "cached")),
              "running": sorted((k for k, x in nodes.items() if x["state"] == "running"), key=int),
              "pending": sum(1 for x in nodes.values() if x["state"] == "pending"), "cached": sum(1 for x in nodes.values() if x["state"] == "cached"),
              "failed": sum(1 for x in nodes.values() if x["state"] == "failed")}
    gen = stage == "generating_scene"
    if status == "completed":
        prog = {"fraction": 1.0, "indeterminate": False, "basis": "output_ready"}
    elif gen or segs:
        prog = {"fraction": round(len(segs) / n * 0.95, 4), "indeterminate": False, "basis": "scenes_saved/total_scenes"}
    else:
        prog = {"fraction": None, "indeterminate": True, "basis": None}
    pw, ph = run["processing"]
    final = media_file(pw, ph, 1.0, 8, True) if out_state == "ready" else None
    fp = probe_upload(final) if final else {}
    used_n = sum(1 for r in run["refs"] if r["used"])
    cond = {"video": {"node_id": "5", "file": _comfy_name(run["video"])} if run["video"] else None,
            "references": [{"position": r["position"], "node_id": str(100 + r["position"]) if r["used"] else None, "file": _comfy_name(r), "used": r["used"],
                            "name": r["name"], **({"reason": r["reason"]} if r["reason"] else {})} for r in run["refs"]],
            "agree": (not STATE["break_agree"]) and used_n == sum(1 for x in run["nodes"] if x[1] == "LoadImage")}
    return {"job_id": run["id"], "backend": "comfyui", "profile": run["profile"], "operation": run["operation"], "status": status, "stage": stage,
            "stage_label": label, "prompt_id": f"00000000-0000-4000-8000-{int(run['id'].split('_')[-1]):012d}",
            "started_at": T0 + TICK_SECONDS if t >= 1 else None, "updated_at": T0 + t * TICK_SECONDS, "elapsed_s": round(max(0, t - 1) * TICK_SECONDS, 1),
            "progress": prog, "sampling": sampling,
            "scenes": {"total": n, "completed": len(segs), "current": (min(len(segs) + 1, n) if gen else None), "segments": segs},
            "loop": {"iterations_ended": loops, "meaning": LOOP_MEANING}, "nodes": counts, "node_states": nodes, "conditioning": cond,
            "memory": {"ram_available_mb": 21400 - 400 * min(t, 20), "comfyui_rss_mb": 58112 if t >= 2 else None, "vram_mb": None,
                       "source": "/proc/meminfo + process RSS (unified memory: NVML reports no device memory)"},
            "output": {"state": out_state, "file": ({"width": fp.get("width"), "height": fp.get("height"), "duration_s": round(fp.get("duration", 0), 3),
                                                     "bytes": len(final), "has_audio": True} if final else None)},
            "error": error, "_events": evs, "attempt": 1, "has_preview": bool(segs),
            "current_node": ({"id": counts["running"][0], "title": nodes[counts["running"][0]]["title"], "class_type": nodes[counts["running"][0]]["class_type"]}
                             if counts["running"] else None),
            "history": {"status_str": {"completed": "success", "failed": "error"}.get(status), "completed": status == "completed",
                        "output_nodes": ["90"] if out_state == "ready" else [], "trace_agrees": True}}


def _sync_job(j: dict, run: dict) -> None:
    d = _fold(run)
    j.update(status=d["status"], progress=d["progress"]["fraction"] or 0.0, progress_text=d["stage_label"],
             error={"message": d["error"]["message"]} if d["error"] else None)
    if d["status"] == "completed" and not j["generations"]:
        pw, ph = run["processing"]
        fid = f"f_{j['id']}"
        STATE["files"][fid] = media_file(pw, ph, 1.0, 8, True)
        j["generations"] = [{"id": f"g_{j['id']}", "job_id": j["id"], "file_id": fid, "url": f"/v1/files/{fid}", "type": "video", "width": pw, "height": ph,
                             "seed": 4242, "duration": 1.0, "fps": 8, "frames": 8}]
        j["progress"] = 1.0


def _run_for(jid: str) -> dict:
    if STATE["inspect_down"]:
        raise HTTPException(503, "engine busy")
    run = STATE["runs"].get(jid)
    if not run or STATE["forget_traces"]:
        raise HTTPException(404, {"code": "no_trace", "message": "no trace for this job"})
    return run


@app.get("/v1/jobs/{jid}/inspect", dependencies=[Depends(auth)])
def inspect_job(jid: str, after: int = 0):
    STATE["inspect_calls"] += 1
    run = _run_for(jid)
    d = _fold(run)
    evs = d.pop("_events")
    mine = [e for e in evs if e["seq"] > after][:500]
    d["events"] = [{k: v for k, v in e.items() if k != "tick"} for e in mine]
    d["last_seq"] = mine[-1]["seq"] if mine else max(after, evs[-1]["seq"] if evs else 0)      # pass THIS back as ``after``
    d["next_seq"] = d["last_seq"] + 1                                                           # the seq the next event will get
    return d


def _graph(run: dict) -> dict:
    nodes = {}
    for nid, ct, title, _ in run["nodes"]:
        inp: dict = {}
        if ct == "LoadImage":
            inp = {"image": _comfy_name(next(r for r in run["refs"] if str(100 + r["position"]) == nid))}
        elif ct == "VHS_LoadVideo":
            inp = {"video": _comfy_name(run["video"]), "force_rate": 24}
        elif ct == SAMPLER["class"]:
            inp = {"seed": 123, "steps": SAMPLER["steps"], "model": ["1", 0], "positive": ["40", 0]}
        elif ct == "H3ReferenceConditioning":
            inp = {"references": [[str(100 + r["position"]), 0] for r in run["refs"] if r["used"]], "clip": ["2", 0]}
        elif ct == "CLIPTextEncode":
            inp = {"text": str(run["inputs"].get("target") or run["inputs"].get("prompt") or "")[:2000], "clip": ["2", 0]}
        nodes[nid] = {"class_type": ct, "title": title, "inputs": inp}
    return {"prompt_id": _fold(run)["prompt_id"], "format": "comfyui-api", "nodes": nodes, "order": [x[0] for x in run["nodes"]]}


@app.get("/v1/jobs/{jid}/graph", dependencies=[Depends(auth)])
def job_graph(jid: str):
    return _graph(_run_for(jid))


@app.get("/v1/jobs/{jid}/history", dependencies=[Depends(auth)])
def job_history(jid: str):
    run = _run_for(jid)
    if STATE["forget_history"]:
        raise HTTPException(404, {"code": "no_history", "message": "ComfyUI no longer has this prompt"})
    d = _fold(run)
    return {"status": {"status_str": {"completed": "success", "failed": "error"}.get(d["status"], d["status"]), "completed": d["status"] == "completed"},
            "messages": [["execution_start", {"prompt_id": d["prompt_id"]}]] + ([["execution_error", {"node_id": d["error"]["node_id"]}]] if d["error"] else []),
            "outputs": {"90": {"gifs": [{"filename": "final.mp4"}]}} if d["output"]["state"] == "ready" else {},
            "node_errors": {d["error"]["node_id"]: {"errors": [{"message": d["error"]["message"]}]}} if d["error"] else {}}


def _ranged(data: bytes, request: Request, mime: str) -> Response:
    rng = request.headers.get("range", "")
    h = {"Accept-Ranges": "bytes"}
    if rng.startswith("bytes="):
        a, _, b = rng[6:].partition("-")
        start = int(a) if a else max(0, len(data) - int(b))
        end = min(int(b) if (a and b) else len(data) - 1, len(data) - 1)
        if start > end or start >= len(data):
            return Response(status_code=416, headers={"Content-Range": f"bytes */{len(data)}"})
        return Response(data[start:end + 1], status_code=206, media_type=mime, headers={**h, "Content-Range": f"bytes {start}-{end}/{len(data)}"})
    return Response(data, media_type=mime, headers=h)


@app.get("/v1/jobs/{jid}/segments/{n}", dependencies=[Depends(auth)])
def job_segment(jid: str, n: int, request: Request):
    run = _run_for(jid)
    if not any(s["index"] == n for s in _fold(run)["scenes"]["segments"]):
        raise HTTPException(404, {"code": "not_saved", "message": f"segment {n} is not saved yet"})
    return _ranged(_segment_bytes(run, n), request, "video/mp4")


@app.get("/v1/jobs/{jid}/preview", dependencies=[Depends(auth)])
def job_preview(jid: str):
    run = _run_for(jid)
    segs = _fold(run)["scenes"]["segments"]
    if not segs:
        raise HTTPException(404, {"code": "no_preview", "message": "no preview yet"})
    return Response(jpeg_frame(_segment_bytes(run, segs[-1]["index"])), media_type="image/jpeg")


@app.get("/v1/profiles/{pid}/estimate", dependencies=[Depends(auth)])
def profile_estimate(pid: str, operation: str | None = None, width: int | None = None, height: int | None = None, short_edge: int | None = None,
                     seconds: float | None = None, refs: int = 0):
    prof = next((p for p in STATE["profiles"] or [] if p["id"] == pid), None)
    if not prof:
        raise HTTPException(404, "no such profile")
    STATE["estimates"].append({"operation": operation, "width": width, "height": height, "short_edge": short_edge, "seconds": seconds, "refs": refs})
    if not (width and height and short_edge):
        raise HTTPException(422, "width, height and short_edge are required")
    pw, ph = processing_size(width, height, short_edge)
    clip = min(seconds or 6.0, 15.0)
    runs = ((prof.get("limits") or {}).get("memory") or {}).get("measured") or []
    measured = next((m for m in runs if m["short_edge"] == short_edge), None)
    larger = None if measured else next((m for m in sorted(runs, key=lambda m: m["short_edge"]) if m["short_edge"] >= short_edge), None)
    pick = measured or larger
    basis = "measured" if measured else "extrapolated" if larger else "none"
    return {"processing": {"width": pw, "height": ph, "short_edge": short_edge}, "clip_seconds": clip, "frames": int(clip * 24),
            "memory": {"peak_mb": pick["peak_mb"] if pick else None, "basis": basis,
                       "note": (f"measured on a {pick['clip_seconds']} s clip" if measured else f"not measured at {short_edge}p; the run at {pick['short_edge']}p peaked at this"
                                if larger else "nothing was measured at this size")},
            "seconds": {"estimate": 197 if measured else None, "basis": "measured" if measured else "none"}}


# ---- control endpoints (tests and the browser harness) ---------------------------------------------------------------------
@app.post("/__control/advance")
def control_advance(n: int = 1, job: str | None = None):
    return {"ticks": advance(n, job)}


@app.post("/__control/config")
async def control_config(request: Request):
    try:
        return configure(**(await request.json()))
    except KeyError as e:
        raise HTTPException(422, f"unknown setting {e}") from None


@app.get("/__control/runs")
def control_runs():
    return {jid: {"tick": r["tick"], "last_tick": r["last_tick"], "variant": r["variant"], "scenes": r["scenes"], "operation": r["operation"],
                  "status": _fold(r)["status"]} for jid, r in STATE["runs"].items()}


# ---- the real OpenJutsu profile entry (modes, limits, reference policy ...) built from the engine's definition file ----------------------------
DEF_PATH = Path(__file__).resolve().parents[2] / "web" / "e2e_media" / "openjutsu-minimax-h3.json"


def openjutsu_entry(d: dict | None = None, *, vehicle: str = "experimental", box: bool = True, points: bool = False, ready: bool = True) -> dict:
    """A registry ``profiles[]`` entry for OpenJutsu with the Studio additions of contract 2.1. ``vehicle`` sets that mode's status."""
    d = d or json.loads(DEF_PATH.read_text())
    ms = [dict(m) for m in d["modes"]]
    for m in ms:
        if m["id"] == "vehicle_replacement":
            m["status"] = vehicle
            if vehicle == "unavailable":
                m["reason"] = "no vehicle-capable graph is installed"
    return {"id": d["id"], "name": d["name"], "category": "video", "family": d["family"], "official": True,
            "readiness": {"state": "ready" if ready else "not_ready", "ready": ready, "reason": None if ready else "models missing"},
            "residency": {"state": "unloaded"}, "memory": {"estimate_mb": d["estimate_mb"]}, "inputs_schema": json.loads(json.dumps(d["inputs_schema"])),
            "capabilities": {"operations": d["operations"]}, "notes": d.get("notes"), "backend": "comfyui",
            "modes": ms, "limits": d["limits"], "reference_policy": d["reference_policy"],
            "subject_selection": {**d["subject_selection"], "box": box, "points": points}, "operation_status": d["operation_status"]}
