"""Work Mode on Goose: model gateway, session lifecycle, event relay, approvals, cancel, resume, isolation of owners.
The ACP peer is a test double (fake_goose.py); the real Goose is exercised by the Spark end-to-end run."""
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from semechka import goose_proxy, goose_svc
from semechka.work_runtime import LocalDirRuntime

from .conftest import PW, Server, _free_port, make_user
from .test_work_contract import setting  # noqa: F401  (helper)

HERE = Path(__file__).parent
CKPT = (HERE.parent.parent / "work-image" / "goose" / "ckpt.py").read_text()

# ---- the router double ------------------------------------------------------------------------------------------------
ROUTER = {"states": {"script-model": "loaded", "big-coder": "unloaded"}, "seen": [], "loads": []}
rapp = FastAPI()


@rapp.get("/v1/models")
def r_models(request: Request):
    return {"data": [{"id": k, "status": {"value": v}} for k, v in ROUTER["states"].items()]}


@rapp.post("/models/load")
async def r_load(request: Request):
    body = await request.json()
    ROUTER["loads"].append(body["model"])
    ROUTER["states"][body["model"]] = "loading"
    threading.Timer(0.6, lambda: ROUTER["states"].__setitem__(body["model"], "loaded")).start()
    return {"ok": True}


@rapp.post("/v1/chat/completions")
async def r_chat(request: Request):
    body = await request.json()
    ROUTER["seen"].append({"auth": request.headers.get("authorization"), "model": body.get("model"), "stream": body.get("stream")})
    if body.get("stream"):
        def gen():
            for t in ("he", "llo"):
                yield f'data: {json.dumps({"choices": [{"delta": {"content": t}}]})}\n\n'
            yield "data: [DONE]\n\n"
        return StreamingResponse(gen(), media_type="text/event-stream")
    return JSONResponse({"choices": [{"message": {"content": "hello"}}]})


