"""Experimental tools (media_extras.py, routers/media_extras.py) through the real app, database, vault and disk; only the engine is a double
(fake_spark_media.py serving a real capture of the engine's /v1/tools). Speech, music, picture tools and 3D are models of capability ``media_tool``:
never in the chat/Images/Videos choosers, off until an administrator enables them, and run as ordinary media jobs."""
import copy
import io
import json

import pytest

from . import fake_spark_media as fm
from .conftest import PW, Api, Server, _free_port

TOOLS_READY = [{**t, "ready": True, "state": "ready", "reason": None} for t in fm.TOOLS]


@pytest.fixture(scope="module")
def spark_url():
    srv = Server(fm.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


def setup(app, st, spark_url, enable_tools=True, tools=None):
    fm.reset()
    fm.STATE["tools"] = copy.deepcopy(TOOLS_READY if tools is None else tools)
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    ids = [f"tool:{t['id']}" for t in fm.STATE["tools"] if t["ready"]] if enable_tools else []
    r = api.post("/api/setup/complete", headers={"x-setup-token": ensure_setup_token(st)}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
        "backend": {"adapter": "spark-media", "base_url": spark_url, "api_key": fm.TOKEN},
        "enable_models": ["image:z-image-turbo", "video:minimax-h3", *ids]})
    assert r.status_code == 200, r.text
    api.csrf = api.get("/api/me").json()["csrf"]
    return api


@pytest.fixture
def ex(app, st, spark_url):
    return setup(app, st, spark_url)


def tools(api, group=None):
    r = api.get("/api/media/extras", params={"group": group} if group else None)
    assert r.status_code == 200, r.text
    return {t["tool"]: t for t in r.json()["tools"]}


def send(api, mid, inputs, files=(), assets=None, **form):
    data = {"model_id": mid, "inputs": json.dumps(inputs), **form, **(assets or {})}
    parts = [(f"file:{name}", (fn, io.BytesIO(body), mime)) for name, fn, body, mime in files]
    return api.c.post("/api/media/extras/jobs", data=data, files=parts or None, headers={"x-csrf-token": api.csrf})


