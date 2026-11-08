"""Imajutsu: the plan (pure), the routing table and the iPhone Photo prompt builder. No server, no engine: facts in, plan out."""
import json

import pytest

from semechka import imajutsu_plan as P
from semechka import imajutsu_presets as presets
from semechka import imajutsu_routing as R
from semechka import imajutsu_svc as svc

DOC = R.load()["doc"]


def facts(engine_id, modes, refs=0, **kw):
    return {"model_id": "m_" + engine_id[:8], "alias": engine_id, "engine_id": engine_id, "modes": modes, "references": refs, "negative_prompt": kw.get("neg", False), "schedulers": [],
            "limits": kw.get("limits", {"steps": [1, 50], "guidance": [0, 10], "min_side": 256, "max_side": 2048, "multiple": 16}), "defaults": {"steps": 4, "guidance": 1.0}}


KLEIN = facts("flux2-klein-4b", ["txt2img", "edit", "inpaint"], 4)
QWEN_EDIT = facts("qwen-image-edit-2511", ["edit"], 3, neg=True, limits={"steps": [1, 100], "guidance": [0, 15], "min_side": 256, "max_side": 2048, "multiple": 16})
ZIMAGE = facts("z-image-turbo", ["txt2img", "img2img", "inpaint"], 0, neg=True)
SRC = {"id": "ma_src", "width": 3000, "height": 4000, "mime": "image/png", "name": "src.png"}


def spec(mode="generate", **kw):
    s = svc.default_spec(mode)
    s["prompt"] = "a red bicycle"
    for k, v in kw.items():
        s[k] = v
    return s


def run(s, model, mode=None, source=None, mask=None, entry="auto"):
    mode = mode or s["mode"]
    e = R.entry_for(DOC, mode, model["engine_id"]) if entry == "auto" else entry
    return P.plan(s, {"mode": mode, "model": model, "entry": e, "recommended": True, "source": source, "mask": mask, "avoid_words": R.avoid_words(DOC), "pixels": DOC["pixels"]})


# ---- sizes ----------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("aid,expect", [("9:16", (720, 1280)), ("16:9", (1280, 720)), ("1:1", (960, 960)), ("4:5", (832, 1040)), ("3:4", (816, 1088))])
def test_native_size_for_every_aspect_is_a_multiple_of_16_and_near_the_budget(aid, expect):
    w, h = P.ASPECTS[aid]
    p = run(spec(aspect={"id": aid, "w": w, "h": h}), KLEIN)
    n = p["native"]
    assert (n["width"], n["height"]) == expect and p["errors"] == []
    assert n["width"] % 16 == 0 and n["height"] % 16 == 0 and 0.8e6 <= n["width"] * n["height"] <= 1.0e6
    assert abs(n["width"] / n["height"] / (w / h) - 1) < 0.004
    assert p["output"] == {"width": n["width"], "height": n["height"], "scaling": "none", "note": p["output"]["note"]}


def test_computed_native_size_when_the_routing_file_has_none_for_that_shape():
    for w, h in [(1000, 700), (7, 5), (21, 9), (1, 2.5)]:
        p = run(spec(aspect={"id": "custom", "w": w, "h": h}), KLEIN)
        n = p["native"]
        assert n["width"] % 16 == 0 and n["height"] % 16 == 0 and 256 <= min(n["width"], n["height"]) and max(n["width"], n["height"]) <= 2048
        assert abs(n["width"] / n["height"] / (w / h) - 1) < 0.02, (w, h, n)
        assert 0.6e6 <= n["width"] * n["height"] <= 1.3e6, (w, h, n)


def test_limits_come_from_the_engine_not_from_the_code():
    small = facts("sdxl-turbo", ["txt2img", "img2img", "inpaint"], 0, neg=True, limits={"steps": [1, 10], "guidance": [0, 2], "min_side": 256, "max_side": 1024, "multiple": 8})
    p = run(spec(aspect={"id": "9:16", "w": 9, "h": 16}), small, entry=None)
    n = p["native"]
    assert n["multiple"] == 8 and n["width"] % 8 == 0 and n["height"] % 8 == 0 and max(n["width"], n["height"]) <= 1024
    assert abs(n["width"] / n["height"] - 9 / 16) < 0.01
    # an engine that says nothing about multiples is treated as 16
    bare = {**KLEIN, "limits": {}}
    assert run(spec(), bare)["native"]["multiple"] == 16


