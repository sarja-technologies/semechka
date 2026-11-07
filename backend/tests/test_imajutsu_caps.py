"""Imajutsu: what the server says it can do (capabilities, routing, navigation flag), the estimate, and who may see what."""
import json

import pytest

from semechka import imajutsu_routing as R

from . import fake_image as fi
from . import fake_sarness as fs
from .conftest import Api, make_user
from .imajutsu_helpers import (
    boot_images,
    gradient,
    mkproject,
    model_id,
    png_of,
    pytestmark_ffmpeg,
    solid_png,
    up,
)
from .studio_helpers import make_video, profile_entry
from .studio_helpers import up as upvideo

pytestmark = pytestmark_ffmpeg


def caps(api):
    r = api.get("/api/imajutsu/capabilities")
    assert r.status_code == 200, r.text
    return r.json()


def mode(c, mid):
    return next(m for m in c["modes"] if m["id"] == mid)


def rows(c, mid):
    return {m["engine_id"]: m for m in mode(c, mid)["models"]}


# ---- the matrix is honest ----------------------------------------------------------------------------------------------------------------------
# What each installed model can do comes from the engine double's registry (the real capabilities); this table is written independently of the code under test.
NEEDS = {"generate": "txt2img", "iphone_photo": "txt2img", "edit": "edit", "replace_subject": "edit", "inpaint": "inpaint", "outpaint": "inpaint", "reference": "edit"}
MODES = {m["id"]: m["capabilities"]["modes"] for m in fi.IMAGE_MODELS}
REFS = {m["id"]: m["capabilities"].get("references", 0) for m in fi.IMAGE_MODELS}


def test_every_mode_lists_every_model_with_the_truth(im):
    c = caps(im)
    assert [m["id"] for m in c["modes"]] == list(R.MODE_IDS) and [m["label"] for m in c["modes"]][:2] == ["Generate Image", "iPhone Photo"]
    for mid, need in NEEDS.items():
        r = rows(c, mid)
        assert {m["id"] for m in fi.IMAGE_MODELS} <= set(r), mid
        for eid, row in r.items():
            if eid not in MODES:
                continue                                                                                      # the classic test model of the double
            ok = need in MODES[eid]
            if mid == "replace_subject":
                ok = ok and REFS[eid] >= 2
            if mid == "reference":
                ok = ok and REFS[eid] >= 1
            routed = row["routed"]
            assert (row["status"] == ("ready" if routed else "available")) is ok, (mid, eid, row["status"], row["reason"])
            if not ok:
                assert row["status"] == "unavailable" and row["reason"]
    assert rows(c, "generate")["qwen-image-edit-2511"]["reason"].startswith("qwen-image-edit-2511 does not offer txt2img")
    assert rows(c, "edit")["z-image-turbo"]["status"] == "unavailable" and rows(c, "restyle")["z-image-turbo"]["status"] == "ready"
    assert rows(c, "generate")["sdxl-turbo"]["routed"] is False and rows(c, "generate")["sdxl-turbo"]["status"] == "available"
    for m in c["modes"]:
        assert m["status"] in ("ready", "unavailable") and (m["status"] == "ready") == bool(m["recommended"] or m["id"] == "upscale")


def test_recommended_is_the_first_operational_model_of_the_routing_file(im):
    c = caps(im)
    want = {"generate": "flux2-klein-4b", "iphone_photo": "flux2-klein-4b", "edit": "qwen-image-edit-2511", "replace_subject": "qwen-image-edit-2511", "inpaint": "flux2-klein-4b",
            "outpaint": "flux2-klein-4b", "reference": "flux2-klein-4b", "restyle": "z-image-turbo", "advanced": "flux2-klein-4b"}
    for mid, eid in want.items():
        rec = mode(c, mid)["recommended"]
        assert rec["alias"] == eid and rec["model_id"] == model_id(im, eid) and rec["why"] and rec["evidence"], mid
    up_ = mode(c, "upscale")
    assert up_["recommended"] is None and up_["models"] == [] and up_["scaling"] == {"method": "lanczos", "label": "Scaled, no new detail", "ai": False}
    assert up_["refine"]["available"] is True and up_["refine"]["recommended"]["model_id"] == model_id(im, "z-image-turbo")
    assert mode(c, "generate")["recommended"]["why"].startswith("The most convincing")


