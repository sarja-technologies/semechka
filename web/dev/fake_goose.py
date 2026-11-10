"""A MOCK of the Work Mode (Goose) API, for developing and testing the Work UI without the real backend.
It implements the contract in docs/WORK_MODE_GOOSE.md (branch consol/work) closely enough to exercise every UI state, and is scripted: it never
runs a command and its "agent" says the same thing every time. Anything seen through it is MOCKED, never evidence that Work Mode works.
Run: python web/dev/fake_goose.py 8794   (and SEMECHKA_FAKE_GOOSE=http://127.0.0.1:8794 for `vite dev`)."""
import asyncio, json, sys, time, uuid
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
import uvicorn

app = FastAPI()
S: dict[str, dict] = {}
FLAGS = {"enabled": True, "ready": True, "slow_model": 4.0}

def status():
    return {"enabled": FLAGS["enabled"], "ready": FLAGS["ready"], "problems": [] if FLAGS["ready"] else ["container runtime unreachable"], "goose_version": "mock",
            "models": [{"id": "qwen3.8-27b", "label": "Qwen3.8 27B", "context": 65536, "state": "unloaded", "memory_mib": 28000, "default": True},
                       {"id": "qwen3-coder-next", "label": "Qwen3 Coder Next", "context": 65536, "state": "loaded", "memory_mib": 46000}],
            "autonomy": [{"id": "ask", "label": "Ask before every action", "default": True}, {"id": "smart", "label": "Ask only for sensitive actions"}, {"id": "auto", "label": "Never ask"}],
            "extensions": [{"id": "developer", "label": "Files and shell", "builtin": True, "required": True}], "mcp_servers": [], "skills": [], "limits": {"approval_timeout_s": 900}}

def sess(cid):
    s = S.get(cid)
    if not s: raise HTTPException(404, {"code": "no_session", "message": "This conversation has no Work session."})
    return s

def out(s):
    return {"id": s["id"], "conversation_id": s["cid"], "state": s["state"], "model": s["model"], "model_state": "unloaded", "autonomy": s["autonomy"], "mcp": [], "skills": [],
            "goose_session_id": None, "container": "running", "turn": s["turn"], "pending_approvals": [e["approval"] for e in s["events"] if e["type"] == "approval_request" and e["approval"]["id"] in s["open"]],
            "last_seq": len(s["events"]), "title": "", "created_at": s["t0"], "updated_at": time.time(), "last_active_at": time.time()}

def emit(s, type, **f):
    ev = {"seq": len(s["events"]) + 1, "type": type, "ts": time.time(), "turn_id": (s["turn"] or {}).get("id"), **f}
    s["events"].append(ev)
    return ev

@app.get("/api/work/goose/status")
def st(): return status()

@app.post("/api/work/goose/conversations/{cid}/session")
async def create(cid: str, request: Request):
    body = await request.json() if (await request.body()) else {}
    if cid not in S:
        S[cid] = {"id": "wg_" + uuid.uuid4().hex[:8], "cid": cid, "state": "idle", "model": body.get("model") or "qwen3.8-27b", "autonomy": body.get("autonomy") or "ask", "events": [], "turn": None, "open": {}, "t0": time.time(), "task": None}
    return out(S[cid])

@app.get("/api/work/goose/conversations/{cid}/session")
def get(cid: str): return out(sess(cid))

@app.patch("/api/work/goose/conversations/{cid}/session")
async def patch(cid: str, request: Request):
    s = sess(cid); b = await request.json()
    if s["turn"] and b.get("model"): raise HTTPException(409, {"code": "turn_running", "message": "A turn is running; change the model after it ends."})
    s.update({k: b[k] for k in ("model", "autonomy") if b.get(k)})
    return out(s)

@app.post("/api/work/goose/conversations/{cid}/session/stop")
def stop(cid: str): sess(cid); return {"ok": True}

