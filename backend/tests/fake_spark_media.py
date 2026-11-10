"""A test double for the Spark media engine's HTTP protocol (docs/MEDIA_ENGINE.md section 4). The catalog it serves is a REAL capture of the engine's
``GET /v1/capabilities`` (fixtures/spark_media_caps.json, 2026-11-10); jobs are scripted: queued -> running (two polls) -> completed, with real PNG/MP4-looking
bytes from the Sarness double. Prompts steer it: 'slow' never finishes, 'boom' fails, 'noon' answers 422 like the engine's request check."""
import json
import re
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from . import fake_sarness as fs

TOKEN = "fake-spark-media-token"
CAPS = json.loads((Path(__file__).parent / "fixtures" / "spark_media_caps.json").read_text())
# Experimental tools: a REAL capture of the engine's GET /v1/tools (fixtures/spark_media_tools.json, 2026-11-10; moge-relief still unverified there).
# Served in /v1/capabilities only when a test sets STATE["tools"] (the capture of /v1/capabilities predates the list).
TOOLS = json.loads((Path(__file__).parent / "fixtures" / "spark_media_tools.json").read_text())
WAV = b"RIFF" + (36 + 8).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + bytes([1, 0, 1, 0]) + (24000).to_bytes(4, "little") + (48000).to_bytes(4, "little") + bytes([2, 0, 16, 0]) + b"data" + (8).to_bytes(4, "little") + bytes(8)
GLB = b"glTF" + (2).to_bytes(4, "little") + (20 + 12).to_bytes(4, "little") + (12).to_bytes(4, "little") + b"JSON" + b'{"asset":{} }'[:12]
app = FastAPI()
STATE: dict = {}


def reset():
    STATE.clear()
    STATE.update(jobs={}, by_key={}, uploads={}, files={}, discards=[], submissions=[], profile_submissions=[], cancels=[], n=0, comfy=True, releases=[], upload_status=None,
                 tools=None, tool_submissions=[])


reset()


def auth(request: Request):
    if request.headers.get("authorization") != f"Bearer {TOKEN}":
        raise HTTPException(401, "a valid bearer token is required")


@app.get("/v1/health")
def health():
    return {"ok": True, "name": "semechka-media-engine", "version": "1.0.0", "api_version": 1, "comfy_reachable": STATE["comfy"]}


@app.get("/v1/system", dependencies=[Depends(auth)])
def system():
    running = sum(1 for j in STATE["jobs"].values() if j["status"] == "running")
    return {"memory": {"total_gb": 121.7, "available_gb": 100.0, "reserve_gb": 6.0}, "comfy": {"reachable": STATE["comfy"], "idle": not running, "resident": []},
            "jobs": {"running": running, "queued": sum(1 for j in STATE["jobs"].values() if j["status"] == "queued")}}


@app.get("/v1/capabilities", dependencies=[Depends(auth)])
def capabilities():
    return CAPS if STATE["tools"] is None else {**CAPS, "tools": STATE["tools"]}


@app.post("/v1/tools/{tid}/jobs", status_code=202, dependencies=[Depends(auth)])
async def submit_tool(tid: str, request: Request):
    body = await request.json()
    t = next((x for x in STATE["tools"] or [] if x["id"] == tid), None)
    if t is None:
        raise HTTPException(404, "unknown tool")
    if not t["ready"]:
        raise HTTPException(409, f"{t['name']} is not available on this server: {t['reason']}")
    key = str(body.get("client_key") or "")
    if key and key in STATE["by_key"]:
        return _doc(STATE["jobs"][STATE["by_key"][key]])
    for k, spec in t["inputs_schema"].items():
        if spec["type"] == "file" and (body.get("inputs") or {}).get(k) not in STATE["uploads"]:
            raise HTTPException(422, f"'{k}' is not an uploaded file")
    STATE["n"] += 1
    jid = f"{STATE['n']:012x}"
    STATE["jobs"][jid] = {"id": jid, "client_key": key, "kind": t["output"], "model": tid, "status": "queued", "polls": 0, "simple": {"prompt": str((body.get("inputs") or {}).get("text") or (body.get("inputs") or {}).get("prompt") or "")},
                          "profile": None, "tool": tid, "error": None}
    STATE["tool_submissions"].append({"tool": tid, **(body.get("inputs") or {})})
    if key:
        STATE["by_key"][key] = jid
    return _doc(STATE["jobs"][jid])


