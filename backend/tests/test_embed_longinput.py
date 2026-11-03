"""embed_svc.embed() with inputs the model cannot take whole: a fake llama-server (over httpx.MockTransport) that answers
/props, /tokenize and /v1/embeddings and refuses, with HTTP 400, any request holding an input over its per-slot limit.

The real model and server are measured on the Spark (see the commit message); these tests pin the behaviour: ordinary
batches unchanged, a bad input isolated and recovered, bounded work, explicit attributed failure, consistent dimensions."""
import hashlib
import itertools
import json
import logging
import time
from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from semechka import embed_svc
from semechka.embed_svc import EmbedConfig, EmbedUnavailable

URL = "http://llama.test:8760"
DIMS = 24
CJK = "".join(chr(0x4E00 + (i * 7919) % 20000) for i in range(6000))
DIGITS = "".join(str(i % 10) for i in range(6000))


def tokens_of(text: str) -> list[bytes]:
    """The fake tokenizer. Digits: one token each. Other ASCII: one token per 4 characters. CJK: one token per character,
    but every third one is three byte-level tokens (so a window edge can fall inside a character, like the real model)."""
    out: list[bytes] = []
    ascii_run = b""
    for i, ch in enumerate(text):
        if ch.isdigit() and ord(ch) < 128:
            if ascii_run:
                out.append(ascii_run)
                ascii_run = b""
            out.append(ch.encode())
        elif ord(ch) < 128:
            ascii_run += ch.encode()
            if len(ascii_run) == 4:
                out.append(ascii_run)
                ascii_run = b""
        else:
            if ascii_run:
                out.append(ascii_run)
                ascii_run = b""
            b = ch.encode()
            out += [b[:1], b[1:2], b[2:]] if i % 3 == 0 and len(b) == 3 else [b]
    if ascii_run:
        out.append(ascii_run)
    return out


def vec_of(text: str, dims: int = DIMS) -> list[float]:
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")
    return np.random.RandomState(seed).normal(size=dims).tolist()


class FakeLlama:
    def __init__(self, n_ctx=4096, *, props=True, tokenize=True, poison=None, wrong_dims_for=None, down=False, hints=True):
        self.hints = hints
        self.n_ctx, self.props, self.tokenize, self.poison = n_ctx, props, tokenize, poison
        self.wrong_dims_for, self.down = wrong_dims_for, down
        self.log: list[tuple[str, list[str] | None]] = []   # (path, inputs) per request
        self.accepted: list[list[str]] = []                  # inputs of every accepted embeddings request

    def count(self, path: str) -> int:
        return sum(1 for p, _ in self.log if p == path)

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        body = json.loads(request.content) if request.content else {}
        self.log.append((path, body.get("input") if path == "/v1/embeddings" else None))
        if self.down:
            return httpx.Response(503, json={"error": "down"})
        if path == "/props":
            if not self.props:
                return httpx.Response(404)
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": self.n_ctx}, "total_slots": 2})
        if path == "/tokenize":
            if not self.tokenize:
                return httpx.Response(404)
            toks = tokens_of(body["content"])
            if body.get("with_pieces"):
                return httpx.Response(200, json={"tokens": [{"id": i, "piece": t.decode() if t.decode("utf-8", "ignore").encode() == t
                                                              else list(t)} for i, t in enumerate(toks)]})
            return httpx.Response(200, json={"tokens": list(range(len(toks)))})
        if path == "/v1/embeddings":
            texts = body["input"]
            for t in texts:
                n = len(tokens_of(t)) + 1  # the server appends an end-of-sequence token
                if n >= self.n_ctx:
                    err = {"code": 400, "type": "exceed_context_size_error", "message": f"request ({n} tokens) exceeds the context size"}
                    if self.hints:
                        err |= {"n_prompt_tokens": n, "n_ctx": self.n_ctx}
                    return httpx.Response(400, json={"error": err})
                if self.poison and self.poison in t:
                    return httpx.Response(400, json={"error": {"code": 400, "message": "invalid input"}})
            self.accepted.append(texts)
            data = []
            for i, t in enumerate(texts):
                dims = DIMS // 2 if self.wrong_dims_for and self.wrong_dims_for in t else DIMS
                data.append({"index": i, "embedding": vec_of(t, dims)})
            return httpx.Response(200, json={"object": "list", "model": body.get("model"), "data": data})
        return httpx.Response(404)