@pytest.fixture(scope="module")
def router_url():
    srv = Server(rapp, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


class FakeGooseRuntime(LocalDirRuntime):
    """Container double with the Goose parts: the process is the ACP peer double, checkpoints run the real ckpt.py on the directory."""

    def goose_argv(self, name, env):
        d = self._dir(name)
        e = dict(os.environ, FAKE_MODE={"approve": "approve", "auto": "auto", "smart_approve": "approve"}[env["GOOSE_MODE"]])
        self.last_env = dict(env)
        return [sys.executable, str(HERE / "fake_goose.py"), str(d / ".home"), str(d)], e

    def kill_goose(self, name):
        pass

    def run_in(self, name, argv, *, stdin=None, timeout=60):
        import subprocess
        d = self._dir(name)
        script = d.parent / f"{name}-ckpt.py"
        script.write_text(CKPT.replace('ROOT = "/work"', f'ROOT = "{d}"').replace('STORE = "/work/.home/snap"', f'STORE = "{d}/.home/snap"'))
        r = subprocess.run([sys.executable, str(script), *argv[3:]], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr


@pytest.fixture
def gk(env, app, st, tmp_path, router_url):
    """The scripted-model install with Work on Goose switched on, the router double, a verified-models file and the runtime double."""
    ROUTER["states"].update({"script-model": "loaded", "big-coder": "unloaded"})
    ROUTER["seen"].clear(); ROUTER["loads"].clear()
    key = tmp_path / "router.key"; key.write_text("router-secret-key\n")
    ver = tmp_path / "verified.json"
    ver.write_text(json.dumps({"models": {"script-model": {"coding_agent_compatible": True, "loaded_memory_mib": 100},
                                           "big-coder": {"coding_agent_compatible": True},
                                           "tiny": {"coding_agent_compatible": False}}}))
    st.goose_runtime = FakeGooseRuntime(tmp_path / "runtime")
    for k, v in {"goose.enabled": True, "modes.work.enabled": True, "goose.router_url": router_url, "goose.router_key_file": str(key),
                 "goose.verified_file": str(ver), "goose.sock_root": tempfile.mkdtemp(prefix="sg", dir="/tmp"), "goose.approval_timeout_s": 30, "goose.default_model": "script-model"}.items():
        setting(env, k, v)
    env.rt = st.goose_runtime
    yield env
    goose_svc._mgr(st).close_all()


def conv(api) -> str:
    r = api.post("/api/chat/conversations", json={"mode": "chat"})
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def wait(fn, timeout=15, what=""):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError(f"timed out: {what}")


def evs(api, cid, since=0):
    return api.get(f"/api/work/goose/conversations/{cid}/events", params={"format": "json", "since": since}).json()["events"]


def types(api, cid):
    return [e["type"] for e in evs(api, cid)]


def run_turn(api, cid, text, answer=None):
    done = sum(e["type"] == "turn_end" for e in evs(api, cid))
    r = api.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": text})
    assert r.status_code == 202, r.text
    if answer:
        before = sum(e["type"] == "approval_request" for e in evs(api, cid))
        ap = wait(lambda: [e for e in evs(api, cid) if e["type"] == "approval_request"][before:][:1] and [e for e in evs(api, cid) if e["type"] == "approval_request"][before], what="approval request")
        r2 = api.post(f"/api/work/goose/conversations/{cid}/approvals/{ap['approval']['id']}", json={"decision": answer})
        assert r2.status_code == 200, r2.text
    wait(lambda: sum(e["type"] == "turn_end" for e in evs(api, cid)) > done, what="turn_end")
    return evs(api, cid)


# ---- gateway --------------------------------------------------------------------------------------------------------------------
def test_gateway_pins_model_hides_key_and_streams(tmp_path, router_url):
    ROUTER["seen"].clear()
    key = tmp_path / "k"; key.write_text("real-key")
    gw = goose_proxy.ModelGateway(str(tmp_path / "s" / "llm.sock"), goose_proxy.GatewayConfig(router_url, str(key), lambda: {"script-model"}, "script-model"))
    try:
        def post(body, path="/v1/chat/completions", extra=""):
            raw = json.dumps(body).encode()
            req = f"POST {path} HTTP/1.1\r\nHost: x\r\nAuthorization: Bearer agent-chosen\r\n{extra}Content-Type: application/json\r\nContent-Length: {len(raw)}\r\n\r\n".encode() + raw
            return goose_proxy.unix_request(gw.sock_path, req)
        out = post({"model": "script-model", "stream": True, "messages": []})
        assert out.startswith(b"HTTP/1.1 200") and b"hello" not in out and b"[DONE]" in out and b'"he"' in out
        assert ROUTER["seen"][-1] == {"auth": "Bearer real-key", "model": "script-model", "stream": True}   # agent's own header was dropped
        assert post({"model": "big-coder", "messages": []}).startswith(b"HTTP/1.1 403")
        assert len(ROUTER["seen"]) == 1
        assert post({"model": "script-model"}, path="/v1/embeddings").startswith(b"HTTP/1.1 404")
        assert post({"model": "script-model"}, path="/models/unload").startswith(b"HTTP/1.1 404")
        listing = goose_proxy.unix_request(gw.sock_path, b"GET /v1/models HTTP/1.1\r\nHost: x\r\n\r\n")
        assert b"script-model" in listing and b"big-coder" not in listing
        assert goose_proxy.unix_request(gw.sock_path, b"DELETE /v1/models HTTP/1.1\r\nHost: x\r\n\r\n").startswith(b"HTTP/1.1 405")
        assert oct(os.stat(gw.sock_path).st_mode & 0o777) == "0o666"
    finally:
        gw.close()
    assert not os.path.exists(gw.sock_path)


# ---- gate, status, ownership --------------------------------------------------------------------------------------------------
def test_off_by_default_and_gated(env, st):
    assert env.get("/api/work/goose/status").status_code == 403
    assert env.get("/api/work/goose/status").json()["detail"]["code"] == "disabled"


def test_status_lists_only_coding_verified_models(gk):
    s = gk.get("/api/work/goose/status").json()
    assert s["enabled"] and s["ready"], s
    assert [m["id"] for m in s["models"]] == ["script-model", "big-coder"]   # "tiny" is not coding-verified; big-coder has no catalogue row (owner holds models.use_all)
    assert {a["id"] for a in s["autonomy"]} == {"ask", "smart", "auto"}
    assert [m["id"] for m in s["mcp_servers"]] == ["repo-stats"]


def test_session_is_owner_only_and_idempotent(gk, app):
    cid = conv(gk)
    a = gk.post(f"/api/work/goose/conversations/{cid}/session", json={}).json()
    b = gk.post(f"/api/work/goose/conversations/{cid}/session", json={}).json()
    assert a["id"] == b["id"] and a["state"] == "idle" and a["model"] == "script-model" and a["autonomy"] == "ask"
    other = make_user(gk, app, "bob@example.test", role="platform_admin")
    for method, url in (("get", f"/session"), ("get", "/transcript"), ("get", "/events"), ("post", "/cancel"), ("get", "/files")):
        r = getattr(other, method)(f"/api/work/goose/conversations/{cid}{url}")
        assert r.status_code == 404, (url, r.status_code)
    assert other.post(f"/api/work/goose/conversations/{cid}/session", json={}).status_code == 404   # not their conversation
    assert gk.get(f"/api/work/goose/conversations/{cid}/session").json()["id"] == a["id"]
    assert gk.post(f"/api/work/goose/conversations/{cid}/session", json={"model": "tiny"}).status_code in (200, 409)
    bad = conv(gk)
    assert gk.post(f"/api/work/goose/conversations/{bad}/session", json={"model": "tiny"}).status_code == 409
    assert gk.post(f"/api/work/goose/conversations/{bad}/session", json={"mcp": ["nope"]}).status_code == 422


# ---- the relay ----------------------------------------------------------------------------------------------------------------------
def test_prompt_with_allowed_tool_streams_events_and_mirrors_messages(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    ev = run_turn(gk, cid, "please run a tool", answer="allow_once")
    t = [e["type"] for e in ev]
    assert t.index("turn_start") < t.index("tool_start") < t.index("approval_request") < t.index("approval_resolved") < t.index("tool_end") < t.index("files_changed") < t.index("turn_end")
    assert [e["seq"] for e in ev] == sorted(e["seq"] for e in ev) and len({e["seq"] for e in ev}) == len(ev)
    ts = next(e for e in ev if e["type"] == "tool_start")
    assert ts["tool"] == "shell" and ts["extension"] == "developer" and ts["input"] == {"command": "echo hi > out.txt"}
    te = next(e for e in ev if e["type"] == "tool_end")
    assert te["status"] == "completed" and te["exit_code"] == 0
    assert any(e["type"] == "tool_output" and "running" in e["text"] for e in ev)
    ap = next(e for e in ev if e["type"] == "approval_request")["approval"]
    assert ap["options"] == ["allow_once", "allow_always", "deny", "deny_always"] and ap["tool"] == "shell"
    assert next(e for e in ev if e["type"] == "approval_resolved")["decision"] == "allow_once"
    text = "".join(e["text"] for e in ev if e["type"] == "assistant_delta")
    assert text == "Working. Done." and "REPLAYED" not in text
    fc = next(e for e in ev if e["type"] == "files_changed")
    assert fc["files"] == [{"path": "out.txt", "status": "added", "additions": 1, "deletions": 0, "binary": False}]
    d = gk.get(f"/api/work/goose/conversations/{cid}/changes/{fc['turn_id']}").json()
    assert "+hi" in d["diff"]
    assert next(e for e in ev if e["type"] == "turn_end")["stop_reason"] == "end_turn"
    tr = gk.get(f"/api/work/goose/conversations/{cid}/transcript").json()
    kinds = [i["kind"] for i in tr["items"]]
    assert kinds[0] == "user" and "tool" in kinds and "approval" in kinds and "files" in kinds
    assert next(i for i in tr["items"] if i["kind"] == "approval")["decision"] == "allow_once"
    msgs = gk.get(f"/api/chat/conversations/{cid}").json()["messages"]
    assert [m["role"] for m in msgs][-2:] == ["user", "assistant"] and msgs[-1]["content"] == "Working. Done."
    s = gk.get(f"/api/work/goose/conversations/{cid}/session").json()
    assert s["state"] == "idle" and s["goose_session_id"] == "fake_1" and s["turn"] is None


def test_denied_tool_fails_and_workspace_is_untouched(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    ev = run_turn(gk, cid, "tool", answer="deny")
    assert next(e for e in ev if e["type"] == "tool_end")["status"] == "failed"
    assert next(e for e in ev if e["type"] == "approval_resolved")["decision"] == "deny"
    assert not any(e["type"] == "files_changed" for e in ev)
    assert gk.get(f"/api/work/goose/conversations/{cid}/files").json()["entries"] == []


def test_edit_tool_carries_a_unified_diff(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={"autonomy": "auto"})
    assert gk.put(f"/api/work/goose/conversations/{cid}/files", params={"path": "a.py"}, files={"file": ("a.py", b"x = 1\n")}).status_code == 200
    ev = run_turn(gk, cid, "edit it")
    assert not any(e["type"] == "approval_request" for e in ev)                    # auto: never asks
    te = next(e for e in ev if e["type"] == "tool_end")
    assert "-x = 1" in te["diff"]["unified"] and "+x = 2" in te["diff"]["unified"]
    assert gk.get(f"/api/work/goose/conversations/{cid}/files/content", params={"path": "a.py"}).content == b"x = 2\n"


def test_cancel_stops_a_running_turn_and_resolves_approvals(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    assert gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "slow please"}).status_code == 202
    wait(lambda: gk.get(f"/api/work/goose/conversations/{cid}/session").json()["state"] == "running", what="running")
    assert gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "again"}).json()["detail"]["code"] == "turn_running"
    assert gk.post(f"/api/work/goose/conversations/{cid}/cancel").status_code == 200
    wait(lambda: any(e["type"] == "turn_end" for e in evs(gk, cid)), what="turn_end")
    assert next(e for e in evs(gk, cid) if e["type"] == "turn_end")["stop_reason"] == "cancelled"
    wait(lambda: gk.get(f"/api/work/goose/conversations/{cid}/session").json()["state"] == "idle", what="idle")
    # a pending approval is cancelled too
    ev0 = len(evs(gk, cid))
    assert gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "tool"}).status_code == 202
    wait(lambda: any(e["type"] == "approval_request" for e in evs(gk, cid)[ev0:]), what="approval")
    assert gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "x"}).json()["detail"]["code"] in ("turn_running", "awaiting_approval")
    gk.post(f"/api/work/goose/conversations/{cid}/cancel")
    wait(lambda: sum(e["type"] == "turn_end" for e in evs(gk, cid)) == 2, what="second turn_end")
    assert [e["decision"] for e in evs(gk, cid) if e["type"] == "approval_resolved"] == ["cancelled"]


