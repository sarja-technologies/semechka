"""Cloud agents: configuration, runs, approvals and memory. Authority lives in agent_svc / authz."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from .. import agent_svc as A
from .. import audit
from ..deps import Ctx, current
from ..models_agents import Agent, AgentMemory, AgentRun, AgentStep, AgentVersion
from ..models_files import Job
from ..models_tenancy import Workspace
from .orgs import az, run, ws_for

router = APIRouter(prefix="/api")


def _agent(a: Agent, st=None, full=False) -> dict:
    d = {"id": a.id, "workspace_id": a.workspace_id, "name": a.name, "description": a.description, "model_id": a.model_id,
         "tools": a.tools, "context_ids": a.context_ids, "memory": A.memory_cfg(a), "limits": {**A.DEFAULT_LIMITS, **(a.limits or {})},
         "approval": a.approval, "network": a.network, "version": a.version, "archived": a.archived,
         "created_at": a.created_at, "updated_at": a.updated_at}
    if full and st is not None:
        d["instructions"] = A.instructions_of(st, a)
    return d


@router.get("/agent-tools")
def tools(ctx: Ctx = Depends(current)):
    return [{"name": t.name, "summary": t.summary, "args": t.args, "permissions": list(t.perms), "sensitive": t.sensitive}
            for t in A.TOOLS.values()]


class AgentIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    instructions: str = Field(default="", max_length=20000)
    model_id: str | None = None
    tools: list[str] | None = None
    context_ids: list[str] | None = None
    memory: dict | None = None
    limits: dict | None = None
    approval: str | None = None
    network: dict | None = None


def _cfg(body: BaseModel) -> dict:
    return {k: v for k, v in body.model_dump(exclude_unset=True).items()
            if k in ("tools", "context_ids", "memory", "limits", "approval", "network") and v is not None}


@router.get("/workspaces/{wid}/agents")
def list_agents(wid: str, ctx: Ctx = Depends(current)):
    _a, w = ws_for(ctx, wid, "agent.view")
    return [_agent(x) for x in ctx.s.execute(select(Agent).where(Agent.workspace_id == w.id, Agent.archived == False)
                                              .order_by(Agent.name)).scalars()]


@router.post("/workspaces/{wid}/agents")
def new_agent(wid: str, body: AgentIn, ctx: Ctx = Depends(current)):
    a = run(A.create_agent, ctx.st, ctx.s, az(ctx), wid, body.name, description=body.description,
            instructions=body.instructions, model_id=body.model_id, **_cfg(body))
    return _agent(a, ctx.st, True)


@router.get("/agents/{aid}")
def get_agent(aid: str, ctx: Ctx = Depends(current)):
    a, w = run(A.get_agent, ctx.s, az(ctx), aid)
    manage = az(ctx).check("agent.manage", w).allowed
    d = _agent(a, ctx.st, full=manage)  # instructions are configuration: shown to people who can change them
    d["can_manage"] = manage
    return d


class AgentPatch(AgentIn):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    instructions: str | None = Field(default=None, max_length=20000)
    archived: bool | None = None


@router.patch("/agents/{aid}")
def patch_agent(aid: str, body: AgentPatch, ctx: Ctx = Depends(current)):
    extra = {"model_id": body.model_id} if "model_id" in body.model_fields_set else {}
    a = run(A.update_agent, ctx.st, ctx.s, az(ctx), aid, name=body.name, description=body.description,
            instructions=body.instructions, archived=body.archived, **extra, **_cfg(body))
    return _agent(a, ctx.st, True)


@router.get("/agents/{aid}/versions")
def agent_versions(aid: str, ctx: Ctx = Depends(current)):
    a, _ = run(A.get_agent, ctx.s, az(ctx), aid, "agent.manage")
    rows = ctx.s.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id).order_by(AgentVersion.n.desc())).scalars()
    return [{"n": v.n, "changed_by": v.changed_by, "created_at": v.created_at,
             "config": {k: x for k, x in v.config.items() if k != "instructions_enc"}} for v in rows]


# ---- runs ----------------------------------------------------------------------------------------------
class RunIn(BaseModel):
    task: str = Field(min_length=1, max_length=20000)


@router.post("/agents/{aid}/runs")
def start(aid: str, body: RunIn, ctx: Ctx = Depends(current)):
    r = run(A.start_run, ctx.st, ctx.s, az(ctx), aid, body.task, ctx.principal)
    return {"run_id": r.id, "job_id": r.job_id}


def _run(ctx: Ctx, r: AgentRun, detail=False) -> dict:
    job = ctx.s.get(Job, r.job_id)
    steps = ctx.s.execute(select(AgentStep).where(AgentStep.run_id == r.id).order_by(AgentStep.n)).scalars().all()
    mine = r.user_id == ctx.user.id
    d = {"id": r.id, "agent_id": r.agent_id, "agent_version": r.agent_version, "workspace_id": r.workspace_id, "user_id": r.user_id,
         "status": job.status if job else "unknown", "error": job.error if job else "", "created_at": r.created_at,
         "started_at": job.started_at if job else None, "finished_at": job.finished_at if job else None, "usage": r.usage,
         "artifacts": r.artifacts, "sources": r.sources,
         "steps": [{"n": s.n, "kind": s.kind, "tool": s.tool, "status": s.status, "error": s.error,
                    "seconds": (s.meta or {}).get("seconds"), "model": (s.meta or {}).get("model"),
                    "tokens": ((s.meta or {}).get("usage") or None)} for s in steps]}
    if mine:  # the task, the answer and step contents are the invoker's; managers see progress only
        d["task"] = A._dec(ctx.st, r.org_id, r.task_enc)
        d["final"] = A._dec(ctx.st, r.org_id, r.final_enc)
        if detail:
            for out, s in zip(d["steps"], steps, strict=True):
                out["args"] = A._dec(ctx.st, r.org_id, s.args_enc) if s.kind == "tool" else ""
                out["result"] = A._dec(ctx.st, r.org_id, s.result_enc)
    return d


@router.get("/runs/{rid}")
def get_run(rid: str, detail: bool = False, ctx: Ctx = Depends(current)):
    r, _ = run(A.get_run, ctx.s, az(ctx), rid)
    return _run(ctx, r, detail)


@router.get("/workspaces/{wid}/runs")
def list_runs(wid: str, limit: int = 30, ctx: Ctx = Depends(current)):
    a, w = ws_for(ctx, wid, "ws.view")
    q = select(AgentRun).where(AgentRun.workspace_id == w.id).order_by(AgentRun.created_at.desc()).limit(max(1, min(limit, 100)))
    if not a.check("job.manage_all", w).allowed:
        q = q.where(AgentRun.user_id == ctx.user.id)
    return [_run(ctx, r) for r in ctx.s.execute(q).scalars()]


@router.post("/runs/{rid}/cancel")
def cancel_run(rid: str, ctx: Ctx = Depends(current)):
    r, _ = run(A.get_run, ctx.s, az(ctx), rid)
    from .. import jobs_svc
    run(jobs_svc.cancel, ctx.s, az(ctx), r.job_id)
    return _run(ctx, r)


class ApproveIn(BaseModel):
    step: int
    approve: bool


@router.post("/runs/{rid}/approve")
def approve(rid: str, body: ApproveIn, ctx: Ctx = Depends(current)):
    s = run(A.decide_approval, ctx.s, az(ctx), rid, body.step, body.approve)
    return {"step": s.n, "status": s.status}


# ---- memory ----------------------------------------------------------------------------------------------
class MemIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


def _mem(st, a: Agent, m: AgentMemory) -> dict:
    return {"id": m.id, "text": A._dec(st, a.org_id, m.text_enc), "source": m.source, "run_id": m.run_id,
            "created_by": m.created_by, "created_at": m.created_at, "updated_at": m.updated_at, "expires_at": m.expires_at}


@router.get("/agents/{aid}/memory")
def list_memory(aid: str, ctx: Ctx = Depends(current)):
    a, w = run(A.get_agent, ctx.s, az(ctx), aid)
    run(az(ctx).require, "mem.read", w)
    return {"persistent": A.memory_cfg(a)["persistent"], "items": [_mem(ctx.st, a, m) for m, _ in A.live_memory(ctx.s, ctx.st, a)]}


@router.post("/agents/{aid}/memory")
def add_memory(aid: str, body: MemIn, ctx: Ctx = Depends(current)):
    a, w = run(A.get_agent, ctx.s, az(ctx), aid)
    run(az(ctx).require, "mem.write", w)
    cfg = A.memory_cfg(a)
    if not cfg["persistent"]:
        raise HTTPException(409, {"code": "off", "message": "Persistent memory is switched off for this agent."})
    m = AgentMemory(org_id=a.org_id, workspace_id=a.workspace_id, agent_id=a.id, source="user", created_by=ctx.user.id,
                    text_enc=A._enc(ctx.st, a.org_id, body.text),
                    expires_at=time.time() + cfg["retention_days"] * 86400 if cfg["retention_days"] else None)
    ctx.s.add(m)
    ctx.s.flush()
    audit.record(ctx.s, ctx.user.id, "memory.save", m.id, {"agent": a.id, "via": "user"}, ctx.ip, org_id=a.org_id)
    return _mem(ctx.st, a, m)


def _mem_for(ctx: Ctx, mid: str) -> tuple[AgentMemory, Agent, Workspace]:
    m = ctx.s.get(AgentMemory, mid)
    a = ctx.s.get(Agent, m.agent_id) if m and m.agent_id else None
    w = ctx.s.get(Workspace, m.workspace_id) if m else None
    if m is None or a is None or w is None or not az(ctx).check("mem.read", w).allowed:
        raise HTTPException(404, {"code": "not_found", "message": "No such memory."})
    run(az(ctx).require, "mem.write", w)
    return m, a, w


@router.patch("/memory/{mid}")
def edit_memory(mid: str, body: MemIn, ctx: Ctx = Depends(current)):
    m, a, _ = _mem_for(ctx, mid)
    m.text_enc, m.updated_at, m.source = A._enc(ctx.st, a.org_id, body.text), time.time(), "user"
    audit.record(ctx.s, ctx.user.id, "memory.edit", m.id, {"agent": a.id}, ctx.ip, org_id=a.org_id)
    return _mem(ctx.st, a, m)


@router.delete("/memory/{mid}")
def delete_memory(mid: str, ctx: Ctx = Depends(current)):
    m, a, _ = _mem_for(ctx, mid)
    audit.record(ctx.s, ctx.user.id, "memory.delete", m.id, {"agent": a.id}, ctx.ip, org_id=a.org_id)
    ctx.s.delete(m)
    return {"ok": True}