@pytest.fixture()
def llama(monkeypatch):
    def install(**kw) -> FakeLlama:
        fake = FakeLlama(**kw)
        client = httpx.Client(transport=httpx.MockTransport(fake.handler))
        monkeypatch.setattr(httpx, "post", lambda url, json=None, timeout=None: client.post(url, json=json))
        monkeypatch.setattr(httpx, "get", lambda url, timeout=None: client.get(url))
        return fake
    embed_svc.reset_stats()
    return install


@pytest.fixture()
def st():
    return SimpleNamespace(health_cache={})


def cfg(instruction=""):
    return EmbedConfig(URL, "m", instruction, 0.47)


def unit(v):
    v = np.asarray(v, dtype=np.float64)
    return v / np.linalg.norm(v)


def prose(i: int) -> str:
    return f"Paragraph {i}. " + "The quick brown fox jumps over the lazy dog. " * 20


def test_ordinary_batches_are_unchanged_and_cost_nothing_extra(llama, st):
    fake = llama()
    texts = [prose(i) for i in range(20)]
    out = embed_svc.embed(st, cfg(), texts)
    assert out.shape == (20, DIMS) and out.dtype == np.float32
    for i, t in enumerate(texts):
        np.testing.assert_allclose(out[i], unit(vec_of(t)), atol=1e-6)
    assert [p for p, _ in fake.log] == ["/v1/embeddings", "/v1/embeddings"]  # 16 + 4: no /props, no /tokenize
    assert [len(i) for _, i in fake.log] == [16, 4]
    assert fake.log[0][1] == texts[:16]  # the same inputs, in the same order
    assert embed_svc.STATS["batches_rejected"] == 0 and embed_svc.STATS["items_isolated"] == 0
    r = embed_svc.embed_with_report(st, cfg(), texts)
    assert r.items == [] and r.requests == 2 and r.batches_rejected == 0


def test_empty_input_makes_no_request(llama, st):
    fake = llama()
    assert embed_svc.embed(st, cfg(), []).shape == (0, 0)
    assert fake.log == []


def test_query_prefix_and_char_cut_are_as_before(llama, st):
    fake = llama()
    embed_svc.embed(st, cfg("Find it"), ["x" * 7000, "short"], query=True)
    sent = fake.log[0][1]
    assert sent[0].startswith("Instruct: Find it\nQuery: x") and len(sent[0]) == embed_svc.MAX_CHARS
    assert sent[1] == "Instruct: Find it\nQuery: short"


def test_one_overlong_numeric_and_one_cjk_input_do_not_lose_the_batch(llama, st, caplog):
    fake = llama()
    texts = [prose(i) for i in range(14)] + [DIGITS, CJK]
    with caplog.at_level(logging.INFO, logger="semechka.embed_svc"):
        res = embed_svc.embed_with_report(st, cfg(), texts)
    out = res.vectors
    assert out.shape == (16, DIMS)
    np.testing.assert_allclose(np.linalg.norm(out, axis=1), 1.0, atol=1e-5)
    for i in range(14):  # the good ones got exactly the vectors they would have had alone
        np.testing.assert_allclose(out[i], unit(vec_of(texts[i])), atol=1e-6)
    assert [(it.index, it.batch_pos, it.action) for it in res.items] == [(14, 14, "split"), (15, 15, "split")]
    assert res.batches_rejected == 1 and res.items[0].tokens == 6000 and res.items[1].tokens > 6000
    # the combined vector is the re-normalised mean of the unit vectors of the windows that were actually embedded
    for idx, src in ((14, DIGITS), (15, CJK)):
        wins = [t for batch in fake.accepted for t in batch if len(t) < len(src) and t in src and len(t) > 1000]
        assert 2 <= len(wins) <= embed_svc.MAX_PIECES
        np.testing.assert_allclose(out[idx], unit(np.mean([unit(vec_of(w)) for w in wins], axis=0)), atol=1e-6)
        assert all(len(tokens_of(w)) + 1 < 4096 - embed_svc.SAFETY_TOKENS + 8 for w in wins)  # +8: a window edge may widen a character
        assert src.startswith(wins[0]) and src.endswith(wins[-1])  # the windows cover the text from end to end ...
        assert all(src.find(b) <= src.find(a) + len(a) - 1 for a, b in itertools.pairwise(wins))  # ... overlapping
    assert fake.count("/props") == 1 and fake.count("/tokenize") == 2
    assert fake.count("/v1/embeddings") <= 1 + 16 + 2  # batch, each item once, one request of windows per bad item
    assert embed_svc.STATS["items_split"] == 2 and embed_svc.STATS["items_failed"] == 0
    # the log names the items by index, size, tokens and action - and never carries the text
    assert "input 14 (batch position 14, 6000 chars, 6000 tokens" in caplog.text and "input 15" in caplog.text
    assert "split" in caplog.text and CJK[:20] not in caplog.text and DIGITS[:40] not in caplog.text and "quick brown" not in caplog.text
    # the second call finds the limit in the cache: no /props again
    embed_svc.embed(st, cfg(), [DIGITS])
    assert fake.count("/props") == 1


