"""Organisations, groups, workspaces and access-control entries (see docs/ENTERPRISE.md, D1-D4).

Only adds tables, so existing databases get them from `create_all`; migration 8 backfills the default organisation.
Names here are administrative metadata and are stored in the clear so they can be listed and searched; anything a
person wrote as content (files, notes, memories) is encrypted with a per-organisation key.
"""
from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base
from .models import now, uid


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("o_"))
    slug: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | suspended
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # the organisation created for an existing install
    auto_join: Mapped[bool] = mapped_column(Boolean, default=False)  # new platform users become members (single-tenant use)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)  # ceiling: {"deny": [perm patterns], "models": ..., ...}
    quotas: Mapped[dict] = mapped_column(JSON, default=dict)  # {"storage_mb": n, "workspaces": n, "agents": n, ...}
    created_at: Mapped[float] = mapped_column(Float, default=now)


class OrgRole(Base):
    """An editable role of one organisation. `scope` says where it can be assigned: on the organisation or a workspace."""
    __tablename__ = "org_roles"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("or_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(String(48))  # stable template key, or a slug for custom roles
    scope: Mapped[str] = mapped_column(String(12))  # org | workspace
    label: Mapped[str] = mapped_column(String(80))
    description: Mapped[str] = mapped_column(Text, default="")
    grants: Mapped[list] = mapped_column(JSON, default=list)
    denies: Mapped[list] = mapped_column(JSON, default=list)
    system: Mapped[bool] = mapped_column(Boolean, default=False)  # a shipped template: editable, not deletable
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_org_role_key"),)


class OrgMember(Base):
    __tablename__ = "org_members"
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    status: Mapped[str] = mapped_column(String(12), default="active")  # active | suspended
    role_ids: Mapped[list] = mapped_column(JSON, default=list)  # OrgRole ids with scope=org
    added_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class Group(Base):
    """A department, team or ad-hoc group. Groups nest; membership in a sub-group counts as membership of its parents."""
    __tablename__ = "org_groups"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("g_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("org_groups.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12), default="department")  # department | team | group
    created_at: Mapped[float] = mapped_column(Float, default=now)


class GroupMember(Base):
    __tablename__ = "org_group_members"
    group_id: Mapped[str] = mapped_column(ForeignKey("org_groups.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False)  # department administrator


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("w_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text, default="")
    visibility: Mapped[str] = mapped_column(String(8), default="private")  # private | shared (visible to the org; no content)
    inherit_members: Mapped[bool] = mapped_column(Boolean, default=True)  # members of the parent are members here
    policy: Mapped[dict] = mapped_column(JSON, default=dict)  # ceiling for everyone here: {"deny": [perm patterns]}
    quotas: Mapped[dict] = mapped_column(JSON, default=dict)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)


class WorkspaceMember(Base):
    """A user or group holding one workspace role in one workspace."""
    __tablename__ = "workspace_members"
    workspace_id: Mapped[str] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True)
    principal_type: Mapped[str] = mapped_column(String(8), primary_key=True)  # user | group
    principal_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    role_id: Mapped[str] = mapped_column(ForeignKey("org_roles.id", ondelete="CASCADE"), primary_key=True)
    added_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (Index("ix_wsm_principal", "principal_type", "principal_id"),)


class AclEntry(Base):
    """An explicit allow or deny on one resource (workspace, folder, file, context, agent). Deny always wins."""
    __tablename__ = "acl_entries"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=lambda: uid("ac_"))
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    resource_type: Mapped[str] = mapped_column(String(16))
    resource_id: Mapped[str] = mapped_column(String(32))
    principal_type: Mapped[str] = mapped_column(String(8))  # user | group
    principal_id: Mapped[str] = mapped_column(String(32))
    effect: Mapped[str] = mapped_column(String(5))  # allow | deny
    perms: Mapped[list] = mapped_column(JSON, default=list)
    created_by: Mapped[str] = mapped_column(String(32), default="")
    created_at: Mapped[float] = mapped_column(Float, default=now)
    __table_args__ = (Index("ix_acl_resource", "resource_type", "resource_id"),)
