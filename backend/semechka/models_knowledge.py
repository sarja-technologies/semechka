"""Knowledge collections, assistants and per-conversation context.

Kept in its own module and only ever adds tables, so existing databases upgrade by `create_all`.
All human-readable text (names, descriptions, prompts, document text, chunks) is stored encrypted.
"""
from __future__ import annotations

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class Collection(Base):
    """A set of documents. Personal when team_id is NULL; otherwise readable by members of that team."""
    __tablename__ = "kn_collections"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("k_"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    name_enc: Mapped[str] = mapped_column(Text)
    description_enc: Mapped[str] = mapped_column(Text)
    version: Mapped[int] = mapped_column(Integer, default=1)  # bumped on every content change (index cache key)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class KDocument(Base):
    __tablename__ = "kn_documents"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("d_"))
    collection_id: Mapped[str] = mapped_column(ForeignKey("kn_collections.id", ondelete="CASCADE"), index=True)
    uploader_id: Mapped[str] = mapped_column(String(32))
    filename_enc: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # text | pdf
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    n_chars: Mapped[int] = mapped_column(Integer, default=0)
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class KChunk(Base):
    __tablename__ = "kn_chunks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(ForeignKey("kn_documents.id", ondelete="CASCADE"), index=True)
    collection_id: Mapped[str] = mapped_column(String(32), index=True)
    ord: Mapped[int] = mapped_column(Integer)
    text_enc: Mapped[str] = mapped_column(Text)


class Assistant(Base):
    """A saved system prompt + defaults. It cannot call tools, run code or browse: it only shapes the model's reply."""
    __tablename__ = "assistants"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("a_"))
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    visibility: Mapped[str] = mapped_column(String(8), default="private")  # private | team | org
    team_id: Mapped[str | None] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), index=True)
    name_enc: Mapped[str] = mapped_column(Text)
    description_enc: Mapped[str] = mapped_column(Text)
    prompt_enc: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(String(32))
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    collection_ids: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now)


class ConversationContext(Base):
    """Which assistant / collections a saved conversation uses. Absent for plain chats."""
    __tablename__ = "conversation_contexts"
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True)
    assistant_id: Mapped[str | None] = mapped_column(String(32))
    collection_ids: Mapped[list] = mapped_column(JSON, default=list)


class MessageSources(Base):
    """Passages that were put in front of the model for one reply (encrypted JSON: file names and excerpts)."""
    __tablename__ = "message_sources"
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), primary_key=True)
    sources_enc: Mapped[str] = mapped_column(Text)


Index("ix_kn_chunk_doc_ord", KChunk.doc_id, KChunk.ord)