def test_overlong_query_keeps_its_instruction_on_every_window(llama, st):
    fake = llama()
    out = embed_svc.embed(st, cfg("Find it"), [DIGITS], query=True)
    assert out.shape == (1, DIMS)
    wins = fake.accepted[-1]
    assert len(wins) >= 2 and all(w.startswith("Instruct: Find it\nQuery: ") for w in wins)


def test_unrecoverable_item_is_isolated_reported_and_fails_the_call_after_the_others_were_tried(llama, st, caplog):
    fake = llama(poison="POISON")
    texts = [prose(i) for i in range(5)] + ["POISON in range" * 3] + [prose(i) for i in range(6, 10)] + [DIGITS]
    with caplog.at_level(logging.INFO, logger="semechka.embed_svc"), pytest.raises(EmbedUnavailable) as e:
        embed_svc.embed(st, cfg(), texts)
    msg = str(e.value)
    assert "1 of 11 inputs" in msg and "input 5 (batch position 5" in msg and "10 were embedded" in msg and "none are returned" in msg
    assert "input 10" not in msg  # the overlong digits were recovered; only the poisoned one failed
    # every good input was attempted individually, the digits were split and embedded, the poisoned one only tried alone
    seen = {t for batch in fake.accepted for t in batch}
    assert {prose(i) for i in (0, 1, 2, 3, 4, 6, 7, 8, 9)} <= seen and any(len(t) > 1000 and t in DIGITS for t in seen)
    assert embed_svc.STATS["items_failed"] == 1 and "could not be embedded" in caplog.text


def test_window_still_refused_fails_that_item_only(llama, st):
    fake = llama(poison="9012")  # every window of the digits contains this run, so no window is accepted
    with pytest.raises(EmbedUnavailable) as e:
        embed_svc.embed(st, cfg(), [prose(1), DIGITS, prose(2)])
    assert "1 of 3 inputs" in str(e.value) and "input 1 " in str(e.value) and "still refused" in str(e.value)
    assert fake.count("/v1/embeddings") <= 1 + 3 + 1 + embed_svc.MAX_PIECES


def test_input_within_the_limit_that_the_server_refuses_is_not_split(llama, st):
    fake = llama(poison="POISON")
    with pytest.raises(EmbedUnavailable) as e:
        embed_svc.embed(st, cfg(), ["POISON"])
    assert "although it fits the limit" in str(e.value)
    assert fake.count("/v1/embeddings") == 1  # a single input is not retried as itself


def test_retry_bounds(llama, st):
    # 70 inputs the server refuses for a reason other than size: work stops at MAX_REJECTED_ITEMS, it does not run on
    fake = llama(poison="POISON")
    with pytest.raises(EmbedUnavailable) as e:
        embed_svc.embed(st, cfg(), [f"POISON {i}" for i in range(70)])
    assert "too many inputs" in str(e.value)
    assert fake.count("/v1/embeddings") <= 5 + 70  # 5 batches + one retry per input; never more
    assert fake.count("/tokenize") <= embed_svc.MAX_REJECTED_ITEMS
    # a batch of 16 overlong inputs: batch + 16 singles + 16 window requests, nothing more
    fake = llama()
    out = embed_svc.embed(st, cfg(), [DIGITS[i:] for i in range(16)])
    assert out.shape == (16, DIMS)
    assert fake.count("/v1/embeddings") == 1 + 16 + 16 and fake.count("/tokenize") == 16 and fake.count("/props") == 0  # (the limit is cached from above)


