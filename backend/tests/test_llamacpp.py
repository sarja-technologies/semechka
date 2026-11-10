"""The llama.cpp router adapter against a double of the Spark's router (tests/fake_llamacpp.py): discovery and capability enrichment
from models.json / models.verified.json, streaming reasoning + content, cold start, memory admission, tools only where verified,
vision, thinking switch, the chat route's `engine` event, lifecycle load/unload, and embeddings through the router."""
import asyncio
import json
import time
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select

from semechka import embed_svc
from semechka.adapters import REGISTRY, AdapterError
from semechka.adapters import llamacpp as lc
from semechka.adapters.llamacpp import LlamaCppAdapter
from semechka.models import Backend, Model

from . import fake_llamacpp as fr
from .conftest import PW, Api, Server, _free_port

GIB = 1024  # MiB


@pytest.fixture(scope="module")
def router_url():
    srv = Server(fr.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}/v1"
    srv.stop()


@pytest.fixture
def spark(tmp_path, monkeypatch):
    """A spark-llm directory (models.json + models.verified.json) and a meminfo file the tests control."""
    d = tmp_path / "spark-llm"
    d.mkdir()
    (d / "models.json").write_text(json.dumps([
        {"id": "chat-small", "legacy_id": "chat-small@Q4_K_M", "file": "/secret/a.gguf", "mmproj": None, "ctx": 32768, "parallel": 4,
         "vision": False, "reasoning": False, "context_max": 262144, "est_mem_mb": 3900, "tags": ["small"]},
        {"id": "chat-vision", "legacy_id": "chat-vision@UD-Q6_K", "file": "/secret/b.gguf", "mmproj": "/secret/b.mmproj", "ctx": 65536,
         "parallel": 2, "vision": True, "reasoning": True, "context_max": 262144, "est_mem_mb": 34000, "tags": ["27b"]},
        {"id": "chat-notools", "legacy_id": "chat-notools@Q6_K", "file": "/secret/c.gguf", "ctx": 65536, "parallel": 2, "vision": False,
         "reasoning": True, "context_max": 32768, "est_mem_mb": 25000},
        {"id": "chat-big", "legacy_id": "chat-big@MXFP4", "file": "/secret/d.gguf", "ctx": 65536, "parallel": 2, "vision": False,
         "reasoning": True, "context_max": 131072, "est_mem_mb": 70000},
    ]))
    (d / "models.verified.json").write_text(json.dumps({"models": {
        "chat-small": {"loaded_memory_mib": 8 * GIB, "tool_roundtrip": True, "context_retention": True, "coding_agent_compatible": False, "note": ""},
        "chat-vision": {"loaded_memory_mib": 28 * GIB, "tool_roundtrip": True, "context_retention": True, "coding_agent_compatible": True, "note": ""},
        "chat-notools": {"loaded_memory_mib": 30 * GIB, "tool_roundtrip": False, "context_retention": True, "note": "never emitted a tool call"},
        "chat-big": {"loaded_memory_mib": 64 * GIB, "tool_roundtrip": True},
    }}))
    mem = tmp_path / "meminfo"
    meminfo(mem, 100 * GIB)
    monkeypatch.setenv("SEMECHKA_SPARK_LLM_DIR", str(d))
    monkeypatch.setenv("SEMECHKA_MEMINFO_PATH", str(mem))
    fr.reset()
    return SimpleNamespace(dir=d, mem=mem)


def meminfo(path, avail_mib):
    path.write_text(f"MemTotal:       {121 * GIB * 1024} kB\nMemFree:        1 kB\nMemAvailable:   {avail_mib * 1024} kB\n")


def adapter(url, **opts):
    return LlamaCppAdapter(url, fr.KEY, opts)


def run(coro):
    return asyncio.run(coro)


async def collect(ad, model, messages=None, params=None):
    return [ev async for ev in ad.chat_stream(model, messages or [{"role": "user", "content": "hi"}], params or {})]


# ---- registry / discovery -----------------------------------------------------------------------------------------
def test_registered_and_declares_what_it_does():
    m = REGISTRY["llamacpp"].manifest
    assert m.multi_model and m.model_load and m.model_unload and not m.exclusive_chat_model and m.reports_usage_tokens


