"""Usage records (per organisation / workspace) and platform diagnostics. Counts and measurements only: no content."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from .. import embed_svc, gateway_svc, sandbox_svc, services, usage_svc
from ..deps import Ctx, current, require
from ..models_files import Job
from .orgs import org_for, ws_for

router = APIRouter(prefix="/api")


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
def workspace_models(wid: str, mode: str = "chat", ctx: Ctx = Depends(current)):
    """The model gateway's answer: models this person may use here for this purpose, with real capabilities."""
    if mode not in services.MODE_CAPABILITY and mode != "vision":
        raise HTTPException(422, {"code": "invalid", "message": "Unknown mode."})
    a, w = ws_for(ctx, wid, "ws.view")
    return gateway_svc.models_for(ctx.st, ctx.s, ctx.principal, a.org(w.org_id), w, mode)
