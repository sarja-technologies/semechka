"""Work Mode end to end over the HTTP API with a scripted language model and the LocalDirRuntime test double.

The model's replies are scripted (tests/fake_llm.py); everything else is real: authorisation, the durable run loop, the tools,
the in-container helper (run by the host python in a temp directory), events, approvals, artifacts, recovery."""
import asyncio
import json
import time

import pytest
from sqlalchemy import select

from semechka import agent_svc, jobs_svc, work_svc
from semechka.models_agents import AgentRun, AgentStep
from semechka.models_files import Job
from semechka.models_work import WorkArtifact, WorkEvent, WorkSession
from semechka.work_runtime import LocalDirRuntime

from . import fake_llm as L
from .conftest import make_user


def drive(st, n=30):
    return jobs_svc.run_pending(st, n)


@pytest.fixture
def wk(env, app, st, tmp_path):
    """The scripted-model install with Work Mode switched on and the test-double runtime installed."""
    st.work_runtime = LocalDirRuntime(tmp_path / "runtime")
    for k in ("work.enabled", "modes.work.enabled"):
        assert env.put(f"/api/admin/settings/{k}", json={"value": True}).status_code == 200
    return env


def new_session(c, **kw):
    r = c.post("/api/work/sessions", json={"title": "t", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def say_and_run(st, c, sid, text, *script):
    L.say(*script)
    r = c.post(f"/api/work/sessions/{sid}/messages", json={"content": text})
    assert r.status_code == 200, r.text
    drive(st)
    return r.json()


def events(c, sid, since=0):
    r = c.get(f"/api/work/sessions/{sid}/events", params={"since": since})
    assert r.status_code == 200, r.text
    return r.json()["events"]


def test_session_lifecycle_and_timeline(wk, st):
    sid = new_session(wk, objective="compute primes")
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "idle" and s["container_state"] == "absent" and s["autonomy"] == "contained" and s["network"] == "none"
    say_and_run(st, wk, sid, "write primes", L.tool("work_write_file", path="primes.py", content="print([p for p in range(2, 30) if all(p % d for d in range(2, p))])\n"),
                L.tool("work_shell", cmd="python3 primes.py > artifacts_out.txt && cat artifacts_out.txt"),
                L.final("Done: wrote primes.py and ran it."))
    ev = events(wk, sid)
    kinds = [e["kind"] for e in ev]
    assert kinds[0] == "state" and "message" in kinds and kinds.count("tool_call") == 2 and kinds.count("tool_result") == 2
    ids = [e["id"] for e in ev]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)
    res = [e for e in ev if e["kind"] == "tool_result"]
    assert "[2, 3, 5, 7, 11, 13, 17, 19, 23, 29]" in res[1]["detail"]["text"] and res[1]["detail"]["exit"] == 0
    assert ev[-1]["kind"] == "message" and ev[-1]["detail"]["role"] == "assistant"
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "idle" and s["container_state"] == "running" and s["active_run_id"] is None
    assert wk.get(f"/api/work/sessions/{sid}/state").json()["usage"]["workspace_bytes"] > 0


# ---- helpers ------------------------------------------------------------------------------------------------
def setting(c, key, value):
    r = c.put(f"/api/admin/settings/{key}", json={"value": value, "confirm_weaker": True})
    assert r.status_code == 200, r.text


def result_of(c, sid, n=-1):
    return [e for e in events(c, sid) if e["kind"] == "tool_result"][n]["detail"]


def rt_of(st):
    return st.work_runtime


def sess_row(st, sid):
    with st.db.session() as s:
        return s.get(WorkSession, sid)


def admin2(owner, app, email="two@acme.test"):
    """A second person who holds mode.work (platform_admin does), for the owner-only tests."""
    return make_user(owner, app, email, role="platform_admin")


def one_shot(st, c, sid, *calls, final="ok"):
    """Run one message whose model calls the given tools in order, then answers."""
    return say_and_run(st, c, sid, "go", *calls, L.final(final))


# ---- API surface, gating and ownership -------------------------------------------------------------------------
def test_options_lists_only_models_the_person_may_use_and_the_permissions(wk, app):
    o = wk.get("/api/work/options").json()
    assert [m["alias"] for m in o["models"]] == ["script-model"] and o["models"][0]["tools"] is True
    assert o["perms"] == {"network": True, "skills_manage": True} and o["network_allowed"] is False and o["skills"] == []
    assert o["limits"]["memory_mb"] == 4096 and o["limits"]["workspace_mb"] == 2048 and o["image_ready"] is True
    mem = make_user(wk, app, "mem@acme.test", role="member")
    assert mem.get("/api/work/options").status_code == 403  # a member holds no mode.work


def body_for(method):
    return {"json": {"content": "x", "approve": True}} if method in ("post", "patch") else (
        {"files": {"file": ("u", b"x")}} if method == "put" else {})


def test_every_route_is_closed_without_the_permission_and_when_the_mode_is_off(wk, app):
    sid = new_session(wk)
    mem = make_user(wk, app, "mem@acme.test", role="member")
    routes = [("get", "/api/work/options"), ("get", "/api/work/sessions"), ("post", "/api/work/sessions"),
              ("get", f"/api/work/sessions/{sid}"), ("patch", f"/api/work/sessions/{sid}"), ("delete", f"/api/work/sessions/{sid}"),
              ("post", f"/api/work/sessions/{sid}/messages"), ("post", f"/api/work/sessions/{sid}/pause"),
              ("post", f"/api/work/sessions/{sid}/resume"), ("post", f"/api/work/sessions/{sid}/cancel"),
              ("get", f"/api/work/sessions/{sid}/events"), ("get", f"/api/work/sessions/{sid}/state"),
              ("post", f"/api/work/sessions/{sid}/approvals/2"), ("get", f"/api/work/sessions/{sid}/files"),
              ("get", f"/api/work/sessions/{sid}/files/content?path=a"), ("put", f"/api/work/sessions/{sid}/files?path=a"),
              ("delete", f"/api/work/sessions/{sid}/files?path=a"), ("get", f"/api/work/sessions/{sid}/artifacts"),
              ("get", "/api/work/artifacts/wa_x/content"), ("post", "/api/work/artifacts/wa_x/save-to-library")]
    for method, url in routes:
        assert getattr(mem, method)(url, **body_for(method)).status_code == 403, (method, url)
    setting(wk, "modes.work.enabled", False)
    for method, url in routes:
        assert getattr(wk, method)(url, **body_for(method)).status_code == 403, ("off", method, url)
    assert wk.get("/api/work/options").json()["detail"]["code"] == "disabled"
    setting(wk, "modes.work.enabled", True)
    setting(wk, "work.enabled", False)
    assert wk.get("/api/work/sessions").status_code == 403
    assert mem.get("/api/me").json()["ui"]["modes"]["work"]["enabled"] is False


def test_sessions_are_owner_only_on_every_route(wk, app, st):
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_write_file", path="artifacts/a.txt", content="hello"))
    aid = wk.get(f"/api/work/sessions/{sid}/artifacts").json()[0]["id"]
    two = admin2(wk, app)
    assert two.get("/api/work/sessions").json() == []  # not even listed
    calls = [("get", f"/api/work/sessions/{sid}"), ("patch", f"/api/work/sessions/{sid}"), ("delete", f"/api/work/sessions/{sid}"),
             ("post", f"/api/work/sessions/{sid}/messages"), ("post", f"/api/work/sessions/{sid}/pause"),
             ("post", f"/api/work/sessions/{sid}/resume"), ("post", f"/api/work/sessions/{sid}/cancel"),
             ("get", f"/api/work/sessions/{sid}/events"), ("get", f"/api/work/sessions/{sid}/state"),
             ("post", f"/api/work/sessions/{sid}/approvals/2"), ("get", f"/api/work/sessions/{sid}/files"),
             ("get", f"/api/work/sessions/{sid}/files/content?path=artifacts/a.txt"), ("put", f"/api/work/sessions/{sid}/files?path=x"),
             ("delete", f"/api/work/sessions/{sid}/files?path=artifacts/a.txt"), ("get", f"/api/work/sessions/{sid}/artifacts"),
             ("get", f"/api/work/artifacts/{aid}/content"), ("post", f"/api/work/artifacts/{aid}/save-to-library")]
    for method, url in calls:
        r = getattr(two, method)(url, **body_for(method))
        assert r.status_code == 404, (method, url, r.status_code)
        assert r.json()["detail"]["code"] == "not_found"
    # ...and nothing changed for the owner
    assert wk.get(f"/api/work/sessions/{sid}").status_code == 200
    assert wk.get(f"/api/work/artifacts/{aid}/content").content == b"hello"
    assert wk.post("/api/work/sessions", json={"model_id": "nope"}).status_code == 403


def test_csrf_is_required_like_the_rest_of_semechka(wk):
    wk.csrf, good = "wrong", wk.csrf
    assert wk.post("/api/work/sessions", json={}).status_code == 403
    wk.csrf = good


def test_work_is_not_a_chat_mode(wk):
    assert wk.get("/api/chat/models", params={"mode": "work"}).status_code == 422
    assert wk.get("/api/chat/models", params={"mode": "chat"}).status_code == 200


def test_the_hidden_work_agent_is_per_org_and_never_listed(wk, st):
    sid = new_session(wk)
    new_session(wk)
    with st.db.session() as s:
        agents = s.execute(select(agent_svc.Agent).where(agent_svc.Agent.kind == "work")).scalars().all()
        assert len(agents) == 1 and agents[0].approval == "never" and "work_shell" in agents[0].tools
    orgs = wk.get("/api/orgs").json()
    oid = orgs[0]["id"] if isinstance(orgs, list) else orgs["orgs"][0]["id"]
    assert [w["name"] for w in wk.get(f"/api/orgs/{oid}/workspaces").json()] == []  # the internal workspace is not offered
    tools = {t["name"] for t in wk.get("/api/agent-tools").json()}
    assert {"work_shell", "work_python", "work_read_file", "work_write_file", "work_edit_file", "work_list_dir", "work_search_files",
            "work_git", "work_publish_artifact"} <= tools and {"python", "shell", "read_file"} <= tools  # the cloud tools are untouched
    assert sid


# ---- conversation, durable state --------------------------------------------------------------------------------
def test_earlier_turns_and_the_session_state_reach_the_model(wk, st):
    sid = new_session(wk, objective="Build the prime sieve")
    say_and_run(st, wk, sid, "first: write sieve.py", L.tool("work_write_file", path="sieve.py", content="x=1\n"),
                L.tool("work_note", text="sieve.py is the entry point"), L.final("wrote sieve.py"))
    say_and_run(st, wk, sid, "second: what did you do?", L.final("I wrote sieve.py."))
    seen = L.SEEN[-1]
    assert seen[0]["role"] == "system" and "Build the prime sieve" in seen[0]["content"]
    assert "sieve.py is the entry point" in seen[0]["content"] and "Files of note: sieve.py" in seen[0]["content"]
    roles = [m["role"] for m in seen[1:]]
    assert roles == ["user", "assistant", "user"]  # strictly alternating: earlier question, earlier answer, new question
    assert "first: write sieve.py" in seen[1]["content"] and "wrote sieve.py" in seen[2]["content"] and seen[3]["content"] == "second: what did you do?"
    assert "work_write_file" in seen[0]["content"] and "Tool results" in seen[0]["content"]


def test_the_state_block_is_bounded(wk, st):
    sid = new_session(wk, objective="o" * 15000)
    for i in range(14):
        say_and_run(st, wk, sid, f"m{i}", L.tool("work_note", text=f"note {i} " + "z" * 250), L.final("k" * 3000))
    sys_msg = L.SEEN[-1][0]["content"]
    state = sys_msg.split("Session state (kept for you; it survives long conversations):\n")[1].split("\n\nTools:")[0]
    assert len(state) <= work_svc.STATE_BLOCK_CHARS and "note 13" in state and "note 0 " not in state


# ---- tools ------------------------------------------------------------------------------------------------------
def test_tool_semantics_files_python_search_and_lists(wk, st):
    sid = new_session(wk)
    body = "\n".join(f"line {i}" for i in range(1, 51)) + "\n"
    one_shot(st, wk, sid, L.tool("work_write_file", path="notes/a.txt", content=body),
             L.tool("work_read_file", path="/work/notes/a.txt", offset=10, limit=3),
             L.tool("work_edit_file", path="notes/a.txt", old="line 7\n", new="SEVEN\n"),
             L.tool("work_edit_file", path="notes/a.txt", old="line 1", new="X"),          # many matches: refused
             L.tool("work_edit_file", path="notes/a.txt", old="nope", new="X"),           # none: refused
             L.tool("work_edit_file", path="notes/a.txt", old="line 1", new="L1", replace_all=True),
             L.tool("work_search_files", pattern="SEVEN|L1[0-1]", path="notes"),
             L.tool("work_list_dir", path=""), L.tool("work_list_dir", path="notes"),
             L.tool("work_python", code="import sys\nprint(sum(range(10)))\nprint('err', file=sys.stderr)\nsys.exit(3)"),
             L.tool("work_shell", cmd="echo $HOME; pwd | wc -l; exit 0"))
    res = [e["detail"] for e in events(wk, sid) if e["kind"] == "tool_result"]
    assert [r["status"] for r in res] == ["done", "done", "done", "error", "error", "done", "done", "done", "done", "done", "done"]
    assert "    10\tline 10" in res[1]["text"] and "[lines 10-12 of 50]" in res[1]["text"]
    assert "occurs" in res[3]["error"] and "not found" in res[4]["error"]
    assert "Replaced" in res[2]["text"] and "SEVEN" in res[6]["text"] and "L10" in res[6]["text"]
    assert "dir   notes/" in res[7]["text"].replace("  ", "  ") or "notes/" in res[7]["text"]
    assert "a.txt" in res[8]["text"] and "bytes" in res[8]["text"]
    assert res[9]["exit"] == 3 and "45" in res[9]["text"] and "stderr:\nerr" in res[9]["text"] and "exit code 3" in res[9]["text"]
    assert res[10]["exit"] == 0 and res[10]["text"].rstrip().endswith("1")  # (the double's HOME is a host path, scrubbed)


def test_git_tool_whitelist_hooks_and_a_real_commit(wk, st):
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_git", args=["init"]), L.tool("work_git", args=["config", "user.name", "Tester"]),
             L.tool("work_git", args=["config", "user.email", "t@example.test"]),
             L.tool("work_write_file", path="a.txt", content="one\n"),
             L.tool("work_git", args=["add", "a.txt"]),
             L.tool("work_git", args=["commit", "-m", "first", "--author", "A <a@x.y>"]),
             L.tool("work_git", args=["log", "--oneline"]),
             L.tool("work_git", args=["clone", "https://example.com/x.git"]),
             L.tool("work_git", args=["push"]),
             L.tool("work_git", args=["-c", "core.hooksPath=/x", "status"]),
             L.tool("work_git", args=["rebase", "--exec", "id", "HEAD~1"]),
             L.tool("work_git", args=["gc"]),
             L.tool("work_git", args=["config", "core.hooksPath", "/tmp/h"]),
             L.tool("work_git", args=["diff", "--no-index", "/etc/passwd", "a.txt"]),
             L.tool("work_git", args="status"))
    res = [e["detail"] for e in events(wk, sid) if e["kind"] == "tool_result"]
    assert [r["exit"] for r in res[:3]] == [0, 0, 0] and res[5]["exit"] == 0 and res[5]["status"] == "done", res[5]
    assert "first" in res[6]["text"]
    assert "needs network access" in res[7]["error"] and "needs network access" in res[8]["error"]
    for i in (9, 10, 13):
        assert res[i]["status"] == "error" and "not allowed" in res[i]["error"], res[i]
    assert "'git gc' is not allowed" in res[11]["error"] and "not allowed" in res[12]["error"]
    assert res[14]["status"] == "done"  # a string is split like a shell would


