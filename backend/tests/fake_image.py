"""Imajutsu part of the Sarness engine double: the image models with their REAL capabilities (modes, references), scripted native image runs and their
inspect documents. Wired into ``fake_sarness`` (the pytest double) and, through it, into ``web/e2e_media/fake_engine.py`` (the browser double).

Only the engine is a double. Everything it hands back is real data: result files are real PNGs of the requested size (numpy + zlib), previews are real small
JPEGs (ffmpeg), uploaded pictures and masks are kept byte for byte, and every accepted request is recorded exactly as it arrived
(``STATE["image_submissions"]``) so a test can compare what Semechka sent with what it meant to send.

The rules mirror the real engine as audited in ~/Documents/Sarness-imajutsu:
  * ``reference_image`` is the first picture, ``extra_references`` are the others; all together may not exceed the model's ``capabilities.references``
    (422 "model takes at most N pictures", never a silent truncation); a model without ``references`` takes one picture at most;
  * ``mask_image`` needs ``reference_image`` and a model that can inpaint; a reference picture needs a model that can ``edit`` or ``img2img``;
  * a job runs as the engine's native workflow: backend "sarness-workflow", stages queued -> loading_model -> encoding_prompt -> sampling -> decoding ->
    saving_output -> done, with the sampler's step counter and a latent preview (JPEG) once the first step has run.
"""
from __future__ import annotations

import hashlib
import re
import struct
import subprocess
import tempfile
import zlib
from pathlib import Path

import numpy as np

T0 = 1793700000.0
TICK_SECONDS = 2.5
_TMP = Path(tempfile.mkdtemp(prefix="fake-image-"))
_CACHE: dict = {}

# ---- the models (real capabilities: ~/Documents/Sarness-imajutsu/engine/sarness_engine/models/catalog.py, measured 2026-11-06) --------------------------
_STD_LIMITS = {"min_side": 256, "max_side": 2048, "multiple": 16}


def _model(mid, name, family, caps, defaults, limits, *, license_, vram, size, desc, tags=()):
    return {"id": mid, "name": name, "category": "image", "family": family, "supported": True, "scripted": True, "license": license_, "description": desc,
            "loaded": False, "vram_mb": vram, "size": size, "tags": list(tags), "capabilities": caps, "defaults": defaults, "limits": limits}


IMAGE_MODELS = [
    _model("flux2-klein-4b", "FLUX.2 klein 4B", "flux2klein",
           {"modes": ["txt2img", "edit", "inpaint"], "references": 4, "lora": True, "controlnet": False, "negative_prompt": False, "schedulers": []},
           {"steps": 4, "guidance": 1.0, "width": 1024, "height": 1024}, {"steps": [1, 50], "guidance": [0, 10], **_STD_LIMITS},
           license_="Apache-2.0", vram=24000, size=16_000_000_000, desc="Fast 4-step image model: text to image, instruction edit with up to 4 pictures, inpaint.", tags=["fast"]),
    _model("qwen-image-edit-2511", "Qwen-Image-Edit 2511", "qwenimage-edit",
           {"modes": ["edit"], "references": 3, "denoise": False, "lora": True, "controlnet": False, "negative_prompt": True, "schedulers": []},
           {"steps": 40, "guidance": 4.0, "width": 1024, "height": 1024}, {"steps": [1, 100], "guidance": [0, 15], **_STD_LIMITS},
           license_="Apache-2.0", vram=62000, size=58_000_000_000, desc="Instruction editing with up to 3 pictures.", tags=["edit"]),
    _model("qwen-image-2512", "Qwen-Image 2512", "qwenimage",
           {"modes": ["txt2img", "img2img", "inpaint"], "lora": True, "controlnet": False, "negative_prompt": True, "schedulers": []},
           {"steps": 50, "guidance": 4.0, "width": 1328, "height": 1328}, {"steps": [1, 100], "guidance": [0, 15], **_STD_LIMITS},
           license_="Apache-2.0", vram=62000, size=58_000_000_000, desc="High quality, slow: 50 steps with true CFG.", tags=["slow"]),
    _model("z-image-turbo", "Z-Image Turbo", "zimage",
           {"modes": ["txt2img", "img2img", "inpaint"], "lora": True, "controlnet": False, "negative_prompt": True, "schedulers": []},
           {"steps": 9, "guidance": 0.0, "width": 1024, "height": 1024}, {"steps": [1, 30], "guidance": [0, 3], **_STD_LIMITS},
           license_="Apache-2.0", vram=24000, size=12_000_000_000, desc="Turbo model; avoid phone-style wording.", tags=["fast"]),
    _model("qwen-image-2.1", "Qwen-Image 2.1", "qwenimage",
           {"modes": ["txt2img"], "lora": False, "controlnet": False, "negative_prompt": True, "schedulers": []},
           {"steps": 30, "guidance": 4.0, "width": 1328, "height": 1328}, {"steps": [1, 100], "guidance": [0, 15], **_STD_LIMITS},
           license_="Qwen non-commercial", vram=40000, size=40_000_000_000, desc="Text to image only.", tags=["non-commercial"]),
    _model("sdxl-turbo", "SDXL Turbo", "sdxl",
           {"modes": ["txt2img", "img2img", "inpaint"], "lora": True, "controlnet": True, "negative_prompt": True,
            "schedulers": ["euler_a", "euler", "dpmpp_2m", "ddim", "lcm"]},
           {"steps": 4, "guidance": 0.0, "width": 512, "height": 512}, {"steps": [1, 10], "guidance": [0, 2], "min_side": 256, "max_side": 1024, "multiple": 8},
           license_="OpenRAIL", vram=8000, size=7_000_000_000, desc="Old 4-step model.", tags=["small"]),
]
BY_ID = {m["id"]: m for m in IMAGE_MODELS}