def test_bounded_truncation_is_explicit(llama, st, monkeypatch, caplog):
    monkeypatch.setattr(embed_svc, "MAX_CHARS", 60000)
    fake = llama(n_ctx=512)
    text = "".join(str(i % 10) for i in range(20000))
    with caplog.at_level(logging.WARNING, logger="semechka.embed_svc"):
        res = embed_svc.embed_with_report(st, cfg(), [text])
    it = res.items[0]
    assert it.action == "truncated" and it.pieces == embed_svc.MAX_PIECES and it.tokens == 20000
    assert "TRUNCATED" in caplog.text and "were NOT embedded" in caplog.text and embed_svc.STATS["items_truncated"] == 1
    assert len(fake.accepted[-1]) == embed_svc.MAX_PIECES
    assert np.isclose(np.linalg.norm(res.vectors[0]), 1.0, atol=1e-5)


def test_limit_discovery_fallbacks_and_cache(llama, st):
    fake = llama(props=False)  # no /props, but the refusal itself names the server's n_ctx: that is used
    assert embed_svc.embed(st, cfg(), [DIGITS]).shape == (1, DIMS)
    assert max(len(w) for w in fake.accepted[-1]) == 4096 - embed_svc.SAFETY_TOKENS
    st.health_cache.clear()
    fake = llama(props=False, hints=False)  # nothing says: assume the conservative default
    out = embed_svc.embed(st, cfg(), [DIGITS])
    assert out.shape == (1, DIMS)
    assert max(len(w) for w in fake.accepted[-1]) == embed_svc.DEFAULT_SLOT_TOKENS - embed_svc.SAFETY_TOKENS
    embed_svc.embed(st, cfg(), [DIGITS])
    assert fake.count("/props") == 1  # the failure is remembered briefly
    st.health_cache["embedlimit:" + URL] = (time.monotonic() - embed_svc.LIMIT_FAIL_TTL - 1, {"n_ctx": None})
    embed_svc.embed(st, cfg(), [DIGITS])
    assert fake.count("/props") == 2
    # a discovered limit is trusted for LIMIT_TTL, then read again
    st.health_cache.clear()
    fake = llama()
    embed_svc.embed(st, cfg(), [DIGITS])
    assert st.health_cache["embedlimit:" + URL][1]["n_ctx"] == 4096
    old = st.health_cache["embedlimit:" + URL]
    st.health_cache["embedlimit:" + URL] = (old[0] - embed_svc.LIMIT_TTL - 1, old[1])
    embed_svc.embed(st, cfg(), [DIGITS])
    assert fake.count("/props") == 2


def test_the_limit_comes_from_the_server_not_a_constant(llama, st):
    fake = llama(n_ctx=1024)
    out = embed_svc.embed(st, cfg(), [prose(1) * 1, "9" * 1500])
    assert out.shape == (2, DIMS)
    assert all(len(tokens_of(w)) + 1 < 1024 for b in fake.accepted for w in b)


def test_without_tokenize_the_servers_own_count_in_the_refusal_is_used(llama, st):
    fake = llama(tokenize=False)
    out = embed_svc.embed(st, cfg(), [CJK, prose(1)])
    assert out.shape == (2, DIMS)
    assert fake.count("/tokenize") >= 1
    assert all(len(tokens_of(w)) + 1 < 4096 for b in fake.accepted for w in b)


def test_dimension_mismatch_is_an_error_not_a_ragged_array(llama, st):
    llama(wrong_dims_for="ODD")
    with pytest.raises(EmbedUnavailable, match="different sizes"):
        embed_svc.embed(st, cfg(), [prose(1), "ODD " + prose(2)])
    llama(wrong_dims_for="9012")  # a window answered with a different size than the rest
    with pytest.raises(EmbedUnavailable, match="different sizes"):
        embed_svc.embed(st, cfg(), [prose(1), DIGITS])


def test_unusable_vectors_are_still_refused(llama, st, monkeypatch):
    llama()
    monkeypatch.setitem(globals(), "vec_of", lambda text, dims=DIMS: [0.0] * dims)
    with pytest.raises(EmbedUnavailable, match="unusable"):
        embed_svc.embed(st, cfg(), [prose(1)])
    with pytest.raises(EmbedUnavailable, match="unusable"):
        embed_svc.embed(st, cfg(), [DIGITS])


def test_a_service_that_is_down_fails_at_once_without_recovery(llama, st):
    fake = llama(down=True)
    st.health_cache["embed:" + URL] = (time.monotonic(), {"ok": True})
    with pytest.raises(EmbedUnavailable, match="did not answer"):
        embed_svc.embed(st, cfg(), [prose(i) for i in range(20)])
    assert fake.count("/v1/embeddings") == 1 and fake.count("/props") == 0
    assert "embed:" + URL not in st.health_cache  # as before: the cached health verdict is dropped
