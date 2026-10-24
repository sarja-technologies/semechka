"""0.3.0 conversation engine: threads in slices, branches, folders, attachments, vision, context fitting, catalogue,
theme publishing and governed administrator access. Runs through the real app, database, vault and disk against the
OpenAI-compatible test double (tests/fakebackend.py)."""
import struct
import zlib

from semechka.models import AuditEvent, ChatAttachment
from sqlalchemy import select

from . import fakebackend
from .conftest import make_user
from .test_chat_privacy_visibility import model_id


def png(w=3, h=2) -> bytes:
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def new_conv(api, mid):
    return api.post("/api/chat/conversations", json={"model_id": mid}).json()["id"]


def say(api, cid, mid, text="hello", **kw):
    status, ev = api.stream(f"/api/chat/conversations/{cid}/generate", {"content": text, "model_id": mid, **kw})
    assert status == 200, ev
    return ev


def upload(api, name, data, **params):
    return api.post("/api/chat/attachments", files={"file": (name, data)}, params=params)


# ---- threads and branches ------------------------------------------------------------------------------
def test_thread_is_served_in_slices_newest_first(owner, app):
    mid = model_id(owner)
    cid = new_conv(owner, mid)
    for i in range(5):
        say(owner, cid, mid, f"question {i}")
    t = owner.get(f"/api/chat/conversations/{cid}/thread", params={"limit": 4}).json()
    assert t["total"] == 10 and t["has_more"] is True and len(t["messages"]) == 4
    assert [m["role"] for m in t["messages"]] == ["user", "assistant", "user", "assistant"]
    assert t["messages"][-2]["content"] == "question 4"
    older = owner.get(f"/api/chat/conversations/{cid}/thread",
                      params={"limit": 4, "before": t["messages"][0]["id"]}).json()
    assert older["messages"][-1]["id"] == t["messages"][0]["parent_id"] and older["start"] == 2


def test_done_event_carries_stored_messages_so_no_reload_is_needed(owner):
    mid = model_id(owner)
    cid = new_conv(owner, mid)
    done = say(owner, cid, mid)[-1]
    assert done["message"]["id"] == done["assistant_id"] and done["message"]["content"] == "Hello from the fake model"
    assert done["user_message"]["content"] == "hello" and done["message"]["parent_id"] == done["user_message"]["id"]


def test_regenerate_creates_a_sibling_and_branch_switch_follows_it(owner):
    mid = model_id(owner)
    cid = new_conv(owner, mid)
    first = say(owner, cid, mid)[-1]["message"]
    status, ev = owner.stream(f"/api/chat/conversations/{cid}/generate",
                              {"mode": "regenerate", "parent_id": first["id"], "model_id": mid})
    second = ev[-1]["message"]
    assert second["siblings"] == {"index": 1, "count": 2, "prev": first["id"], "next": None}
    t = owner.post(f"/api/chat/conversations/{cid}/branch", json={"message_id": first["id"]}).json()
    assert t["messages"][-1]["id"] == first["id"] and t["messages"][-1]["siblings"]["next"] == second["id"]


def test_folders_and_pinning_belong_to_their_owner(owner, app):
    mid = model_id(owner)
    a = new_conv(owner, mid)
    b = new_conv(owner, mid)
    f = owner.post("/api/chat/folders", json={"name": "Research"}).json()
    assert owner.patch(f"/api/chat/conversations/{a}", json={"folder_id": f["id"], "pinned": True}).status_code == 200
    rows = owner.get("/api/chat/conversations").json()
    assert rows[0]["id"] == a and rows[0]["pinned"] and rows[0]["folder_id"] == f["id"]
    assert [r["id"] for r in owner.get("/api/chat/conversations", params={"folder_id": f["id"]}).json()] == [a]
    other = make_user(owner, app, "x@example.test")
    assert other.patch(f"/api/chat/conversations/{b}", json={"title": "mine now"}).status_code == 404
    assert other.get(f"/api/chat/conversations/{a}/thread").status_code == 404
    xf = other.post("/api/chat/folders", json={"name": "Mine"}).json()
    assert owner.patch(f"/api/chat/conversations/{a}", json={"folder_id": xf["id"]}).status_code == 404
    assert owner.delete(f"/api/chat/folders/{f['id']}").status_code == 200
    assert owner.get("/api/chat/conversations").json()[0]["folder_id"] is None


def test_deleting_conversations_can_be_reserved_for_retention(owner, app):
    mid = model_id(owner)
    m = make_user(owner, app, "m@example.test")
    cid = new_conv(m, mid)
    owner.put("/api/admin/settings/privacy.user_delete_conversations", json={"value": False})
    assert m.delete(f"/api/chat/conversations/{cid}").status_code == 403
    assert m.get("/api/me").json()["ui"]["composer"]["delete"] is False


