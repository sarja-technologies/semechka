"""Workspace files: folders, versions, trash, quotas, encrypted storage, access records.

Every read and write goes through `authz.Authz` with the file's real ancestry (file, then its folders), so a restricted
folder or an access rule on a single file is honoured. Bodies are stored encrypted on disk under the organisation's
key and addressed only by generated ids, so no user-supplied name ever reaches the filesystem.
"""
from __future__ import annotations

import hashlib
import os
import re
import time
from pathlib import Path

from sqlalchemy import func, select

from . import audit, authz
from .models_files import File, FileAccess, FileVersion, Folder
from .models_tenancy import Workspace
from .tenancy_svc import TenancyError

MAX_NAME = 200
MAX_DEPTH = 32
_ID = re.compile(r"^[a-z0-9_]{4,40}$")

EXT_KIND = {
    "document": {"doc", "docx", "odt", "rtf", "pdf"}, "sheet": {"xls", "xlsx", "ods", "csv", "tsv"},
    "slides": {"ppt", "pptx", "odp"},
    "text": {"txt", "md", "markdown", "rst", "log", "tex", "org"},
    "code": {"py", "js", "mjs", "ts", "tsx", "jsx", "rb", "go", "rs", "c", "h", "cpp", "hpp", "java", "kt", "sh", "bash",
             "sql", "svelte", "vue", "php", "swift", "cs", "css", "html", "htm", "xml", "yaml", "yml", "toml", "ini", "cfg"},
    "data": {"json", "jsonl", "parquet"},
    "image": {"png", "jpg", "jpeg", "gif", "webp", "bmp", "svg", "tif", "tiff"},
    "video": {"mp4", "mov", "webm", "mkv", "avi"}, "audio": {"mp3", "wav", "flac", "ogg", "m4a"},
    "archive": {"zip", "tar", "gz", "tgz", "7z", "rar"},
}
MIME = {"pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "gif": "image/gif",
        "webp": "image/webp", "svg": "image/svg+xml", "mp4": "video/mp4", "webm": "video/webm", "csv": "text/csv",
        "json": "application/json", "md": "text/markdown", "txt": "text/plain", "html": "text/html",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "mp3": "audio/mpeg", "wav": "audio/wav", "zip": "application/zip"}
# What a file claiming this extension must begin with. A mismatch is refused: it is how a renamed binary slips in.
MAGIC = {"pdf": [b"%PDF-"], "png": [b"\x89PNG\r\n\x1a\n"], "jpg": [b"\xff\xd8\xff"], "jpeg": [b"\xff\xd8\xff"],
         "gif": [b"GIF87a", b"GIF89a"], "docx": [b"PK\x03\x04"], "xlsx": [b"PK\x03\x04"], "pptx": [b"PK\x03\x04"],
         "zip": [b"PK\x03\x04", b"PK\x05\x06"], "gz": [b"\x1f\x8b"], "mp4": [b"\x00\x00\x00"], "webp": [b"RIFF"]}
