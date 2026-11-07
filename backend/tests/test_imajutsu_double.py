"""The engine double for native image jobs: it must behave like the real engine (as audited in ~/Documents/Sarness-imajutsu) or every test built on it proves nothing.
These tests talk to the double directly, and check one thing through Semechka's model discovery."""
import hashlib
import struct

import numpy as np
import pytest
from fastapi.testclient import TestClient

from semechka.adapters import sarness

from . import fake_image as fi
from . import fake_sarness as fs
from .imajutsu_helpers import boot_images, decode, pytestmark_ffmpeg

pytestmark = pytestmark_ffmpeg


@pytest.fixture
def eng():
    fs.reset()
    fs.use_imajutsu()
    fs.configure(clock="manual", step=1)
    c = TestClient(fs.app)
    c.headers.update({"Authorization": f"Bearer {fs.TOKEN}"})
    return c


def upload(c, data=None, name="a.png", w=64, h=48):
    data = data or fi.result_png(w, h, 1)
    r = c.post("/v1/uploads", files={"file": (name, data, "image/png")})
    assert r.status_code == 200
    return r.json()["id"], data


def submit(c, expect=200, key=None, **simple):
    r = c.post("/v1/jobs", json={"kind": "image", "client_key": key, "simple": {"prompt": "x", **simple}})
    assert r.status_code == expect, r.text
    return r.json()


def test_inventory_has_the_real_capabilities(eng):
    ms = {m["id"]: m for m in eng.get("/v1/models").json()}
    for mid in fi.BY_ID:
        assert ms[mid]["category"] == "image" and ms[mid]["capabilities"]["modes"] and "path" not in ms[mid]
    assert ms["flux2-klein-4b"]["capabilities"]["modes"] == ["txt2img", "edit", "inpaint"] and ms["flux2-klein-4b"]["capabilities"]["references"] == 4
    assert ms["qwen-image-edit-2511"]["capabilities"]["modes"] == ["edit"] and ms["qwen-image-edit-2511"]["capabilities"]["references"] == 3
    assert ms["qwen-image-2512"]["capabilities"]["modes"] == ["txt2img", "img2img", "inpaint"] and ms["z-image-turbo"]["capabilities"]["modes"] == ["txt2img", "img2img", "inpaint"]
    assert ms["qwen-image-2.1"]["capabilities"]["modes"] == ["txt2img"] and "references" not in ms["qwen-image-2.1"]["capabilities"]
    cap = {m["id"]: m for m in eng.get("/v1/capabilities").json()["models"]}
    assert cap["flux2-klein-4b"]["modes"] == ["txt2img", "edit", "inpaint"]
    fs.reset()                                                                                      # the classic double is unchanged unless asked
    assert "flux2-klein-4b" not in {m["id"] for m in TestClient(fs.app, headers={"Authorization": f"Bearer {fs.TOKEN}"}).get("/v1/models").json()}


def test_uploads_are_kept_byte_for_byte_with_their_size(eng):
    a, data = upload(eng, w=70, h=30)
    meta = fs.STATE["upload_meta"][a]
    assert fs.STATE["uploads"][a] == data and meta["sha256"] == hashlib.sha256(data).hexdigest() and (meta["width"], meta["height"]) == (70, 30) and meta["mime"] == "image/png"


def test_the_engine_refuses_what_the_real_engine_refuses(eng):
    ids = [upload(eng)[0] for _ in range(6)]
    submit(eng, model="flux2-klein-4b", reference_image=ids[0], extra_references=ids[1:4])                     # 4 pictures: exactly the cap
    r = submit(eng, 422, model="flux2-klein-4b", reference_image=ids[0], extra_references=ids[1:5])
    assert "takes at most 4 pictures (got 5)" in r["detail"]
    assert "at most 3 pictures (got 4)" in submit(eng, 422, model="qwen-image-edit-2511", reference_image=ids[0], extra_references=ids[1:4])["detail"]
    assert "at most 1 picture (got 2)" in submit(eng, 422, model="z-image-turbo", reference_image=ids[0], extra_references=ids[1:2])["detail"]
    assert "need a reference_image" in submit(eng, 422, model="flux2-klein-4b", extra_references=ids[1:2])["detail"]
    assert "cannot generate from text" in submit(eng, 422, model="qwen-image-edit-2511")["detail"]
    assert "cannot inpaint" in submit(eng, 422, model="qwen-image-edit-2511", reference_image=ids[0], mask_image=ids[1])["detail"]
    assert "mask_image needs a reference_image" in submit(eng, 422, model="flux2-klein-4b", mask_image=ids[1])["detail"]
    assert "unknown upload" in submit(eng, 422, model="flux2-klein-4b", reference_image="up_9999")["detail"]
    assert "multiple of 16" in submit(eng, 422, model="flux2-klein-4b", width=721, height=1280)["detail"]
    assert "negative prompt" in submit(eng, 422, model="flux2-klein-4b", negative_prompt="blurry")["detail"]
    assert "takes no picture" in submit(eng, 422, model="qwen-image-2.1", reference_image=ids[0])["detail"]
    assert len(fs.STATE["image_submissions"]) == 1                                                             # refused jobs leave nothing behind


