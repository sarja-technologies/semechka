"""Integration against real inference servers. Skipped unless the matching environment variable is set:

  SEMECHKA_REAL_LLAMA_URL=http://127.0.0.1:18080/v1   (llama.cpp llama-server)
  SEMECHKA_REAL_OLLAMA_URL=http://127.0.0.1:11434
  SEMECHKA_REAL_SARNESS_URL=http://127.0.0.1:8742 SEMECHKA_REAL_SARNESS_TOKEN=...
"""
import os

import pytest

from semechka import adapters
from .conftest import PW, Api, make_user

LLAMA = os.environ.get("SEMECHKA_REAL_LLAMA_URL")
OLLAMA = os.environ.get("SEMECHKA_REAL_OLLAMA_URL")
SARNESS = os.environ.get("SEMECHKA_REAL_SARNESS_URL")


async def _collect(ad, model):
    text, done = "", None
    async for ev in ad.chat_stream(model, [{"role": "user", "content": "Say hello in one short sentence."}], {"max_tokens": 24, "temperature": 0}):
        if ev["type"] == "delta":
            text += ev["content"]
        else:
            done = ev
    return text, done


@pytest.mark.skipif(not LLAMA, reason="no llama.cpp server configured")
async def test_llama_cpp_adapter_discovers_and_streams():
    ad = adapters.make("openai_compat", LLAMA)
    assert (await ad.health())["ok"]
    models = await ad.discover()
    assert models and models[0].meta["context"] and models[0].meta["params"] > 1_000_000
    assert models[0].meta["quantization"].startswith("Q")
    text, done = await _collect(ad, models[0].real_id)
    assert text.strip() and done["usage"]["completion_tokens"] > 0 and done["timings"]["predicted_per_second"] > 0


@pytest.mark.skipif(not LLAMA, reason="no llama.cpp server configured")
def test_full_stack_against_llama_cpp(app, st, cfg):
    from semechka.routers.setup import ensure_setup_token
    api = Api(app)
    token = ensure_setup_token(st)
    probe = api.post("/api/setup/backend-test", headers={"x-setup-token": token}, json={"adapter": "openai_compat", "base_url": LLAMA})
    real_id = probe.json()["models"][0]["real_id"]
    r = api.post("/api/setup/complete", headers={"x-setup-token": token}, json={
        "owner": {"email": "o@example.test", "password": PW, "display_name": "O"}, "preset": "research",
        "backend": {"adapter": "openai_compat", "base_url": LLAMA}, "enable_models": [real_id], "smoke_model": real_id})
    assert r.status_code == 200 and r.json()["smoke"]["ok"], r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    m = make_user(api, app, "r@example.test")  # research preset gives Members technical read fields
    mid = m.get("/api/chat/models").json()[0]["id"]
    cid = m.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    status, ev = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "Name one colour.", "model_id": mid, "params": {"max_tokens": 16}})
    assert status == 200
    done = ev[-1]
    assert done["content"].strip()
    t = done["tech"]
    assert t["model_real_id"].endswith(".gguf") and "/" not in t["model_real_id"]  # no server path leaks
    assert t["tokens_per_s"] > 0 and t["tps_source"] == "backend" and t["ttft_s"] > 0 and t["completion_tokens"] > 0
    assert t["quantization"].startswith("Q") and t["context"] >= 2048 and t["runtime"] == "openai_compat"


@pytest.mark.skipif(not OLLAMA, reason="no Ollama configured")
async def test_ollama_adapter():
    ad = adapters.make("ollama", OLLAMA)
    assert (await ad.health())["ok"]
    models = await ad.discover()
    assert models
    text, done = await _collect(ad, models[0].real_id)
    assert text.strip()


@pytest.mark.skipif(not SARNESS, reason="no Sarness Engine configured")
async def test_sarness_adapter():
    ad = adapters.make("sarness", SARNESS, os.environ.get("SEMECHKA_REAL_SARNESS_TOKEN"))
    h = await ad.health()
    assert h["ok"], h
    models = await ad.discover()
    assert isinstance(models, list)


@pytest.mark.skipif(not SARNESS, reason="no Sarness Engine configured")
def test_full_stack_against_sarness_engine(app, st, cfg):
    from semechka.routers.setup import ensure_setup_token
    tok = os.environ.get("SEMECHKA_REAL_SARNESS_TOKEN")
    api = Api(app)
    token = ensure_setup_token(st)
    probe = api.post("/api/setup/backend-test", headers={"x-setup-token": token},
                     json={"adapter": "sarness", "base_url": SARNESS, "api_key": tok}).json()
    assert probe["health"]["ok"], probe
    ref = probe["models"][0]["real_id"]
    assert ref.startswith("local:") and probe["models"][0]["external"] is False
    r = api.post("/api/setup/complete", headers={"x-setup-token": token}, json={
        "owner": {"email": "o@example.test", "password": PW, "display_name": "O"}, "preset": "research",
        "backend": {"adapter": "sarness", "base_url": SARNESS, "api_key": tok}, "enable_models": [ref], "smoke_model": ref})
    assert r.status_code == 200 and r.json()["smoke"]["ok"], r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    m = make_user(api, app, "r@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    cid = m.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    status, ev = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "Name one colour.", "model_id": mid, "params": {"max_tokens": 16}})
    assert status == 200 and ev[-1]["content"].strip()
    t = ev[-1]["tech"]
    assert t["runtime"] == "sarness" and t["tokens_per_s"] > 0 and t["completion_tokens"] > 0
    # real system telemetry comes through the adapter, and only to people who may read it
    sysinfo = api.get("/api/admin/system").json()
    sb = next(b for b in sysinfo["backends"] if b["adapter"] == "sarness")
    assert sb["health"]["ok"] and sb["system"] is not None