def test_unanswered_approval_times_out_as_denied(gk, st):
    setting(gk, "goose.approval_timeout_s", 10)
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "tool"})
    live = wait(lambda: goose_svc._mgr(st).live.values() and list(goose_svc._mgr(st).live.values())[0], what="live")
    ap = wait(lambda: next(iter(live.approvals.values()), None), what="approval")
    ap.expires = time.time()
    live.resolve(ap, "timeout", "timeout")
    wait(lambda: any(e["type"] == "turn_end" for e in evs(gk, cid)), what="turn_end")
    assert next(e for e in evs(gk, cid) if e["type"] == "tool_end")["status"] == "failed"
    assert next(e for e in evs(gk, cid) if e["type"] == "approval_resolved")["by"] == "timeout"


def test_model_loading_is_reported_before_work_starts(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    ROUTER["states"]["script-model"] = "unloaded"
    ev = run_turn(gk, cid, "hello")
    phases = [e["phase"] for e in ev if e["type"] == "model_loading"]
    assert phases == ["loading", "ready"] and ROUTER["loads"] == ["script-model"]
    assert [e["state"] for e in ev if e["type"] == "state"][:2] == ["loading_model", "starting"]


def test_resume_after_restart_reuses_the_goose_session_and_hides_replayed_history(gk, st):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    run_turn(gk, cid, "first")
    # the service dies while a prompt runs: the database still names the turn, the process and the in-memory state are gone
    gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "slow"})
    wait(lambda: gk.get(f"/api/work/goose/conversations/{cid}/session").json()["state"] == "running", what="running")
    gk.post(f"/api/work/goose/conversations/{cid}/cancel")
    wait(lambda: gk.get(f"/api/work/goose/conversations/{cid}/session").json()["state"] == "idle", what="idle")
    goose_svc._mgr(st).close_all()
    st.__dict__.pop("goose_mgr")
    n = len(evs(gk, cid))
    with st.db.session(write=True) as s:
        from semechka.models_goose import GooseSession
        row = s.query(GooseSession).first()
        row.current_turn_id = "t_stranded"
    goose_svc.startup(st)
    tail = evs(gk, cid)[n:]
    assert [e["stop_reason"] for e in tail if e["type"] == "turn_end"] == ["interrupted"]
    ev = run_turn(gk, cid, "second")
    new = [e for e in ev if e["seq"] > tail[-1]["seq"]]
    assert any(e["type"] == "notice" and e["code"] == "resumed" for e in new)
    assert "REPLAYED" not in "".join(e.get("text", "") for e in new)
    assert gk.get(f"/api/work/goose/conversations/{cid}/session").json()["goose_session_id"] == "fake_1"


