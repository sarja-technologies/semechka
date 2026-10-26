"""Tenant-aware operations for the person who runs the server (not available through the web API, because an
organisation's owner must not be able to read its confidential content, and a platform administrator must not either).

  export_org: one organisation's data, decrypted, as a passphrase-sealed stream (SMCK2): people and structure, every
              workspace's current files and (optionally) history, contexts, agent configuration and memory.
  purge_org:  permanent removal of one organisation: database rows, search passages, vectors, file blobs. Other
              organisations are untouched. The audit log keeps its metadata-only records (it holds no content).
Both write an audit event.
"""
from __future__ import annotations

import io
import json
import shutil
import tarfile
import time
from pathlib import Path

from sqlalchemy import select

from . import audit, files_svc
from .crypto import SealWriter
from .models import User
from .models_agents import Agent, AgentMemory, AgentRun, AgentStep, AgentVersion
from .models_files import (
    Chunk,
    Context,
    ContextItem,
    DocText,
    File,
    FileAccess,
    FileVersion,
    Folder,
    Job,
)
from .models_tenancy import (
    AclEntry,
    Group,
    GroupMember,
    Org,
    OrgMember,
    OrgRole,
    Workspace,
    WorkspaceMember,
)


def find_org(s, slug: str) -> Org:
    o = s.execute(select(Org).where((Org.slug == slug) | (Org.id == slug))).scalar_one_or_none()
    if o is None:
        raise LookupError(f"No organisation '{slug}'.")
    return o


def _safe(name: str) -> str:
    return "".join(c if c.isalnum() or c in " ._-" else "_" for c in name).strip(" .") or "unnamed"


def _folder_path(folders: dict, fid: str | None) -> str:
    parts, seen = [], set()
    while fid and fid in folders and fid not in seen:
        seen.add(fid)
        parts.append(_safe(folders[fid].name))
        fid = folders[fid].parent_id
    return "/".join(reversed(parts))


def _add(tf: tarfile.TarFile, name: str, data: bytes) -> None:
    ti = tarfile.TarInfo(name)
    ti.size, ti.mtime = len(data), int(time.time())
    tf.addfile(ti, io.BytesIO(data))