def test_discovery_enriches_from_the_two_files_and_never_leaks_paths(router_url, spark):
    found = {d.real_id: d for d in run(adapter(router_url).discover())}
    assert set(found) == set(fr.IDS)
    v = found["chat-vision"]
    assert v.capabilities == ["chat", "vision", "tools"] and v.loaded is False
    assert v.meta["context"] == 32768 and v.meta["slot_ctx"] == 32768 and v.meta["context_trained"] == 262144  # 65536 / 2 slots
    assert v.meta["loaded_memory_mib"] == 28 * GIB and v.meta["memory_source"] == "measured" and v.meta["reasoning"] is True
    assert v.meta["quantization"] == "UD-Q6_K" and v.meta["coding_agent_compatible"] is True and v.meta["router"] is True
    n = found["chat-notools"]
    assert n.capabilities == ["chat"] and n.meta["tool_roundtrip"] is False and n.meta["verified_note"] == "never emitted a tool call"
    assert found["chat-small"].meta["context"] == 8192  # 32768 / 4 slots
    e = found["embed-test"]
    assert e.capabilities == ["embeddings"] and e.meta["embedding"] is True
    blob = json.dumps({k: [d.meta, d.capabilities] for k, d in found.items()})
    assert "/secret" not in blob and "llama-server" not in blob


def test_discovery_without_the_files_uses_the_router_alone(router_url, tmp_path, monkeypatch):
    monkeypatch.setenv("SEMECHKA_SPARK_LLM_DIR", str(tmp_path / "nothing-here"))
    fr.reset(loaded=["chat-vision"])
    found = {d.real_id: d for d in run(adapter(router_url).discover())}
    v = found["chat-vision"]
    assert v.capabilities == ["chat", "vision"] and v.loaded is True  # vision from architecture.input_modalities; no tools claim
    assert v.meta["context"] == 8192 and "loaded_memory_mib" not in v.meta  # the loaded model's own n_ctx


def test_wrong_key_is_reported_in_words(router_url, spark):
    with pytest.raises(AdapterError) as e:
        run(LlamaCppAdapter(router_url, "nope").discover())
    assert e.value.kind == "auth"
    assert run(LlamaCppAdapter(router_url, "nope").health())["ok"] is False
    assert run(adapter(router_url).health())["ok"] is True


def test_base_url_may_omit_v1(router_url, spark):
    assert run(adapter(router_url.removesuffix("/v1")).health())["ok"] is True


# ---- streaming, cold start, admission --------------------------------------------------------------------------------
def test_streams_reasoning_then_content_and_usage(router_url, spark):
    fr.reset(loaded=["chat-small"])
    evs = run(collect(adapter(router_url), "chat-small"))
    assert [e["type"] for e in evs][-1] == "done" and not any(e["type"] == "status" for e in evs)  # already loaded: no loading state
    assert "".join(e["reasoning"] for e in evs if e["type"] == "delta") == "Let me think."
    assert "".join(e["content"] for e in evs if e["type"] == "delta").strip() == "Hello from the router."
    d = evs[-1]
    assert d["finish_reason"] == "stop" and d["usage"] == {"prompt_tokens": 11, "completion_tokens": 5}
    assert d["timings"]["predicted_per_second"] == 50.0


def test_cold_model_says_loading_then_answers(router_url, spark, monkeypatch):
    monkeypatch.setattr(lc, "POLL_SECONDS", 0.1)
    monkeypatch.setattr(lc, "STATUS_EVERY", 0.2)
    fr.reset()
    fr.CFG["load_delay"] = 0.9
    evs = run(collect(adapter(router_url), "chat-small"))
    status = [e for e in evs if e["type"] == "status"]
    assert len(status) >= 2 and all(s["state"] == "loading" and s["model"] == "chat-small" for s in status)
    assert evs.index(status[-1]) < next(i for i, e in enumerate(evs) if e["type"] == "delta")  # the loading state comes before any text
    assert [c[0] for c in fr.CALLS if c[0] == "load"] == ["load"]
    assert evs[-1]["type"] == "done"


def test_two_replies_for_one_cold_model_start_one_load(router_url, spark, monkeypatch):
    monkeypatch.setattr(lc, "POLL_SECONDS", 0.1)
    fr.reset()
    fr.CFG["load_delay"] = 0.6

    async def both():
        ad = adapter(router_url)
        return await asyncio.gather(collect(ad, "chat-small"), collect(ad, "chat-small"))
    a, b = run(both())
    assert a[-1]["type"] == b[-1]["type"] == "done"
    assert sum(1 for c in fr.CALLS if c[0] == "load") == 1


