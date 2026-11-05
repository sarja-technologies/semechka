"""OpenJutsu Studio, part 4: the live inspector. The engine double runs a scripted OpenJutsu job (queued, loading, tracking, scenes with sampler progress
and Loop End events, assembling, output ready) that the test steps one tick at a time, so every state can be looked at. Also: failure and cancel
variants, an engine that hiccups or forgot the trace, the event stream and its reconnect, and that nobody sees another person's job."""
import json
import threading
import time

import pytest

from semechka import studio_inspect

from . import fake_sarness as fs
from .conftest import make_user
from .studio_helpers import (
    make_video,
    mkproject,
    oj_model,
    png,
    probe,
    pytestmark_ffmpeg,
    up,
)

pytestmark = pytestmark_ffmpeg


@pytest.fixture
def run(sm, tmp_path):
    """A started Studio job (3 scenes) -> (job id, helpers). Nothing has happened in the engine yet (tick 0)."""
    fs.configure(scenes=3)
    clip = up(sm, make_video(tmp_path, dur=6, fps=24, gop=24), "clip.mp4")
    pic = up(sm, png("red", d=tmp_path), "red.png")
    p = mkproject(sm, clip["id"], {"mode": "subject_replacement", "model": oj_model(sm), "subject": {"text": "the dancer", "kind": "person"},
                                   "references": [{"asset": pic["id"], "active": True, "label": "Anna"}]})
    r = sm.post(f"/api/studio/projects/{p['id']}/generate", json={})
    assert r.status_code == 200, r.text
    return r.json()["job"]


def doc(api, jid, after=0, expect=200):
    r = api.get(f"/api/studio/jobs/{jid}/inspect", params={"after": after})
    assert r.status_code == expect, r.text
    return r.json()


def test_states_advance_with_the_engine_and_nothing_is_invented(sm, run):
    d = doc(sm, run)
    assert d["job_id"] == run and d["status"] == "queued" and d["stage"] == "queued" and d["progress"] == {"fraction": None, "indeterminate": True, "basis": None}
    assert d["user_status"] == "queued" and d["trace_available"] is True and d["stale"] is False and d["comfy"] == {"available": True}
    assert d["scenes"]["total"] == 3 and d["scenes"]["segments"] == [] and d["output"]["state"] == "none" and d["loop"]["iterations_ended"] == 0
    seen = []
    cursor = 0
    stages = [d["stage"]]
    for tick in range(1, 30):
        fs.advance(1)
        d = doc(sm, run, cursor)
        for e in d["events"]:
            seen.append(e["seq"])
        assert d["next_seq"] >= cursor
        cursor = d["next_seq"]
        if d["stage"] != stages[-1]:
            stages.append(d["stage"])
        done = d["status"] == "completed"
        assert done == (d["output"]["state"] == "ready")                           # completed ONLY when the output is ready
        if d["scenes"]["completed"] is not None and d["scenes"]["completed"] < 3:
            assert d["status"] != "completed"                                         # a Loop End is not the end of the job
        assert d["loop"]["iterations_ended"] == d["scenes"]["completed"]
        if done:
            break
    assert stages == ["queued", "starting_backend", "loading_models", "tracking_subject", "generating_scene", "assembling", "saving_output", "done"]
    assert seen == sorted(set(seen)) and seen == list(range(1, len(seen) + 1))     # every event exactly once, in order, following the cursor
    assert d["status"] == "completed" and d["user_status"] == "completed" and d["progress"] == {"fraction": 1.0, "indeterminate": False, "basis": "output_ready"}
    assert d["output"]["file"]["has_audio"] is True and d["output"]["file"]["width"] % 32 == 0
    assert d["nodes"]["failed"] == 0 and d["nodes"]["done"] == d["nodes"]["total"]
    assert sm.get(f"/api/media/jobs/{run}").json()["assets"]                          # Semechka collected the result as well


def test_the_sampler_progress_and_scenes_are_the_engines_own(sm, run):
    fs.advance(5)                                                                    # tracking finished, scene 1 started: sampler at 3/6
    d = doc(sm, run)
    assert d["stage"] == "generating_scene" and d["stage_label"] == "Generating scene 1 of 3"
    assert d["sampling"] == {"node_id": "73", "title": "Sample Video + Audio", "step": 3, "max": 6, "scene": 1}
    assert d["scenes"] == {"total": 3, "completed": 0, "current": 1, "segments": []} and d["nodes"]["running"] == ["73"]
    assert d["node_states"]["73"]["state"] == "running" and d["node_states"]["20"]["state"] == "done"
    assert d["progress"]["indeterminate"] is False and d["progress"]["fraction"] == 0.0
    fs.advance(1)                                                                    # scene 1 saved, scene 2 begins
    d = doc(sm, run)
    assert d["scenes"]["completed"] == 1 and d["scenes"]["current"] == 2 and d["scenes"]["segments"][0]["state"] == "saved"
    assert d["loop"]["iterations_ended"] == 1 and d["status"] == "running" and d["output"]["state"] == "none"
    assert d["sampling"]["scene"] == 2 and d["node_states"]["73"]["runs"] == 1
    kinds = [e["type"] for e in d["events"]]
    assert "scene_saved" in kinds and "loop_end" in kinds
    assert 0 < d["progress"]["fraction"] < 1 and d["progress"]["basis"] == "scenes_saved/total_scenes"


