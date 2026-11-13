"""Yaob Origin in Semechka (contract yaob-origin/1): runtime identity, web search mode gating, status events, cited
sources (validated, encrypted, reloaded, exported) and the provenance id. Other servers must behave exactly as before."""
import json

import pytest
from sqlalchemy import select

from semechka.adapters import OpenAICompatAdapter, yaob
from semechka.models import Message, Model
from semechka.models_knowledge import MessageSources
from semechka.routers.chat import merge_sources

from . import fake_yaob, fakebackend
from .conftest import Server, _free_port


@pytest.fixture(scope="session")
def yaob_url():
    srv = Server(fake_yaob.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}/v1"
    srv.stop()


@pytest.fixture
def origin(owner, yaob_url):
    """The owner's install with a Yaob Origin backend added, synced, and its legacy id enabled for owners."""
    fake_yaob.reset()
    b = owner.post("/api/admin/backends", json={"name": "Yaob C4B + MPN-14B 0.2.1", "adapter": "openai_compat",
                                                "base_url": yaob_url, "external": False})
    assert b.status_code == 200, b.text
    bid = b.json()["id"]
    assert owner.post(f"/api/admin/backends/{bid}/sync").status_code == 200
    rows = owner.get("/api/admin/models", params={"backend": bid}).json()["items"]
    m = next(r for r in rows if r["real_id"] == "yaob-c4b-mpn14b")
    assert owner.patch(f"/api/admin/models/{m['id']}", json={"enabled": True}).status_code == 200
    owner.put(f"/api/admin/models/{m['id']}/grants", json={"grants": [{"subject_type": "role", "subject_id": "owner", "effect": "allow"}]})
    owner.backend_id, owner.yaob_model = bid, m["id"]
    owner.plain_model = next(x["id"] for x in owner.get("/api/chat/models").json() if x["id"] != m["id"])
    return owner


def gen(api, cid, text, model_id, **kw):
    code, ev = api.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": text, "model_id": model_id, **kw})
    assert code == 200, ev
    return ev


# ---- validation of what the server sends ------------------------------------------------------------------------
def test_sources_keep_only_http_links_and_bound_every_field():
    raw = [{"n": 1, "url": "javascript:alert(1)", "title": "x"},
           {"n": 2, "url": "data:text/html,<b>x</b>"},
           {"n": 3, "url": "ftp://example.org/file"},
           {"n": 4, "url": "https://user:pw@example.org/"},
           {"n": 5, "url": "https://exa mple.org/"},
           {"n": 6},
           "not a dict",
           {"n": 7, "url": "https://www.example.org/a?b=1", "title": "T‮\x07itle " + "y" * 400, "published": "<script>",
            "snippet": "s" * 2000, "kind": "evil"},
           {"n": 7, "url": "https://example.org/duplicate-number"},
           {"n": "8", "url": "http://example.com/", "site": "Example", "published": "2026-11-12", "kind": "reference"}]
    out = yaob.sources(raw)
    assert [s["n"] for s in out] == [7, 10]  # a non-numeric n takes the position in the list
    a, b = out
    assert a["url"] == "https://www.example.org/a?b=1" and a["site"] == "example.org" and a["kind"] == "web"
    assert len(a["title"]) == 300 and "‮" not in a["title"] and "\x07" not in a["title"]
    assert "published" not in a and len(a["snippet"]) == 500
    assert b == {"n": 10, "kind": "reference", "url": "http://example.com/", "title": "Example", "site": "Example", "published": "2026-11-12"}
    assert len(yaob.sources([{"n": i, "url": f"https://e.org/{i}"} for i in range(1, 40)])) == yaob.MAX_SOURCES
    assert yaob.sources("nope") == [] and yaob.sources(None) == []