def test_a_load_that_never_finishes_ends_with_a_timeout_message(router_url, spark, monkeypatch):
    monkeypatch.setattr(lc, "POLL_SECONDS", 0.1)
    fr.reset()
    fr.CFG["load_delay"] = 60
    with pytest.raises(AdapterError) as e:
        run(collect(adapter(router_url, load_timeout_s=1), "chat-small"))
    assert e.value.kind == "timeout" and "did not finish loading within 1 seconds" in str(e.value)


def test_a_load_that_dies_is_reported(router_url, spark, monkeypatch):
    monkeypatch.setattr(lc, "POLL_SECONDS", 0.05)
    fr.reset()
    fr.CFG["fail_load"] = {"chat-small"}
    fr.CFG["load_delay"] = 0.1
    monkeypatch.setattr(lc, "DIED_AFTER", 0.3)
    with pytest.raises(AdapterError) as e:
        run(collect(adapter(router_url), "chat-small"))
    assert e.value.kind == "load_failed"


def test_not_enough_memory_refuses_in_words_and_evicts_nothing(router_url, spark):
    fr.reset(loaded=["chat-vision"])
    meminfo(spark.mem, 60 * GIB)  # chat-big needs 64 GiB measured + 8 GiB headroom
    with pytest.raises(AdapterError) as e:
        run(collect(adapter(router_url), "chat-big"))
    msg = str(e.value)
    assert e.value.kind == "memory" and e.value.status == 503
    assert "about 64.0 GiB (measured)" in msg and "60.0 GiB is free" in msg and "chat-vision" in msg and "Nothing was stopped" in msg
    assert [c[0] for c in fr.CALLS] == []  # no load, no unload, no chat reached the router
    meminfo(spark.mem, 72 * GIB)  # exactly need + headroom
    assert run(collect(adapter(router_url, load_timeout_s=5), "chat-big"))[-1]["type"] == "done"
    assert [c[0] for c in fr.CALLS if c[0] == "unload"] == []


def test_admission_can_be_turned_off_and_unknown_sizes_are_not_guessed(router_url, spark):
    meminfo(spark.mem, 1 * GIB)
    ad = adapter(router_url, admission=False)
    assert ad.admission("chat-big", []) is None
    assert adapter(router_url).admission("model-without-a-size", []) is None
    assert adapter(router_url).admission("chat-big", []) is not None


def test_estimate_is_used_when_nothing_was_measured(router_url, spark):
    (spark.dir / "models.verified.json").write_text(json.dumps({"models": {}}))
    meminfo(spark.mem, 40 * GIB)
    msg = adapter(router_url).admission("chat-big", ["x"])  # est 70000 MB ~ 66.8 GiB
    assert msg and "(estimated)" in msg


# ---- tools, vision, thinking ---------------------------------------------------------------------------------------------
TOOLS = [{"type": "function", "function": {"name": "get_weather", "description": "d", "parameters": {"type": "object", "properties": {"city": {"type": "string"}}}}}]


def test_tools_go_only_to_a_model_whose_round_trip_was_verified(router_url, spark):
    fr.reset(loaded=["chat-small"])  # chat-notools is NOT loaded: refusing it must not load it (a refused request loads nothing)
    with pytest.raises(AdapterError) as e:
        run(collect(adapter(router_url), "chat-notools", [{"role": "user", "content": "weather in Sydney"}], {"tools": TOOLS}))
    assert e.value.kind == "unsupported" and e.value.status == 422
    assert not [c for c in fr.CALLS if c[0] in ("chat", "load")]
    evs = run(collect(adapter(router_url), "chat-small", [{"role": "user", "content": "weather in Sydney"}], {"tools": TOOLS, "tool_choice": "auto"}))
    calls = next(e for e in evs if e["type"] == "tool_calls")["calls"]
    assert calls == [{"id": "call_1", "name": "get_weather", "arguments": '{"city": "Sydney"}'}]
    assert evs[-1]["finish_reason"] == "tool_calls"
    sent = [c[1] for c in fr.CALLS if c[0] == "chat"][-1]
    assert sent["tools"] == TOOLS and sent["tool_choice"] == "auto"


def test_a_tool_result_turn_gets_a_final_answer(router_url, spark):
    fr.reset(loaded=["chat-small"])
    msgs = [{"role": "user", "content": "weather in Sydney"},
            {"role": "assistant", "content": None, "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"city":"Sydney"}'}}]},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"}]
    evs = run(collect(adapter(router_url), "chat-small", msgs, {"tools": TOOLS}))
    assert "sunny" in "".join(e.get("content", "") for e in evs)