@app.get("/v1/models/{mid}/estimate", dependencies=[Depends(auth)])
def model_estimate(mid: str):
    return {"model": mid, "memory": {"peak_mb": 60000, "basis": "estimated"}, "seconds": {"estimate": 57, "basis": "measured"}}


@app.get("/v1/profiles/{pid}/estimate", dependencies=[Depends(auth)])
def profile_estimate(pid: str):
    return {"profile": pid, "memory": {"peak_mb": 50000, "basis": "estimated"}}


@app.post("/v1/uploads", status_code=201, dependencies=[Depends(auth)])
async def upload(request: Request):
    if STATE["upload_status"]:
        raise HTTPException(STATE["upload_status"], "That file could not be read as a picture.")
    f = (await request.form())["file"]
    data = await f.read()
    uid = f"{len(STATE['uploads']) + 1:032x}.png"
    STATE["uploads"][uid] = data
    return {"id": uid, "name": f.filename, "mime": f.content_type, "size": len(data)}


def _model(mid):
    return next((m for m in CAPS["models"] if m["id"] == mid), None)


@app.post("/v1/jobs", status_code=202, dependencies=[Depends(auth)])
async def submit(request: Request):
    body = await request.json()
    kind, simple, key = body.get("kind"), body.get("simple") or {}, body.get("client_key")
    if key and key in STATE["by_key"]:
        return _doc(STATE["jobs"][STATE["by_key"][key]])
    m = _model(simple.get("model"))
    if m is None or m["kind"] != kind:
        raise HTTPException(404, f"unknown model '{simple.get('model')}'")
    if "noon" in str(simple.get("prompt")):
        raise HTTPException(422, "minimax-h3 makes only these clip lengths: 124 frames (5.167 s).")
    for k in ("reference_image", "mask_image", "start_image", "end_image"):
        if simple.get(k) and simple[k] not in STATE["uploads"]:
            raise HTTPException(422, "unknown upload: add the picture again")
    STATE["n"] += 1
    jid = f"{STATE['n']:012x}"
    STATE["jobs"][jid] = {"id": jid, "client_key": key, "kind": kind, "model": m["id"], "status": "queued", "polls": 0, "simple": simple, "profile": None, "error": None}
    STATE["submissions"].append({"kind": kind, **simple})
    if key:
        STATE["by_key"][key] = jid
    return _doc(STATE["jobs"][jid])


@app.post("/v1/profiles/{pid}/jobs", status_code=202, dependencies=[Depends(auth)])
async def submit_profile(pid: str, request: Request):
    body = await request.json()
    if not any(p["id"] == pid for p in CAPS["profiles"]):
        raise HTTPException(404, "unknown profile")
    key = body.get("client_key")
    if key and key in STATE["by_key"]:
        return _doc(STATE["jobs"][STATE["by_key"][key]])
    for k, v in (body.get("inputs") or {}).items():
        if k in ("video", "refs"):
            for u in v if isinstance(v, list) else [v]:
                if u not in STATE["uploads"]:
                    raise HTTPException(422, "unknown upload: add the picture again")
    STATE["n"] += 1
    jid = f"{STATE['n']:012x}"
    STATE["jobs"][jid] = {"id": jid, "client_key": key, "kind": "video", "model": pid, "status": "queued", "polls": 0, "simple": {}, "profile": pid, "error": None}
    STATE["profile_submissions"].append({"profile": pid, **(body.get("inputs") or {})})
    if key:
        STATE["by_key"][key] = jid
    return _doc(STATE["jobs"][jid])


def _gen(j):
    if j.get("tool"):
        return _tool_gens(j)[0]
    if j["kind"] == "image":
        data, typ, w, h, extra = fs.make_png(), "image", 4, 4, {}
    else:
        data, typ, w, h = fs.make_mp4(), "video", 864, 480
        fr = j["simple"].get("frames") or 124
        fps = j["simple"].get("fps") or 24
        extra = {"duration": round(fr / fps, 4), "fps": fps, "frames": fr}
    gid = f"{j['id']}-0"
    STATE["files"][gid] = data
    return {"id": gid, "file_id": gid, "type": typ, "width": w, "height": h, "seed": j["simple"].get("seed"), **extra}


