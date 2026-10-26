"""Workspace filesystem, durable jobs, extracted text, contexts (docs/ENTERPRISE.md, M3).

Only adds tables. File bodies live on disk, encrypted with the organisation's key (purpose `org:<id>:files`, bound to the
version id); extracted text, chunks and anything a person wrote are encrypted columns (purpose `org:<id>:content`).
File and folder names are administrative metadata, stored in the clear so they can be listed and searched.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class Folder(Base):
    __tablename__ = "ws_folders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("fo_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("ws_folders.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)  # stops inherited access (authz, restricted items)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    deleted_at: Mapped[float | None] = mapped_column(Float)


class File(Base):
    __tablename__ = "ws_files"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("fi_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    folder_id: Mapped[str | None] = mapped_column(ForeignKey("ws_folders.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(120), default="application/octet-stream")
    kind: Mapped[str] = mapped_column(String(16), default="other")  # document | sheet | slides | text | code | data | image | video | audio | archive | other
    size: Mapped[int] = mapped_column(Integer, default=0)  # of the current version
    current: Mapped[int] = mapped_column(Integer, default=1)  # current version number
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    deleted_at: Mapped[float | None] = mapped_column(Float)  # trash; purged by retention
    deleted_by: Mapped[str] = mapped_column(String(32), default="")
    __table_args__ = (Index("ix_files_ws_folder", "workspace_id", "folder_id"),)


class FileVersion(Base):
    __tablename__ = "ws_file_versions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("fv_"))
    file_id: Mapped[str] = mapped_column(ForeignKey("ws_files.id", ondelete="CASCADE"), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    n: Mapped[int] = mapped_column(Integer)
    size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    mime: Mapped[str] = mapped_column(String(120), default="")
    source: Mapped[str] = mapped_column(String(12), default="upload")  # upload | edit | restore | agent | import
    note: Mapped[str] = mapped_column(String(300), default="")
    job_id: Mapped[str | None] = mapped_column(String(32))  # the agent job that produced it, if any
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("file_id", "n", name="uq_version_n"),)


class FileAccess(Base):
    """Document access record: who read or downloaded what, and through which path (a person, an agent, a job)."""
    __tablename__ = "ws_file_access"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, default=now, index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    file_id: Mapped[str] = mapped_column(String(32), index=True)
    version_n: Mapped[int] = mapped_column(Integer, default=0)
    user_id: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(12))  # read | download | preview | retrieve
    via: Mapped[str] = mapped_column(String(48), default="user")


class DocText(Base):
    """Text extracted from one file version. Kept apart from the original, which is never modified."""
    __tablename__ = "ws_doc_text"
    version_id: Mapped[str] = mapped_column(ForeignKey("ws_file_versions.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    file_id: Mapped[str] = mapped_column(String(32), index=True)
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | ok | unsupported | failed
    extractor: Mapped[str] = mapped_column(String(32), default="")
    n_chars: Mapped[int] = mapped_column(Integer, default=0)
    text_enc: Mapped[str] = mapped_column(Text, default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # pages, sheets, summary flags - never content
    error: Mapped[str] = mapped_column(String(300), default="")
    index_version: Mapped[int] = mapped_column(Integer, default=0)  # chunker/extractor generation that produced it
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Chunk(Base):
    """Retrieval passages of a file's CURRENT version. Replaced whenever the current version changes."""
    __tablename__ = "ws_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    file_id: Mapped[str] = mapped_column(String(32), index=True)
    version_id: Mapped[str] = mapped_column(String(32))
    ord: Mapped[int] = mapped_column(Integer)
    loc: Mapped[str] = mapped_column(String(80), default="")  # "p. 3", "sheet Budget", for citations
    text_enc: Mapped[str] = mapped_column(Text)
    embedding: Mapped[bytes | None] = mapped_column()  # float32 vector, encrypted; NULL until an embedding model exists
    embed_model: Mapped[str] = mapped_column(String(80), default="")


class Job(Base):
    """Durable background work. State lives here, never in memory, so a restart or a closed browser loses nothing."""
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("j_"))
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(24), index=True)  # ingest | agent | export | ...
    created_by: Mapped[str] = mapped_column(String(32), index=True)  # the authority the job runs with
    # queued | running | awaiting_approval | succeeded | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=100)  # lower runs first
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # references only (ids); never document content
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    progress: Mapped[float | None] = mapped_column(Float)  # only when the work can really report it
    progress_text: Mapped[str] = mapped_column(String(200), default="")
    error: Mapped[str] = mapped_column(String(300), default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (Index("ix_jobs_queue", "status", "priority", "created_at"),)


# ---- contexts ------------------------------------------------------------------------------------
class Context(Base):
    """A persistent, named knowledge collection: a set of files (and pinned notes) that agents and people can search."""
    __tablename__ = "contexts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("cx_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)  # home: owns its access rules
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    restricted: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on every change to the item set or its content
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class ContextItem(Base):
    __tablename__ = "context_items"
    context_id: Mapped[str] = mapped_column(ForeignKey("contexts.id", ondelete="CASCADE"), primary_key=True)
    file_id: Mapped[str] = mapped_column(ForeignKey("ws_files.id", ondelete="CASCADE"), primary_key=True)
    pinned_version: Mapped[int | None] = mapped_column(Integer)  # NULL = follow the file's current version
    added_by: Mapped[str] = mapped_column(String(32), default="")
    added_at: Mapped[float] = mapped_column(Float, default=now)


class ContextAttachment(Base):
    """A workspace using a context that lives in another workspace of the same organisation."""
    __tablename__ = "context_attachments"
    context_id: Mapped[str] = mapped_column(ForeignKey("contexts.id", ondelete="CASCADE"), primary_key=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    attached_by: Mapped[str] = mapped_column(String(32), default="")
    attached_at: Mapped[float] = mapped_column(Float, default=now)


class ContextVersion(Base):
    __tablename__ = "context_versions"
    context_id: Mapped[str] = mapped_column(ForeignKey("contexts.id", ondelete="CASCADE"), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{"file_id", "version"}] as of this version
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
