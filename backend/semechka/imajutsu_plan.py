"""Imajutsu: the plan. A pure function from (the person's spec, the facts of the model that would run it, the source picture) to what will really happen: which
engine mode, the size the model works at (native), the size the person wants (output) and whether getting there is scaling, the final prompt with a sentence for
every reference picture, which pictures are sent and which are not (and why), the settings, warnings and errors. No I/O: the page calls it while a slider moves.

Honesty rules (docs/IMAJUTSU.md): no picture is dropped silently (it is either sent or listed as not used with the reason); reference "roles" are only words in
the prompt; scaling is called scaling; a time estimate appears only when the engine measured something (that part is added by imajutsu_svc).
"""
from __future__ import annotations

import math

from . import imajutsu_presets as presets
from .imajutsu_routing import DEFAULT_PIXELS, MODE_DEFS

PLAN_VERSION = 1
ASPECTS = {"9:16": (9, 16), "16:9": (16, 9), "4:5": (4, 5), "3:4": (3, 4), "1:1": (1, 1)}
ASPECT_LABELS = {"9:16": "9:16 vertical", "16:9": "16:9 wide", "4:5": "4:5", "3:4": "3:4", "1:1": "1:1 square", "custom": "Custom", "source": "Same shape as the picture"}
ROLES = ("identity", "appearance", "composition", "lighting", "style")
QUALITY = ("draft", "standard", "high")
MIN_OUT, MAX_OUT, MAX_OUT_PIXELS = 64, 8192, 8192 * 4320
SHAPE_TOLERANCE = 0.015
PROMPT_MAX = 4000
DEFAULT_OUTPAINT_PROMPT = "Continue the scene naturally beyond the edges of the picture, matching its light, perspective and style."
DEFAULT_REFINE_PROMPT = "The same picture, unchanged in content and composition, with clean natural detail and texture."
DEFAULT_REFERENCE_PROMPT = "Make a new picture guided by the reference pictures."
ROLE_SENTENCE = {
    "identity": "Keep the identity (face and features) of the person in image {n}.",
    "appearance": "Use the appearance of the subject in image {n}: its shape, colours and details.",
    "composition": "Follow the composition and framing of image {n}.",
    "lighting": "Match the lighting of image {n}.",
    "style": "Use the visual style of image {n}.",
    None: "Use image {n} as a reference.",
}
REPLACE_SENTENCE = "Replace the subject in image 1 with the subject from image {n}."
SOURCE_SENTENCE = "Image 1 is the picture to edit."
MODE_IMAGE_USE = {"edit", "inpaint"}        # engine modes that take more than the source


# ---- engine mode and picture limits ----------------------------------------------------------------------------------------------------------------------
def resolve_engine_mode(mode: str, entry_mode: str | None, *, has_source: bool, has_mask: bool, has_refs: bool, model_modes: list[str]) -> str | None:
    """The engine mode this combination runs as, or None when the model cannot do it. (The routing entry may name its own for a mode.)"""
    if mode == "upscale":
        return entry_mode
    if mode == "advanced":
        if has_source and has_mask and "inpaint" in model_modes:
            return "inpaint"
        if (has_source or has_refs) and "edit" in model_modes:
            return "edit"
        if has_source and "img2img" in model_modes:
            return "img2img"
        if not has_source and "txt2img" in model_modes:
            return "txt2img"
        return None
    base = entry_mode or MODE_DEFS[mode]["engine_mode"]
    if mode == "replace_subject" and has_mask and "inpaint" in model_modes:
        return "inpaint"
    return base


def picture_cap(engine_mode: str | None, references: int) -> int:
    """How many pictures (the source included) the engine takes for this mode."""
    if engine_mode in (None, "txt2img"):
        return 0
    if engine_mode in MODE_IMAGE_USE:
        return max(1, int(references or 0))
    return 1