def test_limits_per_model_are_the_engines(im):
    c = caps(im)
    k = rows(c, "edit")["flux2-klein-4b"]["limits"]
    assert k["references"] == 4 and k["multiple"] == 16 and k["min_side"] == 256 and k["max_side"] == 2048 and k["native_pixels"] == 921600 and k["negative_prompt"] is False
    assert rows(c, "edit")["qwen-image-edit-2511"]["limits"]["references"] == 3 and rows(c, "restyle")["z-image-turbo"]["limits"]["references"] == 0
    assert rows(c, "generate")["sdxl-turbo"]["limits"]["multiple"] == 8 and rows(c, "generate")["sdxl-turbo"]["limits"]["schedulers"]
    assert [a["id"] for a in c["aspects"]] == ["9:16", "16:9", "4:5", "3:4", "1:1", "custom", "source"]
    assert c["presets"][0]["id"] == "iphone_photo" and c["presets"][0]["aspect"] == "9:16" and c["presets"][0]["model_id"] == model_id(im, "flux2-klein-4b")
    assert {x["id"] for x in c["presets"][0]["scene_chips"]} == {"outdoor_day", "indoor", "night", "flash", "selfie", "mirror", "casual"}
    assert c["limits"]["max_pictures"] == 200 and c["limits"]["max_count"] == 4 and c["roles"] == [{"id": r} for r in ("identity", "appearance", "composition", "lighting", "style")]
    assert c["routing"]["source"] == "packaged" and c["routing"]["problem"] is None


def test_a_mode_is_unavailable_with_the_reason_when_no_listed_model_can_do_it(app, st, sarness_url):
    api = boot_images(app, st, sarness_url, models=["qwen-image-2.1", "sdxl-turbo"])
    with api.c:
        member = make_user(api, app, "ann@example.test")
        c = caps(member)
        g = mode(c, "generate")
        assert g["status"] == "unavailable" and "None of the recommended models" in g["reason"] and g["recommended"] is None      # txt2img exists, but not on the list
        assert rows(c, "generate")["sdxl-turbo"]["status"] == "available"
        e = mode(c, "edit")
        assert e["status"] == "unavailable" and "installed or allowed for you" in e["reason"] and "qwen-image-edit-2511" in e["reason"]
        assert member.get("/api/me").json()["ui"]["operations"]["imajutsu"] == {"enabled": True, "reason": ""}                            # txt2img models exist: the page opens


def test_without_the_first_model_the_next_one_is_recommended(app, st, sarness_url):
    api = boot_images(app, st, sarness_url, models=["z-image-turbo", "qwen-image-edit-2511", "qwen-image-2512"])
    with api.c:
        member = make_user(api, app, "ann@example.test")                       # a member only reaches the models an administrator enabled (the owner reaches all)
        c = caps(member)
        assert rows(c, "generate")["flux2-klein-4b"]["status"] == "unavailable" and "available to you" in rows(c, "generate")["flux2-klein-4b"]["reason"]
        want = {"generate": "z-image-turbo", "iphone_photo": "qwen-image-2512", "edit": "qwen-image-edit-2511", "reference": "qwen-image-edit-2511", "inpaint": "z-image-turbo",
                "outpaint": "z-image-turbo", "replace_subject": "qwen-image-edit-2511"}
        for mid, eid in want.items():
            assert mode(c, mid)["recommended"]["alias"] == eid, mid