def test_segments_exist_only_after_they_are_saved_and_play(sm, run, tmp_path):
    fs.advance(5)
    assert sm.get(f"/api/studio/jobs/{run}/segments/1").status_code == 404
    assert sm.get(f"/api/studio/jobs/{run}/preview").status_code == 404
    fs.advance(1)
    r = sm.get(f"/api/studio/jobs/{run}/segments/1")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4" and r.headers["accept-ranges"] == "bytes" and "no-store" in r.headers["cache-control"]
    full = r.content
    info = probe(full, tmp_path)
    assert info["vcodec"] == "h264" and (info["w"], info["h"]) == (64, 112)
    part = sm.get(f"/api/studio/jobs/{run}/segments/1", headers={"range": "bytes=10-109"})
    assert part.status_code == 206 and part.content == full[10:110] and part.headers["content-range"] == f"bytes 10-109/{len(full)}"
    assert sm.get(f"/api/studio/jobs/{run}/segments/1", headers={"range": f"bytes={len(full) + 9}-"}).status_code == 416
    assert sm.get(f"/api/studio/jobs/{run}/segments/2").status_code == 404              # not saved yet
    assert sm.get(f"/api/studio/jobs/{run}/segments/0").status_code == 404
    pv = sm.get(f"/api/studio/jobs/{run}/preview")
    assert pv.status_code == 200 and pv.headers["content-type"] == "image/jpeg" and pv.content[:2] == b"\xff\xd8"


def test_the_graph_and_history_are_read_only_views_of_the_engine(sm, run):
    fs.advance(6)
    g = sm.get(f"/api/studio/jobs/{run}/graph").json()
    assert g["format"] == "comfyui-api" and g["order"][0] == "1" and g["nodes"]["73"]["class_type"] == "SamplerCustomAdvanced"
    d = doc(sm, run)
    assert g["prompt_id"] == d["prompt_id"] and g["nodes"]["101"]["inputs"]["image"] == d["conditioning"]["references"][0]["file"]       # the picture node loads the uploaded file
    assert g["nodes"]["5"]["inputs"]["video"] == d["conditioning"]["video"]["file"] and d["conditioning"]["agree"] is True
    h = sm.get(f"/api/studio/jobs/{run}/history").json()
    assert set(h) == {"status", "messages", "outputs", "node_errors"} and h["node_errors"] == {}
    fs.configure(forget_history=True)
    r = sm.get(f"/api/studio/jobs/{run}/history")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_history"


def test_a_failure_names_the_node_keeps_the_saved_scenes_and_hides_server_paths(sm, tmp_path):
    fs.configure(scenes=3, variant_next="fail")
    clip = up(sm, make_video(tmp_path, dur=6, fps=24, gop=24), "clip.mp4")
    pic = up(sm, png("red", d=tmp_path), "red.png")
    p = mkproject(sm, clip["id"], {"mode": "subject_replacement", "model": oj_model(sm), "subject": {"text": "the dancer"}, "references": [{"asset": pic["id"]}]})
    jid = sm.post(f"/api/studio/projects/{p['id']}/generate", json={}).json()["job"]
    fs.advance(6)                                                                    # scene 1 saved, scene 2 sampling
    assert doc(sm, jid)["status"] == "running"
    fs.advance(1)
    d = doc(sm, jid)
    assert d["status"] == "failed" and d["stage"] == "failed" and d["user_status"] == "failed"
    assert d["error"]["node_id"] == "63" and d["error"]["node_title"] == "Trim Overlap" and d["error"]["class_type"] == "TrimOverlap"
    assert "out of memory" in d["error"]["message"] and "/home/" not in json.dumps(d) and "<path>" in d["error"]["message"]
    assert d["node_states"]["63"]["state"] == "failed" and d["nodes"]["failed"] == 1 and d["output"]["state"] == "none" and d["sampling"] is None
    assert d["scenes"]["completed"] == 1 and sm.get(f"/api/studio/jobs/{jid}/segments/1").status_code == 200       # what was saved can still be looked at
    assert d["progress"]["fraction"] < 1
    assert sm.get(f"/api/studio/jobs/{jid}/history").json()["node_errors"]["63"]
    row = sm.get(f"/api/studio/jobs/{jid}").json()
    assert row["status"] == "failed" and "/home/" not in json.dumps(row)


