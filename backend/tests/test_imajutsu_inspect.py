"""Imajutsu: the job inspector for native image jobs. The engine double runs the engine's own workflow (backend "sarness-workflow": loading_model, encoding_prompt,
sampling with a step counter, decoding, saving_output, done) one tick at a time; Semechka's /api/studio/jobs/{job}/... routes serve it to the person who started it."""
import json

import pytest

from semechka import studio_inspect

from . import fake_sarness as fs
from .conftest import Api, make_user
from .imajutsu_helpers import (
    decode,
    gradient,
    mkproject,
    model_id,
    png_of,
    pytestmark_ffmpeg,
    solid_png,
    up,
)

pytestmark = pytestmark_ffmpeg


@pytest.fixture(autouse=True)
def quick_stream(monkeypatch):
    monkeypatch.setattr(studio_inspect, "POLL_S", 0.05)


@pytest.fixture
def run(im):
    """A started edit job with a source and two references -> (job id). Nothing has happened in the engine yet (tick 0)."""
    src = up(im, png_of(gradient(240, 320)), "src.png")
    refs = [up(im, solid_png((200, 30, 30)), "anna.png"), up(im, solid_png((30, 200, 30)), "bike.png"), up(im, solid_png((30, 30, 200)), "extra.png")]
    p = mkproject(im, "edit", {"prompt": "swap the jacket", "source_asset": src["id"], "aspect": {"id": "source"}, "model": model_id(im, "flux2-klein-4b"),
                               "references": [{"asset": r["id"], "role": "identity", "active": True, "label": f"ref {i}"} for i, r in enumerate(refs)]})
    r = im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={})
    assert r.status_code == 200, r.text
    return r.json()["job"]


def doc(api, jid, after=0, expect=200):
    r = api.get(f"/api/studio/jobs/{jid}/inspect", params={"after": after})
    assert r.status_code == expect, r.text
    return r.json()


def test_the_workflow_run_is_visible_stage_by_stage(im, run):
    d = doc(im, run)
    assert d["backend"] == "sarness-workflow" and d["graph_kind"] == "sarness-workflow" and d["comfy"] == {"available": False} and d["workflow"] == {"available": True}
    assert d["job_id"] == run and d["status"] == "queued" and d["stage"] == "queued" and d["user_status"] == "queued" and d["trace_available"] is True and d["stale"] is False
    assert d["scenes"]["total"] is None and d["loop"] is None and d["output"]["state"] == "none"
    seen_stages, steps, cursor = [d["stage"]], [], 0
    for _ in range(40):
        fs.advance(1)
        d = doc(im, run, cursor)
        cursor = d["next_seq"]
        if d["stage"] != seen_stages[-1]:
            seen_stages.append(d["stage"])
        if d["sampling"]:
            steps.append((d["sampling"]["step"], d["sampling"]["max"], d["sampling"]["node_id"]))
        if d["status"] == "completed":
            break
    assert seen_stages == ["queued", "loading_model", "encoding_prompt", "sampling", "decoding", "saving_output", "done"]
    assert steps[0][0] == 0 and steps[-1] == (4, 4, "sampler") and [s[0] for s in steps] == sorted(s[0] for s in steps) and {s[1] for s in steps} == {4}
    assert d["status"] == "completed" and d["output"]["state"] == "ready" and d["output"]["file"]["width"] == 816 and d["output"]["file"]["format"] == "png"
    assert d["progress"] == {"fraction": 1.0, "indeterminate": False, "basis": "output_ready"} and d["error"] is None and d["nodes"]["failed"] == 0
    assert set(d["node_states"]) >= {"model", "prompt", "ref", "ref_resize", "encode", "ref_2", "ref_3", "ref_4", "sampler", "decode", "save"}
    assert all(n["state"] == "done" for n in d["node_states"].values())
    refs = d["conditioning"]["references"]
    assert [(r["position"], r["used"]) for r in refs] == [(1, True), (2, True), (3, True), (4, True)] and refs[0]["kind"] == "source" and d["conditioning"]["mask"] is None
    assert d["user_status"] == "completed"


def test_events_are_incremental_and_ordered(im, run):
    seqs, cursor = [], 0
    for _ in range(20):
        fs.advance(1)
        d = doc(im, run, cursor)
        seqs += [e["seq"] for e in d["events"]]
        cursor = d["next_seq"]
        assert d["next_seq"] == d["last_seq"] + 1
    assert seqs == sorted(set(seqs)) and seqs[0] == 1 and len(seqs) > 10


def test_preview_appears_with_the_first_step_and_is_a_real_jpeg(im, run, tmp_path):
    r = im.get(f"/api/studio/jobs/{run}/preview")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "no_preview"
    fs.advance(3)
    assert im.get(f"/api/studio/jobs/{run}/preview").status_code == 404 and doc(im, run)["has_preview"] is False          # sampling started, no step done
    fs.advance(1)
    r = im.get(f"/api/studio/jobs/{run}/preview")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
    arr = decode(r.content, tmp_path, "pv.bin")
    assert max(arr.shape[:2]) == 96 and arr.shape[2] == 3                         # the engine's latent preview: a small JPEG (long edge 96)
    fs.advance(2)
    r2 = im.get(f"/api/studio/jobs/{run}/preview")
    assert r2.status_code == 200 and r2.content != r.content                                                          # it changes as the sampler goes on


