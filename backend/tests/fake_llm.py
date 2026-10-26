"""A scripted OpenAI-compatible chat model. Tests queue the exact replies the 'model' will give, and read back every
prompt it was sent - so agent behaviour is tested against real adapter, scheduler and tool code, with the one thing
that cannot be deterministic (the language model) replaced by a script."""
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
SCRIPT: list = []  # each item: a str reply, or a callable(messages) -> str
SEEN: list[list[dict]] = []  # the messages of every call
DELAY = {"s": 0.0}


def reset():
    SCRIPT.clear()
    SEEN.clear()
    DELAY["s"] = 0.0


def say(*replies):
    SCRIPT.extend(replies)


def tool(tool_name, /, **args):
    return json.dumps({"tool": tool_name, "args": args})


def final(text):
    return json.dumps({"final": text})


@app.get("/v1/models")
def models():
    return {"data": [{"id": "script-model", "object": "model", "owned_by": "test", "meta": {"n_ctx_train": 32768}}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    SEEN.append(body["messages"])
    if not SCRIPT:
        raise HTTPException(500, "the test script has no more replies")
    item = SCRIPT.pop(0)
    text = item(body["messages"]) if callable(item) else item

    async def gen():
        import asyncio
        if DELAY["s"]:
            await asyncio.sleep(DELAY["s"])
        for i in range(0, len(text), 40):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": text[i:i + 40]}}]}) + "\n\n"
        yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": max(1, len(text) // 4)}}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
