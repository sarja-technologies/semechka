"""Imajutsu: generating. The real Semechka server and database, real pictures and ffmpeg; the engine double records exactly what it was sent."""
import numpy as np
import pytest

from . import fake_sarness as fs
from .imajutsu_helpers import (
    content,
    decode,
    finish_jobs,
    gradient,
    jpeg_of,
    last_submission,
    mkproject,
    model_id,
    png_of,
    pytestmark_ffmpeg,
    solid,
    solid_png,
    up,
    uploaded,
    wait_task,
    with_orientation,
)

pytestmark = pytestmark_ffmpeg


def generate(api, pid, expect=200, **body):
    r = api.post(f"/api/imajutsu/projects/{pid}/generate", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def raise_concurrency(api, n=6):
    assert api.put("/api/admin/settings/media.max_concurrent_jobs", json={"value": n}).status_code == 200


# ---- text to image -----------------------------------------------------------------------------------------------------------------------------
def test_one_engine_job_per_variation_with_consecutive_seeds(im, tmp_path):
    raise_concurrency(im)
    p = mkproject(im, "generate", {"prompt": "a red bicycle on a quiet street", "count": 3, "seed": 100})
    d = generate(im, p["id"])
    assert len(d["jobs"]) == 3 and d["job"] == d["jobs"][0] and d["batch"]["seeds"] == [100, 101, 102]
    subs = fs.STATE["image_submissions"]
    assert [s["simple"]["seed"] for s in subs] == [100, 101, 102] and all(s["engine_mode"] == "txt2img" and s["pictures"] == [] and s["mask"] is None for s in subs)
    assert {(s["simple"]["width"], s["simple"]["height"], s["simple"]["steps"]) for s in subs} == {(720, 1280, 4)} and subs[0]["model"] == "flux2-klein-4b"
    assert all("reference_image" not in s["simple"] and "extra_references" not in s["simple"] and "negative_prompt" not in s["simple"] for s in subs)
    done = finish_jobs(im, d["jobs"])
    assert [j["status"] for j in done] == ["completed"] * 3
    outs = [a for j in done for a in j["assets"]]
    assert len(outs) == 3 and {(a["width"], a["height"]) for a in outs} == {(720, 1280)} and [a["seed"] for a in outs] == [100, 101, 102]
    img = decode(content(im, outs[0]["id"]), tmp_path)
    assert img.shape == (1280, 720, 3)
    blk = outs[0]["params"]["imajutsu"]
    assert blk["mode"] == "generate" and blk["batch"] == {"id": d["batch"]["id"], "index": 0, "count": 3, "seed": 100} and blk["spec"]["prompt"].startswith("a red bicycle")
    rows = im.get("/api/imajutsu/jobs").json()
    assert {r["job"] for r in rows} == set(d["jobs"]) and all(r["project_id"] == p["id"] and r["outputs"] for r in rows)
    pj = im.get(f"/api/imajutsu/projects/{p['id']}").json()
    assert {j["id"] for j in pj["jobs"]} == set(d["jobs"])


def test_variations_are_all_admitted_or_none(im):
    raise_concurrency(im, 2)
    p = mkproject(im, "generate", {"prompt": "a red bicycle", "count": 3})
    r = im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={})
    assert r.status_code == 429 and "2 generations queued or running" in r.json()["detail"]["message"] and "Ask for 2 variations" in r.json()["detail"]["message"]
    assert fs.STATE["image_submissions"] == [] and fs.STATE["uploads"] == {}
    d = generate(im, p["id"], count=2)
    assert len(d["jobs"]) == 2
    assert im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={"count": 1}).status_code == 429
    assert im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={"count": 5}).status_code == 422


def test_generate_with_the_spec_in_the_request_saves_it(im):
    p = mkproject(im, "generate")
    d = generate(im, p["id"], spec={"mode": "generate", "prompt": "lemons on a plate", "aspect": {"id": "1:1"}, "quality": "high", "seed": 5})
    assert d["plan"]["native"]["width"] == 960 and d["plan"]["steps"] == 8 and last_submission()["simple"]["steps"] == 8
    saved = im.get(f"/api/imajutsu/projects/{p['id']}").json()["spec"]
    assert saved["prompt"] == "lemons on a plate" and saved["aspect"] == {"id": "1:1", "w": 1, "h": 1} and saved["seed"] == 5


