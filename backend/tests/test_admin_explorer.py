"""Unified administrator explorer: every conversation and media item of the instance, governed by the same gate as admin_content.

Real app, real database, vault and disk; the engine is the Sarness double. Rows that need a particular shape (an Imajutsu or OpenJutsu job, an
expired item) are inserted directly, exactly as the services would have written them.
"""
from __future__ import annotations

import json
import time

import pytest
from sqlalchemy import select

from semechka import admin_media as X
from semechka.media_models import MediaAsset, MediaJob
from semechka.models import AuditEvent, Conversation, Model, User
from semechka.models_tenancy import OrgMember
from semechka.tenancy_svc import create_org

from . import fake_sarness as fs
from .conftest import Api, make_user
from .test_chat_engine import png, upload
from .test_media import finish, img, model_id, vid
from .test_media_chat import chat_model, conv, gen

REASON = "Legal hold request #4411 from counsel"
LEAK = "sk-LEAKLEAKLEAKLEAKLEAK1234"
EX = "/api/admin/content/explorer"


def enable(api, policy="audited", instance=True):
    uid = api.get("/api/me").json()["user"]["id"]
    grants = ["admin.content.read", *(["admin.content.instance"] if instance else [])]
    assert api.put(f"/api/admin/users/{uid}/override", json={"grants": grants, "denies": []}).status_code == 200
    if policy:
        assert api.put("/api/admin/settings/privacy.admin_content_access", json={"value": policy, "confirm_weaker": True}).status_code == 200
    return uid


def media(api, filters=None, **kw):
    return api.post(f"{EX}/media", json={"reason": REASON, "filters": filters or {}, **kw})


def convs(api, filters=None, **kw):
    return api.post(f"{EX}/conversations", json={"reason": REASON, "filters": filters or {}, **kw})


def audits(st, *actions):
    with st.db.session() as s:
        return [(e.action, e.target, e.detail, e.actor, e.org_id) for e in
                s.execute(select(AuditEvent).where(AuditEvent.action.in_(actions)).order_by(AuditEvent.id)).scalars()]


def insert_job(st, user_id, *, kind="image", status="completed", params=None, prompt="a prompt", model=None, conversation_id=None, created=None,
               expires=None, error=None, save=True, backend=None):
    with st.db.session(write=True) as s:
        models = s.execute(select(Model)).scalars().all()
        m = s.get(Model, model) if model else next((x for x in models if kind in (x.capabilities or [])), models[0])
        jid = "mj_" + str(time.time_ns())[-16:]
        s.add(MediaJob(id=jid, user_id=user_id, kind=kind, model_id=m.id, backend_id=backend or m.backend_id, status=status, save=save,
                       prompt_enc=st.vault.encrypt(prompt, "media") if save else None,
                       params_enc=st.vault.encrypt(json.dumps(params or {}), "media") if save else None, error=error, created_at=created or time.time(),
                       finished_at=None if status in ("queued", "running") else (created or time.time()), expires_at=expires, conversation_id=conversation_id))
    return jid