def test_a_failing_command_is_reported_with_its_real_exit_code_and_a_timeout_kills_the_tree(wk, st):
    sid = new_session(wk)
    t0 = time.time()
    one_shot(st, wk, sid, L.tool("work_shell", cmd="ls /definitely-missing"), L.tool("work_shell", cmd="(sleep 31.7 &) ; sleep 31.7", timeout_s=1),
             L.tool("work_shell", cmd="pgrep -fc '[s]leep 31.7' || true"))
    assert time.time() - t0 < 20
    res = [e["detail"] for e in events(wk, sid) if e["kind"] == "tool_result"]
    assert res[0]["exit"] == 2 and "No such file" in res[0]["text"]
    assert res[1]["timed_out"] is True and "TIMED OUT after 1s" in res[1]["text"] and "everything it started" in res[1]["text"]
    assert res[2]["text"].rstrip().endswith("0"), res[2]  # the backgrounded sleep did not outlive the command


def test_output_is_capped_head_and_tail_and_says_so(wk, st):
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_python", code="print('HEAD' + 'a' * 400000)\nprint('b' * 400000 + 'TAIL')"))
    d = result_of(wk, sid)
    assert len(d["text"]) <= 12000 and "HEAD" in d["text"] and "TAIL" in d["text"] and "left out" in d["text"]
    assert "more characters not shown" not in d["text"]  # the runtime cut it; the loop's own clip applies to what the model sees
    seen = [m["content"] for m in L.SEEN[-1] if m["role"] == "user" and m["content"].startswith("Tool result")][0]
    assert len(seen) <= 12100