@app.delete("/api/work/goose/conversations/{cid}/session")
def delete(cid: str): S.pop(cid, None); return {"ok": True}

async def wait_open(s, aid, timeout=120):
    for _ in range(int(timeout * 10)):
        if aid not in s["open"]: return s["decisions"].get(aid)
        await asyncio.sleep(0.1)
    return "timeout"

async def script(s, text):
    tid = s["turn"]["id"]
    try:
        emit(s, "turn_start", text=text)
        emit(s, "state", state="starting"); await asyncio.sleep(1.2)
        emit(s, "state", state="loading_model"); emit(s, "model_loading", model=s["model"], phase="loading")
        await asyncio.sleep(FLAGS["slow_model"])
        emit(s, "model_loading", model=s["model"], phase="ready"); emit(s, "state", state="running")
        for w in "Let me look at the project first. ".split(" "):
            emit(s, "reasoning_delta", message_id="m0", text=w + " "); await asyncio.sleep(0.05)
        for w in "I will run the tests to see what fails, then fix it.".split(" "):
            emit(s, "assistant_delta", message_id="m1", text=w + " "); await asyncio.sleep(0.06)
        emit(s, "tool_start", tool_call_id="c1", tool="shell", extension="developer", title="python3 -m pytest -q", input={"command": "python3 -m pytest -q"}, kind="execute")
        for line in ["F.\n", "=== FAILURES ===\n", "test_total: assert 5 == 6\n"]:
            emit(s, "tool_output", tool_call_id="c1", stream="stdout", text=line); await asyncio.sleep(0.4)
        emit(s, "tool_end", tool_call_id="c1", status="failed", output="F.\n=== FAILURES ===\ntest_total: assert 5 == 6\n1 failed, 1 passed", exit_code=1)
        if s["autonomy"] != "auto":
            ap = {"id": "ap_" + uuid.uuid4().hex[:6], "tool_call_id": "c2", "tool": "edit", "extension": "developer", "title": "edit calc.py", "input": {"path": "calc.py"},
                  "options": ["allow_once", "allow_always", "deny", "deny_always"], "created_at": time.time(), "expires_at": time.time() + 900}
            s["open"][ap["id"]] = True
            emit(s, "approval_request", approval=ap); emit(s, "state", state="awaiting_approval")
            d = await wait_open(s, ap["id"])
            emit(s, "approval_resolved", approval_id=ap["id"], decision=d or "cancelled", by="user"); emit(s, "state", state="running")
            if d in ("deny", "deny_always", "timeout", "cancelled"):
                emit(s, "assistant_delta", message_id="m2", text="Understood, I will not change the file.")
                emit(s, "turn_end", stop_reason="end_turn"); return
        emit(s, "tool_start", tool_call_id="c2", tool="edit", extension="developer", title="edit calc.py", input={"path": "calc.py"}, kind="edit")
        await asyncio.sleep(0.5)
        diff = "--- a/calc.py\n+++ b/calc.py\n@@ -1,3 +1,3 @@\n def total(xs):\n-    return sum(xs) - 1\n+    return sum(xs)\n \n"
        emit(s, "tool_end", tool_call_id="c2", status="completed", output="Edited calc.py", diff={"path": "calc.py", "unified": diff})
        emit(s, "files_changed", turn_id=tid, files=[{"path": "calc.py", "status": "modified", "additions": 1, "deletions": 1, "binary": False}], truncated=False)
        s["changes"][tid] = diff
        for w in "Fixed: total() subtracted one by mistake. The tests pass now.".split(" "):
            emit(s, "assistant_delta", message_id="m3", text=w + " "); await asyncio.sleep(0.05)
        emit(s, "usage", used=5231, size=65536)
        emit(s, "turn_end", stop_reason="end_turn")
    except asyncio.CancelledError:
        emit(s, "turn_end", stop_reason="cancelled"); raise
    finally:
        s["turn"] = None; s["state"] = "idle"; emit(s, "state", state="idle")