@pytest.fixture
def world(mo, app, st):
    """Two people with conversations, pictures, a video, a failed and a cancelled job; the owner is the administrator (not yet enabled)."""
    m1, m2 = make_user(mo, app, "m1@example.test", name="Mia One"), make_user(mo, app, "m2@example.test", name="Max Two")
    cid = conv(m1)
    status, _ev = m1.stream(f"/api/chat/conversations/{cid}/generate", {"content": "zz-secret-text-message", "model_id": chat_model(m1)})
    assert status == 200
    assert m1.patch(f"/api/chat/conversations/{cid}", json={"title": "Harbour lighthouse plans"}).status_code == 200
    att = upload(m1, "room.png", png()).json()
    r = gen(m1, cid, "image", "zz-chat-image-prompt", attachments=[att["id"]], media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 200, r.text
    chat_job = finish(m1, r.json()["message"]["media"]["id"])
    v = vid(m1, prompt="zz-m1-video-prompt")
    video_job = finish(m1, v.json()["id"])
    direct = finish(m2, img(m2, prompt="zz-m2-image-prompt", seed=11).json()["id"])
    failed = finish(m2, img(m2, prompt="boom zz-fail").json()["id"])
    slow = img(m2, prompt="slow zz-slow").json()
    cancelled = m2.post(f"/api/media/jobs/{slow['id']}/cancel").json()
    assert (failed["status"], cancelled["status"]) == ("failed", "cancelled") and chat_job["status"] == video_job["status"] == "completed"
    return dict(m1=m1, m2=m2, cid=cid, att=att, chat_job=chat_job, video_job=video_job, direct=direct, failed=failed, cancelled=cancelled)


# ---- authorisation matrix -------------------------------------------------------------------------------------
ENDPOINTS = [("get", f"{EX}/filters", None), ("post", f"{EX}/conversations", {"reason": REASON}), ("post", f"{EX}/media", {"reason": REASON}),
             ("post", f"{EX}/media/jobs/mj_x", {"reason": REASON}), ("post", f"{EX}/media/assets/ma_x", {"reason": REASON}),
             ("post", f"{EX}/files/access", {"reason": REASON, "type": "asset", "id": "ma_x", "mode": "view"}), ("get", f"{EX}/blob/abc.def", None),
             ("post", "/api/admin/content/conversations/c_x", {"reason": REASON})]


def call(api, m, url, body):
    return api.get(url) if m == "get" else api.post(url, json=body)


def test_anonymous_ordinary_user_and_admin_without_the_named_permission_are_refused(mo, app, world):
    enable(mo, policy="audited")
    anon = Api(app)
    for m, url, body in ENDPOINTS:
        assert call(anon, m, url, body).status_code == 401, url
        assert call(world["m1"], m, url, body).status_code == 403, url  # an ordinary user, whatever the policy says
    # an administrator with admin.* but not the sensitive permission by name
    pa = make_user(mo, app, "pa@example.test", role="platform_admin")
    assert pa.get("/api/me").json()["permissions"] and "admin.content.read" not in pa.get("/api/me").json()["permissions"]
    for m, url, body in ENDPOINTS:
        r = call(pa, m, url, body)
        assert r.status_code == 403 and r.json()["detail"]["permission"] == "admin.content.read", url
    pa.csrf = pa.get("/api/me").json()["csrf"]
    mo.put(f"/api/admin/users/{pa.user_id}/override", json={"grants": ["admin.*"], "denies": []})
    assert media(pa).status_code == 403  # the wildcard never carries it


def test_policy_off_answers_disabled_for_every_explorer_endpoint(mo, world, st):
    enable(mo, policy=None)
    for m, url, body in ENDPOINTS[:7]:
        r = call(mo, m, url, body)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "disabled", url
        assert "does not allow administrators to read conversations" in r.json()["detail"]["message"]
    assert mo.post("/api/admin/content/conversations/c_x", json={"reason": REASON}).json()["detail"]["code"] == "disabled"
    assert not audits(st, "content.list", "content.read", "media.admin.list", "media.admin.view", "media.admin.download")


def test_a_reason_is_required_and_nothing_is_audited_without_one(mo, world, st):
    enable(mo)
    assert mo.post(f"{EX}/media", json={"reason": "short", "filters": {}}).status_code == 422
    assert mo.post(f"{EX}/media", json={"filters": {}}).status_code == 422
    assert mo.post(f"{EX}/conversations", json={"reason": "x" * 600}).status_code == 422
    assert not audits(st, "media.admin.list", "content.list")


# ---- conversations ------------------------------------------------------------------------------------------------
def test_conversation_search_filters_titles_and_the_audit_row_holds_no_content(mo, world, st):
    enable(mo)
    r = convs(mo)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["scope"] == "instance" and {i["owner"]["display_name"] for i in d["items"]} >= {"Mia One"}
    mine = next(i for i in d["items"] if i["id"] == world["cid"])
    assert mine["title"] == "Harbour lighthouse plans" and mine["media_items"] >= 1 and mine["mode"] == "chat" and mine["status"] == "active"
    assert mine["models"] and mine["owner"]["email"] == "m1@example.test"
    # filters: person, text in the title (case-insensitive), model, media type, date range, status
    assert [i["id"] for i in convs(mo, {"user_id": world["m1"].user_id, "q": "LIGHTHOUSE"}).json()["items"]] == [world["cid"]]
    assert convs(mo, {"q": "nothing like this"}).json()["items"] == []
    assert convs(mo, {"user_id": world["m2"].user_id}).json()["items"] == []
    mid = chat_model(world["m1"])
    assert world["cid"] in [i["id"] for i in convs(mo, {"model_id": mid}).json()["items"]]
    assert world["cid"] in [i["id"] for i in convs(mo, {"media": "image"}).json()["items"]]
    assert convs(mo, {"media": "video"}).json()["items"] == []
    assert convs(mo, {"since": time.time() + 3600}).json()["items"] == []
    assert world["cid"] in [i["id"] for i in convs(mo, {"until": time.time() + 3600, "since": time.time() - 3600, "status": "active"}).json()["items"]]
    assert convs(mo, {"status": "archived"}).json()["items"] == []
    assert convs(mo, {"status": "bogus"}).status_code == 422
    rows = audits(st, "content.list")
    assert rows and all(REASON in str(d) or d.get("reason") for _a, _t, d, _x, _o in rows)
    blob = json.dumps(rows, default=str)
    for secret in ("zz-secret-text-message", "LIGHTHOUSE", "Harbour lighthouse plans", "lighthouse"):
        assert secret.lower() not in blob.lower(), secret
    q_row = [d for _a, _t, d, _x, _o in rows if d["filters"].get("text_query")]
    assert q_row and "q" not in q_row[0]["filters"]


def test_conversation_pagination_is_stable_and_capped(mo, world, st):
    enable(mo)
    ids = []
    for i in range(7):
        c = world["m2"].post("/api/chat/conversations", json={"model_id": chat_model(world["m2"])}).json()["id"]
        ids.append(c)
        with st.db.session(write=True) as s:
            s.get(Conversation, c).updated_at = time.time() - 100 - i
    seen, cur = [], None
    for _ in range(10):
        d = convs(mo, {"user_id": world["m2"].user_id}, limit=3, cursor=cur).json()
        assert len(d["items"]) <= 3
        seen += [i["id"] for i in d["items"]]
        cur = d["next_cursor"]
        if not cur:
            break
    assert seen == sorted(ids, key=lambda c: ids.index(c)) or set(seen) == set(ids)
    assert len(seen) == len(set(seen)) == 7
    assert mo.post(f"{EX}/conversations", json={"reason": REASON, "limit": 101}).status_code == 422
    assert mo.post(f"{EX}/conversations", json={"reason": REASON, "cursor": "nonsense"}).status_code == 422


def test_conversation_view_shows_messages_with_their_media_and_attachments_in_context(mo, world, st):
    enable(mo)
    r = mo.post(f"/api/admin/content/conversations/{world['cid']}", json={"reason": REASON})
    assert r.status_code == 200, r.text
    d = r.json()
    texts = [m["content"] for m in d["messages"]]
    assert "zz-secret-text-message" in texts and any("zz-chat-image-prompt" in t for t in texts)
    img_msg = next(m for m in d["messages"] if m["kind"] == "image")
    job = img_msg["media"]
    assert job["status"] == "completed" and job["workflow"] == "chat_image" and job["model"]["alias"] == "fake-turbo"
    assert job["outputs"][0]["mime"] == "image/png" and job["outputs"][0]["state"] == "available" and job["outputs"][0]["thumb_url"].startswith(f"{EX}/blob/")
    assert job["params"]["width"] == 512 and job["params"]["steps"] == 4
    att = img_msg["attachment_items"][0]
    assert att["id"] == world["att"]["id"] and att["name"] == "room.png" and att["thumb_url"]
    # the thumbnails are served to this administrator only, from the existing store
    t = mo.get(job["outputs"][0]["thumb_url"])
    assert t.status_code == 200 and t.content.startswith(b"\x89PNG") and "no-store" in t.headers["cache-control"]
    assert mo.get(att["thumb_url"]).content.startswith(b"\x89PNG")
    assert world["m1"].get(job["outputs"][0]["thumb_url"]).status_code == 403
    [(a, t_, det, actor, org)] = audits(st, "content.read")
    assert (a, t_) == ("content.read", world["cid"]) and det["owner"] == world["m1"].user_id and det["reason"] == REASON and det["media_items"] == 1
    assert "zz-secret-text-message" not in json.dumps(det) and org  # the owner's organisation sees it in its own log
    assert mo.post("/api/admin/content/conversations/c_missing", json={"reason": REASON}).status_code == 404


def test_expired_conversations_are_listed_as_expired_and_not_readable(mo, world, st):
    enable(mo)
    with st.db.session(write=True) as s:
        s.get(Conversation, world["cid"]).expires_at = time.time() - 5
    row = next(i for i in convs(mo, {"status": "expired"}).json()["items"] if i["id"] == world["cid"])
    assert row["status"] == "expired"
    assert world["cid"] not in [i["id"] for i in convs(mo, {"status": "active"}).json()["items"]]
    r = mo.post(f"/api/admin/content/conversations/{world['cid']}", json={"reason": REASON})
    assert r.status_code == 410 and r.json()["detail"]["code"] == "expired"


# ---- media library ------------------------------------------------------------------------------------------------------
def test_media_library_lists_every_status_workflow_and_model(mo, world, st):
    enable(mo)
    d = media(mo).json()
    by = {i["id"]: i for i in d["items"]}
    assert world["failed"]["id"] in by and by[world["failed"]["id"]]["status"] == "failed" and "out of memory" in by[world["failed"]["id"]]["error"]
    assert by[world["cancelled"]["id"]]["status"] == "cancelled"
    assert by[world["direct"]["id"]]["workflow"] == "image_generation" and by[world["chat_job"]["id"]]["workflow"] == "chat_image"
    assert by[world["video_job"]["id"]]["kind"] == "video" and by[world["video_job"]["id"]]["workflow"] == "video_generation"
    assert by[world["direct"]["id"]]["outputs"][0]["thumb_url"] and "prompt" not in by[world["direct"]["id"]]  # metadata only in lists
    assert "zz-m2-image-prompt" not in json.dumps(d)
    # filters
    ids = lambda **f: {i["id"] for i in media(mo, f).json()["items"]}
    assert ids(status="failed") == {world["failed"]["id"]} and ids(status="cancelled") == {world["cancelled"]["id"]}
    assert ids(kind="video") == {world["video_job"]["id"]}
    assert ids(user_id=world["m2"].user_id) == {world["direct"]["id"], world["failed"]["id"], world["cancelled"]["id"]}
    assert ids(workflow="chat_image") == {world["chat_job"]["id"]} and ids(workflow="video_generation") == {world["video_job"]["id"]}
    assert world["direct"]["id"] in ids(model_id=model_id(mo, "image")) and world["video_job"]["id"] not in ids(model_id=model_id(mo, "image"))
    assert ids(conversation_id=world["cid"]) == {world["chat_job"]["id"]}
    assert ids(since=time.time() + 3600) == set() and world["direct"]["id"] in ids(since=time.time() - 3600, until=time.time() + 3600)
    assert media(mo, {"status": "bogus"}).status_code == 422 and media(mo, {"workflow": "bogus"}).status_code == 422 and media(mo, {"kind": "audio"}).status_code == 422


def test_imajutsu_and_openjutsu_jobs_are_classified_with_their_operation_and_inputs(mo, world, st):
    enable(mo)
    u = world["m2"].user_id
    with st.db.session(write=True) as s:
        src = MediaAsset(id="ma_src111", user_id=u, job_id=None, kind="image", model_id="upload", mime="image/png", size=10, prompt_enc="",
                         params_enc=st.vault.encrypt(json.dumps({"origin": "upload", "name": "source.png"}), "media"))
        s.add(src)
    ima = insert_job(st, u, params={"steps": 4, "width": 512, "seed": 3, "api_key": LEAK, "authorization": f"Bearer {LEAK}",
                                    "imajutsu": {"mode": "edit", "operation": "edit", "engine_mode": "edit", "source_asset": "ma_src111",
                                                 "references": [{"asset": "ma_src111"}], "plan": {"prompt": "secret plan"}, "token": LEAK}})
    stu = insert_job(st, u, kind="video", params={"operation": "replace", "sources": {"video": ["ma_nothere"], "refs": []},
                                                  "studio": {"operation": "replace", "mode": "swap", "source_asset": "ma_src111"}})
    assert {i["id"]: i["workflow"] for i in media(mo, {"workflow": "imajutsu"}).json()["items"]} == {ima: "imajutsu"}
    assert {i["id"]: i["operation"] for i in media(mo, {"workflow": "openjutsu"}).json()["items"]} == {stu: "replace"}
    r = mo.post(f"{EX}/media/jobs/{ima}", json={"reason": REASON})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["workflow"] == "imajutsu" and d["operation"] == "edit" and d["params"] == {"steps": 4, "width": 512, "seed": 3}
    assert d["prompt"] == "a prompt" and d["workflow_detail"]["source"] == "imajutsu" and d["workflow_detail"]["mode"] == "edit"
    assert "plan" not in d["workflow_detail"] and LEAK not in r.text and "api_key" not in r.text and "secret plan" not in r.text
    assert d["inputs"][0]["role"] == "source" and d["inputs"][0]["id"] == "ma_src111" and d["inputs"][0]["state"] == "missing"  # a row without a file
    s2 = mo.post(f"{EX}/media/jobs/{stu}", json={"reason": REASON}).json()
    assert [(i["role"], i["state"]) for i in s2["inputs"]][:1] == [("source", "missing")] and s2["workflow"] == "openjutsu"


def test_job_detail_shows_parameters_inputs_outputs_and_audits_the_view(mo, world, st):
    enable(mo)
    j = world["chat_job"]
    d = mo.post(f"{EX}/media/jobs/{j['id']}", json={"reason": REASON}).json()
    assert d["prompt"] == "zz-chat-image-prompt" and d["params"]["width"] == 512 and d["conversation_id"] == world["cid"]
    assert d["inputs"][0]["id"] == world["att"]["id"] and d["inputs"][0]["type"] == "attachment" and d["inputs"][0]["state"] == "available"
    assert d["outputs"][0]["id"] == j["assets"][0]["id"] and d["owner"]["email"] == "m1@example.test"
    assert d["created_at"] and d["finished_at"] and d["backend"] and d["model"]["alias"] == "fake-turbo"
    failed = mo.post(f"{EX}/media/jobs/{world['failed']['id']}", json={"reason": REASON}).json()
    assert failed["status"] == "failed" and failed["outputs"] == [] and failed["error"] and "<path>" in failed["error"]
    rows = audits(st, "media.admin.view")
    assert [r[1] for r in rows] == [j["id"], world["failed"]["id"]]
    assert all(r[2]["reason"] == REASON and r[2]["item"] == "job" and "zz-" not in json.dumps(r[2]) for r in rows)
    assert mo.post(f"{EX}/media/jobs/mj_nope", json={"reason": REASON}).status_code == 404
    assert mo.post(f"{EX}/media/jobs/..%2F..", json={"reason": REASON}).status_code in (404, 422)


def test_media_pagination_merges_jobs_and_loose_assets(mo, world, st):
    enable(mo)
    u = world["m2"].user_id
    base = time.time() - 10000
    for i in range(6):
        insert_job(st, u, created=base + i, prompt=f"p{i}")
    with st.db.session(write=True) as s:
        for i in range(3):
            s.add(MediaAsset(id=f"ma_up{i}", user_id=u, kind="image", model_id="upload", mime="image/png", size=5, prompt_enc="", created_at=base + i + 0.5,
                             params_enc=st.vault.encrypt(json.dumps({"origin": "upload", "name": f"file{i}.png"}), "media")))
    full = media(mo, limit=60).json()["items"]
    seen, cur, pages = [], None, 0
    while True:
        d = media(mo, limit=4, cursor=cur).json()
        assert len(d["items"]) <= 4
        seen += [i["id"] for i in d["items"]]
        pages += 1
        cur = d["next_cursor"]
        if not cur or pages > 30:
            break
    assert seen == [i["id"] for i in full] and len(set(seen)) == len(seen)
    assert {i["workflow"] for i in full} >= {"upload", "image_generation"}
    assert [i["id"] for i in media(mo, {"workflow": "upload"}).json()["items"]] == ["ma_up2", "ma_up1", "ma_up0"]
    assert media(mo, limit=500).status_code == 422


def test_scan_cap_is_reported_with_a_cursor_to_continue(mo, world, st, monkeypatch):
    enable(mo)
    monkeypatch.setattr(X, "SCAN_CAP", 3)
    d = media(mo, {"workflow": "imajutsu"}).json()
    assert d["items"] == [] and d["truncated"] is True and d["next_cursor"]
    assert media(mo, {"workflow": "imajutsu"}, cursor=d["next_cursor"]).status_code == 200


# ---- files: preview, download, ranges, retention, traversal --------------------------------------------------------
def test_preview_and_download_stream_the_stored_file_and_each_one_is_audited(mo, world, st):
    enable(mo)
    a = world["direct"]["assets"][0]
    original = world["m2"].get(f"/api/media/assets/{a['id']}/content").content
    r = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a["id"], "mode": "view"})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    got = mo.get(url)
    assert got.status_code == 200 and got.content == original and got.headers["content-type"] == "image/png"
    assert got.headers["content-disposition"].startswith("inline") and "sandbox" in got.headers["content-security-policy"] and got.headers["accept-ranges"] == "bytes"
    rng = mo.get(url, headers={"range": "bytes=0-7"})
    assert rng.status_code == 206 and rng.content == original[:8] and rng.headers["content-range"].startswith("bytes 0-7/")
    assert mo.get(url, headers={"range": "bytes=999999999-"}).status_code == 416
    dl = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a["id"], "mode": "download"}).json()
    got = mo.get(dl["url"])
    assert got.content == original and got.headers["content-disposition"].startswith("attachment; filename=semechka-image-") and dl["filename"].endswith(".png")
    assert [(r_[0], r_[1]) for r_ in audits(st, "media.admin.view", "media.admin.download")] == [("media.admin.view", a["id"]), ("media.admin.download", a["id"])]
    assert len(audits(st, "media.admin.view", "media.admin.download")) == 2  # fetching the bytes again writes nothing more
    # a download of a text attachment goes through the same path
    att = world["att"]
    at = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "attachment", "id": att["id"], "mode": "download"}).json()
    assert mo.get(at["url"]).content.startswith(b"\x89PNG")
    # the link is the administrator's own and short-lived
    assert world["m2"].get(url).status_code == 403
    other = X.make_ticket(st, "someone-else", "asset", a["id"], "view")
    assert mo.get(f"{EX}/blob/{other}").status_code == 403
    old = X.make_ticket(st, mo.get("/api/me").json()["user"]["id"], "asset", a["id"], "view", ttl=-5)
    assert mo.get(f"{EX}/blob/{old}").status_code == 403
    assert mo.get(f"{EX}/blob/{url.rsplit('/', 1)[1][:-3]}xyz").status_code == 403  # tampered
    assert Api(world["m1"].c.app).get(url).status_code == 401


