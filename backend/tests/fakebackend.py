"""A small OpenAI-compatible server used in tests. It is a test double for the network protocol only."""
import asyncio
import json

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

app = FastAPI()
calls: list[dict] = []
LIMITS = {"ctx": None}  # when set, refuse prompts over this many (4-characters-per-token) tokens like llama-server


@app.get("/v1/models")
def models():
    return {"data": [{"id": "fake-small", "object": "model", "owned_by": "test",
                      "meta": {"n_ctx_train": 4096, "n_params": 500_000_000, "size": 400_000_000}},
                     {"id": "fake-other", "object": "model", "owned_by": "test"}]}


@app.post("/v1/chat/completions")
async def chat(req: Request):
    body = await req.json()
    calls.append(body)
    if LIMITS["ctx"]:
        chars = sum(len(m["content"]) if isinstance(m["content"], str)
                    else sum(len(p.get("text", "")) for p in m["content"]) for m in body["messages"])
        if chars // 4 > LIMITS["ctx"]:
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": {"code": 400, "type": "exceed_context_size_error",
                                           "message": f"request ({chars // 4} tokens) exceeds the available context size "
                                                      f"({LIMITS['ctx']} tokens), try increasing it"}}, status_code=400)
    scripted = _agent_reply(body["messages"])  # only when the agent protocol is in the system prompt
    last = body["messages"][-1]["content"]
    words = ["Hello", " from", " the", " fake", " model"]
    if scripted is not None:
        words = [scripted[i:i + 30] for i in range(0, len(scripted), 30)]
    if isinstance(last, list):  # multimodal content parts: answer about the text part only
        last = " ".join(p.get("text", "") for p in last if isinstance(p, dict))
    if "benchmark-markdown" in last:
        words = _bench_tokens()
    elif "slow" in last:
        words = ["tick"] * 60
    elif "medium" in last:
        words = ["tick"] * 20  # about one second: long enough to hold a backend slot in queue tests

    async def gen():
        for w in words:
            yield "data: " + json.dumps({"choices": [{"delta": {"content": w}}]}) + "\n\n"
            await asyncio.sleep(0.004 if "benchmark-markdown" in last else 0.25 if "slow" in last
                                else 0.05 if "medium" in last else 0.01)
        yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
        yield "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": len(words)},
                                     "timings": {"predicted_per_second": 123.4, "prompt_per_second": 900.0}}) + "\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")


def _agent_reply(messages) -> str | None:
    """A deterministic 'agent model' for browser tests: list the files, write a one-page report, then answer.
    It reads only the conversation, so it works across restarts and needs no state."""
    system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
    if not isinstance(system, str) or "You work in steps. On EVERY turn reply with exactly ONE JSON object" not in system:
        return None
    done = sum(1 for m in messages if m["role"] == "user" and isinstance(m["content"], str) and m["content"].startswith("Tool result for"))
    task = next((m["content"] for m in messages if m["role"] == "user"), "")
    if "ask-for-approval" in task:  # one sensitive action, then the answer
        if done == 0:
            return json.dumps({"tool": "write_text_file", "args": {"name": "approved-note.md", "content": "# Note\nWritten after a person approved it."}})
        return json.dumps({"final": "Done. I saved approved-note.md."})
    if done == 0:
        return json.dumps({"tool": "list_files", "args": {}})
    if done == 1:
        return json.dumps({"tool": "create_document", "args": {"name": "agent-report.docx", "title": "Agent report", "blocks": [
            {"type": "heading", "text": "Summary"}, {"type": "paragraph", "text": "Prepared by the browser-test agent from the workspace files."},
            {"type": "bullets", "items": ["First finding", "Second finding"]}]}})
    return json.dumps({"final": "Done. I saved agent-report.docx in the workspace."})


_BENCH_SECTION = """## Section {n}

Here is a paragraph with **bold**, *italic* and `inline code`, followed by a [link](https://example.com/{n}).
It is long enough to wrap across several lines in a normal chat column so layout work is realistic.

| Name | Value | Notes |
| --- | ---: | --- |
| alpha | {n}1 | first row |
| beta | {n}2 | second row |

```python
def merge_{n}(a: list[int], b: list[int]) -> list[int]:
    out, i, j = [], 0, 0
    while i < len(a) and j < len(b):
        if a[i] <= b[j]:
            out.append(a[i]); i += 1
        else:
            out.append(b[j]); j += 1
    return out + a[i:] + b[j:]
```

- a bullet point
- another bullet point with `code`

"""


def _bench_tokens() -> list[str]:
    """A fixed ~1,400-token Markdown reply used by the client rendering benchmark (web/perf)."""
    text = "".join(_BENCH_SECTION.format(n=n) for n in range(1, 9))
    out, cur = [], ""
    for ch in text:  # tokens of about four characters, split like a real tokenizer would (never across a newline run)
        cur += ch
        if len(cur) >= 4 or ch == "\n":
            out.append(cur)
            cur = ""
    return out + ([cur] if cur else [])
