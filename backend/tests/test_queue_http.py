"""The queue end to end: real server, real concurrent SSE streams, the fake OpenAI-compatible backend behind it."""
import json
import threading
import time

import httpx
import pytest

from .conftest import PW, Server, _free_port, make_user
from .test_knowledge import enable


@pytest.fixture(autouse=True)
def quick_heartbeat(monkeypatch):
    from semechka.routers import chat
    monkeypatch.setattr(chat, "HEARTBEAT_SECONDS", 0.3)


@pytest.fixture
def live(app, owner):
    srv = Server(app, _free_port())
    srv.start()
    owner.put("/api/admin/settings/queue.backend_concurrency", json={"value": 1})
    mid = owner.get("/api/chat/models").json()[0]["id"]
    owner.put(f"/api/admin/models/{mid}/grants", json={"grants": [{"subject_type": "role", "subject_id": r, "effect": "allow"}
                                                                  for r in ("owner", "member", "queue_operator")]})
    yield f"http://127.0.0.1:{srv.port}", mid
    srv.stop()


class Person:
    """A browser-like HTTP client against the live server."""

    def __init__(self, base, email, password=PW):
        self.c = httpx.Client(base_url=base, timeout=30)
        r = self.c.post("/api/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        self.csrf = self.c.get("/api/me").json()["csrf"]
        self.h = {"x-csrf-token": self.csrf}

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def post(self, url, **kw):
        return self.c.post(url, headers=self.h, **kw)

    def new_conv(self):
        return self.post("/api/chat/conversations", json={}).json()["id"]

    def stream(self, cid, text, model_id):
        return Stream(self, f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": text, "model_id": model_id})


class Stream:
    """Runs one generation in a thread, recording every SSE event with the time it arrived."""

    def __init__(self, person, url, body):
        self.events: list[tuple[float, dict]] = []
        self.status = None
        self.stop = threading.Event()
        self.client = None
        self.t = threading.Thread(target=self._run, args=(person, url, body), daemon=True)
        self.t.start()

    def _run(self, p, url, body):
        with httpx.Client(base_url=p.c.base_url, cookies=p.c.cookies, timeout=30) as c:
            self.client = c
            with c.stream("POST", url, json=body, headers=p.h) as r:
                self.status = r.status_code
                for line in r.iter_lines():
                    if self.stop.is_set():
                        return  # leaving the context closes the connection, like closing the tab
                    if line.startswith("data: "):
                        self.events.append((time.monotonic(), json.loads(line[6:])))

    def wait_for(self, pred, timeout=8):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for t, e in list(self.events):
                if pred(e):
                    return t, e
            time.sleep(0.02)
        raise AssertionError(f"event not seen; got {[e['type'] for _, e in self.events]}")

    def types(self):
        return [e["type"] for _, e in self.events]

    def abort(self):
        """Close the connection the way a closed tab does (even while the server is silent, e.g. queued)."""
        self.stop.set()
        try:
            self.client.close()
        except Exception:  # noqa: BLE001
            pass
        self.t.join(5)

    def join(self):
        self.t.join(15)


def admin_queue(owner):
    return owner.get("/api/queue/admin").json()["backends"][0]


def test_second_request_waits_with_a_real_position_and_runs_after_the_first(live, owner, app):
    base, mid = live
    make_user(owner, app, "a@example.test")
    make_user(owner, app, "b@example.test")
    a, b = Person(base, "a@example.test"), Person(base, "b@example.test")
    sa = a.stream(a.new_conv(), "medium please", mid)
    sa.wait_for(lambda e: e["type"] == "delta")
    sb = b.stream(b.new_conv(), "medium please", mid)
    _, q = sb.wait_for(lambda e: e["type"] == "queue")
    assert q["position"] == 1 and q["running"] == 1 and q["limit"] == 1 and q["ticket"].startswith("q_")
    snap = admin_queue(owner)  # an operator sees the same numbers, with names
    assert [r["user"] for r in snap["running"]] == ["a"] and [r["user"] for r in snap["waiting"]] == ["b"]
    assert snap["waiting"][0]["position"] == 1 and snap["limit"] == 1
    sa.join(), sb.join()
    t_a_done, _ = sa.wait_for(lambda e: e["type"] == "done")
    t_b_first, _ = sb.wait_for(lambda e: e["type"] == "delta")
    assert t_b_first >= t_a_done - 0.05  # b only started once a had finished
    assert sb.types().index("queue") < sb.types().index("delta")
    assert "error" not in sb.types() and sa.status == sb.status == 200
    after = admin_queue(owner)
    assert after["running"] == [] and after["waiting"] == [] and after["served"] >= 2
    assert after["recent_wait_max_s"] is not None and after["recent_wait_max_s"] > 0.3  # measured, not invented


def test_waiting_request_can_be_cancelled_by_its_owner_only(live, owner, app):
    base, mid = live
    make_user(owner, app, "a@example.test")
    make_user(owner, app, "b@example.test")
    make_user(owner, app, "c@example.test")
    a, b, c = (Person(base, f"{x}@example.test") for x in "abc")
    sa = a.stream(a.new_conv(), "medium", mid)
    sa.wait_for(lambda e: e["type"] == "delta")
    sb = b.stream(b.new_conv(), "medium", mid)
    _, q = sb.wait_for(lambda e: e["type"] == "queue")
    assert c.post(f"/api/queue/tickets/{q['ticket']}/cancel").status_code == 404  # someone else's ticket: no probing
    mine = b.get("/api/queue/mine").json()["backends"][0]
    assert mine["waiting"][0]["position"] == 1 and "user" not in mine["waiting"][0]
    assert b.post(f"/api/queue/tickets/{q['ticket']}/cancel").status_code == 200
    _, err = sb.wait_for(lambda e: e["type"] == "error")
    assert "cancelled while it was waiting" in err["message"]
    sb.join()
    assert "delta" not in sb.types()
    assert admin_queue(owner)["waiting"] == []
    sa.join()
    assert "error" not in sa.types()  # the running request was not disturbed


def test_operator_can_cancel_and_promote_others_and_it_is_audited(live, owner, app):
    base, mid = live
    make_user(owner, app, "a@example.test")
    make_user(owner, app, "b@example.test")
    make_user(owner, app, "c@example.test")
    make_user(owner, app, "ops@example.test", role="queue_operator")
    a, b, c, ops = (Person(base, e) for e in ("a@example.test", "b@example.test", "c@example.test", "ops@example.test"))
    sa = a.stream(a.new_conv(), "slow", mid)  # holds the slot for ~15 s unless we disconnect
    sa.wait_for(lambda e: e["type"] == "delta")
    sb = b.stream(b.new_conv(), "medium", mid)
    sb.wait_for(lambda e: e["type"] == "queue")
    sc = c.stream(c.new_conv(), "medium", mid)
    sc.wait_for(lambda e: e["type"] == "queue")
    view = ops.get("/api/queue/admin").json()
    assert view["can_cancel"] and view["can_promote"]
    order = [r["user"] for r in view["backends"][0]["waiting"]]
    assert order == ["b", "c"]
    c_ticket = view["backends"][0]["waiting"][1]["ticket"]
    assert ops.post(f"/api/queue/tickets/{c_ticket}/promote").status_code == 200
    assert [r["user"] for r in ops.get("/api/queue/admin").json()["backends"][0]["waiting"]] == ["c", "b"]
    assert b.get("/api/queue/admin").status_code == 403 and b.post(f"/api/queue/tickets/{c_ticket}/promote").status_code == 403
    b_ticket = ops.get("/api/queue/admin").json()["backends"][0]["waiting"][1]["ticket"]
    assert ops.post(f"/api/queue/tickets/{b_ticket}/cancel").status_code == 200
    _, err = sb.wait_for(lambda e: e["type"] == "error")
    assert "administrator cancelled" in err["message"]
    sa.abort()  # the slot frees; c (promoted) runs
    sc.join()
    assert "delta" in sc.types() and "error" not in sc.types()
    actions = [e["action"] for e in owner.get("/api/admin/audit").json()["items"]]
    assert "queue.cancel" in actions and "queue.promote" in actions


def test_role_priority_decides_who_runs_next(live, owner, app):
    base, mid = live
    make_user(owner, app, "holder@example.test")
    make_user(owner, app, "member@example.test")
    holder, member, boss = Person(base, "holder@example.test"), Person(base, "member@example.test"), Person(base, "owner@example.test")
    sh = holder.stream(holder.new_conv(), "slow", mid)
    sh.wait_for(lambda e: e["type"] == "delta")
    sm = member.stream(member.new_conv(), "medium", mid)  # queued first
    sm.wait_for(lambda e: e["type"] == "queue")
    cid = boss.new_conv()
    sb = boss.stream(cid, "medium", mid)  # queued second, but the owner role outranks member
    _, q = sb.wait_for(lambda e: e["type"] == "queue")
    assert q["position"] == 1
    sh.abort()
    sb.join(), sm.join()
    t_boss, _ = sb.wait_for(lambda e: e["type"] == "delta")
    t_member, _ = sm.wait_for(lambda e: e["type"] == "delta")
    assert t_boss < t_member
    done = boss.get(f"/api/chat/conversations/{cid}").json()["messages"][-1]
    assert done["tech"]["queue"]["state"] == "waited" and done["tech"]["queue"]["waited_s"] > 0


def test_disconnecting_while_waiting_leaves_no_ghost_and_timeout_is_reported(live, owner, app):
    base, mid = live
    make_user(owner, app, "a@example.test")
    make_user(owner, app, "b@example.test")
    a, b = Person(base, "a@example.test"), Person(base, "b@example.test")
    sa = a.stream(a.new_conv(), "slow", mid)
    sa.wait_for(lambda e: e["type"] == "delta")
    sb = b.stream(b.new_conv(), "medium", mid)
    sb.wait_for(lambda e: e["type"] == "queue")
    sb.abort()  # b closes the tab
    end = time.monotonic() + 5
    while admin_queue(owner)["waiting"] and time.monotonic() < end:
        time.sleep(0.05)
    assert admin_queue(owner)["waiting"] == []
    owner.put("/api/admin/settings/queue.wait_timeout_seconds", json={"value": 1})
    sb2 = b.stream(b.new_conv(), "medium", mid)
    _, err = sb2.wait_for(lambda e: e["type"] == "error", timeout=6)
    assert "waited too long" in err["message"]
    sb2.join()
    sa.abort()
    end = time.monotonic() + 5
    while admin_queue(owner)["running"] and time.monotonic() < end:
        time.sleep(0.05)
    assert admin_queue(owner)["running"] == []  # a's slot was released when a disconnected


def test_queue_depth_limit_refuses_politely(live, owner, app):
    base, mid = live
    owner.put("/api/admin/settings/queue.max_depth", json={"value": 1})
    for e in ("a", "b", "c"):
        make_user(owner, app, f"{e}@example.test")
    a, b, c = (Person(base, f"{x}@example.test") for x in "abc")
    sa = a.stream(a.new_conv(), "slow", mid)
    sa.wait_for(lambda e: e["type"] == "delta")
    sb = b.stream(b.new_conv(), "medium", mid)
    sb.wait_for(lambda e: e["type"] == "queue")
    sc = c.stream(c.new_conv(), "medium", mid)
    _, err = sc.wait_for(lambda e: e["type"] == "error")
    assert "queue" in err["message"].lower() and "full" in err["message"].lower()
    sa.abort(), sb.abort()