def test_a_ticket_stops_working_when_the_policy_is_switched_off(mo, world):
    enable(mo)
    a = world["direct"]["assets"][0]["id"]
    url = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a, "mode": "view"}).json()["url"]
    assert mo.get(url).status_code == 200
    mo.put("/api/admin/settings/privacy.admin_content_access", json={"value": "off"})
    r = mo.get(url)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "disabled"


def test_expired_deleted_and_missing_media_are_shown_as_such_and_never_served(mo, world, st, cfg):
    enable(mo)
    a = world["direct"]["assets"][0]["id"]
    j = world["direct"]["id"]
    url = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a, "mode": "view"}).json()["url"]
    with st.db.session(write=True) as s:  # past retention, not yet swept
        s.get(MediaAsset, a).expires_at = time.time() - 60
        s.get(MediaJob, j).expires_at = time.time() - 60
    item = next(i for i in media(mo).json()["items"] if i["id"] == j)
    assert item["status"] == "expired" and item["raw_status"] == "completed" and item["retained"] is False
    assert item["outputs"][0]["state"] == "expired" and item["outputs"][0]["available"] is False and "thumb_url" not in item["outputs"][0]
    assert {i["id"] for i in media(mo, {"status": "expired"}).json()["items"]} == {j}
    assert j not in {i["id"] for i in media(mo, {"status": "completed"}).json()["items"]}
    d = mo.post(f"{EX}/media/jobs/{j}", json={"reason": REASON}).json()
    assert d["status"] == "expired" and d["prompt"] == "" and d["params"] == {} and "retention" in d["note"]
    r = mo.get(url)  # a link issued earlier is refused once the item has expired
    assert r.status_code == 410 and r.json()["detail"]["code"] == "expired"
    assert mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a, "mode": "download"}).status_code == 410
    # the file removed from disk behind the database's back
    a2 = world["chat_job"]["assets"][0]["id"]
    for f in (cfg.data_dir / "assets").rglob(f"{a2}*"):
        f.unlink()
    out = next(i for i in media(mo, {"conversation_id": world["cid"]}).json()["items"])["outputs"][0]
    assert out["state"] == "missing" and not out["available"]
    r = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a2, "mode": "view"})
    assert r.status_code == 410 and r.json()["detail"]["code"] == "gone"
    # swept: the rows are gone and so is the listing
    from semechka import media_svc
    media_svc.sweep(st)
    assert j not in {i["id"] for i in media(mo).json()["items"]}