EXECUTABLE_MAGIC = (b"\x7fELF", b"MZ", b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xfe\xed\xfa")
TEXTUAL = {"text", "code", "data"}


def clean_name(name: str) -> str:
    n = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", (name or "").strip()).strip(". ")
    n = re.sub(r"\s+", " ", n)[:MAX_NAME]
    if not n:
        raise TenancyError("invalid", "A file or folder needs a name.")
    return n


def ext_of(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def classify(name: str) -> tuple[str, str]:
    e = ext_of(name)
    kind = next((k for k, v in EXT_KIND.items() if e in v), "other")
    return kind, MIME.get(e) or ("text/plain" if kind in TEXTUAL else "application/octet-stream")


def vet_upload(name: str, data: bytes, *, allow_executables: bool = False) -> tuple[str, str]:
    """-> (kind, mime) or raises. Refuses executables, extension/content mismatches and NUL bytes in text."""
    kind, mime = classify(name)
    e = ext_of(name)
    if not data:
        raise TenancyError("empty", "That file is empty.")
    is_pe = data[:2] == b"MZ" and b"PE\x00\x00" in data[:4096]  # a text file that merely starts with "MZ" is fine
    if not allow_executables and (data.startswith(EXECUTABLE_MAGIC) and data[:2] != b"MZ" or is_pe):
        raise TenancyError("unsafe", "Executable programs cannot be stored in a workspace.")
    if e in {"exe", "dll", "so", "dylib", "bat", "cmd", "scr", "msi", "com"} and not allow_executables:
        raise TenancyError("unsafe", f".{e} files cannot be stored in a workspace.")
    if e in MAGIC and not any(data.startswith(m) for m in MAGIC[e]):
        raise TenancyError("mismatch", f"This does not look like a real .{e} file.")
    if kind in TEXTUAL and b"\x00" in data[:8192]:
        raise TenancyError("mismatch", "This looks like a binary file, not text.")
    return kind, mime


class FileStore:
    """Encrypted blobs under <data_dir>/workspaces/<org>/<xx>/<version>.sm. Names come only from generated ids."""

    def __init__(self, root: Path, vault):
        self.root, self.vault = root, vault

    def path(self, org_id: str, version_id: str) -> Path:
        if not _ID.match(org_id) or not _ID.match(version_id):
            raise ValueError("bad storage id")
        p = (self.root / org_id / version_id[-2:] / f"{version_id}.sm").resolve()
        if not str(p).startswith(str(self.root.resolve()) + os.sep):
            raise ValueError("path escapes the store")
        return p

    def put(self, org_id: str, version_id: str, data: bytes) -> None:
        p = self.path(org_id, version_id)
        p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        blob = self.vault.encrypt_bytes(data, f"org:{org_id}:files", version_id)
        tmp = p.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)

    def get(self, org_id: str, version_id: str) -> bytes:
        return self.vault.decrypt_bytes(self.path(org_id, version_id).read_bytes(), f"org:{org_id}:files", version_id)

    def delete(self, org_id: str, version_id: str) -> None:
        self.path(org_id, version_id).unlink(missing_ok=True)


def store_for(st) -> FileStore:
    return FileStore(st.cfg.data_dir / "workspaces", st.vault)


# ---- ancestry ------------------------------------------------------------------------------------
def folder_chain(s, folder_id: str | None) -> list[Folder]:
    out, seen = [], set()
    while folder_id and folder_id not in seen and len(out) < MAX_DEPTH:
        seen.add(folder_id)
        f = s.get(Folder, folder_id)
        if f is None:
            break
        out.append(f)
        folder_id = f.parent_id
    return out


def nodes_for_folder(s, folder_id: str | None) -> list[authz.Node]:
    return [authz.Node("folder", f.id, f.restricted) for f in folder_chain(s, folder_id)]


def nodes_for_file(s, f: File) -> list[authz.Node]:
    return [authz.Node("file", f.id, f.restricted, f.created_by), *nodes_for_folder(s, f.folder_id)]


def workspace_of(s, ws_id: str, az: authz.Authz | None = None) -> Workspace:
    """Load a workspace. With `az`, one the caller cannot even see answers 'not found', like a missing one, so ids cannot
    be probed; one they can see but not use is left for the caller's own permission check to refuse (403)."""
    w = s.get(Workspace, ws_id)
    if w is None or (az is not None and not (az.check("ws.view", w).allowed or az.check_org("org.workspaces.list_all", w.org_id).allowed)):
        raise TenancyError("not_found", "No such workspace.", 404)
    return w


def get_file(s, az: authz.Authz, file_id: str, perm: str = "file.read", *, include_deleted: bool = False) -> tuple[File, Workspace]:
    """Load a file the caller may act on. Anything they may not even see answers 'not found'."""
    f = s.get(File, file_id)
    ws = s.get(Workspace, f.workspace_id) if f else None
    if f is None or ws is None or (f.deleted_at and not include_deleted) or not az.check("file.read", ws, nodes_for_file(s, f)).allowed:
        raise TenancyError("not_found", "No such file.", 404)
    if perm != "file.read":
        az.require(perm, ws, nodes_for_file(s, f))
    return f, ws


def get_folder(s, az: authz.Authz, folder_id: str, perm: str = "file.read") -> tuple[Folder, Workspace]:
    fo = s.get(Folder, folder_id)
    ws = s.get(Workspace, fo.workspace_id) if fo else None
    if fo is None or ws is None or fo.deleted_at or not az.check("file.read", ws, nodes_for_folder(s, fo.id)).allowed:
        raise TenancyError("not_found", "No such folder.", 404)
    if perm != "file.read":
        az.require(perm, ws, nodes_for_folder(s, fo.id))
    return fo, ws


# ---- quotas ---------------------------------------------------------------------------------------
def storage_used(s, org_id: str, workspace_id: str | None = None) -> int:
    q = select(func.coalesce(func.sum(FileVersion.size), 0)).join(File, File.id == FileVersion.file_id).where(FileVersion.org_id == org_id)
    if workspace_id:
        q = q.where(File.workspace_id == workspace_id)
    return int(s.execute(q).scalar() or 0)


def check_quota(s, az: authz.Authz, ws: Workspace, add: int) -> None:
    org = az.org(ws.org_id)
    for scope, quotas, used in (("organisation", org.quotas, None), ("workspace", ws.quotas, ws.id)):
        mb = (quotas or {}).get("storage_mb")
        if mb and storage_used(s, ws.org_id, used) + add > mb * 1024 * 1024:
            raise TenancyError("quota", f"This {scope}'s storage limit of {mb} MB would be exceeded.", 413)


def max_upload(az: authz.Authz, ws: Workspace) -> int:
    mb = (ws.quotas or {}).get("max_upload_mb") or (az.org(ws.org_id).quotas or {}).get("max_upload_mb") or 100
    return int(mb) * 1024 * 1024


# ---- folders -------------------------------------------------------------------------------------
def create_folder(s, az: authz.Authz, ws_id: str, name: str, parent_id: str | None = None) -> Folder:
    ws = workspace_of(s, ws_id, az)
    if parent_id:
        parent, _ = get_folder(s, az, parent_id, "file.write")
        if parent.workspace_id != ws.id:
            raise TenancyError("invalid", "That folder is in another workspace.")
        if len(folder_chain(s, parent_id)) >= MAX_DEPTH:
            raise TenancyError("invalid", "Folders are nested too deeply.")
    else:
        az.require("file.write", ws)
    name = clean_name(name)
    if s.execute(select(Folder.id).where(Folder.workspace_id == ws.id, Folder.parent_id == parent_id, Folder.name == name,
                                         Folder.deleted_at.is_(None))).first():
        raise TenancyError("exists", "A folder with that name already exists here.", 409)
    fo = Folder(org_id=ws.org_id, workspace_id=ws.id, parent_id=parent_id, name=name, created_by=az.user_id)
    s.add(fo)
    s.flush()
    audit.record(s, az.user_id, "folder.create", fo.id, {"ws": ws.id}, org_id=ws.org_id)
    return fo


def set_folder(s, az: authz.Authz, folder_id: str, *, name: str | None = None, parent_id: str | None = None,
               move: bool = False, restricted: bool | None = None) -> Folder:
    fo, ws = get_folder(s, az, folder_id, "file.write")
    if restricted is not None:
        az.require("ws.members.manage", ws, nodes_for_folder(s, fo.id))  # restricting is an access decision
    if move:
        if parent_id:
            if parent_id in {f.id for f in folder_chain(s, fo.id)}:
                raise TenancyError("invalid", "A folder cannot be moved into itself.")
            tgt, _ = get_folder(s, az, parent_id, "file.write")
            if tgt.workspace_id != fo.workspace_id:
                raise TenancyError("invalid", "That folder is in another workspace.")
        else:
            az.require("file.write", ws)
    new_name = clean_name(name) if name is not None else None
    if new_name is not None:
        fo.name = new_name
    if move:
        fo.parent_id = parent_id
    if restricted is not None:
        fo.restricted = restricted
    audit.record(s, az.user_id, "folder.update", fo.id, {"ws": ws.id, "restricted": fo.restricted}, org_id=ws.org_id)
    return fo


# ---- files ---------------------------------------------------------------------------------------
def _log(s, az_user: str, f: File, n: int, action: str, via: str = "user") -> None:
    s.add(FileAccess(org_id=f.org_id, workspace_id=f.workspace_id, file_id=f.id, version_n=n, user_id=az_user, action=action, via=via))


def create_file(st, s, az: authz.Authz, ws_id: str, name: str, data: bytes, *, folder_id: str | None = None,
                source: str = "upload", note: str = "", job_id: str | None = None, via: str = "user") -> tuple[File, FileVersion]:
    ws = workspace_of(s, ws_id, az)
    if folder_id:
        fo, _ = get_folder(s, az, folder_id, "file.write")
        if fo.workspace_id != ws.id:
            raise TenancyError("invalid", "That folder is in another workspace.")
    else:
        az.require("file.write", ws)
    name = clean_name(name)
    if len(data) > max_upload(az, ws):
        raise TenancyError("too_large", f"Files are limited to {max_upload(az, ws) // 1048576} MB here.", 413)
    kind, mime = vet_upload(name, data, allow_executables=False)
    check_quota(s, az, ws, len(data))
    if s.execute(select(File.id).where(File.workspace_id == ws.id, File.folder_id == folder_id, File.name == name,
                                       File.deleted_at.is_(None))).first():
        raise TenancyError("exists", "A file with that name already exists here. Upload it as a new version instead.", 409)
    f = File(org_id=ws.org_id, workspace_id=ws.id, folder_id=folder_id, name=name, mime=mime, kind=kind, size=len(data),
             current=1, created_by=az.user_id)
    s.add(f)
    s.flush()
    v = _add_version(st, s, f, data, 1, mime, source, note, job_id, az.user_id)
    audit.record(s, az.user_id, "file.create", f.id, {"ws": ws.id, "size": len(data), "kind": kind, "via": via}, org_id=ws.org_id)
    return f, v


def _add_version(st, s, f: File, data: bytes, n: int, mime: str, source: str, note: str, job_id, user_id: str) -> FileVersion:
    v = FileVersion(file_id=f.id, org_id=f.org_id, n=n, size=len(data), sha256=hashlib.sha256(data).hexdigest(), mime=mime,
                    source=source, note=note[:300], job_id=job_id, created_by=user_id)
    s.add(v)
    s.flush()
    store_for(st).put(f.org_id, v.id, data)
    from . import ingest_svc  # local: ingest imports this module
    ingest_svc.enqueue(s, f, v, user_id)
    return v


def new_version(st, s, az: authz.Authz, file_id: str, data: bytes, *, note: str = "", source: str = "edit",
                job_id: str | None = None) -> FileVersion:
    f, ws = get_file(s, az, file_id, "file.write")
    if len(data) > max_upload(az, ws):
        raise TenancyError("too_large", f"Files are limited to {max_upload(az, ws) // 1048576} MB here.", 413)
    cur = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n == f.current)).scalar_one()
    if cur.sha256 == hashlib.sha256(data).hexdigest():
        return cur  # identical content: no new version (the only deduplication, and it stays inside one file)
    _, mime = vet_upload(f.name, data)
    check_quota(s, az, ws, len(data))
    n = (s.execute(select(func.max(FileVersion.n)).where(FileVersion.file_id == f.id)).scalar() or 0) + 1
    v = _add_version(st, s, f, data, n, mime, source, note, job_id, az.user_id)
    f.current, f.size, f.updated_at, f.mime = n, len(data), time.time(), mime
    audit.record(s, az.user_id, "file.version", f.id, {"ws": ws.id, "n": n, "source": source}, org_id=ws.org_id)
    return v