def test_output_native_and_scaling():
    base = spec(aspect={"id": "9:16", "w": 9, "h": 16})
    up = run({**base, "size": {"native": None, "output": {"width": 1440, "height": 2560}}}, KLEIN)
    assert up["output"]["scaling"] == "upscale" and (up["native"]["width"], up["native"]["height"]) == (720, 1280) and "no new detail" in up["output"]["note"]
    down = run({**base, "size": {"native": None, "output": {"width": 360, "height": 640}}}, KLEIN)
    assert down["output"]["scaling"] == "downscale" and down["errors"] == []
    wrong = run({**base, "size": {"native": None, "output": {"width": 1000, "height": 1000}}}, KLEIN)
    assert any("different shape" in e for e in wrong["errors"])
    odd = run({**base, "size": {"native": {"width": 721, "height": 1280}, "output": None}}, KLEIN)
    assert any("multiples of 16" in e for e in odd["errors"])
    ok = run({**base, "size": {"native": {"width": 576, "height": 1024}, "output": None}}, KLEIN)
    assert ok["errors"] == [] and ok["native"]["why"] == "Chosen by you."


def test_source_shape_and_cover_warning():
    p = run(spec("edit", prompt="make it red", aspect={"id": "source", "w": None, "h": None}), QWEN_EDIT, source=SRC)
    assert p["errors"] == [] and p["warnings"] == [] and abs(p["native"]["width"] / p["native"]["height"] - 0.75) < 0.01
    cut = run(spec("edit", prompt="make it red", aspect={"id": "16:9", "w": 16, "h": 9}), QWEN_EDIT, source=SRC)
    assert any("cut in the centre" in w for w in cut["warnings"])


def test_steps_guidance_and_scheduler_are_checked_against_the_model():
    s = spec(prompt="x", advanced={"steps": 80})
    assert any("steps must be between 1 and 50" in e for e in run(s, KLEIN)["errors"])
    s = spec(prompt="x", advanced={"guidance": 11})
    assert any("guidance" in e for e in run(s, KLEIN)["errors"])
    s = spec(prompt="x", advanced={"scheduler": "euler"})
    assert any("scheduler" in e for e in run(s, KLEIN)["errors"])
    p = run(spec(prompt="x", quality="high"), KLEIN)
    assert p["steps"] == 8 and run(spec(prompt="x", quality="draft"), KLEIN)["steps"] == 2
    assert run(spec(prompt="x", negative="blurry"), KLEIN)["prompt"]["negative_sent"] is False
    assert any("negative prompt" in w for w in run(spec(prompt="x", negative="blurry"), KLEIN)["warnings"])
    assert run(spec(prompt="x", negative="blurry"), ZIMAGE, entry=None)["prompt"]["negative_sent"] is True


# ---- reference accounting ---------------------------------------------------------------------------------------------------------------------
def refs(n, active_from=0):
    return [{"asset": f"ma_{i:02d}", "role": None, "preserve": "", "active": i >= active_from, "label": ""} for i in range(n)]


@pytest.mark.parametrize("model,mode,cap_for_refs", [(KLEIN, "edit", 3), (QWEN_EDIT, "edit", 2), (KLEIN, "reference", 4), (QWEN_EDIT, "reference", 3), (KLEIN, "replace_subject", 3)])
@pytest.mark.parametrize("n", [1, 3, 4, 5, 9, 12])
def test_references_are_used_in_order_up_to_the_limit_and_the_rest_are_listed(model, mode, cap_for_refs, n):
    needs_src = P.MODE_DEFS[mode]["source"]
    s = spec(mode, prompt="swap", references=refs(n), aspect={"id": "source", "w": None, "h": None} if needs_src else {"id": "9:16", "w": 9, "h": 16})
    p = run(s, model, source=SRC if needs_src else None)
    r = p["references"]
    k = min(n, cap_for_refs)
    assert [u["asset"] for u in r["used"]] == [f"ma_{i:02d}" for i in range(k)]
    assert [u["asset"] for u in r["not_used"]] == [f"ma_{i:02d}" for i in range(k, n)]
    assert all("takes at most" in u["reason"] for u in r["not_used"])
    assert r["pictures"] == k + (1 if needs_src else 0) <= model["references"]
    assert r["limit"] == model["references"] and r["source_counts"] is needs_src
    assert p["errors"] == []
    # one sentence per used picture (+ the line about the source), numbered the way the engine receives the pictures
    nums = [x["image"] for x in p["prompt"]["sentences"]]
    assert nums == list(range(1, r["pictures"] + 1))