def finish(api, jid, tries=12):
    for _ in range(tries):
        j = api.get(f"/api/media/jobs/{jid}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
    return j


PNG = fm.fs.make_png()


def test_ready_tools_are_discovered_as_media_tool_models_and_an_unverified_one_is_not(app, st, spark_url):
    api = setup(app, st, spark_url, tools=fm.TOOLS)          # the real capture: moge-relief not reviewed yet on that server
    got = tools(api)
    assert set(got) == {"kokoro-tts", "ace-step-music", "bg-remove", "image-layers", "style-reference"}
    assert got["kokoro-tts"]["group"] == "audio" and got["kokoro-tts"]["output"] == "audio" and got["kokoro-tts"]["inputs_schema"]["text"]["required"] is True
    assert set(tools(api, "tools")) == {"bg-remove", "image-layers", "style-reference"} and tools(api, "3d") == {}


def test_tools_never_reach_the_chat_images_or_videos_choosers(ex):
    aliases = lambda kind: {m["alias"] for m in ex.get("/api/media/options", params={"kind": kind}).json()["models"]}  # noqa: E731
    assert not any("Kokoro" in a or "MoGe" in a or "BiRefNet" in a for a in aliases("image") | aliases("video"))
    jobs = ex.get("/api/media/jobs").json()
    assert jobs == []


def test_a_new_tool_is_off_until_an_administrator_enables_it(app, st, spark_url):
    api = setup(app, st, spark_url, enable_tools=False)
    from semechka.models import Model, ModelGrant
    from sqlalchemy import select
    with st.db.session() as s:
        rows = [m for m in s.execute(select(Model)).scalars() if m.real_id.startswith("tool:")]
        assert len(rows) == 6 and not any(m.enabled for m in rows)            # discovered, but off
        assert not s.execute(select(ModelGrant).where(ModelGrant.model_id.in_([m.id for m in rows]))).scalars().all()   # and granted to no one
        assert {m.alias for m in rows} >= {"kokoro-tts", "moge-relief"}
    # the owner holds models.use_all, so they can still try one (marked restricted, as on Images); nobody else sees any (see the member test)
    assert all(t["restricted"] for t in tools(api).values())
    assert tools(api)["kokoro-tts"]["alias"] == "Text to speech (Kokoro)"


def test_the_experimental_groups_count_what_this_person_may_use(ex):
    ops = ex.get("/api/me").json()["ui"]["operations"]
    assert ops["extras"]["enabled"] is True and ops["extras"]["groups"] == {"audio": 2, "tools": 3, "3d": 1}


def test_speech_runs_as_an_ordinary_job_and_is_stored_as_a_sound(ex):
    t = tools(ex)["kokoro-tts"]
    r = send(ex, t["id"], {"text": "Good evening.", "voice": t["inputs_schema"]["voice"]["values"][0], "speed": 1.1})
    assert r.status_code == 200, r.text
    sub = fm.STATE["tool_submissions"][-1]
    assert sub == {"tool": "kokoro-tts", "text": "Good evening.", "voice": t["inputs_schema"]["voice"]["values"][0], "speed": 1.1}
    j = finish(ex, r.json()["id"])
    assert j["status"] == "completed" and j["kind"] == "tool" and len(j["assets"]) == 1
    a = j["assets"][0]
    assert a["kind"] == "audio" and a["mime"] == "audio/wav" and a["prompt"] == "Good evening."
    body = ex.get(f"/api/media/assets/{a['id']}/content")
    assert body.status_code == 200 and body.content[:4] == b"RIFF" and body.headers["content-type"].startswith("audio/wav")
    assert fm.STATE["discards"]                                              # the engine keeps no copy
    lib = ex.get("/api/media/assets", params={"kind": "audio"}).json()
    assert [x["id"] for x in lib] == [a["id"]]
    assert [x["id"] for x in ex.get("/api/media/extras/jobs").json()] == [j["id"]]
    assert ex.get("/api/media/jobs", params={"kind": "image"}).json() == []  # the Images page does not list tool jobs


def test_a_3d_relief_delivers_a_mesh_and_its_depth_picture_each_filed_by_what_it_is(ex):
    t = tools(ex, "3d")["moge-relief"]
    r = send(ex, t["id"], {}, [("image", "mug.png", PNG, "image/png")])
    assert r.status_code == 200, r.text
    sub = fm.STATE["tool_submissions"][-1]
    assert sub["image"] in fm.STATE["uploads"] and fm.STATE["uploads"][sub["image"]] == PNG
    j = finish(ex, r.json()["id"])
    kinds = sorted((a["kind"], a["mime"]) for a in j["assets"])
    assert kinds == [("3d", "model/gltf-binary"), ("image", "image/png")]
    glb = next(a for a in j["assets"] if a["kind"] == "3d")
    assert ex.get(f"/api/media/assets/{glb['id']}/content").content[:4] == b"glTF"


def test_a_library_picture_can_be_the_input(ex):
    t = tools(ex, "tools")["bg-remove"]
    first = finish(ex, send(ex, t["id"], {}, [("image", "a.png", PNG, "image/png")]).json()["id"])
    cut = first["assets"][0]
    r = send(ex, t["id"], {}, assets={"asset:image": cut["id"]})
    assert r.status_code == 200, r.text
    assert fm.STATE["tool_submissions"][-1]["image"] in fm.STATE["uploads"]
    assert len(finish(ex, r.json()["id"])["assets"]) == 2                    # cut-out and mask


def test_the_engines_schema_is_enforced_before_anything_is_sent(ex):
    t = tools(ex)
    n = len(fm.STATE["tool_submissions"])
    assert send(ex, t["kokoro-tts"]["id"], {"voice": "af_heart"}).status_code == 422                       # text required
    assert send(ex, t["kokoro-tts"]["id"], {"text": "hi", "speed": 9}).status_code == 422                  # out of range
    assert send(ex, t["kokoro-tts"]["id"], {"text": "hi", "voice": "zz_nobody"}).status_code == 422        # not an installed voice
    assert send(ex, t["kokoro-tts"]["id"], {"text": "hi", "shout": True}).status_code == 422               # unknown setting
    assert send(ex, t["bg-remove"]["id"], {}).status_code == 422                                           # picture required
    bad = send(ex, t["bg-remove"]["id"], {}, [("image", "a.txt", b"hello", "text/plain")])
    assert bad.status_code == 422
    assert len(fm.STATE["tool_submissions"]) == n


def test_an_ordinary_model_cannot_be_run_as_a_tool_and_a_tool_not_as_an_image(ex):
    img = next(m for m in ex.get("/api/media/options", params={"kind": "image"}).json()["models"])
    assert send(ex, img["id"], {"text": "x"}).status_code == 404
    t = tools(ex)["kokoro-tts"]
    r = ex.post("/api/media/jobs", json={"kind": "image", "model_id": t["id"], "prompt": "x"})
    assert r.status_code == 403


def member(app, ex):
    r = ex.post("/api/admin/users", json={"email": "m@example.test", "display_name": "Mia Member", "password": PW, "role": "member"})
    assert r.status_code in (200, 201), r.text
    m = Api(app)
    assert m.post("/api/auth/login", json={"email": "m@example.test", "password": PW}).status_code == 200
    m.csrf = m.get("/api/me").json()["csrf"]
    return m


def test_a_disabled_tool_is_refused_to_members_even_by_id(ex, app, st):
    t = tools(ex)["ace-step-music"]
    from semechka.models import Model
    with st.db.session(write=True) as s:
        s.get(Model, t["id"]).enabled = False
    assert tools(ex)["ace-step-music"]["restricted"] is True                # the owner (models.use_all) still reaches it, marked
    m = member(app, ex)
    assert "ace-step-music" not in {x["tool"] for x in m.get("/api/media/extras").json()["tools"]}
    assert send(m, t["id"], {"tags": "lofi"}).status_code == 403
    assert m.get("/api/me").json()["ui"]["operations"]["extras"]["groups"].get("audio") == 1


def test_a_member_without_a_grant_sees_nothing_and_is_refused(ex, app, st):
    from semechka.models import Model, ModelGrant
    from sqlalchemy import select
    t = tools(ex)["kokoro-tts"]
    with st.db.session(write=True) as s:                                     # take every grant away from the speech tool
        for g in s.execute(select(ModelGrant).where(ModelGrant.model_id == t["id"])).scalars().all():
            s.delete(g)
    m = member(app, ex)
    listed = m.get("/api/media/extras").json()["tools"]
    assert "kokoro-tts" not in {x["tool"] for x in listed}
    assert send(m, t["id"], {"text": "hello"}).status_code == 403


def test_without_images_and_videos_there_are_no_tools(ex):
    for k in ("image", "video"):
        assert ex.put(f"/api/admin/settings/modes.{k}.enabled", json={"value": False}).status_code == 200
    assert ex.get("/api/media/extras").status_code == 403
    assert ex.get("/api/me").json()["ui"]["operations"]["extras"]["enabled"] is False


def test_an_engine_refusal_is_a_plain_error_and_nothing_is_kept(ex):
    t = tools(ex)["kokoro-tts"]
    fm.STATE["tools"][0]["ready"] = False
    fm.STATE["tools"][0]["reason"] = "the Kokoro environment (venv-tts) is not installed"
    r = send(ex, t["id"], {"text": "hello"})
    assert r.status_code in (409, 502) and "Kokoro environment" in r.text and "/home/" not in r.text
    assert ex.get("/api/media/extras/jobs").json() == []


def test_no_save_tool_results_live_in_memory_only(ex):
    assert ex.put("/api/admin/settings/privacy.ephemeral_allowed", json={"value": True}).status_code == 200
    t = tools(ex)["kokoro-tts"]
    j = finish(ex, send(ex, t["id"], {"text": "secret words"}, save="false").json()["id"])
    a = j["assets"][0]
    assert a["saved"] is False and a["id"].startswith("me_") and a["kind"] == "audio"
    assert ex.get(f"/api/media/assets/{a['id']}/content").content[:4] == b"RIFF"
    assert ex.get("/api/media/assets", params={"kind": "audio"}).json() == []


def test_tool_jobs_count_towards_the_daily_limit(ex):
    assert ex.put("/api/admin/settings/media.daily_generations", json={"value": 1}).status_code == 200
    t = tools(ex)["kokoro-tts"]
    finish(ex, send(ex, t["id"], {"text": "one"}).json()["id"])
    r = send(ex, t["id"], {"text": "two"})
    assert r.status_code == 429 and "Daily limit" in r.text


def test_sounds_and_meshes_are_not_edited_previewed_or_used_as_pictures(ex):
    t = tools(ex)
    snd = finish(ex, send(ex, t["kokoro-tts"]["id"], {"text": "hello"}).json()["id"])["assets"][0]
    mesh = next(a for a in finish(ex, send(ex, t["moge-relief"]["id"], {}, [("image", "p.png", PNG, "image/png")]).json()["id"])["assets"] if a["kind"] == "3d")
    for a in (snd, mesh):
        assert ex.get(f"/api/media/assets/{a['id']}/thumb").status_code == 404
        assert ex.post(f"/api/media/assets/{a['id']}/edits", json={"rotate": 90}).status_code == 422
        r = send(ex, t["bg-remove"]["id"], {}, assets={"asset:image": a["id"]})
        assert r.status_code == 422                                          # not a picture: refused before anything is sent
    lib = ex.get("/api/media/assets").json()
    assert {x["kind"] for x in lib} >= {"audio", "3d", "image"}
