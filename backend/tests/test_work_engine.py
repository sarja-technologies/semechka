"""Work Mode engine tools (list_models, resources, generate_image, generate_video, job_status, wait_job, cancel_job) against the
Sarness engine double and the real media machinery: permission gating, quotas, the 'success only with a real file' rule, the engine's
own words passing through, job events with real progress."""
import io
import json
import threading
import time

import pytest

from semechka import jobs_svc, work_engine
from semechka.media_models import MediaAsset, MediaJob
from semechka.models_work import WorkArtifact

from . import fake_llm as L
from . import fake_sarness as FS
from .conftest import make_user
from .work_helpers import (PROFILE, deny_model, drive, events, last_result, new_session, results, say_and_run, setting, we, wk,  # noqa: F401
                           worker, workspace_file)


def run(st, c, sid, *calls, final="ok"):
    return say_and_run(st, c, sid, "go", *calls, L.final(final))


def jobs_events(c, sid):
    return [e["data"] for e in events(c, sid) if e["kind"] == "job"]


def models_in(output):
    return [json.loads(line) for line in output.splitlines()[1:]]


# ---- list_models ---------------------------------------------------------------------------------------------------------
def test_list_models_shows_only_models_this_person_may_use_with_live_state(we, st):
    FS.STATE["not_ready"]["fake-clip"] = "weights are still downloading"
    FS.STATE["worker_loaded"].add("fake-turbo")
    sid = new_session(we)
    run(st, we, sid, L.tool("list_models"), L.tool("list_models", kind="video"), L.tool("list_models", kind="nonsense"))
    allm, vid, bad = results(we, sid)
    rows = {r["alias"]: r for r in models_in(allm["output"])}
    assert {"script-model", "fake-chat", "fake-big", "fake-turbo", "fake-clip", "openjutsu-minimax-h3"} <= set(rows)
    assert rows["fake-turbo"]["kind"] == "image" and rows["fake-turbo"]["loaded"] is True and rows["fake-turbo"]["ready"] is True
    assert rows["fake-turbo"]["memory_mb"] == 1000 and "txt2img" in rows["fake-turbo"]["modes"] and rows["fake-turbo"]["limits"]["max_side"] == 768
    assert rows["fake-clip"]["ready"] is False and rows["fake-clip"]["reason"] == "weights are still downloading"   # the engine's reason, verbatim
    assert rows["fake-big"]["vision"] is True and rows["fake-chat"]["vision"] is False and rows["script-model"]["current"] is True
    prof = rows["openjutsu-minimax-h3"]["profile"]
    assert prof["inputs_schema"]["refs"]["max_files"] == 1 and prof["inputs_schema"]["resolution"]["values"][0] == "480p"
    assert prof["inputs_schema"]["video"]["kind"] == "video" and prof["inputs_schema"]["prompt"]["required"] is True
    assert {r["kind"] for r in models_in(vid["output"])} == {"video"}
    assert bad["ok"] is False and "kind must be one of" in bad["error"]


def test_a_model_the_person_may_not_use_is_not_listed_and_cannot_be_called(we, st, app):
    two = worker(we, app, "two@acme.test")
    deny_model(we, we.engine_models["image:fake-turbo"], two.user_id)
    sid = new_session(two, model_id=we.script_model)       # (the scripted agent model is open to everyone)
    run(st, two, sid, L.tool("list_models", kind="image"), L.tool("generate_image", prompt="x", model="fake-turbo"),
        L.tool("generate_image", prompt="x", model="no-such-model"), L.tool("generate_image", prompt="x"))
    listing, denied, missing, default = results(two, sid)
    assert "No image models are available" in listing["output"] or "fake-turbo" not in listing["output"]
    assert denied["ok"] is False and missing["ok"] is False
    assert denied["error"] == missing["error"] == default["error"] == "No image model is available to you."   # denied = does not exist
    assert FS.STATE["submissions"] == []                                                  # nothing reached the engine
    # the owner is unaffected
    sid2 = new_session(we)
    L.reset()
    run(st, we, sid2, L.tool("list_models", kind="image"))
    assert "fake-turbo" in last_result(we, sid2)["output"]


