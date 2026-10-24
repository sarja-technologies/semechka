import time

from sqlalchemy import func, select

from semechka.models import Conversation, Message, Model, UsageEvent
from .conftest import PW, Api, make_user


def model_id(owner):
    return owner.get("/api/admin/models").json()["items"][0]["id"] if False else next(
        m for m in owner.get("/api/admin/models").json()["items"] if m["real_id"] == "fake-small")["id"]


def chat_once(api, mid, text="hello", **kw):
    cid = api.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    status, ev = api.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": text, "model_id": mid, **kw})
    return cid, status, ev


def test_setup_smoke_and_enabled_model(owner):
    assert owner.setup_result["models_enabled"] == 1
    assert owner.setup_result["smoke"]["ok"] is True


def test_member_chats_with_streaming_and_no_tech_by_default(owner, app):
    owner.patch(f"/api/admin/models/{model_id(owner)}", json={"alias": "Small Assistant"})
    m = make_user(owner, app, "m@example.test")
    models = m.get("/api/chat/models").json()
    assert [x["alias"] for x in models] == ["Small Assistant"] and models[0]["tech"] == {}
    cid, status, ev = chat_once(m, models[0]["id"])
    assert status == 200
    types = [e["type"] for e in ev]
    assert types[0] == "start" and types[-1] == "done" and "delta" in types
    done = ev[-1]
    assert done["content"] == "Hello from the fake model" and done["tech"] == {}
    # raw response contains none of the infrastructure vocabulary
    blob = str(ev)
    for leak in ("fake-small", "openai_compat", "tokens_per_s", "ttft", "127.0.0.1"):
        assert leak not in blob
    conv = m.get(f"/api/chat/conversations/{cid}").json()
    assert [x["role"] for x in conv["messages"]] == ["user", "assistant"] and conv["messages"][1]["tech"] == {}


def test_technical_fields_are_per_field_and_server_enforced(owner, app):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    # grant exactly one field to one person (Brief 3 / J)
    r = owner.put(f"/api/admin/users/{m.user_id}/override", json={"grants": ["tech.perf.tps", "tech.queue.position"], "denies": []})
    assert r.status_code == 200
    _, _, ev = chat_once(m, mid)
    tech = ev[-1]["tech"]
    assert set(tech) - {"_unavailable"} == {"tokens_per_s", "tps_source", "queue"}
    assert tech["tokens_per_s"] == 123.4 and tech["tps_source"] == "backend"
    assert "model_real_id" not in tech and "runtime" not in tech and "node" not in tech
    me = m.get("/api/me").json()
    assert me["ui"]["tech"]["fields"] == ["tech.perf.tps", "tech.queue.position"]
    # reading is not operating
    assert m.get("/api/system/resources").status_code == 403
    for op in ("load", "unload"):
        assert m.post(f"/api/admin/models/{mid}/{op}").status_code == 403
    assert m.post("/api/chat/conversations", json={}).status_code == 200
    cid = m.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    status, body = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "hi", "model_id": mid, "params": {"temperature": 2}})
    assert status == 403 and body["detail"]["permission"] == "ops.gen.edit_params"


def test_expert_visibility_includes_real_fields_and_marks_unavailable(owner, app):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    owner.put(f"/api/admin/users/{m.user_id}/override", json={"grants": ["tech.*"], "denies": []})
    _, _, ev = chat_once(m, mid)
    t = ev[-1]["tech"]
    assert t["model_real_id"] == "fake-small" and t["runtime"] == "openai_compat" and t["context"] == 4096
    assert t["prompt_tokens"] == 7 and t["completion_tokens"] == 5
    # fields the backend never supplied are reported unavailable, never invented
    assert "quantization" in t["_unavailable"] and "license" in t["_unavailable"]
    assert "quantization" not in t


def test_model_access_is_enforced_for_picker_and_direct_api(owner, app):
    # a second model that everyone may use, so Finance keeps chat but loses one specific model
    items = owner.get("/api/admin/models").json()["items"]
    other_model = next(m for m in items if m["real_id"] == "fake-other")["id"]
    owner.patch(f"/api/admin/models/{other_model}", json={"enabled": True})
    owner.put(f"/api/admin/models/{other_model}/grants", json={"grants": [{"subject_type": "role", "subject_id": "member", "effect": "allow"}]})
    fin = owner.post("/api/admin/teams", json={"name": "Finance"}).json()["id"]
    f = make_user(owner, app, "fin@example.test", teams=[fin])
    other = make_user(owner, app, "other@example.test")
    mid = next(m for m in items if m["real_id"] == "fake-small")["id"]
    # deny one team on one model (Brief 2)
    g = [{"subject_type": "role", "subject_id": "member", "effect": "allow"}, {"subject_type": "team", "subject_id": fin, "effect": "deny"}]
    assert owner.put(f"/api/admin/models/{mid}/grants", json={"grants": g}).status_code == 200
    assert mid not in [m["id"] for m in f.get("/api/chat/models").json()]
    assert mid in [m["id"] for m in other.get("/api/chat/models").json()]
    cid = f.post("/api/chat/conversations", json={}).json()["id"]
    status, body = f.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "hi", "model_id": mid})
    assert status == 403 and body["detail"]["code"] == "model_forbidden"
    status, body = f.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "hi"}], "model_id": mid})
    assert status == 403
    # unknown ids look the same as forbidden ones: no probing
    status, body = f.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "hi", "model_id": "m_doesnotexist"})
    assert status == 403 and body["detail"]["code"] == "model_forbidden"
    # the same person can still use the model they are allowed
    assert f.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "hi", "model_id": other_model})[0] == 200
    ex = owner.get("/api/admin/explain/model", params={"user_id": f.user_id, "model_id": mid}).json()
    assert ex["allowed"] is False and any("Finance" in s["rule"] and s["result"] == "deny" for s in ex["steps"])