def account_references(refs: list[dict], *, cap: int, source_counts: bool, allowed: bool, why_not: str, alias: str) -> tuple[list[dict], list[dict]]:
    """(used, not_used). Only ACTIVE pictures, in the project's order, up to ``cap`` minus the source; the rest are listed with the reason. Nothing is dropped silently."""
    slots = max(0, cap - (1 if source_counts else 0))
    used: list[dict] = []
    not_used: list[dict] = []
    for i, r in enumerate(refs):
        if not r.get("active", True):
            not_used.append({"asset": r["asset"], "position": i + 1, "reason": "Switched off in the project."})
        elif not allowed or slots == 0:
            not_used.append({"asset": r["asset"], "position": i + 1, "reason": why_not})
        elif len(used) >= slots:
            extra = " (the source picture is one of them)" if source_counts else ""
            not_used.append({"asset": r["asset"], "position": i + 1,
                             "reason": f"{alias} takes at most {cap} picture{'s' if cap != 1 else ''}{extra}; this one comes after them."})
        else:
            used.append({"asset": r["asset"], "position": i + 1, "role": r.get("role"), "label": r.get("label") or "", "preserve": r.get("preserve") or ""})
    return used, not_used


def reference_sentences(used: list[dict], *, source_counts: bool, mode: str) -> list[dict]:
    """One sentence per used picture. The engine numbers the pictures in the order it receives them: the source (when there is one) is image 1."""
    out = []
    if used and source_counts:
        out.append({"ref": "source", "position": 0, "image": 1, "role": None, "sentence": SOURCE_SENTENCE})
    for k, r in enumerate(used):
        n = k + 1 + (1 if source_counts else 0)
        if mode == "replace_subject" and not r.get("role"):
            text = REPLACE_SENTENCE.format(n=n)
        else:
            text = ROLE_SENTENCE[r.get("role") if r.get("role") in ROLES else None].format(n=n)
        if r.get("label"):
            text = text[:-1] + f" (it shows: {r['label']})."
        if r.get("preserve"):
            text += f" Keep {r['preserve'].rstrip('. ')} from image {n} unchanged."
        out.append({"ref": r["asset"], "position": r["position"], "image": n, "role": r.get("role"), "sentence": text})
    return out


# ---- sizes ---------------------------------------------------------------------------------------------------------------------------------------------------
def _ratio(spec: dict, source: dict | None, canvas: tuple[int, int] | None) -> tuple[float | None, str, str | None]:
    """(width / height, aspect id, error)."""
    a = spec["aspect"]
    if canvas:
        return canvas[0] / canvas[1], "source", None
    if a["id"] == "source":
        if not source:
            return None, "source", "Choose a picture first: the shape follows it."
        return source["width"] / source["height"], "source", None
    if a["id"] == "custom":
        r = a["w"] / a["h"]
        if not 0.2 <= r <= 5:
            return None, "custom", "A custom shape must be between 1:5 and 5:1."
        return r, "custom", None
    w, h = ASPECTS[a["id"]]
    return w / h, a["id"], None


def pick_native(ratio: float, aspect_id: str, entry: dict | None, limits: dict, pixels: int) -> tuple[int, int, str]:
    """The size the model works at: multiples of ``limits.multiple`` (16 when the engine says nothing), inside min/max side, about ``pixels`` pixels, the shape as
    exact as the multiples allow. A size written in the routing file for this aspect wins when it is valid."""
    mult = int(limits.get("multiple") or 16)
    lo, hi = int(limits.get("min_side") or 256), int(limits.get("max_side") or 2048)
    fixed = ((entry or {}).get("native") or {}).get(aspect_id)
    if fixed and all(v % mult == 0 and lo <= v <= hi for v in fixed):
        return int(fixed[0]), int(fixed[1]), f"{fixed[0]}×{fixed[1]} is the size this model is run at for {ASPECT_LABELS.get(aspect_id, aspect_id)} (multiples of {mult})."
    pixels = min(int(pixels or DEFAULT_PIXELS), hi * hi)
    best = None
    for w in range(lo, hi + 1, mult):
        h = min(hi, max(lo, int(round(w / ratio / mult)) * mult))
        err = abs(w / h / ratio - 1)
        score = (0 if err <= 0.004 else err, abs(w * h - pixels) / pixels)
        if best is None or score < best[0]:
            best = (score, w, h, err)
    _, w, h, err = best
    why = f"{w}×{h}: about {w * h / 1e6:.2f} megapixels in multiples of {mult}"
    if err > 0.004:
        why += f"; the shape is {err * 100:.1f}% off {ASPECT_LABELS.get(aspect_id, 'the one you asked for')} because of those multiples"
    return w, h, why + "."