# ---- attachments and vision ------------------------------------------------------------------------------
def test_text_file_reaches_the_model_and_is_encrypted_at_rest(owner, st):
    mid = model_id(owner)
    cid = new_conv(owner, mid)
    att = upload(owner, "notes.md", b"The launch code word is PERIWINKLE-42.").json()
    assert att["kind"] == "text" and att["saved"] is True
    fakebackend.calls.clear()
    say(owner, cid, mid, "What is the code word?", attachments=[att["id"]])
    sent = fakebackend.calls[-1]["messages"][-1]["content"]
    assert '<file name="notes.md">' in sent and "PERIWINKLE-42" in sent
    files = list((st.cfg.data_dir / "attachments").rglob("*.bin"))
    assert len(files) == 1 and b"PERIWINKLE" not in files[0].read_bytes()
    t = owner.get(f"/api/chat/conversations/{cid}/thread").json()
    assert t["messages"][0]["attachments"][0]["name"] == "notes.md"
    owner.delete(f"/api/chat/conversations/{cid}")
    assert not list((st.cfg.data_dir / "attachments").rglob("*.bin"))
    with st.db.session() as s:
        assert s.execute(select(ChatAttachment)).first() is None


def test_images_reach_only_models_that_can_see_and_may_be_used_for_it(owner, app):
    mid = model_id(owner)
    cid = new_conv(owner, mid)
    att = upload(owner, "chart.png", png()).json()
    assert att["kind"] == "image" and (att["width"], att["height"]) == (3, 2)
    fakebackend.calls.clear()
    say(owner, cid, mid, "What is in this picture?", attachments=[att["id"]])
    sent = fakebackend.calls[-1]["messages"][-1]["content"]
    assert isinstance(sent, str) and "cannot see images" in sent  # no vision reported: told, not shown
    # an administrator says the model can see; access for 'vision' is still governed by grants
    assert owner.patch(f"/api/admin/catalog/models/{mid}", json={"profile": {"capabilities": ["chat", "vision"]}}).status_code == 200
    fakebackend.calls.clear()
    say(owner, cid, mid, "And now?", attachments=[att["id"]])
    parts = fakebackend.calls[-1]["messages"][-1]["content"]
    assert parts[0]["type"] == "text" and parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
    grants = owner.get("/api/admin/catalog").json()["models"]
    g = next(m for m in grants if m["id"] == mid)["grants"]
    owner.put(f"/api/admin/models/{mid}/grants", json={"grants": [{**x, "modes": ["chat"]} for x in g]})
    fakebackend.calls.clear()
    say(owner, cid, mid, "Once more", attachments=[att["id"]])
    assert isinstance(fakebackend.calls[-1]["messages"][-1]["content"], str)


def test_attachments_are_private_and_typed_by_content(owner, app):
    att = upload(owner, "photo.png", png()).json()
    other = make_user(owner, app, "y@example.test")
    assert other.get(f"/api/chat/attachments/{att['id']}/content").status_code == 404
    mid = model_id(owner)
    cid = new_conv(other, mid)
    status, body = other.stream(f"/api/chat/conversations/{cid}/generate",
                                {"content": "hi", "model_id": mid, "attachments": [att["id"]]})
    assert status == 404
    assert upload(owner, "x.svg", b'<svg xmlns="http://www.w3.org/2000/svg"></svg>').status_code == 415
    assert upload(owner, "x.exe", b"MZ\x90\x00\x03\x00\x00\x00").status_code == 415
    assert upload(owner, "fake.png", b"not really a png").status_code == 415  # the bytes decide, not the name
    owner.put("/api/admin/settings/privacy.attachment_max_mb", json={"value": 1})
    assert upload(owner, "big.txt", b"a" * (1024 * 1024 + 10)).status_code == 413
    owner.put("/api/admin/settings/ui.file_upload", json={"value": False})
    assert upload(owner, "n.txt", b"hello").status_code == 403


