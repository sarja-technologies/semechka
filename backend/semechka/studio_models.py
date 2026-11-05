"""OpenJutsu Studio: the table behind a project. Additive (a new table, created by ``create_all``; no migration, nothing existing is altered).

A project is the person's non-destructive edit of a video: which video, the trim/crop/frame/resolution decisions, any number of reference
pictures, what to replace, which model. The editing decisions (the "spec") are encrypted like every other piece of media parameters; the media
files themselves stay ordinary library assets and are never owned, changed or deleted by a project.
"""
from __future__ import annotations

from sqlalchemy import Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class StudioProject(Base):
    __tablename__ = "studio_projects"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("sp_"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title_enc: Mapped[str] = mapped_column(Text, default="")
    spec_enc: Mapped[str] = mapped_column(Text, default="")      # JSON of the version-1 spec, encrypted
    state_enc: Mapped[str] = mapped_column(Text, default="")     # JSON {"jobs": [job ids, newest first]}, encrypted
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, index=True)