def test_reordering_the_routing_file_changes_the_recommendation(im, st):
    assert mode(caps(im), "edit")["recommended"]["alias"] == "qwen-image-edit-2511"
    doc = json.loads(R.PACKAGED.read_text())
    doc["modes"]["edit"]["models"].reverse()
    doc["modes"]["iphone_photo"]["models"].reverse()
    (st.cfg.data_dir / R.OVERRIDE_NAME).write_text(json.dumps(doc))
    c = caps(im)
    assert mode(c, "edit")["recommended"]["alias"] == "flux2-klein-4b" and [m["engine_id"] for m in mode(c, "edit")["models"] if m["routed"]] == ["flux2-klein-4b", "qwen-image-edit-2511"]
    assert mode(c, "iphone_photo")["recommended"]["alias"] == "z-image-turbo" and c["routing"]["source"] == "data_dir"
    # a plan picks it up too: no model is named anywhere in the code
    src = up(im, png_of(gradient(120, 160)), "s.png")
    p = mkproject(im, "edit", {"prompt": "make it warmer", "source_asset": src["id"], "aspect": {"id": "source"}})
    assert im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]["model"]["alias"] == "flux2-klein-4b"
    (st.cfg.data_dir / R.OVERRIDE_NAME).write_text("{ broken")
    c = caps(im)
    assert c["routing"]["source"] == "packaged" and "not usable" in c["routing"]["problem"] and mode(c, "edit")["recommended"]["alias"] == "qwen-image-edit-2511"
    (st.cfg.data_dir / R.OVERRIDE_NAME).unlink()
    assert caps(im)["routing"]["problem"] is None


def test_a_model_chosen_by_hand_that_is_not_on_the_list_still_plans_honestly(im):
    src = up(im, png_of(gradient(120, 160)), "s.png")
    p = mkproject(im, "restyle", {"prompt": "watercolour", "source_asset": src["id"], "aspect": {"id": "source"}, "model": model_id(im, "sdxl-turbo")})
    plan = im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]
    assert plan["errors"] == [] and plan["model"]["routed"] is False and plan["model"]["recommended"] is False and "not on the recommended list" in plan["model"]["why"]
    assert plan["native"]["multiple"] == 8 and plan["engine_mode"] == "img2img" and plan["denoise"] == 0.6


# ---- navigation ---------------------------------------------------------------------------------------------------------------------------------
def test_navigation_flag(app, st, sarness_url):
    api = boot_images(app, st, sarness_url, models=["qwen-image-edit-2511"])
    with api.c:
        assert api.get("/api/me").json()["ui"]["operations"]["imajutsu"] == {"enabled": True, "reason": ""}             # edit alone is enough
        assert api.put("/api/admin/settings/modes.image.enabled", json={"value": False}).status_code == 200
        off = api.get("/api/me").json()["ui"]["operations"]["imajutsu"]
        assert off["enabled"] is False and "administrator" in off["reason"]
        assert api.get("/api/imajutsu/capabilities").status_code == 403
        assert api.put("/api/admin/settings/modes.image.enabled", json={"value": True}).status_code == 200
        member = make_user(api, app, "nomodels@example.test")
        flag = member.get("/api/me").json()["ui"]["operations"]["imajutsu"]
        assert flag["enabled"] is True                                                                          # the member may use the enabled image model


def test_navigation_flag_without_models(app, st, sarness_url):
    api = boot_images(app, st, sarness_url, models=[])
    with api.c:
        member = make_user(api, app, "m@example.test")
        flag = member.get("/api/me").json()["ui"]["operations"]["imajutsu"]
        assert flag["enabled"] is False and flag["reason"]
        assert member.get("/api/imajutsu/capabilities").status_code in (403, 409)
        assert member.post("/api/imajutsu/projects", json={"mode": "generate"}).status_code in (403, 409)


# ---- the estimate ------------------------------------------------------------------------------------------------------------------------------------
def test_the_plan_shows_time_and_memory_only_when_the_engine_measured_something(im):
    p = mkproject(im, "generate", {"prompt": "a lemon"})
    plan = im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]
    assert plan["estimate"]["seconds"] == {"estimate": 4.0, "basis": "measured"} and plan["estimate"]["memory"]["basis"] == "measured" and plan["estimate"]["memory"]["peak_mb"] == 31500
    assert fs.STATE["model_estimates"][-1] == {"model": "flux2-klein-4b", "width": 720, "height": 1280, "steps": 4, "mode": "txt2img", "pictures": None}
    plan = im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={"spec": {"mode": "generate", "prompt": "a lemon", "quality": "high", "aspect": {"id": "1:1"}}}).json()["plan"]
    assert plan["estimate"]["seconds"]["basis"] == "extrapolated" and plan["estimate"]["seconds"]["estimate"] == pytest.approx(4.0 * 960 * 960 * 8 / (720 * 1280 * 4), abs=0.1)
    # a model nobody timed: no number at all
    q = mkproject(im, "generate", {"prompt": "a lemon", "model": model_id(im, "qwen-image-2.1")})
    assert im.post(f"/api/imajutsu/projects/{q['id']}/plan", json={}).json()["plan"]["estimate"] is None
    # an engine without the endpoint (older engine): same
    fs.STATE["no_estimate"] = True
    assert im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]["estimate"] is None


