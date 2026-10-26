"""Usage records and operational diagnostics, measured from what actually ran (no estimates, no content)."""
from __future__ import annotations

import os
import time

from sqlalchemy import func, select

from . import files_svc
from .models_agents import Agent, AgentRun, AgentStep
from .models_files import Context, File, FileVersion, Job
from .models_tenancy import Org, OrgMember, Workspace

DAY = 86400


def _runs(s, org_id: str, since: float, ws_id: str | None = None):
    q = select(AgentRun).where(AgentRun.org_id == org_id, AgentRun.created_at >= since)
    if ws_id:
        q = q.where(AgentRun.workspace_id == ws_id)
    return s.execute(q).scalars().all()


def _run_totals(runs) -> dict:
    t = {"runs": len(runs), "model_calls": 0, "tool_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "seconds": 0.0}
    for r in runs:
        u = r.usage or {}
        t["model_calls"] += u.get("model_calls", 0)
        t["tool_calls"] += u.get("tool_calls", 0)
        t["prompt_tokens"] += u.get("prompt_tokens", 0)
        t["completion_tokens"] += u.get("completion_tokens", 0)
        t["seconds"] += u.get("wall_s", 0)
    t["seconds"] = round(t["seconds"], 1)
    return t


def sandbox_seconds(s, org_id: str, since: float) -> float:
    """Measured wall time of sandbox calls (python/shell tool steps) for the organisation since `since`."""
    runs = {r.id for r in _runs(s, org_id, since - DAY)}
    total = 0.0
    for st_ in s.execute(select(AgentStep).where(AgentStep.run_id.in_(runs or {""}), AgentStep.tool.in_(("python", "shell")),
                                                 AgentStep.started_at >= since)).scalars():
        total += float((st_.meta or {}).get("seconds") or 0)
    return round(total, 1)


def org_usage(s, org: Org) -> dict:
    now = time.time()
    used = files_svc.storage_used(s, org.id)
    q = org.quotas or {}
    jobs = dict(s.execute(select(Job.status, func.count()).where(Job.org_id == org.id, Job.created_at >= now - DAY).group_by(Job.status)).all())
    return {
        "storage": {"used_bytes": used, "quota_mb": q.get("storage_mb"), "files": s.query(File).filter(File.org_id == org.id, File.deleted_at.is_(None)).count(),
                    "trashed": s.query(File).filter(File.org_id == org.id, File.deleted_at.is_not(None)).count()},
        "counts": {"members": s.query(OrgMember).filter(OrgMember.org_id == org.id, OrgMember.status == "active").count(),
                   "workspaces": s.query(Workspace).filter(Workspace.org_id == org.id, Workspace.archived == False).count(),
                   "contexts": s.query(Context).filter(Context.org_id == org.id, Context.archived == False).count(),
                   "agents": s.query(Agent).filter(Agent.org_id == org.id, Agent.archived == False).count()},
        "agent_runs": {"last_24h": _run_totals(_runs(s, org.id, now - DAY)), "last_30d": _run_totals(_runs(s, org.id, now - 30 * DAY)),
                       "sandbox_seconds_24h": sandbox_seconds(s, org.id, now - DAY)},
        "jobs_24h": jobs,
        "quotas": {k: q.get(k) for k in ("storage_mb", "workspaces", "agents", "contexts", "agent_runs_per_day", "max_upload_mb")},
    }


def workspace_usage(s, ws: Workspace) -> dict:
    now = time.time()
    return {"storage": {"used_bytes": files_svc.storage_used(s, ws.org_id, ws.id), "quota_mb": (ws.quotas or {}).get("storage_mb"),
                        "files": s.query(File).filter(File.workspace_id == ws.id, File.deleted_at.is_(None)).count()},
            "contexts": s.query(Context).filter(Context.workspace_id == ws.id, Context.archived == False).count(),
            "agents": s.query(Agent).filter(Agent.workspace_id == ws.id, Agent.archived == False).count(),
            "agent_runs_24h": _run_totals(_runs(s, ws.org_id, now - DAY, ws.id))}


def runs_today(s, org_id: str) -> int:
    return s.query(AgentRun).filter(AgentRun.org_id == org_id, AgentRun.created_at >= time.time() - DAY).count()


# ---- platform diagnostics (no content, no names) ------------------------------------------------------
def mem_available_mb() -> int | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


def cloud_status(st, s) -> dict:
    now = time.time()
    by = {}
    for kind, status, n in s.execute(select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)).all():
        by.setdefault(kind, {})[status] = n
    durations: dict[str, list[float]] = {}
    for kind, a, b in s.execute(select(Job.kind, Job.started_at, Job.finished_at).where(Job.finished_at.is_not(None), Job.started_at.is_not(None),
                                                                                        Job.finished_at >= now - DAY)).all():
        durations.setdefault(kind, []).append(b - a)
    perf = {}
    for kind, d in durations.items():
        d.sort()
        perf[kind] = {"finished_24h": len(d), "avg_s": round(sum(d) / len(d), 2), "p95_s": round(d[min(len(d) - 1, int(len(d) * 0.95))], 2), "max_s": round(d[-1], 2)}
    oldest = s.execute(select(func.min(Job.created_at)).where(Job.status == "queued")).scalar()
    mem = mem_available_mb()
    stored = int(s.execute(select(func.coalesce(func.sum(FileVersion.size), 0))).scalar() or 0)
    per_org = [{"org": o.slug, "bytes": files_svc.storage_used(s, o.id), "quota_mb": (o.quotas or {}).get("storage_mb")}
               for o in s.execute(select(Org)).scalars()]
    return {"jobs": by, "performance_24h": perf, "oldest_queued_age_s": round(now - oldest, 1) if oldest else None,
            "memory_available_mb": mem, "load_avg": [round(x, 2) for x in os.getloadavg()],
            "storage": {"total_bytes": stored, "organisations": sorted(per_org, key=lambda x: -x["bytes"])[:20]},
            "agent_runs_active": s.query(Job).filter(Job.kind == "agent", Job.status.in_(("queued", "running", "awaiting_approval"))).count()}
