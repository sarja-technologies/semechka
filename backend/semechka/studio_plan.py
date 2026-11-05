"""OpenJutsu Studio: the plan. A pure function from (source facts, the person's editing decisions, the engine's limits) to what will really happen:
how the video is cut, which part of the picture is kept, what the prepared file looks like, the size the model processes at and how the
result is scaled to the size that was asked for. No I/O, no side effects: the interface calls it while a handle is being dragged.

Geometry is always in the DISPLAY frame: a phone video stored sideways (rotation 90/270) is first turned upright, and every crop rectangle,
prepared size and processing size is measured on that upright picture.
"""
from __future__ import annotations

import hashlib
import json

PLAN_VERSION = 1
ASPECTS = {"16:9": 16 / 9, "9:16": 9 / 16, "1:1": 1.0, "4:3": 4 / 3, "3:4": 3 / 4}
RESOLUTION_EDGE = {"480p": 480, "720p": 720, "1080p": 1080, "1440p": 1440, "4k": 2160}
MIN_SIDE, MAX_SIDE, MAX_PIXELS = 64, 8192, 8192 * 4320
COPY_VCODECS, COPY_ACODECS = {"h264"}, {None, "", "aac", "mp3"}
MP4_LIKE = {"video/mp4"}


def even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def even_floor(v: float) -> int:
    return max(2, int(v) // 2 * 2)


def round_to(v: float, step: int) -> int:
    return max(step, int(round(v / step)) * step)


def fit_inside(x: int, y: int, w: int, h: int, ratio: float) -> dict:
    """The largest centred rectangle of shape ``ratio`` (width / height) inside the given one: whole pixels, even sizes and offsets."""
    nw, nh = w, w / ratio
    if nh > h:
        nh, nw = h, h * ratio
    nw, nh = min(w, even(nw)), min(h, even(nh))
    nx, ny = int(round(x + (w - nw) / 2)), int(round(y + (h - nh) / 2))
    nx, ny = max(x, nx - nx % 2), max(y, ny - ny % 2)
    return {"x": nx, "y": ny, "w": nw, "h": nh}


def processing_size(w: int, h: int, short_edge: int, step: int = 32) -> tuple[int, int]:
    """The engine's formula: scale = short_edge / min(w, h), both sides rounded to a multiple of ``step`` (H3 works in steps of 32)."""
    sc = short_edge / min(w, h)
    return round_to(w * sc, step), round_to(h * sc, step)


def preset_edges(limits: dict | None) -> tuple[list[int], int | None, int]:
    r = ((limits or {}).get("resolutions") or {})
    edges = sorted({int(str(p).rstrip("p")) for p in r.get("presets") or [] if str(p).rstrip("p").isdigit()})
    mx = r.get("max_short_edge")
    if mx:
        edges = [e for e in edges if e <= int(mx)] or edges
    return edges, (int(mx) if mx else None), int(r.get("step") or 32)


def cache_key(source_id: str, ops: dict) -> str:
    """sha256(source asset id + the normalised operations): the same video with the same decisions is prepared once."""
    return hashlib.sha256((source_id + "\n" + json.dumps(ops, sort_keys=True, separators=(",", ":"))).encode()).hexdigest()


def _label(edge: int) -> str:
    return f"{edge}p"


def plan(src: dict, spec: dict, limits: dict | None = None, *, keyframes: list[float] | None = None) -> dict:
    """``src``: {id, width, height (as displayed), duration, rotation, fps, audio, vcodec, acodec, mime}. ``spec``: the validated project spec.
    ``limits``: the engine profile's ``limits`` (None when no editing model is chosen). ``keyframes``: times of the source's key frames, if known."""
    errors: list[str] = []
    warnings: list[str] = []
    W, H, dur = int(src["width"]), int(src["height"]), float(src.get("duration") or 0)
    rot = int(src.get("rotation") or 0) % 360
    fps = src.get("fps")
    audio = bool(src.get("audio"))
    out: dict = {"version": PLAN_VERSION,
                 "input": {"width": W, "height": H, "duration": round(dur, 3), "rotation": rot, "fps": fps, "audio": audio}}

    # ---- trim -------------------------------------------------------------------------------------------------
    t = spec.get("trim")
    s0, e0 = (float(t["start"]), float(t["end"])) if t else (0.0, dur)
    e0 = min(e0, dur)
    if s0 < 0 or e0 - s0 < 0.1:
        errors.append(f"The selected part must be at least 0.1 s and lie within the {dur:.1f} s video.")
    secs = max(0.0, e0 - s0)
    trim_op = None if (s0 < 0.001 and e0 >= dur - 0.04) else [round(s0, 3), None if e0 >= dur - 0.04 else round(e0, 3)]
    mx_src = (limits or {}).get("max_source_seconds")
    if mx_src and secs > float(mx_src) + 1e-6:
        errors.append(f"The model takes up to {mx_src:g} s of video and the selected part is {secs:.1f} s. Trim it to {mx_src:g} s or less.")

    # ---- frame: crop / aspect / treatment on the displayed picture --------------------------------------------------
    fw, fh = even_floor(W), even_floor(H)          # the even-sized displayed frame (video encoders need even sizes)
    c = spec.get("crop")
    if c:
        cx, cy, cw, ch = (int(round(c[k])) for k in ("x", "y", "w", "h"))
        cx, cy, cw, ch = cx - cx % 2, cy - cy % 2, cw - cw % 2, ch - ch % 2
        if cw < 16 or ch < 16:
            errors.append("The crop is too small (at least 16 × 16 pixels).")
        elif cx < 0 or cy < 0 or cx + cw > W or cy + ch > H:
            errors.append(f"The crop reaches outside the {W} × {H} picture.")
    else:
        cx, cy, cw, ch = 0, 0, fw, fh
    frame_spec = spec.get("frame") or {}
    aspect = frame_spec.get("aspect") or "original"
    treatment = frame_spec.get("treatment") or "crop"
    ratio = frame_spec.get("ratio") if aspect == "custom" else ASPECTS.get(aspect)
    if aspect == "custom" and not (isinstance(ratio, (int, float)) and 0.2 <= ratio <= 5):
        errors.append("Choose a shape for the custom frame (width ÷ height between 1:5 and 5:1).")
        ratio = None
    a = cw / ch if ch else 1.0
    crop_rect = {"x": cx, "y": cy, "w": cw, "h": ch}
    pad = stretch = None
    pw, ph = cw, ch
    if ratio and abs(ratio - a) >= 0.002:
        if treatment == "crop":
            crop_rect = fit_inside(cx, cy, cw, ch, ratio)
            pw, ph = crop_rect["w"], crop_rect["h"]
        else:  # pad or stretch: the whole (cropped) picture is kept; the canvas has the new shape
            cw2, ch2 = (cw, even(cw / ratio)) if a > ratio else (even(ch * ratio), ch)
            pw, ph = cw2, ch2
            if treatment == "pad":
                pad = {"w": cw2, "h": ch2, "x": ((cw2 - cw) // 2) // 2 * 2, "y": ((ch2 - ch) // 2) // 2 * 2}
            else:
                stretch = (cw2, ch2)
    frame = {"aspect": aspect, "treatment": treatment if ratio else "none", "crop": crop_rect}
    if pad:
        frame["pad"] = pad
    if stretch:
        frame["stretch"] = {"w": stretch[0], "h": stretch[1]}
    out["frame"] = frame
    crop_op = None if (crop_rect["x"] == 0 and crop_rect["y"] == 0 and crop_rect["w"] == fw and crop_rect["h"] == fh) else crop_rect

    # ---- requested output size ---------------------------------------------------------------------------------------
    res = spec.get("resolution") or {}
    preset = res.get("preset") or "original"
    portrait = ph > pw
    shape = ratio if ratio else pw / ph      # width / height: the nominal shape when one was chosen (9:16 gives 1080×1920, not 1080×1918)

    def sized(short: int) -> tuple[int, int]:
        return (short, even(short / shape)) if portrait else (even(short * shape), short)
    if preset == "original":
        req_w, req_h = pw, ph
    elif preset in RESOLUTION_EDGE:
        req_w, req_h = sized(RESOLUTION_EDGE[preset])
    else:  # custom
        rw, rh = res.get("width"), res.get("height")
        if res.get("lock_aspect", True):
            if rw:
                req_w, req_h = even(rw), even(rw * ph / pw)
            elif rh:
                req_w, req_h = even(rh * pw / ph), even(rh)
            else:
                req_w = req_h = 0
        else:
            req_w, req_h = (even(rw), even(rh)) if (rw and rh) else (0, 0)
        if not (req_w and req_h):
            errors.append("Enter a width or a height for the custom size.")
            req_w, req_h = pw, ph
    if min(req_w, req_h) < MIN_SIDE or max(req_w, req_h) > MAX_SIDE or req_w * req_h > MAX_PIXELS:
        errors.append(f"The output size {req_w} × {req_h} is outside what can be made (at least {MIN_SIDE} px, at most {MAX_SIDE} px on a side).")
    req_edge = min(req_w, req_h)

    # ---- the size the model processes at -----------------------------------------------------------------------------------
    edges, max_edge, step = preset_edges(limits)
    scale_op = None
    proc = None
    if edges:
        below = [e for e in edges if e <= req_edge]
        chosen = max(below) if below else min(edges)
        # the prepared file is not needed larger than twice what the model works at: shrinking it early saves encoding time and disk
        if min(pw, ph) > 2 * chosen:
            f = 2 * chosen / min(pw, ph)
            scale_op = (even(pw * f), even(ph * f))
            pw, ph = scale_op
        w2, h2 = processing_size(pw, ph, chosen, step)
        label = "the original size" if preset == "original" else ("the custom size" if preset == "custom" else preset)
        sizes = ", ".join(_label(e) for e in edges)
        if chosen == req_edge:
            why = f"The model works at fixed sizes ({sizes}). {_label(chosen)} is the size you asked for, so nothing is scaled afterwards."
        elif below:
            why = (f"The model works at fixed sizes ({sizes}). The largest one not above {label} ({_label(req_edge)} on the short side) is {_label(chosen)}, "
                   f"so the model works at {_label(chosen)} and the result is scaled to {req_w}×{req_h} afterwards.")
        else:
            why = (f"The model works at fixed sizes ({sizes}) and {_label(req_edge)} is smaller than the smallest, so the model works at {_label(chosen)} "
                   f"and the result is scaled down to {req_w}×{req_h} afterwards.")
        if max_edge and req_edge > max_edge:
            warnings.append(f"{req_w}×{req_h} is larger than the model can process: it works at most at {_label(max_edge)} on the short side "
                            f"(the largest size tested on this machine). It will process at {_label(chosen)} ({w2}×{h2}) and the rest is scaling, which adds no detail.")
        mem = {"peak_mb": None, "basis": "none", "note": "Nothing was measured at this size."}
        for m in ((limits or {}).get("memory") or {}).get("measured") or []:
            if m.get("short_edge") == chosen:
                mem = {"peak_mb": m.get("peak_mb"), "basis": "measured", "note": f"Measured on a {m.get('clip_seconds')} s clip at this size."}
        proc = {"short_edge": chosen, "width": w2, "height": h2, "preset": _label(chosen), "exact": chosen == req_edge, "why": why, "memory": mem}
        nat_w, nat_h = w2, h2
    else:
        warnings.append("No editing model with known sizes is chosen yet, so the size the model works at cannot be shown.")
        nat_w, nat_h = pw, ph
    out["processing"] = proc
    out["prepared"] = {"width": pw, "height": ph, "scale": bool(scale_op)}

    nat_edge = min(nat_w, nat_h)
    scaling = "upscale" if req_edge > nat_edge else "downscale" if req_edge < nat_edge else "none"
    if scaling == "none":
        note = f"The result is {nat_w}×{nat_h}; nothing is scaled." if (req_w, req_h) == (nat_w, nat_h) else \
            f"The model produces {nat_w}×{nat_h}; the requested {req_w}×{req_h} has the same short side."
    elif scaling == "upscale":
        note = f"The model produces {nat_w}×{nat_h}; {req_w}×{req_h} is made by scaling it up (no new detail)."
    else:
        note = f"The model produces {nat_w}×{nat_h}; {req_w}×{req_h} is made by scaling it down."
    out["output"] = {"requested": {"preset": preset, "width": req_w, "height": req_h}, "native": {"width": nat_w, "height": nat_h}, "scaling": scaling, "note": note}

    # ---- how the trim/prepared file is made ---------------------------------------------------------------------------------
    mute = (spec.get("audio") or "keep") == "mute" and audio     # muting a video that has no sound changes nothing
    needs_filter = bool(crop_op or pad or stretch or scale_op)
    vc, ac, mime = src.get("vcodec"), src.get("acodec"), src.get("mime")
    tol = min(0.5 / fps, 0.04) if fps else 0.02
    kf = None
    need_kf = False
    if keyframes is not None and trim_op and s0 > 0.001:
        before = [k for k in keyframes if k <= s0 + tol]
        kf = max(before) if before else 0.0
    if needs_filter:
        mode, why = "reencode", "The picture is " + " and ".join(x for x, on in (("cropped", crop_op), ("padded", pad), ("stretched", stretch), ("resized", scale_op)) if on) + \
            ", so every frame is re-encoded (H.264, visually lossless)."
    elif rot:
        mode, why = "reencode", f"The video is stored sideways (rotated {rot}°). It is turned upright for real so the model sees it the way you do."
    elif vc not in COPY_VCODECS or (ac or "") not in COPY_ACODECS and not mute:
        mode, why = "reencode", f"The video is {vc or 'unknown'}/{ac or 'no audio'}, which is converted to H.264/AAC for the model."
    elif trim_op and s0 > 0.001 and keyframes is None:
        mode, why, need_kf = "reencode", "Whether the start is on a keyframe is not known yet, so the cut is made exactly by re-encoding.", True
    elif trim_op and s0 > 0.001 and s0 - kf > tol:
        mode, why = "reencode", f"The start ({s0:.2f} s) is between keyframes (the one before it is at {kf:.2f} s), so the video is re-encoded to cut exactly there."
    elif trim_op or mute or mime not in MP4_LIKE:
        mode = "copy"
        why = ("The start is on a keyframe, so the video is cut without re-encoding: no quality loss." if trim_op and s0 > 0.001 else
               "The video is cut without re-encoding: no quality loss." if trim_op else
               "The sound is removed without touching the picture." if mute else "The video is repackaged as MP4 without re-encoding.")
    else:
        mode, why = "none", "The video is already in the right form; it is used as it is."
    out["trim"] = {"start": round(s0, 3), "end": round(e0, 3), "seconds": round(secs, 3), "mode": mode, "why": why}
    if need_kf:
        out["trim"]["needs_keyframes"] = True    # the caller reads the key frames of the source and plans again
    out["audio"] = {"mode": "mute" if (spec.get("audio") or "keep") == "mute" else "keep", "present": audio}

    ops = {"v": PLAN_VERSION, "trim": trim_op, "crop": crop_op, "pad": pad, "stretch": list(stretch) if stretch else None,
           "scale": list(scale_op) if scale_op else None, "mute": bool(mute)}
    out["prepare"] = {"mode": mode, "ops": ops, "key": cache_key(str(src.get("id") or ""), ops) if src.get("id") else None}
    out["warnings"] = warnings
    out["errors"] = errors
    return out


def edit_spec_from(pln: dict):
    """The media_lib.EditSpec that makes the prepared file of a plan (None when the plan says the source is used as it is)."""
    from .media_lib import EditSpec
    pr = pln["prepare"]
    if pr["mode"] == "none":
        return None
    o = pr["ops"]
    t = o["trim"]
    return EditSpec(start=t[0] if t and t[0] else None, end=t[1] if t else None, crop=dict(o["crop"]) if o["crop"] else None, mute=bool(o["mute"]),
                    pad=dict(o["pad"]) if o["pad"] else None, stretch=tuple(o["stretch"]) if o["stretch"] else None,
                    scale=tuple(o["scale"]) if o["scale"] else None, stream_copy=pr["mode"] == "copy", force=True)
