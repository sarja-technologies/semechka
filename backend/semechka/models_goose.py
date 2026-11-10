"""Work Mode on upstream Goose: one session per conversation, and its durable event history (docs/WORK_MODE_GOOSE.md).

Additive tables only (created by `Base.metadata.create_all`; no existing table or column is touched, so rolling back to a
release without this module simply leaves them unused). Content (titles, event payloads) is encrypted with the organisation key.
"""
from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class GooseSession(Base):
    __tablename__ = "work_goose_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("wg_"))
    conversation_id: Mapped[str] = mapped_column(String(32), index=True)  # no FK on purpose: a conversation delete must not fail on us
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    org_id: Mapped[str] = mapped_column(String(32), index=True)
    goose_session_id: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")     # router model id
    autonomy: Mapped[str] = mapped_column(String(8), default="ask")  # ask | smart | auto
    mcp: Mapped[list] = mapped_column(JSON, default=list)
    skills: Mapped[list] = mapped_column(JSON, default=list)
    title_enc: Mapped[str] = mapped_column(Text, default="")
    container: Mapped[str] = mapped_column(String(64), default="")
    current_turn_id: Mapped[str | None] = mapped_column(String(32))   # set while a turn runs; found set after a crash => interrupted
    last_error: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    last_active_at: Mapped[float] = mapped_column(Float, default=now)
    deleted_at: Mapped[float | None] = mapped_column(Float)
    __table_args__ = (UniqueConstraint("conversation_id", name="uq_work_goose_conversation"),)


class GooseEvent(Base):
    """The timeline the browser streams. `id` is monotonic across the table, hence strictly increasing per session (the `seq`)."""
    __tablename__ = "work_goose_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("work_goose_sessions.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(24))
    turn_id: Mapped[str | None] = mapped_column(String(32))
    payload_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (Index("ix_work_goose_events_sid_id", "session_id", "id"),)
