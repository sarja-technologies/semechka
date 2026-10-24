"""Image and video generation as turns of one conversation, against the Sarness job-protocol double."""
from semechka.media_models import MediaAsset, MediaJob
from sqlalchemy import select

from . import fake_sarness as fs
from .test_chat_engine import png, upload
from .test_media import finish, model_id


def chat_model(api):
    return api.get("/api/models/catalog", params={"operation": "text"}).json()["default"]


def conv(api):
    return api.post("/api/chat/conversations", json={"model_id": chat_model(api)}).json()["id"]


def gen(api, cid, operation, prompt, **kw):
    return api.post(f"/api/chat/conversations/{cid}/generate",
                    json={"operation": operation, "content": prompt, "model_id": model_id(api, operation), **kw})


def test_text_then_image_then_video_in_one_thread(mo, st):
    cid = conv(mo)
    status, ev = mo.stream(f"/api/chat/conversations/{cid}/generate", {"content": "Describe a lighthouse",
                                                                        "model_id": chat_model(mo)})
    assert status == 200 and ev[-1]["status"] == "complete"
    r = gen(mo, cid, "image", "a lighthouse at dusk", media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 200, r.text
    msg = r.json()["message"]
    assert msg["kind"] == "image" and msg["status"] == "pending" and msg["media"]["status"] in ("queued", "running")
    job = finish(mo, msg["media"]["id"])
    assert job["status"] == "completed" and job["assets"]
    t = mo.get(f"/api/chat/conversations/{cid}/thread").json()
    assert [m["kind"] for m in t["messages"]] == ["text", "text", "text", "image"]
    assert t["messages"][-1]["status"] == "complete" and t["messages"][-1]["media"]["assets"][0]["mime"] == "image/png"
    # continue chatting: the model is told an image was made, in words
    status, ev = mo.stream(f"/api/chat/conversations/{cid}/generate", {"content": "Nice. Now a poem.",
                                                                        "model_id": chat_model(mo)})
    assert status == 200
    # animate the generated picture: it is handed to the engine as the start frame
    asset = job["assets"][0]["id"]
    r = gen(mo, cid, "video", "gentle waves", source_id=asset,
            media={"width": 512, "height": 320, "frames": 17, "fps": 16, "steps": 20})
    assert r.status_code == 200, r.text
    v = r.json()["message"]
    assert v["kind"] == "video" and v["meta"]["source"] == asset
    sub = fs.STATE["submissions"][-1]
    assert sub["kind"] == "video" and sub["start_image"] in fs.STATE["uploads"]
    assert fs.STATE["uploads"][sub["start_image"]].startswith(b"\x89PNG")
    assert finish(mo, v["media"]["id"])["status"] == "completed"
    assert mo.get(f"/api/chat/conversations/{cid}/thread").json()["messages"][-1]["status"] == "complete"


def test_editing_an_uploaded_picture_and_unsupported_sources(mo):
    cid = conv(mo)
    att = upload(mo, "room.png", png()).json()
    r = gen(mo, cid, "image", "paint the walls green", attachments=[att["id"]],
            media={"width": 512, "height": 512, "steps": 4})
    assert r.status_code == 200, r.text
    assert fs.STATE["submissions"][-1]["reference_image"] in fs.STATE["uploads"]
    assert r.json()["user_message"]["attachments"][0]["id"] == att["id"]
    # a model that cannot take a starting picture says so instead of ignoring it
    fs.MODELS[1]["capabilities"]["modes"] = ["t2v"]
    try:
        mo.post("/api/admin/backends/" + mo.get("/api/admin/backends").json()[0]["id"] + "/sync")
        r = gen(mo, cid, "video", "move", source_id=att["id"],
                media={"width": 512, "height": 320, "frames": 17, "fps": 16, "steps": 20})
        assert r.status_code == 422 and "cannot animate" in r.json()["detail"]["message"]
    finally:
        fs.MODELS[1]["capabilities"]["modes"] = ["t2v", "i2v"]


def test_failed_and_cancelled_generations_stay_in_the_thread_truthfully(mo):
    cid = conv(mo)
    m = gen(mo, cid, "image", "boom", media={"width": 512, "height": 512, "steps": 4}).json()["message"]
    assert finish(mo, m["media"]["id"])["status"] == "failed"
    last = mo.get(f"/api/chat/conversations/{cid}/thread").json()["messages"][-1]
    assert last["status"] == "error" and "/home/x" not in str(last)
    m = gen(mo, cid, "image", "slow", media={"width": 512, "height": 512, "steps": 4}).json()["message"]
    mo.post(f"/api/media/jobs/{m['media']['id']}/cancel")
    assert mo.get(f"/api/chat/conversations/{cid}/thread").json()["messages"][-1]["status"] == "stopped"


def test_regenerating_a_picture_makes_another_take(mo):
    cid = conv(mo)
    first = gen(mo, cid, "image", "a cat", media={"width": 512, "height": 512, "steps": 4, "seed": 7}).json()["message"]
    finish(mo, first["media"]["id"])
    r = mo.post(f"/api/chat/conversations/{cid}/generate", json={"mode": "regenerate", "parent_id": first["id"]})
    assert r.status_code == 200, r.text
    second = r.json()["message"]
    assert second["kind"] == "image" and second["parent_id"] == first["parent_id"]
    assert second["siblings"]["count"] == 2
    assert fs.STATE["submissions"][-1].get("seed") in (None, -1)  # a new take, not the same seed


def test_deleting_the_conversation_removes_its_media(mo, st):
    cid = conv(mo)
    m = gen(mo, cid, "image", "a tree", media={"width": 512, "height": 512, "steps": 4}).json()["message"]
    finish(mo, m["media"]["id"])
    running = gen(mo, cid, "image", "slow", media={"width": 512, "height": 512, "steps": 4}).json()["message"]
    assert list((st.cfg.data_dir / "assets").rglob("*.bin"))
    assert mo.delete(f"/api/chat/conversations/{cid}").status_code == 200
    assert not list((st.cfg.data_dir / "assets").rglob("*.bin"))
    with st.db.session() as s:
        assert s.execute(select(MediaAsset)).first() is None
        left = s.execute(select(MediaJob)).scalars().all()
        assert all(j.prompt_enc is None and j.conversation_id is None for j in left)
    assert fs.STATE["jobs"][next(iter(fs.STATE["jobs"]))]  # engine was reached
    assert any(j["status"] == "cancelled" for j in fs.STATE["jobs"].values()), running["id"]


def test_hidden_generation_is_refused_by_the_api(mo):
    cid = conv(mo)
    mo.put("/api/admin/settings/ui.video_gen", json={"value": False})
    r = gen(mo, cid, "video", "waves", media={"width": 512, "height": 320, "frames": 17, "fps": 16, "steps": 20})
    assert r.status_code == 403
    assert mo.get("/api/me").json()["ui"]["operations"]["video"]["enabled"] is False