def test_no_save_generations_expose_neither_prompt_nor_result(mo, world, st):
    enable(mo)
    j = insert_job(st, world["m2"].user_id, save=False, prompt="x")
    d = mo.post(f"{EX}/media/jobs/{j}", json={"reason": REASON}).json()
    assert d["prompt"] == "" and d["params"] == {} and d["outputs"] == [] and "never stored" in d["note"] and d["retained"] is False


def test_paths_outside_the_storage_root_are_never_read(mo, world, st, cfg):
    enable(mo)
    (cfg.data_dir / "assets" / "ma_trav.bin").write_bytes(b"TOP SECRET OUTSIDE THE PERSON'S FOLDER")
    with st.db.session(write=True) as s:  # a row whose id climbs out of the person's folder (it cannot be made through the API; the check is defence in depth)
        s.add(MediaAsset(id="../ma_trav", user_id=world["m1"].user_id, kind="image", model_id="upload", mime="image/png", size=25, prompt_enc="",
                         params_enc=st.vault.encrypt(json.dumps({"origin": "upload"}), "media")))
    with st.db.session() as s:
        assert X.asset_exists(st, s.get(MediaAsset, "../ma_trav")) is False
    # the row can be listed (as a missing file) but no route can name it
    row = next(i for i in media(mo, {"workflow": "upload"}).json()["items"] if i["id"] == "../ma_trav")
    assert row["outputs"][0]["state"] == "missing" and "thumb_url" not in row["outputs"][0]
    for bad in ("../ma_trav", "../../etc/passwd", "ma_..%2F..", "ma_a/b", "x" * 41, "ma_a.b"):
        r = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": bad, "mode": "view"})
        assert r.status_code in (404, 422) and b"TOP SECRET" not in r.content and b"root:" not in r.content, bad
        assert mo.post(f"{EX}/media/jobs/{bad}", json={"reason": REASON}).status_code in (404, 405, 422)
    # a ticket can only name an id of the plain form
    assert X.read_ticket(st, X.make_ticket(st, "u", "asset", "../x", "view"), "u") is None