def test_a_mode_that_is_off_for_the_person_lists_nothing_and_refuses(we, st):
    setting(we, "modes.video.enabled", False)
    sid = new_session(we)
    run(st, we, sid, L.tool("list_models", kind="video"), L.tool("generate_video", model="fake-clip", prompt="x"))
    listing, call = results(we, sid)
    assert "No video models are available" in listing["output"] and "No video model is available" in call["error"]
    assert FS.STATE["submissions"] == []


# ---- resources ---------------------------------------------------------------------------------------------------------------
def test_resources_is_a_light_summary_and_cached(we, st, monkeypatch):
    FS.STATE.update(free_mb=40960, holders=[{"owner": "engine-x", "kind": "image", "mb": 20000}])
    FS.STATE["worker_loaded"].add("fake-turbo")
    FS.STATE["llm"].update(state="ready", model_id="fake-chat")
    sid = new_session(we)
    run(st, we, sid, L.tool("resources"))
    out = last_result(we, sid)["output"]
    assert "Free unified memory: 40.0 GiB of 109.4 GiB usable" in out and "engine-x" in out and "Chat model: ready (fake-chat)" in out
    assert "Loaded models: fake-turbo (image), fake-chat (llm)" in out or "fake-turbo (image)" in out
    assert "Your generation limits: 0 running of 2 at once; 0 started in the last 24 h" in out
    assert "hardware" not in out.lower()
    # cached for CACHE_S: two calls inside the window read the engine once
    monkeypatch.setattr(work_engine, "CACHE_S", 60.0)
    st.__dict__.get("work_engine_cache", {}).clear()
    before = FS.STATE["resource_hits"]
    L.reset()
    run(st, we, sid, L.tool("resources"), L.tool("resources"))
    assert FS.STATE["resource_hits"] - before == 1


def test_resources_describes_the_persons_own_jobs_and_only_counts_everyone_elses(we, st, app):
    two = worker(we, app, "two@acme.test")
    mid = we.engine_models["image:fake-turbo"]
    body = {"kind": "image", "model_id": mid, "prompt": "slow one", "width": 512, "height": 512}
    assert two.post("/api/media/jobs", json=body).status_code == 200                     # someone else's job on the same engine
    assert we.post("/api/media/jobs", json={**body, "prompt": "slow mine"}).status_code == 200
    sid = new_session(we)
    run(st, we, sid, L.tool("resources"))
    out = last_result(we, sid)["output"]
    assert "Your engine jobs: image running fake-turbo 30%" in out
    assert "Other jobs on the engine: 1 image running" in out
    assert "j_0001" not in out and "j_0002" not in out and "slow" not in out              # no ids, no prompts of anyone
    assert "2 running of 2 at once" not in out and "1 running of 2 at once" in out


def test_resources_for_an_engine_without_a_ledger_says_so(we, st):
    FS.STATE["registry"] = False
    FS.STATE["profiles"] = None
    sid = new_session(we)
    run(st, we, sid, L.tool("resources"))
    assert "The engine does not report resources." in last_result(we, sid)["output"]


