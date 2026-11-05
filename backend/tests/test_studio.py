"""OpenJutsu Studio, part 1: capabilities and navigation, projects (validation, ownership, concurrency, encryption) and the plan.
Real ffmpeg and real files; only the inference engine is a double (tests/fake_sarness.py)."""
import json

import pytest

from semechka import studio_plan as sp

from . import fake_sarness as fs
from .conftest import make_user
from .studio_helpers import (
    boot,
    content,
    make_video,
    mkproject,
    png,
    profile_entry,
    pytestmark_ffmpeg,
    up,
)

pytestmark = pytestmark_ffmpeg


# ---- capabilities and navigation ----------------------------------------------------------------------------------------
def test_capabilities_list_only_what_the_engine_published(sm):
    c = sm.get("/api/studio/capabilities").json()
    modes = {m["id"]: m for m in c["modes"]}
    assert set(modes) == {"subject_replacement", "character_replacement", "object_replacement", "vehicle_replacement", "motion_transfer",
                          "reference_to_video", "video_restyling", "advanced_editing"}
    assert modes["subject_replacement"]["status"] == "ready" and modes["subject_replacement"]["operation"] == "swap"
    assert modes["vehicle_replacement"]["status"] == "experimental" and "vehicles" in modes["vehicle_replacement"]["reason"]
    assert modes["vehicle_replacement"]["fixed_inputs"] == {"object_mode": "change"}
    assert modes["motion_transfer"]["status"] == "unavailable" and "motion-transfer" in modes["motion_transfer"]["reason"]
    mm = modes["subject_replacement"]["models"][0]
    assert mm["reference_policy"] == {"min": 1, "active_max": 1} and mm["limits"]["resolutions"]["max_short_edge"] == 768 and mm["subject_selection"]["box"] is True
    models = {m["alias"]: m for m in c["models"]}
    oj = models["openjutsu-minimax-h3"]
    assert oj["kind"] == "editing_profile" and "subject_replacement" in oj["modes"] and "vehicle_replacement" in oj["modes"]
    assert {u["mode"] for u in oj["unsupported"]} == {"motion_transfer", "reference_to_video", "video_restyling"}
    gen = models["fake-clip"]
    assert gen["kind"] == "generator" and gen["modes"] == [] and len(gen["unsupported"]) == 8
    assert "cannot edit a video" in gen["unsupported"][0]["reason"]
    assert c["limits"]["video_max_seconds"] > 0 and c["limits"]["max_assets"] and c["limits"]["resolutions"]["presets"][0] == "480p"


def test_vehicle_mode_is_unavailable_when_the_engine_says_so(app, st, sarness_url):
    api = boot(app, st, sarness_url, [profile_entry(vehicle="unavailable")])
    m = next(x for x in api.get("/api/studio/capabilities").json()["modes"] if x["id"] == "vehicle_replacement")
    assert m["status"] == "unavailable" and "vehicle-capable" in m["reason"]


def test_an_engine_that_does_not_say_what_it_can_do_offers_nothing(app, st, sarness_url):
    old = profile_entry()
    for k in ("modes", "limits", "reference_policy", "subject_selection", "operation_status"):
        old.pop(k)
    api = boot(app, st, sarness_url, [old])
    c = api.get("/api/studio/capabilities").json()
    assert all(m["status"] == "unavailable" for m in c["modes"])
    oj = next(m for m in c["models"] if m["kind"] == "editing_profile")
    assert oj["modes"] == [] and "does not say which editing operations" in oj["unsupported"][0]["reason"]
    ops = api.get("/api/me").json()["ui"]["operations"]["openjutsu"]
    assert ops["enabled"] is False and "does not say which operations" in ops["reason"]


def test_navigation_flag_is_on_with_an_editing_model(sm):
    assert sm.get("/api/me").json()["ui"]["operations"]["openjutsu"] == {"enabled": True, "reason": ""}


def test_navigation_flag_is_off_without_an_editing_model(app, st, sarness_url):
    api = boot(app, st, sarness_url, [])
    ops = api.get("/api/me").json()["ui"]["operations"]["openjutsu"]
    assert ops["enabled"] is False and "No video-editing model" in ops["reason"]
    assert api.get("/api/studio/projects").status_code == 403


