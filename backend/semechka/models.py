"""Relational schema. Content columns (messages, titles) are stored encrypted (see crypto.Vault)."""
from __future__ import annotations

import time
import uuid

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def uid(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:20]


def now() -> float:
    return time.time()


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("u_"))
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    # active | pending_approval | pending_verification | suspended | deleted
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    totp_secret_enc: Mapped[str | None] = mapped_column(Text)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[float] = mapped_column(Float, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_login_at: Mapped[float | None] = mapped_column(Float)
    prefs: Mapped[dict] = mapped_column(JSON, default=dict)
    roles: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    teams: Mapped[list["TeamMember"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class Role(Base):
    """Editable template. `id` is the stable internal identifier; `label` is freely renameable."""
    __tablename__ = "roles"
    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    rank: Mapped[int] = mapped_column(Integer, default=100)  # lower = higher precedence for settings merge
    grants: Mapped[list] = mapped_column(JSON, default=list)
    denies: Mapped[list] = mapped_column(JSON, default=list)
    system: Mapped[bool] = mapped_column(Boolean, default=False)  # protected from deletion


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("roles.id", ondelete="CASCADE"), primary_key=True)


class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("t_"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    grants: Mapped[list] = mapped_column(JSON, default=list)
    denies: Mapped[list] = mapped_column(JSON, default=list)


class TeamMember(Base):
    __tablename__ = "team_members"
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class UserOverride(Base):
    """Direct per-user permission override (grant or deny)."""
    __tablename__ = "user_overrides"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    grants: Mapped[list] = mapped_column(JSON, default=list)
    denies: Mapped[list] = mapped_column(JSON, default=list)


class SessionRow(Base):
    __tablename__ = "sessions"
    id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    csrf: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_seen: Mapped[float] = mapped_column(Float, default=now)
    expires_at: Mapped[float] = mapped_column(Float)
    ip: Mapped[str] = mapped_column(String(64), default="")
    ua: Mapped[str] = mapped_column(String(200), default="")
    mfa_ok: Mapped[bool] = mapped_column(Boolean, default=True)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("i_"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    email: Mapped[str | None] = mapped_column(String(320))
    role_id: Mapped[str] = mapped_column(String(48))
    team_id: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[float] = mapped_column(Float)
    max_uses: Mapped[int] = mapped_column(Integer, default=1)
    uses: Mapped[int] = mapped_column(Integer, default=0)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class SettingValue(Base):
    __tablename__ = "setting_values"
    key: Mapped[str] = mapped_column(String(120), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16), primary_key=True)  # deployment|role|team|user|model
    scope_id: Mapped[str] = mapped_column(String(48), primary_key=True, default="")
    value: Mapped[dict] = mapped_column(JSON)  # {"v": <value>} (or {"enc": ...} for secrets)
    updated_at: Mapped[float] = mapped_column(Float, default=now)
    updated_by: Mapped[str] = mapped_column(String(32), default="")


class SettingRevision(Base):
    __tablename__ = "setting_revisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, default=now, index=True)
    actor: Mapped[str] = mapped_column(String(32))
    key: Mapped[str] = mapped_column(String(120), index=True)
    scope_type: Mapped[str] = mapped_column(String(16))
    scope_id: Mapped[str] = mapped_column(String(48), default="")
    old: Mapped[dict | None] = mapped_column(JSON)
    new: Mapped[dict | None] = mapped_column(JSON)
    batch: Mapped[str] = mapped_column(String(32), default="")


class Backend(Base):
    """A configured inference backend instance (adapter + endpoint)."""
    __tablename__ = "backends"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("b_"))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    adapter: Mapped[str] = mapped_column(String(32))  # sarness | ollama | openai_compat
    base_url: Mapped[str] = mapped_column(String(500))
    api_key_enc: Mapped[str | None] = mapped_column(Text)
    external: Mapped[bool] = mapped_column(Boolean, default=False)  # content leaves the local network
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Model(Base):
    __tablename__ = "models"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("m_"))
    backend_id: Mapped[str] = mapped_column(ForeignKey("backends.id", ondelete="CASCADE"), index=True)
    real_id: Mapped[str] = mapped_column(String(300))
    alias: Mapped[str] = mapped_column(String(120))
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    capabilities: Mapped[list] = mapped_column(JSON, default=list)  # chat, vision, tools, embeddings, ...
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # only fields the backend actually reported
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    gen_limits: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"max_tokens": 2048}
    allow_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    available: Mapped[bool] = mapped_column(Boolean, default=True)  # last discovery saw it
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (UniqueConstraint("backend_id", "real_id"),)


class ModelGrant(Base):
    __tablename__ = "model_grants"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model_id: Mapped[str] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"), index=True)
    subject_type: Mapped[str] = mapped_column(String(8))  # role|team|user
    subject_id: Mapped[str] = mapped_column(String(48))
    effect: Mapped[str] = mapped_column(String(8))  # allow|deny
    __table_args__ = (UniqueConstraint("model_id", "subject_type", "subject_id"),)


class Conversation(Base):
    __tablename__ = "conversations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("c_"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title_enc: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(String(32))
    mode: Mapped[str] = mapped_column(String(16), default="chat")
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    active_leaf: Mapped[str | None] = mapped_column(String(32))
    retention_note: Mapped[str] = mapped_column(String(200), default="")
    expires_at: Mapped[float | None] = mapped_column(Float, index=True)
    created_at: Mapped[float] = mapped_column(Float, default=now)
    updated_at: Mapped[float] = mapped_column(Float, default=now, index=True)
    messages: Mapped[list["Message"]] = relationship(cascade="all, delete-orphan", lazy="select")


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("g_"))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), index=True)
    role: Mapped[str] = mapped_column(String(12))
    content_enc: Mapped[str] = mapped_column(Text)
    model_id: Mapped[str | None] = mapped_column(String(32))
    stats: Mapped[dict] = mapped_column(JSON, default=dict)  # full measured stats; filtered on read by permission
    status: Mapped[str] = mapped_column(String(16), default="complete")  # complete|stopped|error
    created_at: Mapped[float] = mapped_column(Float, default=now)


class UsageEvent(Base):
    __tablename__ = "usage_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, default=now, index=True)
    user_id: Mapped[str] = mapped_column(String(32), index=True)
    model_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default="chat")
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    tokens_source: Mapped[str] = mapped_column(String(16), default="unavailable")  # backend|estimate|unavailable
    duration_s: Mapped[float | None] = mapped_column(Float)
    ephemeral: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(16), default="complete")


class AuditEvent(Base):
    """Hash-chained audit log. No prompt or message content is ever written here."""
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[float] = mapped_column(Float, default=now, index=True)
    actor: Mapped[str] = mapped_column(String(32), default="")
    action: Mapped[str] = mapped_column(String(80), index=True)
    target: Mapped[str] = mapped_column(String(120), default="")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    ip: Mapped[str] = mapped_column(String(64), default="")
    prev_hash: Mapped[str] = mapped_column(String(64), default="")
    hash: Mapped[str] = mapped_column(String(64), default="")


class Meta(Base):
    __tablename__ = "meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


Index("ix_msgs_conv_parent", Message.conversation_id, Message.parent_id)

# --- knowledge fork: tables for collections, assistants and conversation context live in their own module ---
from . import models_knowledge  # noqa: E402,F401


class EmailToken(Base):
    __tablename__ = "email_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # verify | reset
    expires_at: Mapped[float] = mapped_column(Float)