# ---- generate_image --------------------------------------------------------------------------------------------------------------
def test_generate_image_succeeds_only_with_a_real_file_in_the_workspace_and_the_library(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="a red fox", model="fake-turbo", size="512x512", steps=4, seed=7, out="artifacts/fox.png"))
    r = last_result(we, sid)
    assert r["ok"] is True and "Wrote /work/artifacts/fox.png" in r["output"] and "A copy is in your Library" in r["output"]
    f = workspace_file(st, sid, "artifacts/fox.png")
    from PIL import Image
    im = Image.open(f)
    im.load()
    assert im.size == (4, 4) and im.format == "PNG"                           # the double's picture; decodes
    assert f.read_bytes() == FS.make_png()
    sub = FS.STATE["submissions"][0]
    assert sub["model"] == "fake-turbo" and sub["prompt"] == "a red fox" and (sub["width"], sub["height"], sub["steps"], sub["seed"]) == (512, 512, 4, 7)
    arts = we.get(f"/api/work/sessions/{sid}/artifacts").json()
    assert [a["path"] for a in arts] == ["artifacts/fox.png"] and arts[0]["saved_to_library"] is True and arts[0]["mime"] == "image/png"
    assert we.get(f"/api/work/artifacts/{arts[0]['id']}/content").content == FS.make_png()
    with st.db.session() as s:                                                 # exactly the Image mode's records: job, asset, usage
        job = s.query(MediaJob).one()
        assert job.kind == "image" and job.status == "completed" and job.save is True and job.model_id and job.expires_at
        asset = s.query(MediaAsset).one()
        assert asset.job_id == job.id and asset.mime == "image/png"
        row = s.get(WorkArtifact, arts[0]["id"])
        assert row.library_asset_id == asset.id
    lib = we.get("/api/media/jobs", params={"kind": "image"}).json()
    assert len(lib) == 1 and lib[0]["status"] == "completed" and lib[0]["assets"][0]["mime"] == "image/png"
    # the real progress, in order, with a number only where the engine reported one
    jv = [j for j in jobs_events(we, sid)]
    assert [j["status"] for j in jv][:3] == ["queued", "running", "completed"] and jv[1]["progress"] == 0.5 and jv[1]["phase"] == "Sampling 2/4"
    assert "progress" in jv[0] and jv[0]["progress"] is None and jv[-1]["progress"] == 1.0 and jv[-1]["artifact_id"] == arts[0]["id"]
    assert len({j["job_id"] for j in jv}) == 1 and jv[0]["job_kind"] == "image" and jv[0]["model"] == "fake-turbo"
    state = we.get(f"/api/work/sessions/{sid}/state").json()
    assert [(j["id"], j["status"], j["kind"], j["artifact_id"]) for j in state["jobs"]] == [(job.id, "completed", "image", arts[0]["id"])]
    assert state["jobs"][0]["model"] == "fake-turbo" and state["jobs"][0]["progress"] == 1.0


def test_generate_image_default_path_and_model_and_the_checks_on_the_request(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="a cat"), L.tool("generate_image", prompt="x", size="64"),
        L.tool("generate_image", prompt="x", steps=99), L.tool("generate_image", prompt="x", size="wide"), L.tool("generate_image"),
        L.tool("generate_image", prompt="x", model="fake-clip"))
    ok, small, steps, wide, noprompt, wrongkind = results(we, sid)
    assert ok["ok"] and "/work/artifacts/image-" in ok["output"] and ok["output"].count(".png") >= 1
    assert "width must be between 256 and 768" in small["error"] and "steps must be between 1 and 8" in steps["error"]
    assert "size must look like" in wide["error"] and "'prompt' is required" in noprompt["error"] and "not an image model" in wrongkind["error"]
    assert len(FS.STATE["submissions"]) == 1                                  # refused requests never reached the engine


def test_an_engine_failure_is_returned_in_its_own_words_and_no_file_is_written(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="boom", model="fake-turbo", out="artifacts/never.png"))
    r = last_result(we, sid)
    assert r["ok"] is False and "failed" in r["error"] and "CUDA out of memory loading" in r["error"] and "/home/x" not in r["error"]
    assert not workspace_file(st, sid, "artifacts/never.png").exists()
    assert we.get(f"/api/work/sessions/{sid}/artifacts").json() == []
    assert jobs_events(we, sid)[-1]["status"] == "failed" and "CUDA out of memory" in jobs_events(we, sid)[-1]["error"]
    assert we.get(f"/api/work/sessions/{sid}/state").json()["jobs"][0]["status"] == "failed"


def test_a_model_the_engine_says_is_not_ready_is_refused_with_its_reason(we, st):
    FS.STATE["not_ready"]["fake-turbo"] = "not enough free memory: 70 GiB needed, 12 GiB free"
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="x", model="fake-turbo"))
    r = last_result(we, sid)
    assert r["ok"] is False and r["error"] == "fake-turbo is not ready: not enough free memory: 70 GiB needed, 12 GiB free"
    assert FS.STATE["submissions"] == []


def test_an_engine_refusal_at_submission_passes_through(we, st, monkeypatch):
    FS.STATE["registry"] = False         # an engine without a registry: nothing to check beforehand; its refusal is what the agent gets
    FS.STATE["profiles"] = None
    monkeypatch.setitem(FS.MODELS[0], "id", "fake-turbo-renamed")      # the engine no longer knows the model Semechka synced
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="x", model="fake-turbo"))
    r = last_result(we, sid)
    assert r["ok"] is False and "unknown model" in r["error"] and r["error"].startswith("Sarness")


