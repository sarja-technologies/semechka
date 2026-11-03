"""The response shapes the Work Mode UI is built against (docs/WORK_MODE.md section 10). These tests are the contract: the web client's
types (web/src/lib/work/types.ts) and the mock server read the same bodies."""
import re
import threading
import time

from sqlalchemy import select

from semechka import jobs_svc
from semechka.models_agents import AgentStep
from semechka.models_files import Job
from semechka.models_work import WorkSession

from . import fake_llm as L
from .work_helpers import (drive, events, new_session, results, say_and_run, setting, we, wk, worker)  # noqa: F401

ISO = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(\.\d+)?Z$")


def test_session_body_has_iso_times_and_the_models_name(wk):
    sid = new_session(wk)
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["model_alias"] == "script-model" and s["model_id"]
    assert all(ISO.match(s[k]) for k in ("created_at", "updated_at", "last_active_at")), s
    assert s["skill_ids"] == [] and s["network"] == "none" and s["autonomy"] == "contained" and s["status"] == "idle"
    listed = wk.get("/api/work/sessions").json()
    assert isinstance(listed, list) and listed[0]["id"] == sid and ISO.match(listed[0]["last_active_at"])


def test_event_shapes_per_kind_and_the_call_join(wk, st):
    sid = new_session(wk)
    say_and_run(st, wk, sid, "go", L.tool("work_shell", cmd="echo hi; exit 3"), L.tool("work_write_file", path="artifacts/a.txt", content="x"),
                L.tool("work_read_file", path="missing.txt"), L.final("done"))
    ev = events(wk, sid)
    for e in ev:
        assert set(e) == {"id", "kind", "run_id", "step_n", "summary", "created_at", "data"}, e
        assert ISO.match(e["created_at"]) and isinstance(e["data"], dict)
    calls = [e for e in ev if e["kind"] == "tool_call"]
    res = [e for e in ev if e["kind"] == "tool_result"]
    assert [c["data"]["call_id"] for c in calls] == [r["data"]["call_id"] for r in res]      # joined by call_id, one-to-one
    assert len({c["data"]["call_id"] for c in calls}) == 3
    c0, r0 = calls[0]["data"], res[0]["data"]
    assert c0["tool"] == "work_shell" and c0["command"] == "echo hi; exit 3" and c0["args"] == {"cmd": "echo hi; exit 3"}
    assert r0["ok"] is True and r0["exit_code"] == 3 and "hi" in r0["output"] and isinstance(r0["duration_ms"], int) and r0["truncated"] is False
    assert res[2]["data"]["ok"] is False and "No such file" in res[2]["data"]["error"] or "no such file" in res[2]["data"]["error"].lower()
    art = next(e for e in ev if e["kind"] == "artifact")["data"]
    assert re.match(r"wa_", art["artifact_id"]) and art["name"] == "a.txt" and art["path"] == "artifacts/a.txt" and art["mime"] == "text/plain" and art["size"] == 1
    msgs = [e["data"] for e in ev if e["kind"] == "message"]
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[1]["content"] == "done"
    states = [e["data"] for e in ev if e["kind"] == "state"]
    assert states and all(s["status"] in ("idle", "running", "paused", "awaiting_approval", "failed", "cancelled") and s.get("note") for s in states)
    # the cursor: since= returns only newer events, last_id names the newest
    body = wk.get(f"/api/work/sessions/{sid}/events", params={"since": ev[3]["id"]}).json()
    assert [e["id"] for e in body["events"]] == [e["id"] for e in ev[4:]] and body["last_id"] == ev[-1]["id"]
    assert body["status"] == "idle" and body["container_state"] == "running"
    assert wk.get(f"/api/work/sessions/{sid}/events", params={"since": ev[-1]["id"]}).json()["events"] == []


def test_a_call_id_does_not_repeat_from_one_run_to_the_next(wk, st):
    sid = new_session(wk)
    say_and_run(st, wk, sid, "one", L.tool("work_note", text="a"), L.final("1"))
    say_and_run(st, wk, sid, "two", L.tool("work_note", text="b"), L.final("2"))
    ids = [e["data"]["call_id"] for e in events(wk, sid) if e["kind"] == "tool_call"]
    assert len(ids) == 2 and ids[0] != ids[1]