def test_cancelling_stops_the_trace_where_it_was(sm, run):
    fs.advance(6)
    assert sm.post(f"/api/media/jobs/{run}/cancel").json()["status"] == "cancelled"
    d = doc(sm, run)
    assert d["status"] == "cancelled" and d["user_status"] == "cancelled" and d["stage_label"] == "Cancelled" and d["output"]["state"] == "none" and d["sampling"] is None
    fs.advance(50)
    assert doc(sm, run)["status"] == "cancelled" and doc(sm, run)["scenes"]["completed"] == 1


def test_a_hiccup_of_the_engine_is_stale_not_an_error(sm, run):
    fs.advance(6)
    fs.configure(inspect_down=True)
    d = doc(sm, run)
    assert d["stale"] is True and d["trace_available"] is None and "HTTP 503" in d["stale_reason"]
    assert d["user_status"] in ("queued", "running") and d["status"] == d["user_status"]
    assert sm.get(f"/api/studio/jobs/{run}/graph").status_code == 502
    fs.configure(inspect_down=False)
    d = doc(sm, run)
    assert d["stale"] is False and d["scenes"]["completed"] == 1


def test_a_finished_job_is_still_inspectable_after_the_engine_forgot_its_trace(sm, run):
    fs.advance(100)
    done = doc(sm, run)
    assert done["status"] == "completed" and done["trace_available"] is True
    fs.configure(forget_traces=True)                                                 # the engine restarted and has no record
    d = doc(sm, run)
    assert d["trace_available"] is False and d["status"] == "completed" and d["user_status"] == "completed" and d["stale"] is False
    assert d["output"]["state"] == "ready" and d["output"]["file"]["width"] == done["output"]["file"]["width"] and d["progress"]["fraction"] == 1.0
    assert d["comfy"] == {"available": False} and d["events"] == [] and d["nodes"] is None and d["scenes"]["segments"] == []
    assert d["conditioning"]["references"][0]["used"] is True and d["conditioning"]["agree"] is None and d["operation"] == "swap"
    for what in ("graph", "history"):
        r = sm.get(f"/api/studio/jobs/{run}/{what}")
        assert r.status_code == 404 and r.json()["detail"]["code"] in ("no_graph", "no_history")
    assert sm.get(f"/api/studio/jobs/{run}/segments/1").status_code == 404
    # and even with the engine down altogether
    fs.configure(forget_traces=False, inspect_down=True)
    d = doc(sm, run)
    assert d["trace_available"] is False and d["stale"] is False and d["status"] == "completed"


def test_a_running_job_whose_trace_is_gone_reports_what_semechka_knows(sm, run):
    fs.advance(6)
    fs.configure(forget_traces=True)
    d = doc(sm, run)
    assert d["trace_available"] is False and d["status"] in ("queued", "running") and d["output"]["state"] == "none" and d["scenes"]["total"] is None


def test_jobs_that_are_not_studio_runs_answer_without_pretending(sm, tmp_path):
    jid = sm.post("/api/media/jobs", json={"kind": "video", "model_id": next(m["id"] for m in sm.get("/api/media/options", params={"kind": "video"}).json()["models"] if m["alias"] == "fake-clip"),
                                           "prompt": "a cat", "width": 512, "height": 320, "frames": 17, "fps": 16}).json()["id"]
    d = doc(sm, jid)
    assert d["trace_available"] is False and d["comfy"] == {"available": False} and d["user_status"] in ("queued", "running", "completed")
    assert sm.get(f"/api/studio/jobs/{jid}/graph").status_code == 404


# ---- other people ---------------------------------------------------------------------------------------------------------------
ROUTES = ["inspect", "inspect/stream", "graph", "history", "segments/1", "preview"]


def test_nobody_else_can_reach_a_job_through_any_route(sm, app, run):
    fs.advance(100)
    assert doc(sm, run)["status"] == "completed"
    bob = make_user(sm, app, "bob@example.test")
    nothing = "mj_0000000000000000000000"
    for suffix in ROUTES:
        mine = bob.get(f"/api/studio/jobs/{run}/{suffix}")
        none = bob.get(f"/api/studio/jobs/{nothing}/{suffix}")
        assert mine.status_code == 404 and mine.json() == none.json() == {"detail": {"code": "not_found", "message": "Not found."}}, (suffix, mine.status_code, mine.text)
    assert bob.get(f"/api/studio/jobs/{run}").status_code == 404
    assert bob.post(f"/api/studio/jobs/{run}/export", json={"width": 640, "height": 480}).status_code == 404
    assert bob.get("/api/studio/jobs").json() == []
    # the engine job is only reachable through the owner's job; bob's own job ids never reach someone else's trace
    again = sm.get(f"/api/studio/jobs/{run}/inspect")
    assert again.status_code == 200 and "bob" not in again.text
    from .conftest import Api
    anon = Api(app)
    for suffix in ROUTES:
        assert anon.get(f"/api/studio/jobs/{run}/{suffix}").status_code == 401


