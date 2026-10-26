"""Workspace files, contexts, retrieval and background jobs. Translation only: authority is decided in
files_svc / contexts_svc / jobs_svc through authz. Anything the caller cannot see answers 404."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi import File as UploadField
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import contexts_svc as C
from .. import files_svc as F
from .. import ingest_svc, jobs_svc
from ..deps import Ctx, abegin_write, cloud_gate, current
from ..models_files import Context, ContextAttachment, DocText, File, FileVersion, Job
from ..models_tenancy import Workspace
from .media import _ranged
from .orgs import az, org_for, run, ws_for

router = APIRouter(prefix="/api", dependencies=[Depends(cloud_gate)])

# Types that may be shown inline in the browser. SVG and HTML are deliberately absent: they can carry script.
INLINE_OK = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp", "text/plain", "text/markdown",
             "text/csv", "video/mp4", "video/webm", "audio/mpeg", "audio/wav"}


def _file_view(f: File, extra: dict | None = None) -> dict:
    return {"id": f.id, "workspace_id": f.workspace_id, "folder_id": f.folder_id, "name": f.name, "mime": f.mime, "kind": f.kind,
            "size": f.size, "version": f.current, "restricted": f.restricted, "tags": f.tags, "created_by": f.created_by,
            "created_at": f.created_at, "updated_at": f.updated_at, "deleted_at": f.deleted_at, **(extra or {})}


def _folder_view(fo) -> dict:
    return {"id": fo.id, "name": fo.name, "parent_id": fo.parent_id, "restricted": fo.restricted, "created_at": fo.created_at}


def _delivery(request: Request, f: File, v: FileVersion, data: bytes, inline: bool) -> Response:
    show = inline and v.mime in INLINE_OK
    resp = _ranged(data, v.mime if show else "application/octet-stream", request, None)
    name = f.name.replace('"', "'")
    ascii_name = name.encode("ascii", "replace").decode().replace("?", "_")
    resp.headers["Content-Disposition"] = f"{'inline' if show else 'attachment'}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(f.name)}"
    return resp


async def _read_upload(file: UploadFile, limit: int) -> bytes:
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, {"code": "too_large", "message": f"Files are limited to {limit // 1048576} MB here."})
    return data


# ---- folders and files ---------------------------------------------------------------------------
class FolderIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    parent_id: str | None = None


@router.post("/workspaces/{wid}/folders")
def new_folder(wid: str, body: FolderIn, ctx: Ctx = Depends(current)):
    return _folder_view(run(F.create_folder, ctx.s, az(ctx), wid, body.name, body.parent_id))


class FolderPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: str | None = None
    restricted: bool | None = None


@router.patch("/folders/{fid}")
def patch_folder(fid: str, body: FolderPatch, ctx: Ctx = Depends(current)):
    return _folder_view(run(F.set_folder, ctx.s, az(ctx), fid, name=body.name, parent_id=body.parent_id,
                            move="parent_id" in body.model_fields_set, restricted=body.restricted))


def _index_status(s, files) -> dict[str, str]:
    """Whether each file's CURRENT version is searchable: ok | pending | unsupported | failed."""
    if not files:
        return {}
    from sqlalchemy import and_
    rows = s.execute(select(DocText.file_id, DocText.status).join(FileVersion, FileVersion.id == DocText.version_id)
                     .join(File, and_(File.id == FileVersion.file_id, File.current == FileVersion.n))
                     .where(DocText.file_id.in_([f.id for f in files]))).all()
    return dict(rows)


@router.get("/workspaces/{wid}/files")
def list_files(wid: str, folder_id: str | None = None, trash: bool = False, ctx: Ctx = Depends(current)):
    r = run(F.list_children, ctx.s, az(ctx), wid, folder_id, trash=trash)
    status = _index_status(ctx.s, r["files"])
    return {"folders": [_folder_view(x) for x in r["folders"]], "files": [_file_view(x, {"index": status.get(x.id, "pending")}) for x in r["files"]],
            "path": [{"id": i, "name": n} for i, n in r["path"]]}


