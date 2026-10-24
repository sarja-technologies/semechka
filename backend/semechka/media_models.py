"""Tables for image/video generation. Kept out of models.py so the media feature merges cleanly."""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class MediaJob(Base):
    """One generation request. For no-save requests the prompt and parameters are never stored (null)."""
    __tablename__ = "media_jobs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("mj_"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # image | video
    model_id: Mapped[str] = mapped_column(String(32))
    backend_id: Mapped[str] = mapped_column(String(32))
    remote_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="queued", index=True)
    save: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_enc: Mapped[str | None] = mapped_column(Text)
    params_enc: Mapped[str | None] = mapped_column(Text)  # JSON of the generation parameters, encrypted
    progress: Mapped[float | None] = mapped_column(Float)
    progress_text: Mapped[str] = mapped_column(String(300), default="")
    queue_position: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(400))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    started_at: Mapped[float | None] = mapped_column(Float)
    finished_at: Mapped[float | None] = mapped_column(Float)
    last_ok: Mapped[float] = mapped_column(Float, default=now)  # last time the backend answered a status poll
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    expires_at: Mapped[float | None] = mapped_column(Float, index=True)  # retention: prompt and parameters go with it
    conversation_id: Mapped[str | None] = mapped_column(String(32))  # set when the job was started from a conversation
    message_id: Mapped[str | None] = mapped_column(String(32))  # the assistant message that shows the result


class MediaAsset(Base):
    """A stored result. The bytes live encrypted under data_dir/assets/<user_id>/; never at a public URL."""
    __tablename__ = "media_assets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("ma_"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(8))
    model_id: Mapped[str] = mapped_column(String(32))
    mime: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float | None] = mapped_column(Float)
    seed: Mapped[int | None] = mapped_column(Integer)
    prompt_enc: Mapped[str] = mapped_column(Text, default="")
    params_enc: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    expires_at: Mapped[float | None] = mapped_column(Float, index=True)