def test_binary_files_and_missing_files_are_reported_not_dumped(wk, st):
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_python", code="open('b.bin','wb').write(bytes(range(256))*4)"),
             L.tool("work_read_file", path="b.bin"), L.tool("work_read_file", path="missing.txt"), L.tool("work_read_file"),
             L.tool("work_write_file", path="x.txt"), L.tool("work_shell", cmd=123), L.tool("work_list_dir", path="missing"),
             L.tool("no_such_tool"))
    res = [e for e in events(wk, sid) if e["kind"] in ("tool_result", "error")]
    d = [e["detail"] for e in res if e["kind"] == "tool_result"]
    assert "binary file" in d[1]["error"] and "no such file" in d[2]["error"].lower() and "'path' is required" in d[3]["error"]
    assert "'content' is required" in d[4]["error"] and "'cmd' must be a string" in d[5]["error"] and d[6]["status"] == "error"
    assert any("not one of your tools" in e["summary"] for e in res if e["kind"] == "error")


# ---- path safety ------------------------------------------------------------------------------------------------
def test_paths_leaving_work_and_symlink_escapes_are_refused_everywhere(wk, st, tmp_path):
    sid = new_session(wk)
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "passwd").write_text("TOP-SECRET")
    one_shot(st, wk, sid,
             L.tool("work_read_file", path="../../etc/passwd"), L.tool("work_read_file", path="/etc/passwd"),
             L.tool("work_write_file", path="../escape.txt", content="x"), L.tool("work_write_file", path="/tmp/x", content="x"),
             L.tool("work_shell", cmd=f"ln -s {outside} link_dir && ln -s {outside}/passwd link_file && ln -s . inside && echo ok > real.txt"),
             L.tool("work_read_file", path="link_file"), L.tool("work_read_file", path="link_dir/passwd"),
             L.tool("work_write_file", path="link_dir/new.txt", content="x"), L.tool("work_list_dir", path="link_dir"),
             L.tool("work_search_files", pattern="SECRET", path="link_dir"), L.tool("work_edit_file", path="link_file", old="TOP", new="X"),
             L.tool("work_read_file", path="inside/real.txt"),                       # a link that stays inside is fine
             L.tool("work_publish_artifact", path="link_file"), L.tool("work_list_dir", path="a/../../.."))
    res = [e["detail"] for e in events(wk, sid) if e["kind"] == "tool_result"]
    for i in (0, 1, 2, 3):
        assert res[i]["status"] == "error", (i, res[i])
    assert "leaves /work" in res[0]["error"] or "inside /work" in res[0]["error"]
    for i in (5, 6, 7, 8, 9, 10, 12, 13):
        assert res[i]["status"] == "error" and ("outside /work" in res[i]["error"] or "leaves /work" in res[i]["error"]), (i, res[i])
    assert "symlink escape refused" in res[5]["error"] and "symlink escape refused" in res[7]["error"]
    assert res[11]["status"] == "done" and "ok" in res[11]["text"]
    assert (outside / "passwd").read_text() == "TOP-SECRET" and not (outside / "new.txt").exists()
    assert not (tmp_path / "escape.txt").exists()
    # the same checks guard the REST file routes
    assert wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": "link_file"}).status_code == 422
    assert wk.get(f"/api/work/sessions/{sid}/files", params={"path": "link_dir"}).status_code == 422
    assert wk.get(f"/api/work/sessions/{sid}/files", params={"path": "../.."}).status_code == 422
    assert wk.put(f"/api/work/sessions/{sid}/files", params={"path": "link_dir/u.txt"}, files={"file": ("u", b"x")}).status_code == 422
    # deleting a link deletes the link, never what it points at
    assert wk.delete(f"/api/work/sessions/{sid}/files", params={"path": "link_dir"}).status_code == 200
    assert (outside / "passwd").exists()
    assert wk.delete(f"/api/work/sessions/{sid}/files", params={"path": ""}).status_code == 422  # never the root


def test_the_command_environment_carries_nothing_from_the_host(wk, st, monkeypatch):
    monkeypatch.setenv("SEMECHKA_SECRET_TOKEN", "hunter2")
    monkeypatch.setenv("DOCKER_HOST", "unix:///run/user/1000/x.sock")
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="env"))
    txt = result_of(wk, sid)["text"]
    keys = {ln.split("=")[0] for ln in txt.splitlines() if "=" in ln and ln.split("=")[0].isupper()}
    assert "hunter2" not in txt and "DOCKER_HOST" not in keys and "SEMECHKA_SECRET_TOKEN" not in keys
    assert {"HOME", "PATH"} <= keys and "GIT_CONFIG_VALUE_0" in keys


def test_host_paths_are_scrubbed_from_tool_errors():
    from semechka.work_tools import scrub
    assert scrub("mkdir /home/fylume/.local/share/docker/volumes/x: denied; also /work/a and /tmp/b") == \
        "mkdir [host path]: denied; also /work/a and /tmp/b"
    assert scrub("see /run/user/1000/semechka-sandbox.sock now") == "see [host path] now"


# ---- quota and limits -------------------------------------------------------------------------------------------
def test_the_workspace_quota_refuses_writes_and_commands_with_the_exact_usage_but_allows_cleanup(wk, st):
    setting(wk, "work.workspace_max_mb", 64)
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="head -c 70000000 /dev/zero > big.bin; ls -l big.bin"),
             L.tool("work_write_file", path="x.txt", content="hello"),      # refused: over quota
             L.tool("work_shell", cmd="echo hi"),                              # refused: over quota
             L.tool("work_shell", cmd="rm big.bin"),                           # cleanup is allowed
             L.tool("work_write_file", path="x.txt", content="hello"))
    res = [e["detail"] for e in events(wk, sid) if e["kind"] == "tool_result"]
    assert res[0]["status"] == "done" and "WARNING: the workspace is over its limit" in res[0]["text"]
    assert res[1]["status"] == "error" and "workspace is full: 66." in res[1]["error"] and "of 64 MB" in res[1]["error"]
    assert res[2]["status"] == "error" and "workspace is full" in res[2]["error"]
    assert res[3]["status"] == "done" and res[4]["status"] == "done"
    # REST upload is refused the same way, with the same arithmetic
    sid2 = new_session(wk)
    one_shot(st, wk, sid2, L.tool("work_shell", cmd="head -c 70000000 /dev/zero > big.bin"))
    r = wk.put(f"/api/work/sessions/{sid2}/files", params={"path": "u.txt"}, files={"file": ("u", b"x")})
    assert r.status_code == 413 and "workspace is full" in r.json()["detail"]["message"]


def test_a_command_that_grows_the_workspace_past_its_limit_is_stopped(wk, st):
    setting(wk, "work.workspace_max_mb", 64)
    sid = new_session(wk)
    t0 = time.time()
    one_shot(st, wk, sid, L.tool("work_shell", cmd="for i in $(seq 1 60); do head -c 10000000 /dev/zero >> big.bin; sleep 0.5; done", timeout_s=60))
    d = result_of(wk, sid)
    assert "STOPPED: the workspace grew past its 64 MB limit" in d["text"] and time.time() - t0 < 25
    assert d["exit"] < 0 or d["exit"] == 137


def test_per_session_limits_are_clamped_to_the_platform_ceiling(wk):
    setting(wk, "work.memory_mb", 1024)
    r = wk.post("/api/work/sessions", json={"limits": {"memory_mb": 99999, "cpus": 0.01, "workspace_mb": 128, "tool_timeout_s": 7, "bogus": 1}}).json()
    assert r["limits"]["memory_mb"] == 1024 and r["limits"]["cpus"] == 0.25 and r["limits"]["workspace_mb"] == 128 and r["limits"]["tool_timeout_s"] == 7
    setting(wk, "work.memory_mb", 512)  # lowering the ceiling applies to existing sessions at once
    assert wk.get(f"/api/work/sessions/{r['id']}").json()["limits"]["memory_mb"] == 512
    assert wk.post("/api/work/sessions", json={"limits": {"memory_mb": "lots"}}).status_code == 422


