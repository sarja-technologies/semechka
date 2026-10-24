"""Queue visibility and control, backed by the live scheduler (scheduler.py). Nothing here is sampled or simulated."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from .. import audit
from ..deps import Ctx, current
from ..models import Backend

router = APIRouter(prefix="/api/queue")


def _names(ctx: Ctx) -> dict:
    return {b.id: b.name for b in ctx.s.execute(select(Backend)).scalars()}


@router.get("/mine")
async def mine(ctx: Ctx = Depends(current)):
    """The caller's own running and waiting requests."""
    snap = ctx.st.scheduler.snapshot(user_id=ctx.user.id)
    names = _names(ctx)
    return {"backends": [{**b, "backend": names.get(b["backend_id"], "")} for b in snap["backends"] if b["running"] or b["waiting"]]}


@router.get("/admin")
async def admin_view(ctx: Ctx = Depends(current)):
    ctx.need("admin.system.view")
    snap = ctx.st.scheduler.snapshot()
    names = _names(ctx)
    return {"backends": [{**b, "backend": names.get(b["backend_id"], b["backend_id"])} for b in snap["backends"]],
            "can_cancel": ctx.principal.can("ops.jobs.cancel_others"), "can_promote": ctx.principal.can("ops.jobs.reprioritise")}


@router.post("/tickets/{tid}/cancel")
async def cancel(tid: str, ctx: Ctx = Depends(current)):
    t = ctx.st.scheduler.tickets.get(tid)
    # Same answer for "no such ticket" and "someone else's ticket you may not touch": no probing.
    if t is None or (t.user_id != ctx.user.id and not ctx.principal.can("ops.jobs.cancel_others")):
        raise HTTPException(404, {"code": "not_found", "message": "That request is no longer in the queue."})
    if t.user_id == ctx.user.id:
        ctx.need("ops.jobs.cancel_own")
    by = "owner" if t.user_id == ctx.user.id else "admin"
    state = t.state
    if by == "admin":
        audit.record(ctx.s, ctx.user.id, "queue.cancel", t.backend_id, {"kind": t.kind, "was": state}, ctx.ip)
        # Commit BEFORE waking the cancelled stream: this handler runs on the event loop, and that stream writes to the
        # same database. Holding an open write transaction here would block the loop against its own lock.
        ctx.s.commit()
    ok = ctx.st.scheduler.cancel(tid, by=by)
    return {"ok": ok, "was": state}


@router.post("/tickets/{tid}/promote")
async def promote(tid: str, ctx: Ctx = Depends(current)):
    ctx.need("ops.jobs.reprioritise")
    t = ctx.st.scheduler.tickets.get(tid)
    if t is None or not ctx.st.scheduler.promote(tid):
        raise HTTPException(404, {"code": "not_found", "message": "That request is no longer waiting."})
    audit.record(ctx.s, ctx.user.id, "queue.promote", t.backend_id, {"kind": t.kind}, ctx.ip)
    ctx.s.commit()
    return {"ok": True}