def test_responses_never_carry_secrets(mo, world, st):
    enable(mo)
    u = world["m2"].user_id
    j = insert_job(st, u, params={"steps": 1, "api_key": LEAK, "password": "hunter2hunter2", "imajutsu": {"mode": "x", "headers": {"Authorization": f"Bearer {LEAK}"}}},
                   error=f"engine said Bearer {LEAK} and token={LEAK} at /home/x", status="failed")
    texts = [media(mo).text, convs(mo).text, mo.get(f"{EX}/filters").text, mo.post(f"{EX}/media/jobs/{j}", json={"reason": REASON}).text,
             mo.post(f"/api/admin/content/conversations/{world['cid']}", json={"reason": REASON}).text,
             mo.post(f"{EX}/media/assets/{world['direct']['assets'][0]['id']}", json={"reason": REASON}).text]
    with st.db.session() as s:
        hashes = [x for (x,) in s.execute(select(User.password_hash)) if x]
    for t in texts:
        for secret in (LEAK, "hunter2", fs.TOKEN, "password_hash", "api_key", *hashes[:3]):
            assert secret not in t, secret
    assert "<removed>" in mo.post(f"{EX}/media/jobs/{j}", json={"reason": REASON}).text


# ---- owner visibility and audit -------------------------------------------------------------------------------------
def test_owners_see_every_administrator_access_to_their_items(mo, app, world, st):
    enable(mo)
    m1, m2 = world["m1"], world["m2"]
    assert convs(mo, {"user_id": m1.user_id}).status_code == 200
    media(mo, {"user_id": m2.user_id})
    mo.post(f"{EX}/media/jobs/{world['failed']['id']}", json={"reason": REASON})
    a = world["direct"]["assets"][0]["id"]
    mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": a, "mode": "download"})
    mo.post(f"/api/admin/content/conversations/{world['cid']}", json={"reason": REASON})
    ev1 = m1.get("/api/me/content-access").json()
    assert ev1["policy"] == "audited"
    assert [e["kind"] for e in ev1["events"]] == ["content.read", "content.list"]  # newest first; nothing about m2's items
    assert all(e["reason"] == REASON and e["by"] == "Olga Owner" for e in ev1["events"])
    ev2 = m2.get("/api/me/content-access").json()
    assert [e["kind"] for e in ev2["events"]] == ["media.admin.download", "media.admin.view", "media.admin.list"]
    assert ev2["events"][0]["item"] == a and "download" in ev2["events"][0]["action"]
    # an aggregate list across everybody names each owner it showed
    media(mo)
    assert m1.get("/api/me/content-access").json()["events"][0]["action"].startswith("listed generated pictures and videos that included yours")
    assert make_user(mo, app, "m3@example.test").get("/api/me/content-access").json()["events"] == []