def read_file(st, s, az: authz.Authz, file_id: str, *, version: int | None = None, action: str = "read",
              via: str = "user") -> tuple[File, FileVersion, bytes]:
    perm = "file.download" if action in ("download", "preview") else "file.read"
    f, ws = get_file(s, az, file_id, perm)
    n = version or f.current
    v = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n == n)).scalar_one_or_none()
    if v is None:
        raise TenancyError("not_found", "No such version.", 404)
    if version and version != f.current:
        az.require("file.restore", ws, nodes_for_file(s, f))  # older versions are history: editors and up
    data = store_for(st).get(f.org_id, v.id)
    _log(s, az.user_id, f, n, action, via)
    return f, v, data


def restore_version(st, s, az: authz.Authz, file_id: str, n: int) -> FileVersion:
    f, ws = get_file(s, az, file_id, "file.restore")
    old = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n == n)).scalar_one_or_none()
    if old is None:
        raise TenancyError("not_found", "No such version.", 404)
    data = store_for(st).get(f.org_id, old.id)
    check_quota(s, az, ws, len(data))
    nn = (s.execute(select(func.max(FileVersion.n)).where(FileVersion.file_id == f.id)).scalar() or 0) + 1
    v = _add_version(st, s, f, data, nn, old.mime, "restore", f"restored version {n}", None, az.user_id)
    f.current, f.size, f.updated_at = nn, len(data), time.time()
    audit.record(s, az.user_id, "file.restore_version", f.id, {"ws": ws.id, "from": n, "to": nn}, org_id=ws.org_id)
    return v


