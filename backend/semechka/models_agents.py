"""Cloud agents: persistent identities, versions, runs, durable steps and memory (docs/ENTERPRISE.md, M4/M6).

An agent is a row, not a container. Runs and steps are written as they happen so a crashed or restarted worker can
resume, and so every model call, tool call and approval can be inspected afterwards. Instructions, task text, step
arguments/results and memory are encrypted with the organisation's key.
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


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("ag_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    instructions_enc: Mapped[str] = mapped_column(Text, default="")
    model_id: Mapped[str | None] = mapped_column(String(32))  # Semechka Model.id; NULL = the organisation's default
    tools: Mapped[list] = mapped_column(JSON, default=list)  # names from agent_svc.TOOLS the agent may call
    context_ids: Mapped[list] = mapped_column(JSON, default=list)  # contexts it may search; ["*"] = every one its workspace reaches
    memory: Mapped[dict] = mapped_column(JSON, default=dict)  # {"persistent": bool, "max_items": n, "retention_days": n|None}
    limits: Mapped[dict] = mapped_column(JSON, default=dict)  # {"max_steps", "timeout_s", "max_observation_chars"}
    approval: Mapped[str] = mapped_column(String(12), default="sensitive")  # never | sensitive | always
    network: Mapped[dict] = mapped_column(JSON, default=dict)  # {"allow": [hostnames]}; empty = no network
    version: Mapped[int] = mapped_column(Integer, default=1)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(12), default="cloud")  # cloud | work (the hidden per-org Work Mode agent)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class AgentVersion(Base):
    """Configuration history. Instructions stay encrypted inside the snapshot."""
    __tablename__ = "agent_versions"
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), primary_key=True)
    n: Mapped[int] = mapped_column(Integer, primary_key=True)
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    changed_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class AgentRun(Base):
    __tablename__ = "agent_runs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("ar_"))
    job_id: Mapped[str] = mapped_column(String(32), index=True)
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    agent_version: Mapped[int] = mapped_column(Integer, default=1)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)  # the authority the run acts with
    task_enc: Mapped[str] = mapped_column(Text)
    final_enc: Mapped[str] = mapped_column(Text, default="")
    artifacts: Mapped[list] = mapped_column(JSON, default=list)  # [{"file_id", "name", "version"}]
    sources: Mapped[list] = mapped_column(JSON, default=list)  # citations gathered from retrieval and file reads
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    session_id: Mapped[str | None] = mapped_column(String(32), index=True)  # Work Mode: the session this run belongs to
    created_at: Mapped[float] = mapped_column(Float, default=now)


class AgentStep(Base):
    __tablename__ = "agent_steps"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("agent_runs.id", ondelete="CASCADE"), index=True)
    n: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(12))  # model | tool | approval
    tool: Mapped[str] = mapped_column(String(40), default="")
    args_enc: Mapped[str] = mapped_column(Text, default="")
    result_enc: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="running")  # running | done | error | denied | awaiting_approval | interrupted
    error: Mapped[str] = mapped_column(String(300), default="")
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # model alias, token counts, durations - never content
    started_at: Mapped[float] = mapped_column(Float, default=now)
    finished_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("run_id", "n", name="uq_step_n"), Index("ix_steps_run_n", "run_id", "n"))


class AgentMemory(Base):
    """Explicitly saved knowledge. Never written implicitly from a conversation; every item can be inspected, edited,
    deleted, and expires under the agent's retention policy."""
    __tablename__ = "agent_memory"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("am_"))
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    workspace_id: Mapped[str] = mapped_column(String(32), index=True)
    agent_id: Mapped[str | None] = mapped_column(String(32), index=True)  # NULL = workspace-level note
    text_enc: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(12), default="agent")  # agent | user
    run_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float | None] = mapped_column(Float)