def test_status_and_provenance_are_checked():
    assert yaob.status({"state": "searching_web", "query": "  weather \n Berlin ", "count": 3}) == \
        {"type": "status", "state": "searching_web", "query": "weather Berlin"}
    assert yaob.status({"state": "reading_sources", "count": 3}) == {"type": "status", "state": "reading_sources", "count": 3}
    assert yaob.status({"state": "reading_sources", "count": "lots"}) == {"type": "status", "state": "reading_sources"}
    assert yaob.status({"state": "loading"}) is None and yaob.status("searching_web") is None
    assert yaob.stream_events({"provenance": {"id": "req_1"}}) == [{"type": "provenance", "id": "req_1"}]
    assert yaob.stream_events({"provenance": {"id": "<script>"}}) == []
    assert yaob.stream_events({"route": {"x": 1}, "validation": {}, "timing": {}}) == []  # debugging chunks stay server-side
    assert yaob.stream_events("garbage") == [] and yaob.stream_events(None) == []


def test_identity_keeps_core_parameters_and_network_nodes_apart():
    meta = yaob.discovered_meta({"id": "yaob-origin", "name": "Yaob Origin N119M", "yaob": fake_yaob.identity()})
    ident = meta["identity"]
    assert meta["display_name"] == "Yaob Origin N119M" and meta["capabilities"] == ["mpn", "procedures", "web_search", "vision"]
    assert ident["core"]["parameters"] == 4659865088 and ident["mpn"]["nodes"] == 119176878
    assert "parameters" not in ident["mpn"] and "nodes" not in ident["core"]
    assert yaob.core_label(meta) == "Qwen3.5-4B" and yaob.network_label(meta) == "Yaob Origin N119M"
    # no name, no identity: nothing is added (llama.cpp, vLLM and the rest are stored as before)
    assert yaob.discovered_meta({"id": "x", "object": "model", "owned_by": "y", "meta": {"n_ctx": 4}}) == {}
    assert yaob.discovered_meta({"id": "x", "name": "x"}) == {}
    assert yaob.discovered_meta({"id": "x", "yaob": "bad"}) == {}


# ---- adapter against a real HTTP stream ------------------------------------------------------------------------
async def test_adapter_discovers_runtime_identity(yaob_url):
    found = {d.real_id: d for d in await OpenAICompatAdapter(yaob_url).discover()}
    d = found["yaob-c4b-mpn14b"]
    assert d.meta["display_name"] == "Yaob Origin N119M" and d.meta["owned_by"] == "fylume"
    assert "web_search" in d.meta["capabilities"] and d.meta["identity"]["mpn"]["designation"] == "N119M"
    assert d.capabilities == ["chat"]  # access decisions are untouched by the identity record


async def test_adapter_streams_status_sources_and_provenance(yaob_url):
    fake_yaob.reset()
    ad = OpenAICompatAdapter(yaob_url)
    evs = [e async for e in ad.chat_stream("yaob-origin", [{"role": "user", "content": "latest news"}], {"web_search": "on"})]
    assert fake_yaob.REQUESTS[-1]["web_search"] == "on"
    kinds = [e["type"] for e in evs]
    assert kinds[:3] == ["status", "status", "sources"] and kinds[-1] == "done"
    assert evs[0] == {"type": "status", "state": "searching_web", "query": "Semechka 0.12 release"}
    assert evs[1] == {"type": "status", "state": "reading_sources", "count": 3}
    assert [s["n"] for s in evs[2]["sources"]] == [1, 2, 3] and evs[2]["sources"][1]["site"] == "example.com"
    assert "[1]" in "".join(e.get("content", "") for e in evs if e["type"] == "delta")
    assert evs[-1]["provenance"] == "req_4f2a9c" and evs[-1]["usage"]["completion_tokens"] == 70


