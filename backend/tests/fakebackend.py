"""A small OpenAI-compatible server used in tests. It is a test double for the network protocol only."""
import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
calls: list[dict] = []


@app.get("/v1/models")
def models():
    return {"data": [{"id": "fake-small", "object": "model", "owned_by": "test",
                      "meta": {"n_ctx_train": 4096, "n_params": 500_000_000, "size": 400_000_000}},
                     {"id": "fake-other", "object": "model", "owned_by": "test"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    calls.append(body)
    last = body["messages"][-1]["content"]
    words = ["Hello", " from", " the", " fake", " model"]
    if "slow" in last:
        words = ["tick"] * 60

    async def gen():
        for w in words:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n"
            await asyncio.sleep(0.25 if "slow" in last else 0.01)
        yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": len(words)},
                                     "timings": {"predicted_per_second": 123.4, "prompt_per_second": 900.0}}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