def test_switched_off_pictures_are_listed_and_do_not_take_a_slot():
    s = spec("edit", prompt="swap", references=refs(6, active_from=3), aspect={"id": "source", "w": None, "h": None})
    r = run(s, KLEIN, source=SRC)["references"]
    assert [u["asset"] for u in r["used"]] == ["ma_03", "ma_04", "ma_05"] and [u["asset"] for u in r["not_used"]] == ["ma_00", "ma_01", "ma_02"]
    assert {u["reason"] for u in r["not_used"]} == {"Switched off in the project."}


def test_a_mode_without_references_lists_them_all_with_the_reason():
    for mode, model, why in [("generate", KLEIN, "does not use reference pictures"), ("restyle", ZIMAGE, "cannot take reference pictures")]:
        s = spec(mode, prompt="a cat", references=refs(2), aspect={"id": "source", "w": None, "h": None} if mode == "restyle" else {"id": "1:1", "w": 1, "h": 1})
        e = R.entry_for(DOC, mode, model["engine_id"])
        p = run(s, model, source=SRC if mode == "restyle" else None, entry=e)
        assert p["references"]["used"] == [] and len(p["references"]["not_used"]) == 2 and why in p["references"]["not_used"][0]["reason"], p["references"]


def test_reference_mode_needs_a_picture_and_replace_subject_needs_one_too():
    p = run(spec("reference", references=[]), KLEIN)
    assert any("at least 1 reference picture" in e for e in p["errors"])
    p = run(spec("replace_subject", prompt="the car", references=[], aspect={"id": "source", "w": None, "h": None}), KLEIN, source=SRC)
    assert any("at least 1 reference picture" in e for e in p["errors"])


def test_role_sentences_are_words_in_the_prompt_and_say_so():
    rs = [{"asset": "ma_a", "role": "identity", "preserve": "", "active": True, "label": "Anna"},
          {"asset": "ma_b", "role": "lighting", "preserve": "the red scarf", "active": True, "label": ""},
          {"asset": "ma_c", "role": None, "preserve": "", "active": True, "label": ""}]
    p = run(spec("edit", prompt="Put her on a beach", references=rs, aspect={"id": "source", "w": None, "h": None}), KLEIN, source=SRC)
    sent = [x["sentence"] for x in p["prompt"]["sentences"]]
    assert sent[0] == "Image 1 is the picture to edit."
    assert "identity" in sent[1] and "image 2" in sent[1] and "Anna" in sent[1]
    assert "lighting of image 3" in sent[2] and "red scarf" in sent[2]
    assert "image 4 as a reference" in sent[3]
    assert p["prompt"]["final"].startswith("Put her on a beach") and all(x in p["prompt"]["final"] for x in sent)
    r = run(spec("replace_subject", prompt="the dog", references=refs(1), aspect={"id": "source", "w": None, "h": None}), KLEIN, source=SRC)
    assert "Replace the subject in image 1 with the subject from image 2" in r["prompt"]["sentences"][1]["sentence"]


def test_outpaint_geometry():
    s = spec("outpaint", aspect={"id": "16:9", "w": 16, "h": 9})
    p = run(s, KLEIN, source={"id": "ma_s", "width": 900, "height": 1200, "mime": "image/png", "name": "s"})
    og = p["outpaint"]
    assert og["canvas"]["width"] * 9 >= og["canvas"]["height"] * 16 - 16 and og["pad"]["left"] + og["pad"]["right"] == og["canvas"]["width"] - 900 and og["pad"]["top"] == 0
    assert abs(p["native"]["width"] / p["native"]["height"] - og["canvas"]["width"] / og["canvas"]["height"]) < 0.01
    t = run(spec("outpaint", outpaint={"pad": None, "target": {"width": 1200, "height": 1200}, "anchor": {"x": 0.0, "y": 0.5}, "overlap": 0}), KLEIN,
            source={"id": "ma_s", "width": 900, "height": 1200, "mime": "image/png", "name": "s"})
    assert t["outpaint"]["pad"] == {"left": 0, "top": 0, "right": 300, "bottom": 0}
    small = run(spec("outpaint", outpaint={"pad": None, "target": {"width": 800, "height": 800}, "anchor": {"x": 0.5, "y": 0.5}, "overlap": 0}), KLEIN,
                source={"id": "ma_s", "width": 900, "height": 1200, "mime": "image/png", "name": "s"})
    assert any("smaller than the picture" in e for e in small["errors"])
    assert "Continue the scene" in run(spec("outpaint", prompt="", outpaint={"pad": {"left": 0, "top": 0, "right": 300, "bottom": 0}, "target": None, "anchor": {"x": .5, "y": .5}, "overlap": 0}), KLEIN,
                                       source={"id": "ma_s", "width": 900, "height": 1200, "mime": "image/png", "name": "s"})["prompt"]["final"]