def test_mcp_and_skills_reach_goose_through_its_own_mechanisms(gk, st):
    cid = conv(gk)
    sk = gk.post("/api/work/skills", json={"scope": "user", "name": "code-review", "title": "Code review", "description": "Review checklist",
                                            "body_md": "Check naming. Check tests."})
    assert sk.status_code == 200, sk.text
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={"mcp": ["repo-stats"], "skills": [sk.json()["id"]]})
    run_turn(gk, cid, "hello")
    d = gk.rt._dir(next(iter(gk.rt.created)))
    sessions = json.loads((d / ".home" / "fake_sessions.json").read_text())
    assert sessions["fake_1"]["mcp"] == [{"name": "repo-stats", "command": "/usr/local/bin/python3", "args": ["-I", "/opt/semechka/mcp/repo_stats.py"], "env": []}]
    md = (d / ".home" / ".agents" / "skills" / "code-review" / "SKILL.md").read_text()
    assert md.startswith("---\nname: code-review") and "Check naming" in md
    assert gk.rt.last_env["GOOSE_PROVIDER"] == "semechka" and "SEMECHKA_LLM_KEY" in gk.rt.last_env and "router-secret" not in json.dumps(gk.rt.last_env)


def test_stop_keeps_workspace_and_delete_removes_everything(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={"autonomy": "auto"})
    run_turn(gk, cid, "tool")
    assert gk.post(f"/api/work/goose/conversations/{cid}/session/stop").json()["state"] in ("idle",)
    assert [e["name"] for e in gk.get(f"/api/work/goose/conversations/{cid}/files").json()["entries"]] == ["out.txt"]
    assert gk.delete(f"/api/work/goose/conversations/{cid}/session").status_code == 200
    assert gk.get(f"/api/work/goose/conversations/{cid}/session").status_code == 404
    assert gk.get(f"/api/chat/conversations/{cid}").status_code == 200      # the conversation itself stays


