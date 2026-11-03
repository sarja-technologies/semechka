"""Embeddings for semantic retrieval, from a separately run OpenAI-compatible endpoint (settings `embeddings.*`).

Nothing here pretends: with no endpoint configured, or the endpoint down, retrieval reports mode "keyword". Vectors are
unit-length float32, stored encrypted with the organisation's key and tagged with the model that produced them, so a
model change makes old vectors stale (they are ignored, never mixed) until the backfill recomputes them.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
import numpy as np
from sqlalchemy import select

from . import authz, files_svc, jobs_svc
from .models_files import Chunk, File, FileVersion
from .models_tenancy import Workspace

log = logging.getLogger(__name__)
BATCH = 16
MAX_CHARS = 6000
HEALTH_TTL = 20.0
# Long-input recovery (see embed()):
INPUT_REJECTED = frozenset({400, 413, 422})  # statuses that mean "this input", not "the service is down"
DEFAULT_SLOT_TOKENS = 2048   # per-input token limit assumed when the server will not tell (smaller than any live setting)
SAFETY_TOKENS = 16           # kept free in every window: the server adds an end-of-sequence token, tokenizers differ slightly
OVERLAP_TOKENS = 64          # shared by neighbouring windows so no sentence is cut without context
MIN_WINDOW_TOKENS = 128      # below this the limit is not a real context size; do not try to split
MAX_PIECES = 8               # windows per input; beyond that the tail is dropped, loudly (ItemReport.action == "truncated")
MAX_REJECTED_ITEMS = 64      # refused inputs one embed() call will work on before it gives up
LIMIT_TTL = 600.0            # how long a discovered limit is trusted (a restart with other flags is noticed within this)
LIMIT_FAIL_TTL = 60.0        # how long a failed discovery is remembered


class EmbedUnavailable(Exception):
    pass


@dataclass
class EmbedConfig:
    url: str
    model: str
    instruction: str
    min_score: float


def config(st, s) -> EmbedConfig | None:
    url = (st.settings.deployment(s, "embeddings.url") or "").strip().rstrip("/")
    model = (st.settings.deployment(s, "embeddings.model") or "").strip()
    if not url or not model:
        return None
    return EmbedConfig(url, model, (st.settings.deployment(s, "embeddings.query_instruction") or "").strip(),
                       float(st.settings.deployment(s, "embeddings.min_score") or 0.47))


def healthy(st, cfg: EmbedConfig) -> bool:
    cache = st.health_cache
    hit = cache.get("embed:" + cfg.url)
    if hit and time.monotonic() - hit[0] < HEALTH_TTL:
        return hit[1]["ok"]
    try:
        ok = httpx.get(cfg.url + "/health", timeout=3).status_code == 200
    except httpx.HTTPError:
        ok = False
    cache["embed:" + cfg.url] = (time.monotonic(), {"ok": ok})
    return ok


@dataclass
class ItemReport:
    """What happened to one input that did not go through the ordinary path. Never carries text, only sizes."""
    index: int                      # position in the `texts` list given to embed()
    chars: int                      # characters actually sent (after the MAX_CHARS cut, with any query instruction)
    batch_pos: int = 0              # position within the request batch that was refused
    tokens: int | None = None       # model tokens, when the server could count them
    action: str = "isolated"        # isolated | split | truncated | failed
    pieces: int = 0                 # windows embedded and combined (action split/truncated)
    error: str = ""


@dataclass
class EmbedResult:
    vectors: np.ndarray
    items: list[ItemReport] = field(default_factory=list)   # only inputs that needed recovery (empty on the happy path)
    requests: int = 0                                         # HTTP requests made (embeddings + discovery + tokenize)
    batches_rejected: int = 0


# Process-wide counters for operators and tests: how often recovery was needed. Reset with reset_stats().
STATS: dict[str, int] = {"calls": 0, "batches_rejected": 0, "items_isolated": 0, "items_split": 0, "pieces": 0,
                         "items_truncated": 0, "items_failed": 0}
_stats_lock = threading.Lock()


def reset_stats() -> None:
    with _stats_lock:
        for k in STATS:
            STATS[k] = 0


def _count(**kw: int) -> None:
    with _stats_lock:
        for k, v in kw.items():
            STATS[k] += v


def _host(url: str) -> str:  # for logs: never the path, query or credentials of the configured URL
    u = urlsplit(url)
    return f"{u.hostname or '?'}:{u.port}" if u.port else (u.hostname or "?")


def _http_post(url: str, payload: dict, timeout: float):
    return httpx.post(url, json=payload, timeout=timeout)


def _http_get(url: str, timeout: float):
    return httpx.get(url, timeout=timeout)


def _body_numbers(r: httpx.Response) -> dict:
    """llama-server's 400 for an over-long input names n_prompt_tokens and n_ctx; use them as hints if present."""
    try:
        err = r.json().get("error", {})
        return {k: int(err[k]) for k in ("n_prompt_tokens", "n_ctx") if isinstance(err.get(k), int)}
    except (ValueError, AttributeError, TypeError):
        return {}


