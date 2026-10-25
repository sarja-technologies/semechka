"""Replies are never cut off by a model switch on an engine that holds one chat model for every client.

The Sarness double (fake_sarness.py) switches its chat model the way the real engine does: on any request for another
model, even while replies stream, and counts every reply cut off that way. These tests drive real concurrent HTTP
requests through a live server (uvicorn in a thread), because the protection lives in the event loop's scheduler.
"""
import json
import threading
import time

import httpx
import pytest
from semechka.scheduler import Scheduler

from . import fake_sarness as fs
from .conftest import PW, Server, _free_port, make_user
from .test_lifecycle import actions, mid


# ---- the scheduler on its own ----------------------------------------------------------------------------
def test_scheduler_never_runs_two_chat_models_at_once_on_an_exclusive_engine():
    s = Scheduler()
    kw = dict(backend_id="e", kind="chat", backend_limit=4, user_limit=4, max_wait=0, exclusive=True)
    a = s.submit(user_id="u1", model_id="A", **kw)
    a2 = s.submit(user_id="u4", model_id="A", **kw)  # same model as the one running: runs alongside
    b = s.submit(user_id="u2", model_id="B", **kw)
    c = s.submit(user_id="u3", model_id="A", **kw)  # arrives after B: must not overtake the switch
    assert (a.state, a2.state, b.state, c.state) == ("running", "running", "waiting", "waiting")
    assert s.describe_wait(b)["reason"] == "model_switch"
    a.release()
    assert b.state == "waiting"
    a2.release()
    assert b.state == "running" and c.state == "waiting" and s.describe_wait(c)["reason"] == "model_switch"
    b.release()
    assert c.state == "running"


def test_scheduler_keeps_running_different_models_together_where_the_engine_allows_it():
    s = Scheduler()
    kw = dict(backend_id="e", kind="chat", backend_limit=4, user_limit=4, max_wait=0)
    x, y = s.submit(user_id="u1", model_id="A", **kw), s.submit(user_id="u2", model_id="B", **kw)
    z = s.submit(user_id="u3", model_id="C", exclusive=True, **kw)  # an exclusive model only conflicts with exclusive ones
    assert x.state == y.state == z.state == "running"