# ---- who may see what ---------------------------------------------------------------------------------------------------------------------------
def test_projects_jobs_and_pictures_are_private(im, app):
    src = up(im, png_of(gradient(120, 160)), "s.png")
    ref = up(im, solid_png((9, 9, 9)), "r.png")
    p = mkproject(im, "edit", {"prompt": "x", "source_asset": src["id"], "aspect": {"id": "source"}, "references": [{"asset": ref["id"]}]})
    d = im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={}).json()
    bob = make_user(im, app, "bob@example.test")
    assert bob.get("/api/imajutsu/projects").json() == [] and bob.get("/api/imajutsu/jobs").json() == []
    ghost = "sp_00000000000000000000"
    for method, path, kw in [("get", f"/api/imajutsu/projects/{p['id']}", {}), ("put", f"/api/imajutsu/projects/{p['id']}", {"json": {"spec": {"mode": "generate", "prompt": "hijack"}}}),
                             ("delete", f"/api/imajutsu/projects/{p['id']}", {}), ("post", f"/api/imajutsu/projects/{p['id']}/plan", {"json": {}}),
                             ("post", f"/api/imajutsu/projects/{p['id']}/generate", {"json": {}})]:
        mine = getattr(bob, method)(path, **kw)
        none = getattr(bob, method)(path.replace(p["id"], ghost), **kw)
        assert mine.status_code == 404 and mine.json() == none.json() == {"detail": {"code": "not_found", "message": "Not found."}}, (method, path, mine.text)
    for path in (f"/api/imajutsu/jobs/{d['job']}", ):
        assert bob.get(path).status_code == 404
    assert bob.post(f"/api/imajutsu/jobs/{d['job']}/reopen").status_code == 404
    # nothing of bob's request reached the owner's project or the engine
    assert im.get(f"/api/imajutsu/projects/{p['id']}").json()["spec"]["prompt"] == "x" and len(fs.STATE["image_submissions"]) == 1
    # someone else's pictures cannot be put into a project
    for field in ({"source_asset": src["id"]}, {"mask_asset": src["id"]}, {"references": [{"asset": ref["id"]}]}):
        bp = mkproject(bob, "generate", {"prompt": "x"})
        r = bob.put(f"/api/imajutsu/projects/{bp['id']}", json={"spec": {"mode": "generate", "prompt": "x", **field}})
        none = bob.put(f"/api/imajutsu/projects/{bp['id']}", json={"spec": {"mode": "generate", "prompt": "x", **{k: ("ma_000000000000000000000000" if isinstance(v, str) else [{"asset": "ma_000000000000000000000000"}]) for k, v in field.items()}}})
        assert r.status_code == 422 and r.json() == none.json() and r.json()["detail"]["code"] == "asset_missing"
    anon = Api(app)
    for path in ("/api/imajutsu/capabilities", "/api/imajutsu/projects", "/api/imajutsu/jobs", f"/api/imajutsu/projects/{p['id']}", f"/api/imajutsu/assets/{src['id']}/adjust/preview"):
        assert anon.get(path).status_code == 401
    # the CSRF check applies as everywhere
    raw = Api(app)
    raw.c.cookies.update(im.c.cookies)
    assert raw.post("/api/imajutsu/projects", json={"mode": "generate"}).status_code in (401, 403)