def _tool_gens(j):
    """What the real tools deliver: speech or music (one audio file), a 3D relief (a GLB and its depth picture), picture tools (PNGs)."""
    out = []
    files = {"audio": [(WAV, "audio", {"duration": 0.0002, "sample_rate": 24000, "channels": 1})], "3d": [(GLB, "3d", {}), (fs.make_png(), "image", {"width": 4, "height": 4})],
             "image": [(fs.make_png(), "image", {"width": 4, "height": 4})] * (2 if j["tool"] == "bg-remove" else 1)}[j["kind"]]
    for i, (data, typ, extra) in enumerate(files):
        gid = f"{j['id']}-{i}"
        STATE["files"][gid] = data
        out.append({"id": gid, "file_id": gid, "type": typ, **extra})
    return out


def _doc(j):
    text = str(j["simple"].get("prompt") or "")
    if j["status"] in ("queued", "running"):
        j["polls"] += 1
        if "slow" in text:
            j.update(status="running", progress=0.2)
        elif j["polls"] == 1:
            j.update(status="queued")
        elif j["polls"] <= 3:
            j.update(status="running", progress=0.25 * j["polls"])
        elif "boom" in text:
            j.update(status="failed", error="ComfyUI error: out of memory at /home/spark/opensfield/comfyui/x.py")
        else:
            j.update(status="completed", progress=1.0, generations=_tool_gens(j) if j.get("tool") else [_gen(j)])
    return {"id": j["id"], "client_key": j["client_key"], "kind": j["kind"], "model": j["model"], "status": j["status"], "progress": j.get("progress"),
            "progress_text": "Queued (position 1)" if j["status"] == "queued" else "Generating, step 3 of 8", "queue_position": 1 if j["status"] == "queued" else None,
            "error": {"message": j["error"]} if j.get("error") else None, "applied": {}, "generations": j.get("generations", [])}


@app.get("/v1/jobs", dependencies=[Depends(auth)])
def list_jobs(status: str | None = None, limit: int = 100):
    return [{**_doc(j)} for j in STATE["jobs"].values() if not status or j["status"] == status][:limit]


@app.get("/v1/jobs/{jid}", dependencies=[Depends(auth)])
def get_job(jid: str):
    j = STATE["jobs"].get(jid)
    if j is None:
        raise HTTPException(404, "unknown job")
    return _doc(j)


@app.post("/v1/jobs/{jid}/cancel", dependencies=[Depends(auth)])
def cancel(jid: str):
    j = STATE["jobs"].get(jid)
    if j is None:
        raise HTTPException(404, "unknown job")
    if j["status"] in ("queued", "running"):
        j["status"] = "cancelled"
        STATE["cancels"].append(jid)
    return _doc(j)


@app.get("/v1/jobs/{jid}/preview", dependencies=[Depends(auth)])
def preview(jid: str):
    raise HTTPException(404, "no preview yet")


@app.get("/v1/jobs/{jid}/{what}", dependencies=[Depends(auth)])
def trace(jid: str, what: str):
    raise HTTPException(404, "this engine publishes no trace for that")


@app.get("/v1/files/{fid}", dependencies=[Depends(auth)])
def get_file(fid: str):
    data = STATE["files"].get(fid)
    if data is None:
        raise HTTPException(404, "no such file")
    mime = "image/png" if data.startswith(b"\x89PNG") else "audio/wav" if data.startswith(b"RIFF") else "model/gltf-binary" if data.startswith(b"glTF") else "video/mp4"
    return Response(data, media_type=mime)


@app.delete("/v1/generations/{gid}", status_code=204, dependencies=[Depends(auth)])
def delete_generation(gid: str, permanent: bool = False):
    STATE["discards"].append({"id": gid, "permanent": permanent})
    STATE["files"].pop(gid, None)


@app.post("/v1/release", dependencies=[Depends(auth)])
async def release(request: Request):
    STATE["releases"].append(await request.json())
    if any(j["status"] == "running" for j in STATE["jobs"].values()):
        return JSONResponse({"ok": False, "error": "busy"}, status_code=409)
    return {"ok": True, "freed_gb": 40.0, "available_before_gb": 70.0, "available_after_gb": 110.0}


@app.get("/v1/reverse/{jid}")
def reverse(jid: str):
    j = STATE["jobs"].get(jid)
    if j is None or j.get("profile") != "reverse-video":
        raise HTTPException(404, "not an analysis job")
    return {"id": jid, "analysis": {"shots": [{"index": 0}], "source": {"duration_s": 2.0}}, "plan": {"shots": []}}
