"""Work Mode: sessions, messages, the event timeline, artifacts and skills (docs/WORK_MODE.md, section 2).

A session is a sequence of AgentRuns (one per user message, `agent_runs.session_id`) that share one container, one
workspace volume and one conversation. Content (objective, state, messages, event payloads) is encrypted with the
organisation's key exactly like agent runs; summaries on events are short and never carry secrets.
"""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class WorkSession(Base):
    __tablename__ = "work_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("ws_"))
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    objective_enc: Mapped[str] = mapped_column(Text, default="")
    state_enc: Mapped[str] = mapped_column(Text, default="")  # the bounded durable "session state" block (JSON, encrypted)
    model_id: Mapped[str | None] = mapped_column(String(32))  # Semechka Model.id of the main agent
    agent_id: Mapped[str | None] = mapped_column(String(32))  # the hidden per-org Work agent
    # idle | running | awaiting_approval | paused | failed | cancelled
    status: Mapped[str] = mapped_column(String(20), default="idle", index=True)
    container_name: Mapped[str] = mapped_column(String(64), default="")
    volume_name: Mapped[str] = mapped_column(String(64), default="")
    container_state: Mapped[str] = mapped_column(String(12), default="absent")  # absent | stopped | running | error
    has_workspace: Mapped[bool] = mapped_column(Boolean, default=False)  # a container (and so a volume) was created at least once
    network: Mapped[str] = mapped_column(String(10), default="none")  # none | internet
    autonomy: Mapped[str] = mapped_column(String(10), default="contained")  # contained | ask
    skill_ids: Mapped[list] = mapped_column(JSON, default=list)
    limits: Mapped[dict] = mapped_column(JSON, default=dict)  # clamped to the platform ceilings when read
    active_run_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    last_active_at: Mapped[float] = mapped_column(Float, default=now)
    deleted_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (Index("ix_work_sessions_user", "user_id", "deleted_at"),)


class WorkMessage(Base):
    __tablename__ = "work_messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("wm_"))
    session_id: Mapped[str] = mapped_column(ForeignKey("work_sessions.id", ondelete="CASCADE"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(10))  # user | assistant | system
    content_enc: Mapped[str] = mapped_column(Text, default="")
    run_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("session_id", "seq", name="uq_work_msg_seq"),)


class WorkEvent(Base):
    """The timeline the UI polls (`since=`). `id` is monotonic, so it is monotonic per session too."""
    __tablename__ = "work_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("work_sessions.id", ondelete="CASCADE"), index=True)
    # message | tool_call | tool_result | job | delegate | artifact | approval | state | error
    kind: Mapped[str] = mapped_column(String(16))
    run_id: Mapped[str | None] = mapped_column(String(32))
    step_n: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(String(300), default="")
    payload_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (Index("ix_work_events_sid_id", "session_id", "id"),)


class WorkArtifact(Base):
    __tablename__ = "work_artifacts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("wa_"))
    session_id: Mapped[str] = mapped_column(ForeignKey("work_sessions.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(String(500))  # relative to /work
    name: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(100), default="application/octet-stream")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    stored_asset: Mapped[str] = mapped_column(String(80), default="")  # encrypted copy outside the container (survives it)
    created_by_step: Mapped[int | None] = mapped_column(Integer)
    mtime: Mapped[float] = mapped_column(Float, default=0)  # of the file in the workspace when it was copied out
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    saved_to_library: Mapped[bool] = mapped_column(Boolean, default=False)
    library_asset_id: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (UniqueConstraint("session_id", "path", name="uq_work_artifact_path"),)


class WorkSkill(Base):
    """Created by migration 11; its API belongs to a later phase (docs/WORK_MODE.md section 5)."""
    __tablename__ = "work_skills"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("sk_"))
    scope: Mapped[str] = mapped_column(String(8))  # org | user
    org_id: Mapped[str | None] = mapped_column(String(32), index=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200), default="")
    description: Mapped[str] = mapped_column(String(400), default="")
    category: Mapped[str] = mapped_column(String(40), default="general")
    body_enc: Mapped[str] = mapped_column(Text, default="")  # Markdown, up to 64 KB, encrypted
    files_enc: Mapped[str] = mapped_column(Text, default="")  # small supporting files (JSON), up to 1 MB, encrypted
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("scope", "org_id", "owner_user_id", "name", name="uq_work_skill_name"),)