def test_mode_disabled_for_team_hides_nav_and_blocks_api(owner, app):
    team = owner.post("/api/admin/teams", json={"name": "Finance"}).json()["id"]
    f = make_user(owner, app, "fin@example.test", teams=[team])
    assert f.get("/api/me").json()["ui"]["operations"]["text"]["enabled"] is True
    r = owner.put("/api/admin/settings/modes.chat.enabled", json={"value": False, "scope_type": "team", "scope_id": team})
    assert r.status_code == 200
    me = f.get("/api/me").json()
    assert me["ui"]["operations"]["text"]["enabled"] is False
    assert f.get("/api/chat/models").status_code == 403
    assert f.post("/api/chat/conversations", json={}).status_code == 403
    assert f.get("/api/chat/conversations").status_code == 403
    # other people unaffected
    o = make_user(owner, app, "o@example.test")
    assert o.get("/api/chat/models").status_code == 200


def test_unsupported_modes_report_truthfully(owner, app):
    m = make_user(owner, app, "m@example.test")
    owner.put("/api/admin/settings/modes.video.enabled", json={"value": True})
    owner.put("/api/admin/roles/member", json={"label": "Member", "description": "", "rank": 100, "grants": ["mode.chat", "chat.attach", "chat.export", "ops.jobs.cancel_own", "mode.video"], "denies": []})
    me = m.get("/api/me").json()
    video = me["ui"]["operations"]["video"]
    assert video["enabled"] is False and "No connected inference backend can generate video" in video["reason"]
    audio_state = me["ui"]["modes"]["audio"]
    assert audio_state["code"] in ("disabled", "forbidden", "unsupported")
    assert m.get("/api/chat/models", params={"mode": "video"}).status_code == 501


def test_no_save_conversation_writes_nothing(owner, app, st):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    owner.put("/api/admin/roles/member", json={"label": "Member", "description": "", "rank": 100, "grants": ["mode.chat", "chat.ephemeral"], "denies": []})
    status, ev = m.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "super secret zebra"}], "model_id": mid})
    assert status == 200 and ev[0]["saved"] is False and ev[-1]["content"].startswith("Hello")
    with st.db.session() as s:
        assert s.execute(select(func.count()).select_from(Conversation)).scalar() == 0
        assert s.execute(select(func.count()).select_from(Message)).scalar() == 0
        ue = s.execute(select(UsageEvent)).scalars().all()
        assert len(ue) == 1 and ue[0].ephemeral is True
    # the text is nowhere in the database file
    import glob
    for f in glob.glob(str(st.cfg.data_dir / "semechka.db*")):
        assert b"zebra" not in open(f, "rb").read()
    # and an organisation can switch no-save chats off for a team, enforced at the API
    owner.put("/api/admin/settings/privacy.ephemeral_allowed", json={"value": False})
    status, body = m.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "x"}], "model_id": mid})
    assert status == 403 and body["detail"]["code"] == "ephemeral_disabled"
    assert m.get("/api/me").json()["ui"]["composer"]["ephemeral"] is False


def test_history_is_encrypted_at_rest_and_private_between_users(owner, app, st):
    a = make_user(owner, app, "a@example.test")
    b = make_user(owner, app, "b@example.test")
    mid = a.get("/api/chat/models").json()[0]["id"]
    cid, _, _ = chat_once(a, mid, "the launch code is plumtree")
    import glob
    blob = b"".join(open(f, "rb").read() for f in glob.glob(str(st.cfg.data_dir / "semechka.db*")))
    assert b"plumtree" not in blob
    assert b.get(f"/api/chat/conversations/{cid}").status_code == 404
    assert b.delete(f"/api/chat/conversations/{cid}").status_code == 404
    assert b.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "x", "model_id": mid})[0] == 404
    assert a.get("/api/chat/conversations", params={"q": "plumtree"}).json()[0]["id"] == cid
    assert b.get("/api/chat/conversations", params={"q": "plumtree"}).json() == []
    # the owner has no endpoint that reads other people's conversations
    assert owner.get(f"/api/chat/conversations/{cid}").status_code == 404
    # nothing in the audit log carries content
    assert "plumtree" not in str(owner.get("/api/admin/audit", params={"per_page": 200}).json())