# ---- context fitting ---------------------------------------------------------------------------------------
def test_oldest_turns_are_left_out_when_the_engine_refuses_the_length(owner, st):
    mid = model_id(owner)
    cid = new_conv(owner, mid)
    for i in range(6):
        say(owner, cid, mid, f"turn {i} " + "lorem ipsum " * 60)
    fakebackend.LIMITS["ctx"] = 600
    try:
        fakebackend.calls.clear()
        ev = say(owner, cid, mid, "short question")
        done = ev[-1]
        assert done["status"] == "complete" and done["trimmed"] and done["message"]["meta"]["trimmed"] == done["trimmed"]
        assert any(e["type"] == "trimmed" for e in ev)
        assert len(fakebackend.calls) == 2  # refused once, then fitted
        assert fakebackend.calls[-1]["messages"][-1]["content"] == "short question"
        assert fakebackend.calls[-1]["messages"][0]["role"] in ("system", "user")
        # the window is now known: the next request is fitted before it is sent
        fakebackend.calls.clear()
        say(owner, cid, mid, "another")
        assert len(fakebackend.calls) == 1
    finally:
        fakebackend.LIMITS["ctx"] = None


# ---- catalogue ---------------------------------------------------------------------------------------------
def test_catalogue_groups_order_and_hiding_never_grant_access(owner, app):
    mid = model_id(owner)
    other = next(m for m in owner.get("/api/admin/models").json()["items"] if m["real_id"] == "fake-other")["id"]
    owner.patch(f"/api/admin/models/{other}", json={"enabled": True})
    owner.put(f"/api/admin/models/{other}/grants", json={"grants": [{"subject_type": "role", "subject_id": "owner", "effect": "allow"}]})
    g = owner.post("/api/admin/catalog/groups", json={"name": "Everyday"}).json()["id"]
    owner.put(f"/api/admin/catalog/groups/{g}/members", json={"ids": [other, mid]})
    m = make_user(owner, app, "m@example.test")
    cat = m.get("/api/models/catalog", params={"operation": "text"}).json()
    assert [x["id"] for x in cat["models"]] == [mid] and cat["groups"][0]["models"] == [mid]  # membership is not access
    assert m.stream(f"/api/chat/conversations/{new_conv(m, mid)}/generate", {"content": "x", "model_id": other})[0] == 403
    owner.put("/api/admin/catalog/order", json={"ids": [other, mid]})
    assert [x["id"] for x in owner.get("/api/models/catalog").json()["models"]][:2] == [other, mid]
    owner.patch(f"/api/admin/catalog/models/{mid}", json={"hidden": True})
    assert mid not in [x["id"] for x in m.get("/api/models/catalog").json()["models"]]
    assert m.stream(f"/api/chat/conversations/{new_conv(m, mid)}/generate", {"content": "x", "model_id": mid})[0] == 200


def test_everyone_rule_and_mode_limited_rules(owner, app):
    mid = model_id(owner)
    m = make_user(owner, app, "m@example.test")
    mc, oc = new_conv(m, mid), new_conv(owner, mid)
    owner.put(f"/api/admin/models/{mid}/grants", json={"grants": [
        {"subject_type": "everyone", "subject_id": "", "effect": "allow", "modes": ["code"]}]})
    status, body = m.stream(f"/api/chat/conversations/{mc}/generate", {"content": "x", "model_id": mid})
    assert status == 409 and body["detail"]["code"] == "no_model"  # their only model is limited to code
    with app.state.sem.db.session() as s:
        from semechka import catalog_svc, services
        from semechka.models import ModelGrant, User
        grants = list(s.execute(select(ModelGrant).where(ModelGrant.model_id == mid)).scalars())
        p = services.load_principal(s, s.get(User, m.user_id))
        assert catalog_svc.decide(grants, p, "chat") == (False, "You may use this model, but not for chat.")
        assert catalog_svc.decide(grants, p, "code") == (True, "")
    assert m.get("/api/me").json()["ui"]["operations"]["text"]["enabled"] is False
    owner.put(f"/api/admin/models/{mid}/grants", json={"grants": [
        {"subject_type": "everyone", "subject_id": "", "effect": "allow"},
        {"subject_type": "user", "subject_id": m.user_id, "effect": "deny"}]})
    assert m.stream(f"/api/chat/conversations/{mc}/generate", {"content": "x", "model_id": mid})[0] in (403, 409)  # denied by name
    assert owner.stream(f"/api/chat/conversations/{oc}/generate", {"content": "x", "model_id": mid})[0] == 200


def test_provider_detection_labels_without_granting_capabilities():
    from semechka import catalog_svc
    from semechka.models import Model
    m = Model(id="m1", backend_id="b", real_id="local:qwen3.6-35b-a3b@MXFP4_MOE", alias="Qwen", capabilities=["chat"])
    p = catalog_svc.profile_of(m)
    assert p["provider"] == "qwen" and p["parameters"] == "35B (A3B active)" and p["quantization"] == "MXFP4_MOE"
    assert p["_source"]["parameters"] == "name" and catalog_svc.capabilities(m) == ["chat"]
    assert catalog_svc.detect_provider("FLUX.2-klein").id == "bfl"
    assert catalog_svc.detect_provider("gpt-oss-120b").id == "openai"
    assert catalog_svc.detect_provider("totally-unknown-7b") is None