def test_every_accepted_submission_is_recorded_exactly(eng):
    a, da = upload(eng, name="src.png", w=64, h=48)
    m, dm = upload(eng, name="mask.png", w=64, h=48)
    j = submit(eng, key="k1", model="flux2-klein-4b", prompt="swap", width=720, height=1280, steps=4, guidance=1.0, seed=77, reference_image=a, mask_image=m, denoise=1.0)
    rec = fs.STATE["image_submissions"][0]
    assert rec["job"] == j["id"] and rec["engine_mode"] == "inpaint" and rec["client_key"] == "k1"
    assert rec["simple"] == {"model": "flux2-klein-4b", "prompt": "swap", "width": 720, "height": 1280, "steps": 4, "guidance": 1.0, "seed": 77, "reference_image": a, "mask_image": m, "denoise": 1.0}
    assert rec["pictures"][0]["sha256"] == hashlib.sha256(da).hexdigest() and rec["mask"]["sha256"] == hashlib.sha256(dm).hexdigest() and rec["pictures"][0]["name"] == "src.png"
    assert submit(eng, key="k1", model="flux2-klein-4b", prompt="swap")["id"] == j["id"] and len(fs.STATE["image_submissions"]) == 1       # idempotent by client_key


def test_scripted_run_stages_steps_preview_and_result(eng, tmp_path):
    j = submit(eng, model="flux2-klein-4b", width=256, height=368, steps=4, seed=5)
    jid = j["id"]
    stages, steps = [], []
    for _ in range(14):
        d = eng.get(f"/v1/jobs/{jid}/inspect").json()
        if not stages or stages[-1] != d["stage"]:
            stages.append(d["stage"])
        if d["sampling"]:
            steps.append(d["sampling"]["step"])
        if d["status"] == "completed":
            break
        fs.advance(1)
    assert stages == ["queued", "loading_model", "encoding_prompt", "sampling", "decoding", "saving_output", "done"] and steps == [0, 1, 2, 3, 4]
    assert d["backend"] == "sarness-workflow" and d["graph_kind"] == "sarness-workflow" and d["prompt_id"] is None
    job = eng.get(f"/v1/jobs/{jid}").json()
    g = job["generations"][0]
    assert job["status"] == "completed" and (g["width"], g["height"], g["seed"], g["type"]) == (256, 368, 5, "image")
    png = eng.get(g["url"]).content
    arr = decode(png, tmp_path)
    assert arr.shape == (368, 256, 3) and png == fi.result_png(256, 368, 5)                                           # a real PNG of the requested size; deterministic
    assert eng.get(f"/v1/jobs/{jid}/history").status_code == 404 and eng.get(f"/v1/jobs/{jid}/segments/1").status_code == 404
    pv = eng.get(f"/v1/jobs/{jid}/preview")
    assert pv.status_code == 200 and pv.content[:3] == b"\xff\xd8\xff"
    assert eng.get(f"/v1/jobs/{jid}/graph").json()["nodes"]["sampler"]["inputs"]["seed"] == 5


def test_a_failure_names_the_node_and_hides_server_paths(eng):
    fs.configure(variant_next="fail")
    jid = submit(eng, model="z-image-turbo", prompt="x", steps=8)["id"]
    fs.advance(50)
    d = eng.get(f"/v1/jobs/{jid}/inspect").json()
    j = eng.get(f"/v1/jobs/{jid}").json()
    assert d["status"] == "failed" and d["error"]["node_id"] == "sampler" and "/home" not in str(d["error"]) and "<path>" in d["error"]["message"] and "/home" not in str(j["error"]) and j["status"] == "failed" and j["error"]["message"].startswith("Sample image:")
    boom = submit(eng, model="flux2-klein-4b", prompt="this will boom")["id"]
    fs.advance(50)
    assert eng.get(f"/v1/jobs/{boom}").json()["status"] == "failed"
    assert eng.get(f"/v1/jobs/{submit(eng, model='flux2-klein-4b')['id']}").json()["status"] in ("queued", "running")      # the next job is fine