# ---- real concurrent requests ----------------------------------------------------------------------------
@pytest.fixture
def live(app, mo):
    srv = Server(app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


def client(base, email):
    c = httpx.Client(base_url=base, timeout=60)
    assert c.post("/api/auth/login", json={"email": email, "password": PW}).status_code == 200
    c.headers["x-csrf-token"] = c.get("/api/me").json()["csrf"]
    return c


def reply(c, model, text="hello"):
    cid = c.post("/api/chat/conversations", json={"model_id": model}).json()["id"]
    events = []
    with c.stream("POST", f"/api/chat/conversations/{cid}/generate",
                  json={"content": text, "model_id": model, "mode": "send", "operation": "text", "parent_id": None}) as r:
        assert r.status_code == 200, r.read()
        for line in r.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return events


def done(events):
    return next(e for e in events if e["type"] == "done")


def enable_big(mo, st):
    big = mid(st, "local:fake-big")
    mo.patch(f"/api/admin/models/{big}", json={"enabled": True})
    mo.put(f"/api/admin/models/{big}/grants", json={"grants": [{"subject_type": "role", "subject_id": "member", "effect": "allow"}]})
    return big


def test_a_reply_on_one_model_is_not_cut_off_by_someone_asking_for_another(mo, st, app, live):
    big, chat = enable_big(mo, st), mid(st, "local:fake-chat")
    for e in ("one@example.test", "two@example.test"):
        make_user(mo, app, e)
    assert mo.put("/api/admin/settings/queue.backend_concurrency", json={"value": 4}).status_code == 200
    fs.STATE["stream_delay"] = 0.4
    out = {}
    first = threading.Thread(target=lambda: out.update(a=reply(client(live, "one@example.test"), big)))
    first.start()
    for _ in range(100):  # wait until the first reply is really streaming on the engine
        if fs.STATE["llm"]["model_id"] == "fake-big" and fs.STATE["llm"]["active_streams"]:
            break
        time.sleep(0.02)
    out["b"] = reply(client(live, "two@example.test"), chat)
    first.join()
    assert done(out["a"])["status"] == "complete" and done(out["b"])["status"] == "complete"
    assert fs.STATE["cut_off"] == 0
    assert any(e["type"] == "queue" and e.get("reason") == "model_switch" for e in out["b"])
    assert [s["to"] for s in fs.STATE["switches"]][-2:] == ["fake-big", "fake-chat"]
    assert all(s["streams"] == 0 for s in fs.STATE["switches"])  # nobody was mid-reply at any switch


def test_a_switch_waits_while_another_client_is_mid_reply(mo, st, app, live):
    make_user(mo, app, "one@example.test")
    fs.STATE["llm"].update(state="ready", model_id="fake-big", active_streams=1, ctx=4096)  # another client streaming
    threading.Timer(1.5, lambda: fs.STATE["llm"].update(active_streams=0)).start()
    t0 = time.monotonic()
    ev = reply(client(live, "one@example.test"), mid(st, "local:fake-chat"))
    assert done(ev)["status"] == "complete" and time.monotonic() - t0 >= 1.4
    assert any(e["type"] == "queue" and e.get("reason") == "engine_busy" for e in ev)
    assert fs.STATE["switches"][-1] == {"to": "fake-chat", "ctx": 2048, "from": "fake-big", "streams": 0}


def test_the_same_model_with_another_context_window_counts_as_a_switch(mo, st, app, live):
    make_user(mo, app, "one@example.test")
    fs.STATE["llm"].update(state="ready", model_id="fake-chat", active_streams=1, ctx=4096)  # loaded by someone else
    threading.Timer(1.0, lambda: fs.STATE["llm"].update(active_streams=0)).start()
    ev = reply(client(live, "one@example.test"), mid(st, "local:fake-chat"))  # Semechka asks for the default, 2048
    assert any(e["type"] == "queue" and e.get("reason") == "engine_busy" for e in ev)
    assert fs.STATE["switches"][-1]["streams"] == 0 and fs.STATE["cut_off"] == 0


def test_no_wait_when_the_engine_already_holds_the_right_model(mo, st, app, live):
    make_user(mo, app, "one@example.test")
    fs.STATE["llm"].update(state="ready", model_id="fake-chat", active_streams=1, ctx=2048)  # someone else, same model
    n = len(fs.STATE["switches"])
    ev = reply(client(live, "one@example.test"), mid(st, "local:fake-chat"))
    assert done(ev)["status"] == "complete" and not any(e.get("reason") == "engine_busy" for e in ev)
    assert len(fs.STATE["switches"]) == n


def test_a_switch_that_would_cut_someone_off_gives_up_without_switching(mo, st, app, live):
    make_user(mo, app, "one@example.test")
    assert mo.put("/api/admin/settings/queue.wait_timeout_seconds", json={"value": 5}).status_code == 200
    fs.STATE["llm"].update(state="ready", model_id="fake-big", active_streams=1, ctx=4096)  # and it never finishes
    n = len(fs.STATE["switches"])
    ev = reply(client(live, "one@example.test"), mid(st, "local:fake-chat"))
    err = next(e for e in ev if e["type"] == "error")
    assert "another client's reply" in err["message"] and done(ev)["status"] == "error"
    assert len(fs.STATE["switches"]) == n and fs.STATE["llm"]["model_id"] == "fake-big"


# ---- stopping the picture and video worker ---------------------------------------------------------------
def test_the_worker_can_be_stopped_only_when_it_is_idle(mo, st, app):
    bid = mo.get("/api/admin/backends").json()[0]["id"]
    assert mo.post(f"/api/engines/{bid}/worker/stop").json()["noop"] == "The worker is not running."
    fs.STATE["worker_alive"] = True
    fs.STATE["jobs"]["j_busy"] = {"id": "j_busy", "kind": "image", "status": "running", "simple": {"model": "fake-turbo"},
                                  "polls": 0, "progress": 0.2, "generations": []}
    r = mo.post(f"/api/engines/{bid}/worker/stop")
    assert r.status_code == 409 and "making a picture or video" in r.json()["detail"]["message"]
    fs.STATE["jobs"]["j_busy"]["status"] = "queued"
    r = mo.post(f"/api/engines/{bid}/worker/stop")
    assert r.status_code == 409 and "waiting" in r.json()["detail"]["message"]
    fs.STATE["jobs"]["j_busy"]["status"] = "completed"
    member = make_user(mo, app, "member@example.test")
    assert member.post(f"/api/engines/{bid}/worker/stop").status_code == 403
    assert fs.STATE["worker_stops"] == 0
    r = mo.post(f"/api/engines/{bid}/worker/stop")
    assert r.status_code == 200 and r.json()["operation"]["state"] == "done" and fs.STATE["worker_stops"] == 1
    assert actions(st, "engine.") == ["engine.worker_stop.refused", "engine.worker_stop.refused", "engine.worker_stop",
                                       "engine.worker_stop.done"]