def test_iphone_photo_uses_the_preset_and_the_models_wording_rules(im):
    scenes = ["night", "flash", "selfie"]
    p = mkproject(im, "iphone_photo", {"prompt": "my friend laughing at a bus stop", "preset": {"id": "iphone_photo", "scenes": scenes, "finish": True, "tier": "fast"}})
    d = generate(im, p["id"])
    sub = last_submission()
    assert sub["model"] == "flux2-klein-4b" and (sub["simple"]["width"], sub["simple"]["height"]) == (720, 1280) and len(d["jobs"]) == 1
    prompt = sub["simple"]["prompt"]
    assert prompt.startswith("my friend laughing at a bus stop.") and "street lights" in prompt and "direct on-camera flash" in prompt and "arm's length" in prompt
    low = prompt.lower()
    assert "exif" not in low and "9:16" not in low and "iphone" not in low
    assert d["plan"]["prompt"]["preset"]["finish_recipe"]["grain"] > 0                     # the optional finish is offered, not applied
    # the same request on Z-Image: plain wording, nothing that makes it draw phones
    z = model_id(im, "z-image-turbo")
    pz = mkproject(im, "iphone_photo", {"prompt": "my friend laughing at a bus stop", "model": z, "preset": {"id": "iphone_photo", "scenes": scenes, "finish": False}})
    generate(im, pz["id"])
    zp = last_submission()["simple"]["prompt"]
    assert last_submission()["model"] == "z-image-turbo" and "phone" not in zp.lower() and "selfie" not in zp.lower() and "9:16" not in zp and "snapshot" not in zp.lower()
    assert "arm's length" in zp


def test_negative_prompt_is_sent_only_to_models_that_use_one(im):
    p = mkproject(im, "generate", {"prompt": "a cat", "negative": "blurry"})
    d = generate(im, p["id"])
    assert "negative_prompt" not in last_submission()["simple"] and any("negative prompt" in w for w in d["plan"]["warnings"]) and d["accounting"]["negative_sent"] is False
    pz = mkproject(im, "generate", {"prompt": "a cat", "negative": "blurry", "model": model_id(im, "z-image-turbo")})
    generate(im, pz["id"])
    assert last_submission()["simple"]["negative_prompt"] == "blurry"


# ---- editing with pictures ---------------------------------------------------------------------------------------------------------------------
COLORS = [(250 - 20 * i, 10 + 15 * i, 30 + 10 * i) for i in range(12)]


def mean_rgb(data, tmp_path):
    return tuple(int(round(x)) for x in decode(data, tmp_path, "m.bin").reshape(-1, 3).mean(0))


@pytest.mark.parametrize("alias,mode,cap,source", [("flux2-klein-4b", "edit", 4, True), ("qwen-image-edit-2511", "edit", 3, True), ("flux2-klein-4b", "reference", 4, False),
                                                   ("qwen-image-edit-2511", "reference", 3, False)])
@pytest.mark.parametrize("n", [1, 3, 4, 5, 9, 12])
def test_exactly_the_active_references_reach_the_engine_in_order(im, tmp_path, alias, mode, cap, source, n):
    refs = [up(im, solid_png(c), f"ref{i}.png") for i, c in enumerate(COLORS[:n])]
    off = {2, 3} if n >= 6 else set()                                              # two pictures are switched off in the project
    spec = {"prompt": "put the subject of the other pictures on a beach", "model": model_id(im, alias),
            "references": [{"asset": r["id"], "role": ["identity", "style", None][i % 3], "active": i not in off, "label": f"pic {i}"} for i, r in enumerate(refs)]}
    if source:
        src = up(im, png_of(gradient(240, 320)), "src.png")
        spec.update(source_asset=src["id"], aspect={"id": "source"})
    p = mkproject(im, mode, spec)
    d = generate(im, p["id"])
    slots = cap - (1 if source else 0)
    active = [i for i in range(n) if i not in off]
    sent = active[:slots]
    sub = last_submission()
    pics = sub["pictures"]
    assert len(pics) == len(sent) + (1 if source else 0) <= cap
    if source:
        assert pics[0]["name"] == "source.png" and (pics[0]["width"], pics[0]["height"]) == (sub["simple"]["width"], sub["simple"]["height"])
    got = [mean_rgb(uploaded(x), tmp_path) for x in (pics[1:] if source else pics)]
    assert got == [COLORS[i] for i in sent]                                         # these pictures, in this order, nothing else
    assert sub["simple"].get("reference_image") == pics[0]["upload"] and sub["simple"].get("extra_references", []) == [x["upload"] for x in pics[1:]]
    acc = d["accounting"]
    assert [x["asset"] for x in acc["sent_to_engine"]] == [refs[i]["id"] for i in sent]
    listed = {x["asset"]: x["reason"] for x in acc["not_used"]}
    assert set(listed) == {refs[i]["id"] for i in range(n)} - {refs[i]["id"] for i in sent}
    for i in range(n):
        if i in off:
            assert listed[refs[i]["id"]] == "Switched off in the project."
        elif i not in sent:
            assert "takes at most" in listed[refs[i]["id"]]
    # the sentences about the pictures are in the prompt the engine got, numbered the way the pictures were sent
    for k in range(len(sent)):
        assert f"image {k + 1 + (1 if source else 0)}" in sub["simple"]["prompt"]
    # and the job remembers all of them, used or not, with the roles
    blk = im.get("/api/imajutsu/jobs").json()[0]["imajutsu"]
    assert [r["asset"] for r in blk["references"]] == [r["id"] for r in refs] and [r["active"] for r in blk["references"]] == [i not in off for i in range(n)]