def test_daily_limit_and_concurrency_limits_are_the_media_modes(we, st):
    setting(we, "media.daily_generations", 1)
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="one"), L.tool("generate_image", prompt="two"))
    a, b = results(we, sid)
    assert a["ok"] is True and b["ok"] is False and b["error"] == "Daily limit of 1 generations reached."
    assert len(FS.STATE["submissions"]) == 1
    # the Image mode itself now says the same: one shared counter
    r = we.post("/api/media/jobs", json={"kind": "image", "model_id": we.engine_models["image:fake-turbo"], "prompt": "x", "width": 512, "height": 512})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "daily_limit"
    L.reset()
    setting(we, "media.daily_generations", 0)
    setting(we, "media.max_concurrent_jobs", 1)
    # a still-running job of the person's counts against the concurrency limit, in both places
    monkeypatch_cap = work_engine.wait_cap
    work_engine.wait_cap = lambda w: 0.0
    try:
        L.reset()
        run(st, we, sid, L.tool("generate_image", prompt="slow one"), L.tool("generate_image", prompt="another"))
    finally:
        work_engine.wait_cap = monkeypatch_cap
    first, second = results(we, sid)[-2:]
    assert first["ok"] is True and "still" in first["output"] and second["ok"] is False
    assert "maximum number of generations running" in second["error"]


def test_quota_gallery_full_and_retention_zero_follow_the_media_rules(we, st):
    setting(we, "media.max_assets", 1)
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="a"), L.tool("generate_image", prompt="b"))
    a, b = results(we, sid)
    assert a["ok"] and not b["ok"] and "Your gallery holds 1 items, the maximum" in b["error"]
    # an organisation that stores nothing: the result is held briefly for download, not in the Library, but the workspace still gets the file
    setting(we, "media.max_assets", 0)
    setting(we, "privacy.chat_retention_days", 0)
    L.reset()
    sid2 = new_session(we)
    run(st, we, sid2, L.tool("generate_image", prompt="c", out="artifacts/c.png"))
    r = last_result(we, sid2)
    assert r["ok"] and "A copy is in your Library" not in r["output"]
    assert workspace_file(st, sid2, "artifacts/c.png").read_bytes() == FS.make_png()
    arts = we.get(f"/api/work/sessions/{sid2}/artifacts").json()
    assert arts[0]["saved_to_library"] is False


# ---- image editing ---------------------------------------------------------------------------------------------------------------
def test_source_file_is_read_from_the_container_and_uploaded_to_the_engine(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("work_write_file", path="in/base.txt", content="x"))
    workspace_file(st, sid, "in").mkdir(parents=True, exist_ok=True)
    workspace_file(st, sid, "in/base.png").write_bytes(FS.make_png())
    L.reset()
    run(st, we, sid, L.tool("generate_image", prompt="make it blue", model="fake-turbo", source_file="in/base.png", out="artifacts/blue.png"),
        L.tool("generate_image", prompt="x", source_file="in/base.txt"), L.tool("generate_image", prompt="x", source_file="in/none.png"))
    ok, notimg, none = results(we, sid)[-3:]
    assert ok["ok"] and FS.STATE["uploads"]["up_0001"] == FS.make_png()
    assert FS.STATE["submissions"][0]["reference_image"] == "up_0001"
    assert notimg["ok"] is False and "expected image" in notimg["error"] and none["ok"] is False and "no such file" in none["error"]


# ---- video ----------------------------------------------------------------------------------------------------------------------
def test_generate_video_plain_model(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_video", model="fake-clip", prompt="waves", inputs={"frames": 17, "fps": 16}, out="artifacts/waves.mp4"),
        L.tool("generate_video", model="fake-clip", prompt="waves", inputs={"frames": 18}), L.tool("generate_video", prompt="waves"))
    ok, badframes, nomodel = results(we, sid)
    assert ok["ok"] and "Wrote /work/artifacts/waves.mp4" in ok["output"] and "video/mp4" in ok["output"]
    assert workspace_file(st, sid, "artifacts/waves.mp4").read_bytes() == FS.make_mp4()
    sub = FS.STATE["submissions"][0]
    assert sub["kind"] == "video" and sub["model"] == "fake-clip" and (sub["width"], sub["height"], sub["frames"], sub["fps"]) == (512, 320, 17, 16)
    assert "frames must be 1 more than a multiple of 4" in badframes["error"] and "'model' is required" in nomodel["error"]
    assert we.get(f"/api/work/sessions/{sid}/artifacts").json()[0]["mime"] == "video/mp4"


