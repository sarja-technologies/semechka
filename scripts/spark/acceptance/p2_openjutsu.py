"""OpenJutsu acceptance through Semechka's real API: Studio swap, mask check (text and box), object edit, reference-to-video (4 pictures), cancel of a running job.

Plugin for ~/semechka-scripts/prod_accept2.py (run ON the Spark; instance-neutral: nothing here is specific to staging).
Inputs are small AI-generated clips and pictures kept in ~/semechka-scripts/p2_inputs (copies of the validated OpenJutsu fixtures; no real people).
Every call is one the browser makes: raw upload to /api/media/uploads, /api/studio/projects + /generate (Studio), /api/media/profile-jobs (profile tray),
/api/media/jobs/{id}/cancel. Costs about what the validated runs cost (480p, 2-3 s clips)."""
import io
import json
import os
import subprocess
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, "p2_inputs")
MAIN, REF = "openjutsu-minimax-h3", "openjutsu-reference-video"
NEEDS = {"perms": ["mode.video"], "models": [(MAIN, ["video"]), (REF, ["video"])]}


def rd(name):
    return open(os.path.join(IN, name), "rb").read()


def upload(ctx, name, data):
    """The library upload the browser uses: the raw body, the name in the query."""
    r = urllib.request.Request(ctx.BASE + "/api/media/uploads?name=" + urllib.parse.quote(name), data=data, method="POST",
                               headers={"Origin": ctx.BASE, "x-csrf-token": ctx.u.csrf, "Content-Type": "application/octet-stream"})
    return json.load(ctx.u.op.open(r, timeout=600))


def profile_model(ctx, pid):
    o = ctx.u.call("GET", "/api/media/options?kind=video")
    return next((m for m in o.get("models", []) if (m.get("profile") or {}).get("id") == pid), None)


def frame(path, t, w=96, h=54):
    from PIL import Image
    b = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", path, "-frames:v", "1", "-vf", f"scale={w}:{h}", "-f", "image2pipe", "-vcodec", "png", "-"], capture_output=True).stdout
    return Image.open(io.BytesIO(b)).convert("RGB") if b else None


def diff(a, b, box=None):
    from PIL import ImageChops, ImageStat
    if a is None or b is None:
        return None
    if box:
        a, b = a.crop(box), b.crop(box)
    return sum(ImageStat.Stat(ImageChops.difference(a, b).convert("L")).mean)


def frac(img, pred):
    px = list(img.getdata())
    return sum(1 for p in px if pred(*p)) / max(1, len(px))