def test_not_ready_profile_is_not_offered(app, st, sarness_url):
    api = boot(app, st, sarness_url, [profile_entry(ready=False)])
    ops = api.get("/api/me").json()["ui"]["operations"]["openjutsu"]
    assert ops["enabled"] is False and "models missing" in ops["reason"]
    c = api.get("/api/studio/capabilities").json()
    assert {m["status"] for m in c["modes"]} == {"not_ready"}


def test_studio_needs_the_video_mode(sm):
    assert sm.put("/api/admin/settings/modes.video.enabled", json={"value": False}).status_code == 200
    for method, url in (("get", "/api/studio/capabilities"), ("get", "/api/studio/projects"), ("get", "/api/studio/jobs")):
        assert getattr(sm, method)(url).status_code == 403
    assert sm.get("/api/me").json()["ui"]["operations"]["openjutsu"]["enabled"] is False


# ---- projects ----------------------------------------------------------------------------------------------------------------------
@pytest.fixture
def vid(sm, tmp_path):
    return up(sm, make_video(tmp_path, dur=6, gop=24), "holiday.mp4")


def test_project_lifecycle_spec_validation_and_stale_saves(sm, vid, tmp_path):
    p = mkproject(sm, vid["id"])
    assert p["title"] == "holiday" and p["spec"]["version"] == 1 and p["spec"]["source_asset"] == vid["id"] and p["source"]["id"] == vid["id"]
    assert p["prepared_asset"]["id"] == vid["id"] and p["prepared_current"] is True        # nothing to change: the source is used as it is
    pic = up(sm, png("red", d=tmp_path), "ref.png")
    spec = {"source_asset": vid["id"], "trim": {"start": 1, "end": 4}, "crop": {"x": 10, "y": 10, "w": 200, "h": 150},
            "frame": {"aspect": "9:16", "ratio": None, "lock": True, "treatment": "pad"}, "resolution": {"preset": "720p", "width": None, "height": None, "lock_aspect": True},
            "audio": "mute", "references": [{"asset": pic["id"], "active": True, "subject": 1, "label": "Anna"}],
            "mode": "subject_replacement", "subject": {"text": "the dancer", "kind": "person", "box": [0.1, 0.1, 0.5, 0.5], "points": [{"x": 0.2, "y": 0.3, "positive": True}]},
            "model": None, "settings": {"seed": 5, "hat_fix": False}, "accept_experimental": False}
    r = sm.put(f"/api/studio/projects/{p['id']}", json={"spec": spec, "expected_updated_at": p["updated_at"], "title": "Beach day"})
    assert r.status_code == 200, r.text
    q = r.json()
    assert q["title"] == "Beach day" and q["spec"]["trim"] == {"start": 1.0, "end": 4.0} and q["spec"]["references"][0]["label"] == "Anna" and q["spec"]["audio"] == "mute"
    assert q["updated_at"] > p["updated_at"]
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["spec"] == q["spec"]
    # a stale save is refused with the current project so the page can show what changed
    r = sm.put(f"/api/studio/projects/{p['id']}", json={"spec": spec, "expected_updated_at": p["updated_at"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale" and r.json()["detail"]["project"]["updated_at"] == q["updated_at"]
    lst = sm.get("/api/studio/projects").json()
    assert [x["id"] for x in lst] == [p["id"]] and lst[0]["title"] == "Beach day" and lst[0]["thumb_asset"] == vid["id"] and lst[0]["last_job"] is None


@pytest.mark.parametrize("patch,why", [
    ({"trim": {"start": 5, "end": 9}}, "within the"),
    ({"trim": {"start": 2, "end": 2.05}}, "at least 0.1 s"),
    ({"trim": {"start": -1, "end": 2}}, "between"),
    ({"trim": {"start": "a", "end": 2}}, "must be a number"),
    ({"crop": {"x": 300, "y": 0, "w": 100, "h": 100}}, "reaches outside the 320 × 240"),
    ({"crop": {"x": 0, "y": 0, "w": 8, "h": 8}}, "too small"),
    ({"frame": {"aspect": "7:5"}}, "Pick one of the shapes"),
    ({"frame": {"aspect": "custom"}}, "frame.ratio must be a number"),
    ({"frame": {"aspect": "1:1", "treatment": "warp"}}, "crop, pad or stretch"),
    ({"resolution": {"preset": "8k"}}, "Pick one of the sizes"),
    ({"resolution": {"preset": "custom"}}, "needs a width or a height"),
    ({"resolution": {"preset": "custom", "width": 99999}}, "between"),
    ({"audio": "loud"}, "keep or mute"),
    ({"mode": "Rm -rf"}, "not valid"),
    ({"model": "m_nonexistent"}, "does not exist"),
    ({"subject": {"kind": "ghost"}}, "kind of subject"),
    ({"subject": {"box": [0.5, 0.5, 0.9, 0.9]}}, "inside the first frame"),
    ({"subject": {"box": [0, 0, 1]}}, "x, y, width and height"),
    ({"subject": {"text": "x" * 501}}, "longer than 500"),
    ({"settings": {"Bad Key": 1}}, "not a valid setting name"),
    ({"settings": {"seed": [1]}}, "number, text or on/off"),
    ({"references": "x"}, "must be a list"),
    ({"references": [{"asset": "ma_nope", "active": True}]}, "no longer in your library"),
])
def test_invalid_specs_are_refused_with_a_plain_sentence(sm, vid, patch, why):
    p = mkproject(sm, vid["id"])
    r = sm.put(f"/api/studio/projects/{p['id']}", json={"spec": {"source_asset": vid["id"], **patch}})
    assert r.status_code == 422 and why in r.json()["detail"]["message"], r.text
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]["trim"] is None        # nothing was saved


def test_references_are_unlimited_up_to_a_sanity_cap_and_must_be_the_callers_pictures(sm, vid, tmp_path, app):
    pics = [up(sm, png(c, d=tmp_path), f"{c}.png") for c in ("red", "green", "blue")]
    p = mkproject(sm, vid["id"])
    many = [{"asset": pics[i % 3]["id"], "active": True} for i in range(200)]
    assert sm.put(f"/api/studio/projects/{p['id']}", json={"spec": {"source_asset": vid["id"], "references": many}}).status_code == 200
    r = sm.put(f"/api/studio/projects/{p['id']}", json={"spec": {"source_asset": vid["id"], "references": many + many[:1]}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_references"
    # a video is not a picture; somebody else's picture is "not in your library"
    r = sm.put(f"/api/studio/projects/{p['id']}", json={"spec": {"source_asset": vid["id"], "references": [{"asset": vid["id"]}]}})
    assert r.status_code == 422 and "must be a picture" in r.json()["detail"]["message"]
    bob = make_user(sm, app, "bob@example.test")
    mine = up(bob, png("red", d=tmp_path), "b.png")
    r = sm.put(f"/api/studio/projects/{p['id']}", json={"spec": {"source_asset": vid["id"], "references": [{"asset": mine["id"]}]}})
    assert r.status_code == 422 and "no longer in your library" in r.json()["detail"]["message"]
    assert bob.post("/api/studio/projects", json={"source_asset": vid["id"]}).status_code == 422          # not his video either


def test_a_project_is_private_and_deleting_it_never_deletes_media(sm, vid, app, tmp_path):
    p = mkproject(sm, vid["id"], {"trim": {"start": 1, "end": 3}})
    bob = make_user(sm, app, "bob@example.test")
    for method, url, kw in (("get", f"/api/studio/projects/{p['id']}", {}), ("put", f"/api/studio/projects/{p['id']}", {"json": {"spec": {}}}),
                            ("delete", f"/api/studio/projects/{p['id']}", {}), ("post", f"/api/studio/projects/{p['id']}/plan", {"json": {}}),
                            ("post", f"/api/studio/projects/{p['id']}/prepare", {"json": {}}), ("post", f"/api/studio/projects/{p['id']}/generate", {"json": {}})):
        r = getattr(bob, method)(url, **kw)
        assert r.status_code == 404 and r.json()["detail"] == {"code": "not_found", "message": "Not found."}, (method, url, r.text)
    assert bob.get("/api/studio/projects").json() == []
    ghost = bob.get("/api/studio/projects/sp_doesnotexist0000000")
    assert ghost.status_code == 404 and ghost.json() == bob.get(f"/api/studio/projects/{p['id']}").json()          # a stranger's id looks like a missing one
    before = content(sm, vid["id"])
    assert sm.delete(f"/api/studio/projects/{p['id']}").json() == {"ok": True}
    assert sm.get(f"/api/studio/projects/{p['id']}").status_code == 404
    assert sm.get(f"/api/media/assets/{vid['id']}").status_code == 200 and content(sm, vid["id"]) == before


def test_the_spec_is_encrypted_at_rest(sm, st, vid):
    p = mkproject(sm, vid["id"], {"subject": {"text": "the very secret sentence", "kind": "person"}, "mode": "subject_replacement"}, title="Private title")
    from semechka.studio_models import StudioProject
    with st.db.session() as s:
        row = s.get(StudioProject, p["id"])
        blob = row.spec_enc + row.title_enc + row.state_enc
    assert "secret sentence" not in blob and "Private title" not in blob and vid["id"] not in blob


# ---- the plan: pure -------------------------------------------------------------------------------------------------------------
LIM = profile_entry()["limits"]


def src(w, h, dur=10.0, rot=0, **kw):
    return {"id": "ma_src", "width": w, "height": h, "duration": dur, "rotation": rot, "fps": 24.0, "audio": True, "vcodec": "h264", "acodec": "aac", "mime": "video/mp4", **kw}


def spec(**kw):
    d = {"trim": None, "crop": None, "frame": {"aspect": "original", "ratio": None, "lock": True, "treatment": "crop"},
         "resolution": {"preset": "original", "width": None, "height": None, "lock_aspect": True}, "audio": "keep"}
    for k, v in kw.items():
        d[k] = {**d[k], **v} if isinstance(v, dict) and isinstance(d.get(k), dict) else v
    return d


def test_crop_rectangles_for_landscape_portrait_and_square_sources():
    p = sp.plan(src(1920, 1080), spec(frame={"aspect": "9:16"}), LIM)
    assert p["frame"]["crop"] == {"x": 656, "y": 0, "w": 608, "h": 1080} and p["prepared"] == {"width": 608, "height": 1080, "scale": False}
    p = sp.plan(src(1080, 1920), spec(frame={"aspect": "16:9"}), LIM)
    assert p["frame"]["crop"] == {"x": 0, "y": 656, "w": 1080, "h": 608}
    p = sp.plan(src(1000, 1000), spec(frame={"aspect": "4:3"}), LIM)
    assert p["frame"]["crop"] == {"x": 0, "y": 124, "w": 1000, "h": 750}
    p = sp.plan(src(1920, 1080), spec(frame={"aspect": "1:1"}), LIM)
    assert p["frame"]["crop"] == {"x": 420, "y": 0, "w": 1080, "h": 1080}
    for pr in (sp.plan(src(1921, 1081), spec(frame={"aspect": "3:4"}), LIM), sp.plan(src(333, 777), spec(frame={"aspect": "16:9"}), LIM)):
        c = pr["frame"]["crop"]
        assert all(v % 2 == 0 for v in c.values()) and pr["prepared"]["width"] % 2 == 0 and pr["prepared"]["height"] % 2 == 0


def test_a_chosen_crop_is_reshaped_inside_itself_and_must_fit_the_picture():
    p = sp.plan(src(1920, 1080), spec(crop={"x": 100, "y": 100, "w": 800, "h": 600}, frame={"aspect": "1:1"}), LIM)
    assert p["frame"]["crop"] == {"x": 200, "y": 100, "w": 600, "h": 600}
    p = sp.plan(src(1920, 1080), spec(crop={"x": 1900, "y": 0, "w": 200, "h": 200}), LIM)
    assert "reaches outside" in p["errors"][0]
    p = sp.plan(src(1920, 1080), spec(crop={"x": 0, "y": 0, "w": 10, "h": 10}), LIM)
    assert "too small" in p["errors"][0]


def test_pad_and_stretch_keep_the_whole_picture():
    p = sp.plan(src(1920, 1080), spec(frame={"aspect": "9:16", "treatment": "pad"}), LIM)
    assert p["frame"]["pad"] == {"w": 1920, "h": 3414, "x": 0, "y": 1166} and p["prepared"]["scale"] is True and p["prepare"]["ops"]["crop"] is None
    assert p["prepared"]["height"] % 2 == 0 and min(p["prepared"]["width"], p["prepared"]["height"]) <= 2 * p["processing"]["short_edge"]
    p = sp.plan(src(320, 240), spec(frame={"aspect": "1:1", "treatment": "pad"}), LIM)
    assert p["frame"]["pad"] == {"w": 320, "h": 320, "x": 0, "y": 40} and p["prepared"] == {"width": 320, "height": 320, "scale": False}
    p = sp.plan(src(240, 320), spec(frame={"aspect": "16:9", "treatment": "stretch"}), LIM)
    assert p["frame"]["stretch"] == {"w": 568, "h": 320} and p["frame"]["crop"] == {"x": 0, "y": 0, "w": 240, "h": 320}
    p = sp.plan(src(320, 240), spec(frame={"aspect": "4:3", "treatment": "pad"}), LIM)
    assert "pad" not in p["frame"] and p["frame"]["crop"] == {"x": 0, "y": 0, "w": 320, "h": 240}                  # already that shape: nothing to do


EDGES = {"480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160}


@pytest.mark.parametrize("aspect", ["original", "16:9", "9:16", "1:1", "4:3", "3:4"])
@pytest.mark.parametrize("preset", ["original", "480p", "720p", "1080p", "1440p", "4k"])
def test_the_resolution_matrix_is_honest_about_what_the_model_does(aspect, preset):
    p = sp.plan(src(1920, 1080), spec(frame={"aspect": aspect}, resolution={"preset": preset}), LIM)
    assert not p["errors"]
    pw, ph = p["prepared"]["width"], p["prepared"]["height"]
    req = p["output"]["requested"]
    want = min(pw, ph) if preset == "original" else EDGES[preset]
    assert min(req["width"], req["height"]) == (want if preset != "original" else min(pw, ph))
    proc = p["processing"]
    presets = [480, 576, 736, 768]
    below = [e for e in presets if e <= want]
    expect = max(below) if below else min(presets)
    assert proc["short_edge"] == expect and proc["preset"] == f"{expect}p"
    assert proc["width"] % 32 == 0 and proc["height"] % 32 == 0 and min(proc["width"], proc["height"]) == expect
    assert proc["exact"] is (expect == want)
    nat = p["output"]["native"]
    assert (nat["width"], nat["height"]) == (proc["width"], proc["height"])
    scaling = "upscale" if want > expect else "downscale" if want < expect else "none"
    assert p["output"]["scaling"] == scaling
    assert f"{expect}p" in proc["why"] and ("nothing is scaled" in proc["why"] if proc["exact"] else str(expect) in proc["why"])
    if scaling == "upscale":
        assert "scaling it up" in p["output"]["note"] and "no new detail" in p["output"]["note"]
    if want > 768:
        assert any("larger than the model can process" in w and f"{expect}p" in w for w in p["warnings"])
    else:
        assert not p["warnings"]
    assert min(pw, ph) <= 2 * expect + 1                 # the prepared file is never needlessly larger than twice the working size


def test_the_processing_size_uses_the_engines_own_formula():
    p = sp.plan(src(1024, 576), spec(resolution={"preset": "720p"}), LIM)               # 1024x576 at 576p: scale 1 -> 1024x576
    assert (p["processing"]["short_edge"], p["processing"]["width"], p["processing"]["height"]) == (576, 1024, 576)
    assert sp.processing_size(1024, 576, 736) == (1312, 736)                           # the validated record in the engine's profile
    assert sp.processing_size(608, 1080, 576) == (576, 1024)
    assert fs.processing_size(1234, 777, 576) == sp.processing_size(1234, 777, 576)    # the double and the plan agree
    mem = sp.plan(src(1024, 576), spec(resolution={"preset": "720p"}), {**LIM, "resolutions": {**LIM["resolutions"], "presets": ["480p", "736p"]}})
    assert mem["processing"]["short_edge"] == 480 or mem["processing"]["short_edge"] == 736
    p = sp.plan(src(1024, 576), {**spec(), "resolution": {"preset": "custom", "width": 1314, "height": None, "lock_aspect": True}}, {**LIM, "resolutions": {**LIM["resolutions"], "presets": ["736p"]}})
    assert p["processing"]["short_edge"] == 736 and p["processing"]["memory"]["basis"] == "measured" and p["processing"]["memory"]["peak_mb"] == 78848


def test_custom_sizes_above_what_the_model_can_do_are_not_silently_clamped():
    p = sp.plan(src(1920, 1080), {**spec(), "resolution": {"preset": "custom", "width": 3840, "height": None, "lock_aspect": True}}, LIM)
    assert (p["output"]["requested"]["width"], p["output"]["requested"]["height"]) == (3840, 2160)
    assert p["processing"]["short_edge"] == 768 and p["output"]["scaling"] == "upscale"
    assert any("3840×2160 is larger than the model can process" in w and "768p" in w and "scaling" in w for w in p["warnings"])
    p = sp.plan(src(1920, 1080), {**spec(), "resolution": {"preset": "custom", "width": 300, "height": None, "lock_aspect": True}}, LIM)
    assert p["processing"]["short_edge"] == 480 and p["output"]["scaling"] == "downscale" and "smaller than the smallest" in p["processing"]["why"]
    p = sp.plan(src(1920, 1080), {**spec(), "resolution": {"preset": "custom", "width": 640, "height": 999, "lock_aspect": False}}, LIM)
    assert (p["output"]["requested"]["width"], p["output"]["requested"]["height"]) == (640, 1000)
    p = sp.plan(src(1920, 1080), {**spec(), "resolution": {"preset": "custom", "width": None, "height": None, "lock_aspect": True}}, LIM)
    assert "Enter a width or a height" in p["errors"][0]
    p = sp.plan(src(1920, 1080), {**spec(), "resolution": {"preset": "custom", "width": 9000, "height": None, "lock_aspect": True}}, LIM)
    assert "outside what can be made" in p["errors"][0]


def test_a_source_longer_than_the_models_limit_is_an_error_until_trimmed():
    p = sp.plan(src(1280, 720, dur=42), spec(), LIM)
    assert "takes up to 30 s" in p["errors"][0] and "42.0 s" in p["errors"][0]
    assert not sp.plan(src(1280, 720, dur=42), spec(trim={"start": 3, "end": 8}), LIM)["errors"]
    assert sp.plan(src(1280, 720, dur=42), spec(trim={"start": 0, "end": 31}), LIM)["errors"]


def test_no_model_means_no_processing_size_and_a_warning():
    p = sp.plan(src(1280, 720), spec(), None)
    assert p["processing"] is None and "No editing model" in p["warnings"][0]


def test_trim_decision_copy_reencode_or_nothing():
    kfs = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert sp.plan(src(1280, 720), spec(), LIM)["trim"]["mode"] == "none"
    assert sp.plan(src(1280, 720), spec(trim={"start": 2.0, "end": 4.0}), LIM, keyframes=kfs)["trim"]["mode"] == "copy"
    assert sp.plan(src(1280, 720), spec(trim={"start": 0, "end": 4.0}), LIM)["trim"]["mode"] == "copy"          # the start of the file is always a key frame
    assert sp.plan(src(1280, 720), spec(trim={"start": 2.5, "end": 4.0}), LIM, keyframes=kfs)["trim"]["mode"] == "reencode"
    unknown = sp.plan(src(1280, 720), spec(trim={"start": 2.0, "end": 4.0}), LIM)
    assert unknown["trim"]["mode"] == "reencode" and unknown["trim"]["needs_keyframes"] is True
    assert sp.plan(src(1280, 720), spec(audio="mute"), LIM)["trim"]["mode"] == "copy"
    assert sp.plan(src(1280, 720, rot=90), spec(), LIM)["trim"]["mode"] == "reencode"                            # rotation is burned in
    assert sp.plan(src(1280, 720, vcodec="vp9", acodec="opus", mime="video/webm"), spec(), LIM)["trim"]["mode"] == "reencode"
    assert sp.plan(src(1280, 720, mime="video/x-matroska"), spec(), LIM)["trim"]["mode"] == "copy"             # h264/aac in MKV: repackaged
    assert sp.plan(src(1280, 720), spec(frame={"aspect": "1:1"}), LIM)["trim"]["mode"] == "reencode"
    assert sp.plan(src(1280, 720, audio=False, acodec=None), spec(audio="mute"), LIM)["trim"]["mode"] == "none"     # nothing to mute


def test_the_cache_key_is_deterministic_and_ignores_what_does_not_change_the_file():
    a = sp.plan(src(1920, 1080), spec(trim={"start": 1, "end": 4}, frame={"aspect": "9:16"}), LIM)
    b = sp.plan(src(1920, 1080), spec(trim={"start": 1, "end": 4}, frame={"aspect": "9:16", "lock": False}, resolution={"preset": "720p"}), LIM)
    assert a["prepare"]["key"] == b["prepare"]["key"] and len(a["prepare"]["key"]) == 64
    assert sp.plan(src(1920, 1080), spec(trim={"start": 1, "end": 5}, frame={"aspect": "9:16"}), LIM)["prepare"]["key"] != a["prepare"]["key"]
    assert sp.plan({**src(1920, 1080), "id": "ma_other"}, spec(trim={"start": 1, "end": 4}, frame={"aspect": "9:16"}), LIM)["prepare"]["key"] != a["prepare"]["key"]
    assert sp.plan(src(1920, 1080), spec(frame={"aspect": "original", "treatment": "pad"}), LIM)["prepare"]["key"] == sp.plan(src(1920, 1080), spec(), LIM)["prepare"]["key"]
    assert sp.cache_key("ma_x", {"a": 1, "b": 2}) == sp.cache_key("ma_x", {"b": 2, "a": 1})


def test_the_plan_route_is_pure_and_does_not_save(sm, vid):
    p = mkproject(sm, vid["id"])
    r = sm.post(f"/api/studio/projects/{p['id']}/plan", json={"spec": {"source_asset": vid["id"], "frame": {"aspect": "9:16"}, "resolution": {"preset": "1080p"}}})
    assert r.status_code == 200
    pl = r.json()["plan"]
    assert pl["input"] == {"width": 320, "height": 240, "duration": pl["input"]["duration"], "rotation": 0, "fps": 24.0, "audio": True}
    assert pl["processing"]["short_edge"] == 768 and pl["output"]["scaling"] == "upscale" and pl["prepare"]["mode"] == "reencode"
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]["frame"]["aspect"] == "original"
    assert sm.post(f"/api/studio/projects/{p['id']}/plan", json={"spec": {"source_asset": vid["id"], "frame": {"aspect": "bad"}}}).status_code == 422


def test_discovery_and_the_registry_carry_the_studio_fields(sm, st):
    import asyncio

    from semechka.backends_svc import adapter_for
    from semechka.models import Backend
    with st.db.session() as s:
        ad = adapter_for(s.query(Backend).first(), st)
    entry = asyncio.run(ad.registry())["profiles"]["openjutsu-minimax-h3"]
    assert len(entry["modes"]) == 8 and entry["reference_policy"]["swap"] == {"min": 1, "active_max": 1} and entry["limits"]["resolutions"]["max_short_edge"] == 768
    assert entry["subject_selection"]["box"] is True and entry["operation_status"]["swap"]["status"] == "ready"
    prof = next(m for m in sm.get("/api/media/options", params={"kind": "video"}).json()["models"] if m.get("profile"))["profile"]
    assert {"modes", "limits", "reference_policy", "subject_selection", "operation_status"} <= set(prof)
    assert prof["inputs_schema"]["refs"]["max_files"] == 64 and prof["modes"][0]["id"] == "subject_replacement"
    assert "/home" not in json.dumps(prof)


def test_a_mode_that_runs_on_another_profile_is_not_claimed_by_this_one(sm):
    c = sm.get("/api/studio/capabilities").json()
    oj = next(m for m in c["models"] if m["kind"] == "editing_profile")
    assert "reference_to_video" not in oj["modes"]
    why = next(u["reason"] for u in oj["unsupported"] if u["mode"] == "reference_to_video")
    assert "'openjutsu-reference-video' profile" in why


def test_the_plan_shows_what_the_engine_measured_even_when_extrapolated(sm, vid):
    p = mkproject(sm, vid["id"], {"resolution": {"preset": "480p"}})
    proc = sm.post(f"/api/studio/projects/{p['id']}/plan", json={}).json()["plan"]["processing"]
    assert proc["short_edge"] == 480 and proc["memory"]["basis"] == "extrapolated" and proc["memory"]["peak_mb"] == 78848 and "736p" in proc["memory"]["note"]
    q = mkproject(sm, vid["id"], {"resolution": {"preset": "720p"}})
    proc = sm.post(f"/api/studio/projects/{q['id']}/plan", json={}).json()["plan"]["processing"]
    assert proc["short_edge"] == 576 and proc["memory"]["basis"] == "extrapolated"