def test_sse_stream_replays_and_follows(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={"autonomy": "auto"})
    gk.post(f"/api/work/goose/conversations/{cid}/prompt", json={"text": "tool"})
    got = []
    with gk.c.stream("GET", f"/api/work/goose/conversations/{cid}/events", headers={"accept": "text/event-stream"}) as r:
        assert r.headers["content-type"].startswith("text/event-stream")
        cur = {}
        for line in r.iter_lines():
            if line.startswith("event: "):
                cur["event"] = line[7:]
            elif line.startswith("id: "):
                cur["id"] = int(line[4:])
            elif line.startswith("data: "):
                cur["data"] = json.loads(line[6:])
                got.append(cur); cur = {}
                if cur.get("event") == "turn_end" or got[-1]["event"] == "turn_end":
                    break
    assert got[-1]["event"] == "turn_end" and [g["id"] for g in got] == sorted(g["id"] for g in got)
    assert got[0]["data"]["seq"] == got[0]["id"]


def test_member_without_the_mode_permission_is_refused(gk, app):
    bob = make_user(gk, app, "bob2@example.test")
    r = bob.get("/api/work/goose/status")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"      # the default member role has no mode.work


def test_changing_tools_between_prompts_restarts_goose_quietly_and_resumes_the_session(gk):
    cid = conv(gk)
    gk.post(f"/api/work/goose/conversations/{cid}/session", json={})
    run_turn(gk, cid, "one")
    assert gk.patch(f"/api/work/goose/conversations/{cid}/session", json={"mcp": ["repo-stats"]}).status_code == 200
    ev = run_turn(gk, cid, "two")
    assert not any(e["type"] == "error" for e in ev)                          # ending the old process on purpose is not a failure
    assert any(e["type"] == "notice" and e["code"] == "resumed" for e in ev)
    assert gk.get(f"/api/work/goose/conversations/{cid}/session").json()["goose_session_id"] == "fake_1"
    assert gk.patch(f"/api/work/goose/conversations/{cid}/session", json={"mcp": ["nope"]}).status_code == 422