def slot_limit(st, cfg: EmbedConfig) -> tuple[int, bool]:
    """-> (tokens one input may use, discovered). Read from the server's GET /props (`default_generation_settings.n_ctx`, which
    llama-server reports per slot), cached per endpoint; DEFAULT_SLOT_TOKENS when the server will not say (not llama-server,
    old build, down), also cached - briefly - so a failing discovery does not add a request to every call."""
    key = "embedlimit:" + cfg.url
    hit = st.health_cache.get(key)
    if hit and time.monotonic() - hit[0] < (LIMIT_TTL if hit[1].get("n_ctx") else LIMIT_FAIL_TTL):
        return (hit[1]["n_ctx"], True) if hit[1].get("n_ctx") else (DEFAULT_SLOT_TOKENS, False)
    n_ctx = None
    try:
        r = _http_get(cfg.url + "/props", 5)
        r.raise_for_status()
        v = r.json()["default_generation_settings"]["n_ctx"]
        n_ctx = int(v) if isinstance(v, int) and not isinstance(v, bool) and v >= 64 else None
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        n_ctx = None
    st.health_cache[key] = (time.monotonic(), {"n_ctx": n_ctx})
    return (n_ctx, True) if n_ctx else (DEFAULT_SLOT_TOKENS, False)


class _Run:
    """One embed() call: the shared state of the ordinary path and of recovery."""

    def __init__(self, st, cfg: EmbedConfig, prefixes: list[str]):
        self.st, self.cfg, self.prefixes = st, cfg, prefixes
        self.requests = 0
        self.dims: int | None = None
        self.items: list[ItemReport] = []
        self.rejected_items = 0

    # -- transport ----------------------------------------------------------------------------------------------
    def post(self, texts: list[str]) -> list[list[float]]:
        """One /v1/embeddings request -> exactly len(texts) vectors of one dimension. Raises httpx.HTTPStatusError for
        a refusal (the caller decides whether it is an input problem), EmbedUnavailable for a malformed answer."""
        self.requests += 1
        r = _http_post(self.cfg.url + "/v1/embeddings", {"input": texts, "model": self.cfg.model}, 120)
        r.raise_for_status()
        rows = sorted(r.json()["data"], key=lambda d: d["index"])
        vecs = [d["embedding"] for d in rows]
        if len(vecs) != len(texts):
            raise EmbedUnavailable("The embedding service returned the wrong number of vectors.")
        for v in vecs:
            if not isinstance(v, list) or not v:
                raise EmbedUnavailable("The embedding service returned unusable vectors.")
            if self.dims is None:
                self.dims = len(v)
            elif len(v) != self.dims:
                raise EmbedUnavailable(f"The embedding service returned vectors of different sizes ({self.dims} and {len(v)}).")
        return vecs

    def tokenize(self, text: str) -> tuple[list[int | None], list[bytes] | None] | None:
        """-> (token ids, per-token bytes or None) from POST /tokenize, or None when the server cannot do it."""
        self.requests += 1
        try:
            r = _http_post(self.cfg.url + "/tokenize", {"content": text, "with_pieces": True}, 30)
            r.raise_for_status()
            toks = r.json()["tokens"]
            if not isinstance(toks, list):
                return None
            if toks and isinstance(toks[0], dict):
                pieces = []
                for t in toks:
                    p = t.get("piece", "")
                    pieces.append(p.encode("utf-8") if isinstance(p, str) else bytes(p))
                return [t.get("id") for t in toks], pieces
            return list(toks), None
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            return None

    # -- recovery -----------------------------------------------------------------------------------------------
    def recover_batch(self, start: int, batch: list[str], hint: dict, vecs: list[list[float] | None], failures: list[str]) -> None:
        """A batch was refused as a whole (HTTP 4xx about the input). Retry its items one by one - at most len(batch)
        requests - and deal with only the ones the server still refuses."""
        bad: list[tuple[int, dict]] = []
        for j, text in enumerate(batch):
            if len(batch) == 1:
                bad.append((j, hint))
                continue
            try:
                vecs[start + j] = self.post([text])[0]
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in INPUT_REJECTED:
                    raise
                bad.append((j, _body_numbers(e.response)))
        good = len(batch) - len(bad)
        if len(batch) == 1:
            log.warning("embed: input %d was refused by the service (HTTP 4xx)", start)
        else:
            log.warning("embed: a batch of %d inputs (inputs %d-%d) was refused as a whole (HTTP 4xx); retried the items one by "
                        "one: %d accepted, %d still refused (batch positions %s)", len(batch), start, start + len(batch) - 1,
                        good, len(bad), [j for j, _ in bad])
        for j, h in bad:
            self.rejected_items += 1
            if self.rejected_items > MAX_REJECTED_ITEMS:
                raise EmbedUnavailable(f"The embedding service refused too many inputs (more than {MAX_REJECTED_ITEMS}) in one call; "
                                       "stopped without embedding the rest.")
            idx = start + j
            rep = ItemReport(idx, len(batch[j]), batch_pos=j)
            self.items.append(rep)
            self.recover_item(idx, batch[j], self.prefixes[idx], vecs, rep, h)
            if rep.action == "failed":
                failures.append(f"input {idx} (batch position {j}, {rep.chars} chars{f', {rep.tokens} tokens' if rep.tokens else ''}): {rep.error}")

    def recover_item(self, idx: int, text: str, pre: str, vecs: list, rep: ItemReport, hint: dict) -> None:
        """One input the server refuses even alone. Count its tokens, cut it into windows that fit, embed the windows and
        combine them into one vector. Sets rep.action to split, truncated or failed (with the reason)."""
        _count(items_isolated=1)
        limit, discovered = slot_limit(self.st, self.cfg)
        if hint.get("n_ctx"):
            limit = min(limit, hint["n_ctx"]) if discovered else hint["n_ctx"]
        body = text[len(pre):]
        tk = self.tokenize(body)
        n_pre = 0
        if pre:
            tp = self.tokenize(pre)
            n_pre = len(tp[0]) if tp else len(pre)
        if tk is not None:
            ids, pieces = tk
            n_body = len(ids)
        else:  # no /tokenize: use the server's own count from the refusal, else assume one token per character
            ids, pieces = None, None
            n_body = max(1, hint["n_prompt_tokens"] - n_pre) if hint.get("n_prompt_tokens") else len(body)
        rep.tokens = n_body + n_pre
        w = limit - SAFETY_TOKENS - n_pre
        if w < MIN_WINDOW_TOKENS:
            return self._fail(rep, f"the token limit ({limit}) is too small to split into", idx)
        if n_body <= w:
            return self._fail(rep, f"refused by the service although it fits the limit ({limit} tokens); not split", idx)
        stride = w - OVERLAP_TOKENS
        starts = list(range(0, max(1, n_body - OVERLAP_TOKENS), stride))
        spans = [(s, min(s + w, n_body)) for s in starts]
        truncated = len(spans) > MAX_PIECES
        spans = spans[:MAX_PIECES]
        cuts = _char_cuts(body, n_body, spans, pieces)
        parts = [pre + body[a:b] for a, b in cuts]
        try:
            pv = self.post(parts)
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in INPUT_REJECTED:
                raise
            pv = []  # a window is still too big: find out which, each alone (bounded by MAX_PIECES requests)
            for k, part in enumerate(parts):
                try:
                    pv += self.post([part])
                except httpx.HTTPStatusError as e2:
                    if e2.response.status_code not in INPUT_REJECTED:
                        raise
                    return self._fail(rep, f"window {k + 1} of {len(parts)} is still refused", idx)
        vecs[idx] = _combine(pv)
        rep.pieces, rep.action = len(parts), ("truncated" if truncated else "split")
        _count(items_split=1, pieces=len(parts), items_truncated=1 if truncated else 0)
        log.log(logging.WARNING if truncated else logging.INFO,
                "embed: input %d (batch position %d, %d chars, %d tokens, slot limit %d%s) %s: %d window(s) of <=%d tokens, "
                "overlap %d, combined into one vector%s", idx, rep.batch_pos, rep.chars, rep.tokens, limit, "" if discovered else " (assumed)",
                "TRUNCATED to the first windows" if truncated else "split", len(parts), w, OVERLAP_TOKENS,
                f"; the last {n_body - spans[-1][1]} tokens were NOT embedded" if truncated else "")

    def _fail(self, rep: ItemReport, why: str, idx: int) -> None:
        rep.action, rep.error = "failed", why
        _count(items_failed=1)
        log.error("embed: input %d (batch position %d, %d chars, %s tokens) could not be embedded: %s", idx, rep.batch_pos, rep.chars,
                  rep.tokens if rep.tokens is not None else "?", why)


