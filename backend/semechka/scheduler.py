"""Generation scheduler: a real per-backend admission queue.

Every generation (chat today; image/video jobs can plug in through the same interface) asks for a *ticket* on one
backend. A backend runs at most `backend_limit` tickets at once and one person at most `user_limit` of them
while anyone else is waiting. Waiting tickets are served in this order:

    (priority tier, people-first fairness, arrival time)

* tier comes from the person's best role rank (lower rank = more privileged) and improves with waiting time
  (aging), so nobody waits forever behind a privileged flood; an operator can also promote a ticket by hand;
* fairness: among equal tiers the person with fewer running tickets goes first, so one person who queued ten
  requests does not starve someone who queued one;
* then plain FIFO.

Everything runs on the event loop (single thread), so no locks are needed. State is in memory: it is rebuilt empty
after a restart, which is correct because in-flight streams die with the process too.

The numbers surfaced to users and admins (positions, waits, depth) come straight from this structure.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

AGING_SECONDS = 60.0  # waiting this long moves a ticket up one priority tier


class QueueError(Exception):
    code = "queue_error"


class QueueFull(QueueError):
    code = "queue_full"


class QueueTimeout(QueueError):
    code = "queue_timeout"


class QueueCancelled(QueueError):
    code = "queue_cancelled"


def tier_for_rank(rank: int) -> int:
    """Role rank -> base tier (0 is served first). Matches the shipped role ranks: operators/admins <= 30."""
    return 0 if rank <= 30 else (1 if rank <= 100 else 2)


@dataclass(eq=False)
class Ticket:
    sched: "Scheduler"
    backend_id: str
    user_id: str
    user_label: str
    rank: int
    kind: str
    label: str
    max_wait: float
    model_id: str = ""  # Semechka model id, so model lifecycle can see who is waiting for which model
    id: str = field(default_factory=lambda: "q_" + uuid.uuid4().hex[:12])
    state: str = "waiting"  # waiting | running | done | cancelled | timeout | rejected
    enqueued_at: float = 0.0
    started_at: float | None = None
    boost: int = 0
    position_on_arrival: int = 0
    abort: asyncio.Event = field(default_factory=asyncio.Event)  # set to ask a RUNNING ticket's owner to stop
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    _timer: asyncio.TimerHandle | None = None
    _last_pos: int | None = None
    ended_by: str = ""

    @property
    def waited(self) -> float:
        return (self.started_at or self.sched.clock()) - self.enqueued_at

    def position(self) -> int | None:
        return self.sched.position_of(self)

    async def updates(self):
        """Yield {"position", "ahead", "running", "limit"} whenever the queue state changes, until the ticket runs.

        Raises QueueTimeout / QueueCancelled if the ticket never gets a slot."""
        while True:
            self.changed.clear()  # clear first: a change that lands while the consumer works must not be lost
            if self.state == "running":
                return
            if self.state in ("cancelled", "timeout", "rejected", "done"):
                raise QueueTimeout() if self.state == "timeout" else QueueCancelled()
            pos = self.position()
            if pos is not None and pos != self._last_pos:
                self._last_pos = pos
                yield self.sched.describe_wait(self)
            if self.state == "waiting" and not self.changed.is_set():
                await self.changed.wait()

    async def wait(self) -> "Ticket":
        async for _ in self.updates():
            pass
        return self

    def release(self) -> None:
        self.sched._finish(self, "done")

    def cancel(self, by: str = "owner") -> bool:
        return self.sched.cancel(self.id, by=by)


@dataclass
class _Backend:
    limit: int = 1
    user_limit: int = 1
    running: list[Ticket] = field(default_factory=list)
    waiting: list[Ticket] = field(default_factory=list)
    served: int = 0
    waits: deque = field(default_factory=lambda: deque(maxlen=100))  # real measured waits (seconds)


class Scheduler:
    def __init__(self, aging_seconds: float = AGING_SECONDS, clock=time.monotonic):
        self.backends: dict[str, _Backend] = {}
        self.tickets: dict[str, Ticket] = {}
        self.aging = aging_seconds
        self.clock = clock

    # ---- submitting ---------------------------------------------------------------------------------
    def submit(self, *, backend_id: str, user_id: str, rank: int = 100, kind: str = "chat", label: str = "",
               user_label: str = "", backend_limit: int = 2, user_limit: int = 1, max_wait: float = 300.0,
               max_depth: int = 50, model_id: str = "") -> Ticket:
        b = self.backends.setdefault(backend_id, _Backend())
        b.limit, b.user_limit = max(1, backend_limit), max(1, user_limit)
        t = Ticket(self, backend_id, user_id, user_label, rank, kind, label, max_wait, model_id=model_id,
                   enqueued_at=self.clock())
        if len(b.waiting) >= max_depth:
            t.state = "rejected"
            raise QueueFull(f"The queue for this backend is full ({max_depth} waiting). Try again shortly.")
        self.tickets[t.id] = t
        b.waiting.append(t)
        t.position_on_arrival = len(b.waiting)
        self._dispatch(backend_id)
        if t.state == "waiting":
            t.position_on_arrival = self.position_of(t) or t.position_on_arrival
            if max_wait:
                try:
                    t._timer = asyncio.get_running_loop().call_later(max_wait, self._timed_out, t)
                except RuntimeError:  # no loop (sync unit use): timeouts need a loop
                    pass
        return t

    @asynccontextmanager
    async def slot(self, **kw):
        """`async with scheduler.slot(...) as ticket:` — waits for a slot, always releases it."""
        t = self.submit(**kw)
        try:
            await t.wait()
            yield t
        finally:
            if t.state in ("waiting", "running"):
                self._finish(t, "done" if t.state == "running" else "cancelled")

    # ---- ordering -----------------------------------------------------------------------------------
    def _running_for(self, b: _Backend, user_id: str) -> int:
        return sum(1 for x in b.running if x.user_id == user_id)

    def _key(self, b: _Backend, t: Ticket):
        waited = self.clock() - t.enqueued_at
        tier = max(0, tier_for_rank(t.rank) - int(waited // self.aging))
        if t.boost:  # hand-promoted by an operator: ahead of every automatic tier
            tier = -t.boost
        return (tier, self._running_for(b, t.user_id), t.enqueued_at)

    def _ordered(self, b: _Backend) -> list[Ticket]:
        return sorted(b.waiting, key=lambda t: self._key(b, t))

    def position_of(self, t: Ticket) -> int | None:
        if t.state != "waiting":
            return None
        b = self.backends.get(t.backend_id)
        order = self._ordered(b) if b else []
        return order.index(t) + 1 if t in order else None

    def describe_wait(self, t: Ticket) -> dict:
        b = self.backends[t.backend_id]
        pos = self.position_of(t) or 0
        return {"position": pos, "ahead": max(0, pos - 1) + len(b.running), "waiting": len(b.waiting),
                "running": len(b.running), "limit": b.limit, "ticket": t.id}

    # ---- dispatch -----------------------------------------------------------------------------------
    def _dispatch(self, backend_id: str) -> None:
        b = self.backends.get(backend_id)
        if not b:
            return
        while b.waiting and len(b.running) < b.limit:
            nxt = None
            for t in self._ordered(b):
                others_wait = any(x.user_id != t.user_id for x in b.waiting)
                if self._running_for(b, t.user_id) < b.user_limit or not others_wait:
                    nxt = t
                    break
            if nxt is None:  # everyone waiting is over their share and someone else is waiting: keep the slot free
                nxt = self._ordered(b)[0]
            b.waiting.remove(nxt)
            nxt.state, nxt.started_at = "running", self.clock()
            if nxt._timer:
                nxt._timer.cancel()
            b.running.append(nxt)
            b.waits.append(nxt.started_at - nxt.enqueued_at)
            nxt.changed.set()
        for t in b.waiting:  # positions may have changed for everyone still waiting
            t.changed.set()

    def _finish(self, t: Ticket, state: str) -> None:
        b = self.backends.get(t.backend_id)
        if t.state in ("done", "cancelled", "timeout", "rejected"):
            return
        if t._timer:
            t._timer.cancel()
        if b:
            if t in b.running:
                b.running.remove(t)
                b.served += 1
            if t in b.waiting:
                b.waiting.remove(t)
        t.state = state
        self.tickets.pop(t.id, None)
        t.changed.set()
        self._dispatch(t.backend_id)

    def _timed_out(self, t: Ticket) -> None:
        if t.state == "waiting":
            self._finish(t, "timeout")

    # ---- control ------------------------------------------------------------------------------------
    def cancel(self, ticket_id: str, *, by: str = "owner") -> bool:
        """Cancel a waiting ticket (removed at once). A running ticket is asked to stop: its owner's stream ends
        at the next token. Returns False if the ticket is unknown or already over."""
        t = self.tickets.get(ticket_id)
        if not t:
            return False
        t.ended_by = by
        if t.state == "waiting":
            self._finish(t, "cancelled")
            return True
        if t.state == "running":
            t.abort.set()
            return True
        return False

    def promote(self, ticket_id: str) -> bool:
        t = self.tickets.get(ticket_id)
        if not t or t.state != "waiting":
            return False
        t.boost += 1
        self._dispatch(t.backend_id)
        return True

    # ---- observability ------------------------------------------------------------------------------
    def model_load(self, backend_id: str) -> dict[str, dict[str, int]]:
        """model id -> {"running", "waiting"} tickets on one backend."""
        out: dict[str, dict[str, int]] = {}
        b = self.backends.get(backend_id)
        for state, rows in (("running", b.running if b else []), ("waiting", b.waiting if b else [])):
            for t in rows:
                out.setdefault(t.model_id, {"running": 0, "waiting": 0})[state] += 1
        return out

    def snapshot(self, *, user_id: str | None = None) -> dict:
        now = self.clock()
        out = []
        for bid, b in self.backends.items():
            def row(t: Ticket, pos=None):
                r = {"ticket": t.id, "user_id": t.user_id, "user": t.user_label, "kind": t.kind, "label": t.label,
                     "state": t.state, "waited_s": round((t.started_at or now) - t.enqueued_at, 2), "boost": t.boost}
                if pos is not None:
                    r["position"] = pos
                if t.started_at:
                    r["running_s"] = round(now - t.started_at, 2)
                return r
            waits = list(b.waits)
            entry = {"backend_id": bid, "limit": b.limit, "user_limit": b.user_limit,
                     "running": [row(t) for t in b.running],
                     "waiting": [row(t, i + 1) for i, t in enumerate(self._ordered(b))],
                     "served": b.served,
                     "recent_wait_avg_s": round(sum(waits) / len(waits), 2) if waits else None,
                     "recent_wait_max_s": round(max(waits), 2) if waits else None, "recent_samples": len(waits)}
            if user_id is not None:
                entry["running"] = [r for r in entry["running"] if r["user_id"] == user_id]
                entry["waiting"] = [r for r in entry["waiting"] if r["user_id"] == user_id]
                for r in entry["running"] + entry["waiting"]:
                    r.pop("user", None)
                    r.pop("user_id", None)
            out.append(entry)
        return {"backends": out}