def test_no_tools_in_the_request_when_none_asked(router_url, spark):
    fr.reset(loaded=["chat-small"])
    run(collect(adapter(router_url), "chat-small"))
    assert "tools" not in [c[1] for c in fr.CALLS if c[0] == "chat"][-1]


def test_images_reach_the_router_as_image_url_parts(router_url, spark):
    fr.reset(loaded=["chat-vision"])
    parts = [{"type": "text", "text": "what is this"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}]
    evs = run(collect(adapter(router_url), "chat-vision", [{"role": "user", "content": parts}]))
    assert "I see an image" in "".join(e.get("content", "") for e in evs)
    assert [c[1] for c in fr.CALLS if c[0] == "chat"][-1]["messages"][-1]["content"] == parts


def test_thinking_switch_is_passed_as_a_template_argument(router_url, spark):
    fr.reset(loaded=["chat-small", "chat-vision"])
    ad = adapter(router_url)
    evs = run(collect(ad, "chat-small", params={"thinking": False}))  # a model models.json does not call a reasoning model
    assert [c[1] for c in fr.CALLS if c[0] == "chat"][-1]["chat_template_kwargs"] == {"enable_thinking": False}
    assert not any(e.get("reasoning") for e in evs if e["type"] == "delta")
    run(collect(ad, "chat-small", params={"thinking": True}))
    assert [c[1] for c in fr.CALLS if c[0] == "chat"][-1]["chat_template_kwargs"] == {"enable_thinking": True}
    run(collect(ad, "chat-small"))
    assert "chat_template_kwargs" not in [c[1] for c in fr.CALLS if c[0] == "chat"][-1]


def test_context_overflow_message_survives_so_the_window_can_be_learned(router_url, spark):
    from semechka import chat_svc
    fr.reset(loaded=["chat-small"])
    with pytest.raises(AdapterError) as e:
        run(collect(adapter(router_url), "chat-small", [{"role": "user", "content": "toolong"}]))
    assert chat_svc.CTX_ERR.search(str(e.value))


# ---- status / load / unload --------------------------------------------------------------------------------------------------
def test_status_load_unload(router_url, spark):
    fr.reset(loaded=["chat-small"])
    ad = adapter(router_url)
    s = run(ad.status())
    assert s["llm"]["loaded"] == ["chat-small"] and s["llm"]["state"] == "ready" and s["memory"]["available_mb"] == 100 * GIB
    run(ad.load("chat-vision"))
    run(ad.load("chat-vision"))  # "already running" is not an error
    assert run(ad.status())["llm"]["loading"] == ["chat-vision"]
    run(ad.unload("chat-small"))
    with pytest.raises(AdapterError) as e:
        run(ad.load("no-such-model"))
    assert e.value.status == 404


# ---- through the app ------------------------------------------------------------------------------------------------------------
@pytest.fixture
def lcapp(app, st, router_url, spark, monkeypatch):
    monkeypatch.setattr(lc, "POLL_SECONDS", 0.1)
    monkeypatch.setattr(lc, "STATUS_EVERY", 0.2)
    fr.reset(loaded=["chat-small"])
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "llamacpp", "base_url": router_url, "api_key": fr.KEY},
        "enable_models": ["chat-small", "chat-vision", "chat-big"], "smoke_model": "chat-small"})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    return api


def mid(st, real):
    with st.db.session() as s:
        return s.execute(select(Model.id).where(Model.real_id == real)).scalar_one()


def test_setup_stores_the_key_encrypted_and_lists_capabilities(lcapp, st):
    with st.db.session() as s:
        b = s.execute(select(Backend)).scalar_one()
        assert b.adapter == "llamacpp" and b.api_key_enc and fr.KEY not in b.api_key_enc and not b.external
    models = {m["alias"]: m for m in lcapp.get("/api/chat/models").json()}
    assert models["chat-vision"]["capabilities"] == ["chat", "vision", "tools"]
    assert "embed-test" not in models  # an embedding model is never offered for chat
    assert models["chat-vision"]["tech"]["context"] == 32768