def test_mode_requirements_and_unsupported_combinations():
    assert any("needs a picture" in e for e in run(spec("edit", prompt="x"), QWEN_EDIT)["errors"])
    assert any("needs a selection" in e for e in run(spec("inpaint", prompt="x", aspect={"id": "source", "w": None, "h": None}), KLEIN, source=SRC)["errors"])
    assert run(spec("inpaint", prompt="x", selection={"box": {"x": .1, "y": .1, "w": .5, "h": .5}, "feather": 0, "invert": False, "keep_outside": None},
                    aspect={"id": "source", "w": None, "h": None}), KLEIN, source=SRC)["errors"] == []
    # a model that cannot do the engine mode is refused with the model's own list of modes
    p = run(spec("edit", prompt="x", aspect={"id": "source", "w": None, "h": None}), ZIMAGE, source=SRC, entry=None)
    assert any("cannot do Edit Image" in e and "txt2img" in e for e in p["errors"])
    assert any("Write a prompt" in e for e in run(spec("generate", prompt=""), KLEIN)["errors"])


def test_upscale_is_scaling_and_never_ai():
    p = P.plan(spec("upscale", size={"native": None, "output": {"width": 4500, "height": 6000}}), {"mode": "upscale", "model": None, "source": SRC})
    assert p["errors"] == [] and p["output"]["scaling"] == "upscale" and p["scaling"]["ai"] is False and "not AI" in p["output"]["note"] and p["model"] is None
    same = P.plan(spec("upscale", size={"native": None, "output": {"width": 3000, "height": 4000}}), {"mode": "upscale", "model": None, "source": SRC})
    assert any("already" in e for e in same["errors"])
    bad = P.plan(spec("upscale", size={"native": None, "output": {"width": 6000, "height": 6000}}), {"mode": "upscale", "model": None, "source": SRC})
    assert any("different shape" in e for e in bad["errors"])


# ---- the iPhone Photo prompt builder ---------------------------------------------------------------------------------------------------------------
ALL_SCENES = list(presets.SCENES)
AVOID = R.avoid_words(DOC)


def test_klein_gets_natural_sentences_and_no_claims_about_the_file():
    b = presets.build_prompt("A woman at a bus stop", ALL_SCENES)
    assert b["final"].startswith("A woman at a bus stop.") and len(b["sentences"]) == len(ALL_SCENES) and b["plain"] is False
    low = b["final"].lower()
    assert "exif" not in low and "iphone" not in low and "9:16" not in low and "shot on" not in low
    assert "handheld phone photo" in low                            # a look, not a claim
    for s in ALL_SCENES:
        assert presets.SCENES[s]["sentence"] in b["final"]


def test_z_image_never_gets_phone_wording_from_the_builder():
    b = presets.build_prompt("A woman at a bus stop", ALL_SCENES, avoid_words=AVOID, model_alias="Z-Image Turbo")
    assert b["plain"] and presets.forbidden_in(b["final"], AVOID) == [] and b["warnings"] == []
    assert "9:16" not in b["final"] and "snapshot" not in b["final"].lower() and "exif" not in b["final"].lower()
    # a person's own forbidden words are left alone but reported
    w = presets.build_prompt("phone snapshot of a street, 9:16", ["night"], avoid_words=AVOID, model_alias="Z-Image Turbo")
    assert "phone snapshot of a street, 9:16." in w["final"] and w["warnings"] and "'phone'" in w["warnings"][0] and "'9:16'" in w["warnings"][0]