@router.post("/workspaces/{wid}/files")
async def upload(wid: str, file: UploadFile = UploadField(...), folder_id: str | None = Form(default=None),
                 ctx: Ctx = Depends(current)):
    a = az(ctx)
    ws = run(F.workspace_of, ctx.s, wid, a)
    data = await _read_upload(file, F.max_upload(a, ws))
    await abegin_write(ctx)  # the body took as long as the client took; the lock is held only for the write
    f, _ = run(F.create_file, ctx.st, ctx.s, a, wid, file.filename or "upload", data, folder_id=folder_id)
    return _file_view(f)


@router.get("/files/{fid}")
def file_info(fid: str, ctx: Ctx = Depends(current)):
    a = az(ctx)
    f, ws = run(F.get_file, ctx.s, a, fid)
    vs = ctx.s.execute(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.n.desc())).scalars().all()
    nodes = F.nodes_for_file(ctx.s, f)
    can_hist = a.check("file.restore", ws, nodes).allowed
    dt = ctx.s.get(DocText, next((v.id for v in vs if v.n == f.current), ""))
    return _file_view(f, {
        "versions": [{"n": v.n, "size": v.size, "sha256": v.sha256, "source": v.source, "note": v.note, "created_by": v.created_by,
                      "created_at": v.created_at} for v in vs if can_hist or v.n == f.current],
        "text": {"status": dt.status, "extractor": dt.extractor, "chars": dt.n_chars, "error": dt.error} if dt else None,
        "my_permissions": [p for p in ("file.read", "file.download", "file.write", "file.delete", "file.restore", "ws.members.manage")
                           if a.check(p, ws, nodes).allowed]})


@router.get("/files/{fid}/content")
def file_content(fid: str, request: Request, version: int | None = None, download: bool = False, ctx: Ctx = Depends(current)):
    f, v, data = run(F.read_file, ctx.st, ctx.s, az(ctx), fid, version=version, action="download" if download else "preview")
    return _delivery(request, f, v, data, inline=not download)


@router.post("/files/{fid}/versions")
async def upload_version(fid: str, file: UploadFile = UploadField(...), note: str = Form(default=""), ctx: Ctx = Depends(current)):
    a = az(ctx)
    _, ws = run(F.get_file, ctx.s, a, fid)
    data = await _read_upload(file, F.max_upload(a, ws))
    await abegin_write(ctx)
    v = run(F.new_version, ctx.st, ctx.s, a, fid, data, note=note)
    return {"n": v.n}


class TextIn(BaseModel):
    text: str = Field(max_length=2_000_000)
    note: str = ""


@router.put("/files/{fid}/text")
def edit_text(fid: str, body: TextIn, ctx: Ctx = Depends(current)):
    a = az(ctx)
    f, _ = run(F.get_file, ctx.s, a, fid, "file.write")
    if f.kind not in F.TEXTUAL:
        raise HTTPException(422, {"code": "invalid", "message": "Only text, code and data files can be edited in the browser."})
    v = run(F.new_version, ctx.st, ctx.s, a, fid, body.text.encode(), note=body.note)
    return {"n": v.n}


class RestoreIn(BaseModel):
    version: int


@router.post("/files/{fid}/restore")
def restore(fid: str, body: RestoreIn, ctx: Ctx = Depends(current)):
    return {"n": run(F.restore_version, ctx.st, ctx.s, az(ctx), fid, body.version).n}


class FilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    folder_id: str | None = None
    restricted: bool | None = None
    tags: list[str] | None = None


@router.patch("/files/{fid}")
def patch_file(fid: str, body: FilePatch, ctx: Ctx = Depends(current)):
    return _file_view(run(F.rename_or_move, ctx.s, az(ctx), fid, name=body.name, folder_id=body.folder_id,
                          move="folder_id" in body.model_fields_set, restricted=body.restricted, tags=body.tags))


@router.delete("/files/{fid}")
def trash(fid: str, ctx: Ctx = Depends(current)):
    run(F.trash_file, ctx.s, az(ctx), fid)
    return {"ok": True}


@router.post("/files/{fid}/untrash")
def untrash(fid: str, ctx: Ctx = Depends(current)):
    return _file_view(run(F.untrash_file, ctx.s, az(ctx), fid))


