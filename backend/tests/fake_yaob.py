"""A double of a Yaob Origin server (contract `yaob-origin/1`, docs/YAOB_ORIGIN.md): /v1/models with the runtime
name and identity record, /api/identity, and a chat stream that sends exactly the `yaob` status, sources and provenance
chunks Semechka consumes. Tests read back every request body (REQUESTS) and can replace the stream (SCRIPT).

Also runs on its own for trying the interface in a browser:

    cd backend && ../.venv/bin/python -m tests.fake_yaob --port 8899 --delay 1.2
"""
from __future__ import annotations

import asyncio
import json
import re

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
REQUESTS: list[dict] = []
SCRIPT: list = []  # each item: a list of raw SSE payloads (dict, or str sent as-is) for one call
DELAY = {"s": 0.0}
NODES = {"n": 119176878}

SOURCES = [
    {"n": 1, "kind": "web", "title": "Semechka 0.12 release notes", "url": "https://example.org/semechka/0.12",
     "site": "example.org", "published": "2026-11-12", "retrieved_at": "2026-11-14T10:02:11Z",
     "snippet": "Version 0.12 adds encrypted history for every reply and a faster model picker."},
    {"n": 2, "kind": "web", "title": "Running language models on a DGX Spark at home", "url": "https://www.example.com/articles/spark-llm",
     "site": "example.com", "published": None, "retrieved_at": "2026-11-14T10:02:12Z",
     "snippet": "A single Spark holds a 4B model with room to spare for a node network service."},
    {"n": 3, "kind": "web", "title": "Modular persistent networks, explained", "url": "https://docs.example.net/mpn",
     "site": "docs.example.net", "published": "2026-10-30", "retrieved_at": "2026-11-14T10:02:12Z", "snippet": "…"},
]


def designation(n: int) -> str:
    return f"N{n // 1_000_000_000}B" if n >= 1_000_000_000 else f"N{n // 1_000_000}M"


def identity() -> dict:
    n = NODES["n"]
    return {"system": "Yaob", "display_name": f"Yaob Origin {designation(n)}",
            "core": {"name": "Qwen3.5-4B", "repo": "Qwen/Qwen3.5-4B", "revision": "851bf6e8", "quantization": "Q8_0",
                     "parameters": 4659865088, "kind": "neural cognitive core"},
            "mpn": {"name": "Yaob Origin", "designation": designation(n), "nodes": n, "relationships": 1693590449,
                    "counted_at": "2026-11-14T10:00:00Z", "count_source": "mpn14b /api/status metrics.nodes.validated",
                    "kind": "Modular Persistent Network (node network, not neural parameters)"},
            "capabilities": ["mpn", "procedures", "web_search", "vision"], "runtime": "Semechka",
            "legacy_ids": ["yaob-c4b-mpn14b"]}


def reset():
    REQUESTS.clear()
    SCRIPT.clear()
    DELAY["s"] = 0.0
    NODES["n"] = 119176878


@app.get("/api/identity")
def api_identity():
    return identity()


@app.get("/v1/models")
def models():
    ident = identity()
    rows = []
    for mid, name in (("yaob-origin", ident["display_name"]), ("yaob-c4b-mpn14b", ident["display_name"])):
        rows.append({"id": mid, "object": "model", "name": name, "owned_by": "fylume", "yaob": ident})
    return {"object": "list", "data": rows}


def _chunk(delta=None, finish=None, yaob=None) -> dict:
    c = {"id": "chatcmpl-1", "object": "chat.completion.chunk", "model": "yaob-origin",
         "choices": [] if delta is None and finish is None else [{"index": 0, "delta": delta or {}, "finish_reason": finish}]}
    if yaob is not None:
        c["yaob"] = yaob
    return c


def _wants_web(body: dict) -> bool:
    mode = body.get("web_search")
    if mode not in ("auto", "on"):
        return False  # off, or a client that does not know the extension: the backend never searches
    q = next((m["content"] for m in reversed(body.get("messages", [])) if m.get("role") == "user"), "")
    return mode == "on" or bool(re.search(r"\b(latest|today|news|current|now|this week|release)\b", str(q), re.I))


def default_script(body: dict) -> list:
    if not _wants_web(body):
        text = "I am Yaob Origin. I answered from what I already know, without searching the web."
        return [_chunk({"role": "assistant"}), *[_chunk({"content": w + " "}) for w in text.split()], _chunk(finish="stop"),
                {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 16}}]
    answer = ("Semechka 0.12 stores every reply encrypted and ships a faster model picker [1]. A 4B core fits on one "
              "Spark next to the node network service [2], and the network keeps its knowledge as persistent nodes rather "
              "than as neural weights [3]. A marker without a source stays plain text [7].")
    return [_chunk(yaob={"status": {"state": "searching_web", "query": "Semechka 0.12 release"}}),
            "PAUSE",
            _chunk(yaob={"status": {"state": "reading_sources", "count": 3}}),
            "PAUSE",
            _chunk(yaob={"sources": SOURCES}),
            _chunk({"role": "assistant"}),
            *[_chunk({"content": answer[i:i + 24]}) for i in range(0, len(answer), 24)],
            _chunk(yaob={"provenance": {"id": "req_4f2a9c", "path": "/api/provenance/req_4f2a9c"}}),
            _chunk(finish="stop"), {"choices": [], "usage": {"prompt_tokens": 900, "completion_tokens": 70}}]


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    REQUESTS.append(body)
    items = SCRIPT.pop(0) if SCRIPT else default_script(body)

    async def gen():
        for it in items:
            if it == "PAUSE":
                if DELAY["s"]:
                    await asyncio.sleep(DELAY["s"])
                continue
            yield "data: " + (it if isinstance(it, str) else json.dumps(it, ensure_ascii=False)) + "\n\n"
            if DELAY["s"]:
                await asyncio.sleep(DELAY["s"] / 20)
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


if __name__ == "__main__":
    import argparse

    import uvicorn
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--delay", type=float, default=1.2, help="seconds each status stays on screen")
    a = ap.parse_args()
    DELAY["s"] = a.delay
    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")
