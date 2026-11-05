"""OpenJutsu Studio, part 3: generating. What the engine receives is checked against the engine double: exactly the active reference pictures in order,
only the subject information the engine advertises support for, the prepared video; everything else is listed as not used or refused in plain words."""
import pytest

from . import fake_sarness as fs
from .conftest import make_user
from .studio_helpers import (
    boot,
    content,
    make_video,
    mkproject,
    oj_model,
    png,
    probe,
    profile_entry,
    pytestmark_ffmpeg,
    up,
    wait_edit,
)

pytestmark = pytestmark_ffmpeg

COLORS = ["red", "green", "blue", "yellow", "cyan", "magenta", "orange", "purple", "pink", "brown", "gray", "white"]


@pytest.fixture
def clip(sm, tmp_path):
    return up(sm, make_video(tmp_path, dur=6, fps=24, gop=24), "clip.mp4")


@pytest.fixture
def pics(sm, tmp_path):
    """Twelve different pictures (different colours AND sizes, so their bytes differ)."""
    return [up(sm, png(c, w=64 + 2 * i, h=64, d=tmp_path), f"{c}.png") for i, c in enumerate(COLORS)]


def make_spec(sm, clip, refs, **kw):
    s = {"mode": "subject_replacement", "model": oj_model(sm), "subject": {"text": "the dancer", "kind": "person", "box": None, "points": []},
         "references": refs, **kw}
    return s


def refs_of(pics, n, active=None):
    return [{"asset": pics[i]["id"], "active": True if active is None else i in active, "subject": None, "label": f"p{i + 1}"} for i in range(n)]


def gen(api, pid, spec=None, expect=200, **body):
    r = api.post(f"/api/studio/projects/{pid}/generate", json={**({"spec": spec} if spec is not None else {}), **body})
    assert r.status_code == expect, r.text
    return r.json()


def engine_inputs(n=-1):
    return fs.STATE["profile_submissions"][n]["inputs"]


def uploaded(ids):
    ids = ids if isinstance(ids, list) else [ids]
    return [fs.STATE["uploads"][i] for i in ids]


def test_a_basic_run_sends_the_prepared_video_the_picture_and_the_subject(sm, clip, pics, tmp_path):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), trim={"start": 2, "end": 5}, frame={"aspect": "9:16", "treatment": "crop"},
                                            resolution={"preset": "720p"}))
    out = gen(sm, p["id"])
    i = engine_inputs()
    assert i["operation"] == "swap" and i["target"] == "the dancer" and i["prompt"] == "the dancer"
    assert i["short_edge"] == 576 and i["resolution"] == "576p"                             # the largest size not above 720p
    assert len(i["refs"]) == 1 and uploaded(i["refs"])[0] == content(sm, pics[0]["id"])
    prepared = out["prepared_asset"]
    assert prepared["origin"] == "prepared" and uploaded(i["video"])[0] == content(sm, prepared["id"])
    info = probe(uploaded(i["video"])[0], tmp_path)
    assert (info["w"], info["h"]) == (136, 240) and abs(info["vdur"] - 3.0) < 0.1 and info["acodec"] == "aac"
    sub = fs.STATE["profile_submissions"][0]
    assert [f["name"] for f in sub["files"]["video"]] == ["clip (prepared).mp4"] and [f["name"] for f in sub["files"]["refs"]] == ["red.png"]
    acc = out["accounting"]
    assert [x["asset"] for x in acc["sent_to_engine"]] == [pics[0]["id"]] and acc["not_used"] == [] and acc["operation"] == "swap"
    assert acc["subject"]["text"] == "the dancer" and acc["subject"]["sent"] == ["text", "kind"]
    assert acc["inputs_sent"]["target"] == "the dancer" and "video" not in acc["inputs_sent"]
    assert out["job"].startswith("mj_") and out["job_detail"]["status"] in ("queued", "running")
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["jobs"][0]["id"] == out["job"]