async def test_adapter_ignores_malformed_yaob_chunks(yaob_url):
    fake_yaob.reset()
    fake_yaob.SCRIPT.append([
        "{not json", "[1, 2]", "null",
        {"choices": [], "yaob": "a string"},
        {"choices": [], "yaob": {"status": "searching"}},
        {"choices": [], "yaob": {"status": {"state": "rm -rf"}}},
        {"choices": [], "yaob": {"sources": "nope"}},
        {"choices": [], "yaob": {"sources": [{"n": 1, "url": "javascript:alert(1)"}]}},
        {"choices": [], "yaob": {"provenance": {"id": 5}}},
        {"choices": [{"delta": {"content": "still here"}}]},
        {"choices": [{"delta": {}, "finish_reason": "stop"}]}])
    evs = [e async for e in OpenAICompatAdapter(yaob_url).chat_stream("yaob-origin", [{"role": "user", "content": "x"}], {})]
    assert "web_search" not in fake_yaob.REQUESTS[-1]  # not set by the caller: not sent
    assert evs == [{"type": "sources", "sources": []}, {"type": "delta", "content": "still here", "reasoning": ""},
                   {"type": "done", "finish_reason": "stop", "usage": None, "timings": None}]


async def test_other_openai_servers_are_unchanged(fake_url):
    found = {d.real_id: d for d in await OpenAICompatAdapter(fake_url).discover()}
    assert found["fake-small"].meta == {"context": 4096, "context_trained": 4096, "params": 500_000_000,
                                        "size_bytes": 400_000_000, "owned_by": "test"}
    evs = [e async for e in OpenAICompatAdapter(fake_url).chat_stream("fake-small", [{"role": "user", "content": "hi"}], {"temperature": 0.2})]
    body = fakebackend.calls[-1]
    assert "web_search" not in body and body["temperature"] == 0.2
    assert [e["type"] for e in evs].count("delta") == 5 and evs[-1] == {
        "type": "done", "finish_reason": "stop", "usage": {"prompt_tokens": 7, "completion_tokens": 5},
        "timings": {"prompt_per_second": 900.0, "predicted_per_second": 123.4}}


# ---- catalogue: runtime name, web search flag, core and network ---------------------------------------------
def test_picker_label_comes_from_the_runtime_name_and_follows_it(origin, st):
    # what deploy/spark/semechka_register_origin.py does to the existing row: alias = the runtime display name
    with st.db.session(write=True) as s:
        s.get(Model, origin.yaob_model).alias = s.get(Model, origin.yaob_model).meta["display_name"]
    cat = origin.get("/api/models/catalog", params={"operation": "text"}).json()
    row = next(m for m in cat["models"] if m["id"] == origin.yaob_model)
    assert row["label"] == "Yaob Origin N119M" and row["web_search"] is True
    assert row["details"]["core"] == "Qwen3.5-4B" and row["details"]["network"] == "Yaob Origin N119M"
    assert "parameters" not in row["details"]  # 119 M nodes are not parameters, and no combined size is invented
    plain = next(m for m in cat["models"] if m["id"] == origin.plain_model)
    assert "web_search" not in plain and "core" not in plain["details"] and "network" not in plain["details"]
    # the node network grows: an alias that was the runtime name follows it ...
    fake_yaob.NODES["n"] = 120_400_000
    assert origin.post(f"/api/admin/backends/{origin.backend_id}/sync").status_code == 200
    with st.db.session() as s:
        assert s.get(Model, origin.yaob_model).alias == "Yaob Origin N120M"
    # ... while a name an administrator chose is kept
    origin.patch(f"/api/admin/catalog/models/{origin.yaob_model}", json={"alias": "Our research assistant"})
    fake_yaob.NODES["n"] = 121_000_000
    origin.post(f"/api/admin/backends/{origin.backend_id}/sync")
    with st.db.session() as s:
        assert s.get(Model, origin.yaob_model).alias == "Our research assistant"