@router.get("/workspaces/{wid}/all-files")
def all_files(wid: str, ctx: Ctx = Depends(current)):
    rows = run(F.all_files, ctx.s, az(ctx), wid)
    status = _index_status(ctx.s, [f for f, _ in rows])
    return [_file_view(f, {"path": p, "index": status.get(f.id, "pending")}) for f, p in rows]


@router.get("/workspaces/{wid}/search")
def search(wid: str, q: str, k: int = 8, ctx: Ctx = Depends(current)):
    """File names and, where indexed, passages - only from what the caller may read."""
    a = az(ctx)
    names = run(F.search_names, ctx.s, a, wid, q)
    r = run(C.retrieve, ctx.st, ctx.s, a, q, workspace_id=wid, k=max(1, min(k, 25)))
    return {"files": [_file_view(f) for f in names], "passages": [_hit(h) for h in r.hits], "mode": r.mode, "notes": r.notes}


# ---- contexts ------------------------------------------------------------------------------------
def _hit(h: C.Hit) -> dict:
    return {"file_id": h.file_id, "file": h.file_name, "version": h.version_n, "loc": h.loc, "context_id": h.context_id,
            "context": h.context_name, "score": h.score, "text": h.text, "mode": h.mode, "bm25": h.bm25, "cosine": h.cosine}


def _ctx_view(c: Context, a=None, items=None) -> dict:
    d = {"id": c.id, "workspace_id": c.workspace_id, "name": c.name, "description": c.description, "restricted": c.restricted,
         "archived": c.archived, "version": c.version, "created_at": c.created_at, "updated_at": c.updated_at}
    if items is not None:
        d["items"] = [{"file_id": f.id, "name": f.name, "kind": f.kind, "version": f.current} for f in items]
    return d


@router.get("/workspaces/{wid}/contexts")
def list_contexts(wid: str, ctx: Ctx = Depends(current)):
    a = az(ctx)
    run(F.workspace_of, ctx.s, wid, a)
    # the count only includes files this person may read, so it cannot hint at restricted ones
    return [{**_ctx_view(c), "files": len(C.context_items(ctx.s, a, c)), "home": c.workspace_id != wid} for c in C.contexts_for_workspace(ctx.s, a, wid)]