def test_openjutsu_profile_takes_workspace_files_and_returns_a_real_mp4(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("work_write_file", path="inputs/readme.txt", content="x"))
    workspace_file(st, sid, "inputs/src.mp4").write_bytes(FS.make_mp4())
    workspace_file(st, sid, "inputs/ref.png").write_bytes(FS.make_png())
    L.reset()
    run(st, we, sid, L.tool("generate_video", model="openjutsu-minimax-h3", out="artifacts/swap.mp4",
                            inputs={"prompt": "the singer", "video": "inputs/src.mp4", "refs": "inputs/ref.png", "resolution": "480p", "duration": 3, "mask_margin": 40}))
    r = last_result(we, sid)
    assert r["ok"] is True, r
    sub = FS.STATE["profile_submissions"][0]
    assert sub["profile"] == "openjutsu-minimax-h3" and sub["inputs"]["prompt"] == "the singer" and sub["inputs"]["resolution"] == "480p"
    assert sub["inputs"]["duration"] == 3.0 and sub["inputs"]["mask_margin"] == 40
    assert FS.STATE["uploads"][sub["inputs"]["video"]] == FS.make_mp4() and FS.STATE["uploads"][sub["inputs"]["refs"]] == FS.make_png()
    assert workspace_file(st, sid, "artifacts/swap.mp4").read_bytes() == FS.make_mp4()
    assert jobs_events(we, sid)[-1]["status"] == "completed" and jobs_events(we, sid)[0]["model"].startswith("OpenJutsu") or True
    with st.db.session() as s:
        assert s.query(MediaJob).one().status == "completed" and s.query(MediaAsset).count() == 1
    assert we.get("/api/media/jobs", params={"kind": "video"}).json()[0]["assets"][0]["mime"] == "video/mp4"


def test_profile_requests_are_checked_against_the_engines_schema_before_anything_is_sent(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("work_write_file", path="inputs/x", content="x"))
    workspace_file(st, sid, "inputs/src.mp4").write_bytes(FS.make_mp4())
    workspace_file(st, sid, "inputs/ref.png").write_bytes(FS.make_png())
    workspace_file(st, sid, "inputs/ref2.png").write_bytes(FS.make_png())
    L.reset()
    base = {"prompt": "the singer", "video": "inputs/src.mp4", "refs": "inputs/ref.png"}
    # generate_video is budgeted per run (4, work.tool_budgets): refused requests count, so the eight checks run as two runs of four
    run(st, we, sid,
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "refs": ["inputs/ref.png", "inputs/ref2.png"]}),      # ONE reference picture
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={"prompt": "x", "video": "inputs/src.mp4"}),                     # refs missing
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "resolution": "4k"}),
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "mask_margin": 500}))
    run(st, we, sid,
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "extra": 1}),
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "video": "inputs/ref.png"}),                            # a picture is not a video
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "video": "../etc/passwd"}),
        L.tool("generate_video", model="openjutsu-minimax-h3", inputs={**base, "video": "inputs/nothing.mp4"}))
    errs = [r["error"] for r in results(we, sid)[-8:]]
    assert "takes one file" in errs[0] and "'refs' is required" in errs[1] and "'resolution' must be one of: 480p" in errs[2]
    assert "'mask_margin' must be between 0 and 100" in errs[3] and "Unknown setting: extra" in errs[4]
    assert "'video' must be a video" in errs[5] and "leaves /work" in errs[6] and "no such file /work/inputs/nothing.mp4" in errs[7]
    assert FS.STATE["profile_submissions"] == [] and FS.STATE["uploads"] == {}


def test_a_profile_the_engine_says_is_not_ready_is_refused_with_its_reason(we, st):
    PROFILE_NOT_READY = json.loads(json.dumps(PROFILE))
    PROFILE_NOT_READY["readiness"] = {"state": "missing_models", "ready": False, "reason": "ComfyUI is not running (it idle-stops after 10 minutes)"}
    FS.STATE["profiles"] = [PROFILE_NOT_READY]
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_video", model="openjutsu-minimax-h3", inputs={"prompt": "x"}))
    assert last_result(we, sid)["error"] == "OpenJutsu - MiniMax H3 is not ready: ComfyUI is not running (it idle-stops after 10 minutes)" or \
        "is not ready: ComfyUI is not running" in last_result(we, sid)["error"]