def test_video_and_image_projects_never_mix(app, st, sarness_url, tmp_path):
    api = boot_images(app, st, sarness_url, profiles=[profile_entry()])
    with api.c:
        clip = upvideo(api, make_video(tmp_path, dur=3), "clip.mp4")
        vp = api.post("/api/studio/projects", json={"source_asset": clip["id"]})
        assert vp.status_code == 200, vp.text
        ip = mkproject(api, "generate", {"prompt": "x"})
        assert [x["id"] for x in api.get("/api/studio/projects").json()] == [vp.json()["id"]]
        assert [x["id"] for x in api.get("/api/imajutsu/projects").json()] == [ip["id"]]
        assert api.get(f"/api/studio/projects/{ip['id']}").status_code == 404 and api.get(f"/api/imajutsu/projects/{vp.json()['id']}").status_code == 404
        assert api.put(f"/api/studio/projects/{ip['id']}", json={"spec": {}}).status_code == 404
        assert api.delete(f"/api/studio/projects/{ip['id']}").status_code == 404 and api.delete(f"/api/imajutsu/projects/{vp.json()['id']}").status_code == 404
        assert api.post(f"/api/studio/projects/{ip['id']}/plan", json={}).status_code == 404
        assert api.get(f"/api/imajutsu/projects/{ip['id']}").status_code == 200 and api.get(f"/api/studio/projects/{vp.json()['id']}").status_code == 200
        assert api.get("/api/studio/jobs").json() == [] and api.get("/api/imajutsu/jobs").json() == []
        assert api.get("/api/media/options", params={"kind": "image"}).status_code == 200


def test_iphone_photo_has_three_tiers_and_fast_is_the_default(im):
    c = caps(im)
    m = mode(c, "iphone_photo")
    assert [(t["id"], t["alias"], t["status"], t["default"]) for t in m["tiers"]] == [("fast", "flux2-klein-4b", "ready", True), ("best", "qwen-image-2512", "ready", False),
                                                                                     ("polished", "z-image-turbo", "ready", False)]
    assert [t["label"] for t in m["tiers"]] == ["Fast", "Best realism", "Polished"] and m["default_tier"] == "fast" and m["recommended"]["alias"] == "flux2-klein-4b"
    assert "2.5 min" in m["tiers"][1]["why"] and "144 s" in m["tiers"][1]["seconds_hint"] and "4 s" in m["tiers"][0]["seconds_hint"]
    assert c["presets"][0]["tiers"] == m["tiers"] and c["presets"][0]["default_tier"] == "fast"
    # generate: klein, then z-image-turbo, then qwen-image-2512
    assert [x["engine_id"] for x in mode(c, "generate")["models"] if x["routed"]] == ["flux2-klein-4b", "z-image-turbo", "qwen-image-2512"]
    # the tier chooses the model; the plan carries the tier, the wording and the real default steps
    for tier, alias, steps, guidance, secs in [(None, "flux2-klein-4b", 4, 1.0, 4.0), ("fast", "flux2-klein-4b", 4, 1.0, 4.0), ("best", "qwen-image-2512", 50, 4.0, 144.0),
                                                ("polished", "z-image-turbo", 9, 0.0, 12.0)]:
        p = mkproject(im, "iphone_photo", {"prompt": "my brother at a barbecue", "preset": {"id": "iphone_photo", "scenes": ["outdoor_day"], "finish": False, "tier": tier}})
        plan = im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]
        assert plan["model"]["alias"] == alias and plan["steps"] == steps and plan["guidance"] == guidance and plan["estimate"]["seconds"] == {"estimate": secs, "basis": "measured"}, tier
        assert plan["model"]["tier"] == (tier or "fast")
        assert (alias == "flux2-klein-4b") == ("handheld phone photo" in plan["prompt"]["final"]) and (alias == "flux2-klein-4b" or plan["prompt"]["final"].startswith("A photograph of"))
    bad = im.put(f"/api/imajutsu/projects/{p['id']}", json={"spec": {"mode": "iphone_photo", "prompt": "x", "preset": {"id": "iphone_photo", "tier": "ultra"}}})
    assert bad.status_code == 422


def test_a_tier_whose_model_is_missing_falls_back_and_says_so(app, st, sarness_url):
    api = boot_images(app, st, sarness_url, models=["flux2-klein-4b", "z-image-turbo"])
    with api.c:
        member = make_user(api, app, "ann@example.test")
        c = caps(member)
        tiers = {t["id"]: t for t in mode(c, "iphone_photo")["tiers"]}
        assert tiers["best"]["status"] == "unavailable" and tiers["fast"]["status"] == "ready"
        p = mkproject(member, "iphone_photo", {"prompt": "a dog", "preset": {"id": "iphone_photo", "scenes": [], "finish": False, "tier": "best"}})
        plan = member.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]
        assert plan["model"]["alias"] == "flux2-klein-4b" and any("Best realism needs a model that is not available" in w for w in plan["warnings"])
