"""Engine status, queue and model load/unload (see lifecycle_svc.py for the rules).

Reading is split by permission: memory and hardware need a hardware or system permission, which models are loaded
needs `models.status.view` (or the right to load them), the engine queue needs `ops.queue.view`. Operating needs
`ops.model.load` and is refused, with the reason, whenever it would disturb someone's generation.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import adapters, lifecycle_svc
from ..deps import Ctx, audit_ctx, require, require_any
from ..models import Backend, Model

router = APIRouter(prefix="/api")

LOAD = "ops.model.load"


def _err(status, code, message, **kw):
    return HTTPException(status, {"code": code, "message": message, **kw})


def _supports(b: Backend) -> dict:
    cls = adapters.REGISTRY.get(b.adapter)
    m = cls.manifest if cls else None
    return {"load": bool(m and m.model_load), "unload": bool(m and m.model_unload),
            "status": bool(cls and cls.status is not adapters.Adapter.status),
            "queue": bool(cls and cls.engine_jobs is not adapters.Adapter.engine_jobs)}


def _visible_status(ctx: Ctx, status: dict | None) -> dict | None:
    if status is None:
        return None
    p = ctx.principal
    out: dict = {"health": status.get("health")}
    if p.can("tech.runtime.version") or p.can("admin.system.view"):
        out["engine"] = status.get("engine")
    if p.can("tech.resources.gpu") or p.can("admin.system.view") or p.can(LOAD):
        out["memory"] = status.get("memory")
    if p.can("models.status.view") or p.can(LOAD):
        out["llm"] = status.get("llm")
        out["worker"] = status.get("worker")
    if p.can("ops.queue.view") or p.can(LOAD) or p.can("tech.queue.position"):
        out["jobs"] = status.get("jobs")
    return out


@router.get("/engines")
async def engines(ctx: Ctx = Depends(require_any(LOAD, "ops.queue.view", "admin.system.view"))):
    """Each enabled engine's live state, the load/unload in progress, and (with ops.queue.view) its job queue."""
    backends = list(ctx.s.execute(select(Backend).where(Backend.enabled == True)).scalars())  # noqa: E712
    names = {(m.backend_id, lifecycle_svc.engine_id(m.real_id)): {"id": m.id, "alias": m.alias}
             for m in ctx.s.execute(select(Model)).scalars()}
    out = []
    for b in backends:
        sup = _supports(b)
        status = await lifecycle_svc.engine_state(ctx.st, b) if sup["status"] else None
        lifecycle_svc.reconcile(ctx.s, b, status)
        queue = None
        if sup["queue"] and (ctx.principal.can("ops.queue.view") or ctx.principal.can(LOAD)):
            queue = await lifecycle_svc.engine_queue(ctx.st, ctx.s, b)
            for j in queue or []:
                j["model_ref"] = names.get((b.id, j.get("model") or ""))
        vis = _visible_status(ctx, status)
        if vis and vis.get("llm") and vis["llm"].get("model_id"):
            vis["llm"] = {**vis["llm"], "model_ref": names.get((b.id, vis["llm"]["model_id"]))}
        if vis and vis.get("worker"):
            vis["worker"] = {**vis["worker"], "model_refs": [names.get((b.id, x)) for x in vis["worker"].get("loaded") or []]}
        here = ctx.st.scheduler.model_load(b.id)
        out.append({"id": b.id, "name": b.name, "adapter": b.adapter, "supports": sup, "reachable": status is not None,
                    "status": vis, "queue": queue,
                    "this_server": {"running": sum(v["running"] for v in here.values()),
                                    "waiting": sum(v["waiting"] for v in here.values())},
                    "operation": lifecycle_svc.public_op(ctx.st.lifecycle.get(b.id))})
    ctx.s.commit()
    return {"engines": out}


def _model(ctx: Ctx, mid: str) -> tuple[Model, Backend]:
    m = ctx.s.get(Model, mid)
    if m is None:
        raise _err(404, "not_found", "No such model.")
    return m, ctx.s.get(Backend, m.backend_id)


@router.get("/admin/models/{mid}/lifecycle")
async def model_lifecycle_state(mid: str, ctx: Ctx = Depends(require(LOAD))):
    m, b = _model(ctx, mid)
    status = await lifecycle_svc.engine_state(ctx.st, b) if _supports(b)["status"] else None
    lifecycle_svc.reconcile(ctx.s, b, status)
    ctx.s.commit()
    return {"state": lifecycle_svc.model_state(m, status), "kind": lifecycle_svc.kind_of(m),
            "load": lifecycle_svc.check(ctx.st, m, b, "load", status),
            "unload": lifecycle_svc.check(ctx.st, m, b, "unload", status),
            "operation": lifecycle_svc.public_op(ctx.st.lifecycle.get(b.id))}


@router.post("/admin/models/{mid}/{op}")
async def model_lifecycle(mid: str, op: str, ctx: Ctx = Depends(require(LOAD))):
    """load/unload: operating infrastructure, a separate permission from managing the catalogue."""
    if op not in ("load", "unload"):
        raise _err(404, "not_found", "Unknown operation.")
    m, b = _model(ctx, mid)
    status = await lifecycle_svc.engine_state(ctx.st, b) if _supports(b)["status"] else None
    verdict = lifecycle_svc.check(ctx.st, m, b, op, status)
    if verdict["noop"] and verdict["allowed"]:
        return {"ok": True, "noop": verdict["noop"], "operation": None}
    if not verdict["allowed"]:
        audit_ctx(ctx, f"model.{op}.refused", mid, {"model": lifecycle_svc.engine_id(m.real_id),
                                                     "reason": verdict["blockers"][0][:200]})
        ctx.s.commit()
        code = verdict["code"]
        raise _err({"unsupported": 501, "unreachable": 502}.get(code, 409), code, verdict["blockers"][0],
                   blockers=verdict["blockers"], warnings=verdict["warnings"])
    audit_ctx(ctx, f"model.{op}", mid, {"model": lifecycle_svc.engine_id(m.real_id)})
    ctx.s.commit()
    rec = await lifecycle_svc.run(ctx.st, b, m, op, ctx.user.id, ctx.ip)
    if op == "unload" and rec["state"] == "failed":
        busy = rec["error_kind"] == "busy"
        raise _err(409 if busy else 502, "busy" if busy else "engine_error", rec["error"] or "The engine refused.")
    return {"ok": True, "operation": rec, "warnings": verdict["warnings"]}