def rename_or_move(s, az: authz.Authz, file_id: str, *, name: str | None = None, folder_id: str | None = None,
                   move: bool = False, restricted: bool | None = None, tags: list | None = None) -> File:
    f, ws = get_file(s, az, file_id, "file.write")
    if restricted is not None:
        az.require("ws.members.manage", ws, nodes_for_file(s, f))
    target = f.folder_id
    if move:
        if folder_id:
            tgt, _ = get_folder(s, az, folder_id, "file.write")
            if tgt.workspace_id != f.workspace_id:
                raise TenancyError("invalid", "That folder is in another workspace.")
        else:
            az.require("file.write", ws)
        target = folder_id
    new_name = clean_name(name) if name is not None else f.name
    if (new_name, target) != (f.name, f.folder_id) and s.execute(select(File.id).where(
            File.workspace_id == f.workspace_id, File.folder_id == target, File.name == new_name, File.deleted_at.is_(None),
            File.id != f.id)).first():
        raise TenancyError("exists", "A file with that name already exists there.", 409)
    f.name, f.folder_id = new_name, target
    if restricted is not None:
        f.restricted = restricted
    if tags is not None:
        f.tags = [str(t)[:40] for t in tags][:20]
    f.updated_at = time.time()
    audit.record(s, az.user_id, "file.update", f.id, {"ws": ws.id, "restricted": f.restricted}, org_id=ws.org_id)
    return f