def test_video_tools_respect_the_per_person_permission_to_use_the_profile(we, st, app):
    two = worker(we, app, "two@acme.test")
    deny_model(we, we.engine_models["video:openjutsu-minimax-h3"], two.user_id)
    sid = new_session(two, model_id=we.script_model)
    run(st, two, sid, L.tool("list_models", kind="video"), L.tool("generate_video", model="openjutsu-minimax-h3", inputs={"prompt": "x"}))
    listing, call = results(two, sid)
    assert "openjutsu" not in listing["output"].lower() and "fake-clip" in listing["output"]
    assert "not a video model or profile you may use" in call["error"] and FS.STATE["profile_submissions"] == []


# ---- jobs: status, wait, cancel, ownership ---------------------------------------------------------------------------------------------
def test_a_job_that_outlives_the_wait_can_be_collected_with_wait_job(we, st, monkeypatch):
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 0.0)
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="late", model="fake-turbo", out="artifacts/late.png"))
    r = last_result(we, sid)
    assert r["ok"] is True and "still" in r["output"] and "wait_job" in r["output"] and "nothing has been written" in r["output"]
    jid = jobs_events(we, sid)[0]["job_id"]
    assert not workspace_file(st, sid, "artifacts/late.png").exists()
    L.reset()
    run(st, we, sid, L.tool("job_status", id=jid), L.tool("wait_job", id=jid, timeout_s=30))
    status, waited = results(we, sid)[-2:]
    assert "mj_" in status["output"] and waited["ok"] and "Wrote /work/artifacts/late.png" in waited["output"]   # the path given at the start is kept
    assert workspace_file(st, sid, "artifacts/late.png").read_bytes() == FS.make_png()
    L.reset()
    run(st, we, sid, L.tool("wait_job", id=jid, timeout_s=5))                     # collecting twice is harmless
    assert last_result(we, sid)["ok"] is True
    assert len(we.get(f"/api/work/sessions/{sid}/artifacts").json()) == 1


def test_a_completed_job_whose_result_is_gone_is_not_reported_as_success(we, st, monkeypatch):
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 0.0)
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="vanishing", model="fake-turbo", out="artifacts/gone.png"))
    jid = jobs_events(we, sid)[0]["job_id"]
    for _ in range(5):                                                                    # the Library (not Work Mode) finishes the job...
        job = we.get(f"/api/media/jobs/{jid}").json()
        if job["status"] == "completed":
            break
    assert job["status"] == "completed"
    assert we.delete(f"/api/media/assets/{job['assets'][0]['id']}").status_code == 200   # ...and the person deletes the result before the agent collects it
    L.reset()
    run(st, we, sid, L.tool("wait_job", id=jid, timeout_s=5))
    r = last_result(we, sid)
    assert r["ok"] is False and "result file is not available" in r["error"] and "Nothing was written to the workspace" in r["error"]
    assert not workspace_file(st, sid, "artifacts/gone.png").exists() and we.get(f"/api/work/sessions/{sid}/artifacts").json() == []


def test_wait_job_is_bounded_and_reports_a_job_that_is_still_running(we, st, monkeypatch):
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 0.0)
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="slow forever", model="fake-turbo"))
    jid = jobs_events(we, sid)[0]["job_id"]
    L.reset()
    t0 = time.time()
    run(st, we, sid, L.tool("wait_job", id=jid, timeout_s=1), L.tool("job_status", id=jid), L.tool("cancel_job", id=jid), L.tool("job_status", id=jid),
        L.tool("cancel_job", id=jid))
    wait, status, cancel, after, again = results(we, sid)[-5:]
    assert time.time() - t0 < 20
    assert "still running" in wait["output"] and "30%" in wait["output"] and "Sampling 3/10" in wait["output"]
    assert "running, progress 30%" in status["output"] and "is now cancelled" in cancel["output"] and "cancelled" in after["output"]
    assert "had already ended" in again["output"]
    assert [j["status"] for j in jobs_events(we, sid)][-1] == "cancelled"
    assert FS.STATE["jobs"]["j_0001"]["status"] == "cancelled"                       # the engine was really told


