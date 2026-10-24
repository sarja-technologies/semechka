"""Knowledge collections: personal or team-shared document sets searched by keyword (BM25), see knowledge_svc."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from .. import audit, knowledge_svc
from ..deps import Ctx, current
from ..models import Team
from ..models_knowledge import Collection, KChunk, KDocument
from .chat import _need_mode

router = APIRouter(prefix="/api/knowledge")

NOT_FOUND = {"code": "not_found", "message": "Knowledge collection not found."}


def _gate(ctx: Ctx):
    _need_mode(ctx, "knowledge")


def _col(ctx: Ctx, cid: str, *, write: bool = False) -> Collection:
    c = knowledge_svc.get_visible(ctx.s, ctx.principal, ctx.user.id, cid)
    if c is None:
        raise HTTPException(404, NOT_FOUND)
    if write and not knowledge_svc.can_write(ctx.principal, ctx.user.id, c):
        raise HTTPException(403, {"code": "forbidden", "message": "You can read this collection but not change it."})
    return c


def _out(ctx: Ctx, c: Collection) -> dict:
    docs, chunks = ctx.s.execute(select(func.count(KDocument.id), func.coalesce(func.sum(KDocument.n_chunks), 0))
                                 .where(KDocument.collection_id == c.id)).one()
    team = ctx.s.get(Team, c.team_id) if c.team_id else None
    return {"id": c.id, "name": ctx.st.vault.decrypt(c.name_enc, "kn.name"),
            "description": ctx.st.vault.decrypt(c.description_enc, "kn.name"),
            "scope": "team" if c.team_id else "personal", "team_id": c.team_id, "team": team.name if team else None,
            "mine": c.owner_id == ctx.user.id, "can_write": knowledge_svc.can_write(ctx.principal, ctx.user.id, c),
            "documents": docs, "passages": int(chunks), "updated_at": c.updated_at}


def _doc_out(ctx: Ctx, d: KDocument) -> dict:
    return {"id": d.id, "name": ctx.st.vault.decrypt(d.filename_enc, "kn.name"), "kind": d.kind, "size_bytes": d.size_bytes,
            "characters": d.n_chars, "passages": d.n_chunks, "created_at": d.created_at}


def _limit(ctx: Ctx, key: str) -> int:
    return ctx.st.settings.value_for(ctx.s, key, ctx.principal)


class NewCollection(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    team_id: str | None = None


@router.get("/info")
def info(ctx: Ctx = Depends(current)):
    """Limits and the plain description of how search works, for the UI."""
    _gate(ctx)
    return {"search": "keyword", "search_note": "Keyword search (BM25). It finds passages that contain your words; it does not "
            "understand meaning or synonyms. English word forms are stemmed; other languages match exact words.",
            "limits": {"collections": _limit(ctx, "knowledge.max_collections"),
                       "documents": _limit(ctx, "knowledge.max_docs_per_collection"),
                       "document_kb": _limit(ctx, "knowledge.max_doc_kb"),
                       "passages": _limit(ctx, "knowledge.max_chunks_per_collection"),
                       "passages_per_question": _limit(ctx, "knowledge.top_k")},
            "types": "Text, Markdown, CSV, JSON, source code, and PDFs that have a text layer (no OCR).",
            "teams": [{"id": t.id, "name": t.name} for t in ctx.s.execute(select(Team)).scalars() if t.id in ctx.principal.team_ids]}


@router.get("/collections")
def list_collections(ctx: Ctx = Depends(current)):
    _gate(ctx)
    return [_out(ctx, c) for c in knowledge_svc.visible_collections(ctx.s, ctx.principal, ctx.user.id)]


@router.post("/collections")
def create_collection(body: NewCollection, ctx: Ctx = Depends(current)):
    _gate(ctx)
    if body.team_id and body.team_id not in ctx.principal.team_ids:
        raise HTTPException(403, {"code": "forbidden", "message": "You are not a member of that team."})
    mine = ctx.s.execute(select(func.count(Collection.id)).where(Collection.owner_id == ctx.user.id)).scalar() or 0
    if mine >= _limit(ctx, "knowledge.max_collections"):
        raise HTTPException(409, {"code": "limit", "message": f"You can have at most {_limit(ctx, 'knowledge.max_collections')} collections."})
    c = Collection(owner_id=ctx.user.id, team_id=body.team_id, name_enc=ctx.st.vault.encrypt(body.name.strip(), "kn.name"),
                   description_enc=ctx.st.vault.encrypt(body.description.strip(), "kn.name"))
    ctx.s.add(c)
    ctx.s.flush()
    audit.record(ctx.s, ctx.user.id, "knowledge.collection.create", c.id, {"scope": "team" if c.team_id else "personal"}, ctx.ip)
    return _out(ctx, c)


class PatchCollection(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=1000)


@router.patch("/collections/{cid}")
def patch_collection(cid: str, body: PatchCollection, ctx: Ctx = Depends(current)):
    _gate(ctx)
    c = _col(ctx, cid, write=True)
    if body.name is not None:
        c.name_enc = ctx.st.vault.encrypt(body.name.strip(), "kn.name")
    if body.description is not None:
        c.description_enc = ctx.st.vault.encrypt(body.description.strip(), "kn.name")
    return _out(ctx, c)


@router.delete("/collections/{cid}")
def delete_collection(cid: str, ctx: Ctx = Depends(current)):
    _gate(ctx)
    c = _col(ctx, cid, write=True)
    knowledge_svc.cache_for(ctx.st).purge(c.id)
    ctx.s.query(KChunk).filter(KChunk.collection_id == c.id).delete()
    ctx.s.query(KDocument).filter(KDocument.collection_id == c.id).delete()
    ctx.s.delete(c)
    audit.record(ctx.s, ctx.user.id, "knowledge.collection.delete", cid, {}, ctx.ip)
    return {"ok": True}


@router.get("/collections/{cid}/documents")
def list_documents(cid: str, ctx: Ctx = Depends(current)):
    _gate(ctx)
    c = _col(ctx, cid)
    docs = ctx.s.execute(select(KDocument).where(KDocument.collection_id == c.id).order_by(KDocument.created_at)).scalars()
    return [_doc_out(ctx, d) for d in docs]


@router.post("/collections/{cid}/documents")
async def upload_document(cid: str, file: UploadFile = File(...), ctx: Ctx = Depends(current)):
    _gate(ctx)
    c = _col(ctx, cid, write=True)
    max_bytes = _limit(ctx, "knowledge.max_doc_kb") * 1024
    data = await file.read(max_bytes + 1)
    name = (file.filename or "document").replace("\\", "/").rsplit("/", 1)[-1][:200] or "document"
    n_docs = ctx.s.execute(select(func.count(KDocument.id)).where(KDocument.collection_id == c.id)).scalar() or 0
    if n_docs >= _limit(ctx, "knowledge.max_docs_per_collection"):
        raise HTTPException(409, {"code": "limit", "message": f"A collection holds at most {_limit(ctx, 'knowledge.max_docs_per_collection')} documents."})
    try:
        kind, text = await asyncio.to_thread(knowledge_svc.extract_text, name, data, max_bytes)
    except knowledge_svc.KnowledgeError as e:
        raise HTTPException(422, {"code": "unsupported_document", "message": str(e)}) from None
    n_chunks = ctx.s.execute(select(func.coalesce(func.sum(KDocument.n_chunks), 0)).where(KDocument.collection_id == c.id)).scalar() or 0
    new_chunks = len(knowledge_svc.chunk_text(text))
    cap = _limit(ctx, "knowledge.max_chunks_per_collection")
    if n_chunks + new_chunks > cap:
        raise HTTPException(409, {"code": "limit", "message": f"This collection would exceed {cap:,} passages "
                                  f"({n_chunks:,} used, this file adds {new_chunks:,})."})
    doc = knowledge_svc.add_document(ctx.s, ctx.st, c, ctx.user.id, name, kind, len(data), text)
    audit.record(ctx.s, ctx.user.id, "knowledge.document.add", c.id, {"kind": kind, "bytes": len(data)}, ctx.ip)
    return _doc_out(ctx, doc)


@router.delete("/collections/{cid}/documents/{did}")
def delete_document(cid: str, did: str, ctx: Ctx = Depends(current)):
    _gate(ctx)
    c = _col(ctx, cid, write=True)
    d = ctx.s.get(KDocument, did)
    if d is None or d.collection_id != c.id:
        raise HTTPException(404, {"code": "not_found", "message": "Document not found."})
    ctx.s.query(KChunk).filter(KChunk.doc_id == d.id).delete()
    ctx.s.delete(d)
    knowledge_svc.touch(ctx.st, c)
    audit.record(ctx.s, ctx.user.id, "knowledge.document.delete", c.id, {}, ctx.ip)
    return {"ok": True}


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    k: int | None = Field(default=None, ge=1, le=20)


@router.post("/collections/{cid}/search")
def search_collection(cid: str, body: SearchIn, ctx: Ctx = Depends(current)):
    """Run the same keyword search that chat uses, so people can see exactly what would be retrieved."""
    _gate(ctx)
    c = _col(ctx, cid)
    hits = knowledge_svc.search(ctx.s, ctx.st, [c], body.query, body.k or _limit(ctx, "knowledge.top_k"))
    return {"method": "keyword (BM25)", "results": [{"file": h.filename, "passage": h.ord + 1, "score": h.score, "text": h.text} for h in hits]}