def scaling_kind(native: tuple[int, int], out: tuple[int, int]) -> str:
    npx, opx = native[0] * native[1], out[0] * out[1]
    return "none" if out == native else ("upscale" if opx > npx else "downscale")


def outpaint_geometry(spec: dict, source: dict) -> tuple[dict | None, str | None]:
    """The padding (pixels of the source picture) and the canvas, from a stated padding, a target size or a shape. -> ({"pad", "canvas", "anchor"}, error)"""
    o = spec.get("outpaint") or {}
    w, h = source["width"], source["height"]
    if o.get("pad"):
        p = o["pad"]
    else:
        tw = th = None
        if o.get("target"):
            tw, th = o["target"]["width"], o["target"]["height"]
        elif spec["aspect"]["id"] not in ("source",):
            ratio, _id, err = _ratio(spec, None, None)
            if err or ratio is None:
                return None, err
            tw, th = (max(w, int(math.ceil(h * ratio))), h) if ratio >= w / h else (w, max(h, int(math.ceil(w / ratio))))
        if tw is None:
            return None, "Say how far to extend the picture: a size, a shape or the pixels to add on each side."
        if tw < w or th < h:
            return None, f"The new size {tw} × {th} is smaller than the picture ({w} × {h}); outpainting only adds."
        ax, ay = (o.get("anchor") or {}).get("x", 0.5), (o.get("anchor") or {}).get("y", 0.5)
        left, top = int(round((tw - w) * ax)), int(round((th - h) * ay))
        p = {"left": left, "top": top, "right": tw - w - left, "bottom": th - h - top}
    if min(p.values()) < 0:
        return None, "The padding cannot be negative."
    cw, ch = w + p["left"] + p["right"], h + p["top"] + p["bottom"]
    if (cw, ch) == (w, h):
        return None, "Nothing to add: the new size equals the picture."
    if cw * ch > MAX_OUT_PIXELS or max(cw, ch) > MAX_OUT:
        return None, "The extended picture would be larger than 8K."
    return {"pad": p, "canvas": {"width": cw, "height": ch}, "overlap": int(o.get("overlap") or 0)}, None


def steps_for(quality: str, entry: dict | None, defaults: dict, limits: dict, preset: str | None = None) -> int | None:
    """The step count. ``standard`` is ALWAYS the engine's own default for the model (flux2-klein 4, z-image-turbo 9 ...), never a number of ours; draft and high come
    from the routing file or, failing that, half and double of it. None = the engine does not state a default: nothing is sent and the engine fills it in."""
    d = defaults.get("steps")
    if quality == "standard":
        # a MEASURED exception, recorded in the routing file with its evidence (e.g. Qwen-Image-2512 at 30 steps looks the same as at its own 50 for photographs and
        # takes 86 s instead of 145 s). Only for a named preset; the plain Generate Image mode keeps the engine's own default.
        ps = ((entry or {}).get("preset_steps") or {}).get(preset or "")
        if ps:
            lo, hi = (limits.get("steps") or [1, 200])[:2]
            return int(min(max(int(ps), lo), hi))
        return int(d) if d else None
    q = ((entry or {}).get("quality") or {}).get(quality)
    if q is None:
        if not d:
            return None
        q = max(1, math.ceil(int(d) / 2)) if quality == "draft" else int(d) * 2
    lo, hi = (limits.get("steps") or [1, 200])[:2]
    return int(min(max(q, lo), hi))