def trash_file(s, az: authz.Authz, file_id: str) -> None:
    f, ws = get_file(s, az, file_id, "file.delete")
    f.deleted_at, f.deleted_by = time.time(), az.user_id
    audit.record(s, az.user_id, "file.trash", f.id, {"ws": ws.id}, org_id=ws.org_id)


def untrash_file(s, az: authz.Authz, file_id: str) -> File:
    f, ws = get_file(s, az, file_id, "file.restore", include_deleted=True)
    if not f.deleted_at:
        return f
    if s.execute(select(File.id).where(File.workspace_id == f.workspace_id, File.folder_id == f.folder_id, File.name == f.name,
                                       File.deleted_at.is_(None))).first():
        raise TenancyError("exists", "A file with that name now exists there; rename one first.", 409)
    f.deleted_at, f.deleted_by = None, ""
    audit.record(s, az.user_id, "file.untrash", f.id, {"ws": ws.id}, org_id=ws.org_id)
    return f


def list_children(s, az: authz.Authz, ws_id: str, folder_id: str | None = None, *, trash: bool = False) -> dict:
    """Folders and files directly in a folder, filtered to what the caller may read."""
    ws = workspace_of(s, ws_id, az)
    here = nodes_for_folder(s, folder_id)
    if folder_id:
        fo = s.get(Folder, folder_id)
        if fo is None or fo.workspace_id != ws.id or not az.check("file.read", ws, here).allowed:
            raise TenancyError("not_found", "No such folder.", 404)
    elif not az.check("file.read", ws).allowed:
        raise TenancyError("not_found", "No such workspace.", 404)
    folders = [fo for fo in s.execute(select(Folder).where(Folder.workspace_id == ws.id, Folder.parent_id == folder_id,
                                                           Folder.deleted_at.is_(None)).order_by(Folder.name)).scalars()
               if az.check("file.read", ws, [authz.Node("folder", fo.id, fo.restricted), *here]).allowed]
    q = select(File).where(File.workspace_id == ws.id, File.folder_id == folder_id)
    q = q.where(File.deleted_at.is_not(None)) if trash else q.where(File.deleted_at.is_(None))
    files = [f for f in s.execute(q.order_by(File.name)).scalars()
             if az.check("file.read", ws, [authz.Node("file", f.id, f.restricted, f.created_by), *here]).allowed]
    return {"folders": folders, "files": files, "path": [(f.id, f.name) for f in reversed(folder_chain(s, folder_id))]}