def test_branching_edit_and_regenerate_create_siblings(owner, app):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    cid, _, ev = chat_once(m, mid, "first")
    conv = m.get(f"/api/chat/conversations/{cid}").json()
    user_id, asst_id = conv["messages"][0]["id"], conv["messages"][1]["id"]
    st1, ev2 = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "regenerate", "parent_id": asst_id, "model_id": mid})
    st2, ev3 = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "edit", "parent_id": user_id, "content": "first, edited", "model_id": mid})
    assert st1 == st2 == 200
    msgs = m.get(f"/api/chat/conversations/{cid}").json()["messages"]
    assert len(msgs) == 5  # nothing was overwritten
    siblings = [x for x in msgs if x["parent_id"] == user_id]
    assert len(siblings) == 2
    owner.put("/api/admin/settings/chat.branching", json={"value": False})
    assert m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "regenerate", "parent_id": asst_id, "model_id": mid})[0] == 403


def test_quota_and_concurrency_are_enforced(owner, app):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    owner.put("/api/admin/settings/quota.daily_messages", json={"value": 2})
    for _ in range(2):
        assert chat_once(m, mid)[1] == 200
    cid = m.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]
    status, body = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "x", "model_id": mid})
    assert status == 429 and body["detail"]["code"] == "quota_exceeded"
    # a per-person override lifts only that person
    owner.put("/api/admin/settings/quota.daily_messages", json={"value": 0, "scope_type": "user", "scope_id": m.user_id})
    # most restrictive non-zero wins across scopes, so an override of 0 (= unlimited) cannot widen the deployment limit
    assert m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "x", "model_id": mid})[0] == 429


def test_external_backend_models_blocked_by_egress_policy(owner, app, fake_url):
    b = owner.post("/api/admin/backends", json={"name": "Cloud", "adapter": "openai_compat", "base_url": fake_url, "external": True})
    bid = b.json()["id"]
    assert owner.post(f"/api/admin/backends/{bid}/sync").status_code == 200
    models = owner.get("/api/admin/models", params={"backend": bid}).json()["items"]
    ext = models[0]
    assert ext["external"] is True
    owner.patch(f"/api/admin/models/{ext['id']}", json={"enabled": True})
    owner.put(f"/api/admin/models/{ext['id']}/grants", json={"grants": [{"subject_type": "role", "subject_id": "member", "effect": "allow"}]})
    m = make_user(owner, app, "m@example.test")
    assert ext["id"] not in [x["id"] for x in m.get("/api/chat/models").json()]
    cid = m.post("/api/chat/conversations", json={}).json()["id"]
    status, body = m.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": "x", "model_id": ext["id"]})
    assert status == 403
    # turning external inference on is a weakening change that needs explicit confirmation, then it works
    r = owner.put("/api/admin/settings/privacy.external_egress", json={"value": True})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "confirm_required"
    assert owner.put("/api/admin/settings/privacy.external_egress", json={"value": True, "confirm_weaker": True}).status_code == 200
    assert ext["id"] in [x["id"] for x in m.get("/api/chat/models").json()]
    # the protection panel says so plainly
    p = m.get("/api/chat/protection", params={"model_id": ext["id"]}).json()
    assert p["external"] is True and any("external service" in l["text"] for l in p["lines"])


def test_protection_panel_is_truthful(owner, app):
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    p = m.get("/api/chat/protection", params={"model_id": mid}).json()
    text = " ".join(l["text"] for l in p["lines"])
    assert "not end-to-end" in text.lower() and "30 days" in text
    assert "plain HTTP" in text  # the test client is not on HTTPS and the panel says so
    assert "100%" not in text and "military" not in text.lower()
    e = m.get("/api/chat/protection", params={"model_id": mid, "ephemeral": True}).json()
    assert any("Not saved" in l["text"] for l in e["lines"])


def test_retention_expiry_sweeps_conversations(owner, app, st):
    from semechka.app import sweep_once
    m = make_user(owner, app, "m@example.test")
    mid = m.get("/api/chat/models").json()[0]["id"]
    cid, _, _ = chat_once(m, mid)
    with st.db.session() as s:
        c = s.get(Conversation, cid)
        assert c.expires_at and abs(c.expires_at - (time.time() + 30 * 86400)) < 60
        c.expires_at = time.time() - 1
    assert sweep_once(st)["conversations"] == 1
    assert m.get(f"/api/chat/conversations/{cid}").status_code == 404
    with st.db.session() as s:
        assert s.execute(select(func.count()).select_from(Message)).scalar() == 0
    # retention of 0 means nothing is stored
    owner.put("/api/admin/settings/privacy.chat_retention_days", json={"value": 0})
    assert m.post("/api/chat/conversations", json={}).status_code == 409
