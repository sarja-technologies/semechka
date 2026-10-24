"""OIDC single sign-on tables. Kept out of models.py so the SSO slice merges cleanly."""
from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class SsoProvider(Base):
    __tablename__ = "sso_providers"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("sp_"))
    name: Mapped[str] = mapped_column(String(80), unique=True)
    issuer: Mapped[str] = mapped_column(String(500))
    client_id: Mapped[str] = mapped_column(String(300))
    client_secret_enc: Mapped[str | None] = mapped_column(Text)  # write-only; Vault purpose "sso"
    scopes: Mapped[str] = mapped_column(String(200), default="openid email profile")
    allowed_domains: Mapped[list] = mapped_column(JSON, default=list)
    email_claim: Mapped[str] = mapped_column(String(60), default="email")
    name_claim: Mapped[str] = mapped_column(String(60), default="name")
    groups_claim: Mapped[str] = mapped_column(String(60), default="groups")
    role_map: Mapped[dict] = mapped_column(JSON, default=dict)  # {idp group: [role ids]}
    default_role: Mapped[str] = mapped_column(String(48), default="member")
    require_approval: Mapped[bool] = mapped_column(Boolean, default=False)
    jit_create: Mapped[bool] = mapped_column(Boolean, default=True)
    assume_email_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # for IdPs that omit the claim
    allow_private_network: Mapped[bool] = mapped_column(Boolean, default=False)  # owner-only
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[float] = mapped_column(Float, default=now)


class SsoFlow(Base):
    """One in-flight sign-in. Single use, short lived, bound to the browser that started it."""
    __tablename__ = "sso_flows"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("sso_providers.id", ondelete="CASCADE"))
    nonce: Mapped[str] = mapped_column(String(64))
    verifier_enc: Mapped[str] = mapped_column(Text)
    binding_hash: Mapped[str] = mapped_column(String(64))
    redirect_uri: Mapped[str] = mapped_column(String(600))
    return_to: Mapped[str] = mapped_column(String(300), default="/")
    link_user_id: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[float] = mapped_column(Float)


class SsoIdentity(Base):
    __tablename__ = "sso_identities"
    __table_args__ = (UniqueConstraint("provider_id", "subject"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("sso_providers.id", ondelete="CASCADE"))
    subject: Mapped[str] = mapped_column(String(300))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    granted_roles: Mapped[list] = mapped_column(JSON, default=list)  # roles this identity's groups gave the user
    created_at: Mapped[float] = mapped_column(Float, default=now)
    last_login_at: Mapped[float | None] = mapped_column(Float)


class SsoSession(Base):
    """Which local sessions came from SSO (for RP-initiated logout). The ID token is stored encrypted."""
    __tablename__ = "sso_sessions"
    session_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(ForeignKey("sso_providers.id", ondelete="CASCADE"))
    id_token_enc: Mapped[str | None] = mapped_column(Text)