def test_graph_is_the_workflow_not_a_comfyui_prompt(im, run):
    g = im.get(f"/api/studio/jobs/{run}/graph")
    assert g.status_code == 200
    j = g.json()
    assert j["graph_kind"] == "sarness-workflow" and j["format"] == "sarness-workflow"
    assert j["nodes"]["sampler"]["class_type"] == "sampler.image" and j["nodes"]["sampler"]["inputs"]["model"] == ["model", 0]
    assert j["nodes"]["sampler"]["inputs"]["reference_2"] == ["ref_2", 0] and j["nodes"]["sampler"]["inputs"]["reference_3"] == ["ref_3", 0]
    assert j["nodes"]["prompt"]["inputs"]["prompt"].startswith("swap the jacket")
    assert j["order"][0] == "model" and j["order"][-1] == "save"
    h = im.get(f"/api/studio/jobs/{run}/history")
    assert h.status_code == 404 and h.json()["detail"]["code"] == "no_graph"
    assert "/home" not in g.text


def test_a_failed_run_names_the_failing_node_without_server_paths(im, run):
    fs.configure(variant_next="fail")
    src = up(im, png_of(gradient(120, 160)), "s2.png")
    r = im.post(f"/api/imajutsu/projects/{mkproject(im, 'edit', {'prompt': 'x', 'source_asset': src['id'], 'aspect': {'id': 'source'}})['id']}/generate", json={})
    bad = r.json()["job"]
    fs.advance(100)
    d = doc(im, bad)
    assert d["status"] == "failed" and d["stage"] == "failed" and d["error"]["node_id"] == "sampler" and d["error"]["node_title"] == "Sample image" and d["error"]["class_type"] == "sampler.image"
    assert "out of memory" in d["error"]["message"] and "/home" not in json.dumps(d) and "<path>" in d["error"]["message"]
    assert d["node_states"]["sampler"]["state"] == "failed" and d["node_states"]["decode"]["state"] == "pending" and d["output"]["state"] == "none" and d["sampling"] is None
    im.get(f"/api/media/jobs/{bad}")
    assert doc(im, bad)["user_status"] == "failed"
    ok = doc(im, run)                                                                              # another job is not affected
    assert ok["status"] != "failed"


def test_cancel_stops_the_run_at_its_tick(im, run):
    fs.advance(5)
    assert im.post(f"/api/media/jobs/{run}/cancel").status_code == 200
    fs.advance(50)
    d = doc(im, run)
    assert d["status"] == "cancelled" and d["stage"] == "failed" and d["user_status"] == "cancelled" and d["output"]["state"] == "none"


def test_when_the_engine_forgot_the_trace_semechka_reports_only_what_it_knows(im, run):
    fs.advance(100)
    im.get(f"/api/media/jobs/{run}")
    fs.configure(forget_traces=True)
    d = doc(im, run)
    assert d["trace_available"] is False and d["status"] == "completed" and d["workflow"] == {"available": False} and d["comfy"] == {"available": False} and d["graph_kind"] is None
    refs = d["conditioning"]["references"]
    assert [(r["position"], r["used"]) for r in refs] == [(1, True), (2, True), (3, True), (4, True)] and refs[0]["kind"] == "source" and d["output"]["file"]["width"] == 816
    assert d["operation"] == "edit" and d["node_states"] == {} and d["events"] == []
    fs.configure(forget_traces=False, inspect_down=True)
    d = doc(im, run)
    assert d["trace_available"] is False and d["stale"] is False and d["status"] == "completed"


def read_events(api, url, headers=None, limit=60):
    events = []
    ev = {"event": None, "id": None, "data": []}
    with api.c.stream("GET", url, headers=headers or {}) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        for line in r.iter_lines():
            if line == "":
                if ev["event"]:
                    events.append((ev["event"], ev["id"], json.loads("".join(ev["data"])) if ev["data"] else None))
                ev = {"event": None, "id": None, "data": []}
            elif line.startswith("event:"):
                ev["event"] = line[6:].strip()
            elif line.startswith("id:"):
                ev["id"] = line[3:].strip()
            elif line.startswith("data:"):
                ev["data"].append(line[5:].strip())
            if len(events) > limit:
                break
    return events


def test_the_stream_ends_with_the_final_document(im, run):
    fs.advance(100)
    ev = read_events(im, f"/api/studio/jobs/{run}/inspect/stream")
    docs = [e for e in ev if e[0] == "inspect"]
    assert docs and docs[-1][2]["status"] == "completed" and docs[-1][2]["graph_kind"] == "sarness-workflow" and docs[-1][2]["user_status"] == "completed"


def test_nobody_else_can_reach_an_image_job_through_any_route(im, app, run):
    fs.advance(100)
    im.get(f"/api/media/jobs/{run}")
    bob = make_user(im, app, "bob@example.test")
    nothing = "mj_0000000000000000000000"
    for suffix in ["inspect", "inspect/stream", "graph", "history", "preview", "segments/1"]:
        mine = bob.get(f"/api/studio/jobs/{run}/{suffix}")
        none = bob.get(f"/api/studio/jobs/{nothing}/{suffix}")
        assert mine.status_code == 404 and mine.json() == none.json() == {"detail": {"code": "not_found", "message": "Not found."}}, (suffix, mine.status_code, mine.text)
    anon = Api(app)
    for suffix in ["inspect", "inspect/stream", "graph", "history", "preview"]:
        assert anon.get(f"/api/studio/jobs/{run}/{suffix}").status_code == 401
    assert im.get(f"/api/studio/jobs/{run}/inspect").status_code == 200


def test_a_person_without_image_access_cannot_use_the_inspector(im, app, run):
    assert im.put("/api/admin/settings/modes.image.enabled", json={"value": False}).status_code == 200
    assert im.get(f"/api/studio/jobs/{run}/inspect").status_code == 403
    assert im.put("/api/admin/settings/modes.image.enabled", json={"value": True}).status_code == 200
    assert im.get(f"/api/studio/jobs/{run}/inspect").status_code == 200