# ---- the plan --------------------------------------------------------------------------------------------------------------------------------------------------
def plan(spec: dict, ctx: dict) -> dict:
    mode = spec["mode"]
    md = MODE_DEFS[mode]
    model, entry = ctx.get("model"), ctx.get("entry")
    source, mask = ctx.get("source"), ctx.get("mask")
    errors: list[str] = []
    warnings: list[str] = list(ctx.get("warnings") or [])
    pln: dict = {"version": PLAN_VERSION, "mode": mode, "label": md["label"], "engine_mode": None, "model": None, "native": None, "output": None, "work": None, "steps": None,
                 "guidance": None, "scheduler": None, "denoise": None, "quality": spec["quality"], "count": spec["count"], "seeds": None,
                 "prompt": None, "references": {"used": [], "not_used": [], "limit": None, "source_counts": False, "pictures": 0},
                 "source": source, "mask": None, "outpaint": None, "estimate": None, "warnings": warnings, "errors": errors}
    active = [r for r in spec["references"] if r.get("active", True)]
    if spec["seed"] is not None:
        pln["seeds"] = [spec["seed"] + i for i in range(spec["count"])]
    # ---- upscale: no model at all --------------------------------------------------------------------------------------------------------------------------
    if mode == "upscale":
        return _plan_upscale(spec, ctx, pln)
    # ---- the model -------------------------------------------------------------------------------------------------------------------------------------------
    if model is None:
        errors.append(ctx.get("why_none") or f"No model available to you can do {md['label']}.")
        pln["references"]["not_used"] = [{"asset": r["asset"], "position": i + 1, "reason": "No model has been chosen yet."} for i, r in enumerate(spec["references"])]
        return pln
    if md["source"] and not source:
        errors.append(f"{md['label']} needs a picture to work from.")
    has_source = bool(source)
    has_mask = bool(mask) or bool((spec.get("selection") or {}).get("box"))
    em = resolve_engine_mode(mode, (entry or {}).get("engine_mode"), has_source=has_source, has_mask=has_mask, has_refs=bool(active), model_modes=model["modes"])
    pln["engine_mode"] = em
    pln["model"] = {"id": model["model_id"], "alias": model["alias"], "engine_id": model["engine_id"], "recommended": bool(ctx.get("recommended")), "routed": bool(entry and entry.get("routed", True)), "tier": (entry or {}).get("tier") if mode == "iphone_photo" else None,
                    "why": (entry or {}).get("why") or "Chosen by you; it is not on the recommended list for this mode.", "slow": bool((entry or {}).get("slow")),
                    "seconds_hint": (entry or {}).get("seconds_hint")}
    if em is None or (em not in model["modes"]):
        errors.append(f"{model['alias']} cannot do {md['label']} ({(em or 'the engine mode it needs')} is not among its modes: {', '.join(model['modes']) or 'none'}).")
    # ---- mode requirements -------------------------------------------------------------------------------------------------------------------------------
    if mode == "inpaint" and not has_mask:
        errors.append("Inpaint needs a selection: paint a mask or draw a box over the part to change.")
    if em == "inpaint" and mode == "replace_subject" and not has_mask:
        em = "edit"
    # ---- references --------------------------------------------------------------------------------------------------------------------------------------
    cap = picture_cap(em, model.get("references") or 0)
    source_counts = has_source and em in ("edit", "inpaint", "img2img")
    allowed = md["refs_ok"] and em in MODE_IMAGE_USE and cap - (1 if source_counts else 0) > 0
    if not md["refs_ok"]:
        why_not = f"{md['label']} does not use reference pictures."
    elif em == "img2img":
        why_not = f"{model['alias']} restyles from one picture (the source); it cannot take reference pictures."
    elif em == "txt2img":
        why_not = f"{md['label']} makes the picture from the words alone; use Reference Image to condition on pictures."
    else:
        why_not = f"{model['alias']} takes {cap} picture{'s' if cap != 1 else ''} and the source is one of them; there is no room for references." if source_counts \
            else f"{model['alias']} does not take reference pictures."
    used, not_used = account_references(spec["references"], cap=cap, source_counts=source_counts, allowed=allowed, why_not=why_not, alias=model["alias"])
    if len(used) < md["refs_min"]:
        n = md["refs_min"]
        errors.append(f"{md['label']} needs at least {n} reference picture{'s' if n != 1 else ''} switched on and usable" + (f"; {model['alias']} can use {cap - (1 if source_counts else 0)}." if em in MODE_IMAGE_USE else "."))
    pln["references"] = {"used": used, "not_used": not_used, "limit": cap or None, "source_counts": source_counts, "pictures": len(used) + (1 if source_counts else 0)}
    # ---- size ---------------------------------------------------------------------------------------------------------------------------------------------
    limits = model["limits"]
    canvas = None
    if mode == "outpaint" and source:
        og, err = outpaint_geometry(spec, source)
        if err:
            errors.append(err)
        else:
            pln["outpaint"] = og
            canvas = (og["canvas"]["width"], og["canvas"]["height"])
    ratio, aid, err = _ratio(spec, source if md["source"] or spec["aspect"]["id"] == "source" else None, canvas)
    if err:
        errors.append(err)
    nat_override = (spec.get("size") or {}).get("native")
    if ratio is not None:
        mult = int(limits.get("multiple") or 16)
        lo, hi = int(limits.get("min_side") or 256), int(limits.get("max_side") or 2048)
        if nat_override:
            nw, nh = nat_override["width"], nat_override["height"]
            if nw % mult or nh % mult or not (lo <= nw <= hi and lo <= nh <= hi):
                errors.append(f"The working size must be multiples of {mult} between {lo} and {hi} pixels for {model['alias']}.")
            why = "Chosen by you."
            if abs(nw / nh / ratio - 1) > SHAPE_TOLERANCE:
                warnings.append(f"The working size {nw}×{nh} does not have the shape you asked for; the picture is cut to fit it.")
        else:
            nw, nh, why = pick_native(ratio, aid, entry, limits, (entry or {}).get("pixels") or ctx.get("pixels") or DEFAULT_PIXELS)
        pln["native"] = {"width": nw, "height": nh, "why": why, "multiple": mult, "megapixels": round(nw * nh / 1e6, 3), "shape": aid}
        pln["work"] = {"width": nw, "height": nh}          # the size the engine is asked for
        out = (spec.get("size") or {}).get("output") or {"width": nw, "height": nh}
        ow, oh = out["width"], out["height"]
        kind = scaling_kind((nw, nh), (ow, oh))
        if abs((ow / oh) / (nw / nh) - 1) > SHAPE_TOLERANCE:
            errors.append(f"The output size {ow}×{oh} has a different shape from the working size {nw}×{nh}. Choose a size with the same shape.")
        note = {"none": "The model's own size; nothing is scaled.",
                "upscale": f"The model makes {nw}×{nh}. {ow}×{oh} is that picture scaled up afterwards (Lanczos, no new detail).",
                "downscale": f"The model makes {nw}×{nh}. {ow}×{oh} is that picture scaled down afterwards."}[kind]
        pln["output"] = {"width": ow, "height": oh, "scaling": kind, "note": note}
        if md["source"] and source and not canvas and abs(source["width"] / source["height"] / (nw / nh) - 1) > SHAPE_TOLERANCE:
            keep = min(1.0, (source["width"] / source["height"]) / (nw / nh)) if (source["width"] / source["height"]) > (nw / nh) else min(1.0, (nw / nh) / (source["width"] / source["height"]))
            warnings.append(f"The picture is not {ASPECT_LABELS.get(aid, 'that shape')}: it is cut in the centre to fit ({keep * 100:.0f}% of it is kept). "
                            "Choose 'Same shape as the picture' to keep all of it.")
    # ---- settings ------------------------------------------------------------------------------------------------------------------------------------------
    adv = spec.get("advanced") or {}
    defaults = model["defaults"]
    pln["steps"] = int(adv["steps"]) if adv.get("steps") is not None else steps_for(spec["quality"], entry, defaults, limits, mode)
    slo, shi = (limits.get("steps") or [1, 200])[:2]
    if pln["steps"] is not None and not slo <= pln["steps"] <= shi:
        errors.append(f"steps must be between {slo} and {shi} for {model['alias']}.")
    g = adv.get("guidance") if adv.get("guidance") is not None else defaults.get("guidance")
    pln["guidance"] = g
    glo, ghi = (limits.get("guidance") or [0, 30])[:2]
    if g is not None and not glo <= g <= ghi:
        errors.append(f"guidance must be between {glo:g} and {ghi:g} for {model['alias']}.")
    if adv.get("scheduler"):
        if adv["scheduler"] not in (model.get("schedulers") or []):
            errors.append(f"{model['alias']} does not offer the scheduler '{adv['scheduler']}'.")
        pln["scheduler"] = adv["scheduler"]
    if em in ("img2img", "inpaint"):
        pln["denoise"] = float(adv["denoise"]) if adv.get("denoise") is not None else (0.6 if em == "img2img" else 1.0)
    elif adv.get("denoise") is not None:
        warnings.append(f"Strength is not used: {model['alias']} works as an instruction editor in this mode.")
    # ---- the prompt ------------------------------------------------------------------------------------------------------------------------------------------
    pln["prompt"] = _prompt(spec, mode, model, entry, ctx, used, source_counts, warnings, errors)
    if mask:
        pln["mask"] = mask
    return pln


