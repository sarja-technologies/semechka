"""The generation queue: limits, priority, fairness, cancellation, timeouts — exercised with real asyncio tasks."""
import asyncio

import pytest

from semechka.scheduler import QueueCancelled, QueueFull, QueueTimeout, Scheduler


def sub(s, user, rank=100, **kw):
    kw.setdefault("backend_limit", 1)
    kw.setdefault("user_limit", 1)
    return s.submit(backend_id="b1", user_id=user, rank=rank, user_label=user, **kw)


def test_runs_immediately_when_a_slot_is_free_and_respects_the_backend_limit():
    s = Scheduler()
    a = sub(s, "a", backend_limit=2)
    b = sub(s, "b", backend_limit=2)
    c = sub(s, "c", backend_limit=2)
    assert (a.state, b.state, c.state) == ("running", "running", "waiting")
    assert c.position() == 1
    a.release()
    assert c.state == "running"
    snap = s.snapshot()["backends"][0]
    assert len(snap["running"]) == 2 and snap["waiting"] == [] and snap["served"] == 1


def test_priority_tier_beats_arrival_order():
    s = Scheduler()
    holder = sub(s, "h")
    guest = sub(s, "guest", rank=200)
    admin = sub(s, "admin", rank=10)
    member = sub(s, "member", rank=100)
    assert [guest.position(), member.position(), admin.position()] == [3, 2, 1]
    holder.release()
    assert admin.state == "running"
    admin.release()
    assert member.state == "running"  # member (tier 1) before guest (tier 2) although guest queued first
    member.release()
    assert guest.state == "running"


def test_fair_share_one_person_cannot_starve_another():
    s = Scheduler()
    a1 = sub(s, "a", backend_limit=2)
    a2 = sub(s, "a", backend_limit=2)  # idle backend: a single person may use both slots
    assert a1.state == a2.state == "running"
    a3 = sub(s, "a", backend_limit=2)
    a4 = sub(s, "a", backend_limit=2)
    b1 = sub(s, "b", backend_limit=2)  # arrives last
    a1.release()
    assert b1.state == "running" and a3.state == "waiting"  # b goes first: a already holds a slot
    b1.release()
    assert a3.state == "running"  # then FIFO among a's own requests
    assert a4.state == "waiting"


def test_work_conserving_user_cap():
    s = Scheduler()
    ts = [sub(s, "solo", backend_limit=3, user_limit=1) for _ in range(3)]
    assert all(t.state == "running" for t in ts)  # nobody else waits, so the per-person cap does not idle the backend


def test_aging_lifts_a_long_waiting_low_priority_request():
    now = [0.0]
    s = Scheduler(aging_seconds=60, clock=lambda: now[0])
    holder = sub(s, "h")
    guest = sub(s, "guest", rank=200)
    now[0] = 130  # two aging periods: guest tier 2 -> 0
    admin = sub(s, "admin", rank=10)
    assert guest.position() == 1 and admin.position() == 2
    holder.release()
    assert guest.state == "running"


def test_operator_promote_moves_a_ticket_to_the_front():
    s = Scheduler()
    holder = sub(s, "h")
    admin = sub(s, "admin", rank=10)
    guest = sub(s, "guest", rank=200)
    assert s.promote(guest.id) is True
    assert guest.position() == 1 and admin.position() == 2
    holder.release()
    assert guest.state == "running"


def test_cancel_waiting_removes_it_and_shifts_positions():
    s = Scheduler()
    holder = sub(s, "h")
    w1, w2 = sub(s, "x"), sub(s, "y")
    assert w2.position() == 2
    assert w1.cancel() is True
    assert w1.state == "cancelled" and w2.position() == 1
    assert s.cancel("q_nope") is False
    holder.release()
    assert w2.state == "running"


def test_cancel_running_asks_the_owner_to_stop_and_keeps_the_slot_until_release():
    s = Scheduler()
    r = sub(s, "a")
    assert s.cancel(r.id, by="admin") is True
    assert r.abort.is_set() and r.state == "running"
    r.release()
    assert r.state == "done" and s.snapshot()["backends"][0]["running"] == []


def test_queue_full_is_refused_without_leaking():
    s = Scheduler()
    sub(s, "h")
    sub(s, "a", max_depth=2)
    sub(s, "b", max_depth=2)
    with pytest.raises(QueueFull):
        sub(s, "c", max_depth=2)
    assert len(s.snapshot()["backends"][0]["waiting"]) == 2


def test_snapshot_for_a_person_hides_other_people():
    s = Scheduler()
    sub(s, "a")
    sub(s, "b")
    mine = s.snapshot(user_id="b")["backends"][0]
    assert mine["running"] == [] and len(mine["waiting"]) == 1
    assert "user" not in mine["waiting"][0] and "user_id" not in mine["waiting"][0]
    assert mine["waiting"][0]["position"] == 1


async def test_timeout_frees_the_place_and_raises():
    s = Scheduler()
    holder = sub(s, "h")
    t = sub(s, "w", max_wait=0.05)
    with pytest.raises(QueueTimeout):
        await t.wait()
    assert t.state == "timeout" and s.snapshot()["backends"][0]["waiting"] == []
    holder.release()


async def test_waiting_ticket_is_notified_of_position_changes_then_starts():
    s = Scheduler()
    holder = sub(s, "h")
    w1, w2 = sub(s, "x"), sub(s, "y")
    seen = []

    async def watch():
        async for u in w2.updates():
            seen.append((u["position"], u["ahead"]))
    task = asyncio.create_task(watch())
    await asyncio.sleep(0.02)
    assert seen == [(2, 2)]
    holder.release()  # w1 starts; w2 moves to position 1
    await asyncio.sleep(0.02)
    w1.release()
    await asyncio.wait_for(task, 1)
    assert seen[0] == (2, 2) and (1, 1) in seen and w2.state == "running"


async def test_cancelled_task_leaves_the_queue():
    s = Scheduler()
    holder = sub(s, "h")

    async def worker():
        async with s.slot(backend_id="b1", user_id="w", backend_limit=1, user_limit=1):
            await asyncio.sleep(10)
    task = asyncio.create_task(worker())
    await asyncio.sleep(0.02)
    assert len(s.snapshot()["backends"][0]["waiting"]) == 1
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert s.snapshot()["backends"][0]["waiting"] == []
    holder.release()
    assert s.snapshot()["backends"][0]["running"] == []


async def test_slot_releases_on_error():
    s = Scheduler()
    with pytest.raises(RuntimeError):
        async with s.slot(backend_id="b1", user_id="a", backend_limit=1, user_limit=1):
            raise RuntimeError("boom")
    assert s.snapshot()["backends"][0]["running"] == []


async def test_cancelled_waiting_ticket_raises_to_its_waiter():
    s = Scheduler()
    holder = sub(s, "h")
    t = sub(s, "w")
    waiter = asyncio.create_task(t.wait())
    await asyncio.sleep(0.02)
    s.cancel(t.id, by="admin")
    with pytest.raises(QueueCancelled):
        await waiter
    assert t.ended_by == "admin"
    holder.release()
