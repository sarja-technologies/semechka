"""Model gateway, quotas, usage records, diagnostics and memory-aware admission."""
import json

import pytest

from semechka import agent_svc, usage_svc

from . import fake_llm as L
from .test_agent_tools import enable_sandbox, tool_steps
from .test_agents import drive, marketing, mk_agent, run_agent
from .test_files_http import up


def model_ids(client, wid, mode="chat"):
    r = client.get(f"/api/workspaces/{wid}/models", params={"mode": mode})
    assert r.status_code == 200, r.text
    return [m["id"] for m in r.json()]


def test_gateway_narrows_models_by_organisation_then_workspace(env, app, st):
    acme, c, wid, _uid, _ids, _ctx_id = marketing(env, app)
    mia, boss = c["mia@acme.test"], c["boss@acme.test"]
    drive(st)
    allowed = model_ids(mia, wid)
    assert len(allowed) == 1
    m = mia.get(f"/api/workspaces/{wid}/models").json()[0]
    assert m["capabilities"] and "chat" in m["capabilities"] and m["alias"] and m["external"] is False
    aid = mk_agent(mia, wid)
    assert mia.post(f"/api/agents/{aid}/runs", json={"task": "x"}).status_code == 200
    # the organisation allows nothing: every route that picks a model refuses, with a reason
    assert boss.patch(f"/api/orgs/{acme}", json={"policy": {"models": []}}).status_code == 200
    assert model_ids(mia, wid) == []
    r = mia.post(f"/api/agents/{aid}/runs", json={"task": "y"})
    text = json.dumps(r.json())
    assert r.status_code in (403, 409) and ("organisation" in text or "No chat model" in text)
    # allow it again at organisation level; the workspace can narrow it further
    assert boss.patch(f"/api/orgs/{acme}", json={"policy": {"models": allowed}}).status_code == 200
    assert model_ids(mia, wid) == allowed
    assert mia.patch(f"/api/workspaces/{wid}", json={"policy": {"models": []}}).status_code == 200
    assert model_ids(mia, wid) == []
    r = mia.post(f"/api/agents/{aid}/runs", json={"task": "z"})
    assert r.status_code in (403, 409)
    # unknown ids are refused, not stored
    assert boss.patch(f"/api/orgs/{acme}", json={"policy": {"models": ["m_nope"]}}).status_code == 422
    assert mia.patch(f"/api/workspaces/{wid}", json={"policy": {"models": ["m_nope"]}}).status_code == 422
    # an outsider cannot ask the gateway about someone else's workspace
    other = company_outsider(env, app)
    assert other.get(f"/api/workspaces/{wid}/models").status_code == 404


def company_outsider(env, app):
    from .test_files_http import company
    _globex, g = company(env, app, "Globex", "boss@globex.test", {"ops@globex.test": "dept_admin"})
    return g["ops@globex.test"]


def test_daily_agent_run_quota_and_usage_records(env, app, st):
    acme, c, wid, _uid, _ids, _ctx_id = marketing(env, app)
    mia, boss, view = c["mia@acme.test"], c["boss@acme.test"], c["view@acme.test"]
    drive(st)
    aid = mk_agent(mia, wid, tools=["list_files", "read_file"])
    assert boss.patch(f"/api/orgs/{acme}", json={"quotas": {"agent_runs_per_day": 2, "storage_mb": 50}}).status_code == 200
    L.say(L.tool("list_files"), L.final("one"), L.final("two"))
    r1 = run_agent(mia, aid)
    drive(st)
    r2 = run_agent(mia, aid)
    drive(st)
    r = mia.post(f"/api/agents/{aid}/runs", json={"task": "third"})
    assert r.status_code == 429 and "2 agent runs per day" in json.dumps(r.json())
    u = boss.get(f"/api/orgs/{acme}/usage").json()
    assert u["agent_runs"]["last_24h"]["runs"] == 2 and u["agent_runs"]["last_24h"]["model_calls"] == 3  # list_files+final, final
    assert u["agent_runs"]["last_24h"]["tool_calls"] == 1 and u["agent_runs"]["last_24h"]["prompt_tokens"] > 0
    assert u["counts"]["workspaces"] == 1 and u["counts"]["agents"] == 1 and u["counts"]["contexts"] == 1 and u["storage"]["files"] == 3
    assert u["storage"]["used_bytes"] > 0 and u["storage"]["quota_mb"] == 50 and u["quotas"]["agent_runs_per_day"] == 2
    assert u["jobs_24h"].get("succeeded", 0) >= 2
    # plain members do not see the organisation's usage; workspace usage needs only workspace access
    assert view.get(f"/api/orgs/{acme}/usage").status_code == 403
    wu = mia.get(f"/api/workspaces/{wid}/usage").json()
    assert wu["agents"] == 1 and wu["agent_runs_24h"]["runs"] == 2 and wu["storage"]["files"] == 3
    assert r1 != r2