def all_files(s, az: authz.Authz, ws_id: str, limit: int = 2000) -> list[tuple[File, str]]:
    """Every readable, non-deleted file of the workspace with its folder path ("Reports / 2026"), for pickers."""
    ws = workspace_of(s, ws_id, az)
    folders = {f.id: f for f in s.execute(select(Folder).where(Folder.workspace_id == ws.id)).scalars()}

    def path(fid):
        names, seen = [], set()
        while fid and fid in folders and fid not in seen:
            seen.add(fid)
            names.append(folders[fid].name)
            fid = folders[fid].parent_id
        return " / ".join(reversed(names))
    out = []
    for f in s.execute(select(File).where(File.workspace_id == ws.id, File.deleted_at.is_(None)).order_by(File.name).limit(limit * 3)).scalars():
        if az.check("file.read", ws, nodes_for_file(s, f)).allowed:
            out.append((f, path(f.folder_id)))
            if len(out) >= limit:
                break
    return out


def search_names(s, az: authz.Authz, ws_id: str, q: str, limit: int = 50) -> list[File]:
    ws = workspace_of(s, ws_id, az)
    like = f"%{q.strip().replace('%', '').replace('_', '')}%"
    out = []
    for f in s.execute(select(File).where(File.workspace_id == ws.id, File.deleted_at.is_(None), File.name.ilike(like))
                       .order_by(File.updated_at.desc()).limit(500)).scalars():
        if az.check("file.read", ws, nodes_for_file(s, f)).allowed:
            out.append(f)
            if len(out) >= limit:
                break
    return out


def purge_trash(st, s, *, older_than_days: float) -> int:
    """Retention: permanently remove trashed files (all versions, blobs and text). Run by the sweeper."""
    cutoff = time.time() - older_than_days * 86400
    n = 0
    store = store_for(st)
    for f in s.execute(select(File).where(File.deleted_at.is_not(None), File.deleted_at < cutoff)).scalars().all():
        purge_file(store, s, f)
        n += 1
    return n


def purge_file(store: FileStore, s, f: File) -> None:
    from .models_files import Chunk, DocText
    for v in s.execute(select(FileVersion).where(FileVersion.file_id == f.id)).scalars().all():
        store.delete(f.org_id, v.id)
    s.query(Chunk).filter(Chunk.file_id == f.id).delete()
    s.query(DocText).filter(DocText.file_id == f.id).delete()
    audit.record(s, "system", "file.purge", f.id, {"ws": f.workspace_id}, org_id=f.org_id)
    s.delete(f)
    s.flush()


def sweep(st, s) -> dict:
    """Configurable retention, per organisation (Org.policy): trash_days (default 30), keep_versions (default: all),
    job_days (default 30), access_log_days (default 365)."""
    from .models_files import Job
    from .models_tenancy import Org
    now = time.time()
    out = {"purged": 0, "versions": 0, "jobs": 0, "access": 0}
    store = store_for(st)
    for org in s.execute(select(Org)).scalars().all():
        pol = org.policy or {}
        for f in s.execute(select(File).where(File.org_id == org.id, File.deleted_at.is_not(None),
                                              File.deleted_at < now - float(pol.get("trash_days", 30)) * 86400)).scalars().all():
            purge_file(store, s, f)
            out["purged"] += 1
        keep = pol.get("keep_versions")
        if keep and int(keep) >= 1:
            for f in s.execute(select(File).where(File.org_id == org.id, File.deleted_at.is_(None))).scalars().all():
                old = s.execute(select(FileVersion).where(FileVersion.file_id == f.id, FileVersion.n != f.current)
                                .order_by(FileVersion.n.desc()).offset(int(keep) - 1)).scalars().all()
                for v in old:
                    store.delete(org.id, v.id)
                    s.delete(v)
                    out["versions"] += 1
        out["jobs"] += s.query(Job).filter(Job.org_id == org.id, Job.finished_at.is_not(None),
                                           Job.finished_at < now - float(pol.get("job_days", 30)) * 86400).delete()
        out["access"] += s.query(FileAccess).filter(FileAccess.org_id == org.id,
                                                    FileAccess.ts < now - float(pol.get("access_log_days", 365)) * 86400).delete()
    return out