@app.post("/api/work/goose/conversations/{cid}/prompt", status_code=202)
async def prompt(cid: str, request: Request):
    s = sess(cid); b = await request.json()
    if s["turn"]: raise HTTPException(409, {"code": "turn_running", "message": "A turn is already running."})
    s["turn"] = {"id": "t_" + uuid.uuid4().hex[:6], "started": time.time()}; s["state"] = "running"; s.setdefault("decisions", {}); s.setdefault("changes", {})
    s["task"] = asyncio.create_task(script(s, b["text"]))
    return {"turn_id": s["turn"]["id"], "seq": len(s["events"])}

@app.post("/api/work/goose/conversations/{cid}/cancel")
async def cancel(cid: str):
    s = sess(cid)
    if s["task"] and not s["task"].done():
        for aid in list(s["open"]): s["open"].pop(aid, None); s["decisions"][aid] = "cancelled"
        s["task"].cancel()
    return out(s)

@app.post("/api/work/goose/conversations/{cid}/approvals/{aid}")
async def approve(cid: str, aid: str, request: Request):
    s = sess(cid); b = await request.json()
    if aid not in s["open"]: raise HTTPException(409, {"code": "already_resolved", "message": "Already answered."})
    s["decisions"][aid] = b["decision"]; s["open"].pop(aid)
    return {"ok": True}

@app.get("/api/work/goose/conversations/{cid}/transcript")
def transcript(cid: str):
    s = sess(cid); items = []
    for e in s["events"]:
        if e["type"] == "turn_start": items.append({"kind": "user", "turn_id": e["turn_id"], "text": e["text"]})
        elif e["type"] == "assistant_delta":
            if items and items[-1]["kind"] == "assistant" and items[-1]["message_id"] == e["message_id"]: items[-1]["text"] += e["text"]
            else: items.append({"kind": "assistant", "turn_id": e["turn_id"], "message_id": e["message_id"], "text": e["text"]})
        elif e["type"] == "tool_start": items.append({"kind": "tool", "turn_id": e["turn_id"], "tool_call_id": e["tool_call_id"], "tool": e["tool"], "title": e["title"], "input": e["input"], "status": "running", "output": ""})
        elif e["type"] == "tool_end":
            for i in items:
                if i["kind"] == "tool" and i["tool_call_id"] == e["tool_call_id"]: i.update(status=e["status"], output=e["output"], diff=e.get("diff"))
        elif e["type"] == "approval_request": items.append({"kind": "approval", **e["approval"]})
        elif e["type"] == "approval_resolved":
            for i in items:
                if i["kind"] == "approval" and i["id"] == e["approval_id"]: i["decision"] = e["decision"]; i["by"] = e["by"]
        elif e["type"] == "files_changed": items.append({"kind": "files", "turn_id": e["turn_id"], "files": e["files"]})
    return {"items": items, "last_seq": len(s["events"])}

@app.get("/api/work/goose/conversations/{cid}/changes/{tid}")
def changes(cid: str, tid: str):
    s = sess(cid); return {"turn_id": tid, "files": [], "diff": s["changes"].get(tid, ""), "truncated": False}

@app.get("/api/work/goose/conversations/{cid}/events")
async def events(cid: str, since: int = 0):
    s = sess(cid)
    async def gen():
        n = since
        while True:
            while n < len(s["events"]):
                e = s["events"][n]; n += 1
                yield f"id: {e['seq']}\nevent: {e['type']}\ndata: {json.dumps(e)}\n\n"
            yield ": ping\n\n" if int(time.time()) % 15 == 0 else ""
            await asyncio.sleep(0.15)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

@app.post("/__mock/flags")
async def flags(request: Request):
    FLAGS.update(await request.json()); return FLAGS

if __name__ == "__main__":
    print("MOCK Work Mode (Goose) API: scripted, runs nothing", flush=True)
    uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8794, log_level="warning")