def test_every_open_and_list_is_one_audit_row_and_the_chain_verifies(mo, world, st):
    enable(mo)
    from semechka import audit
    before = len(audits(st, "content.list", "media.admin.list", "media.admin.view", "media.admin.download", "content.read"))
    media(mo)
    media(mo, {"status": "failed"})
    convs(mo)
    mo.post(f"{EX}/media/jobs/{world['failed']['id']}", json={"reason": REASON})
    assert len(audits(st, "content.list", "media.admin.list", "media.admin.view", "media.admin.download", "content.read")) == before + 4
    rows = audits(st, "media.admin.list")
    assert rows[-1][2]["filters"] == {"status": "failed"} and rows[-1][2]["scope"] == "instance"
    with st.db.session() as s:
        assert audit.verify_chain(s)["ok"]


def test_rate_limit_returns_429(mo, world, monkeypatch):
    enable(mo)
    monkeypatch.setattr(X, "RATE_OPS", (3, 60.0))
    assert [media(mo).status_code for _ in range(5)] == [200, 200, 200, 429, 429]


# ---- organisation scope -----------------------------------------------------------------------------------------------------
def make_scope_world(mo, app, st, world):
    """a1: an administrator of the default organisation only. m2 is moved to a second organisation; m3 belongs to no organisation."""
    a1 = make_user(mo, app, "a1@example.test", role="platform_admin", name="Org Admin")
    m3 = make_user(mo, app, "m3@example.test", name="No Org")
    enable_other(mo, a1)
    with st.db.session(write=True) as s:
        other = create_org(s, "", "Other Co", "other-co")
        s.execute(OrgMember.__table__.delete().where(OrgMember.user_id.in_([world["m2"].user_id, m3.user_id])))
        s.add(OrgMember(org_id=other.id, user_id=world["m2"].user_id, status="active", role_ids=[]))
    return a1, m3


