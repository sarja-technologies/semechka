"""Usage records (per organisation / workspace) and platform diagnostics. Counts and measurements only: no content."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from .. import (
    audit,
    embed_svc,
    files_svc,
    gateway_svc,
    sandbox_svc,
    services,
    usage_svc,
)
from ..deps import Ctx, cloud_gate, current, require
from ..models_files import Job
from ..models_tenancy import Org, OrgMember, Workspace
from .orgs import org_for, ws_for

router = APIRouter(prefix="/api", dependencies=[Depends(cloud_gate)])


@router.get("/orgs/{oid}/usage")
def org_usage(oid: str, ctx: Ctx = Depends(current)):
    a = org_for(ctx, oid, "org.policy.edit")
    return usage_svc.org_usage(ctx.s, a.org(oid))


@router.get("/workspaces/{wid}/usage")
def workspace_usage(wid: str, ctx: Ctx = Depends(current)):
    _, w = ws_for(ctx, wid, "ws.view")
    return usage_svc.workspace_usage(ctx.s, w)


@router.get("/admin/cloud/status")
def status(ctx: Ctx = Depends(require("admin.system.view"))):
    d = usage_svc.cloud_status(ctx.st, ctx.s)
    ecfg = embed_svc.config(ctx.st, ctx.s)
    d["embeddings"] = {"configured": ecfg is not None, "healthy": bool(ecfg and embed_svc.healthy(ctx.st, ecfg)), "model": ecfg.model if ecfg else None}
    sb = ctx.st.settings.deployment
    enabled = bool(sb(ctx.s, "sandbox.enabled"))
    d["workspaces_enabled"] = bool(sb(ctx.s, "cloud.workspaces.enabled"))
    d["rollout"] = sb(ctx.s, "cloud.rollout")
    d["sandbox"] = {"enabled": enabled, "agents_enabled": bool(sb(ctx.s, "cloud.agents.enabled"))}
    if enabled:
        ok, detail = sandbox_svc.available(sb(ctx.s, "sandbox.docker_host"), sb(ctx.s, "sandbox.image"))
        d["sandbox"].update(ready=ok, detail=detail, image=sb(ctx.s, "sandbox.image"))
    return d


@router.get("/admin/cloud/jobs")
def jobs(limit: int = 100, status: str | None = None, ctx: Ctx = Depends(require("ops.queue.view"))):
    """The platform queue as an operator sees it: kind, state, age and owner organisation - never what the job is about."""
    from sqlalchemy import select
    q = select(Job).order_by(Job.created_at.desc()).limit(max(1, min(limit, 500)))
    if status:
        q = q.where(Job.status == status)
    return [{"id": j.id, "kind": j.kind, "status": j.status, "org_id": j.org_id, "priority": j.priority, "attempts": j.attempts,
             "created_at": j.created_at, "started_at": j.started_at, "finished_at": j.finished_at, "error": j.error}
            for j in ctx.s.execute(q).scalars()]


@router.get("/workspaces/{wid}/models")
def workspace_models(wid: str, mode: str = "chat", scope: str = "workspace", ctx: Ctx = Depends(current)):
    """The model gateway's answer: models this person may use here for this purpose, with real capabilities.
    scope=org ignores the workspace's own allow-list (so its administrators can edit it)."""
    if mode not in services.MODE_CAPABILITY and mode != "vision":
        raise HTTPException(422, {"code": "invalid", "message": "Unknown mode."})
    a, w = ws_for(ctx, wid, "ws.policy.edit" if scope == "org" else "ws.view")
    return gateway_svc.models_for(ctx.st, ctx.s, ctx.principal, a.org(w.org_id), None if scope == "org" else w, mode)


@router.get("/orgs/{oid}/models")
def org_models(oid: str, ctx: Ctx = Depends(current)):
    """Models the platform offers, for the organisation's administrators to allow or withhold."""
    org_for(ctx, oid, "org.models.manage")
    return gateway_svc.platform_models(ctx.s)


@router.get("/admin/orgs")
def all_orgs(ctx: Ctx = Depends(require("admin.orgs.manage"))):
    """Every organisation on the platform: identity, state and size. Never anything inside one."""
    out = []
    for o in ctx.s.execute(select(Org).order_by(Org.name)).scalars():
        out.append({"id": o.id, "slug": o.slug, "name": o.name, "status": o.status, "is_default": o.is_default, "auto_join": o.auto_join,
                    "members": ctx.s.query(OrgMember).filter(OrgMember.org_id == o.id, OrgMember.status == "active").count(),
                    "workspaces": ctx.s.query(Workspace).filter(Workspace.org_id == o.id, Workspace.archived == False).count(),
                    "storage_bytes": files_svc.storage_used(ctx.s, o.id), "created_at": o.created_at})
    return out


class OrgState(BaseModel):
    status: str


@router.patch("/admin/orgs/{oid}")
def set_org_state(oid: str, body: OrgState, ctx: Ctx = Depends(require("admin.orgs.manage"))):
    """Suspending an organisation stops all access to it at once (every check starts with an active membership of an
    active organisation); nothing is deleted, and reactivating restores it exactly."""
    o = ctx.s.get(Org, oid)
    if o is None:
        raise HTTPException(404, {"code": "not_found", "message": "No such organisation."})
    if body.status not in ("active", "suspended"):
        raise HTTPException(422, {"code": "invalid", "message": "Status must be active or suspended."})
    o.status = body.status
    audit.record(ctx.s, ctx.user.id, "org.state", oid, {"status": body.status}, ctx.ip, org_id=oid)
    return {"id": o.id, "status": o.status}