def output(ctx, j, tag):
    """The first saved asset of a finished job -> a local file, its ffprobe facts and a contact sheet."""
    outs = j.get("assets") or j.get("outputs") or []
    aid = outs[0].get("id") if outs and isinstance(outs[0], dict) else (outs[0] if outs else None)
    if not aid:
        ctx.check(f"{tag}: the job has an output asset", False, {k: j.get(k) for k in ("status", "error", "progress_text")})
        return None
    path = f"{ctx.OUT}/{tag}.mp4"
    open(path, "wb").write(ctx.asset(aid))
    info = ctx.probe(path)
    st = info.get("streams", [])
    v = next((s for s in st if s["codec_type"] == "video"), {})
    a = next((s for s in st if s["codec_type"] == "audio"), None)
    dur = float(info.get("format", {}).get("duration", 0))
    dec = ctx.sh(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"]).stderr.strip()
    ctx.check(f"{tag}: the whole file decodes with no errors", dec == "" and dur > 0, dec[:100])
    ctx.sheet(path, f"{ctx.OUT}/{tag}_sheet.png", every=max(1, int(v.get("nb_frames") or 30) // 6))
    return {"path": path, "dur": dur, "w": v.get("width"), "h": v.get("height"), "frames": int(v.get("nb_frames") or 0), "audio": a is not None}


def grid_frames(seconds):
    """What MiniMax H3 makes for a clip of this length: 17k + 5 frames at 24 fps, rounded down (one clip)."""
    n = round(seconds * 24)
    return (n - 5) // 17 * 17 + 5


def studio(ctx, tag, model, mode, source, refs, subject, settings, seconds_expected=None):
    """One Studio generation, the way the page does it: project -> generate (spec in the request) -> wait for the saved job."""
    cap = ctx.u.call("GET", "/api/studio/capabilities")
    row = next((m for m in cap.get("modes", []) if m["id"] == mode), {})
    mm = next((x for x in row.get("models", []) if x["model_id"] == model["id"]), None)
    status = (mm or {}).get("status")
    ctx.check(f"{tag}: the Studio offers '{mode}' on {model['alias']}", status in ("ready", "experimental"), f"status={status} {((mm or {}).get('reason') or '')[:120]}")
    if status not in ("ready", "experimental"):
        return None
    proj = ctx.u.call("POST", "/api/studio/projects", {"source_asset": source["id"], "title": "acceptance " + tag})
    ctx.check(f"{tag}: Studio project created from the uploaded video", bool(proj.get("id")), proj if not proj.get("id") else "")
    if not proj.get("id"):
        return None
    spec = {"version": 1, "source_asset": source["id"], "trim": None, "crop": None, "frame": {"aspect": "original", "treatment": "crop"},
            "resolution": {"preset": "480p"}, "audio": "keep", "references": [{"asset": a["id"], "active": True} for a in refs], "mode": mode,
            "subject": subject, "model": model["id"], "settings": settings}
    r = {}
    for _ in range(45):
        r = ctx.u.call("POST", f"/api/studio/projects/{proj['id']}/generate", {"spec": spec, "accept_experimental": status == "experimental"})
        if r.get("job") or r.get("code") != "preparing":
            break
        time.sleep(2)
    ctx.check(f"{tag}: generate accepted (sent {len((r.get('accounting') or {}).get('sent_to_engine') or [])} picture(s))", bool(r.get("job")), {k: r.get(k) for k in ("code", "message", "_http")} if not r.get("job") else "")
    return r if r.get("job") else None


def finish(ctx, tag, r, timeout=2400):
    j = ctx.wait(r["job"], timeout)
    ctx.check(f"{tag}: completed on the real GPU", j.get("status") == "completed", f"{j.get('_seconds')} s {j.get('error') or ''}")
    return j


def run(ctx):
    main, ref = profile_model(ctx, MAIN), profile_model(ctx, REF)
    ctx.check("the OpenJutsu editing profile is offered to the test account", bool(main), main and main["alias"])
    ctx.check("the OpenJutsu reference profile is offered to the test account", bool(ref), ref and ref["alias"])
    if not main:
        return
    woman = upload(ctx, "woman_street.mp4", rd("src_3s_480p.mp4"))
    pic = {n: upload(ctx, n, rd(n)) for n in ("ref_woman_red.png", "ref_man_grey.png", "ref_man_beard.png", "ref_woman_yellow_coat.png")}
    ctx.check("the source clip and four reference pictures upload to the library", all(a.get("id") for a in [woman, *pic.values()]), woman.get("kind"))

    # (1) swap: Studio, character replacement, one reference picture
    r = studio(ctx, "swap", main, "character_replacement", woman, [pic["ref_woman_red.png"]], {"text": "the woman", "kind": "person"}, {"seed": 11})
    if r:
        j = finish(ctx, "swap", r)
        o = output(ctx, j, "swap") if j.get("status") == "completed" else None
        if o:
            secs = float(((r.get("plan") or {}).get("trim") or {}).get("seconds") or 3.0)
            ctx.check("swap: length follows H3's frame grid (17k+5 frames, at most 0.7 s shorter than asked)", o["frames"] == grid_frames(secs) and 0 <= secs - o["dur"] < 0.75,
                      f"{o['dur']:.2f}s {o['frames']} fr, plan {secs}s -> grid {grid_frames(secs)}")
            m_, mx = ctx.volume(o["path"])
            ctx.check("swap: the source soundtrack is kept (audio present, not silent)", o["audio"] and m_ is not None and m_ > -60, f"mean {m_} peak {mx}")
            src = f"{ctx.OUT}/swap_source.mp4"
            open(src, "wb").write(rd("src_3s_480p.mp4"))
            centre, border = [], []
            for t in (0.5, 1.5):
                a, b = frame(src, t), frame(o["path"], t)
                centre.append(diff(a, b, (24, 4, 72, 50)))
                border.append(diff(a, b, (0, 0, 10, 54)))
            ctx.check("swap: the person area changed, the background did not", min(centre) > 12 and max(border) < min(centre) / 1.5,
                      f"centre diff {['%.1f' % c for c in centre]} border diff {['%.1f' % c for c in border]}")

    # (2) mask check: the profile tray (no Studio mode), text target and a box
    for tag, extra in (("mask_text", {}), ("mask_box", {"subject_box": [0.08, 0.02, 0.5, 0.95]})):
        inputs = {"operation": "check_mask", "target": "person", "duration": 3, **extra}
        r = ctx.u.call("POST", "/api/media/profile-jobs", multipart={"model_id": main["id"], "save": "true", "inputs": json.dumps(inputs),
                                                                     "file:video": ("woman_street.mp4", rd("src_3s_480p.mp4"), "video/mp4")})
        ctx.check(f"{tag}: profile job accepted", bool(r.get("id")), {k: r.get(k) for k in ("code", "message", "_http")} if not r.get("id") else "")
        if not r.get("id"):
            continue
        j = finish(ctx, tag, {"job": r["id"]}, 900)
        o = output(ctx, j, tag) if j.get("status") == "completed" else None
        if o:
            ctx.check(f"{tag}: a 3 s preview strip without sound", abs(o["dur"] - 3.0) < 0.3 and not o["audio"], f"{o['dur']:.2f}s {o['w']}x{o['h']} audio={o['audio']}")
            src = f"{ctx.OUT}/mask_source.mp4"
            open(src, "wb").write(rd("src_3s_480p.mp4"))
            pv, sv = frame(o["path"], 1.0, 256, 74), frame(src, 1.0, 256, 74)
            tint = lambda im: frac(im.crop((0, 0, 128, 74)), lambda r_, g, b: b - r_ > 50 and g > 90)
            ctx.check(f"{tag}: the left panel shows the tracked subject (mask tint)", pv is not None and tint(pv) > tint(sv) + 0.01, f"tinted {tint(pv):.3f} vs source {tint(sv):.3f}")

    # (3) object edit: Studio, vehicle replacement by words only (the red bike becomes blue)
    bike = upload(ctx, "bike_clip.mp4", rd("bike_clip.mp4"))
    r = studio(ctx, "object", main, "vehicle_replacement", bike, [], {"text": "the dirt bike", "kind": "vehicle"}, {"change_to": "a blue dirt bike", "seed": 4})
    if r:
        j = finish(ctx, "object", r)
        o = output(ctx, j, "object") if j.get("status") == "completed" else None
        if o:
            secs = float(((r.get("plan") or {}).get("trim") or {}).get("seconds") or 3.0)
            ctx.check("object: length follows H3's frame grid", o["frames"] == grid_frames(secs) and 0 <= secs - o["dur"] < 0.75, f"{o['dur']:.2f}s {o['frames']} fr, plan {secs}s -> grid {grid_frames(secs)}")
            ctx.check("object: the output carries an audio track (the source is silent, so it is silent)", o["audio"])
            src = f"{ctx.OUT}/object_source.mp4"
            open(src, "wb").write(rd("bike_clip.mp4"))
            red = lambda im: frac(im, lambda r_, g, b: r_ > 140 and g < 100 and b < 100)
            blue = lambda im: frac(im, lambda r_, g, b: b > 110 and b - r_ > 40)
            a, b = frame(src, 1.0, 128, 72), frame(o["path"], 1.0, 128, 72)
            ctx.check("object: the red bike became blue (red share fell, blue share rose)", red(b) < red(a) * 0.4 and blue(b) - blue(a) > 0.01,
                      f"red {red(a):.3f}->{red(b):.3f} blue {blue(a):.3f}->{blue(b):.3f}")

    # (4) reference to video: Studio, four reference pictures named in the prompt
    if ref:
        prompt = ("A woman with long red hair <Picture 1>, a woman in a yellow coat <Picture 2>, a bearded man in a blue hoodie <Picture 3> and a grey-haired man <Picture 4> "
                  "stand together on a rainy harbour walkway at dusk, talking quietly.")
        r = studio(ctx, "ref2v", ref, "reference_to_video", woman, [pic["ref_woman_red.png"], pic["ref_woman_yellow_coat.png"], pic["ref_man_beard.png"], pic["ref_man_grey.png"]],
                   {"text": "", "kind": "person"}, {"prompt": prompt, "aspect": "16:9", "seconds": 2.5, "seed": 4})
        if r:
            sent = (r.get("accounting") or {}).get("sent_to_engine") or []
            ctx.check("ref2v: all four pictures were sent, in order", len(sent) == 4, len(sent))
            j = finish(ctx, "ref2v", r)
            o = output(ctx, j, "ref2v") if j.get("status") == "completed" else None
            if o:
                ctx.check("ref2v: 2.5 s asked, H3 makes 56 frames = 2.33 s at 864x480 (its grid)", o["frames"] == grid_frames(2.5) and abs(o["dur"] - 2.333) < 0.1 and o["w"] == 864 and o["h"] == 480,
                          f"{o['dur']:.2f}s {o['frames']} fr {o['w']}x{o['h']}")
                m_, mx = ctx.volume(o["path"])
                ctx.check("ref2v: generated audio present and not silent", o["audio"] and m_ is not None and m_ > -60, f"mean {m_} peak {mx}")

    # (5) cancel a running job: Studio swap of a 10 s clip, cancelled once it is sampling; ComfyUI must go idle
    long = upload(ctx, "boomerang_10s.mp4", rd("src_boomerang_10s_480p.mp4"))
    r = studio(ctx, "cancel", main, "character_replacement", long, [pic["ref_woman_yellow_coat.png"]], {"text": "the woman", "kind": "person"}, {"seed": 99})
    if r:
        jid, t0, seen = r["job"], time.time(), None
        while time.time() - t0 < 900:
            j = ctx.u.call("GET", f"/api/media/jobs/{jid}")
            if j.get("status") in ("completed", "failed", "cancelled"):
                break
            if "Generating" in (j.get("progress_text") or "") and (j.get("progress") or 0) > 0.1:
                seen = j
                break
            time.sleep(3)
        ctx.check("cancel: the job reached the sampler with real progress", seen is not None, f"{(seen or j).get('progress')} {(seen or j).get('progress_text')}")
        if seen is not None:
            t1 = time.time()
            ctx.u.call("POST", f"/api/media/jobs/{jid}/cancel", {})
            end = ctx.wait(jid, 120)
            ctx.check("cancel: the job ends as cancelled and keeps no output", end.get("status") == "cancelled" and not (end.get("assets") or []), f"{end.get('status')} {round(time.time() - t1)} s")
            idle_at = None
            for _ in range(60):
                try:
                    q = json.load(urllib.request.urlopen("http://127.0.0.1:8190/queue", timeout=5))
                except Exception:
                    q = {"queue_running": ["?"], "queue_pending": []}
                if not q.get("queue_running") and not q.get("queue_pending"):
                    idle_at = round(time.time() - t1, 1)
                    break
                time.sleep(1)
            ctx.check("cancel: ComfyUI's queue is empty (the GPU work stopped)", idle_at is not None, f"{idle_at} s after the request")
            util = []
            for _ in range(3):
                time.sleep(2)
                out = ctx.sh(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"]).stdout.strip().split()
                util.append(int(out[0]) if out and out[0].isdigit() else -1)
            ctx.check("cancel: GPU utilisation is back to ~0 %", max(util) <= 10, util)