def test_chat_route_reports_the_loading_state_then_streams(lcapp, st):
    cid = lcapp.post("/api/chat/conversations", json={"model_id": mid(st, "chat-vision")}).json()["id"]
    code, evs = lcapp.stream(f"/api/chat/conversations/{cid}/generate",
                             {"mode": "send", "operation": "text", "content": "hello", "model_id": mid(st, "chat-vision")})
    assert code == 200
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "start" and "engine" in kinds and kinds.index("engine") < kinds.index("delta") and kinds[-1] == "done"
    eng = next(e for e in evs if e["type"] == "engine")
    assert eng["state"] == "loading" and eng["model"] == "chat-vision"
    done = evs[-1]
    assert done["status"] == "complete" and "Hello from the router" in done["content"] and done["reasoning"] == "Let me think."
    assert done["tech"]["runtime"] == "llamacpp"
    th = lcapp.get(f"/api/chat/conversations/{cid}/thread").json()
    assert th["messages"][-1]["content"].strip() == "Hello from the router."  # the answer, not the reasoning, is what is stored


def test_chat_route_shows_the_memory_refusal_as_the_error(lcapp, st, spark):
    meminfo(spark.mem, 10 * GIB)
    cid = lcapp.post("/api/chat/conversations", json={"model_id": mid(st, "chat-big")}).json()["id"]
    code, evs = lcapp.stream(f"/api/chat/conversations/{cid}/generate",
                             {"mode": "send", "operation": "text", "content": "hello", "model_id": mid(st, "chat-big")})
    err = next(e for e in evs if e["type"] == "error")
    assert "Not enough free memory" in err["message"] and "GiB" in err["message"]
    assert evs[-1]["type"] == "done" and evs[-1]["status"] == "error"
    assert not [c for c in fr.CALLS if c[0] in ("load", "unload")]


def test_vision_attachment_through_the_app(lcapp, st):
    png = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
           b"\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82")
    m = mid(st, "chat-vision")
    cid = lcapp.post("/api/chat/conversations", json={"model_id": m}).json()["id"]
    up = lcapp.post(f"/api/chat/attachments?conversation_id={cid}", files={"file": ("p.png", png, "image/png")})
    if up.status_code != 200:
        up = lcapp.post("/api/chat/attachments", files={"file": ("p.png", png, "image/png")})
    assert up.status_code == 200, up.text
    fr.reset(loaded=["chat-vision"])
    code, evs = lcapp.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "operation": "text", "content": "what is this?",
                                                                       "model_id": m, "attachments": [up.json()["id"]]})
    assert "I see an image" in evs[-1]["content"]
    sent = [c[1] for c in fr.CALLS if c[0] == "chat"][-1]["messages"][-1]["content"]
    assert any(p.get("type") == "image_url" and p["image_url"]["url"].startswith("data:image/png;base64,") for p in sent)


def test_lifecycle_admin_load_and_unload_through_the_router(lcapp, st):
    m = mid(st, "chat-vision")
    s = lcapp.get(f"/api/admin/models/{m}/lifecycle").json()
    assert s["kind"] == "llm" and s["state"] == "idle" and s["load"]["allowed"] and s["unload"]["noop"] == "Not loaded."
    assert lcapp.post(f"/api/admin/models/{m}/load").status_code == 200
    time.sleep(1.6)
    assert lcapp.get(f"/api/admin/models/{m}/lifecycle").json()["state"] == "loaded"
    eng = lcapp.get("/api/engines").json()["engines"][0]
    assert eng["supports"]["load"] and not eng["supports"]["exclusive_chat_model"] and sorted(eng["status"]["llm"]["loaded"]) == ["chat-small", "chat-vision"]
    assert lcapp.post(f"/api/admin/models/{m}/unload").status_code == 200
    assert ("unload", {"model": "chat-vision"}) in fr.CALLS


def test_lifecycle_load_is_refused_for_lack_of_memory(lcapp, st, spark):
    meminfo(spark.mem, 20 * GIB)
    m = mid(st, "chat-big")
    r = lcapp.post(f"/api/admin/models/{m}/load")
    assert r.status_code == 409 and "Not enough free memory" in r.json()["detail"]["message"]
    assert not [c for c in fr.CALLS if c[0] == "load"]