def test_the_builder_through_the_plan_follows_the_routing_flag():
    s = spec("iphone_photo", prompt="a dog in the park", preset={"id": "iphone_photo", "scenes": ["outdoor_day", "casual"], "finish": True})
    k = run(s, KLEIN)
    z = run(s, ZIMAGE, entry=R.entry_for(DOC, "iphone_photo", "z-image-turbo"))
    assert k["prompt"]["preset"]["plain"] is False and z["prompt"]["preset"]["plain"] is True
    assert presets.forbidden_in(z["prompt"]["final"], AVOID) == [] and "phone" in k["prompt"]["final"].lower()
    assert k["prompt"]["preset"]["finish"] is True and k["prompt"]["preset"]["finish_recipe"] == presets.FINISH_RECIPE
    assert (k["native"]["width"], k["native"]["height"]) == (720, 1280) and k["count"] == 1
    off = run({**s, "preset": {"id": "iphone_photo", "scenes": [], "finish": False}}, KLEIN)
    assert off["prompt"]["preset"]["finish_recipe"] is None


# ---- the routing table is data ----------------------------------------------------------------------------------------------------------------------
def test_routing_file_is_valid_and_names_every_mode():
    assert set(DOC["modes"]) == set(R.MODE_IDS)
    for mode in R.MODE_IDS:
        for e in R.entries(DOC, mode):
            assert e["why"] and e["evidence"], (mode, e["engine_id"])
    assert R.entries(DOC, "generate")[0]["engine_id"] == "flux2-klein-4b" and R.entries(DOC, "iphone_photo")[0]["engine_id"] == "flux2-klein-4b"
    assert R.entries(DOC, "iphone_photo")[2]["avoid_phone_words"] is True and R.entries(DOC, "iphone_photo")[1]["engine_id"] == "qwen-image-2512"
    assert [e["engine_id"] for e in R.entries(DOC, "upscale")] == []


def test_a_broken_routing_file_is_rejected(tmp_path):
    (tmp_path / "imajutsu_routing.json").write_text(json.dumps({"modes": {"generate": {"models": [{"why": "no id"}]}}}))
    got = R.load(tmp_path)
    assert got["source"] == "packaged" and "not usable" in got["problem"]
    (tmp_path / "imajutsu_routing.json").write_text("{ nope")
    assert R.load(tmp_path)["source"] == "packaged"


def test_standard_steps_are_the_models_own_defaults_never_thirty():
    z = {**ZIMAGE, "defaults": {"steps": 9, "guidance": 0.0}, "limits": {"steps": [1, 30], "guidance": [0, 3], "min_side": 256, "max_side": 2048, "multiple": 16}}
    p = run(spec(prompt="a car"), z, entry=R.entry_for(DOC, "generate", "z-image-turbo"))
    assert p["steps"] == 9 and p["guidance"] == 0.0
    k = run(spec(prompt="a car"), KLEIN)
    assert k["steps"] == 4 and k["guidance"] == 1.0
    assert run(spec(prompt="a car", quality="draft"), z, entry=R.entry_for(DOC, "generate", "z-image-turbo"))["steps"] == 4
    assert run(spec(prompt="a car", quality="high"), z, entry=R.entry_for(DOC, "generate", "z-image-turbo"))["steps"] == 14
    # an engine that states no default: nothing is invented, nothing is sent
    none = {**KLEIN, "defaults": {}}
    assert run(spec(prompt="a car"), none)["steps"] is None and run(spec(prompt="a car", quality="draft"), none)["steps"] == 2
    assert all("standard" not in (e.get("quality") or {}) for m in R.MODE_IDS for e in R.entries(DOC, m))


QWEN = facts("qwen-image-2512", ["txt2img", "img2img", "inpaint"], 0, neg=True, limits={"steps": [1, 100], "guidance": [0, 15], "min_side": 256, "max_side": 2048, "multiple": 16})
QWEN["defaults"] = {"steps": 50, "guidance": 4.0}


