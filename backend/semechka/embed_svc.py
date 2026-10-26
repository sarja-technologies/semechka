"""Embeddings for semantic retrieval, from a separately run OpenAI-compatible endpoint (settings `embeddings.*`).

Nothing here pretends: with no endpoint configured, or the endpoint down, retrieval reports mode "keyword". Vectors are
unit-length float32, stored encrypted with the organisation's key and tagged with the model that produced them, so a
model change makes old vectors stale (they are ignored, never mixed) until the backfill recomputes them.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

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


def embed(st, cfg: EmbedConfig, texts: list[str], *, query: bool = False) -> np.ndarray:
    """-> (n, dims) unit vectors. Raises EmbedUnavailable (never returns zeros or partial output)."""
    if not texts:
        return np.zeros((0, 0), dtype=np.float32)
    inputs = [(f"Instruct: {cfg.instruction}\nQuery: {t}" if (query and cfg.instruction) else t)[:MAX_CHARS] for t in texts]
    out: list[list[float]] = []
    try:
        for i in range(0, len(inputs), BATCH):
            r = httpx.post(cfg.url + "/v1/embeddings", json={"input": inputs[i:i + BATCH], "model": cfg.model}, timeout=120)
            r.raise_for_status()
            rows = sorted(r.json()["data"], key=lambda d: d["index"])
            out += [d["embedding"] for d in rows]
    except (httpx.HTTPError, KeyError, ValueError) as e:
        st.health_cache.pop("embed:" + cfg.url, None)
        raise EmbedUnavailable(f"The embedding service did not answer ({type(e).__name__}).") from None
    if len(out) != len(inputs):
        raise EmbedUnavailable("The embedding service returned the wrong number of vectors.")
    m = np.asarray(out, dtype=np.float32)
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    if not np.isfinite(m).all() or (norms == 0).any():
        raise EmbedUnavailable("The embedding service returned unusable vectors.")
    return m / norms


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