class CtxIn(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = ""


@router.post("/workspaces/{wid}/contexts")
def new_context(wid: str, body: CtxIn, ctx: Ctx = Depends(current)):
    return _ctx_view(run(C.create_context, ctx.s, az(ctx), wid, body.name, body.description))


@router.get("/contexts/{cid}")
def get_context(cid: str, ctx: Ctx = Depends(current)):
    a = az(ctx)
    c, _ = run(C.get_context, ctx.s, a, cid)
    attached = [w for (w,) in ctx.s.execute(select(ContextAttachment.workspace_id).where(ContextAttachment.context_id == c.id))]
    return {**_ctx_view(c, a, C.context_items(ctx.s, a, c)), "attached_to": attached}


class CtxPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    restricted: bool | None = None
    archived: bool | None = None


@router.patch("/contexts/{cid}")
def patch_context(cid: str, body: CtxPatch, ctx: Ctx = Depends(current)):
    return _ctx_view(run(C.update_context, ctx.s, az(ctx), cid, name=body.name, description=body.description,
                         restricted=body.restricted, archived=body.archived))


@router.delete("/contexts/{cid}")
def delete_context(cid: str, ctx: Ctx = Depends(current)):
    run(C.delete_context, ctx.s, az(ctx), cid)
    return {"ok": True}


@router.put("/contexts/{cid}/items/{fid}")
def add_item(cid: str, fid: str, ctx: Ctx = Depends(current)):
    return {"version": run(C.add_item, ctx.s, az(ctx), cid, fid)}


@router.delete("/contexts/{cid}/items/{fid}")
def remove_item(cid: str, fid: str, ctx: Ctx = Depends(current)):
    return {"version": run(C.remove_item, ctx.s, az(ctx), cid, fid)}


class AttachIn(BaseModel):
    workspace_id: str


@router.post("/contexts/{cid}/attach")
def attach(cid: str, body: AttachIn, ctx: Ctx = Depends(current)):
    run(C.attach, ctx.s, az(ctx), cid, body.workspace_id)
    return {"ok": True}


@router.delete("/contexts/{cid}/attach/{wid}")
def detach(cid: str, wid: str, ctx: Ctx = Depends(current)):
    run(C.detach, ctx.s, az(ctx), cid, wid)
    return {"ok": True}


@router.get("/contexts/{cid}/versions")
def context_versions(cid: str, ctx: Ctx = Depends(current)):
    return [{"n": v.n, "note": v.note, "items": len(v.items), "created_by": v.created_by, "created_at": v.created_at}
            for v in run(C.versions, ctx.s, az(ctx), cid)]


@router.post("/contexts/{cid}/versions/{n}/restore")
def restore_context(cid: str, n: int, ctx: Ctx = Depends(current)):
    return {"version": run(C.restore_version, ctx.s, az(ctx), cid, n)}


@router.get("/contexts/{cid}/export")
def export_context(cid: str, ctx: Ctx = Depends(current)):
    c, _ = run(C.get_context, ctx.s, az(ctx), cid)
    data = run(C.export_context, ctx.st, ctx.s, az(ctx), cid)
    return Response(data, media_type="application/zip", headers={
        "Content-Disposition": f"attachment; filename=\"context-{c.id}.zip\"", "X-Content-Type-Options": "nosniff"})


@router.post("/workspaces/{wid}/contexts/import")
async def import_context(wid: str, file: UploadFile = UploadField(...), ctx: Ctx = Depends(current)):
    data = await _read_upload(file, C.MAX_EXPORT_BYTES)
    return _ctx_view(run(C.import_context, ctx.st, ctx.s, az(ctx), wid, data))


class RetrieveIn(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    context_ids: list[str] | None = None
    k: int = Field(default=8, ge=1, le=25)


@router.post("/workspaces/{wid}/retrieve")
def retrieve(wid: str, body: RetrieveIn, ctx: Ctx = Depends(current)):
    a = az(ctx)
    run(F.workspace_of, ctx.s, wid, a)
    r = run(C.retrieve, ctx.st, ctx.s, a, body.query, workspace_id=wid, context_ids=body.context_ids, k=body.k)
    return {"mode": r.mode, "searched_files": r.searched_files, "notes": r.notes, "passages": [_hit(h) for h in r.hits]}


# ---- jobs and indexing ---------------------------------------------------------------------------
def _job(j: Job) -> dict:
    return {"id": j.id, "kind": j.kind, "workspace_id": j.workspace_id, "status": j.status, "progress": j.progress,
            "progress_text": j.progress_text, "error": j.error, "attempts": j.attempts, "created_at": j.created_at,
            "started_at": j.started_at, "finished_at": j.finished_at, "result": j.result}


@router.get("/workspaces/{wid}/jobs")
def list_jobs(wid: str, limit: int = 50, ctx: Ctx = Depends(current)):
    a, w = ws_for(ctx, wid, "ws.view")
    q = select(Job).where(Job.workspace_id == wid).order_by(Job.created_at.desc()).limit(max(1, min(limit, 200)))
    if not a.check("job.manage_all", w).allowed:
        q = q.where(Job.created_by == ctx.user.id)
    return [_job(j) for j in ctx.s.execute(q).scalars()]


@router.get("/jobs/{jid}")
def get_job(jid: str, ctx: Ctx = Depends(current)):
    a = az(ctx)
    j = ctx.s.get(Job, jid)
    w = ctx.s.get(Workspace, j.workspace_id) if j and j.workspace_id else None
    if j is None or not (j.created_by == ctx.user.id and a.membership(j.org_id) or (w and a.check("job.manage_all", w).allowed)):
        raise HTTPException(404, {"code": "not_found", "message": "No such job."})
    return _job(j)


@router.post("/jobs/{jid}/cancel")
def cancel_job(jid: str, ctx: Ctx = Depends(current)):
    return _job(run(jobs_svc.cancel, ctx.s, az(ctx), jid))


@router.post("/orgs/{oid}/reindex")
def reindex(oid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.policy.edit")
    return {"queued": ingest_svc.reindex(ctx.s, oid, a.user_id)}