def test_the_job_is_a_normal_job_and_reopens_with_everything(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 3), subject={"text": "the man", "kind": "person"}))
    out = gen(sm, p["id"])
    fs.advance(100)
    j = sm.get(f"/api/media/jobs/{out['job']}").json()
    assert j["status"] == "completed" and j["assets"][0]["kind"] == "video"
    rows = sm.get("/api/studio/jobs").json()
    assert [r["job"] for r in rows] == [out["job"]] and rows[0]["status"] == "completed" and rows[0]["project_id"] == p["id"] and rows[0]["mode"] == "subject_replacement"
    assert rows[0]["outputs"][0]["kind"] == "video" and rows[0]["model"] == "openjutsu-minimax-h3"
    st_ = rows[0]["studio"]
    assert [r["asset"] for r in st_["references"]] == [x["id"] for x in pics[:3]] and st_["prepared_asset"] == clip["id"]      # nothing needed preparing: the source was used as it is
    assert st_["plan"]["processing"]["short_edge"] and st_["requested_output"]["preset"] == "original"
    one = sm.get(f"/api/studio/jobs/{out['job']}").json()
    assert one["job"] == out["job"] and one["studio"]["accounting"]["sent_to_engine"][0]["asset"] == pics[0]["id"]
    # a job made without the Studio is not in the list
    plain = sm.post("/api/media/profile-jobs", data={"model_id": oj_model(sm), "inputs": '{"prompt":"the man","target":"the man"}', "asset:video": clip["id"], "asset:refs": pics[0]["id"]})
    assert plain.status_code == 200, plain.text
    assert [r["job"] for r in sm.get("/api/studio/jobs").json()] == [out["job"]]


@pytest.mark.parametrize("n", [1, 3, 9, 12])
@pytest.mark.parametrize("mode,limit", [("subject_replacement", 1), ("character_replacement", 4)])
def test_reference_accounting_sends_only_the_active_pictures_the_operation_can_use(sm, clip, pics, n, mode, limit):
    off = {1} if n >= 3 else set()
    active = [i for i in range(n) if i not in off]
    refs = refs_of(pics, n, set(active))
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs, mode=mode, accept_experimental=True))
    out = gen(sm, p["id"])
    want = active[:limit]
    i = engine_inputs()
    assert uploaded(i["refs"]) == [content(sm, pics[k]["id"]) for k in want]               # exactly those, in the project's order
    acc = out["accounting"]
    assert [x["asset"] for x in acc["sent_to_engine"]] == [pics[k]["id"] for k in want]
    unused = {x["asset"]: x["reason"] for x in acc["not_used"]}
    assert set(unused) == {pics[k]["id"] for k in range(n)} - {pics[k]["id"] for k in want}
    for k in range(n):
        if k in off:
            assert unused[pics[k]["id"]] == "Switched off in the project."
        elif k not in want:
            assert "uses" in unused[pics[k]["id"]] and "after them" in unused[pics[k]["id"]]
    # the project and the job keep ALL the pictures, active or not
    assert len(sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]["references"]) == n
    fs.advance(2)
    row = next(r for r in sm.get("/api/studio/jobs").json() if r["job"] == out["job"])
    assert [r["asset"] for r in row["studio"]["references"]] == [x["id"] for x in pics[:n]]
    assert [r["active"] for r in row["studio"]["references"]] == [k not in off for k in range(n)]
    # and the engine's own record agrees: one picture node per used picture, loading the file that was uploaded
    doc = sm.get(f"/api/studio/jobs/{out['job']}/inspect").json()
    assert doc["conditioning"]["agree"] is True and len(doc["conditioning"]["references"]) == len(want) and all(r["used"] for r in doc["conditioning"]["references"])


def test_reordering_and_removing_pictures_persists_and_changes_what_is_sent(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 3), mode="character_replacement", accept_experimental=True))
    gen(sm, p["id"])
    assert uploaded(engine_inputs()["refs"]) == [content(sm, pics[k]["id"]) for k in (0, 1, 2)]
    spec = sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]
    spec["references"] = [spec["references"][2], spec["references"][0]]                       # reorder and remove one
    assert sm.put(f"/api/studio/projects/{p['id']}", json={"spec": spec}).status_code == 200
    assert [r["asset"] for r in sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]["references"]] == [pics[2]["id"], pics[0]["id"]]
    gen(sm, p["id"])
    assert uploaded(engine_inputs()["refs"]) == [content(sm, pics[2]["id"]), content(sm, pics[0]["id"])]
    assert engine_inputs()["roles"] == "p3\np1"                                                 # the labels name who each picture replaces


def test_too_few_pictures_for_the_operation_is_refused_before_anything_is_sent(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 2, active=set())))
    r = gen(sm, p["id"], expect=422)
    assert r["detail"]["code"] == "references_missing" and "at least 1 reference picture switched on; you have 0" in r["detail"]["message"]
    assert fs.STATE["profile_submissions"] == [] and fs.STATE["uploads"] == {}