def test_a_person_without_video_access_cannot_use_the_inspector(sm, app, run):
    guest = make_user(sm, app, "guest@example.test", role="guest")
    for suffix in ROUTES:
        assert guest.get(f"/api/studio/jobs/{run}/{suffix}").status_code in (403, 404)


# ---- the event stream ------------------------------------------------------------------------------------------------------------
def read_stream(api, url, headers=None, stop_after=None, limit=200):
    """-> list of (event, id, data|None) and the raw comment lines seen. Stops at the end of the stream."""
    events, comments = [], []
    ev = {"event": None, "id": None, "data": []}
    with api.c.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200, r.read()
        assert r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line.startswith(":"):
                comments.append(line)
            elif line == "":
                if ev["event"] or ev["data"]:
                    events.append((ev["event"], ev["id"], json.loads("".join(ev["data"])) if ev["data"] else None))
                    if stop_after and stop_after(events):
                        break
                ev = {"event": None, "id": None, "data": []}
            elif line.startswith("event:"):
                ev["event"] = line[6:].strip()
            elif line.startswith("id:"):
                ev["id"] = line[3:].strip()
            elif line.startswith("data:"):
                ev["data"].append(line[5:].strip())
            if len(events) > limit:
                break
    return events, comments


@pytest.fixture(autouse=True)
def quick_stream(monkeypatch):
    monkeypatch.setattr(studio_inspect, "POLL_S", 0.05)


def test_the_stream_sends_the_final_document_then_ends_and_reconnects_from_last_event_id(sm, run):
    fs.advance(100)
    events, _ = read_stream(sm, f"/api/studio/jobs/{run}/inspect/stream")
    docs = [e for e in events if e[0] == "inspect"]
    assert len(docs) == 1 and docs[0][2]["status"] == "completed" and docs[0][2]["user_status"] == "completed"
    last_id = docs[0][1]
    assert last_id == str(docs[0][2]["next_seq"]) and len(docs[0][2]["events"]) > 10
    again, _ = read_stream(sm, f"/api/studio/jobs/{run}/inspect/stream", headers={"Last-Event-ID": last_id})
    d2 = [e for e in again if e[0] == "inspect"]
    assert len(d2) == 1 and d2[0][2]["events"] == [] and d2[0][2]["status"] == "completed"           # nothing repeated; the current state once; then the stream ends
    q, _ = read_stream(sm, f"/api/studio/jobs/{run}/inspect/stream?after=5")
    assert q[-1][2]["events"][0]["seq"] == 5          # Semechka's cursor is next_seq: ?after=5 is "from event 5 on"


def test_a_live_stream_follows_the_run_and_survives_a_stale_engine(sm, run, monkeypatch):
    monkeypatch.setattr(studio_inspect, "HEARTBEAT_S", 0.2)
    monkeypatch.setattr(studio_inspect, "RESEND_S", 60.0)

    def driver():
        time.sleep(0.8)                      # nothing changes for a while: heartbeats
        fs.advance(6)
        time.sleep(0.4)
        fs.configure(inspect_down=True)      # the engine stops answering
        time.sleep(0.5)
        fs.configure(inspect_down=False)
        time.sleep(0.3)
        fs.advance(100)
    t = threading.Thread(target=driver, daemon=True)
    t.start()
    events, comments = read_stream(sm, f"/api/studio/jobs/{run}/inspect/stream")
    t.join()
    kinds = [e[0] for e in events]
    assert "stale" in kinds and kinds.count("inspect") >= 3 and kinds[-1] == "inspect"
    assert events[kinds.index("stale")][2]["reason"]
    assert any("heartbeat" in c for c in comments)
    inspects = [e[2] for e in events if e[0] == "inspect"]
    assert inspects[0]["status"] == "queued" and inspects[-1]["status"] == "completed" and inspects[-1]["user_status"] == "completed"
    seqs = [x["seq"] for d in inspects for x in d["events"]]
    assert seqs == sorted(set(seqs)) and seqs[0] == 1                                    # every event once, in order, across documents
    ids = [int(e[1]) for e in events if e[0] == "inspect"]
    assert ids == sorted(ids)


def test_a_stream_for_someone_elses_job_is_a_404_not_a_stream(sm, app, run):
    bob = make_user(sm, app, "bob@example.test")
    r = bob.get(f"/api/studio/jobs/{run}/inspect/stream")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
