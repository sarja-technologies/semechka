"""Semantic retrieval plumbing: permissions, staleness, failure, encryption - with a deterministic fake embedder."""
import pytest
from sqlalchemy import select

from semechka import contexts_svc as C
from semechka import embed_svc, jobs_svc
from semechka import files_svc as F
from semechka import tenancy_svc as T
from semechka.models_files import Chunk, Job

from . import fake_embed
from .conftest import Server, _free_port
from .test_files_contexts import World, make_ctx, s, st, w  # noqa: F401  (fixtures and helpers)


@pytest.fixture(scope="module")
def embed_url():
    srv = Server(fake_embed.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


@pytest.fixture()
def emb(embed_url, st, s):  # noqa: F811
    fake_embed.STATE.update(down=False, calls=0)
    st.settings.set(s, "test", "embeddings.url", embed_url)
    st.settings.set(s, "test", "embeddings.model", "fake-embed-1")
    st.settings.set(s, "test", "embeddings.min_score", 0.5)
    s.commit()
    st.health_cache.clear()
    return st


def prep(w, st):  # noqa: F811
    a = w.upload("alice", "travel.txt", b"Taxi rides over fifty dollars need director approval.")
    b = w.upload("alice", "finance.txt", b"Quarterly revenue grew by twelve percent.")
    c = make_ctx(w, [a, b])
    w.ingest()
    return a, b, c


def test_semantic_finds_what_keywords_cannot_and_says_so(w, st, emb):  # noqa: F811
    _, _, c = prep(w, st)
    assert {j.kind for j in w.s.execute(select(Job)).scalars()} == {"ingest", "embed"}
    r = C.retrieve(st, w.s, w.az("bob"), "cab permission", context_ids=[c.id])
    assert r.mode == "hybrid" and r.hits[0].file_name == "travel.txt"  # no shared word with the passage
    assert r.hits[0].mode == "semantic" and r.hits[0].cosine >= 0.5 and r.hits[0].bm25 is None
    r = C.retrieve(st, w.s, w.az("bob"), "taxi approval", context_ids=[c.id])
    assert r.hits[0].mode == "hybrid" and r.hits[0].bm25 is not None and r.hits[0].cosine is not None
    r = C.retrieve(st, w.s, w.az("bob"), "banana bread walnut", context_ids=[c.id])
    assert r.hits == []  # unrelated: the similarity floor keeps junk out of the prompt


def test_without_a_service_it_is_keyword_only_and_honest(w, st, s):  # noqa: F811
    _, _, c = prep(w, st)
    r = C.retrieve(st, w.s, w.az("bob"), "cab permission", context_ids=[c.id])
    assert r.mode == "keyword" and r.hits == []
    assert C.retrieve(st, w.s, w.az("bob"), "taxi approval", context_ids=[c.id]).hits[0].mode == "keyword"


def test_service_down_at_query_time_falls_back_with_a_note(w, st, emb):  # noqa: F811
    _, _, c = prep(w, st)
    fake_embed.STATE["down"] = True
    r = C.retrieve(st, w.s, w.az("bob"), "taxi approval", context_ids=[c.id])
    assert r.mode == "keyword" and r.hits and any("unavailable" in n for n in r.notes)


def test_service_down_at_ingest_then_backfill_recovers(w, st, emb):  # noqa: F811
    fake_embed.STATE["down"] = True
    st.health_cache.clear()
    a = w.upload("alice", "travel.txt", b"Taxi rides over fifty dollars need director approval.")
    c = make_ctx(w, [a])
    w.ingest()
    j = w.s.execute(select(Job).where(Job.kind == "embed")).scalar_one()
    assert j.status == "failed" and "embedding service" in j.error  # visible, not silent
    assert embed_svc.backfill(st) == 0  # still down: nothing queued
    fake_embed.STATE["down"] = False
    st.health_cache.clear()
    assert embed_svc.backfill(st) == 1
    w.ingest()
    assert C.retrieve(st, w.s, w.az("bob"), "cab permission", context_ids=[c.id]).mode == "hybrid"
    assert embed_svc.backfill(st) == 0  # nothing left to do, and no duplicate jobs


def test_pending_embeddings_are_reported_and_a_model_change_makes_vectors_stale(w, st, emb):  # noqa: F811
    a = w.upload("alice", "travel.txt", b"Taxi rides over fifty dollars need director approval.")
    c = make_ctx(w, [a])
    w.s.commit()
    jobs_svc.run_pending(st, kinds=["ingest"])  # ingested, but the embed job has not run yet
    w.s.expire_all()
    r = C.retrieve(st, w.s, w.az("alice"), "taxi approval", context_ids=[c.id])
    assert r.mode == "keyword" and any("still being embedded" in n for n in r.notes)
    w.ingest()
    assert C.retrieve(st, w.s, w.az("alice"), "cab permission", context_ids=[c.id]).mode == "hybrid"  # warm cache is not reused
    st.settings.set(w.s, "test", "embeddings.model", "fake-embed-2")
    w.s.commit()
    r = C.retrieve(st, w.s, w.az("alice"), "cab permission", context_ids=[c.id])
    assert r.mode == "keyword" and r.hits == []  # old vectors are ignored, never mixed with the new model
    embed_svc.backfill(st)
    w.ingest()
    assert C.retrieve(st, w.s, w.az("alice"), "cab permission", context_ids=[c.id]).mode == "hybrid"


def test_semantic_search_respects_permissions_and_the_cache_does_not_leak(w, st, emb):  # noqa: F811
    secret = w.upload("alice", "payroll.txt", b"Executive pay and compensation: the wage band is confidential.")
    open_ = w.upload("alice", "cafe.txt", b"The cafeteria opens at eight.")
    c = make_ctx(w, [secret, open_])
    w.ingest()
    F.rename_or_move(w.s, w.az("alice"), secret.id, restricted=True)
    assert C.retrieve(st, w.s, w.az("alice"), "salary", context_ids=[c.id]).hits[0].file_name == "payroll.txt"  # warms the cache
    r = C.retrieve(st, w.s, w.az("bob"), "salary", context_ids=[c.id])
    assert r.hits == [] and r.searched_files == 1
    for who in ("carol", "gina", "owner"):
        assert C.retrieve(st, w.s, w.az(who), "salary", context_ids=[c.id]).hits == [], who
    T.remove_workspace_member(w.s, w.az("alice"), w.fin.id, "user", w.u["bob"].id)
    assert C.retrieve(st, w.s, w.az("bob"), "cafeteria", context_ids=[c.id]).hits == []


def test_vectors_are_encrypted_per_organisation(w, st, emb):  # noqa: F811
    a, _, _ = prep(w, st)
    ch = w.s.execute(select(Chunk).where(Chunk.file_id == a.id)).scalars().first()
    assert ch.embedding[:4] == b"SMA1" and ch.embed_model == "fake-embed-1"
    assert embed_svc.unseal(st, a.org_id, ch.id, ch.embedding).shape == (fake_embed.DIMS,)
    with pytest.raises(Exception):  # noqa: B017  another organisation's key cannot open it
        embed_svc.unseal(st, w.globex.id, ch.id, ch.embedding)
    with pytest.raises(Exception):  # noqa: B017  nor can it be replayed against another passage
        embed_svc.unseal(st, a.org_id, ch.id + 1, ch.embedding)
