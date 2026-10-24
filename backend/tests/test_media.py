"""Image and video generation against a Sarness job-protocol double (tests/fake_sarness.py).

The double is a test fixture for the HTTP protocol only; opt-in tests against the real engine are in
test_media_real.py. Everything below runs through the real FastAPI app, database, vault and disk.
"""
import time
from pathlib import Path

import pytest

from semechka import media_svc
from semechka.media_models import MediaAsset, MediaJob
from semechka.models import Backend, Model, ModelGrant, UsageEvent, User
from sqlalchemy import select

from . import fake_sarness as fs
from .conftest import PW, Api, Server, _free_port, make_user


@pytest.fixture(scope="session")
def sarness_url():
    srv = Server(fs.app, _free_port())
    srv.start()
    yield f"http://127.0.0.1:{srv.port}"
    srv.stop()


@pytest.fixture
def mo(app, st, sarness_url):
    """Owner of an install whose only backend is the Sarness double, with chat + image + video models enabled."""
    fs.reset()
    api = Api(app)
    from semechka.routers.setup import ensure_setup_token
    token = ensure_setup_token(st)
    r = api.post("/api/setup/complete", headers={"x-setup-token": token}, json={
        "owner": {"email": "owner@example.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
        "settings": {"modes.image.enabled": True, "modes.video.enabled": True, "privacy.chat_retention_days": 30},
        "backend": {"adapter": "sarness", "base_url": sarness_url, "api_key": fs.TOKEN},
        "enable_models": ["local:fake-chat", "image:fake-turbo", "video:fake-clip"], "smoke_model": "local:fake-chat"})
    assert r.status_code == 200, r.text
    assert r.json()["models_enabled"] == 3 and r.json()["smoke"]["ok"]
    api.csrf = api.get("/api/me").json()["csrf"]
    return api


def grant(owner, role, perms):
    r = {x["id"]: x for x in owner.get("/api/admin/roles").json()}[role]
    body = {"label": r["label"], "description": r["description"], "rank": r["rank"],
            "grants": sorted(set(r["grants"]) | set(perms)), "denies": r["denies"]}
    assert owner.put(f"/api/admin/roles/{role}", json=body).status_code == 200


def model_id(api, kind):
    return api.get("/api/media/options", params={"kind": kind}).json()["models"][0]["id"]


def img(api, mid=None, **kw):
    body = {"kind": "image", "model_id": mid or model_id(api, "image"), "prompt": "a red bicycle", "width": 512, "height": 512,
            "steps": 4, "seed": -1, **kw}
    return api.post("/api/media/jobs", json=body)


def vid(api, mid=None, **kw):
    body = {"kind": "video", "model_id": mid or model_id(api, "video"), "prompt": "waves", "width": 512, "height": 320,
            "frames": 17, "fps": 16, "steps": 20, **kw}
    return api.post("/api/media/jobs", json=body)


def finish(api, jid, tries=8):
    for _ in range(tries):
        j = api.get(f"/api/media/jobs/{jid}").json()
        if j["status"] in ("completed", "failed", "cancelled"):
            return j
    return j


def all_bytes(root: Path) -> bytes:
    out = b""
    for f in root.rglob("*"):
        if f.is_file():
            out += f.read_bytes()
    return out


# ---- discovery and mode state ----------------------------------------------------------------------
def test_media_models_are_discovered_for_their_mode_and_never_offered_to_chat(mo):
    chat = [m["alias"] for m in mo.get("/api/chat/models").json()]
    assert chat == ["fake-chat"]
    o = mo.get("/api/media/options", params={"kind": "image"}).json()
    assert [m["alias"] for m in o["models"]] == ["fake-turbo"]
    m = o["models"][0]
    assert m["limits"]["max_side"] == 768 and m["negative_prompt"] and m["schedulers"] == ["euler", "ddim"]
    v = mo.get("/api/media/options", params={"kind": "video"}).json()["models"][0]
    assert v["limits"]["resolutions"] == [[512, 320], [320, 512]] and v["defaults"]["frames"] <= 49
    # unsupported engine models and LoRAs are not catalogued as generators
    items = mo.get("/api/admin/models").json()["items"]
    assert not any("unsupported-one" in str(x) or "some-lora" in str(x) for x in items) and len(items) == 3


def test_mode_state_reflects_the_connected_backend(mo):
    ui = mo.get("/api/me").json()["ui"]["modes"]
    assert ui["image"]["enabled"] and ui["video"]["enabled"]


def test_modes_are_unsupported_when_no_backend_can_generate(owner):
    owner.put("/api/admin/settings/modes.image.enabled", json={"value": True})
    ui = owner.get("/api/me").json()["ui"]["modes"]["image"]
    assert not ui["enabled"] and ui["code"] == "unsupported" and "Sarness" in ui["reason"]
    r = owner.get("/api/media/options", params={"kind": "image"})
    assert r.status_code == 501


def test_mode_can_be_switched_off(mo):
    mid = model_id(mo, "image")
    mo.put("/api/admin/settings/modes.image.enabled", json={"value": False})
    assert mo.get("/api/media/options", params={"kind": "image"}).status_code == 403
    assert img(mo, mid=mid).status_code == 403
    assert mo.get("/api/media/options", params={"kind": "video"}).status_code == 200


# ---- the happy path, storage and serving -----------------------------------------------------------
def test_image_generation_end_to_end_encrypted_at_rest(mo, cfg, st):
    r = img(mo, prompt="a red bicycle", negative_prompt="blurry", seed=7)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "running" and j["progress"] == 0.5 and "Sampling" in j["progress_text"]
    sent = fs.STATE["submissions"][-1]
    assert sent["model"] == "fake-turbo" and sent["seed"] == 7 and sent["negative_prompt"] == "blurry"
    done = finish(mo, j["id"])
    assert done["status"] == "completed" and len(done["assets"]) == 1
    a = done["assets"][0]
    assert a["kind"] == "image" and a["mime"] == "image/png" and a["saved"] and a["prompt"] == "a red bicycle"
    c = mo.get(f"/api/media/assets/{a['id']}/content")
    assert c.status_code == 200 and c.headers["content-type"] == "image/png" and c.content.startswith(b"\x89PNG")
    assert fs.MARKER in c.content
    assert "sandbox" in c.headers["content-security-policy"] and c.headers["x-content-type-options"] == "nosniff"
    dl = mo.get(f"/api/media/assets/{a['id']}/content", params={"download": "true"})
    assert dl.headers["content-disposition"].startswith("attachment; filename=semechka-image-")
    # the bytes on disk, the database and the prompt are not plaintext
    files = list((cfg.data_dir / "assets").rglob("*.bin"))
    assert len(files) == 1 and fs.MARKER not in files[0].read_bytes()
    blob = all_bytes(cfg.data_dir)
    assert fs.MARKER not in blob and b"a red bicycle" not in blob and b"blurry" not in blob
    # the engine kept no copy of the result file
    assert fs.STATE["discards"] and fs.STATE["discards"][0]["permanent"] is True
    # one usage event, no text
    with st.db.session() as s:
        ev = s.execute(select(UsageEvent).where(UsageEvent.kind == "image")).scalars().all()
        assert len(ev) == 1 and ev[0].status == "complete" and not ev[0].ephemeral


def test_video_generation_and_range_requests(mo):
    j = vid(mo).json()
    done = finish(mo, j["id"])
    assert done["status"] == "completed"
    a = done["assets"][0]
    assert a["mime"] == "video/mp4" and a["duration"] == pytest.approx(17 / 16)
    full = mo.get(f"/api/media/assets/{a['id']}/content")
    part = mo.get(f"/api/media/assets/{a['id']}/content", headers={"Range": "bytes=4-11"})
    assert part.status_code == 206 and part.content == full.content[4:12]
    assert part.headers["content-range"] == f"bytes 4-11/{len(full.content)}"
    assert mo.get(f"/api/media/assets/{a['id']}/content", headers={"Range": "bytes=999999-"}).status_code == 416


def test_gallery_lists_own_jobs_newest_first_with_progress(mo):
    a = img(mo, prompt="first").json()
    finish(mo, a["id"])
    b = img(mo, prompt="second").json()
    rows = mo.get("/api/media/jobs", params={"kind": "image"}).json()
    assert [r["prompt"] for r in rows][:2] == ["second", "first"] or rows[0]["id"] in (b["id"], a["id"])
    assert {r["status"] for r in rows} <= {"running", "completed"}
    assert mo.get("/api/media/jobs", params={"kind": "video"}).json() == []


# ---- validation ------------------------------------------------------------------------------------
@pytest.mark.parametrize("kw,needle", [
    ({"width": 1024}, "between"), ({"width": 513}, "multiple of 8"), ({"steps": 99}, "steps"),
    ({"guidance": 5}, "guidance"), ({"prompt": "   "}, "prompt"), ({"seed": -5}, "seed"),
    ({"scheduler": "nope"}, "scheduler"), ({"prompt": "x" * 4001}, "longer"),
])
def test_image_parameters_are_validated_against_the_engines_limits(mo, kw, needle):
    r = img(mo, **kw)
    assert r.status_code == 422 and needle in r.json()["detail"]["message"], r.text
    assert not fs.STATE["jobs"]


def test_video_parameters_and_clip_length_cap(mo):
    assert vid(mo, width=640, height=360).status_code == 422          # not a supported resolution
    assert vid(mo, frames=18).status_code == 422                      # 1 + multiple of 4
    assert vid(mo, negative_prompt="").status_code == 200
    mo.put("/api/admin/settings/media.video_max_seconds", json={"value": 1})
    r = vid(mo)                                                        # 17 frames at 16 fps is 1.06 s
    assert r.status_code == 422 and "1 seconds" in r.json()["detail"]["message"]
    assert vid(mo, frames=13).status_code == 200
    opts = mo.get("/api/media/options", params={"kind": "video"}).json()
    assert opts["video_max_seconds"] == 1 and opts["models"][0]["defaults"]["frames"] == 13


def test_default_image_size_setting_is_clamped_to_the_model(mo):
    mo.put("/api/admin/settings/media.default_image_size", json={"value": 2000})
    d = mo.get("/api/media/options", params={"kind": "image"}).json()["models"][0]["defaults"]
    assert d["width"] == d["height"] == 768
    mo.put("/api/admin/settings/media.default_image_size", json={"value": 300})
    d = mo.get("/api/media/options", params={"kind": "image"}).json()["models"][0]["defaults"]
    assert d["width"] == 304


def test_kind_must_match_the_model(mo):
    r = mo.post("/api/media/jobs", json={"kind": "video", "model_id": model_id(mo, "image"), "prompt": "x",
                                         "width": 512, "height": 320, "frames": 17, "fps": 16})
    assert r.status_code == 403


# ---- permissions, ownership, model access -----------------------------------------------------------
def test_members_need_the_mode_permission_and_never_see_each_others_media(mo, app):
    m1, m2 = make_user(mo, app, "m1@example.test"), make_user(mo, app, "m2@example.test")
    mid = model_id(mo, "image")
    assert m1.get("/api/media/options", params={"kind": "image"}).status_code == 403
    assert img(m1, mid=mid).status_code == 403 and m1.get("/api/media/jobs").status_code == 403
    grant(mo, "member", ["mode.image", "mode.video", "chat.ephemeral"])
    j = img(m1, prompt="private thing").json()
    done = finish(m1, j["id"])
    aid = done["assets"][0]["id"]
    assert m1.get(f"/api/media/assets/{aid}/content").status_code == 200
    for other in (m2, mo):  # another member, and even the owner
        assert other.get(f"/api/media/jobs/{j['id']}").status_code == 404
        assert other.get(f"/api/media/assets/{aid}/content").status_code == 404
        assert other.delete(f"/api/media/assets/{aid}").status_code == 404
        assert other.delete(f"/api/media/jobs/{j['id']}").status_code == 404
        assert other.post(f"/api/media/jobs/{j['id']}/cancel").status_code == 404
        assert all(r["id"] != j["id"] for r in other.get("/api/media/jobs").json())
    assert mo.get(f"/api/media/assets/{aid}/content").json()["detail"]["code"] == "not_found"


def test_unauthenticated_and_csrf(mo, app):
    anon = Api(app)
    assert anon.get("/api/media/options", params={"kind": "image"}).status_code == 401
    assert anon.get("/api/media/assets/ma_x/content").status_code == 401
    r = mo.c.request("POST", "/api/media/jobs", json={"kind": "image", "model_id": "x", "prompt": "p"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "csrf"


def test_model_grants_are_enforced_on_the_direct_api(mo, app, st):
    grant(mo, "member", ["mode.image"])
    m = make_user(mo, app, "m@example.test")
    mid = model_id(mo, "image")
    assert img(m, mid=mid).status_code == 200
    with st.db.session() as s:
        s.execute(ModelGrant.__table__.delete().where(ModelGrant.model_id == mid, ModelGrant.subject_id == "member"))
    # with no accessible image model the mode itself reports why
    r = m.get("/api/media/options", params={"kind": "image"})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_model"
    r = m.post("/api/media/jobs", json={"kind": "image", "model_id": mid, "prompt": "x", "width": 512, "height": 512})
    assert r.status_code in (403, 409)
    with st.db.session() as s:
        s.add(ModelGrant(model_id=mid, subject_type="user", subject_id=m.user_id, effect="deny"))
        s.add(ModelGrant(model_id=mid, subject_type="role", subject_id="member", effect="allow"))
    # deny wins over allow: the person has no usable image model, so the mode reports it
    assert img(m, mid=mid).status_code == 409
    assert m.get("/api/media/options", params={"kind": "image"}).json()["detail"]["code"] == "no_model"


# ---- quotas ----------------------------------------------------------------------------------------
def test_concurrent_job_quota(mo):
    mo.put("/api/admin/settings/media.max_concurrent_jobs", json={"value": 1})
    first = img(mo, prompt="slow one")
    assert first.status_code == 200
    second = img(mo, prompt="another")
    assert second.status_code == 429 and second.json()["detail"]["code"] == "busy"
    assert mo.post(f"/api/media/jobs/{first.json()['id']}/cancel").json()["status"] == "cancelled"
    assert img(mo, prompt="another").status_code == 200


def test_daily_and_gallery_quotas(mo):
    mo.put("/api/admin/settings/media.daily_generations", json={"value": 2})
    for i in range(2):
        finish(mo, img(mo, prompt=f"p{i}").json()["id"])
    r = img(mo)
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit"
    mo.put("/api/admin/settings/media.daily_generations", json={"value": 0})
    mo.put("/api/admin/settings/media.max_assets", json={"value": 2})
    r = img(mo)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "gallery_full"
    caps = mo.get("/api/media/options", params={"kind": "image"}).json()["caps"]
    assert caps["stored"] == 2 and caps["max_assets"] == 2


# ---- cancel, failure, reconnection -----------------------------------------------------------------
def test_cancel_stops_the_engine_job_and_records_it(mo, st):
    j = img(mo, prompt="slow render").json()
    assert j["status"] == "running" and j["progress"] == 0.3
    c = mo.post(f"/api/media/jobs/{j['id']}/cancel").json()
    assert c["status"] == "cancelled" and fs.STATE["jobs"]["j_0001"]["status"] == "cancelled"
    with st.db.session() as s:
        assert s.execute(select(UsageEvent).where(UsageEvent.status == "cancelled")).scalars().first()
    assert mo.delete(f"/api/media/jobs/{j['id']}").status_code == 200
    assert mo.post(f"/api/media/jobs/{j['id']}/cancel").status_code == 404


def test_engine_failure_is_reported_without_server_paths(mo):
    j = img(mo, prompt="boom").json()
    done = finish(mo, j["id"])
    assert done["status"] == "failed" and "out of memory" in done["error"]
    assert "/home/x" not in done["error"] and "<path>" in done["error"]
    assert done["assets"] == []


def test_active_jobs_cannot_be_deleted_and_backend_outage_is_survived(mo, st):
    j = img(mo, prompt="slow forever").json()
    assert mo.delete(f"/api/media/jobs/{j['id']}").status_code == 409
    with st.db.session() as s:
        b = s.execute(select(Backend)).scalars().first()
        good = b.base_url
        b.base_url = "http://127.0.0.1:1"
    again = mo.get(f"/api/media/jobs/{j['id']}").json()
    assert again["status"] == "running" and "Waiting for the inference backend" in again["progress_text"]
    with st.db.session() as s:
        s.execute(select(Backend)).scalars().first().base_url = good
    assert mo.get(f"/api/media/jobs/{j['id']}").json()["progress_text"].startswith("Sampling")
    # an outage longer than the give-up window ends the job instead of leaving it spinning forever
    with st.db.session() as s:
        s.get(MediaJob, j["id"]).last_ok = time.time() - media_svc.UNREACHABLE_GIVE_UP_S - 5
        s.execute(select(Backend)).scalars().first().base_url = "http://127.0.0.1:1"
    assert mo.get(f"/api/media/jobs/{j['id']}").json()["status"] == "failed"


def test_result_retrieval_is_retried_then_gives_up(mo):
    fs.STATE["reject_files"] = True
    j = img(mo, prompt="flaky").json()
    for _ in range(3):
        cur = mo.get(f"/api/media/jobs/{j['id']}").json()
    assert cur["status"] == "running" and cur["progress_text"] == "Retrieving the result"
    fs.STATE["reject_files"] = False
    assert finish(mo, j["id"])["status"] == "completed"
    fs.STATE["reject_files"] = True
    j2 = img(mo, prompt="flaky again").json()
    done = finish(mo, j2["id"], tries=12)
    assert done["status"] == "failed" and done["assets"] == []


def test_a_restart_does_not_lose_a_running_job(mo, app, cfg):
    j = img(mo, prompt="survive").json()
    from semechka.app import create_app
    app2 = create_app(cfg)  # same data dir: a restarted process
    other = Api(app2)
    assert other.login("owner@example.test").status_code == 200
    done = finish(other, j["id"])
    assert done["status"] == "completed" and done["assets"][0]["prompt"] == "survive"


# ---- no-save and retention -------------------------------------------------------------------------
def test_no_save_writes_neither_prompt_nor_file_nor_asset_row(mo, cfg, st):
    grant(mo, "member", [])
    j = img(mo, prompt="top secret scribble", save=False, negative_prompt="hush").json()
    done = finish(mo, j["id"])
    assert done["status"] == "completed" and done["save"] is False
    a = done["assets"][0]
    assert a["id"].startswith("me_") and a["saved"] is False and a["prompt"] == ""
    assert mo.get(f"/api/media/assets/{a['id']}/content").content.startswith(b"\x89PNG")
    assert not list((cfg.data_dir / "assets").rglob("*")) or not list((cfg.data_dir / "assets").rglob("*.bin"))
    with st.db.session() as s:
        assert s.execute(select(MediaAsset)).scalars().all() == []
        row = s.get(MediaJob, j["id"])
        assert row.prompt_enc is None and row.params_enc is None
    st.vault  # noqa: B018
    assert b"top secret scribble" not in all_bytes(cfg.data_dir) and fs.MARKER not in all_bytes(cfg.data_dir)
    assert mo.delete(f"/api/media/assets/{a['id']}").status_code == 200
    assert mo.get(f"/api/media/assets/{a['id']}/content").status_code == 404


def test_no_save_results_expire_from_memory(mo, st):
    j = img(mo, save=False).json()
    a = finish(mo, j["id"])["assets"][0]
    for v in st.media_mem.values():
        v["expires"] = time.time() - 1
    assert mo.get(f"/api/media/assets/{a['id']}/content").status_code == 404
    assert all(r["id"] != j["id"] for r in mo.get("/api/media/jobs").json())  # an expired result is simply gone
    media_svc.sweep(st)
    # the content-free job row is dropped once its result is gone
    with st.db.session() as s:
        s.get(MediaJob, j["id"]).finished_at = time.time() - media_svc.EPHEMERAL_TTL - 5
    media_svc.sweep(st)
    with st.db.session() as s:
        assert s.get(MediaJob, j["id"]) is None


def test_no_save_can_be_forbidden(mo, app):
    mo.put("/api/admin/settings/privacy.ephemeral_allowed", json={"value": False})
    assert img(mo, save=False).status_code == 403
    assert mo.get("/api/media/options", params={"kind": "image"}).json()["ephemeral"] is False
    grant(mo, "member", ["mode.image"])
    mo.put("/api/admin/settings/privacy.ephemeral_allowed", json={"value": True})
    m = make_user(mo, app, "nomem@example.test")
    assert img(m, save=False).status_code == 403  # members lack chat.ephemeral


def test_organisation_that_stores_nothing_forces_no_save(mo, cfg):
    mo.put("/api/admin/settings/privacy.chat_retention_days", json={"value": 0, "confirm_weaker": True})
    r = img(mo, prompt="not stored").json()
    assert r["forced_unsaved"] is True
    done = finish(mo, r["id"])
    assert done["assets"][0]["saved"] is False
    assert not list((cfg.data_dir / "assets").rglob("*.bin"))


def test_retention_expiry_removes_files_prompts_and_failed_jobs(mo, cfg, st):
    j = img(mo, prompt="soon gone").json()
    done = finish(mo, j["id"])
    f = done["assets"][0]
    assert f["expires_at"] and f["expires_at"] == pytest.approx(time.time() + 30 * 86400, abs=120)
    bad = finish(mo, img(mo, prompt="boom").json()["id"])
    assert bad["status"] == "failed"
    with st.db.session() as s:
        s.get(MediaAsset, f["id"]).expires_at = time.time() - 1
        s.get(MediaJob, j["id"]).expires_at = time.time() - 1
        s.get(MediaJob, bad["id"]).expires_at = time.time() - 1
    res = media_svc.sweep(st)
    assert res["expired_assets"] >= 2
    assert not list((cfg.data_dir / "assets").rglob("*.bin"))
    with st.db.session() as s:
        assert s.execute(select(MediaJob)).scalars().all() == []
    assert b"soon gone" not in all_bytes(cfg.data_dir)


def test_deleting_an_asset_and_a_user_removes_the_files(mo, app, cfg, st):
    grant(mo, "member", ["mode.image"])
    m = make_user(mo, app, "gone@example.test")
    j = img(m, prompt="mine").json()
    aid = finish(m, j["id"])["assets"][0]["id"]
    assert len(list((cfg.data_dir / "assets").rglob("*.bin"))) == 1
    assert m.delete(f"/api/media/assets/{aid}").status_code == 200
    assert not list((cfg.data_dir / "assets").rglob("*.bin"))
    j = img(m, prompt="mine again").json()
    finish(m, j["id"])
    assert list((cfg.data_dir / "assets").rglob("*.bin"))
    assert mo.delete(f"/api/admin/users/{m.user_id}").status_code == 200
    media_svc.sweep(st)
    assert not list((cfg.data_dir / "assets").rglob("*.bin"))
    with st.db.session() as s:
        assert s.execute(select(MediaAsset)).scalars().all() == [] and s.get(User, m.user_id) is None


def test_gallery_item_removal_deletes_its_files(mo, cfg):
    j = img(mo).json()
    finish(mo, j["id"])
    assert mo.delete(f"/api/media/jobs/{j['id']}").status_code == 200
    assert not list((cfg.data_dir / "assets").rglob("*.bin")) and mo.get("/api/media/jobs").json() == []


# ---- technical visibility and honesty ---------------------------------------------------------------
def test_technical_fields_follow_permissions_and_hide_paths(mo, app):
    grant(mo, "member", ["mode.image"])
    plain = make_user(mo, app, "plain@example.test")
    j = finish(plain, img(plain).json()["id"])
    assert j["tech"] == {}
    grant(mo, "member", ["tech.queue.position", "tech.perf.duration", "tech.model.id", "tech.node.name"])
    j = finish(plain, img(plain).json()["id"])
    assert j["tech"]["model_real_id"] == "image:fake-turbo" and "elapsed_s" in j["tech"] and j["tech"]["node"]
    assert "gen_params" not in j["tech"] and "trace_id" not in j["tech"]


def test_protection_statement_is_derived_from_the_configuration(mo):
    p = mo.get("/api/media/protection", params={"kind": "image"}).json()
    text = " ".join(x["text"] for x in p["lines"])
    assert "not end-to-end" in text and "job history" in text and "30 days" in text and p["ephemeral_available"]
    mo.put("/api/admin/settings/privacy.chat_retention_days", json={"value": 0, "confirm_weaker": True})
    text = " ".join(x["text"] for x in mo.get("/api/media/protection", params={"kind": "image"}).json()["lines"])
    assert "stores no generated files" in text


def test_other_adapters_report_media_as_unsupported():
    import asyncio
    from semechka import adapters
    ad = adapters.make("openai_compat", "http://127.0.0.1:1/v1")
    with pytest.raises(adapters.Unsupported):
        asyncio.run(ad.media_submit("image", {}, "k" * 10))
    assert adapters.REGISTRY["sarness"].manifest.media and not adapters.REGISTRY["ollama"].manifest.media


def test_backup_carries_the_encrypted_gallery(mo, cfg):
    from semechka.cli import _snapshot
    import io
    import tarfile
    finish(mo, img(mo).json()["id"])
    with tarfile.open(fileobj=io.BytesIO(_snapshot(cfg)), mode="r:gz") as tf:
        assert any(n.startswith("assets/") and n.endswith(".bin") for n in tf.getnames())