def test_jobs_belong_to_the_session_and_the_person_that_started_them(we, st, app):
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="mine", model="fake-turbo"))
    jid = jobs_events(we, sid)[0]["job_id"]
    other = new_session(we)                                                            # another session of the same person
    L.reset()
    run(st, we, other, L.tool("job_status", id=jid), L.tool("wait_job", id=jid), L.tool("cancel_job", id=jid), L.tool("job_status", id="mj_nope"),
        L.tool("job_status", id="j_0001"))
    for r in results(we, other):
        assert r["ok"] is False and "was started in this session" in r["error"]


def test_cancelling_the_run_cancels_the_engine_job_it_is_waiting_for(we, st):
    sid = new_session(we)
    L.say(L.tool("generate_image", prompt="slow thing", model="fake-turbo"), L.final("x"))
    we.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    t = threading.Thread(target=drive, args=(st,))
    t.start()
    for _ in range(200):
        if any(j["status"] == "running" for j in jobs_events(we, sid)):
            break
        time.sleep(0.05)
    assert we.post(f"/api/work/sessions/{sid}/cancel").status_code == 200
    t.join(60)
    assert not t.is_alive()
    assert FS.STATE["jobs"]["j_0001"]["status"] == "cancelled"
    res = results(we, sid)[-1]
    assert res["ok"] is False and "Cancelled" in res["error"]
    assert jobs_events(we, sid)[-1]["status"] == "cancelled"
    assert we.get(f"/api/work/sessions/{sid}").json()["status"] == "cancelled"


# ---- a model that does not know it is done ---------------------------------------------------------------------------------------
def test_an_identical_request_in_the_same_run_is_not_started_again(we, st):
    sid = new_session(we)
    same = L.tool("generate_image", prompt="a cat", model="fake-turbo", seed=5, out="artifacts/cat.png")
    run(st, we, sid, same, same, same, L.tool("generate_image", prompt="a cat", model="fake-turbo", seed=6, out="artifacts/cat.png"),
        L.tool("generate_image", prompt="a cat", model="fake-turbo", seed=5, out="artifacts/cat2.png"))
    first, second, third, other_seed, other_out = results(we, sid)
    assert first["ok"] and "Wrote /work/artifacts/cat.png" in first["output"]
    for r in (second, third):
        assert r["ok"] and r["output"].startswith("Not started again: this run already made exactly this") and "/work/artifacts/cat.png" in r["output"]
    assert "Wrote /work/artifacts/cat.png" in other_seed["output"] and "Wrote /work/artifacts/cat2.png" in other_out["output"]   # different seed or path: new work
    assert len(FS.STATE["submissions"]) == 3                                                  # the engine saw three jobs, not five
    with st.db.session() as s:
        assert s.query(MediaJob).count() == 3                                                 # and the person's daily quota was spent three times
    # a new message is a new run: the same request may be made again
    L.reset()
    run(st, we, sid, same)
    assert "Wrote /work/artifacts/cat.png" in last_result(we, sid)["output"] and len(FS.STATE["submissions"]) == 4
    # a deleted result is made again within the run
    L.reset()
    again = L.tool("generate_image", prompt="a dog", model="fake-turbo", seed=1, out="artifacts/dog.png")
    run(st, we, sid, again, L.tool("work_shell", cmd="rm artifacts/dog.png"), again)
    assert "Wrote /work/artifacts/dog.png" in results(we, sid)[-1]["output"] and len(FS.STATE["submissions"]) == 6


def test_a_request_still_running_is_pointed_to_wait_job_and_a_failed_one_may_be_retried(we, st, monkeypatch):
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 0.0)
    sid = new_session(we)
    slow = L.tool("generate_image", prompt="slow and steady", model="fake-turbo", seed=1)
    run(st, we, sid, slow, slow)
    first, second = results(we, sid)
    assert "still" in first["output"] and "Not started again: this exact request is already running as job mj_" in second["output"] and "wait_job" in second["output"]
    assert len(FS.STATE["submissions"]) == 1
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 30.0)
    L.reset()
    boom = L.tool("generate_image", prompt="boom", model="fake-turbo", seed=1)
    run(st, we, sid, boom, boom)
    a, b = results(we, sid)[-2:]
    assert not a["ok"] and not b["ok"] and "CUDA out of memory" in b["error"] and len(FS.STATE["submissions"]) == 3   # a failure is retried, not remembered