def test_replace_subject_sentence_and_inpaint_path_with_a_selection(im, tmp_path):
    src = up(im, png_of(gradient(240, 320)), "street.png")
    bike = up(im, solid_png((20, 200, 40), 48, 48), "motorcycle.png")
    spec = {"prompt": "the red car", "source_asset": src["id"], "aspect": {"id": "source"}, "references": [{"asset": bike["id"], "role": None, "active": True, "label": "a green motorcycle"}],
            "selection": {"box": {"x": 0.25, "y": 0.4, "w": 0.5, "h": 0.3}, "feather": 0, "invert": False, "keep_outside": None}}
    d = generate(im, mkproject(im, "replace_subject", spec)["id"])
    sub = last_submission()
    assert sub["model"] == "qwen-image-edit-2511" and sub["engine_mode"] == "edit" and sub["mask"] is None       # qwen-edit cannot inpaint: the box is not sent
    assert "Replace the subject in image 1 with the subject from image 2 (it shows: a green motorcycle)" in sub["simple"]["prompt"]
    k = generate(im, mkproject(im, "replace_subject", {**spec, "model": model_id(im, "flux2-klein-4b")})["id"])
    sub = last_submission()
    assert sub["engine_mode"] == "inpaint" and sub["mask"] is not None and len(sub["pictures"]) == 2 and sub["simple"]["denoise"] == 1.0
    assert k["accounting"]["mask"]["box"] == spec["selection"]["box"] and d["accounting"]["mask"] is None


# ---- masks, outpaint, upscale -------------------------------------------------------------------------------------------------------------------
def mask_of(sub, tmp_path):
    return decode(uploaded(sub["mask"]), tmp_path, "mask.bin")[..., 0]


def test_mask_polarity_box_invert_and_feather(im, tmp_path):
    src = up(im, png_of(gradient(200, 300)), "src.png")
    m = np.zeros((300, 200, 3), np.uint8)
    m[100:200, 50:150] = 255                                                                    # white = change this
    mask = up(im, png_of(m), "mask.png")
    base = {"prompt": "a cat sitting", "source_asset": src["id"], "mask_asset": mask["id"], "aspect": {"id": "source"}}
    generate(im, mkproject(im, "inpaint", {**base, "selection": {"feather": 0}})["id"])
    sub = last_submission()
    w, h = sub["simple"]["width"], sub["simple"]["height"]
    mk = mask_of(sub, tmp_path)
    assert mk.shape == (h, w) and sub["engine_mode"] == "inpaint" and sub["simple"]["mask_image"] == sub["mask"]["upload"]
    assert mk[int(h * 0.5), int(w * 0.5)] == 255 and mk[int(h * 0.1), int(w * 0.1)] == 0 and mk[int(h * 0.9), int(w * 0.9)] == 0
    assert abs(float(mk.mean()) / 255 - (100 * 100) / (200 * 300)) < 0.02                       # the selected share of the picture survives the rescale
    src_png = decode(uploaded(sub["pictures"][0]), tmp_path, "s.bin")
    assert src_png.shape == (h, w, 3)
    generate(im, mkproject(im, "inpaint", {**base, "selection": {"feather": 0, "invert": True}})["id"])
    inv = mask_of(last_submission(), tmp_path)
    assert inv[int(h * 0.5), int(w * 0.5)] == 0 and inv[int(h * 0.1), int(w * 0.1)] == 255
    generate(im, mkproject(im, "inpaint", {**base, "selection": {"feather": 12, "invert": False}})["id"])
    soft = mask_of(last_submission(), tmp_path)
    assert ((soft > 10) & (soft < 245)).sum() > 100 and soft[int(h * 0.5), int(w * 0.5)] > 240       # a soft edge, solid inside
    # a drawn box instead of a mask picture
    generate(im, mkproject(im, "inpaint", {"prompt": "a cat", "source_asset": src["id"], "aspect": {"id": "source"},
                                          "selection": {"box": {"x": 0.25, "y": 0.3333, "w": 0.5, "h": 0.3333}, "feather": 0, "invert": False, "keep_outside": None}})["id"])
    bx = mask_of(last_submission(), tmp_path)
    assert bx[int(h * 0.5), int(w * 0.5)] == 255 and bx[int(h * 0.1), int(w * 0.5)] == 0 and abs(float(bx.mean()) / 255 - 1 / 6) < 0.02