def export_org(st, s, org: Org, sink, passphrase: str, *, history: bool = True, actor: str = "operator") -> dict:
    store = files_svc.store_for(st)
    purpose = f"org:{org.id}:content"
    users = {u.id: u for u in s.execute(select(User)).scalars()}
    members = s.execute(select(OrgMember).where(OrgMember.org_id == org.id)).scalars().all()
    roles = s.execute(select(OrgRole).where(OrgRole.org_id == org.id)).scalars().all()
    wss = s.execute(select(Workspace).where(Workspace.org_id == org.id)).scalars().all()
    folders = {f.id: f for f in s.execute(select(Folder).where(Folder.org_id == org.id)).scalars()}
    manifest = {
        "format": "semechka-org-export/1", "created": time.time(),
        "organisation": {"name": org.name, "slug": org.slug, "policy": org.policy, "quotas": org.quotas},
        "roles": [{"key": r.key, "scope": r.scope, "label": r.label, "grants": r.grants, "denies": r.denies} for r in roles],
        "members": [{"email": users[m.user_id].email if m.user_id in users else "", "status": m.status,
                     "roles": [r.key for r in roles if r.id in (m.role_ids or [])]} for m in members],
        "groups": [{"name": g.name, "kind": g.kind, "parent": g.parent_id, "id": g.id} for g in s.execute(select(Group).where(Group.org_id == org.id)).scalars()],
        "workspaces": [], "counts": {"files": 0, "bytes": 0}}
    n_files = n_bytes = 0
    w = SealWriter(sink, passphrase)
    with tarfile.open(fileobj=w, mode="w|gz") as tf:
        for ws in wss:
            wsdir = f"workspaces/{_safe(ws.name)}-{ws.id[-6:]}"
            entry = {"id": ws.id, "name": ws.name, "description": ws.description, "visibility": ws.visibility, "parent": ws.parent_id,
                     "policy": ws.policy, "quotas": ws.quotas, "members": [], "contexts": [], "agents": [], "files": []}
            for m in s.execute(select(WorkspaceMember).where(WorkspaceMember.workspace_id == ws.id)).scalars():
                entry["members"].append({"principal": f"{m.principal_type}:{m.principal_id}", "role": next((r.key for r in roles if r.id == m.role_id), m.role_id)})
            for f in s.execute(select(File).where(File.workspace_id == ws.id, File.deleted_at.is_(None))).scalars():
                base = f"{wsdir}/files/{_folder_path(folders, f.folder_id)}/{_safe(f.name)}".replace("//", "/")
                vs = s.execute(select(FileVersion).where(FileVersion.file_id == f.id).order_by(FileVersion.n)).scalars().all()
                rec = {"name": f.name, "path": base, "version": f.current, "restricted": f.restricted, "tags": f.tags, "versions": []}
                for v in vs:
                    if v.n != f.current and not history:
                        continue
                    try:
                        data = store.get(org.id, v.id)
                    except (OSError, ValueError):
                        rec["versions"].append({"n": v.n, "missing": True})
                        continue
                    arc = base if v.n == f.current else f"{wsdir}/history/{_safe(f.name)}.v{v.n}"
                    _add(tf, arc, data)
                    rec["versions"].append({"n": v.n, "sha256": v.sha256, "source": v.source, "path": arc})
                    n_files += 1
                    n_bytes += len(data)
                entry["files"].append(rec)
            for c in s.execute(select(Context).where(Context.workspace_id == ws.id)).scalars():
                entry["contexts"].append({"name": c.name, "description": c.description, "restricted": c.restricted, "version": c.version,
                                          "files": [f.name for f in s.execute(select(File).join(ContextItem, ContextItem.file_id == File.id)
                                                                               .where(ContextItem.context_id == c.id)).scalars()]})
            for a in s.execute(select(Agent).where(Agent.workspace_id == ws.id)).scalars():
                mem = [st.vault.decrypt(x.text_enc, purpose) for x in s.execute(select(AgentMemory).where(AgentMemory.agent_id == a.id)).scalars()]
                entry["agents"].append({"name": a.name, "description": a.description, "instructions": st.vault.decrypt(a.instructions_enc, purpose) if a.instructions_enc else "",
                                        "tools": a.tools, "approval": a.approval, "memory_policy": a.memory, "limits": a.limits, "network": a.network, "memory": mem})
            manifest["workspaces"].append(entry)
        manifest["counts"] = {"files": n_files, "bytes": n_bytes}
        _add(tf, "manifest.json", json.dumps(manifest, indent=1, ensure_ascii=False).encode())
    w.close()
    audit.record(s, actor, "org.export", org.id, {"files": n_files, "bytes": n_bytes, "history": history}, org_id=org.id)
    return manifest["counts"]


def purge_org(st, s, org: Org, *, actor: str = "operator") -> dict:
    """Permanently remove everything that belongs to the organisation."""
    oid = org.id
    counts = {"files": s.query(File).filter(File.org_id == oid).count(), "workspaces": s.query(Workspace).filter(Workspace.org_id == oid).count(),
              "members": s.query(OrgMember).filter(OrgMember.org_id == oid).count()}
    run_ids = [r for (r,) in s.execute(select(AgentRun.id).where(AgentRun.org_id == oid))]
    if run_ids:
        s.query(AgentStep).filter(AgentStep.run_id.in_(run_ids)).delete(synchronize_session=False)
    for model in (AgentRun, AgentMemory, Chunk, DocText, FileAccess, Job):
        s.query(model).filter(model.org_id == oid).delete(synchronize_session=False)
    agent_ids = [a for (a,) in s.execute(select(Agent.id).where(Agent.org_id == oid))]
    if agent_ids:
        s.query(AgentVersion).filter(AgentVersion.agent_id.in_(agent_ids)).delete(synchronize_session=False)
    s.query(Agent).filter(Agent.org_id == oid).delete(synchronize_session=False)
    s.query(AclEntry).filter(AclEntry.org_id == oid).delete(synchronize_session=False)
    s.flush()
    ws_ids = [w for (w,) in s.execute(select(Workspace.id).where(Workspace.org_id == oid))]
    if ws_ids:
        s.query(GroupMember).filter(GroupMember.group_id.in_(select(Group.id).where(Group.org_id == oid))).delete(synchronize_session=False)
    s.expunge_all()
    s.delete(s.get(Org, oid))  # roles, members, groups, workspaces, folders, files, versions, contexts cascade
    s.flush()
    shutil.rmtree(Path(st.cfg.data_dir) / "workspaces" / oid, ignore_errors=True)
    audit.record(s, actor, "org.purge", oid, {"name_slug": org.slug, **counts}, org_id=None)
    return counts