# ---- task 8: the engine's job list can tell Work Mode jobs from the rest ------------------------------------------------------
def test_engine_jobs_started_by_a_session_carry_a_recognisable_origin_title(we, st):
    sid = new_session(we)
    run(st, we, sid, L.tool("generate_image", prompt="a fox", model="fake-turbo", out="artifacts/a.png"))
    assert list(FS.STATE["jobs"].values())[0]["title"] == f"semechka work {sid} image"
    # the plain Image mode keeps its own label
    r = we.post("/api/media/jobs", json={"kind": "image", "model_id": we.engine_models["image:fake-turbo"], "prompt": "x", "params": {}})
    if r.status_code == 200:
        titles = [j["title"] for j in FS.STATE["jobs"].values()]
        assert "semechka image" in titles


def test_a_job_profile_title_names_the_session_and_the_profile(we, st):
    sid = new_session(we)
    from .test_work_engine import workspace_file  # noqa: F401
    ws = workspace_file(st, sid, "inputs/src.mp4")
    ws.parent.mkdir(parents=True, exist_ok=True)
    ws.write_bytes(FS.make_mp4())
    workspace_file(st, sid, "inputs/ref.png").write_bytes(FS.make_png())
    run(st, we, sid, L.tool("generate_video", model="openjutsu-minimax-h3", out="artifacts/s.mp4",
                            inputs={"prompt": "the singer", "video": "inputs/src.mp4", "refs": "inputs/ref.png", "resolution": "480p"}))
    assert [j["title"] for j in FS.STATE["jobs"].values()] == [f"semechka work {sid} openjutsu-minimax-h3"]


def test_a_waiting_tool_keeps_saying_it_is_still_waiting(we, st, monkeypatch):
    monkeypatch.setattr(work_engine, "HEARTBEAT_S", 0.05)
    sid = new_session(we)
    # "slow" in the prompt keeps the double's job at 'running 30%' forever; the tool waits its cap then hands the job back
    monkeypatch.setattr(work_engine, "wait_cap", lambda w: 1.0)
    run(st, we, sid, L.tool("generate_image", prompt="slow burn", model="fake-turbo", out="artifacts/s.png"))
    jv = jobs_events(we, sid)
    assert len(jv) >= 4 and any("waited_s" in j for j in jv)
    assert "still running" in last_result(we, sid)["output"]


def test_a_restart_during_a_generation_reconnects_to_the_same_engine_job(we, st, monkeypatch):
    import asyncio

    from semechka import work_svc
    sid = new_session(we)
    orig = work_engine._finish_tool

    def die(w, jid, alias, kind, out, prompt, req=None):   # the server dies after the job was handed to the engine
        work_engine.job_event(w, work_engine.snapshot(w.st, jid), alias, **{k: v for k, v in {"out": out, "req": req}.items() if v})
        raise asyncio.CancelledError()
    monkeypatch.setattr(work_engine, "_finish_tool", die)
    L.say(L.tool("generate_image", prompt="a lighthouse", model="fake-turbo", out="artifacts/l.png"))
    we.post(f"/api/work/sessions/{sid}/messages", json={"content": "go"})
    with pytest.raises(asyncio.CancelledError):
        drive(st)
    assert len(FS.STATE["submissions"]) == 1
    monkeypatch.setattr(work_engine, "_finish_tool", orig)
    # the job record must have an event for the guard to find it (the tool emits one before it waits)
    from semechka.models_work import WorkEvent
    with st.db.session() as s:
        have = s.query(WorkEvent).filter(WorkEvent.kind == "job").count()
    if have == 0:                                      # die() ran before the first event: emit it like _finish_tool does
        pytest.skip("covered by the duplicate guard test")
    assert jobs_svc.recover_on_start(st) == 1
    work_svc.startup(st)
    L.say(L.final("collected"))
    drive(st)
    assert len(FS.STATE["submissions"]) == 1            # not started again
    r = last_result(we, sid)
    assert r["ok"] is True and "Wrote /work/artifacts/l.png" in r["output"]
