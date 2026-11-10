"""A double of the Spark's llama.cpp router, faithful to what was measured on the real one (b1-620d222):
GET /v1/models lists every model with status.value loaded|unloaded|loading; POST /models/load answers at once and the model
becomes `loaded` later (400 "model is already running" when it is loaded or loading, 404 unknown); POST /models/unload answers
{"success": true}; chat streams reasoning_content then content (and native tool_calls); /v1/embeddings, /tokenize and /props need the
model in the request. Every endpoint except /health wants the Bearer key."""
import asyncio
import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

KEY = "test-router-key"
app = FastAPI()
STATE: dict = {}
CALLS: list[tuple[str, dict]] = []
CFG = {"load_delay": 0.4, "fail_load": set()}

IDS = {
    "chat-small": dict(vision=False),
    "chat-vision": dict(vision=True),
    "chat-notools": dict(vision=False),
    "chat-big": dict(vision=False),
    "embed-test": dict(vision=False),
}


def reset(loaded=()):
    STATE.clear()
    CALLS.clear()
    CFG.update(load_delay=0.4, fail_load=set())
    for i in IDS:
        STATE[i] = {"state": "loaded" if i in loaded else "unloaded", "ready_at": 0.0}


reset()


def _auth(req: Request):
    if req.headers.get("authorization") != f"Bearer {KEY}":
        return JSONResponse({"error": {"code": 401, "message": "Invalid API Key", "type": "authentication_error"}}, status_code=401)


def _state(i):
    s = STATE[i]
    if s["state"] == "loading" and time.monotonic() >= s["ready_at"]:
        s["state"] = "unloaded" if i in CFG["fail_load"] else "loaded"
    return s["state"]


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/v1/models")
def models(req: Request):
    if (e := _auth(req)):
        return e
    out = []
    for i, d in IDS.items():
        st = _state(i)
        m = {"id": i, "aliases": [], "tags": [], "object": "model", "owned_by": "llamacpp", "created": 1,
             "status": {"value": st, "args": ["/secret/path/llama-server", "--model", "/home/x/secret.gguf"]},
             "architecture": {"input_modalities": ["text"] + (["image"] if d["vision"] else []), "output_modalities": ["text"]},
             "context_length": 262144}
        if st == "loaded":
            m["meta"] = {"n_ctx": 8192, "n_ctx_train": 262144, "n_embd": 1024, "n_params": 1_000_000, "size": 5_000_000, "ftype": "Q8_0"}
        out.append(m)
    return {"object": "list", "data": out}


@app.post("/models/load")
async def load(req: Request):
    if (e := _auth(req)):
        return e
    body = await req.json()
    CALLS.append(("load", body))
    i = body.get("model")
    if i not in STATE:
        return JSONResponse({"error": {"code": 404, "message": "File Not Found", "type": "not_found_error"}}, status_code=404)
    if _state(i) != "unloaded":
        return JSONResponse({"error": {"code": 400, "message": "model is already running", "type": "invalid_request_error"}}, status_code=400)
    STATE[i].update(state="loading", ready_at=time.monotonic() + CFG["load_delay"])
    return {"success": True}


@app.post("/models/unload")
async def unload(req: Request):
    if (e := _auth(req)):
        return e
    body = await req.json()
    CALLS.append(("unload", body))
    if body.get("model") in STATE:
        STATE[body["model"]]["state"] = "unloaded"
    return {"success": True}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    if (e := _auth(req)):
        return e
    body = await req.json()
    CALLS.append(("chat", body))
    last = body["messages"][-1]["content"]
    parts = last if isinstance(last, list) else [{"type": "text", "text": last}]
    text = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
    has_image = any(p.get("type") == "image_url" for p in parts)
    if _state(body["model"]) != "loaded":
        return JSONResponse({"error": {"code": 503, "message": "model not loaded"}}, status_code=503)
    if "toolong" in text:
        return JSONResponse({"error": {"code": 400, "type": "exceed_context_size_error",
                                       "message": "request (9000 tokens) exceeds the available context size (8192 tokens), try increasing it"}},
                            status_code=400)

    async def gen():
        def ev(delta, fin=None):
            return "data: " + json.dumps({"choices": [{"index": 0, "delta": delta, "finish_reason": fin}]}) + "\n\n"
        if body.get("tools") and "weather" in text and not any(m.get("role") == "tool" for m in body["messages"]):
            yield ev({"tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": ""}}]})
            yield ev({"tool_calls": [{"index": 0, "function": {"arguments": '{"city": "Syd'}}]})
            yield ev({"tool_calls": [{"index": 0, "function": {"arguments": 'ney"}'}}]})
            yield ev({}, "tool_calls")
        else:
            if (body.get("chat_template_kwargs") or {}).get("enable_thinking") is not False:
                yield ev({"reasoning_content": "Let me "})
                yield ev({"reasoning_content": "think."})
            ans = "I see an image." if has_image else ("The weather is sunny." if any(m.get("role") == "tool" for m in body["messages"]) else "Hello from the router.")
            for w in ans.split(" "):
                yield ev({"content": w + " "})
                await asyncio.sleep(0.01)
            yield ev({}, "stop")
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 11, "completion_tokens": 5},
                                     "timings": {"prompt_per_second": 500.0, "predicted_per_second": 50.0}}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.post("/v1/embeddings")
async def embeddings(req: Request):
    if (e := _auth(req)):
        return e
    body = await req.json()
    CALLS.append(("embeddings", body))
    if _state(body["model"]) != "loaded":
        STATE[body["model"]].update(state="loaded")  # the router loads it on demand
    texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {"object": "list", "model": body["model"], "data": [{"index": i, "embedding": [1.0] + [0.0] * 1023} for i, _ in enumerate(texts)]}


@app.get("/props")
def props(req: Request, model: str | None = None):
    if (e := _auth(req)):
        return e
    CALLS.append(("props", {"model": model}))
    if not model:
        return {"role": "router", "default_generation_settings": {"n_ctx": 0}}
    return {"default_generation_settings": {"n_ctx": 4096}}


@app.post("/tokenize")
async def tokenize(req: Request):
    if (e := _auth(req)):
        return e
    body = await req.json()
    CALLS.append(("tokenize", body))
    if "model" not in body:
        return JSONResponse({"error": {"message": "model name is missing from the request"}}, status_code=400)
    return {"tokens": [1, 2, 3]}