# ---- artifacts --------------------------------------------------------------------------------------------------
def test_artifacts_are_listed_automatically_survive_the_container_and_follow_changes(wk, st, app):
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_python", code="import os\nos.makedirs('artifacts', exist_ok=True)\nopen('artifacts/primes.txt','w').write('2 3 5 7')"),
             L.tool("work_write_file", path="artifacts/report.md", content="# R\n"),
             L.tool("work_write_file", path="elsewhere/data.csv", content="a,b\n1,2\n"),
             L.tool("work_publish_artifact", path="elsewhere/data.csv", title="The data"))
    arts = wk.get(f"/api/work/sessions/{sid}/artifacts").json()
    assert [(a["path"], a["name"], a["mime"]) for a in arts] == [("artifacts/primes.txt", "primes.txt", "text/plain"),
                                                              ("artifacts/report.md", "report.md", "text/markdown"),
                                                              ("elsewhere/data.csv", "The data", "text/csv")]
    ev = [e for e in events(wk, sid) if e["kind"] == "artifact"]
    assert len(ev) == 3 and ev[0]["detail"]["content_url"].endswith("/content")
    run = wk.get(f"/api/work/sessions/{sid}/state").json()
    assert run["artifacts"] == 3
    pid = arts[0]["id"]
    assert wk.get(f"/api/work/artifacts/{pid}/content").content == b"2 3 5 7"
    assert "attachment" in wk.get(f"/api/work/artifacts/{pid}/content", params={"download": 1}).headers["content-disposition"]
    # the stored copy outlives the container and its volume
    rt_of(st).remove(sess_row(st, sid).container_name, volume=True)
    assert wk.get(f"/api/work/artifacts/{pid}/content").content == b"2 3 5 7"
    # the final answer's run lists the artifacts the run made
    with st.db.session() as s:
        run = s.execute(select(AgentRun).where(AgentRun.session_id == sid)).scalars().first()
        assert {a["name"] for a in run.artifacts} == {"primes.txt", "report.md", "The data"}
    # unchanged content is not announced twice; changed content updates the same artifact
    one_shot(st, wk, sid, L.tool("work_write_file", path="artifacts/report.md", content="# R2\n"))
    again = wk.get(f"/api/work/sessions/{sid}/artifacts").json()
    assert len(again) == 3 and [a for a in again if a["name"] == "report.md"][0]["sha256"] != [a for a in arts if a["name"] == "report.md"][0]["sha256"]
    assert wk.get(f"/api/work/artifacts/{[a for a in again if a['name'] == 'report.md'][0]['id']}/content").content == b"# R2\n"
    # unsafe types are never served inline
    one_shot(st, wk, sid, L.tool("work_write_file", path="artifacts/page.html", content="<script>alert(1)</script>"))
    page = [a for a in wk.get(f"/api/work/sessions/{sid}/artifacts").json() if a["name"] == "page.html"][0]
    r = wk.get(f"/api/work/artifacts/{page['id']}/content")
    assert r.headers["content-disposition"].startswith("attachment") and "sandbox" in r.headers["content-security-policy"]


def test_save_to_library_handles_images_and_refuses_the_rest(wk, st, monkeypatch):
    import io

    from PIL import Image
    from semechka import services
    from semechka.media_models import MediaAsset
    buf = io.BytesIO()
    Image.new("RGB", (16, 9), "red").save(buf, "PNG")
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_python", code="import os, base64\nos.makedirs('artifacts', exist_ok=True)\n"
                                                    f"open('artifacts/pic.png','wb').write(base64.b64decode('{__import__('base64').b64encode(buf.getvalue()).decode()}'))"),
             L.tool("work_write_file", path="artifacts/n.txt", content="text"))
    arts = {a["name"]: a for a in wk.get(f"/api/work/sessions/{sid}/artifacts").json()}
    assert wk.post(f"/api/work/artifacts/{arts['n.txt']['id']}/save-to-library").status_code == 422
    r = wk.post(f"/api/work/artifacts/{arts['pic.png']['id']}/save-to-library")
    assert r.status_code == 409 and "not available" in r.json()["detail"]["message"]  # no image model in this install
    real = services.mode_state
    monkeypatch.setattr(services, "mode_state", lambda s, p, st_, mode: {"enabled": True, "reason": ""} if mode == "image" else real(s, p, st_, mode))
    r = wk.post(f"/api/work/artifacts/{arts['pic.png']['id']}/save-to-library")
    assert r.status_code == 200 and r.json()["kind"] == "image" and r.json()["already"] is False
    assert wk.post(f"/api/work/artifacts/{arts['pic.png']['id']}/save-to-library").json()["already"] is True
    with st.db.session() as s:
        a = s.get(MediaAsset, r.json()["asset_id"])
        assert (a.width, a.height, a.mime) == (16, 9, "image/png") and a.job_id == r.json()["job_id"]
        from semechka import media_svc
        assert media_svc.read_asset(st, a) == buf.getvalue()
    assert wk.get(f"/api/work/sessions/{sid}/artifacts").json()[0]["saved_to_library"] in (True, False)


# ---- files over REST ----------------------------------------------------------------------------------------------
def test_files_tree_content_upload_and_delete(wk, st):
    sid = new_session(wk)
    assert wk.get(f"/api/work/sessions/{sid}/files").json()["entries"] == []  # a brand-new session has no container yet
    assert rt_of(st).state(sess_row(st, sid).container_name) == "absent"
    r = wk.put(f"/api/work/sessions/{sid}/files", params={"path": "data/in.csv"}, files={"file": ("in.csv", b"a,b\n1,2\n")})
    assert r.status_code == 200 and r.json()["size"] == 8
    one_shot(st, wk, sid, L.tool("work_shell", cmd="cat data/in.csv | wc -l"))
    assert "2" in result_of(wk, sid)["text"]
    tree = wk.get(f"/api/work/sessions/{sid}/files").json()["entries"]
    assert [(e["name"], e["type"]) for e in tree] == [("data", "dir")]
    sub = wk.get(f"/api/work/sessions/{sid}/files", params={"path": "data"}).json()["entries"]
    assert sub[0]["path"] == "data/in.csv" and sub[0]["size"] == 8
    c = wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": "data/in.csv"})
    assert c.content == b"a,b\n1,2\n" and c.headers["content-disposition"].startswith("attachment") is False
    assert wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": "data/nope"}).status_code == 404
    assert wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": "data"}).status_code == 422
    setting(wk, "work.upload_max_mb", 1)
    assert wk.put(f"/api/work/sessions/{sid}/files", params={"path": "big"}, files={"file": ("b", b"0" * 1100000)}).status_code == 413
    assert wk.delete(f"/api/work/sessions/{sid}/files", params={"path": "data"}).json()["deleted"] >= 1
    assert wk.get(f"/api/work/sessions/{sid}/files").json()["entries"] == []
    assert wk.delete(f"/api/work/sessions/{sid}/files", params={"path": "data"}).status_code == 404


# ---- run control ------------------------------------------------------------------------------------------------
def active_job(st, sid):
    with st.db.session() as s:
        sess = s.get(WorkSession, sid)
        run = s.get(AgentRun, sess.active_run_id) if sess.active_run_id else None
        return s.get(Job, run.job_id) if run else None


def test_pause_stops_the_run_between_steps_and_resume_continues_it(wk, st):
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="echo one"), L.final("done"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    assert wk.post(f"/api/work/sessions/{sid}/pause").json()["status"] == "paused"
    drive(st)
    assert L.SEEN == []  # the model was not even asked: the run waits
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "paused" and active_job(st, sid).status == "awaiting_approval"
    assert wk.post(f"/api/work/sessions/{sid}/pause").status_code == 200   # idempotent
    wk.post(f"/api/work/sessions/{sid}/resume")
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "running"
    drive(st)
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"
    texts = [e["summary"] for e in events(wk, sid) if e["kind"] == "state"]
    assert "Resumed" in texts and any(t.startswith("Paused") for t in texts)
    assert wk.post(f"/api/work/sessions/{sid}/resume").status_code == 409  # not paused any more
    assert wk.post(f"/api/work/sessions/{sid}/pause").status_code == 409   # nothing running


def test_cancel_before_the_run_starts_and_cancel_of_a_running_command(wk, st):
    sid = new_session(wk)
    L.say(L.final("never"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    c = wk.post(f"/api/work/sessions/{sid}/cancel").json()
    assert c["status"] == "cancelled" and c["active_run_id"] is None
    drive(st)
    assert L.SEEN == [] and wk.post(f"/api/work/sessions/{sid}/cancel").status_code == 409
    # the session is usable again after a cancel
    L.reset()
    say_and_run(st, wk, sid, "again", L.final("fine"))
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"
    # a running command is stopped at once
    import threading
    L.reset()
    L.say(L.tool("work_shell", cmd="echo started > marker; sleep 60", timeout_s=120), L.final("unreachable"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "sleep a long time"})
    t = threading.Thread(target=lambda: drive(st))
    t.start()
    t0 = time.time()
    for _ in range(100):
        with st.db.session() as s:
            if s.execute(select(AgentStep).where(AgentStep.tool == "work_shell", AgentStep.status == "running")).first():
                break
        time.sleep(0.1)
    time.sleep(0.8)
    assert wk.post(f"/api/work/sessions/{sid}/cancel").status_code == 200
    t.join(30)
    assert not t.is_alive() and time.time() - t0 < 25
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "cancelled" and s["active_run_id"] is None
    ev = events(wk, sid)
    tr = [e for e in ev if e["kind"] == "tool_result"][-1]["detail"]
    assert "INTERRUPTED" in tr["text"] or tr["status"] == "error"
    assert any("Cancelling" in e["summary"] for e in ev) and ev[-1]["kind"] == "state" and ev[-1]["summary"].startswith("Cancelled")


def test_a_message_sent_while_the_run_is_working_is_read_by_that_run(wk, st):
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="echo one"), L.final("ok"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "first task"})
    r = wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "also: print the date"}).json()
    assert r["appended"] is True and r["queued"] is False
    drive(st)
    first = [m["content"] for m in L.SEEN[0] if m["role"] == "user"]
    assert first[0] == "first task" and first[1] == "The person sent another message while you were working:\nalso: print the date"
    assert any("also: print the date" in m["content"] for m in L.SEEN[-1] if m["role"] == "user")
    with st.db.session() as s:
        assert s.query(AgentRun).filter(AgentRun.session_id == sid).count() == 1  # one run, not two