def test_qwen_gets_plain_wording_a_photograph_of_and_no_selfie_unless_chosen():
    e = R.entry_for(DOC, "iphone_photo", "qwen-image-2512")
    assert e["wording"] == "plain" and e["tier"] == "best" and "snapshot" in e["avoid_words"]
    s = spec("iphone_photo", prompt="A man fixing a Hilux in a driveway", preset={"id": "iphone_photo", "scenes": ["outdoor_day", "casual"], "finish": False, "tier": None})
    p = run(s, QWEN, entry=e)
    f = p["prompt"]["final"]
    assert f.startswith("A photograph of a man fixing a Hilux in a driveway.") and p["steps"] == 30 and p["guidance"] == 4.0   # the measured iPhone Photo count, not the engine default 50
    for bad in ("snapshot", "phone", "9:16", "candid", "selfie", "exif", "mirror"):
        assert bad not in f.lower(), bad
    chosen = run({**s, "preset": {**s["preset"], "scenes": ["selfie", "mirror"]}}, QWEN, entry=e)["prompt"]["final"]
    assert "arm's length" in chosen and "mirror" in chosen and "snapshot" not in chosen.lower() and "phone" not in chosen.lower()
    already = run({**s, "prompt": "A photo of a dog"}, QWEN, entry=e)["prompt"]["final"]
    assert already.startswith("A photo of a dog.")
    w = run({**s, "prompt": "candid phone snapshot of a dog"}, QWEN, entry=e)
    assert w["prompt"]["final"].startswith("A photograph of candid phone snapshot of a dog.") and any("'snapshot'" in x for x in w["warnings"])
    k = run(s, KLEIN)["prompt"]["final"]
    assert k.startswith("A man fixing a Hilux in a driveway.") and "handheld phone photo" in k       # klein keeps natural sentences


def test_zimage_wording_never_contains_phone():
    z = run(spec("iphone_photo", prompt="a red pickup", preset={"id": "iphone_photo", "scenes": list(presets.SCENES), "finish": False, "tier": None}), ZIMAGE,
            entry=R.entry_for(DOC, "iphone_photo", "z-image-turbo"))
    assert z["prompt"]["final"].startswith("A photograph of a red pickup.") and presets.forbidden_in(z["prompt"]["final"], AVOID) == []


def test_qwen_iphone_photo_uses_the_measured_30_steps_but_plain_generate_keeps_the_engine_default():
    e = R.entry_for(DOC, "iphone_photo", "qwen-image-2512")
    assert e["preset_steps"] == {"iphone_photo": 30} and "86 s" in e["preset_steps_evidence"]
    s = spec("iphone_photo", prompt="A man fixing a Hilux", preset={"id": "iphone_photo", "scenes": [], "finish": False, "tier": None})
    assert run(s, QWEN, entry=e)["steps"] == 30
    assert run({**s, "quality": "draft"}, QWEN, entry=e)["steps"] == 20 and run({**s, "quality": "high"}, QWEN, entry=e)["steps"] == 70
    g = run(spec(prompt="a red bicycle"), QWEN, entry=R.entry_for(DOC, "generate", "qwen-image-2512"))
    assert g["steps"] == 50


def _inpaint(prompt, model=None, **kw):
    sel = {"box": {"x": .1, "y": .1, "w": .5, "h": .5}, "feather": 0, "invert": False, "keep_outside": None}
    return run(spec("inpaint", prompt=prompt, selection=sel, aspect={"id": "source", "w": None, "h": None}, **kw), model or KLEIN, source=SRC)


def test_klein_inpaint_words_become_an_instruction_and_use_the_measured_8_steps():
    """Measured 2026-11-08: FLUX.2 klein draws nothing from the bare phrase 'A potted green plant' in the selection, and draws it when told what to do."""
    p = _inpaint("A potted green plant")
    assert p["errors"] == [] and p["steps"] == 8
    assert p["prompt"]["final"] == "Fill the selected area with a potted green plant. Keep the lighting, perspective and style of the rest of the picture."
    assert _inpaint("wooden bench.")["prompt"]["final"].startswith("Fill the selected area with wooden bench. ")
    assert _inpaint("the old oak tree!")["prompt"]["final"].startswith("Fill the selected area with the old oak tree. ")


def test_other_models_and_modes_keep_their_words_and_the_engine_default_steps():
    assert _inpaint("A potted green plant", ZIMAGE)["prompt"]["final"] == "A potted green plant"
    g = run(spec("generate", prompt="A potted green plant"), KLEIN)
    assert g["prompt"]["final"] == "A potted green plant" and g["steps"] == 4
    e = run(spec("edit", prompt="Make the sky orange", aspect={"id": "source", "w": None, "h": None}), KLEIN, source=SRC)
    assert e["prompt"]["final"] == "Make the sky orange" and e["steps"] == 4