def test_experimental_operations_need_an_explicit_opt_in(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), mode="object_replacement", subject={"text": "the cup", "kind": "object"}))
    r = gen(sm, p["id"], expect=422)
    assert r["detail"]["code"] == "experimental_not_accepted" and "experimental" in r["detail"]["message"] and "no real-inference validation" in r["detail"]["message"]
    assert fs.STATE["profile_submissions"] == []
    out = gen(sm, p["id"], accept_experimental=True)
    i = engine_inputs()
    assert i["operation"] == "object_edit" and i["object_mode"] == "change" and i["object_target"] == "the cup" and i["accept_experimental"] is True
    assert "target" not in i and out["accounting"]["operation"] == "object_edit"
    # the opt-in saved in the project counts too
    q = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), mode="object_replacement", subject={"text": "the cup", "kind": "object"}, accept_experimental=True))
    gen(sm, q["id"])


def test_a_validated_operation_does_not_need_the_opt_in(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1)))
    gen(sm, p["id"])
    assert engine_inputs().get("accept_experimental") is not True


def test_operations_the_engine_cannot_do_are_refused_with_its_reason(sm, clip, pics):
    for mode, why in (("motion_transfer", "no motion-transfer graph"), ("reference_to_video", "runs on the engine's 'openjutsu-reference-video' profile"),
                      ("video_restyling", "restyling has not been wired")):
        p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), mode=mode, accept_experimental=True))
        r = gen(sm, p["id"], expect=422)
        assert r["detail"]["code"] == "mode_unavailable" and why in r["detail"]["message"], r
    assert fs.STATE["profile_submissions"] == []


def test_vehicle_replacement_on_an_engine_that_says_unavailable(app, st, sarness_url, tmp_path):
    api = boot(app, st, sarness_url, [profile_entry(vehicle="unavailable")])
    with api.c:
        clip = up(api, make_video(tmp_path, dur=3), "c.mp4")
        pic = up(api, png("red", d=tmp_path), "r.png")
        p = mkproject(api, clip["id"], {"mode": "vehicle_replacement", "model": oj_model(api), "subject": {"text": "the dirt bike", "kind": "vehicle"},
                                        "references": [{"asset": pic["id"], "active": True}], "accept_experimental": True})
        r = gen(api, p["id"], expect=422)
        assert r["detail"]["code"] == "mode_unavailable" and "Vehicle replacement is not available on" in r["detail"]["message"] and "vehicle-capable" in r["detail"]["message"]


def test_vehicle_replacement_is_experimental_when_the_engine_says_so(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), mode="vehicle_replacement", subject={"text": "the dirt bike", "kind": "vehicle"}))
    assert gen(sm, p["id"], expect=422)["detail"]["code"] == "experimental_not_accepted"
    gen(sm, p["id"], accept_experimental=True)
    i = engine_inputs()
    assert i["object_target"] == "the dirt bike" and i["subject_kind"] == "vehicle" and i["object_mode"] == "change"


def test_a_model_that_is_not_an_editing_model_is_refused(sm, clip, pics):
    models = sm.get("/api/media/options", params={"kind": "video"}).json()["models"]
    gen_model = next(m["id"] for m in models if not m.get("profile"))
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1)))
    r = gen(sm, p["id"], spec={**sm.get(f"/api/studio/projects/{p['id']}").json()["spec"], "model": gen_model}, expect=422)
    assert r["detail"]["code"] == "no_model" and "not an editing model" in r["detail"]["message"]
    base = sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]
    for patch, code in (({"model": None}, "no_model"), ({"mode": None}, "no_mode")):
        assert gen(sm, p["id"], spec={**base, "model": oj_model(sm), **patch}, expect=422)["detail"]["code"] == code


def test_subject_text_is_required_and_box_and_points_only_when_the_engine_advertises_them(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), subject={"text": "", "kind": "person"}))
    assert gen(sm, p["id"], expect=422)["detail"]["code"] == "subject_missing"
    spec = make_spec(sm, clip, refs_of(pics, 1), subject={"text": "the singer", "kind": "person", "box": [0.1, 0.2, 0.3, 0.4]})
    out = gen(sm, p["id"], spec={"source_asset": clip["id"], **spec})
    i = engine_inputs()
    assert i["subject_box"] == [0.1, 0.2, 0.3, 0.4] and i["subject_kind"] == "person" and out["accounting"]["subject"]["sent"] == ["text", "box", "kind"]
    # the double does not advertise points: that is refused, naming what is unsupported, not dropped
    spec["subject"]["points"] = [{"x": 0.5, "y": 0.5, "positive": True}]
    r = gen(sm, p["id"], spec={"source_asset": clip["id"], **spec}, expect=422)
    assert r["detail"]["code"] == "subject_unsupported" and "Clicked points on the subject is not supported" in r["detail"]["message"]
    assert len(fs.STATE["profile_submissions"]) == 1


