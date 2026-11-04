"""Task 1: long-running commands. A command runs detached under a supervisor in the container; the tool polls it, shows progress, can
be cancelled, and after a restart of the server it reconnects to the SAME command instead of starting it again. (The test double
runs the same in-container helper as production, with the host's python.)"""
import asyncio
import json
import os
import signal
import threading
import time

import pytest
from sqlalchemy import select

from semechka import jobs_svc, work_svc, work_tools
from semechka.models_agents import AgentStep
from semechka.models_files import Job
from semechka.work_runtime import LocalDirRuntime

from . import fake_llm as L
from .test_work import drive, events, new_session, wk  # noqa: F401


def _cmdline(pid):
    try:
        return open(f"/proc/{pid}/cmdline", "rb").read()
    except OSError:
        return b""


def _jobs(c, sid):
    return [e["data"] for e in events(c, sid) if e["kind"] == "job" and e["data"].get("job_kind") == "command"]


def test_a_command_longer_than_the_first_beat_shows_a_card_with_real_progress_and_ends_accurately(wk, st, monkeypatch):
    monkeypatch.setattr(work_tools, "HEARTBEAT_FIRST_S", 0.5)
    monkeypatch.setattr(work_tools, "HEARTBEAT_S", 0.5)
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="for i in 1 2 3 4; do echo tick$i; sleep 1; done; echo finished", timeout_s=60), L.final("ok"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    cards = _jobs(wk, sid)
    assert len(cards) >= 3, cards                                   # no silent wait: several updates while it ran
    assert {c["job_id"] for c in cards} and len({c["job_id"] for c in cards}) == 1
    assert [c["status"] for c in cards][:-1] == ["running"] * (len(cards) - 1) and cards[-1]["status"] == "completed"
    assert all(c["progress"] is None for c in cards)                # never an invented percentage
    assert any(c["out_bytes"] > 0 for c in cards if c["status"] == "running")
    assert cards[-1]["phase"].startswith("Ended after")
    tr = [e["data"] for e in events(wk, sid) if e["kind"] == "tool_result"][-1]
    assert tr["ok"] and tr["exit_code"] == 0 and "finished" in tr["output"] and "tick1" in tr["output"]
    # the bookkeeping folder is gone and is not listed
    assert not (st.work_runtime._dir("work-" + sid.split("_", 1)[1]) / ".jobs").exists()


def test_a_short_command_gets_no_card(wk, st):
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="echo hi"), L.final("ok"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    assert _jobs(wk, sid) == []


def test_a_restart_in_the_middle_of_a_command_reconnects_without_running_it_twice(wk, st, monkeypatch):
    monkeypatch.setattr(work_tools, "HEARTBEAT_FIRST_S", 0.5)
    sid = new_session(wk)
    rt = st.work_runtime
    real_run_job = LocalDirRuntime.run_job

    def die_after_spawn(self, name, job, cmd=None, **kw):   # the "server" dies right after the command was started
        self.spawn(name, job, cmd, **{k: v for k, v in kw.items() if k not in ("on_tick", "poll_max_s")})
        raise asyncio.CancelledError()
    monkeypatch.setattr(LocalDirRuntime, "run_job", die_after_spawn)
    L.say(L.tool("work_shell", cmd="echo run >> count.txt; sleep 3; echo end >> count.txt; echo FINISHED", timeout_s=60))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    with pytest.raises(asyncio.CancelledError):
        drive(st)
    # --- restart (what the application does at start-up); the command keeps running in the container meanwhile
    monkeypatch.setattr(LocalDirRuntime, "run_job", real_run_job)
    assert jobs_svc.recover_on_start(st) == 1
    work_svc.startup(st)
    L.say(L.final("done after restart"))
    drive(st)
    f = rt._dir(f"work-{sid.split('_', 1)[1]}") / "count.txt"
    assert f.read_text().split() == ["run", "end"]                   # started exactly once
    ev = events(wk, sid)
    assert any("reconnecting" in e["summary"] for e in ev)
    tr = [e["data"] for e in ev if e["kind"] == "tool_result"][-1]
    assert tr["ok"] and "FINISHED" in tr["output"] and tr["exit_code"] == 0
    with st.db.session() as s:
        stp = s.execute(select(AgentStep).where(AgentStep.tool == "work_shell")).scalars().one()
        assert stp.status == "done" and stp.meta.get("resumed") is True
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"


def test_the_result_of_a_command_that_finished_while_the_server_was_down_is_still_collected(wk, st, monkeypatch):
    sid = new_session(wk)
    real_run_job = LocalDirRuntime.run_job

    def die_after_spawn(self, name, job, cmd=None, **kw):
        self.spawn(name, job, cmd, **{k: v for k, v in kw.items() if k not in ("on_tick", "poll_max_s")})
        raise asyncio.CancelledError()
    monkeypatch.setattr(LocalDirRuntime, "run_job", die_after_spawn)
    L.say(L.tool("work_shell", cmd="echo x >> n.txt; echo OUT-OF-BAND", timeout_s=30))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    with pytest.raises(asyncio.CancelledError):
        drive(st)
    time.sleep(1.0)                                                  # the command ends while nobody is polling
    monkeypatch.setattr(LocalDirRuntime, "run_job", real_run_job)
    jobs_svc.recover_on_start(st)
    work_svc.startup(st)
    L.say(L.final("ok"))
    drive(st)
    tr = [e["data"] for e in events(wk, sid) if e["kind"] == "tool_result"][-1]
    assert tr["ok"] and "OUT-OF-BAND" in tr["output"]
    assert (st.work_runtime._dir(f"work-{sid.split('_', 1)[1]}") / "n.txt").read_text() == "x\n"


def test_cancel_kills_the_command_and_the_run_ends_quickly(wk, st):
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="echo up > marker; sleep 117", timeout_s=300), L.final("unreachable"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    t = threading.Thread(target=lambda: drive(st))
    t.start()
    for _ in range(100):
        with st.db.session() as s:
            if s.execute(select(AgentStep).where(AgentStep.tool == "work_shell", AgentStep.status == "running")).first():
                break
        time.sleep(0.1)
    time.sleep(1.0)
    t0 = time.time()
    assert wk.post(f"/api/work/sessions/{sid}/cancel").status_code == 200
    t.join(30)
    assert not t.is_alive() and time.time() - t0 < 20
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "cancelled"
    # nothing of the command is left running
    time.sleep(0.5)
    left = [d for d in os.listdir("/proc") if d.isdigit() and _cmdline(d) == b"sleep\x00117\x00"]
    assert left == []


def test_a_supervisor_that_vanished_is_reported_as_lost_and_never_run_again(wk, st):
    sid = new_session(wk)
    name = "work-" + sid.split("_", 1)[1]
    L.say(L.final("hi"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    work_svc.ensure_running(st, sid)
    rt = st.work_runtime
    rt.spawn(name, "jobLOST000001", "echo once >> lost.txt; sleep 30", timeout_s=60)
    for _ in range(50):
        s_ = rt.job_status(name, "jobLOST000001")
        if s_.get("pid"):
            break
        time.sleep(0.1)
    os.kill(s_["pid"], signal.SIGKILL)
    time.sleep(0.3)
    assert rt.job_status(name, "jobLOST000001")["state"] == "lost"
    res = rt.run_job(name, "jobLOST000001", "echo once >> lost.txt", timeout_s=10)   # a reconnect after the loss
    assert res.lost
    assert (rt._dir(name) / "lost.txt").read_text() == "once\n"                         # not started again


def test_spawn_is_idempotent_per_job_id(wk, st):
    sid = new_session(wk)
    L.say(L.final("hi"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    name = work_svc.ensure_running(st, sid)
    rt = st.work_runtime
    a = rt.spawn(name, "jobIDEM000001", "echo a >> idem.txt; sleep 1", timeout_s=30)
    b = rt.spawn(name, "jobIDEM000001", "echo b >> idem.txt; sleep 1", timeout_s=30)
    assert a["existing"] is False and b["existing"] is True
    res = rt.run_job(name, "jobIDEM000001", "echo c >> idem.txt", timeout_s=30)
    assert res.exit == 0
    assert (rt._dir(name) / "idem.txt").read_text() == "a\n"


def test_job_ids_and_paths_cannot_escape(wk, st):
    sid = new_session(wk)
    L.say(L.final("hi"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    name = work_svc.ensure_running(st, sid)
    from semechka.work_runtime import PathRefused
    for bad in ("../x" * 3, "a/b/cccccc", "short", "x" * 100):
        with pytest.raises(PathRefused):
            st.work_runtime.spawn(name, bad, "echo hi", timeout_s=5)


def test_the_default_ceiling_allows_a_command_longer_than_five_minutes(wk):
    r = wk.get("/api/work/options").json()
    assert r["limits"]["tool_timeout_s"] >= 600