# time per (megapixel x step) measured on the Spark at each model's OWN default steps (re-run 2026-11-07); used by the estimate endpoint (``basis: measured`` only at the measured size)
MEASURED = {"flux2-klein-4b": {"size": (720, 1280), "steps": 4, "seconds": 4.0, "peak_mb": 31500},
            "z-image-turbo": {"size": (720, 1280), "steps": 9, "seconds": 12.0, "peak_mb": 29800},
            "qwen-image-2512": {"size": (720, 1280), "steps": 50, "seconds": 144.0, "peak_mb": 61000}}


# ---- real files ------------------------------------------------------------------------------------------------------------------------------------
def png_bytes(arr: np.ndarray) -> bytes:
    """A real PNG (8-bit RGB) of an H x W x 3 uint8 array."""
    h, w, _ = arr.shape
    raw = np.concatenate([np.zeros((h, 1), np.uint8), arr.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 3)) + chunk(b"IEND", b"")


def result_png(w: int, h: int, seed: int) -> bytes:
    """The 'generated' picture: a smooth, seed-dependent pattern of exactly w x h pixels (cached; the same seed and size always give the same bytes)."""
    key = ("png", w, h, seed)
    if key not in _CACHE:
        y = np.linspace(0, 1, h, dtype=np.float32)[:, None]
        x = np.linspace(0, 1, w, dtype=np.float32)[None, :]
        s = (seed % 997) / 997.0
        r = 128 + 100 * np.sin(2 * np.pi * (x * (1 + seed % 3) + s))
        g = 128 + 100 * np.sin(2 * np.pi * (y * (1 + seed % 5) + s * 2))
        b = 128 + 100 * np.sin(2 * np.pi * ((x + y) * (1 + seed % 2) + s * 3))
        _CACHE[key] = png_bytes(np.stack([np.broadcast_to(c, (h, w)) for c in (r, g, b)], axis=-1).astype(np.uint8))
    return _CACHE[key]