def test_a_box_is_refused_by_an_engine_that_does_not_advertise_it(app, st, sarness_url, tmp_path):
    api = boot(app, st, sarness_url, [profile_entry(box=False)])
    with api.c:
        clip = up(api, make_video(tmp_path, dur=3), "c.mp4")
        pic = up(api, png("red", d=tmp_path), "r.png")
        p = mkproject(api, clip["id"], {"mode": "subject_replacement", "model": oj_model(api), "subject": {"text": "the dancer", "kind": "person", "box": [0.1, 0.1, 0.5, 0.5]},
                                        "references": [{"asset": pic["id"], "active": True}]})
        r = gen(api, p["id"], expect=422)
        assert r["detail"]["code"] == "subject_unsupported" and "A box around the subject is not supported" in r["detail"]["message"] and fs.STATE["profile_submissions"] == []


def test_the_subject_kind_is_reported_not_silently_dropped_when_the_engine_has_no_field_for_it(app, st, sarness_url, tmp_path):
    entry = profile_entry()
    entry["inputs_schema"].pop("subject_kind")
    api = boot(app, st, sarness_url, [entry])
    with api.c:
        clip = up(api, make_video(tmp_path, dur=3), "c.mp4")
        pic = up(api, png("red", d=tmp_path), "r.png")
        p = mkproject(api, clip["id"], {"mode": "subject_replacement", "model": oj_model(api), "subject": {"text": "the dancer", "kind": "animal"}, "references": [{"asset": pic["id"], "active": True}]})
        out = gen(api, p["id"])
        assert "subject_kind" not in fs.STATE["profile_submissions"][0]["inputs"]
        assert out["accounting"]["subject"]["not_sent"][0]["field"] == "kind" and "no field" in out["accounting"]["subject"]["not_sent"][0]["reason"]


def test_a_source_longer_than_the_engine_takes_must_be_trimmed(sm, tmp_path, pics):
    long = up(sm, make_video(tmp_path, dur=40, fps=8, w=160, h=120), "long.mp4")
    p = mkproject(sm, long["id"], make_spec(sm, long, refs_of(pics, 1)))
    r = gen(sm, p["id"], expect=422)
    assert r["detail"]["code"] == "plan_errors" and "takes up to 30 s" in r["detail"]["message"] and "processing" in r["detail"]["plan"]
    spec = {**sm.get(f"/api/studio/projects/{p['id']}").json()["spec"], "trim": {"start": 5, "end": 15}}
    gen(sm, p["id"], spec=spec)
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["spec"]["trim"] == {"start": 5.0, "end": 15.0}          # what was on screen is now the project


def test_engine_settings_go_through_the_engines_own_schema(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), settings={"seed": 12, "hat_fix": True, "clip_seconds": 4}))
    gen(sm, p["id"])
    i = engine_inputs()
    assert i["seed"] == 12 and i["hat_fix"] is True and i["clip_seconds"] == 4
    q = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), settings={"turbo": True}))
    r = gen(sm, q["id"], expect=422)
    assert "Unknown setting: turbo" in r["detail"]["message"]
    q2 = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), settings={"clip_seconds": 99}))
    assert "between 2.5 and 15" in gen(sm, q2["id"], expect=422)["detail"]["message"]


def test_generate_prepares_the_video_itself_and_reuses_the_copy_next_time(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), trim={"start": 1, "end": 4}, frame={"aspect": "1:1"}))
    first = gen(sm, p["id"])
    n = len(sm.get("/api/media/assets", params={"origin": "prepared"}).json())
    again = gen(sm, p["id"])
    assert n == 1 and first["prepared_asset"]["id"] == again["prepared_asset"]["id"]
    assert len(sm.get("/api/media/assets", params={"origin": "prepared"}).json()) == 1


def test_a_slow_prepare_answers_202_and_the_second_call_finds_the_copy(sm, clip, pics, monkeypatch):
    from semechka import studio_svc
    monkeypatch.setattr(studio_svc, "PREPARE_WAIT_S", 0.0)
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), frame={"aspect": "1:1"}))
    r = sm.post(f"/api/studio/projects/{p['id']}/generate", json={})
    assert r.status_code == 202 and r.json()["code"] == "preparing" and r.json()["task"]
    assert wait_edit(sm, r.json()["task"])["status"] == "completed"
    assert fs.STATE["profile_submissions"] == []
    assert sm.post(f"/api/studio/projects/{p['id']}/generate", json={}).status_code == 200


