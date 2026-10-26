"""Ingestion: extract text from a file version, split it into passages, and index them. Runs as a durable job; the
original file is never modified, and extracted text and passages are encrypted with the organisation's key."""
from __future__ import annotations

import time

from sqlalchemy import select

from . import authz, extract_svc, files_svc, jobs_svc
from .knowledge_svc import chunk_text
from .models_files import (
    Chunk,
    Context,
    ContextItem,
    ContextVersion,
    DocText,
    File,
    FileVersion,
)
from .models_tenancy import Workspace

INDEX_VERSION = 1  # chunking / extraction generation; bump to re-index everything (see reindex)


def enqueue(s, f: File, v: FileVersion, user_id: str) -> None:
    dt = s.get(DocText, v.id)
    if dt is None:
        s.add(DocText(version_id=v.id, org_id=f.org_id, workspace_id=f.workspace_id, file_id=f.id, status="pending"))
    jobs_svc.enqueue(s, "ingest", org_id=f.org_id, ws_id=f.workspace_id, user_id=user_id,
                     payload={"file_id": f.id, "version_id": v.id}, priority=200)


def reindex(s, org_id: str, user_id: str) -> int:
    """Queue ingestion for every current version whose index is older than INDEX_VERSION (or never succeeded)."""
    n = 0
    for f in s.execute(select(File).where(File.org_id == org_id, File.deleted_at.is_(None))).scalars().all():
        v = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n == f.current)).scalar_one()
        dt = s.get(DocText, v.id)
        if dt is None or dt.index_version < INDEX_VERSION or dt.status in ("pending", "failed"):
            enqueue(s, f, v, v.created_by or f.created_by)  # runs as whoever stored it, so authority is theirs, not the admin's
            n += 1
    return n


def touch_contexts(s, file_id: str, by: str, note: str) -> None:
    """A change to a file's content is a new version of every context that contains it (cache key + history)."""
    for cid in [c for (c,) in s.execute(select(ContextItem.context_id).where(ContextItem.file_id == file_id))]:
        bump_context(s, cid, by, note)


def bump_context(s, context_id: str, by: str, note: str = "") -> int:
    c = s.get(Context, context_id)
    if c is None:
        return 0
    c.version += 1
    c.updated_at = time.time()
    items = [{"file_id": i.file_id, "version": i.pinned_version} for i in
             s.execute(select(ContextItem).where(ContextItem.context_id == c.id)).scalars()]
    s.add(ContextVersion(context_id=c.id, n=c.version, items=items, note=note[:200], created_by=by))
    return c.version


@jobs_svc.handler("ingest")
def handle_ingest(st, s, job):
    p = job.payload
    f = s.get(File, p["file_id"])
    v = s.get(FileVersion, p["version_id"])
    if f is None or v is None or f.deleted_at:
        return {"skipped": "the file was removed"}
    if f.current != v.n:
        return {"skipped": "a newer version exists"}
    ws = s.get(Workspace, f.workspace_id)
    az = authz.Authz(s, job.created_by)
    if not az.check("file.write", ws, files_svc.nodes_for_file(s, f)).allowed:  # authority is rechecked when the job runs
        raise jobs_svc.JobCancelled("The person who uploaded this no longer has write access to it.")
    data = files_svc.store_for(st).get(f.org_id, v.id)
    dt = s.get(DocText, v.id) or DocText(version_id=v.id, org_id=f.org_id, workspace_id=f.workspace_id, file_id=f.id)
    s.add(dt)
    s.query(Chunk).filter(Chunk.file_id == f.id).delete()  # only the current version is searchable
    try:
        ex = extract_svc.extract(f.name, f.kind, data)
    except extract_svc.Unsupported as e:
        dt.status, dt.error, dt.extractor, dt.n_chars, dt.text_enc = "unsupported", str(e)[:300], "", 0, ""
        dt.index_version = INDEX_VERSION
        touch_contexts(s, f.id, job.created_by, "content changed")
        return {"status": "unsupported"}
    except extract_svc.Unreadable as e:
        dt.status, dt.error, dt.extractor, dt.n_chars, dt.text_enc = "failed", str(e)[:300], "", 0, ""
        dt.index_version = INDEX_VERSION
        touch_contexts(s, f.id, job.created_by, "content changed")
        return {"status": "failed"}
    purpose = f"org:{f.org_id}:content"
    full = "\n\n".join(t for _, t in ex.segments)
    dt.status, dt.error, dt.extractor, dt.n_chars = "ok", "", ex.extractor, len(full)
    dt.text_enc, dt.meta, dt.index_version = st.vault.encrypt(full, purpose), ex.meta, INDEX_VERSION
    n = 0
    for loc, text in ex.segments:
        for piece in chunk_text(text):
            s.add(Chunk(org_id=f.org_id, workspace_id=f.workspace_id, file_id=f.id, version_id=v.id, ord=n, loc=loc[:80],
                        text_enc=st.vault.encrypt(piece, purpose)))
            n += 1
    touch_contexts(s, f.id, job.created_by, "content changed")
    return {"status": "ok", "chunks": n, "chars": len(full)}