def test_a_new_row_never_repeats_a_name_already_shown(origin, st):
    """The fake lists yaob-origin first, so it takes the runtime name; the legacy row added next keeps its identifier."""
    with st.db.session() as s:
        rows = {m.real_id: m.alias for m in s.execute(select(Model).where(Model.backend_id == origin.backend_id)).scalars()}
    assert rows == {"yaob-origin": "Yaob Origin N119M", "yaob-c4b-mpn14b": "yaob-c4b-mpn14b"}


# ---- chat: mode gating, events, persistence ------------------------------------------------------------------------
def test_web_search_defaults_to_auto_and_follows_the_persons_choice(origin):
    cid = origin.post("/api/chat/conversations", json={}).json()["id"]
    gen(origin, cid, "hello", origin.yaob_model)
    assert fake_yaob.REQUESTS[-1]["web_search"] == "auto"
    gen(origin, cid, "latest news please", origin.yaob_model, web_search="off")
    assert fake_yaob.REQUESTS[-1]["web_search"] == "off"
    ev = gen(origin, cid, "anything", origin.yaob_model, web_search="on")
    assert fake_yaob.REQUESTS[-1]["web_search"] == "on" and any(e["type"] == "activity" for e in ev)
    code, err = origin.stream(f"/api/chat/conversations/{cid}/generate",
                              {"mode": "send", "content": "x", "model_id": origin.yaob_model, "web_search": "always"})
    assert code == 422


def test_models_without_the_capability_never_receive_web_search(origin):
    cid = origin.post("/api/chat/conversations", json={}).json()["id"]
    n = len(fakebackend.calls)
    ev = gen(origin, cid, "hello", origin.plain_model, web_search="on")
    assert len(fakebackend.calls) == n + 1 and "web_search" not in fakebackend.calls[-1]
    assert not any(e["type"] == "activity" for e in ev)
    done = next(e for e in ev if e["type"] == "done")
    assert done["sources"] == [] and "web_search" not in done["message"]["meta"]
    with origin.c.app.state.sem.db.session() as s:
        m = s.get(Message, done["assistant_id"])
        assert m.meta is None and s.get(MessageSources, m.id) is None  # stored exactly as before


def test_status_sources_and_provenance_reach_the_browser_and_are_stored(origin, st):
    cid = origin.post("/api/chat/conversations", json={}).json()["id"]
    ev = gen(origin, cid, "What changed in the latest Semechka release?", origin.yaob_model)
    act = [e for e in ev if e["type"] == "activity"]
    assert act == [{"type": "activity", "state": "searching_web", "query": "Semechka 0.12 release"},
                   {"type": "activity", "state": "reading_sources", "count": 3}]
    assert not any(e["type"] == "engine" for e in ev)  # not mistaken for a model cold start
    src = next(e for e in ev if e["type"] == "sources")["sources"]
    assert [(s["n"], s["kind"]) for s in src] == [(1, "web"), (2, "web"), (3, "web")]
    done = next(e for e in ev if e["type"] == "done")
    assert done["sources"] == src and done["message"]["sources"] == src
    assert done["tech"]["provenance_id"] == "req_4f2a9c"  # the owner may see trace ids
    assert done["tech"]["core"] == "Qwen3.5-4B" and done["tech"]["network"] == "Yaob Origin N119M"
    with st.db.session() as s:
        m = s.get(Message, done["assistant_id"])
        assert m.meta == {"provenance": "req_4f2a9c", "web_search": "auto"}
        row = s.get(MessageSources, m.id)
        assert "example.org" not in row.sources_enc and "Semechka 0.12" not in row.sources_enc  # encrypted at rest
    # a reload (and another device) shows the same sources
    thread = origin.get(f"/api/chat/conversations/{cid}/thread").json()
    asst = [m for m in thread["messages"] if m["role"] == "assistant"][-1]
    assert asst["sources"] == src and "provenance" not in asst["meta"]
    full = origin.get(f"/api/chat/conversations/{cid}").json()
    assert [m for m in full["messages"] if m["role"] == "assistant"][-1]["sources"] == src
    # an exported conversation carries its sources as links
    md = origin.get(f"/api/chat/conversations/{cid}/export", params={"format": "md"}).text
    assert "Sources:" in md and "1. [Semechka 0.12 release notes](<https://example.org/semechka/0.12>) (2026-11-12)" in md
    js = origin.get(f"/api/chat/conversations/{cid}/export", params={"format": "json"}).json()
    assert [m for m in js["messages"] if m["role"] == "assistant"][-1]["sources"] == src