def test_the_engine_refusing_the_job_is_a_plain_error_and_nothing_is_kept(sm, clip, pics):
    fs.STATE["profiles"][0]["readiness"].update(ready=False, state="not_ready", reason="the engine is busy")
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1)))
    r = sm.post(f"/api/studio/projects/{p['id']}/generate", json={})
    assert r.status_code in (409, 422, 502) and "not ready" in str(r.json())
    assert sm.get(f"/api/studio/projects/{p['id']}").json()["jobs"] == []


def test_other_people_cannot_generate_from_my_project_or_see_my_jobs(sm, app, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1)))
    out = gen(sm, p["id"])
    bob = make_user(sm, app, "bob@example.test")
    assert bob.post(f"/api/studio/projects/{p['id']}/generate", json={}).status_code == 404
    assert bob.get("/api/studio/jobs").json() == []
    assert bob.get(f"/api/studio/jobs/{out['job']}").status_code == 404
    assert bob.post(f"/api/studio/jobs/{out['job']}/export", json={"width": 640, "height": 480}).status_code == 404


# ---- export ----------------------------------------------------------------------------------------------------------------------
def finished_job(sm, clip, pics, **spec_kw):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1), **spec_kw))
    out = gen(sm, p["id"])
    fs.advance(100)
    j = sm.get(f"/api/media/jobs/{out['job']}").json()
    assert j["status"] == "completed", j
    return out["job"], j["assets"][0]


def do_export(sm, job, w, h, fit="scale", expect=200):
    r = sm.post(f"/api/studio/jobs/{job}/export", json={"width": w, "height": h, "fit": fit})
    assert r.status_code == expect, r.text
    return r.json()


def test_export_scales_the_result_and_says_so(sm, clip, pics, tmp_path):
    job, result = finished_job(sm, clip, pics)                                  # 320x240 source at the largest size not above "original" 240p -> 480p: 640x480
    assert (result["width"], result["height"]) == (640, 480)
    before = content(sm, result["id"])
    out = do_export(sm, job, 1280, 960)
    assert out["scaling"] == "upscale" and "no new detail" in out["note"] and out["native"] == {"width": 640, "height": 480}
    t = wait_edit(sm, out["task"])
    assert t["status"] == "completed"
    a = t["asset"]
    assert a["origin"] == "export" and (a["width"], a["height"]) == (1280, 960) and a["parent"] == result["id"]
    assert a["export"] == {"job": job, "project_id": a["export"]["project_id"], "requested": {"width": 1280, "height": 960, "fit": "scale"},
                           "native": {"width": 640, "height": 480}, "scaling": "upscale"}
    info = probe(content(sm, a["id"]), tmp_path)
    assert (info["w"], info["h"]) == (1280, 960) and info["acodec"] == "aac" and abs(info["vdur"] - 1.0) < 0.2
    assert content(sm, result["id"]) == before                                  # the engine's own result is untouched
    down = do_export(sm, job, 320, 240)
    assert down["scaling"] == "downscale" and "scaled down" in down["note"]
    wait_edit(sm, down["task"])
    row = sm.get("/api/studio/jobs").json()[0]
    assert sorted(e["scaling"] for e in row["exports"]) == ["downscale", "upscale"]


def test_export_with_bars_keeps_the_shape_and_a_wrong_shape_is_refused(sm, clip, pics, tmp_path):
    job, result = finished_job(sm, clip, pics)
    r = do_export(sm, job, 1080, 1920, expect=422)
    assert r["detail"]["code"] == "shape_differs" and "pad" in r["detail"]["message"]
    out = do_export(sm, job, 1080, 1920, fit="pad")
    assert out["scaling"] == "upscale"
    a = wait_edit(sm, out["task"])["asset"]
    info = probe(content(sm, a["id"]), tmp_path)
    assert (info["w"], info["h"]) == (1080, 1920) and info["acodec"] == "aac"
    for w, h, why in ((641, 480, "even"), (640, 480, "already 640×480"), (9000, 480, "between"), (32, 32, "between")):
        assert why in do_export(sm, job, w, h, expect=422)["detail"]["message"]
    assert do_export(sm, job, 800, 600, fit="stretch", expect=422)["detail"]["code"] == "invalid"


def test_export_waits_for_a_finished_result(sm, clip, pics):
    p = mkproject(sm, clip["id"], make_spec(sm, clip, refs_of(pics, 1)))
    out = gen(sm, p["id"])
    r = do_export(sm, out["job"], 1280, 960, expect=409)
    assert r["detail"]["code"] == "not_finished"