def test_messages_that_arrive_as_the_run_finishes_start_a_follow_up_run(wk, st):
    sid = new_session(wk)
    L.say(L.final("first answer"), L.final("second answer"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "q1"})
    orig = work_svc.WorkExtension.on_final
    fired = []

    def late(self, st_, s, run, final):
        orig(self, st_, s, run, final)
        if fired:
            return
        fired.append(1)
        # a person's message committed after the model's last turn read the conversation, before the run was closed
        from semechka.models_work import WorkMessage
        sess = s.get(WorkSession, run.session_id)
        s.add(WorkMessage(session_id=sess.id, seq=work_svc._next_seq(s, sess.id), role="user", content_enc=work_svc.enc(st_, sess.org_id, "late question"),
                          run_id=run.id))
    work_svc.WorkExtension.on_final = late
    try:
        drive(st)
    finally:
        work_svc.WorkExtension.on_final = orig
    with st.db.session() as s:
        runs = s.query(AgentRun).filter(AgentRun.session_id == sid).count()
    assert runs == 2 and wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"
    assert any("late question" in m["content"] for m in L.SEEN[-1] if m["role"] == "user")


# ---- approvals --------------------------------------------------------------------------------------------------
def test_ask_autonomy_asks_before_every_mutating_call_and_a_decline_reaches_the_model(wk, st):
    sid = new_session(wk, autonomy="ask")
    L.say(L.tool("work_list_dir"), L.tool("work_shell", cmd="echo hello > a.txt"), L.tool("work_write_file", path="b.txt", content="x"),
          lambda m: L.final("declined: " + m[-1]["content"][:60]))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    s = wk.get(f"/api/work/sessions/{sid}/state").json()
    assert s["status"] == "awaiting_approval" and active_job(st, sid).status == "awaiting_approval"
    pend = s["pending_approvals"]
    assert len(pend) == 1 and pend[0]["tool"] == "work_shell" and "asks before every" in pend[0]["reason"] and pend[0]["args"]["cmd"].startswith("echo hello")
    assert [e["kind"] for e in events(wk, sid)][-1] == "approval"
    r = wk.post(f"/api/work/sessions/{sid}/approvals/{pend[0]['step']}", json={"approve": True})
    assert r.json()["status"] == "approved"
    assert wk.post(f"/api/work/sessions/{sid}/approvals/{pend[0]['step']}", json={"approve": True}).status_code == 404  # decided once
    drive(st)
    pend = wk.get(f"/api/work/sessions/{sid}/state").json()["pending_approvals"]
    assert len(pend) == 1 and pend[0]["tool"] == "work_write_file"   # the read-only list_dir needed no approval
    wk.post(f"/api/work/sessions/{sid}/approvals/{pend[0]['step']}", json={"approve": False})
    drive(st)
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"
    ev = events(wk, sid)
    assert [e["summary"] for e in ev if e["kind"] == "approval" and e["summary"].startswith(("Approved", "Declined"))] == ["Approved: work_shell", "Declined: work_write_file"]
    assert ev[-1]["detail"]["content"].startswith("declined: Tool work_write_file did not run")
    assert rt_of(st)._dir(sess_row(st, sid).container_name).joinpath("a.txt").read_text() == "hello\n"
    assert not rt_of(st)._dir(sess_row(st, sid).container_name).joinpath("b.txt").exists()
    assert wk.post(f"/api/work/sessions/{sid}/approvals/1", json={"approve": True}).status_code == 404


def test_contained_autonomy_runs_freely_but_asks_for_workspace_deletion_and_network_use(wk, st, app):
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="mkdir -p d && echo x > d/f && rm -r d"), L.tool("work_python", code="print(1)"))
    assert [e["kind"] for e in events(wk, sid)].count("approval") == 0   # contained: no questions for ordinary work
    L.reset()
    L.say(L.tool("work_shell", cmd="rm -rf /work/*"), L.final("x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "wipe"})
    drive(st)
    pend = wk.get(f"/api/work/sessions/{sid}/state").json()["pending_approvals"]
    assert "deletes the workspace" in pend[0]["reason"]
    wk.post(f"/api/work/sessions/{sid}/approvals/{pend[0]['step']}", json={"approve": False})
    drive(st)
    # network: a session with internet asks before commands that look like they use it
    setting(wk, "work.network_allowed", True)
    sid2 = new_session(wk, network="internet")
    L.reset()
    L.say(L.tool("work_shell", cmd="echo local only"), L.tool("work_shell", cmd="curl -s https://example.com"), L.tool("work_git", args=["push"]),
          L.final("x"))
    wk.post(f"/api/work/sessions/{sid2}/messages", json={"content": "go"})
    drive(st)
    pend = wk.get(f"/api/work/sessions/{sid2}/state").json()["pending_approvals"]
    assert "network" in pend[0]["reason"]
    wk.post(f"/api/work/sessions/{sid2}/approvals/{pend[0]['step']}", json={"approve": False})
    drive(st)
    pend = wk.get(f"/api/work/sessions/{sid2}/state").json()["pending_approvals"]
    assert "git push" in pend[0]["reason"]


def test_pausing_while_waiting_for_approval_and_resuming_keeps_the_question(wk, st):
    sid = new_session(wk, autonomy="ask")
    L.say(L.tool("work_shell", cmd="echo hi"), L.final("done"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    drive(st)
    step = wk.get(f"/api/work/sessions/{sid}/state").json()["pending_approvals"][0]["step"]
    wk.post(f"/api/work/sessions/{sid}/pause")
    wk.post(f"/api/work/sessions/{sid}/resume")
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "awaiting_approval" and active_job(st, sid).status == "awaiting_approval"
    wk.post(f"/api/work/sessions/{sid}/approvals/{step}", json={"approve": True})
    drive(st)
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"


# ---- limits, network, audit ---------------------------------------------------------------------------------------
def test_concurrency_limits_per_person_and_for_the_server(wk, st, app):
    setting(wk, "work.max_sessions_per_user", 1)
    a, b = new_session(wk), new_session(wk)
    wk.post(f"/api/work/sessions/{a}/messages", json={"content": "x"})
    r = wk.post(f"/api/work/sessions/{b}/messages", json={"content": "y"})
    assert r.status_code == 429 and "limit 1" in r.json()["detail"]["message"]
    # another person is not held back by it
    two = admin2(wk, app)
    c = new_session(two)
    assert two.post(f"/api/work/sessions/{c}/messages", json={"content": "z"}).status_code == 200
    # the server-wide limit is applied when jobs are claimed
    setting(wk, "work.max_running_total", 1)
    with st.db.session() as s:
        queued = s.query(Job).filter(Job.kind == "work", Job.status == "queued").count()
    assert queued == 2
    first = jobs_svc.claim(st, ["work"])
    assert first is not None and jobs_svc.claim(st, ["work"]) is None   # the second waits for a free slot
    jobs_svc._running["work"] = 0   # (the claimed job is not run here)
    # stored sessions are capped too
    setting(wk, "work.max_sessions_stored", 3)
    new_session(wk)
    assert wk.post("/api/work/sessions", json={}).status_code == 429


def test_network_is_for_permitted_people_and_only_when_the_administrator_allows_it(wk, st, app):
    mem = make_user(wk, app, "mem2@acme.test", role="platform_admin")
    assert wk.post("/api/work/sessions", json={"network": "internet"}).json()["detail"]["code"] == "network_off"  # not allowed server-wide
    setting(wk, "work.network_allowed", True)
    # a person with mode.work but without work.network is refused
    sid = new_session(wk, network="internet")
    assert wk.get(f"/api/work/sessions/{sid}").json()["network"] == "internet"
    spec = work_svc.container_spec(st, st.db.Session(), sess_row(st, sid))
    assert spec.network == "internet"
    # revoked server-wide: the next tool call refuses, with the reason
    setting(wk, "work.network_allowed", False)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="echo hi"))
    assert "no longer permitted" in result_of(wk, sid)["error"]
    # lowering a session's network works while it is idle and recreates the container with the same workspace
    assert wk.patch(f"/api/work/sessions/{sid}", json={"network": "none"}).json()["network"] == "none"
    assert mem  # (used for the permission test below)


def test_the_network_permission_is_required_by_name(wk, st, app):
    setting(wk, "work.network_allowed", True)
    # a role that has mode.work but not work.network
    r = wk.post("/api/admin/roles", json={"id": "workers", "label": "Workers", "description": "x", "rank": 150, "grants": ["mode.work", "mode.chat", "models.use_all"], "denies": []})
    assert r.status_code == 200, r.text
    w = make_user(wk, app, "worker@acme.test", role="workers")
    sid = new_session(w)  # may work
    r = w.post("/api/work/sessions", json={"network": "internet"})
    assert r.status_code == 403 and "permission" in r.json()["detail"]["message"]
    assert w.patch(f"/api/work/sessions/{sid}", json={"network": "internet"}).status_code == 403


def test_audit_trail_records_what_happened_without_the_commands(wk, st):
    from semechka.models import AuditEvent
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="echo SUPERSECRET-COMMAND-TEXT"), L.tool("work_write_file", path="a.txt", content="SECRET-CONTENT"))
    wk.post(f"/api/work/sessions/{sid}/pause")  # nothing running: 409, no audit
    wk.delete(f"/api/work/sessions/{sid}")
    with st.db.session() as s:
        evs = s.execute(select(AuditEvent).where(AuditEvent.action.like("work.%"))).scalars().all()
        actions = [e.action for e in evs]
        blob = json.dumps([e.detail for e in evs])
        from semechka import audit
        assert audit.verify_chain(s)["ok"]
    assert actions.count("work.session.create") == 1 and actions.count("work.run.start") == 1 and actions.count("work.tool") == 2
    assert "work.session.delete" in actions
    assert "SUPERSECRET" not in blob and "SECRET-CONTENT" not in blob
    tool = [e for e in evs if e.action == "work.tool"][0]
    assert tool.detail["tool"] == "work_shell" and tool.detail["exit"] == 0 and tool.target == sid and tool.org_id