def test_inpaint_needs_a_selection_and_a_mask_of_the_same_shape(im):
    src = up(im, png_of(gradient(200, 300)), "src.png")
    p = mkproject(im, "inpaint", {"prompt": "a cat", "source_asset": src["id"], "aspect": {"id": "source"}})
    r = im.post(f"/api/imajutsu/projects/{p['id']}/generate", json={})
    assert r.status_code == 422 and "needs a selection" in r.json()["detail"]["message"] and r.json()["detail"]["code"] == "plan_errors"
    wrong = up(im, png_of(solid(300, 200, (255, 255, 255))), "wrong.png")
    p2 = mkproject(im, "inpaint", {"prompt": "a cat", "source_asset": src["id"], "mask_asset": wrong["id"], "aspect": {"id": "source"}})
    r = im.post(f"/api/imajutsu/projects/{p2['id']}/generate", json={})
    assert r.status_code == 422 and "another shape" in r.json()["detail"]["message"]
    assert fs.STATE["image_submissions"] == []


def test_outpaint_builds_the_canvas_and_the_mask_of_the_new_area(im, tmp_path):
    src = up(im, png_of(solid(200, 300, (30, 60, 200))), "src.png")
    p = mkproject(im, "outpaint", {"prompt": "", "source_asset": src["id"], "aspect": {"id": "16:9"}})
    d = generate(im, p["id"])
    sub = last_submission()
    w, h = sub["simple"]["width"], sub["simple"]["height"]
    assert (w, h) == (1280, 720) and sub["engine_mode"] == "inpaint" and "Continue the scene" in sub["simple"]["prompt"]
    pad = d["plan"]["outpaint"]["pad"]
    assert pad["top"] == pad["bottom"] == 0 and pad["left"] + pad["right"] == d["plan"]["outpaint"]["canvas"]["width"] - 200 and abs(pad["left"] - pad["right"]) <= 1
    canvas = decode(uploaded(sub["pictures"][0]), tmp_path, "c.bin")
    mk = mask_of(sub, tmp_path)
    assert canvas.shape == (720, 1280, 3) and mk.shape == (720, 1280)
    left_band, right_band, centre = (slice(0, int(w * 0.2)), slice(int(w * 0.8), w), slice(int(w * 0.45), int(w * 0.55)))
    assert (canvas[:, left_band] == 127).all() and (canvas[:, right_band] == 127).all()           # neutral fill in the new area
    assert np.abs(canvas[:, centre].reshape(-1, 3).mean(0) - np.array([30, 60, 200])).max() < 3 # the picture, in the middle
    assert (mk[:, left_band] == 255).all() and (mk[:, right_band] == 255).all() and (mk[:, centre] == 0).all()
    assert set(np.unique(mk)) <= {0, 255}                                                         # a crisp mask
    assert d["accounting"]["mask"]["outpaint"]["canvas"] == d["plan"]["outpaint"]["canvas"]
    # an exact padding instead of a shape; the canvas keeps the picture's own pixels
    q = generate(im, mkproject(im, "outpaint", {"prompt": "more sky", "source_asset": src["id"], "aspect": {"id": "source"},
                                               "outpaint": {"pad": {"left": 0, "top": 100, "right": 0, "bottom": 0}, "target": None, "anchor": {"x": .5, "y": .5}, "overlap": 0}})["id"])
    assert q["plan"]["outpaint"]["canvas"] == {"width": 200, "height": 400}
    sub = last_submission()
    mk = mask_of(sub, tmp_path)
    h = sub["simple"]["height"]
    assert (mk[: h // 4 - 4] == 255).all() and (mk[h // 4 + 4:] == 0).all()
    ov = generate(im, mkproject(im, "outpaint", {"prompt": "more sky", "source_asset": src["id"], "aspect": {"id": "source"},
                                                "outpaint": {"pad": {"left": 0, "top": 100, "right": 0, "bottom": 0}, "target": None, "anchor": {"x": .5, "y": .5}, "overlap": 50}})["id"])
    assert ov["plan"]["outpaint"]["overlap"] == 50
    mk2 = mask_of(last_submission(), tmp_path)
    assert mk2.mean() > mk.mean()                                                                  # the mask reaches into the picture by the overlap


def test_upscale_with_refine_is_a_model_pass_and_plain_upscale_is_not(im, tmp_path):
    src = up(im, png_of(gradient(360, 640)), "small.png")
    plain = mkproject(im, "upscale", {"source_asset": src["id"], "aspect": {"id": "source"}, "size": {"native": None, "output": {"width": 720, "height": 1280}}})
    r = im.post(f"/api/imajutsu/projects/{plain['id']}/generate", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "use_upscale"
    pl = im.post(f"/api/imajutsu/projects/{plain['id']}/plan", json={}).json()["plan"]
    assert pl["model"] is None and pl["engine_mode"] is None and pl["output"]["scaling"] == "upscale" and pl["scaling"]["ai"] is False
    ref = mkproject(im, "upscale", {"source_asset": src["id"], "aspect": {"id": "source"}, "size": {"native": None, "output": {"width": 720, "height": 1280}},
                                    "upscale": {"refine": True, "strength": 0.25}})
    d = generate(im, ref["id"])
    sub = last_submission()
    assert sub["model"] == "z-image-turbo" and sub["engine_mode"] == "img2img" and sub["simple"]["denoise"] == 0.25 and (sub["simple"]["width"], sub["simple"]["height"]) == (720, 1280)
    assert (sub["pictures"][0]["width"], sub["pictures"][0]["height"]) == (720, 1280) and any("model pass" in w for w in d["plan"]["warnings"])


# ---- keep everything outside the selection ----------------------------------------------------------------------------------------------------
def test_keep_outside_blend_returns_the_source_exactly_outside_the_selection(im, tmp_path):
    src = up(im, png_of(gradient(192, 288)), "street.png")
    m = np.zeros((288, 192, 3), np.uint8)
    m[96:192, 48:144] = 255
    mask = up(im, png_of(m), "mask.png")
    p = mkproject(im, "inpaint", {"prompt": "a lamp", "source_asset": src["id"], "mask_asset": mask["id"], "aspect": {"id": "source"}, "selection": {"feather": 0}})
    d = generate(im, p["id"])
    sub = last_submission()
    done = finish_jobs(im, d["jobs"])
    res = done[0]["assets"][0]
    r = im.post(f"/api/imajutsu/assets/{res['id']}/keep-outside", json={})
    assert r.status_code == 200, r.text
    t = wait_task(im, r.json()["task"])
    assert t["status"] == "completed", t
    new = t["asset"]
    assert new["origin"] == "composited" and new["parent"] == res["id"] and new["imajutsu"]["kind"] == "composited" and "SOURCE" not in str(new["imajutsu"]["label"]).upper()[:0]
    out = decode(content(im, new["id"]), tmp_path, "out.bin")
    generated = decode(content(im, res["id"]), tmp_path, "gen.bin")
    prepared = decode(uploaded(sub["pictures"][0]), tmp_path, "prep.bin")
    h, w = out.shape[:2]
    inside = (slice(int(h * 0.38), int(h * 0.62)), slice(int(w * 0.3), int(w * 0.7)))
    assert np.array_equal(out[inside], generated[inside])                                           # the generated picture inside
    outside = np.ones((h, w), bool)
    outside[int(h * 0.30):int(h * 0.70), int(w * 0.22):int(w * 0.78)] = False
    assert np.array_equal(out[outside], prepared[outside])                                          # the source, exactly, everywhere else
    assert not np.array_equal(prepared[inside], generated[inside])
    # the job lists what was made from its result, so a reopened job shows its adjustments and blends too
    adj = wait_task(im, im.post(f"/api/imajutsu/assets/{res['id']}/adjust", json={"recipe": {"contrast": 15}}).json()["task"])["asset"]
    derived = im.get(f"/api/imajutsu/jobs/{d['job']}").json()["outputs"][0]["derived"]
    assert [(x["id"], x["origin"]) for x in derived] == [(new["id"], "composited"), (adj["id"], "adjusted")]
    # it works from a result only, and says so otherwise
    other = up(im, png_of(gradient(100, 100)), "x.png")
    assert im.post(f"/api/imajutsu/assets/{other['id']}/keep-outside", json={}).status_code == 422


def test_keep_outside_for_an_outpaint_keeps_the_original_picture(im, tmp_path):
    src = up(im, png_of(gradient(200, 300)), "src.png")
    d = generate(im, mkproject(im, "outpaint", {"prompt": "wider", "source_asset": src["id"], "aspect": {"id": "16:9"}})["id"])
    sub = last_submission()
    res = finish_jobs(im, d["jobs"])[0]["assets"][0]
    t = wait_task(im, im.post(f"/api/imajutsu/assets/{res['id']}/keep-outside", json={}).json()["task"])
    assert t["status"] == "completed", t
    out = decode(content(im, t["asset"]["id"]), tmp_path, "o.bin")
    prepared = decode(uploaded(sub["pictures"][0]), tmp_path, "p.bin")
    w = out.shape[1]
    centre = slice(int(w * 0.43), int(w * 0.57))
    assert np.array_equal(out[:, centre], prepared[:, centre])
    assert np.array_equal(out[:, : int(w * 0.2)], decode(content(im, res["id"]), tmp_path, "r.bin")[:, : int(w * 0.2)])           # the new area is the generated one


# ---- validation ----------------------------------------------------------------------------------------------------------------------------------
def test_unsupported_combinations_are_plain_422s(im):
    src = up(im, png_of(gradient(120, 160)), "s.png")
    z = model_id(im, "z-image-turbo")
    r = im.post(f"/api/imajutsu/projects/{mkproject(im, 'edit', {'prompt': 'x', 'source_asset': src['id'], 'aspect': {'id': 'source'}, 'model': z})['id']}/generate", json={})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "plan_errors" and "cannot do Edit Image" in r.json()["detail"]["message"]
    assert r.json()["detail"]["plan"]["errors"]
    r = im.post(f"/api/imajutsu/projects/{mkproject(im, 'edit', {'prompt': 'x'})['id']}/generate", json={})
    assert r.status_code == 422 and "needs a picture" in r.json()["detail"]["message"]
    r = im.post(f"/api/imajutsu/projects/{mkproject(im, 'generate', {'prompt': ''})['id']}/generate", json={})
    assert r.status_code == 422 and "Write a prompt" in r.json()["detail"]["message"]
    r = im.post(f"/api/imajutsu/projects/{mkproject(im, 'generate', {'prompt': 'x', 'advanced': {'steps': 99}})['id']}/generate", json={})
    assert r.status_code == 422 and "steps must be between" in r.json()["detail"]["message"]
    qwen = model_id(im, "qwen-image-2.1")
    r = im.post(f"/api/imajutsu/projects/{mkproject(im, 'generate', {'prompt': 'x', 'model': qwen, 'advanced': {'denoise': 0.5}})['id']}/generate", json={})
    assert r.status_code == 200                                                                      # strength is ignored by a text-to-image run, with a warning
    assert fs.STATE["image_submissions"] and "denoise" not in last_submission()["simple"]
    assert len(fs.STATE["image_submissions"]) == 1


@pytest.mark.parametrize("bad", [{"mode": "paint"}, {"aspect": {"id": "21:9"}}, {"aspect": {"id": "custom", "w": 1, "h": 9}}, {"quality": "ultra"}, {"count": 9}, {"seed": 2**40},
                                 {"references": "x"}, {"references": [{"asset": "ma_nope"}]}, {"advanced": {"loras": []}}, {"advanced": {"steps": 0}}, {"selection": {"box": {"x": .9, "y": 0, "w": .5, "h": .5}}},
                                 {"outpaint": {"pad": {"left": -1}}}, {"adjustments": {"exposure": 9}}, {"preset": {"scenes": ["moonlight"]}}, {"size": {"output": {"width": 10, "height": 10}}},
                                 {"references": [{"asset": "ma_x", "role": "boss"}]}, {"prompt": 5}, {"model": "m_nope"}, {"source_asset": "ma_nope"}])
def test_the_spec_is_validated_on_the_server(im, bad):
    p = mkproject(im, "generate", {"prompt": "x"})
    r = im.put(f"/api/imajutsu/projects/{p['id']}", json={"spec": {"mode": "generate", "prompt": "x", **bad}})
    assert r.status_code == 422, (bad, r.text)
    assert isinstance(r.json()["detail"], dict) and r.json()["detail"]["message"]


def test_more_than_the_sanity_cap_of_pictures_is_refused_but_many_are_kept(im):
    pic = up(im, solid_png((1, 2, 3)), "r.png")
    many = [{"asset": pic["id"], "active": True} for _ in range(60)]
    p = mkproject(im, "generate", {"prompt": "x", "references": many})
    assert len(im.get(f"/api/imajutsu/projects/{p['id']}").json()["spec"]["references"]) == 60
    r = im.put(f"/api/imajutsu/projects/{p['id']}", json={"spec": {"mode": "generate", "prompt": "x", "references": [{"asset": pic["id"]}] * 201}})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "too_many_references"


def test_project_saves_are_optimistic(im):
    p = mkproject(im, "generate", {"prompt": "one"})
    r = im.put(f"/api/imajutsu/projects/{p['id']}", json={"spec": {"mode": "generate", "prompt": "two"}, "expected_updated_at": p["updated_at"]})
    assert r.status_code == 200
    stale = im.put(f"/api/imajutsu/projects/{p['id']}", json={"spec": {"mode": "generate", "prompt": "three"}, "expected_updated_at": p["updated_at"]})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale" and stale.json()["detail"]["project"]["spec"]["prompt"] == "two"


# ---- reopening ---------------------------------------------------------------------------------------------------------------------------------
def test_a_job_reopens_fully_and_reopening_twice_changes_nothing(im):
    src = up(im, png_of(gradient(200, 300)), "src.png")
    refs = [up(im, solid_png(c), f"r{i}.png") for i, c in enumerate(COLORS[:5])]
    m = np.zeros((300, 200, 3), np.uint8)
    m[50:150, 50:150] = 255
    mask = up(im, png_of(m), "mask.png")
    spec = {"prompt": "swap the jacket", "source_asset": src["id"], "mask_asset": mask["id"], "aspect": {"id": "source"}, "model": model_id(im, "flux2-klein-4b"), "seed": 9, "quality": "high",
            "references": [{"asset": r["id"], "role": ["identity", "lighting", None][i % 3], "preserve": "the red scarf" if i == 0 else "", "active": i != 1, "label": f"ref {i}"} for i, r in enumerate(refs)],
            "selection": {"box": None, "feather": 6, "invert": False, "keep_outside": True}, "adjustments": {"exposure": 0.3, "grain": 0.1}, "advanced": {"steps": 6, "denoise": 0.8}}
    p = mkproject(im, "replace_subject", spec)
    d = generate(im, p["id"])
    saved_spec = im.get(f"/api/imajutsu/projects/{p['id']}").json()["spec"]
    assert im.delete(f"/api/imajutsu/projects/{p['id']}").status_code == 200
    r1 = im.post(f"/api/imajutsu/jobs/{d['job']}/reopen")
    assert r1.status_code == 200, r1.text
    r2 = im.post(f"/api/imajutsu/jobs/{d['job']}/reopen")
    a, b = r1.json(), r2.json()
    assert a["project"]["id"] == b["project"]["id"] and a["project"]["spec"] == saved_spec == b["project"]["spec"] and a["dropped"] == [] == b["dropped"]
    assert [x["role"] for x in a["project"]["spec"]["references"]] == ["identity", "lighting", None, "identity", "lighting"] and a["project"]["spec"]["references"][0]["preserve"] == "the red scarf"
    assert a["project"]["source"]["id"] == src["id"] and a["project"]["mask"]["id"] == mask["id"]
    assert len([x for x in im.get("/api/imajutsu/projects").json()]) == 1                         # no duplicates
    # a picture that left the library is named, not guessed
    assert im.delete(f"/api/media/assets/{refs[2]['id']}").status_code == 200
    c = im.post(f"/api/imajutsu/jobs/{d['job']}/reopen").json()
    assert c["dropped"] == [refs[2]["id"]] and len(c["project"]["spec"]["references"]) == 4
    j1 = im.get(f"/api/imajutsu/jobs/{d['job']}").json()
    j2 = im.get(f"/api/imajutsu/jobs/{d['job']}").json()
    assert j1 == j2 and j1["imajutsu"]["spec"]["references"][2]["asset"] == refs[2]["id"]            # the job itself keeps its snapshot
    assert j1["imajutsu"]["accounting"]["sent_to_engine"] and j1["imajutsu"]["plan"]["native"]


# ---- installs that store nothing --------------------------------------------------------------------------------------------------------------
def test_no_save_install_generates_without_keeping_settings(im, tmp_path):
    assert im.put("/api/admin/settings/privacy.chat_retention_days", json={"value": 0}).status_code == 200
    src = up(im, png_of(gradient(160, 240)), "src.png")
    assert src["id"].startswith("me_")                                                           # held in memory only
    p = mkproject(im, "edit", {"prompt": "make it blue", "source_asset": src["id"], "aspect": {"id": "source"}})
    d = generate(im, p["id"])
    assert d["forced_unsaved"] is True
    done = finish_jobs(im, d["jobs"])[0]
    assert done["status"] == "completed" and done["params"] == {} and done["prompt"] == ""
    assert done["assets"] and done["assets"][0]["id"].startswith("me_") and done["assets"][0]["saved"] is False
    assert im.get("/api/imajutsu/jobs").json() == []                                             # nothing about the settings was written
    assert last_submission()["pictures"]                                                          # the engine did get the picture
    tx = generate(im, mkproject(im, "generate", {"prompt": "a lemon"})["id"])
    assert tx["forced_unsaved"] is True and finish_jobs(im, tx["jobs"])[0]["assets"][0]["id"].startswith("me_")


# ---- the pictures that are sent ------------------------------------------------------------------------------------------------------------------
def test_exif_orientation_is_applied_to_the_source_and_to_references(im, tmp_path):
    left_to_right = gradient(120, 60)                                                              # stored landscape: dark red on the left, bright red on the right
    src = up(im, with_orientation(jpeg_of(left_to_right), 6), "phone.jpg")                         # 'turn clockwise to view': shown upright as 60 x 120
    ref = up(im, with_orientation(jpeg_of(left_to_right), 8), "ref.jpg")                           # 'turn counter-clockwise': the dark side ends at the bottom
    assert (src["width"], src["height"]) == (60, 120)
    p = mkproject(im, "edit", {"prompt": "x", "source_asset": src["id"], "aspect": {"id": "source"}, "references": [{"asset": ref["id"]}], "model": model_id(im, "flux2-klein-4b")})
    generate(im, p["id"])
    sub = last_submission()
    pic = decode(uploaded(sub["pictures"][0]), tmp_path, "s.bin")
    assert pic.shape[1] * 2 == pic.shape[0] or abs(pic.shape[1] / pic.shape[0] - 0.5) < 0.02                      # upright, not sideways
    h = pic.shape[0]
    assert pic[: h // 8, :, 0].mean() < pic[-h // 8:, :, 0].mean()                                  # the stored left edge is at the top
    r = decode(uploaded(sub["pictures"][1]), tmp_path, "r.bin")
    assert r.shape[:2] == (120, 60) and r[: 120 // 8, :, 0].mean() > r[-120 // 8:, :, 0].mean()    # and for the reference the other way round
    assert sub["pictures"][1]["mime"] == "image/jpeg" and sub["pictures"][1]["name"] == "ref.jpg"


def test_big_references_are_scaled_down_and_formats_are_kept(im, tmp_path):
    big_png = up(im, png_of(gradient(3000, 2000)), "big.png")
    big_jpg = up(im, jpeg_of(gradient(2600, 1300)), "big.jpg")
    p = mkproject(im, "reference", {"prompt": "x", "aspect": {"id": "1:1"}, "model": model_id(im, "flux2-klein-4b"), "references": [{"asset": big_png["id"]}, {"asset": big_jpg["id"]}]})
    generate(im, p["id"])
    a, b = last_submission()["pictures"]
    assert (a["width"], a["height"], a["mime"]) == (2048, 1365, "image/png") or (a["width"] == 2048 and abs(a["height"] - 1365) <= 1)
    assert b["mime"] == "image/jpeg" and b["width"] == 2048 and b["height"] == 1024
    assert big_png["width"] == 3000                                                                  # the library picture is untouched
    assert len(content(im, big_png["id"])) > a["size"] // 4


def test_the_plan_and_the_engine_get_each_models_own_default_steps(im):
    for alias, steps, guidance in [("flux2-klein-4b", 4, 1.0), ("z-image-turbo", 9, 0.0)]:
        p = mkproject(im, "generate", {"prompt": "a green car on a hill", "model": model_id(im, alias)})
        plan = im.post(f"/api/imajutsu/projects/{p['id']}/plan", json={}).json()["plan"]
        assert plan["steps"] == steps and plan["guidance"] == guidance
        generate(im, p["id"])
        assert last_submission()["simple"]["steps"] == steps and last_submission()["simple"]["guidance"] == guidance


def test_best_realism_tier_sends_qwen_its_plain_prompt_and_own_defaults(im):
    p = mkproject(im, "iphone_photo", {"prompt": "a family barbecue", "preset": {"id": "iphone_photo", "scenes": ["outdoor_day"], "finish": False, "tier": "best"}})
    generate(im, p["id"])
    sub = last_submission()
    assert sub["model"] == "qwen-image-2512" and sub["simple"]["steps"] == 30 and sub["simple"]["guidance"] == 4.0 and (sub["simple"]["width"], sub["simple"]["height"]) == (720, 1280)
    pr = sub["simple"]["prompt"]
    assert pr.startswith("A photograph of a family barbecue.") and not any(w in pr.lower() for w in ("snapshot", "phone", "9:16", "selfie", "candid"))