def _char_cuts(body: str, n_tokens: int, spans: list[tuple[int, int]], pieces: list[bytes] | None) -> list[tuple[int, int]]:
    """Token windows -> character ranges of `body`. Exact when the server gave each token's bytes (they must add up to the
    text; a window edge that falls inside a multi-byte character takes the whole character); otherwise proportional."""
    raw = body.encode("utf-8")
    if pieces is not None and b"".join(pieces) == raw:
        offs = [0]
        for p in pieces:
            offs.append(offs[-1] + len(p))

        def lo(t: int) -> int:  # first character that starts at or contains byte offs[t]
            return len(raw[:offs[t]].decode("utf-8", "ignore"))

        def hi(t: int) -> int:  # one past the last character that starts before byte offs[t]
            pre = raw[:offs[t]]
            got = pre.decode("utf-8", "ignore")
            return len(got) + (1 if len(got.encode("utf-8")) < len(pre) else 0)
        return [(lo(a), hi(b)) for a, b in spans]
    n = len(body)
    return [(a * n // n_tokens, -(-b * n // n_tokens)) for a, b in spans]


def _combine(vs: list[list[float]]) -> list[float]:
    """Windows of one input -> one vector: the mean of the unit-length window vectors, which embed() then re-normalises."""
    m = np.asarray(vs, dtype=np.float64)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    if not np.isfinite(m).all() or (norms == 0).any():
        raise EmbedUnavailable("The embedding service returned unusable vectors.")
    c = (m / norms).mean(axis=0)
    n = np.linalg.norm(c)
    if not np.isfinite(n) or n < 1e-6:
        raise EmbedUnavailable("The embedding service returned unusable vectors.")
    return (c / n).tolist()


def embed_with_report(st, cfg: EmbedConfig, texts: list[str], *, query: bool = False) -> EmbedResult:
    """embed(), plus what it had to do. Ordinary inputs go out exactly as before: batches of BATCH, one request each, no
    discovery or tokenizing. Only when the server refuses a batch (HTTP 400: an input over its per-slot token limit) does
    recovery start - see "Long inputs" in embed()."""
    _count(calls=1)
    if not texts:
        return EmbedResult(np.zeros((0, 0), dtype=np.float32))
    prefixes = [f"Instruct: {cfg.instruction}\nQuery: " if (query and cfg.instruction) else "" for _ in texts]
    inputs = [(p + t)[:MAX_CHARS] for p, t in zip(prefixes, texts, strict=True)]
    prefixes = [p if i.startswith(p) else "" for p, i in zip(prefixes, inputs, strict=True)]
    run = _Run(st, cfg, prefixes)
    vecs: list[list[float] | None] = [None] * len(inputs)
    failures: list[str] = []
    rejected = 0
    try:
        for i in range(0, len(inputs), BATCH):
            batch = inputs[i:i + BATCH]
            try:
                for j, v in enumerate(run.post(batch)):
                    vecs[i + j] = v
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in INPUT_REJECTED:
                    raise
                rejected += 1
                _count(batches_rejected=1)
                run.recover_batch(i, batch, _body_numbers(e.response), vecs, failures)
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
        st.health_cache.pop("embed:" + cfg.url, None)
        raise EmbedUnavailable(f"The embedding service did not answer ({type(e).__name__}).") from None
    if failures:
        n_ok = sum(v is not None for v in vecs)
        raise EmbedUnavailable(f"The embedding service could not embed {len(failures)} of {len(inputs)} inputs "
                               f"({n_ok} were embedded; none are returned): " + "; ".join(failures[:5])
                               + ("; ..." if len(failures) > 5 else ""))
    if any(v is None for v in vecs):
        raise EmbedUnavailable("The embedding service returned the wrong number of vectors.")
    m = np.asarray(vecs, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    if not np.isfinite(m).all() or (norms == 0).any():
        raise EmbedUnavailable("The embedding service returned unusable vectors.")
    return EmbedResult(m / norms, run.items, run.requests, rejected)


def embed(st, cfg: EmbedConfig, texts: list[str], *, query: bool = False) -> np.ndarray:
    """-> (n, dims) unit vectors, one per input, in order. Raises EmbedUnavailable (never returns zeros or partial output).

    Long inputs. The server limits one input to a slot's share of its context (llama-server: `-c` / `--parallel` tokens; 4096
    on the Spark). A text is cut at MAX_CHARS characters, which is below that for prose and code but not for digits or
    CJK, where a character can be one or more tokens. One such input makes the server refuse its whole batch, so, only
    after a refusal: the batch's items are retried one at a time (the good ones get their vectors); an item still refused
    is counted with POST /tokenize and cut into windows of at most (slot limit - SAFETY_TOKENS) tokens with
    OVERLAP_TOKENS of overlap (the slot limit comes from GET /props, cached per endpoint); the windows are embedded
    (at most MAX_PIECES; beyond that the tail is dropped, logged and flagged as "truncated" in embed_with_report) and
    combined into ONE vector - the mean of the unit window vectors, re-normalised - so the caller still gets one vector
    per input, at the chunk's own position. An item that cannot be recovered fails the call with an error naming its index,
    size and reason, but only after every other input was attempted. Logs and reports carry sizes, never text."""
    return embed_with_report(st, cfg, texts, query=query).vectors


def seal(st, org_id: str, chunk_id: int, vec: np.ndarray) -> bytes:
    return st.vault.encrypt_bytes(vec.astype(np.float32).tobytes(), f"org:{org_id}:vec", str(chunk_id))


def unseal(st, org_id: str, chunk_id: int, blob: bytes) -> np.ndarray:
    return np.frombuffer(st.vault.decrypt_bytes(blob, f"org:{org_id}:vec", str(chunk_id)), dtype=np.float32)


def enqueue(s, f: File, v: FileVersion, user_id: str) -> None:
    jobs_svc.enqueue(s, "embed", org_id=f.org_id, ws_id=f.workspace_id, user_id=user_id,
                     payload={"file_id": f.id, "version_id": v.id}, priority=300)


@jobs_svc.handler("embed")
def handle_embed(st, s, job):
    f = s.get(File, job.payload["file_id"])
    if f is None or f.deleted_at:
        return {"skipped": "the file was removed"}
    v = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n == f.current)).scalar_one()
    if v.id != job.payload["version_id"]:
        return {"skipped": "a newer version exists"}
    ws = s.get(Workspace, f.workspace_id)
    if not authz.Authz(s, job.created_by).check("file.write", ws, files_svc.nodes_for_file(s, f)).allowed:
        raise jobs_svc.JobCancelled("The person who stored this no longer has write access to it.")
    cfg = config(st, s)
    if cfg is None:
        return {"skipped": "no embedding service is configured"}
    todo = [c for c in s.execute(select(Chunk).where(Chunk.file_id == f.id, Chunk.version_id == v.id).order_by(Chunk.ord)).scalars()
            if c.embedding is None or c.embed_model != cfg.model]
    if not todo:
        return {"embedded": 0}
    purpose = f"org:{f.org_id}:content"
    texts = [st.vault.decrypt(c.text_enc, purpose) for c in todo]
    try:
        vecs = embed(st, cfg, texts)
    except EmbedUnavailable as e:
        raise RuntimeError(str(e)) from None  # the job fails visibly; the backfill tries again when the service is back
    for c, vec in zip(todo, vecs, strict=True):
        c.embedding, c.embed_model = seal(st, f.org_id, c.id, vec), cfg.model
    return {"embedded": len(todo), "model": cfg.model, "dims": int(vecs.shape[1])}


def backfill(st, limit: int = 200) -> int:
    """Queue embedding for current file versions that lack current-model vectors. Called by the sweeper; a no-op while
    no service is configured or reachable, and never duplicates a job that is already waiting."""
    with st.db.session() as s:  # (the health check can wait on the network: do it before taking the write lock)
        cfg = config(st, s)
    if cfg is None or not healthy(st, cfg):
        return 0
    with st.db.session(write=True) as s:
        busy = {j.payload.get("file_id") for j in s.execute(select(jobs_svc.Job).where(
            jobs_svc.Job.kind == "embed", jobs_svc.Job.status.in_(("queued", "running")))).scalars()}
        n = 0
        stale = s.execute(select(Chunk.file_id).where((Chunk.embedding.is_(None)) | (Chunk.embed_model != cfg.model)).distinct()).all()
        for (fid,) in stale:
            f = s.get(File, fid)
            if f is None or f.deleted_at or fid in busy:
                continue
            v = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n == f.current)).scalar_one()
            enqueue(s, f, v, v.created_by or f.created_by)
            n += 1
            if n >= limit:
                break
        return n