# ---- events: long poll and ordering -----------------------------------------------------------------------------------
def test_events_are_ordered_filtered_by_since_and_long_poll_wakes_on_news(wk, st, app):
    from .conftest import Api
    sid = new_session(wk)
    say_and_run(st, wk, sid, "go", L.tool("work_shell", cmd="echo 1"), L.final("ok"))
    all_ev = events(wk, sid)
    ids = [e["id"] for e in all_ev]
    assert ids == sorted(ids) and len(ids) == len(set(ids))
    mid = ids[len(ids) // 2]
    later = events(wk, sid, since=mid)
    assert [e["id"] for e in later] == [i for i in ids if i > mid]
    body = wk.get(f"/api/work/sessions/{sid}/events", params={"since": ids[-1]}).json()
    assert body["events"] == [] and body["last_id"] == ids[-1] and body["status"] == "idle"
    # nothing new: the call waits as long as asked, then returns empty
    t0 = time.time()
    assert wk.get(f"/api/work/sessions/{sid}/events", params={"since": ids[-1], "wait": 1}).json()["events"] == []
    assert 0.9 < time.time() - t0 < 4
    assert wk.get(f"/api/work/sessions/{sid}/events", params={"wait": 60}).status_code == 422   # at most 25 s
    # news wakes the poll at once
    c2 = Api(app)
    assert c2.login("owner@example.test").status_code == 200
    got = {}

    def poll():
        t = time.time()
        got["body"] = c2.get(f"/api/work/sessions/{sid}/events", params={"since": ids[-1], "wait": 20}).json()
        got["t"] = time.time() - t
    th = threading.Thread(target=poll)
    th.start()
    time.sleep(1.0)
    with st.db.session(write=True) as s:
        work_svc.emit(st, s, s.get(WorkSession, sid), "state", "ping")
    th.join(15)
    assert not th.is_alive() and 0.9 < got["t"] < 5 and [e["summary"] for e in got["body"]["events"]] == ["ping"]
    # a deleted session ends the poll with 404
    wk.delete(f"/api/work/sessions/{sid}")
    assert wk.get(f"/api/work/sessions/{sid}/events", params={"wait": 1}).status_code == 404


import threading  # noqa: E402


# ---- recovery -----------------------------------------------------------------------------------------------------------
def crash_in_tool(monkeypatch, st):
    """Make the next tool call 'kill the worker' after it was started: the step stays running+started, the job running."""
    spec = agent_svc.TOOLS["work_write_file"]
    orig = spec.fn

    def die(tc, a):
        raise asyncio.CancelledError()
    monkeypatch.setattr(spec, "fn", die)
    return orig, spec


def test_a_run_interrupted_by_a_restart_resumes_and_says_which_step_was_cut(wk, st, monkeypatch):
    sid = new_session(wk)
    orig, spec = crash_in_tool(monkeypatch, st)
    L.say(L.tool("work_write_file", path="a.txt", content="x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    with pytest.raises(asyncio.CancelledError):   # the "process" dies in the middle of the tool
        drive(st)
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "running" and active_job(st, sid).status == "running"
    # --- restart: what the application does at start-up
    monkeypatch.setattr(spec, "fn", orig)
    assert jobs_svc.recover_on_start(st) == 1
    out = work_svc.startup(st)
    assert out["settled"] == 0
    L.say(lambda m: L.final("recovered; saw: " + [x for x in m if x["role"] == "user"][-1]["content"][:60]))
    drive(st)
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "idle"
    ev = events(wk, sid)
    assert any("interrupted by a restart" in e["summary"] for e in ev)
    assert ev[-1]["detail"]["content"].startswith("recovered; saw: The tool call work_write_file was interrupted by a restart")
    with st.db.session() as s2:
        steps = s2.execute(select(AgentStep).where(AgentStep.tool == "work_write_file")).scalars().all()
        assert [x.status for x in steps] == ["interrupted"]   # never replayed


def test_a_run_whose_job_cannot_resume_ends_failed_with_a_clear_event_not_stuck(wk, st, monkeypatch):
    sid = new_session(wk)
    orig, spec = crash_in_tool(monkeypatch, st)
    L.say(L.tool("work_write_file", path="a.txt", content="x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    with pytest.raises(asyncio.CancelledError):
        drive(st)
    monkeypatch.setattr(spec, "fn", orig)
    with st.db.session(write=True) as s:
        s.query(Job).filter(Job.kind == "work").update({"attempts": jobs_svc.MAX_ATTEMPTS})
    jobs_svc.recover_on_start(st)
    assert work_svc.startup(st)["settled"] == 1
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "failed" and s["active_run_id"] is None
    last = events(wk, sid)[-1]
    assert last["kind"] == "error" and "stopped repeatedly" in last["detail"]["message"]
    # and the session is usable again
    say_and_run(st, wk, sid, "again", L.final("fine"))
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"
    assert any("The run stopped before finishing" in m["content"] for m in L.SEEN[-1] if m["role"] == "user")


def test_a_failed_run_is_reported_and_the_model_failing_does_not_wedge_the_session(wk, st):
    sid = new_session(wk)
    say_and_run(st, wk, sid, "go", "not json at all", "still not json", "nope")   # three unusable replies in a row
    s = wk.get(f"/api/work/sessions/{sid}").json()
    assert s["status"] == "failed"
    ev = events(wk, sid)
    assert [e["kind"] for e in ev].count("error") >= 4 and "three times in a row" in ev[-1]["detail"]["message"]
    L.reset()
    say_and_run(st, wk, sid, "retry", L.final("ok now"))
    assert wk.get(f"/api/work/sessions/{sid}").json()["status"] == "idle"


def test_work_mode_switched_off_mid_run_ends_the_run_with_the_reason(wk, st):
    sid = new_session(wk)
    L.say(L.tool("work_shell", cmd="echo 1"), L.final("never"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    setting(wk, "modes.work.enabled", False)
    drive(st)
    with st.db.session() as s:
        sess = s.get(WorkSession, sid)
        assert sess.status == "cancelled" and sess.active_run_id is None
        ev = s.execute(select(WorkEvent).where(WorkEvent.session_id == sid).order_by(WorkEvent.id.desc())).scalars().first()
        assert "not available to you" in ev.summary
    assert L.SEEN == []


# ---- container lifecycle ------------------------------------------------------------------------------------------------
def test_delete_removes_the_container_the_volume_the_copies_and_every_row(wk, st):
    import os
    sid = new_session(wk)
    other = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_write_file", path="artifacts/r.txt", content="keep?"))
    name = sess_row(st, sid).container_name
    rt = rt_of(st)
    assert rt.state(name) == "running" and rt._dir(name).exists()
    blob = work_svc.artifact_dir(st, sess_row(st, sid).user_id)
    assert any(f.endswith(".bin") for f in os.listdir(blob))
    r = wk.delete(f"/api/work/sessions/{sid}")
    assert r.json() == {"ok": True, "container_removed": True}
    assert rt.state(name) == "absent" and not rt._dir(name).exists()          # container AND volume (the double's directory)
    assert not any(f.endswith(".bin") for f in os.listdir(blob))
    with st.db.session() as s:
        for model in (work_svc.WorkMessage, work_svc.WorkEvent, WorkArtifact):
            assert s.query(model).filter(model.session_id == sid).count() == 0
        assert s.get(WorkSession, sid) is None and s.query(AgentRun).filter(AgentRun.session_id == sid).count() == 0
        assert s.get(WorkSession, other) is not None
    assert wk.get(f"/api/work/sessions/{sid}").status_code == 404 and wk.delete(f"/api/work/sessions/{sid}").status_code == 404
    assert not [n for n in rt.list_managed("") if n["name"] == name]


def test_a_container_that_cannot_be_removed_now_is_cleaned_up_by_the_reconciler(wk, st, monkeypatch):
    from semechka.work_runtime import RuntimeError_
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="echo 1"))
    name = sess_row(st, sid).container_name
    rt = rt_of(st)
    real_remove = rt.remove
    monkeypatch.setattr(rt, "remove", lambda *a, **k: (_ for _ in ()).throw(RuntimeError_("daemon unavailable")))
    assert wk.delete(f"/api/work/sessions/{sid}").json()["container_removed"] is False
    assert wk.get(f"/api/work/sessions/{sid}").status_code == 404 and rt.state(name) == "running"
    monkeypatch.setattr(rt, "remove", real_remove)
    rep = work_svc.reconcile(st)
    assert rep["removed"] == [name] and rt.state(name) == "absent"


def test_reconcile_adopts_what_exists_removes_orphans_and_recreates_what_vanished(wk, st):
    from semechka.work_runtime import ContainerSpec
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_write_file", path="keep.txt", content="data"))
    name = sess_row(st, sid).container_name
    rt = rt_of(st)
    rt.create(ContainerSpec("ws_orphanorphanorphan01"))
    (rt.base / "foreign").mkdir()
    calls_before = [c for c in rt.calls if c[0] == "create"]
    rep = work_svc.startup(st)["reconcile"]                      # what happens when Semechka starts
    assert rep["removed"] == ["work-orphanorphanorphan01"] and (rt.base / "foreign").exists()
    assert rt.state(name) == "running" and [c for c in rt.calls if c[0] == "create"] == calls_before   # adopted, not recreated
    # the container vanishes but the volume stays: it is started again on use, with the same files
    rt.remove(name, volume=False)
    work_svc.reconcile(st)
    assert sess_row(st, sid).container_state == "absent"
    one_shot(st, wk, sid, L.tool("work_read_file", path="keep.txt"))
    assert "data" in result_of(wk, sid)["text"] and rt.state(name) == "running"
    # everything vanishes: the person is told, and the session starts over with an empty workspace
    rt.remove(name, volume=True)
    work_svc.reconcile(st)
    assert any("removed outside Semechka" in e["summary"] for e in events(wk, sid))
    one_shot(st, wk, sid, L.tool("work_list_dir"))
    assert "(empty)" in result_of(wk, sid)["text"] or ".home" in result_of(wk, sid)["text"]


def test_idle_containers_stop_and_wake_with_their_workspace(wk, st):
    setting(wk, "work.idle_stop_minutes", 1)
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_write_file", path="p.txt", content="persist"))
    name = sess_row(st, sid).container_name
    rt = rt_of(st)
    assert work_svc.stop_idle(st) == 0                                   # just used: left running
    with st.db.session(write=True) as s:
        s.get(WorkSession, sid).last_active_at = time.time() - 3600
    assert work_svc.stop_idle(st) == 1 and rt.state(name) == "stopped"
    assert sess_row(st, sid).container_state == "stopped"
    assert wk.get(f"/api/work/sessions/{sid}/state").json()["container"]["state"] == "stopped"
    assert wk.get(f"/api/work/sessions/{sid}/files/content", params={"path": "p.txt"}).content == b"persist"   # REST wakes it
    assert rt.state(name) == "running"
    with st.db.session(write=True) as s:
        s.get(WorkSession, sid).last_active_at = time.time() - 3600
        s.get(WorkSession, sid).status = "running"
    assert work_svc.stop_idle(st) == 0                                   # a session that is working is never stopped
    with st.db.session(write=True) as s:
        s.get(WorkSession, sid).status = "idle"
    work_svc.stop_idle(st)
    one_shot(st, wk, sid, L.tool("work_read_file", path="p.txt"))
    assert "persist" in result_of(wk, sid)["text"]
    assert any(e["summary"] == "Container started" for e in events(wk, sid))


def test_changing_the_network_recreates_the_container_but_keeps_the_workspace(wk, st):
    setting(wk, "work.network_allowed", True)
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_write_file", path="p.txt", content="same volume"))
    name = sess_row(st, sid).container_name
    assert wk.patch(f"/api/work/sessions/{sid}", json={"network": "internet"}).json()["network"] == "internet"
    rt = rt_of(st)
    assert rt.state(name) == "absent" and rt._dir(name).exists()          # container gone, volume kept
    L.reset()
    one_shot(st, wk, sid, L.tool("work_read_file", path="p.txt"))
    assert "same volume" in result_of(wk, sid)["text"] and rt.created[name].network == "internet"
    # not while it is working
    L.reset()
    L.say(L.final("x"))
    wk.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    assert wk.patch(f"/api/work/sessions/{sid}", json={"network": "none"}).status_code == 409


def test_deleting_a_person_or_an_organisation_takes_their_sessions_with_them(wk, st, app):
    two = admin2(wk, app, "gone@acme.test")
    sid = new_session(two)
    one_shot(st, two, sid, L.tool("work_shell", cmd="echo 1"))
    name = sess_row(st, sid).container_name
    assert rt_of(st).state(name) == "running"
    assert wk.delete(f"/api/admin/users/{two.user_id}").status_code == 200
    assert rt_of(st).state(name) == "absent"
    with st.db.session() as s:
        assert s.get(WorkSession, sid) is None


# ---- schema -------------------------------------------------------------------------------------------------------------
def tables(st):
    from sqlalchemy import inspect
    with st.db.engine.connect() as c:
        i = inspect(c)
        return set(i.get_table_names()), {t: {x["name"] for x in i.get_columns(t)} for t in ("agents", "agent_runs")}


def test_migration_11_applies_to_the_previous_schema_and_rolls_back_cleanly(wk, st, cfg):
    from semechka import migrations
    from sqlalchemy import text
    sid = new_session(wk)
    one_shot(st, wk, sid, L.tool("work_shell", cmd="echo 1"))
    aid = wk.post("/api/work/sessions", json={}).json()["id"]
    assert migrations.SCHEMA_VERSION == 11
    with st.db.engine.connect() as c:
        roles_now = dict(c.execute(text("select id, grants from roles")).fetchall())
    # a cloud agent that must survive both directions
    with st.db.engine.begin() as c:
        org = c.execute(text("select id from orgs limit 1")).scalar()
        wsid = c.execute(text("select id from workspaces where org_id=:o and policy not like '%system%' limit 1"), {"o": org}).scalar()
        if wsid is None:
            c.execute(text("insert into workspaces(id, org_id, name, description, visibility, inherit_members, policy, quotas, archived, created_by, created_at) "
                           "values ('w_keep', :o, 'Keep', '', 'private', 1, '{}', '{}', 0, '', 0)"), {"o": org})
            wsid = "w_keep"
        c.execute(text("insert into agents(id, org_id, workspace_id, name, description, instructions_enc, tools, context_ids, memory, limits, approval, "
                       "network, version, archived, created_by, created_at, updated_at, kind) values ('ag_keep', :o, :w, 'Keep', '', '', '[]', '[]', '{}', "
                       "'{}', 'sensitive', '{}', 1, 0, '', 0, 0, 'cloud')"), {"o": org, "w": wsid})
    ran = migrations.downgrade(st.db, 10, cfg.data_dir)
    assert len(ran) == 1 and "10" in ran[0]
    tabs, cols = tables(st)
    assert not {"work_sessions", "work_messages", "work_events", "work_artifacts", "work_skills"} & tabs
    assert "kind" not in cols["agents"] and "session_id" not in cols["agent_runs"]
    with st.db.engine.connect() as c:
        assert c.execute(text("select count(*) from agents")).scalar() == 1                    # the work agents went, the cloud agent stayed
        assert c.execute(text("select count(*) from agent_runs")).scalar() == 0
        assert c.execute(text("select count(*) from jobs where kind='work'")).scalar() == 0
        assert c.execute(text("select count(*) from workspaces where policy like '%system%'")).scalar() == 0
        assert c.execute(text("select count(*) from setting_values where key like 'work.%' or key like 'modes.work.%'")).scalar() == 0
        assert migrations.current_version(c) == 10
        old_roles = dict(c.execute(text("select id, grants from roles")).fetchall())
    assert "work.network" not in old_roles["platform_admin"] and "mode.work" not in old_roles["platform_admin"]
    assert "work.network" not in old_roles["owner"]
    assert not (cfg.data_dir / "work_artifacts").exists()
    # forward again: the previous version's data upgrades
    ran = migrations.apply(st.db, cfg.data_dir)
    assert len(ran) == 1 and ran[0].startswith("11:")
    tabs, cols = tables(st)
    assert {"work_sessions", "work_messages", "work_events", "work_artifacts", "work_skills"} <= tabs
    assert "kind" in cols["agents"] and "session_id" in cols["agent_runs"]
    with st.db.engine.connect() as c:
        assert c.execute(text("select kind from agents where id='ag_keep'")).scalar() == "cloud"
        assert migrations.current_version(c) == 11
        new_roles = dict(c.execute(text("select id, grants from roles")).fetchall())
    for perm in ("mode.work", "work.network", "work.skills.manage"):
        assert perm in new_roles["platform_admin"], perm
    assert "work.network" in new_roles["owner"] and "mode.work" not in new_roles["member"]
    assert sid and aid and roles_now
    assert migrations.apply(st.db, cfg.data_dir) == []                                         # idempotent


# ---- small models' JSON slips ---------------------------------------------------------------------------------------------
def test_repair_fixes_the_syntax_slips_small_models_make_and_never_the_content():
    from semechka.work_prompt import repair
    code = "with open('a.txt', 'w') as f:\\n    f.write('x')"
    good = {"tool": "work_python", "args": {"code": "with open('a.txt', 'w') as f:\n    f.write('x')", "timeout_s": 120}}
    # what a 0.8B model really sent: fenced, apostrophes escaped, closed with ] instead of }
    real = "```json\n{\"tool\": \"work_python\", \"args\": {\"code\": \"with open(\\'a.txt\\', \\'w\\') as f:\\n    f.write(\\'x\\')\", \"timeout_s\": 120}]\n```"
    assert repair(real) == (good, "")
    assert repair('{"tool": "work_shell", "args": {"cmd": "echo hi"}')[0] == {"tool": "work_shell", "args": {"cmd": "echo hi"}}   # missing closer
    assert repair('Sure! {"tool": "work_shell", "args": {"cmd": "a\nb"}} done')[0]["args"]["cmd"] == "a\nb"                      # raw line break, text around
    assert repair('{"final": "it\\\\\'s done"}')[0] == {"final": "it\\'s done"}                  # an escaped backslash before an apostrophe is content
    assert repair('<think>hm {"x": 1}</think>{"final": "ok"}')[0] == {"final": "ok"}
    act, why = repair('{"tool": "work_shell", "args": {"cmd": "echo "hi""}}')
    assert act is None and why.startswith("invalid JSON") and "end the object with }}" in why
    assert repair("no json here") == (None, "no JSON object found")
    assert repair('{"hello": 1}') == (None, 'the JSON object has neither a "tool" nor a "final" key')


def test_a_slightly_broken_action_runs_and_the_run_says_it_was_repaired(wk, st):
    sid = new_session(wk)
    broken = "```json\n{\"tool\": \"work_python\", \"args\": {\"code\": \"print(\\'it works\\')\"}]\n```"
    say_and_run(st, wk, sid, "go", broken, L.final("done"))
    d = result_of(wk, sid)
    assert d["status"] == "done" and "it works" in d["text"]
    with st.db.session() as s:
        steps = s.execute(select(AgentStep).where(AgentStep.kind == "model").order_by(AgentStep.n)).scalars().all()
        assert steps[0].meta.get("repaired") is True and "repaired" not in steps[1].meta and "parse_error" not in steps[0].meta
    # a reply that cannot be repaired tells the model why, naming the real problem (not the inner object)
    L.reset()
    say_and_run(st, wk, sid, "again", '{"tool": "work_shell", "args": {"cmd": "echo "x""}}', L.final("ok"))
    assert any("invalid JSON" in m["content"] and "end the object with" in m["content"] for m in L.SEEN[-1] if m["role"] == "user")


# ---- long runs stay inside the model's context window ------------------------------------------------------------------------
def test_a_long_run_is_shortened_to_fit_the_window_and_keeps_the_recent_steps(wk, st):
    from semechka import chat_svc
    from semechka.work_prompt import context_budget_chars
    sid = new_session(wk, objective="Keep going")
    mid = [L.tool("work_python", code=f"print('step{i} ' + 'x' * 5000)") for i in range(14)]
    say_and_run(st, wk, sid, "a medium run", *mid, L.final("done"))
    mid_total = sum(len(m["content"]) for m in L.SEEN[-1])
    assert mid_total < 30000 and not any("shortened to fit" in m["content"] for m in L.SEEN[-1])  # shorter results were enough
    assert any("more characters not shown" in m["content"] for m in L.SEEN[-1])
    L.reset()
    chat_svc.learned(st)[sess_row(st, sid).model_id] = {"n_ctx": 8192, "ratio": 0.4}                  # a smaller window: the engine said so
    calls = [L.tool("work_python", code=f"print('step{i} ' + 'x' * 5000)") for i in range(30)]
    say_and_run(st, wk, sid, "do many things", *calls, L.final("done"))
    last = L.SEEN[-1]
    total = sum(len(m["content"]) for m in last)
    with st.db.session() as s:
        budget = context_budget_chars(st, s, sess_row(st, sid).model_id)
    assert budget == int((8192 - 4096 - 300) / 0.4) and total <= budget + 2500, (total, budget)
    roles = [m["role"] for m in last]
    assert roles[0] == "system" and all(a != b for a, b in zip(roles[1:], roles[2:], strict=False))     # still alternating
    task = next(m["content"] for m in last if m["content"].startswith("do many things"))
    assert task.startswith("do many things") and "shortened to fit your context window" in task and "- work_python" in task
    assert "step29" in last[-2]["content"] or "step29" in last[-1]["content"]                          # the newest result is intact
    assert sum(1 for m in last if m["content"].startswith("Tool result")) < 30                          # older ones were folded away
    # a short run is left exactly as it is
    L.reset()
    say_and_run(st, wk, sid, "small", L.tool("work_shell", cmd="echo hi"), L.final("ok"))
    assert not any("shortened to fit" in m["content"] for m in L.SEEN[-1])
    # the engine's own refusal teaches the real window for the next turn
    chat_svc.learned(st).pop(sess_row(st, sid).model_id)
    ext = work_svc.WorkExtension()
    with st.db.session(write=True) as s:
        sess = s.get(WorkSession, sid)
        sess.active_run_id = "ar_fake"
        run = AgentRun(id="ar_fake", job_id="j", agent_id=sess.agent_id, org_id=sess.org_id, workspace_id="w", user_id=sess.user_id, task_enc="", session_id=sid)
        ext.on_end(st, s, run, "failed", "RuntimeError: request (16674 tokens) exceeds the available context size (8192 tokens)")
    assert chat_svc.learned(st)[sess_row(st, sid).model_id]["n_ctx"] == 8192
    with st.db.session() as s:
        assert context_budget_chars(st, s, sess_row(st, sid).model_id) == int((8192 - 4096 - 300) / 0.4)
