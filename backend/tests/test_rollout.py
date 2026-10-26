"""cloud.rollout == "admins": the cloud features answer platform administrators only, whatever the switches and the
organisation roles say; flipping it back restores everyone, and nothing is lost in between."""
import pytest

from semechka import agent_svc, services
from semechka.authz import Authz
from semechka.models import User
from semechka.tenancy_svc import TenancyError

from .test_agents import marketing, mk_agent
from .test_files_http import up


def pages(c):
    return [p["id"] for p in c.get("/api/me").json()["ui"]["pages"]]


def test_admins_only_rollout_closes_every_cloud_door_to_ordinary_accounts_and_reopens_cleanly(env, app, st):
    acme, c, wid, uid, ids, ctx_id = marketing(env, app)
    mia, boss = c["mia@acme.test"], c["boss@acme.test"]  # organisation members; neither is a platform administrator
    aid = mk_agent(mia, wid)
    assert env.put("/api/admin/settings/cloud.workspaces.enabled", json={"value": True}).status_code == 200
    with app.state.sem.db.session() as s:
        assert st.settings.deployment(s, "cloud.rollout") == "everyone"  # the default keeps today's behaviour
    assert "workspaces" in pages(mia) and mia.get(f"/api/workspaces/{wid}/files").status_code == 200

    assert env.put("/api/admin/settings/cloud.rollout", json={"value": "admins"}).status_code == 200
    # ordinary organisation members: no navigation entry and every cloud endpoint refuses
    assert "workspaces" not in pages(mia) and "workspaces" not in pages(boss)
    doors = [("get", f"/api/orgs/{acme}/workspaces"), ("get", f"/api/workspaces/{wid}/files"), ("get", f"/api/workspaces/{wid}/agents"),
             ("get", f"/api/contexts/{ctx_id}"), ("get", f"/api/files/{next(iter(ids.values()))}"),
             ("post", f"/api/workspaces/{wid}/retrieve"), ("post", f"/api/agents/{aid}/runs"), ("get", f"/api/workspaces/{wid}/usage")]
    for method, path in doors:
        r = getattr(mia, method)(path, **({"json": {"task": "x", "query": "x"}} if method == "post" else {}))
        assert r.status_code == 403 and r.json()["detail"]["code"] == "rollout", (method, path, r.status_code, r.text)
    assert up(mia, wid, "late.md", b"x").status_code == 403
    # an unauthenticated caller still gets 401, not a rollout message
    from .conftest import Api
    assert Api(app).get(f"/api/workspaces/{wid}/files").status_code == 401
    # the administrator is in, and the status endpoint says what is going on
    assert "workspaces" in pages(env)
    assert env.get("/api/admin/cloud/status").json()["rollout"] == "admins"
    # defence in depth: the service itself refuses a run for an ordinary principal, even if a route forgot the guard
    with app.state.sem.db.session() as s:
        mia_p = services.load_principal(s, s.get(User, uid["mia@acme.test"]))
        with pytest.raises(TenancyError) as e:
            agent_svc.start_run(app.state.sem, s, Authz(s, mia_p.user_id), aid, "x", mia_p)
        assert e.value.code == "rollout"

    # back to everyone: the same people, the same data, nothing was lost
    assert env.put("/api/admin/settings/cloud.rollout", json={"value": "everyone"}).status_code == 200
    assert "workspaces" in pages(mia) and mia.get(f"/api/workspaces/{wid}/files").status_code == 200
    assert up(mia, wid, "late.md", b"x").status_code == 200


def test_agent_model_allow_list_binds_creation_runs_and_the_picker(env, app, st):
    """cloud.agents.models: a small or unchecked model can stay available for chat yet never drive an agent."""
    from .test_agents import run_agent
    _acme, c, wid, _uid, _ids, _ctx = marketing(env, app)
    mia = c["mia@acme.test"]
    good = mia.get(f"/api/workspaces/{wid}/models", params={"mode": "chat"}).json()[0]["id"]
    aid = mk_agent(mia, wid)  # no model chosen: uses the first allowed one
    assert env.put("/api/admin/settings/cloud.agents.models", json={"value": ["m_some_other_model"]}).status_code == 200
    assert mia.get(f"/api/workspaces/{wid}/models", params={"mode": "chat", "purpose": "agent"}).json() == []
    assert [m["id"] for m in mia.get(f"/api/workspaces/{wid}/models", params={"mode": "chat"}).json()] == [good]  # chat is unaffected
    r = mia.post(f"/api/agents/{aid}/runs", json={"task": "x"})
    assert r.status_code == 409  # nothing allowed for agents: refused, not silently run on another model
    r = mia.post(f"/api/workspaces/{wid}/agents", json={"name": "Pinned", "instructions": "x", "model_id": good, "tools": ["calc"]})
    assert r.status_code in (400, 403, 422) and "approved models" in r.text
    assert env.put("/api/admin/settings/cloud.agents.models", json={"value": [good]}).status_code == 200
    assert [m["id"] for m in mia.get(f"/api/workspaces/{wid}/models", params={"mode": "chat", "purpose": "agent"}).json()] == [good]
    assert mia.post(f"/api/workspaces/{wid}/agents", json={"name": "Pinned", "instructions": "x", "model_id": good, "tools": ["calc"]}).status_code == 200
    assert run_agent(mia, aid)
    # an agent saved earlier on a model that has since been removed from the list is refused at run time too
    assert env.put("/api/admin/settings/cloud.agents.models", json={"value": ["m_some_other_model"]}).status_code == 200
    pinned = next(a for a in mia.get(f"/api/workspaces/{wid}/agents").json() if a["name"] == "Pinned")["id"]
    assert mia.post(f"/api/agents/{pinned}/runs", json={"task": "x"}).status_code == 403