def preview_jpeg(w: int, h: int, step: int, steps: int) -> bytes:
    """A real small JPEG standing for the latent preview: noisy early on, smoother with every step (long edge 96)."""
    key = ("jpg", w, h, step, steps)
    if key not in _CACHE:
        sc = 96 / max(w, h)
        pw, ph = max(8, int(w * sc) // 2 * 2), max(8, int(h * sc) // 2 * 2)
        rng = np.random.default_rng(step)
        base = np.linspace(40, 200, pw, dtype=np.float32)[None, :, None] * np.ones((ph, 1, 3), np.float32)
        noise = rng.normal(0, 60 * (1 - step / max(steps, 1)), (ph, pw, 3))
        arr = np.clip(base + noise, 0, 255).astype(np.uint8)
        p = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{pw}x{ph}", "-i", "pipe:0",
                            "-frames:v", "1", "-q:v", "5", "-f", "mjpeg", "pipe:1"], input=arr.tobytes(), capture_output=True, check=True)
        _CACHE[key] = p.stdout
    return _CACHE[key]


def probe_image(data: bytes) -> dict | None:
    """Width and height of an uploaded PNG / JPEG (header parsing); None for anything else."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return {"width": w, "height": h, "format": "png"}
    if data[:3] == b"\xff\xd8\xff":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            m = data[i + 1]
            if m in (0xC0, 0xC1, 0xC2):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return {"width": w, "height": h, "format": "jpeg"}
            if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                i += 2
                continue
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return None


# ---- the engine's rules for a submission ------------------------------------------------------------------------------------------------------------
def engine_mode(simple: dict, model: dict) -> str:
    """What the real engine will do with these inputs, or an HTTP 422 reason (a ValueError) when the model cannot."""
    caps = model["capabilities"]
    modes = caps.get("modes") or []
    has_src, has_mask = bool(simple.get("reference_image")), bool(simple.get("mask_image"))
    if simple.get("extra_references") and not has_src:
        raise ValueError("extra_references need a reference_image")
    if has_mask:
        if not has_src:
            raise ValueError("mask_image needs a reference_image")
        if "inpaint" not in modes:
            raise ValueError(f"{model['id']} cannot inpaint")
        return "inpaint"
    if has_src:
        if "edit" in modes:
            return "edit"
        if "img2img" in modes:
            return "img2img"
        raise ValueError(f"{model['id']} takes no picture to work from")
    if "txt2img" not in modes:
        raise ValueError(f"{model['id']} cannot generate from text alone; give it a picture to edit")
    return "txt2img"


def check_submission(simple: dict, model: dict, uploads: dict) -> tuple[str, list[str]]:
    """Validate like the engine's image_workflow + sampler: -> (engine mode, ordered picture file ids). Raises ValueError with the engine's wording."""
    mode = engine_mode(simple, model)
    pics = [p for p in [simple.get("reference_image"), *(simple.get("extra_references") or [])] if p]
    cap = int(model["capabilities"].get("references") or 1)
    if len(pics) > cap:
        raise ValueError(f"{model['id']} takes at most {cap} picture{'s' if cap != 1 else ''} (got {len(pics)}); send fewer")
    for p in pics + ([simple["mask_image"]] if simple.get("mask_image") else []):
        if p not in uploads:
            raise ValueError(f"unknown upload '{p}'")
    lim = model["limits"]
    for k in ("width", "height"):
        v = simple.get(k)
        if v is not None and (v % lim["multiple"] or not lim["min_side"] <= v <= lim["max_side"]):
            raise ValueError(f"{k} {v} must be a multiple of {lim['multiple']} between {lim['min_side']} and {lim['max_side']}")
    st = simple.get("steps")
    if st is not None and not lim["steps"][0] <= st <= lim["steps"][1]:
        raise ValueError(f"steps must be between {lim['steps'][0]} and {lim['steps'][1]}")
    if simple.get("negative_prompt") and not model["capabilities"].get("negative_prompt"):
        raise ValueError(f"{model['id']} does not use a negative prompt")
    return mode, pics


# ---- the scripted run ----------------------------------------------------------------------------------------------------------------------------------
def run_nodes(simple: dict, pics: list[str], mode: str, names: dict) -> list[tuple]:
    """(id, class_type, title, group) in execution order: the workflow image_workflow() builds for these inputs."""
    n = [("model", "loader.image_model", "Load image model", "load"), ("prompt", "prompt.encode", "Encode prompt", "encode")]
    if pics:
        n += [("ref", "image.load", "Reference image", "load"), ("ref_resize", "image.resize", "Resize reference", "load"), ("encode", "vae.encode", "Encode reference", "encode")]
        if simple.get("mask_image"):
            n.append(("mask", "mask.load", "Load mask", "load"))
        n += [(f"ref_{i + 2}", "image.load", f"Reference {i + 2}", "load") for i in range(len(pics) - 1)]
    else:
        n.append(("latent", "latent.empty", "Empty latent", "encode"))
    return n + [("sampler", "sampler.image", "Sample image", "sample"), ("decode", "vae.decode", "Decode", "decode"), ("save", "output.save_image", "Save image", "save")]


def make_run(jid: str, simple: dict, model: dict, mode: str, pics: list[str], upload_meta: dict, *, fail: bool) -> dict:
    steps = int(simple.get("steps") or model["defaults"]["steps"])
    w, h = int(simple.get("width") or model["defaults"]["width"]), int(simple.get("height") or model["defaults"]["height"])
    run = {"id": jid, "kind": "image", "profile": None, "operation": mode, "model": model["id"], "simple": dict(simple), "steps": steps, "size": (w, h), "tick": 0,
           "cancelled": None, "variant": "fail" if fail else "ok", "pics": list(pics), "mask": simple.get("mask_image"),
           "names": {u: (upload_meta.get(u) or {}).get("name") for u in pics + ([simple["mask_image"]] if simple.get("mask_image") else [])}}
    run["nodes"] = run_nodes(simple, pics, mode, run["names"])
    run["sample_ticks"] = min(steps, 4)
    run["events"], run["last_tick"] = script(run)
    return run


def script(run: dict) -> tuple[list[dict], int]:
    ev: list[dict] = []
    nodes = {n[0]: n for n in run["nodes"]}

    def add(tick, type_, **kw):
        ev.append({"seq": len(ev) + 1, "tick": tick, "t": round(T0 + tick * TICK_SECONDS, 3), "type": type_, **kw})

    def node(nid, type_, tick, **kw):
        add(tick, type_, node_id=nid, title=nodes[nid][2], class_type=nodes[nid][1], **kw)

    add(0, "stage", stage="queued", message="Queued")
    add(1, "stage", stage="loading_model", message="Loading the model")
    node("model", "node_start", 1)
    loaders = [n[0] for n in run["nodes"] if n[3] == "load" and n[0] != "model"]
    for nid in loaders:
        node(nid, "node_start", 1)
        node(nid, "node_done", 1, ms=40)
    node("model", "node_done", 2, ms=3100)
    add(2, "stage", stage="encoding_prompt", message="Encoding the prompt")
    node("prompt", "node_start", 2)
    node("prompt", "node_done", 3, ms=210)
    for nid in ("latent", "ref_resize", "encode", "mask"):
        if nid in nodes and nid not in loaders:
            node(nid, "node_start", 3)
            node(nid, "node_done", 3, ms=70)
    add(3, "stage", stage="sampling", message="Sampling")
    node("sampler", "node_start", 3)
    st, n = run["steps"], run["sample_ticks"]
    add(3, "progress", node_id="sampler", title=nodes["sampler"][2], value=0, max=st)
    t = 3
    for i in range(1, n + 1):
        t += 1
        value = round(i * st / n)
        if run["variant"] == "fail" and i == min(2, n):
            add(t, "error", node_id="sampler", title=nodes["sampler"][2], class_type="sampler.image",
                message="Sample image: CUDA out of memory while sampling (tried to allocate 3.2 GiB) in /home/x/engine/workflow/nodes/diffusion.py")
            add(t, "stage", stage="failed", message="Failed in Sample image")
            return ev, t
        add(t, "progress", node_id="sampler", title=nodes["sampler"][2], value=value, max=st)
    t += 1
    node("sampler", "node_done", t, ms=19000)
    add(t, "stage", stage="decoding", message="Decoding")
    node("decode", "node_start", t)
    node("decode", "node_done", t, ms=400)
    t += 1
    add(t, "stage", stage="saving_output", message="Saving the image")
    node("save", "node_start", t)
    add(t, "output", state="writing")
    t += 1
    node("save", "node_done", t, ms=120)
    add(t, "output", state="ready")
    add(t, "stage", stage="done", message="Done")
    return ev, t


def fold(run: dict, tick: int, cancelled: int | None, state: dict) -> dict:
    """The inspect document at ``tick``: everything is derived from the events, so the same script always gives the same answers."""
    evs = [e for e in run["events"] if e["tick"] <= tick and (cancelled is None or e["tick"] <= cancelled)]
    stage, label = "queued", "Queued"
    nodes = {nid: {"state": "pending", "class_type": ct, "title": title, "runs": 0, "total_ms": 0, "last_ms": None} for nid, ct, title, _g in run["nodes"]}
    sampling, error, out_state, last_step = None, None, "none", 0
    for e in evs:
        ty, nid = e["type"], e.get("node_id")
        if ty == "stage":
            stage, label = e["stage"], e["message"]
        elif ty == "node_start" and nid in nodes:
            nodes[nid]["state"] = "running"
        elif ty == "node_done" and nid in nodes:
            nd = nodes[nid]
            nd["state"], nd["runs"], nd["last_ms"] = "done", nd["runs"] + 1, e.get("ms")
            nd["total_ms"] += e.get("ms") or 0
            if nid == "sampler":
                sampling = None
        elif ty == "progress":
            sampling = {"node_id": nid, "title": e["title"], "step": e["value"], "max": e["max"]}
            last_step = e["value"]
        elif ty == "error":
            error = {"message": re.sub(r"(?:/[\w.@+\-]+){3,}", "<path>", e["message"]), "node_id": nid, "node_title": e["title"], "class_type": e["class_type"]}
            nodes[nid]["state"] = "failed"
            sampling = None
        elif ty == "output":
            out_state = e["state"]
    status = "queued" if stage == "queued" else "completed" if out_state == "ready" else "failed" if error else "running"
    if cancelled is not None:
        status, stage, label, sampling = "cancelled", "failed", "Cancelled", None
    if status in ("failed", "cancelled"):
        for nd in nodes.values():
            if nd["state"] == "running":
                nd["state"] = "pending"
    running = [k for k, x in nodes.items() if x["state"] == "running"]
    counts = {"total": len(nodes), "done": sum(1 for x in nodes.values() if x["state"] == "done"), "running": running,
              "pending": sum(1 for x in nodes.values() if x["state"] == "pending"), "cached": 0, "failed": sum(1 for x in nodes.values() if x["state"] == "failed")}
    if status == "completed":
        prog = {"fraction": 1.0, "indeterminate": False, "basis": "output_ready"}
    elif last_step:
        prog = {"fraction": round(0.15 + 0.8 * last_step / run["steps"], 4), "indeterminate": False, "basis": "sampler_steps"}
    else:
        prog = {"fraction": None, "indeterminate": True, "basis": None}
    w, h = run["size"]
    refs = []
    for i, u in enumerate(run["pics"]):
        refs.append({"position": i + 1, "node_id": "ref" if i == 0 else f"ref_{i + 1}", "file": u, "used": True, "name": run["names"].get(u),
                     "kind": "source" if i == 0 and run["operation"] in ("edit", "img2img", "inpaint") else "reference"})
    cond = {"video": None, "references": refs, "mask": ({"node_id": "mask", "file": run["mask"], "name": run["names"].get(run["mask"])} if run["mask"] else None),
            "agree": True}
    t = min(tick, run["last_tick"])
    out_file = {"width": w, "height": h, "bytes": len(result_png(w, h, int(run["simple"].get("seed") or 0))), "format": "png"} if out_state == "ready" else None
    return {"job_id": run["id"], "backend": "sarness-workflow", "graph_kind": "sarness-workflow", "profile": None, "operation": run["operation"], "status": status,
            "stage": stage, "stage_label": label, "prompt_id": None, "started_at": T0 + TICK_SECONDS if t >= 1 else None, "updated_at": T0 + t * TICK_SECONDS,
            "elapsed_s": round(max(0, t - 1) * TICK_SECONDS, 1), "progress": prog, "sampling": sampling,
            "scenes": {"total": None, "completed": None, "current": None, "segments": []}, "loop": None, "nodes": counts, "node_states": nodes, "conditioning": cond,
            "memory": {"ram_available_mb": 90400 - 1200 * min(t, 20), "comfyui_rss_mb": None, "vram_mb": None, "process_rss_mb": 31500 if t >= 2 else None,
                       "source": "/proc/meminfo + process RSS (unified memory: NVML reports no device memory)"},
            "output": {"state": out_state, "file": out_file}, "error": error, "_events": evs, "attempt": 1, "has_preview": bool(last_step),
            "model": run["model"], "seed": run["simple"].get("seed"),
            "current_node": ({"id": running[0], "title": nodes[running[0]]["title"], "class_type": nodes[running[0]]["class_type"]} if running else None),
            "history": None}


def graph(run: dict) -> dict:
    """{nodes: {id: {class_type, title, inputs}}} with links as [node_id, output_index], like the engine's workflow converted for the graph view."""
    s = run["simple"]
    nodes: dict = {}
    pics = run["pics"]
    for nid, ct, title, _g in run["nodes"]:
        inp: dict = {}
        if ct == "loader.image_model":
            inp = {"model": run["model"], "memory_mode": "auto"}
        elif ct == "prompt.encode":
            inp = {"prompt": str(s.get("prompt") or "")[:2000], "negative_prompt": str(s.get("negative_prompt") or ""), "model": ["model", 0]}
        elif ct == "image.load":
            idx = 0 if nid == "ref" else int(nid.split("_")[1]) - 1
            inp = {"file": pics[idx]}
        elif ct == "image.resize":
            inp = {"image": ["ref", 0], "width": run["size"][0], "height": run["size"][1], "mode": "fill"}
        elif ct == "vae.encode":
            inp = {"image": ["ref_resize", 0], "model": ["model", 0]}
        elif ct == "mask.load":
            inp = {"file": run["mask"], "channel": "luminance"}
        elif ct == "latent.empty":
            inp = {"width": run["size"][0], "height": run["size"][1], "batch_size": int(s.get("batch_size") or 1)}
        elif ct == "sampler.image":
            inp = {"seed": int(s.get("seed") if s.get("seed") is not None else -1), "steps": run["steps"], "model": ["model", 0], "conditioning": ["prompt", 0],
                   "latent": ["encode" if pics else "latent", 0]}
            if s.get("denoise") is not None:
                inp["denoise"] = s["denoise"]
            for i in range(1, len(pics)):
                inp[f"reference_{i + 1}"] = [f"ref_{i + 1}", 0]
            if run["mask"]:
                inp["mask"] = ["mask", 0]
        elif ct == "vae.decode":
            inp = {"latent": ["sampler", 0], "model": ["model", 0]}
        elif ct == "output.save_image":
            inp = {"image": ["decode", 0], "format": "png", "quality": 95}
        nodes[nid] = {"class_type": ct, "title": title, "inputs": inp}
    return {"prompt_id": None, "format": "sarness-workflow", "graph_kind": "sarness-workflow", "nodes": nodes, "order": [n[0] for n in run["nodes"]]}


def estimate(model_id: str, width: int, height: int, steps: int) -> dict:
    """The engine's own recorded timings: measured at the benchmark size, extrapolated (pixels x steps) elsewhere, none for a model nobody timed."""
    m = MEASURED.get(model_id)
    if not m:
        return {"model": model_id, "seconds": {"estimate": None, "basis": "none"}, "memory": {"peak_mb": None, "basis": "none", "note": "nothing was measured for this model"}}
    base_px = m["size"][0] * m["size"][1]
    same = (width, height, steps) == (m["size"][0], m["size"][1], m["steps"])
    sec = m["seconds"] * (width * height * steps) / (base_px * m["steps"])
    return {"model": model_id, "seconds": {"estimate": round(sec, 1), "basis": "measured" if same else "extrapolated"},
            "memory": {"peak_mb": m["peak_mb"], "basis": "measured" if same else "extrapolated",
                       "note": f"measured on a {m['size'][0]}x{m['size'][1]} image in {m['steps']} steps" if same else "scaled from the measured run"}}


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