def enable_other(mo, api):
    uid = api.get("/api/me").json()["user"]["id"]
    assert mo.put(f"/api/admin/users/{uid}/override", json={"grants": ["admin.content.read"], "denies": []}).status_code == 200
    api.csrf = api.get("/api/me").json()["csrf"]
    return uid


def test_an_organisation_scoped_administrator_sees_only_their_organisation(mo, app, st, world):
    enable(mo, instance=False)
    a1, m3 = make_scope_world(mo, app, st, world)
    c3 = conv(m3)
    owner_id = mo.get("/api/me").json()["user"]["id"]
    # the platform owner is in the default organisation, as a1 is: both reach m1 and not m2 or m3 (a different organisation / none)
    for who in (a1, mo):
        users = {u["id"] for u in who.get(f"{EX}/filters").json()["users"]}
        assert world["m1"].user_id in users and world["m2"].user_id not in users and m3.user_id not in users
    d = media(a1).json()
    assert d["scope"] == "organisation" and {i["owner"]["id"] for i in d["items"]} == {world["m1"].user_id}
    assert {i["owner"]["id"] for i in convs(a1).json()["items"]} == {world["m1"].user_id}
    # a person outside is indistinguishable from a missing one, on every route
    assert a1.post(f"{EX}/media/jobs/{world['direct']['id']}", json={"reason": REASON}).status_code == 404
    assert a1.post(f"{EX}/media/assets/{world['direct']['assets'][0]['id']}", json={"reason": REASON}).status_code == 404
    assert a1.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": world["direct"]["assets"][0]["id"], "mode": "view"}).status_code == 404
    assert a1.post(f"/api/admin/content/conversations/{c3}", json={"reason": REASON}).status_code == 404
    assert a1.post(f"/api/admin/content/users/{world['m2'].user_id}/conversations", json={"reason": REASON}).status_code == 404
    assert media(a1, {"user_id": world["m2"].user_id}).json()["items"] == []
    # a ticket issued to the instance-wide administrator is useless to a1, and the file is out of a1's reach anyway
    url = mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": world["direct"]["assets"][0]["id"], "mode": "view"})
    assert url.status_code == 404  # the owner in the default organisation does not reach m2 either, without the instance permission
    # the named instance-wide permission widens it, and only then
    mo.put(f"/api/admin/users/{owner_id}/override", json={"grants": ["admin.content.read", "admin.content.instance"], "denies": []})
    wide = media(mo).json()
    assert wide["scope"] == "instance" and {world["m1"].user_id, world["m2"].user_id} <= {i["owner"]["id"] for i in wide["items"]}
    assert c3 in [i["id"] for i in convs(mo).json()["items"]]
    assert mo.post(f"{EX}/files/access", json={"reason": REASON, "type": "asset", "id": world["direct"]["assets"][0]["id"], "mode": "view"}).status_code == 200
    assert "admin.content.instance" not in a1.get("/api/me").json()["permissions"]
    # the wildcard does not carry it
    mo.put(f"/api/admin/users/{a1.user_id}/override", json={"grants": ["admin.content.read", "admin.*"], "denies": []})
    assert media(a1).json()["scope"] == "organisation"


def test_without_any_organisation_membership_nothing_is_reachable(mo, app, st, world):
    enable(mo, instance=False)
    with st.db.session(write=True) as s:
        s.execute(OrgMember.__table__.delete().where(OrgMember.user_id == mo.get("/api/me").json()["user"]["id"]))
    assert media(mo).json()["items"] == []
    assert mo.get(f"{EX}/filters").json()["users"] == []