def test_two_chats_on_two_models_run_together(lcapp, st, app):
    """A real server and two real concurrent streams: the router holds both models, neither is unloaded, both complete."""
    import threading
    fr.reset(loaded=["chat-small", "chat-vision"])
    convs = {r: (mid(st, r), lcapp.post("/api/chat/conversations", json={"model_id": mid(st, r)}).json()["id"]) for r in ("chat-small", "chat-vision")}
    srv = Server(app, _free_port())
    srv.start()
    out = {}

    def go(real):
        m, cid = convs[real]
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.port}", timeout=30) as c:
            assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW}).status_code == 200
            csrf = c.get("/api/me").json()["csrf"]
            with c.stream("POST", f"/api/chat/conversations/{cid}/generate", headers={"x-csrf-token": csrf},
                          json={"mode": "send", "operation": "text", "content": "hi", "model_id": m}) as r:
                out[real] = (r.status_code, [json.loads(x[6:]) for x in r.iter_lines() if x.startswith("data: ")])
    try:
        ts = [threading.Thread(target=go, args=(r,)) for r in convs]
        [t.start() for t in ts]
        [t.join(20) for t in ts]
    finally:
        srv.stop()
    assert all(v[0] == 200 and v[1][-1]["status"] == "complete" for v in out.values()), out
    chats = [c[1]["model"] for c in fr.CALLS if c[0] == "chat"]
    assert sorted(chats) == ["chat-small", "chat-vision"] and not [c for c in fr.CALLS if c[0] == "unload"]


def test_many_chats_starting_in_the_same_instant_never_hit_a_database_lock(lcapp, st, app):
    """Regression (found on the Spark by two people starting a chat together): a coroutine handler began as a reader and could not upgrade over
    another writer's commit, so the loser got HTTP 500 'database is locked'. All of them must now start and complete."""
    import threading
    fr.reset(loaded=["chat-small"])
    n = 8
    convs = [lcapp.post("/api/chat/conversations", json={"model_id": mid(st, "chat-small")}).json()["id"] for _ in range(n)]
    srv = Server(app, _free_port())
    srv.start()
    go_all = threading.Barrier(n)
    out = {}

    def go(i):
        with httpx.Client(base_url=f"http://127.0.0.1:{srv.port}", timeout=60) as c:
            assert c.post("/api/auth/login", json={"email": "owner@example.test", "password": PW}).status_code == 200
            csrf = c.get("/api/me").json()["csrf"]
            go_all.wait(15)
            with c.stream("POST", f"/api/chat/conversations/{convs[i]}/generate", headers={"x-csrf-token": csrf},
                          json={"mode": "send", "operation": "text", "content": f"hi {i}", "model_id": mid(st, "chat-small")}) as r:
                out[i] = (r.status_code, [json.loads(x[6:]) for x in r.iter_lines() if x.startswith("data: ")])
    try:
        ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
        [t.start() for t in ts]
        [t.join(60) for t in ts]
    finally:
        srv.stop()
    assert len(out) == n and all(v[0] == 200 for v in out.values()), {k: v[0] for k, v in out.items()}
    assert all(v[1][-1]["status"] == "complete" for v in out.values()), out


# ---- embeddings through the router ---------------------------------------------------------------------------------------------------
def test_embeddings_use_the_router_key_and_model_name(lcapp, st, router_url):
    root = router_url.removesuffix("/v1")
    with st.db.session(write=True) as s:
        st.settings.set(s, "operator", "embeddings.url", root)
        st.settings.set(s, "operator", "embeddings.model", "embed-test")
    with st.db.session() as s:
        cfg = embed_svc.config(st, s)
    assert cfg.router and cfg.api_key == fr.KEY and cfg.url == root and cfg.model == "embed-test"
    v = embed_svc.embed(st, cfg, ["hello", "world"])
    assert v.shape == (2, 1024)  # the dimension the stored vectors have
    assert embed_svc.healthy(st, cfg)
    n, lim = 0, embed_svc.slot_limit(st, cfg)
    assert lim == (4096, True)  # /props?model=embed-test, not the router's own n_ctx 0
    assert ("props", {"model": "embed-test"}) in fr.CALLS


def test_embeddings_without_the_router_key_are_refused(lcapp, st, router_url):
    cfg = embed_svc.EmbedConfig(router_url.removesuffix("/v1"), "embed-test", "", 0.47)  # no key: the router answers 401
    with pytest.raises(embed_svc.EmbedUnavailable):
        embed_svc.embed(st, cfg, ["x"])


def test_overlong_input_recovery_tokenizes_with_the_model_name(lcapp, st, router_url, monkeypatch):
    cfg = embed_svc.EmbedConfig(router_url.removesuffix("/v1"), "embed-test", "", 0.47, fr.KEY)
    run_ = embed_svc._Run(st, cfg, [""])
    assert run_.tokenize("some text") is not None  # the router's /tokenize insists on `model`
    assert ("tokenize", {"content": "some text", "with_pieces": True, "model": "embed-test"}) in fr.CALLS