def test_platform_diagnostics_are_operator_only_and_carry_no_content(env, app, st):
    _acme, c, wid, _uid, _ids, _ctx_id = marketing(env, app)
    mia = c["mia@acme.test"]
    drive(st)
    up(mia, wid, "very-secret-merger-plan.md", b"# merger\nacquire globex in march\n")
    drive(st)
    d = env.get("/api/admin/cloud/status").json()
    assert d["jobs"]["ingest"]["succeeded"] >= 4 and d["performance_24h"]["ingest"]["finished_24h"] >= 4
    assert d["performance_24h"]["ingest"]["p95_s"] >= 0 and "memory_available_mb" in d and d["storage"]["total_bytes"] > 0
    assert d["embeddings"]["configured"] is False and d["sandbox"]["enabled"] is False
    flat = json.dumps(d) + json.dumps(env.get("/api/admin/cloud/jobs").json())
    assert "merger" not in flat and "very-secret" not in flat and "globex" not in flat.lower().replace("acme", "")
    assert mia.get("/api/admin/cloud/status").status_code == 403 and mia.get("/api/admin/cloud/jobs").status_code == 403
    q = env.get("/api/admin/cloud/jobs", params={"status": "succeeded"}).json()
    assert q and all(j["status"] == "succeeded" and "payload" not in j for j in q)


def test_sandbox_waits_for_memory_and_then_says_so(env, app, st, monkeypatch):
    _acme, _c, _wid, _uid, _ids, _ctx_id = marketing(env, app)
    class TC:  # the pieces _admit_memory looks at
        class st:
            class settings:
                deployment = staticmethod(lambda s, k: 8192)
        s = None
    monkeypatch.setattr(usage_svc, "mem_available_mb", lambda: 3000)
    with pytest.raises(agent_svc.ToolError) as e:
        agent_svc._admit_memory(TC, 1024, wait_s=0.3)
    assert "short of memory" in str(e.value) and "3000 MB free" in str(e.value) and "9216 MB needed" in str(e.value)
    monkeypatch.setattr(usage_svc, "mem_available_mb", lambda: 20000)
    agent_svc._admit_memory(TC, 1024, wait_s=0.3)  # enough headroom: returns at once
    monkeypatch.setattr(usage_svc, "mem_available_mb", lambda: None)  # unknown platform: do not block
    agent_svc._admit_memory(TC, 1024, wait_s=0.3)


def test_sandbox_calls_never_exceed_the_platform_ceiling(env, app, st, fake_sandbox):
    enable_sandbox(env)
    _acme, c, wid, _uid, _ids, _ctx_id = marketing(env, app)
    mia = c["mia@acme.test"]
    drive(st)
    assert env.put("/api/admin/settings/sandbox.memory_mb", json={"value": 512}).status_code == 200
    assert env.put("/api/admin/settings/sandbox.timeout_s", json={"value": 30}).status_code == 200
    assert env.put("/api/admin/settings/sandbox.reserve_mb", json={"value": 0}).status_code == 200
    # an agent cannot ask for more than the ceiling by putting limits in its own configuration
    aid = mk_agent(mia, wid, tools=["python"], approval="never", limits={"timeout_s": 1500, "memory_mb": 99999})
    L.say(L.tool("python", code="print(1)"), L.final("x"))
    rid = run_agent(mia, aid)
    drive(st)
    assert tool_steps(mia, rid)[0]["status"] == "done"
    lim = fake_sandbox[0]["lim"]
    assert lim.memory_mb == 512 and lim.timeout_s == 30
