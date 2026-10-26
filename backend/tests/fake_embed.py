"""A deterministic stand-in for an OpenAI-compatible embeddings endpoint. Words in the same concept share a
dimension (so "cab" and "taxi" are close without sharing a keyword); unrelated words are orthogonal. It exists to test
OUR plumbing - permissions, caching, staleness, failure handling - not embedding quality, which is checked against the
real model on the Spark."""
import hashlib
import math
import re

from fastapi import FastAPI, HTTPException

CONCEPTS = [{"taxi", "cab", "ride", "rides"}, {"approval", "permission", "authorization", "authorisation", "approve"},
            {"revenue", "income", "sales", "earnings"}, {"grew", "growth", "increase", "rose", "grow"},
            {"salary", "pay", "compensation", "wage"}]
DIMS = 96
STATE = {"down": False, "calls": 0, "model": "fake-embed-1"}
app = FastAPI()


def vec(text: str) -> list[float]:
    if "Query: " in text:
        text = text.split("Query: ", 1)[1]
    v = [0.0] * DIMS
    for w in re.findall(r"\w+", text.lower()):
        c = next((i for i, grp in enumerate(CONCEPTS) if w in grp), None)
        key = f"concept{c}" if c is not None else w
        v[int(hashlib.sha1(key.encode()).hexdigest(), 16) % DIMS] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@app.get("/health")
def health():
    if STATE["down"]:
        raise HTTPException(503)
    return {"status": "ok"}


@app.post("/v1/embeddings")
def embeddings(body: dict):
    if STATE["down"]:
        raise HTTPException(503, "down")
    STATE["calls"] += 1
    texts = body["input"] if isinstance(body["input"], list) else [body["input"]]
    return {"data": [{"index": i, "embedding": vec(t)} for i, t in enumerate(texts)], "model": body.get("model")}