# ---- theme -------------------------------------------------------------------------------------------------
def test_theme_publish_enforces_contrast_and_reverts_as_a_unit(owner, app):
    r = owner.get("/api/admin/theme").json()
    light = dict(r["spec"]["presets"]["light"])
    bad = {**light, "fg": "#EEEEEE"}
    assert owner.put("/api/admin/theme/draft", json={"bundle": {"custom": {"light": bad}}}).status_code == 200
    rej = owner.post("/api/admin/theme/publish", json={})
    assert rej.status_code == 422 and "needs 4.5:1" in rej.json()["detail"]["message"]
    good = {**light, "accent": "#1D5C9E"}
    pub = owner.post("/api/admin/theme/publish", json={"bundle": {"custom": {"light": good}, "default": "custom",
                                                                    "font_size": 15}}).json()
    assert pub["published"]["default"] == "custom" and pub["published"]["custom"]["light"]["accent"] == "#1D5C9E"
    m = make_user(owner, app, "m@example.test")
    ui = m.get("/api/me").json()["ui"]["theme"]
    assert ui["active"] == "custom" and "custom" in ui["choices"]
    owner.post("/api/admin/theme/revert", json={"batch": pub["batch"]})
    back = owner.get("/api/admin/theme").json()["published"]
    assert back["default"] == "light" and back["font_size"] == 14 and back["custom"] is None


def test_people_choose_a_theme_only_when_allowed(owner, app):
    m = make_user(owner, app, "m@example.test")
    assert m.patch("/api/me", json={"theme": "graphite"}).status_code == 200
    assert m.get("/api/me").json()["ui"]["theme"]["active"] == "graphite"
    assert m.patch("/api/me", json={"theme": "paper"}).status_code == 422
    owner.put("/api/admin/settings/theme.user_choice", json={"value": False})
    assert m.patch("/api/me", json={"theme": "dark"}).status_code == 403
    assert m.get("/api/me").json()["ui"]["theme"]["active"] == "light"


def test_custom_css_is_bounded(owner):
    put = lambda v: owner.put("/api/admin/settings/theme.custom_css", json={"value": v})  # noqa: E731
    assert put(".msg { letter-spacing: .01em }").status_code == 200
    for bad in ("@import url(x.css);", "a{background:url(https://tracker.example/p.png)}", "</style><script>",
                "a{behavior:url(x.htc)}"):
        assert put(bad).status_code == 422, bad


# ---- governed administrator access -------------------------------------------------------------------------
def test_admin_content_access_is_off_named_reasoned_audited_and_visible_to_owner(owner, app, st):
    mid = model_id(owner)
    m = make_user(owner, app, "m@example.test")
    cid = new_conv(m, mid)
    say(m, cid, mid, "my private thought")
    reason = {"reason": "Legal hold request #4411 from counsel"}
    assert owner.post(f"/api/admin/content/conversations/{cid}", json=reason).status_code == 403  # owner lacks the named perm
    me = owner.get("/api/me").json()
    assert "admin.content.read" not in me["permissions"]
    owner.put(f"/api/admin/users/{owner.get('/api/me').json()['user']['id']}/override",
              json={"grants": ["admin.content.read"], "denies": []})
    r = owner.post(f"/api/admin/content/conversations/{cid}", json=reason)
    assert r.status_code == 403 and r.json()["detail"]["code"] == "disabled"  # policy is off
    owner.put("/api/admin/settings/privacy.admin_content_access", json={"value": "audited", "confirm_weaker": True})
    assert owner.post(f"/api/admin/content/conversations/{cid}", json={"reason": "x"}).status_code == 422
    r = owner.post(f"/api/admin/content/conversations/{cid}", json=reason).json()
    assert r["messages"][0]["content"] == "my private thought"
    log = m.get("/api/me/content-access").json()
    assert log["policy"] == "audited" and log["events"][0]["reason"] == reason["reason"]
    with st.db.session() as s:
        ev = s.execute(select(AuditEvent).where(AuditEvent.action == "content.read")).scalar_one()
        assert "my private thought" not in str(ev.detail)


def test_personal_export_contains_my_data_only(owner, app):
    mid = model_id(owner)
    m = make_user(owner, app, "m@example.test")
    say(m, new_conv(m, mid), mid, "export me")
    say(owner, new_conv(owner, mid), mid, "not yours")
    out = m.get("/api/me/export").json()
    blob = str(out)
    assert out["schema"] == "semechka.personal-export/1" and "export me" in blob and "not yours" not in blob