def test_estimate_endpoint_bases(eng):
    e = eng.get("/v1/models/flux2-klein-4b/estimate", params={"width": 720, "height": 1280, "steps": 4}).json()
    assert e["seconds"] == {"estimate": 4.0, "basis": "measured"}
    e = eng.get("/v1/models/flux2-klein-4b/estimate", params={"width": 1440, "height": 2560, "steps": 4}).json()
    assert e["seconds"]["basis"] == "extrapolated" and e["seconds"]["estimate"] == pytest.approx(16.0)
    assert eng.get("/v1/models/qwen-image-2.1/estimate", params={"width": 720, "height": 1280}).json()["seconds"] == {"estimate": None, "basis": "none"}
    assert eng.get("/v1/models/nope/estimate", params={"width": 720, "height": 1280}).status_code == 404


def test_png_writer_is_a_valid_png():
    arr = (np.arange(30 * 20 * 3) % 251).astype(np.uint8).reshape(20, 30, 3)
    data = fi.png_bytes(arr)
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", data[16:24]) == (30, 20)
    assert fi.probe_image(data) == {"width": 30, "height": 20, "format": "png"}


def test_semechka_reads_the_reference_limit_from_the_registry_when_models_do_not_say(app, st, sarness_url):
    api = boot_images(app, st, sarness_url, models=["flux2-klein-4b", "qwen-image-edit-2511", "qwen-image-2.1"], engine_state={"registry": True, "refs_registry_only": True})
    with api.c:
        c = api.get("/api/imajutsu/capabilities").json()
        edit = {m["engine_id"]: m for m in next(x for x in c["modes"] if x["id"] == "edit")["models"]}
        assert edit["flux2-klein-4b"]["limits"]["references"] == 4 and edit["qwen-image-edit-2511"]["limits"]["references"] == 3
    assert sarness.media_meta({"category": "image"}, {"capabilities": {"modes": ["edit"], "references": 3}}, None)["media"]["references"] == 3
    assert "references" not in sarness.media_meta({"category": "image"}, {"capabilities": {"modes": ["txt2img"]}}, None)["media"]


def test_without_any_statement_a_model_takes_no_references(app, st, sarness_url):
    """An engine that never says how many pictures an edit model takes: Semechka does not guess; the model takes the source only."""
    api = boot_images(app, st, sarness_url, models=["flux2-klein-4b"], engine_state={"refs_registry_only": True})
    with api.c:
        c = api.get("/api/imajutsu/capabilities").json()
        row = {m["engine_id"]: m for m in next(x for x in c["modes"] if x["id"] == "edit")["models"]}["flux2-klein-4b"]
        assert row["limits"]["references"] == 0 and row["status"] == "ready"
        rf = next(x for x in c["modes"] if x["id"] == "reference")
        assert {m["engine_id"]: m for m in rf["models"]}["flux2-klein-4b"]["status"] == "ready"          # one picture is still a reference
        rs = next(x for x in c["modes"] if x["id"] == "replace_subject")
        assert {m["engine_id"]: m for m in rs["models"]}["flux2-klein-4b"]["status"] == "unavailable"     # but replacing a subject needs the source AND a reference


def test_the_adapter_forwards_the_new_image_keys(sarness_url):
    import asyncio

    from semechka.adapters.sarness import SarnessAdapter
    fs.reset()
    fs.use_imajutsu()
    ad = SarnessAdapter(sarness_url, fs.TOKEN)

    async def go():
        a = await ad.media_upload(fi.result_png(64, 48, 1), "image/png", "a.png")
        b = await ad.media_upload(fi.result_png(64, 48, 2), "image/png", "b.png")
        m = await ad.media_upload(fi.result_png(64, 48, 3), "image/png", "m.png")
        return a, b, m, await ad.media_submit("image", {"model": "flux2-klein-4b", "prompt": "x", "reference_image": a, "extra_references": [b], "mask_image": m, "denoise": 0.9,
                                                        "loras": [{"id": "some-lora", "strength": 0.7}], "upscale": {"model": "x", "factor": 2}, "batch_size": 2, "unknown_key": 1}, "ck-1")
    a, b, m, jid = asyncio.run(go())
    simple = fs.STATE["image_submissions"][-1]["simple"]
    assert simple["extra_references"] == [b] and simple["mask_image"] == m and simple["loras"] == [{"id": "some-lora", "strength": 0.7}] and simple["upscale"] == {"model": "x", "factor": 2}
    assert simple["batch_size"] == 2 and simple["denoise"] == 0.9 and "unknown_key" not in simple and jid