def test_an_answered_approval_emits_a_new_event_for_the_same_step(wk, st):
    sid = new_session(wk, autonomy="ask")
    L.say(L.tool("work_shell", cmd="echo one"), L.tool("work_shell", cmd="echo two"), L.final("done"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    first = [e for e in events(wk, sid) if e["kind"] == "approval"]
    assert len(first) == 1 and first[0]["data"]["status"] == "pending" and first[0]["data"]["command"] == "echo one"
    step = first[0]["data"]["step"]
    assert first[0]["data"]["tool"] == "work_shell" and "asks before" in first[0]["data"]["reason"]
    state = wk.get(f"/api/work/sessions/{sid}/state").json()
    assert state["pending_approvals"][0]["step"] == step and state["status"] == "awaiting_approval"
    wk.post(f"/api/work/sessions/{sid}/approvals/{step}", json={"approve": True})
    ap = [e for e in events(wk, sid) if e["kind"] == "approval"]
    assert [e["data"]["status"] for e in ap] == ["pending", "approved"] and ap[1]["data"]["step"] == step and ap[1]["id"] > ap[0]["id"]
    drive(st)
    step2 = wk.get(f"/api/work/sessions/{sid}/state").json()["pending_approvals"][0]["step"]
    wk.post(f"/api/work/sessions/{sid}/approvals/{step2}", json={"approve": False})
    ap = [e["data"] for e in events(wk, sid) if e["kind"] == "approval"]
    assert [(a["step"], a["status"]) for a in ap] == [(step, "pending"), (step, "approved"), (step2, "pending"), (step2, "denied")]
    drive(st)
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"


def test_a_question_nobody_can_answer_is_marked_expired(wk, st):
    sid = new_session(wk, autonomy="ask")
    L.say(L.tool("work_shell", cmd="echo one"), L.final("x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    step = wk.get(f"/api/work/sessions/{sid}/state").json()["pending_approvals"][0]["step"]
    assert wk.post(f"/api/work/sessions/{sid}/cancel").status_code == 200
    ap = [e["data"] for e in events(wk, sid) if e["kind"] == "approval"]
    assert [(a["step"], a["status"]) for a in ap] == [(step, "pending"), (step, "expired")]
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "cancelled"


def test_a_cancelled_session_accepts_new_messages(wk, st):
    sid = new_session(wk, autonomy="ask")
    L.say(L.tool("work_shell", cmd="echo one"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    assert wk.post(f"/api/work/sessions/{sid}/cancel").status_code == 200
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "cancelled"
    L.reset()
    r = say_and_run(st, wk, sid, "try again", L.final("fresh start"))
    assert r["appended"] is False
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "idle" and events(wk, sid)[-1]["data"]["content"] == "fresh start"


def test_a_message_while_a_cancel_is_in_progress_is_refused_with_the_reason(wk, st):
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="sleep 20"), L.final("x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    t = threading.Thread(target=drive, args=(st,))
    t.start()
    for _ in range(100):  # wait until the command is really running
        if any(e["kind"] == "tool_call" for e in events(wk, sid)):
            break
        time.sleep(0.05)
    wk.post(f"/api/work/sessions/{sid}/cancel")
    r = wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "more"})
    t.join(30)
    # either the cancel was still being carried out (409 with its reason) or it had just finished (a new run): never a lost message
    assert (r.status_code == 409 and r.json()["detail"]["code"] == "cancelling") or r.status_code == 200


def test_state_body(wk, st):
    sid = new_session(wk, autonomy="ask")
    L.say(L.tool("work_shell", cmd="echo one"), L.final("x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    s = wk.get(f"/api/work/sessions/{sid}/state").json()
    assert s["status"] == "awaiting_approval" and s["network"] == "none" and s["autonomy"] == "ask" and s["model_id"]
    assert s["container"]["state"] in ("running", "absent") and s["container"]["name"].startswith("work-")
    run = s["active_run"]
    assert run["id"].startswith("ar_") and run["max_steps"] == 40 and isinstance(run["step_n"], int) and run["step_n"] >= 1
    assert s["jobs"] == [] and s["pending_approvals"][0]["tool"] == "work_shell"
    u = s["usage"]
    assert u["steps"] == 1 and u["tokens_in"] == 100 and u["tokens_out"] > 0
    assert u["workspace_limit_bytes"] == 2048 * 1048576 and "workspace_bytes" in u
    wk.post(f"/api/work/sessions/{sid}/approvals/{s['pending_approvals'][0]['step']}", json={"approve": True})
    drive(st)
    s = wk.get(f"/api/work/sessions/{sid}/state").json()
    assert s["active_run"] is None and s["status"] == "idle" and s["usage"]["steps"] == 2
    assert s["usage"]["workspace_bytes"] is None or s["usage"]["workspace_bytes"] >= 0


def test_files_listing_is_one_level_with_the_documented_fields(wk, st):
    sid = new_session(wk)
    say_and_run(st, wk, sid, "go", L.tool("work_write_file", path="data/in.csv", content="a,b\n1,2\n"),
                L.tool("work_write_file", path="data/sub/x.png", content="not really"), L.tool("work_write_file", path="top.txt", content="t"), L.final("ok"))
    root = wk.get(f"/api/work/sessions/{sid}/files").json()
    assert root["path"] == "" and sorted(e["name"] for e in root["entries"]) == ["data", "top.txt"]
    d = next(e for e in root["entries"] if e["name"] == "data")
    assert d["type"] == "dir" and d["path"] == "data" and ISO.match(d["modified"])
    f = next(e for e in root["entries"] if e["name"] == "top.txt")
    assert f["type"] == "file" and f["path"] == "top.txt" and f["size"] == 1 and f["mime"] == "text/plain" and ISO.match(f["modified"])
    sub = wk.get(f"/api/work/sessions/{sid}/files", params={"path": "data"}).json()
    assert sub["path"] == "data" and sorted((e["name"], e["type"], e["path"]) for e in sub["entries"]) == [("in.csv", "file", "data/in.csv"), ("sub", "dir", "data/sub")]
    csv = next(e for e in sub["entries"] if e["name"] == "in.csv")
    assert csv["mime"] == "text/csv" and csv["size"] == 8
    r = wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": csv["path"]})   # the path is sent back verbatim
    assert r.status_code == 200 and r.content == b"a,b\n1,2\n" and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": csv["path"], "download": 1}).headers["content-disposition"]


def test_artifact_list_body(wk, st):
    sid = new_session(wk)
    say_and_run(st, wk, sid, "go", L.tool("work_write_file", path="artifacts/r.md", content="# R"), L.final("ok"))
    a = wk.get(f"/api/work/sessions/{sid}/artifacts").json()
    assert len(a) == 1
    assert {"id", "name", "path", "mime", "size", "created_at", "saved_to_library"} <= set(a[0])
    assert a[0]["mime"] == "text/markdown" and ISO.match(a[0]["created_at"]) and a[0]["saved_to_library"] is False
    assert wk.get(f"/api/work/artifacts/{a[0]['id']}/content").content == b"# R"


def test_options_and_me_carry_what_the_screens_read(wk, app):
    o = wk.get("/api/work/options").json()
    lim = o["limits"]
    assert lim["upload_max_mb"] == 100 and lim["workspace_max_mb"] == 2048 and lim["max_steps"] == 40 and lim["max_sessions_per_user"] == 2
    assert lim["delegation_max_depth"] == 2 and lim["delegation_max_count"] == 8
    m = o["models"][0]
    assert {"id", "alias", "tools", "vision", "context", "ready"} <= set(m) and m["ready"] is True
    sk = o["skills"][0]
    assert {"id", "scope", "name", "title", "description", "category", "enabled", "can_edit"} <= set(sk) and sk["scope"] == "org"
    ops = wk.get("/api/me").json()["ui"]["operations"]
    assert ops["work"] == {"enabled": True, "reason": ""}
    setting(wk, "modes.work.enabled", False)
    ops = wk.get("/api/me").json()["ui"]["operations"]
    assert ops["work"]["enabled"] is False and ops["work"]["reason"]
    w = worker(wk, app)
    assert w.get("/api/me").json()["ui"]["operations"]["work"]["enabled"] is False   # the mode is off for everyone now


def test_work_tools_cannot_be_given_to_an_assistant(wk):
    ws = wk.get("/api/orgs").json()
    oid = ws[0]["id"] if isinstance(ws, list) else ws["orgs"][0]["id"]
    wid = wk.post(f"/api/orgs/{oid}/workspaces", json={"name": "W"}).json()["id"]
    for tool in ("generate_image", "delegate", "work_shell", "read_skill"):
        r = wk.post(f"/api/workspaces/{wid}/agents", json={"name": "A", "tools": [tool]})
        assert r.status_code in (400, 422) and "Work Mode session" in str(r.json()), (tool, r.text)
