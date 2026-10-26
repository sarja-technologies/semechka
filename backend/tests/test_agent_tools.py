"""Sandbox-backed and network tools of cloud agents. The container itself is replaced by a LOCAL SUBPROCESS stand-in
(`fake_sandbox`), so these tests cover everything around it - approval, permissions, input selection, output vetting,
limits, observations - and NOT isolation. Isolation is proven against the real sandbox by scripts/sandbox_probe.py
(18 hostile jobs; results in docs/ENTERPRISE.md)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select

from semechka import agent_svc, sandbox_svc
from semechka.models_agents import AgentStep

from . import fake_llm as L
from .test_agents import drive, last_obs, marketing, mk_agent, run_agent
from .test_files_http import up


@pytest.fixture
def fake_sandbox(monkeypatch):
    calls = []

    def run(host, image, spec, inputs, lim, *, job_label=""):
        calls.append({"host": host, "image": image, "spec": spec, "inputs": dict(inputs), "lim": lim})
        if spec["mode"] == "tool":
            return sandbox_svc.Result(0, False, 0.1, "ffprobe version test\n", "", {}, False, {"peak_memory_mb": 12.5})
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "in").mkdir()
            (Path(d) / "out").mkdir()
            for n, b in inputs.items():
                (Path(d) / "in" / n).write_bytes(b)
            r = subprocess.run([sys.executable, "-I", "-B", "-c", spec["code"]], cwd=d, capture_output=True, timeout=30,
                               env={"IN": f"{d}/in", "OUT": f"{d}/out", "PATH": os.environ["PATH"]}, check=False)
            files = {p.name: p.read_bytes() for p in (Path(d) / "out").iterdir() if p.is_file()}
        return sandbox_svc.Result(r.returncode, False, 0.2, r.stdout.decode(), r.stderr.decode(), files, False, {"peak_memory_mb": 30.0})
    monkeypatch.setattr(sandbox_svc, "run", run)
    return calls


def enable_sandbox(env):
    assert env.put("/api/admin/settings/sandbox.enabled", json={"value": True}).status_code == 200


def setup(env, app, st, **agent_kw):
    acme, c, wid, uid, ids, _ctx_id = marketing(env, app)
    mia = c["mia@acme.test"]
    drive(st)
    aid = mk_agent(mia, wid, tools=["list_files", "read_file", "python", "shell"], **agent_kw)
    return acme, c, wid, uid, ids, mia, aid


def tool_steps(client, rid):
    return [s for s in client.get(f"/api/runs/{rid}", params={"detail": 1}).json()["steps"] if s["kind"] == "tool"]


# ---- python ----------------------------------------------------------------------------------------------------
def test_python_needs_the_switch_then_approval_then_runs_and_saves_outputs(env, app, st, fake_sandbox):
    _acme, _c, wid, _uid, _ids, mia, aid = setup(env, app, st)
    code = ("import os, csv\nrows = list(csv.reader(open(os.environ['IN'] + '/data.csv')))\n"
            "total = sum(float(r[1]) for r in rows[1:])\nprint('total', total)\n"
            "open(os.environ['OUT'] + '/totals.csv', 'w').write('metric,value\\ntotal,%s\\n' % total)\n")
    up(mia, wid, "data.csv", b"name,amount\na,100\nb,250.5\n")
    drive(st)
    L.say(L.tool("python", code=code, files=["data.csv"]), L.final("unreachable"))
    rid = run_agent(mia, aid, "total the data")
    drive(st)
    r = mia.get(f"/api/runs/{rid}", params={"detail": 1}).json()  # switch is off: the call is refused, nothing ran
    assert r["status"] == "failed" or tool_steps(mia, rid)[0]["status"] == "awaiting_approval"
    # off by default => once approved it still refuses, honestly
    mia.post(f"/api/runs/{rid}/approve", json={"step": 2, "approve": True})
    L.say(lambda m: L.final("said: " + last_obs(m)))
    drive(st)
    s = tool_steps(mia, rid)[0]
    assert s["status"] == "error" and "not enabled" in s["error"] and fake_sandbox == []

    enable_sandbox(env)
    L.reset()
    L.say(L.tool("python", code=code, files=["data.csv"]), lambda m: L.final("said: " + last_obs(m)))
    rid = run_agent(mia, aid, "total the data")
    drive(st)
    assert mia.get(f"/api/runs/{rid}").json()["status"] == "awaiting_approval"  # python is sensitive: a person decides first
    assert fake_sandbox == []
    assert mia.post(f"/api/runs/{rid}/approve", json={"step": 2, "approve": True}).status_code == 200
    drive(st)
    r = mia.get(f"/api/runs/{rid}", params={"detail": 1}).json()
    assert r["status"] == "succeeded", r
    call = fake_sandbox[0]
    assert call["spec"]["mode"] == "python" and set(call["inputs"]) == {"data.csv"}  # exactly the named file, nothing else
    assert call["lim"].timeout_s == 120 and call["lim"].memory_mb == 1024 and call["image"] == "semechka-sandbox:1"
    res = tool_steps(mia, rid)[0]["result"]
    assert "exit code 0" in res and "total 350.5" in res and "peak memory 30.0 MB" in res and "totals.csv" in res
    assert [a["name"] for a in r["artifacts"]] == ["totals.csv"]
    names = [f["name"] for f in mia.get(f"/api/workspaces/{wid}/files").json()["files"]]
    assert "totals.csv" in names
    fid = r["artifacts"][0]["file_id"]
    assert mia.get(f"/api/files/{fid}/content", params={"download": 1}).content == b"metric,value\ntotal,350.5\n"
    assert "total 350.5" in r["final"]


def test_python_inputs_are_limited_to_what_the_person_and_agent_may_read(env, app, st, fake_sandbox):
    enable_sandbox(env)
    acme, _c, _wid, _uid, _ids, mia, aid = setup(env, app, st, approval="never")
    other = mia.post(f"/api/orgs/{acme}/workspaces", json={"name": "Finance"}).json()["id"]
    secret = up(mia, other, "payroll.csv", b"name,pay\nx,1\n").json()["id"]
    drive(st)
    L.say(L.tool("python", code="print(1)", files=[secret]), L.tool("python", code="print(1)", files=["payroll.csv"]),
          L.tool("python", code="print(2)", files=["budget.xlsx"]), L.final("done"))
    rid = run_agent(mia, aid)
    drive(st)
    st_ = [s["status"] for s in tool_steps(mia, rid)]
    assert st_ == ["error", "error", "done"]
    assert len(fake_sandbox) == 1 and set(fake_sandbox[0]["inputs"]) == {"budget.xlsx"}  # the foreign file never reached the sandbox


def test_python_outputs_are_vetted_like_uploads(env, app, st, fake_sandbox):
    enable_sandbox(env)
    _acme, _c, wid, _uid, _ids, mia, aid = setup(env, app, st, approval="never")
    code = ("import os\nopen(os.environ['OUT'] + '/ok.csv', 'w').write('a,b\\n1,2\\n')\n"
            "open(os.environ['OUT'] + '/evil.exe', 'wb').write(b'MZ' + b'\\0' * 30 + b'PE\\0\\0')\n"
            "open(os.environ['OUT'] + '/fake.pdf', 'wb').write(b'not really a pdf')\n")
    L.say(L.tool("python", code=code), L.final("done"))
    rid = run_agent(mia, aid)
    drive(st)
    res = tool_steps(mia, rid)[0]["result"]
    assert "files saved to the workspace: ok.csv" in res and "files NOT saved" in res and "evil.exe" in res and "fake.pdf" in res
    names = [f["name"] for f in mia.get(f"/api/workspaces/{wid}/files").json()["files"]]
    assert "ok.csv" in names and "evil.exe" not in names and "fake.pdf" not in names


def test_contributors_cannot_use_code_tools_even_through_an_agent(env, app, st, fake_sandbox):
    enable_sandbox(env)
    _acme, c, wid, uid, _ids, mia, aid = setup(env, app, st, approval="never")
    mia.put(f"/api/workspaces/{wid}/members", json={"principal_type": "user", "principal_id": uid["view@acme.test"], "role": "contributor"})
    L.say(L.tool("python", code="print(1)"), L.final("done"))
    rid = run_agent(c["view@acme.test"], aid)
    drive(st)
    s = tool_steps(c["view@acme.test"], rid)[0]
    assert s["status"] == "denied" and "Not permitted" in s["error"] and fake_sandbox == []


# ---- shell -----------------------------------------------------------------------------------------------------
def test_shell_is_an_allow_list_not_a_shell(env, app, st, fake_sandbox):
    enable_sandbox(env)
    _acme, _c, _wid, _uid, _ids, mia, aid = setup(env, app, st, approval="never")
    L.say(L.tool("shell", argv=["bash", "-c", "id"]), L.tool("shell", argv="ffprobe /etc/passwd"), L.tool("shell", argv=[]),
          L.tool("shell", argv=["ffprobe", "-version"]), L.final("done"))
    rid = run_agent(mia, aid)
    drive(st)
    s = tool_steps(mia, rid)
    assert [x["status"] for x in s] == ["error", "error", "error", "done"]
    assert "not allowed" in s[0]["error"] and len(fake_sandbox) == 1 and fake_sandbox[0]["spec"] == {"mode": "tool", "argv": ["ffprobe", "-version"]}


# ---- network ----------------------------------------------------------------------------------------------------
@pytest.fixture
def net(monkeypatch):
    """DNS and HTTP replaced: names resolve to whatever the test says, and requests are answered from a table."""
    state = {"dns": {}, "routes": {}, "hits": []}

    def gai(host, port, *a, **k):
        return [(2, 1, 6, "", (state["dns"].get(host, "93.184.216.34"), port))]

    def handler(req: httpx.Request):
        state["hits"].append(str(req.url))
        return state["routes"].get(str(req.url)) or httpx.Response(404)

    real = httpx.Client
    monkeypatch.setattr("socket.getaddrinfo", gai)
    monkeypatch.setattr(agent_svc.httpx if hasattr(agent_svc, "httpx") else httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    return state


def test_fetch_url_is_deny_by_default_and_blocks_internal_addresses(env, app, st, net):
    _acme, c, wid, _uid, _ids, _ctx_id = marketing(env, app)
    mia = c["mia@acme.test"]
    drive(st)
    no_net = mk_agent(mia, wid, name="NoNet", tools=["fetch_url"], approval="never")
    allowed = mk_agent(mia, wid, name="Net", tools=["fetch_url"], approval="never", network={"allow": ["example.com"]})
    net["dns"]["internal.example.com"] = "10.0.0.5"
    net["dns"]["meta.example.com"] = "169.254.169.254"
    net["dns"]["loop.example.com"] = "127.0.0.1"
    net["routes"]["https://example.com/report.txt"] = httpx.Response(200, text="Quarterly outlook: strong.", headers={"content-type": "text/plain"})
    net["routes"]["https://example.com/jump"] = httpx.Response(302, headers={"location": "https://internal.example.com/secret"})
    net["routes"]["https://example.com/binary"] = httpx.Response(200, content=b"\x7fELF", headers={"content-type": "application/octet-stream"})
    L.say(L.tool("fetch_url", url="https://example.com/report.txt"), L.final("x"))
    rid = run_agent(mia, no_net)
    drive(st)
    assert "no network allow-list" in tool_steps(mia, rid)[0]["error"] and net["hits"] == []
    L.say(L.tool("fetch_url", url="https://evil.test/x"), L.tool("fetch_url", url="https://internal.example.com/x"),
          L.tool("fetch_url", url="https://meta.example.com/latest"), L.tool("fetch_url", url="https://loop.example.com/"),
          L.tool("fetch_url", url="file:///etc/passwd"), L.tool("fetch_url", url="https://user:pw@example.com/report.txt"),
          L.tool("fetch_url", url="https://example.com/jump"), L.tool("fetch_url", url="https://example.com/binary"),
          L.tool("fetch_url", url="https://example.com/report.txt"), L.final("x"))
    rid = run_agent(mia, allowed)
    drive(st)
    s = tool_steps(mia, rid)
    assert [x["status"] for x in s] == ["error"] * 8 + ["done"]
    assert "allow-list" in s[0]["error"] and all("private or internal" in x["error"] for x in s[1:4])
    assert "http(s)" in s[4]["error"] and "http(s)" in s[5]["error"] and "private or internal" in s[6]["error"]  # a redirect is re-checked
    assert "not accepted" in s[7]["error"]
    assert all("internal" not in h and "169.254" not in h for h in net["hits"])  # nothing internal was even requested
    names = [f["name"] for f in mia.get(f"/api/workspaces/{wid}/files").json()["files"]]
    assert "report.txt" in names
    assert "Quarterly outlook" in s[8]["result"] and "untrusted" in s[8]["result"]


def test_network_tool_needs_the_network_permission_and_approval(env, app, st, net):
    _acme, c, wid, uid, _ids, _ctx_id = marketing(env, app)
    mia = c["mia@acme.test"]
    drive(st)
    aid = mk_agent(mia, wid, name="Net", tools=["fetch_url"], network={"allow": ["example.com"]})  # default approval: sensitive
    mia.put(f"/api/workspaces/{wid}/members", json={"principal_type": "user", "principal_id": uid["view@acme.test"], "role": "editor"})
    net["routes"]["https://example.com/a.txt"] = httpx.Response(200, text="hi", headers={"content-type": "text/plain"})
    L.say(L.tool("fetch_url", url="https://example.com/a.txt"), L.final("x"))
    rid = run_agent(c["view@acme.test"], aid)  # an Editor does not hold agent.tool.network
    drive(st)
    r = c["view@acme.test"].get(f"/api/runs/{rid}").json()
    assert r["status"] == "awaiting_approval" and net["hits"] == []
    assert mia.post(f"/api/runs/{rid}/approve", json={"step": 2, "approve": True}).status_code == 200
    drive(st)
    s = tool_steps(c["view@acme.test"], rid)[0]
    assert s["status"] == "denied" and net["hits"] == []  # approval cannot grant what the person's role lacks


def test_org_policy_ceiling_switches_tools_off_for_everyone(env, app, st, fake_sandbox):
    enable_sandbox(env)
    acme, c, _wid, _uid, _ids, mia, aid = setup(env, app, st, approval="never")
    assert c["boss@acme.test"].patch(f"/api/orgs/{acme}", json={"policy": {"deny": ["agent.tool.python"]}}).status_code == 200
    L.say(L.tool("python", code="print(1)"), L.final("x"))
    rid = run_agent(mia, aid)
    drive(st)
    assert tool_steps(mia, rid)[0]["status"] == "denied" and fake_sandbox == []
    with st.db.session() as s:
        assert s.execute(select(AgentStep).where(AgentStep.tool == "python")).scalar_one().error.startswith("Not permitted")