def _object_phrase(text: str) -> str:
    """The person's words as the end of a sentence: no final full stop, a leading article in lower case ('A potted plant' -> 'a potted plant')."""
    t = text.strip().rstrip(" .!?")
    first, _, rest = t.partition(" ")
    return (first.lower() + " " + rest) if first in ("A", "An", "The") and rest else t


def _prompt(spec: dict, mode: str, model: dict, entry: dict | None, ctx: dict, used: list[dict], source_counts: bool, warnings: list[str], errors: list[str]) -> dict:
    md = MODE_DEFS[mode]
    text = (spec.get("prompt") or "").strip()
    preset = None
    if mode == "iphone_photo":
        wording = (entry or {}).get("wording") or ("plain" if (entry or {}).get("avoid_phone_words") else "natural")
        avoid = ((entry or {}).get("avoid_words") or ctx.get("avoid_words") or []) if wording == "plain" else None
        b = presets.build_prompt(text, (spec.get("preset") or {}).get("scenes") or [], avoid_words=avoid if avoid is not None else None, model_alias=model["alias"])
        base, preset = b["final"], {"id": presets.PRESET_ID, "scenes": [s["scene"] for s in b["sentences"]], "sentences": b["sentences"], "look": b["look"], "plain": b["plain"],
                                    "finish": bool((spec.get("preset") or {}).get("finish")), "finish_recipe": dict(presets.FINISH_RECIPE) if (spec.get("preset") or {}).get("finish") else None}
        warnings += b["warnings"]
        if not text:
            errors.append("Describe what is in the photo.")
    else:
        base = text
        tpl = (entry or {}).get("inpaint_prompt")
        if mode == "inpaint" and text and tpl:
            # a measured rule of the routing file: this model draws the object when told what to do with the selection, and does not when only given a noun phrase
            base = tpl.replace("{text}", _object_phrase(text))
        if not text and mode == "outpaint":
            base = DEFAULT_OUTPAINT_PROMPT
        elif not text and mode == "reference" and used:
            base = DEFAULT_REFERENCE_PROMPT
        elif not text and md["prompt"]:
            errors.append("Write a prompt first." if mode in ("generate", "advanced") else f"{md['label']} needs a sentence saying what to do.")
    sentences = reference_sentences(used, source_counts=source_counts, mode=mode) if used else []
    lead = base if (not sentences or not base or base[-1] in ".!?:") else base + "."        # the words, then one sentence per picture
    final = " ".join(x for x in [lead, *[s["sentence"] for s in sentences]] if x)
    if len(final) > PROMPT_MAX:
        errors.append(f"The prompt, with the sentences about the pictures, is longer than {PROMPT_MAX} characters.")
    neg = (spec.get("negative") or "").strip()
    sent_neg = bool(neg) and bool(model.get("negative_prompt"))
    if neg and not sent_neg:
        warnings.append(f"{model['alias']} does not use a negative prompt; yours was not sent.")
    return {"base": base, "final": final, "sentences": sentences, "preset": preset, "negative": neg, "negative_sent": sent_neg}