def test_trace_ids_and_model_details_follow_technical_visibility(origin, app):
    from .conftest import make_user
    owner = origin
    owner.put(f"/api/admin/models/{owner.yaob_model}/grants", json={"grants": [
        {"subject_type": "role", "subject_id": r, "effect": "allow"} for r in ("owner", "member")]})
    m = make_user(owner, app, "member@example.test")
    cid = m.post("/api/chat/conversations", json={}).json()["id"]
    ev = gen(m, cid, "latest release?", owner.yaob_model)
    done = next(e for e in ev if e["type"] == "done")
    assert "provenance_id" not in done["tech"]
    assert len(done["sources"]) == 3  # citations are part of the answer, not technical detail
    row = next(x for x in m.get("/api/models/catalog", params={"operation": "text"}).json()["models"] if x["id"] == owner.yaob_model)
    assert row["web_search"] is True and "core" not in row["details"]  # model details need technical visibility


def test_no_save_conversation_uses_the_mode_and_stores_nothing(origin, st):
    with st.db.session() as s:
        before = len(s.execute(select(MessageSources)).scalars().all())
    code, ev = origin.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "latest news"}],
                                                             "model_id": origin.yaob_model, "web_search": "on"})
    assert code == 200 and fake_yaob.REQUESTS[-1]["web_search"] == "on"
    assert len(next(e for e in ev if e["type"] == "done")["sources"]) == 3
    with st.db.session() as s:
        assert len(s.execute(select(MessageSources)).scalars().all()) == before


def test_knowledge_and_web_sources_are_kept_together():
    kn = [{"n": 1, "file": "ops.md", "chunk": 0, "excerpt": "x"}]
    web = yaob.sources(fake_yaob.SOURCES[:2])
    out = merge_sources(kn, web)
    assert out[0] == kn[0] and [(s["n"], s["kind"]) for s in out[1:]] == [(1, "web"), (2, "web")]
    assert merge_sources([], []) == [] and merge_sources(kn, []) == kn


def test_web_search_preference(origin):
    assert origin.patch("/api/me", json={"web_search": "off"}).status_code == 200
    assert origin.get("/api/me").json()["user"]["prefs"]["web_search"] == "off"
    assert origin.patch("/api/me", json={"web_search": "sometimes"}).status_code == 422
    assert origin.patch("/api/me", json={"web_search": None}).status_code == 200
    assert "web_search" not in origin.get("/api/me").json()["user"]["prefs"]


def test_sources_are_stored_with_a_reply_cut_short(origin, st):
    """Sources that arrived before a reply ended early (here: the token limit) are stored with it."""
    fake_yaob.SCRIPT.append([{"choices": [], "yaob": {"sources": fake_yaob.SOURCES[:1]}},
                             {"choices": [{"delta": {"content": "Partial [1]"}}]},
                             {"choices": [{"delta": {}, "finish_reason": "length"}]}])
    cid = origin.post("/api/chat/conversations", json={}).json()["id"]
    ev = gen(origin, cid, "q", origin.yaob_model)
    done = next(e for e in ev if e["type"] == "done")
    assert [s["n"] for s in done["message"]["sources"]] == [1]
    with st.db.session() as s:
        assert json.loads(st.vault.decrypt(s.get(MessageSources, done["assistant_id"]).sources_enc, "sources"))[0]["url"] \
            == "https://example.org/semechka/0.12"