def _plan_upscale(spec: dict, ctx: dict, pln: dict) -> dict:
    errors, warnings = pln["errors"], pln["warnings"]
    source = ctx.get("source")
    pln["engine_mode"] = None
    pln["prompt"] = {"base": "", "final": "", "sentences": [], "preset": None, "negative": "", "negative_sent": False}
    pln["references"]["not_used"] = [{"asset": r["asset"], "position": i + 1, "reason": "Upscale scales the picture; it does not use reference pictures."} for i, r in enumerate(spec["references"])]
    if not source:
        errors.append("Upscale needs a picture.")
        return pln
    out = (spec.get("size") or {}).get("output")
    if not out:
        errors.append("Say the size you want (width and height).")
        return pln
    if out["width"] * out["height"] > MAX_OUT_PIXELS:
        errors.append("That size is larger than 8K.")
    sw, sh = source["width"], source["height"]
    if abs((out["width"] / out["height"]) / (sw / sh) - 1) > SHAPE_TOLERANCE:
        errors.append(f"{out['width']}×{out['height']} has a different shape from the picture ({sw}×{sh}).")
    kind = scaling_kind((sw, sh), (out["width"], out["height"]))
    if kind == "none":
        errors.append(f"The picture already is {sw}×{sh}.")
    pln["native"] = {"width": sw, "height": sh, "why": "The picture as it is.", "multiple": 1, "megapixels": round(sw * sh / 1e6, 3), "shape": "source"}
    pln["output"] = {"width": out["width"], "height": out["height"], "scaling": kind,
                     "note": ("Scaled with Lanczos and a light sharpening. No new detail is added: this is not AI." if kind == "upscale"
                              else "Scaled down with Lanczos.")}
    pln["scaling"] = {"method": "lanczos", "label": "Scaled, no new detail", "ai": False}
    up = spec.get("upscale") or {}
    model = ctx.get("model")
    if up.get("refine"):
        if model is None:
            errors.append(ctx.get("why_none") or "No model available to you can refine a picture.")
        else:
            pln["model"] = {"id": model["model_id"], "alias": model["alias"], "engine_id": model["engine_id"], "recommended": bool(ctx.get("recommended")),
                            "routed": bool(ctx.get("entry") and ctx["entry"].get("routed", True)), "why": (ctx.get("entry") or {}).get("why") or "", "slow": False, "seconds_hint": None}
            pln["engine_mode"] = "img2img"
            lim = model["limits"]
            mult = int(lim.get("multiple") or 16)
            pln["work"] = {"width": out["width"], "height": out["height"]}
            if out["width"] % mult or out["height"] % mult:
                errors.append(f"{model['alias']} works in multiples of {mult} pixels; choose a size such as {out['width'] // mult * mult}×{out['height'] // mult * mult} to refine, or scale without refining.")
            if max(out["width"], out["height"]) > int(lim.get("max_side") or 2048):
                errors.append(f"{model['alias']} works up to {lim.get('max_side') or 2048} pixels on a side; refine a smaller size, or scale without refining.")
            pln["denoise"] = float(up.get("strength") or 0.3)
            text = (spec.get("prompt") or "").strip() or DEFAULT_REFINE_PROMPT
            pln["prompt"] = {"base": text, "final": text, "sentences": [], "preset": None, "negative": "", "negative_sent": False}
            warnings.append(f"Refine is a model pass ({model['alias']}, image-to-image at strength {pln['denoise']:g}): it can add or change texture. The scaling step itself adds none.")
    return pln
