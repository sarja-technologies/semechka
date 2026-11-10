"""Imajutsu, Images page, Experimental tools and REA through Semechka's real API as the throwaway account (no instance-specific assumptions).

Inputs are small AI-generated pictures shipped next to this file (p2_imajutsu_inputs/: man, woman, teapot, kitchen, dog). Outputs and contact sheets go to ctx.OUT."""
import io, json, os, struct, subprocess, time, uuid
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageStat

HERE = os.path.dirname(os.path.abspath(__file__))
IN = os.path.join(HERE, "p2_imajutsu_inputs")
NEEDS = {"perms": ["mode.image", "mode.video", "mode.chat", "chat.attach"],
         "models": [("flux2-klein-4b", ["image"]), ("qwen-image-edit-2511", ["image"]), ("qwen-image-2512", ["image"]), ("z-image-turbo", ["image"]),
                    ("flux1-fill-dev", ["image"]), ("kokoro-tts", ["image"]), ("bg-remove", ["image"]), ("moge-relief", ["image"]),
                    ("reverse-video", ["video"])]}


# ------------------------------------------------------------------------------------------------ helpers
def upload(ctx, name, data):
    """The Library upload the pages use: one file as the raw body."""
    import urllib.request, urllib.error
    u = ctx.u
    req = urllib.request.Request(f"{ctx.BASE}/api/media/uploads?name={name}", method="POST", data=data,
                                 headers={"Origin": ctx.BASE, "x-csrf-token": u.csrf, "Content-Type": "application/octet-stream"})
    try:
        with u.op.open(req, timeout=300) as x:
            return json.loads(x.read())
    except urllib.error.HTTPError as e:
        return {"_http": e.code, "_body": e.read()[:200].decode(errors="replace")}


def pic(name):
    return open(os.path.join(IN, name + ".png"), "rb").read()


def first_asset(j):
    a = [x for x in (j.get("assets") or []) if x.get("id")]
    return a[0] if a else None


def img(data):
    return Image.open(io.BytesIO(data)).convert("RGB")


def sheet(ims, out, h=420, labels=None):
    ims = [i.convert("RGB") for i in ims]
    ims = [i.resize((max(1, round(i.width * h / i.height)), h)) for i in ims]
    s = Image.new("RGB", (sum(i.width for i in ims) + 6 * (len(ims) - 1), h), (30, 30, 30)); x = 0
    for i in ims:
        s.paste(i, (x, 0)); x += i.width + 6
    s.save(out)


def outside_diff(src, res, box):
    """Mean absolute difference between source and result OUTSIDE a fractional box {x,y,w,h}."""
    if res.size != src.size:
        res = res.resize(src.size)
    m = Image.new("L", src.size, 255); d = ImageDraw.Draw(m)
    W, H = src.size
    d.rectangle((box["x"] * W, box["y"] * H, (box["x"] + box["w"]) * W, (box["y"] + box["h"]) * H), fill=0)
    diff = ImageChops.difference(src, res).convert("L")
    return ImageStat.Stat(diff, mask=m).mean[0], ImageStat.Stat(diff, mask=Image.eval(m, lambda v: 255 - v)).mean[0]


def corr_small(a, b, n=32):
    """Layout similarity: correlation of the two pictures as 32x32 greyscale thumbnails (1 = same layout)."""
    x = list(a.convert("L").resize((n, n)).getdata()); y = list(b.convert("L").resize((n, n)).getdata())
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y)); sxx = sum((p - mx) ** 2 for p in x); syy = sum((q - my) ** 2 for q in y)
    return sxy / ((sxx * syy) ** 0.5 or 1)


def edge_corr(a, b, n=48):
    """Layout similarity that ignores brightness (a day picture re-lit at night keeps its edges): correlation of the two edge maps (related ~0.35-0.85, unrelated ~0.15)."""
    f = lambda i: list(i.convert("L").resize((n, n)).filter(ImageFilter.FIND_EDGES).getdata())
    x, y = f(a), f(b); mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((p - mx) * (q - my) for p, q in zip(x, y)); sxx = sum((p - mx) ** 2 for p in x); syy = sum((q - my) ** 2 for q in y)
    return sxy / ((sxx * syy) ** 0.5 or 1)


class Studio:
    def __init__(self, ctx):
        self.ctx = ctx

    def run(self, tag, spec, timeout=1500):
        """One Picture studio generation exactly as the page does it: project, then generate with the spec. Returns (job, first asset bytes, applied plan)."""
        u, ctx = self.ctx.u, self.ctx
        p = u.call("POST", "/api/imajutsu/projects", {"mode": spec["mode"]})
        if "_http" in p or not p.get("id"):
            ctx.check(f"{tag}: project created", False, p); return None, None, None
        g = u.call("POST", f"/api/imajutsu/projects/{p['id']}/generate", {"spec": spec, "count": 1})
        if "_http" in g or not g.get("job"):
            ctx.check(f"{tag}: generation accepted", False, (g.get("message") or g.get("detail") or g)); return None, None, g
        j = ctx.wait(g["job"], timeout)
        ok = j.get("status") == "completed"
        ctx.check(f"{tag}: completed on the real GPU", ok, f"{j.get('_seconds')} s {j.get('error') or ''}")
        a = first_asset(j)
        if not (ok and a):
            return j, None, g
        data = ctx.asset(a["id"]); open(f"{ctx.OUT}/{tag}.png", "wb").write(data)
        return j, data, g

    def models(self):
        c = self.ctx.u.call("GET", "/api/imajutsu/capabilities")
        return c


# ------------------------------------------------------------------------------------------------ the checks
def run(ctx):
    u = ctx.u
    S = Studio(ctx)
    caps = S.models()
    ctx.check("Picture studio capabilities are served", isinstance(caps, dict) and caps.get("modes") is not None, str(caps)[:120] if "modes" not in caps else "")
    modes = {m["id"]: m for m in caps.get("modes", [])}
    for mid in ("edit", "replace_subject", "reference", "restyle", "inpaint", "outpaint", "iphone_photo"):
        m = modes.get(mid) or {}
        rec = m.get("recommended") or {}
        ctx.check(f"mode {mid}: a model is recommended and usable", bool(rec.get("model_id")), f"{rec.get('alias')} {m.get('reason') or ''}")
    up = modes.get("upscale") or {}
    ctx.check("upscale: refine model offered", bool((up.get("refine") or {}).get("available")), str((up.get("refine") or {}).get("recommended", {}).get("alias")))

    man, woman, tea, kit, dog = (upload(ctx, f"{n}.png", pic(n)) for n in ("man", "woman", "teapot", "kitchen", "dog"))
    ctx.check("synthetic inputs uploaded to the Library", all(x.get("id") for x in (man, woman, tea, kit, dog)), [x for x in (man, woman, tea, kit, dog) if not x.get("id")])
    if not all(x.get("id") for x in (man, woman, tea, kit, dog)):
        return
    A = lambda x: x["id"]
    src_man, src_kit = img(pic("man")), img(pic("kitchen"))

    # (1) the Images page with an uploaded reference on FLUX.2 klein: a conversation of its own, attachments + source_id, media settings
    try:
        km = next((x for x in (u.call("GET", "/api/media/options?kind=image").get("models") or []) if "klein" in (x.get("alias") or "").lower() or "klein" in (x.get("display_name") or "").lower()), None)
        ctx.check("Images page: FLUX.2 klein is offered", bool(km))
        if km:
            c = u.call("POST", "/api/chat/conversations", {"model_id": km["id"], "mode": "chat"})
            u.call("PATCH", f"/api/chat/conversations/{c['id']}", {"title": "Images (made from a reference)", "archived": True})
            r = u.call("POST", f"/api/chat/conversations/{c['id']}/generate", {"mode": "send", "operation": "image", "content": "The same kitchen at night with warm lamps switched on",
                                                                              "model_id": km["id"], "attachments": [A(kit)], "source_id": A(kit), "media": {}, "parent_id": None})
            job = ((r.get("message") or {}).get("media") or {}).get("id")
            ctx.check("Images page: reference + words accepted as a job", bool(job), str(r)[:200] if not job else "")
            if job:
                j = ctx.wait(job, 900); a = first_asset(j)
                ctx.check("Images page: reference run completed", j.get("status") == "completed" and bool(a), f"{j.get('_seconds')} s {j.get('error') or ''}")
                if a:
                    res = img(ctx.asset(a["id"])); res.save(f"{ctx.OUT}/p1_images_reference.png"); sheet([src_kit, res], f"{ctx.OUT}/p1_sheet.png")
                    ctx.check("Images page: a new picture at the reference's size that is not a copy (relit scenes are re-composed; same-scene is judged on p1_sheet.png)", res.size == src_kit.size and ImageStat.Stat(ImageChops.difference(src_kit, res.resize(src_kit.size)).convert("L")).mean[0] > 10,
                              f"edge corr {edge_corr(src_kit, res.resize(src_kit.size)):.2f} size {res.size}")
    except Exception as e:
        ctx.check("Images page flow ran", False, f"{type(e).__name__}: {e}")

    # (2) multi-picture edit: source + 2 references (3 pictures)
    try:
        j, d, g = S.run("p2_edit3", {"mode": "edit", "prompt": "Keep the kitchen of image 1. Put the blue teapot from image 2 on the wooden table and the golden retriever from image 3 sitting on the floor next to the table.",
                                      "source_asset": A(kit), "references": [{"asset": A(tea)}, {"asset": A(dog)}], "aspect": {"id": "source"}})
        if d:
            res = img(d); sheet([src_kit, img(pic("teapot")), img(pic("dog")), res], f"{ctx.OUT}/p2_sheet.png")
            used = ((g.get("plan") or {}).get("references") or {}).get("used") or g.get("accounting", {}).get("sent_to_engine") or []
            ctx.check("multi-picture edit: both references reached the engine", len(used) == 2, f"model {((g.get('accounting') or {}).get('model'))} sent {len(used)}")
            ctx.check("multi-picture edit: kitchen kept, picture changed", corr_small(src_kit, res) > 0.6 and ImageStat.Stat(ImageChops.difference(src_kit, res.resize(src_kit.size)).convert("L")).mean[0] > 4, f"corr {corr_small(src_kit, res):.2f}")
    except Exception as e:
        ctx.check("multi-picture edit ran", False, f"{type(e).__name__}: {e}")

    # (3) reference mode: two pictures, no source
    try:
        j, d, g = S.run("p3_reference", {"mode": "reference", "prompt": "The woman from image 1 sits at a cafe table holding the blue teapot from image 2, warm afternoon light.",
                                         "references": [{"asset": A(woman)}, {"asset": A(tea)}], "aspect": {"id": "9:16"}})
        if d:
            res = img(d); sheet([img(pic("woman")), img(pic("teapot")), res], f"{ctx.OUT}/p3_sheet.png")
            ctx.check("reference mode: a new picture of the asked shape", res.height > res.width, f"{res.size}")
    except Exception as e:
        ctx.check("reference mode ran", False, f"{type(e).__name__}: {e}")

    # (4) restyle: style changes, content stays
    try:
        j, d, g = S.run("p4_restyle", {"mode": "restyle", "prompt": "Restyle as a watercolour painting with soft washes of colour. Keep the same objects and composition.", "source_asset": A(kit), "aspect": {"id": "source"}})
        if d:
            res = img(d); sheet([src_kit, res], f"{ctx.OUT}/p4_sheet.png")
            cr = corr_small(src_kit, res); df = ImageStat.Stat(ImageChops.difference(src_kit, res.resize(src_kit.size)).convert("L")).mean[0]
            ctx.check("restyle: same composition (layout correlation) and visibly different rendering", cr > 0.6 and df > 6, f"corr {cr:.2f} mean diff {df:.1f} model {(g.get('accounting') or {}).get('model')}")
    except Exception as e:
        ctx.check("restyle ran", False, f"{type(e).__name__}: {e}")

    # (5) replace subject with a selection box: pixels outside the box unchanged in the engine's picture
    try:
        box = {"x": 0.0, "y": 0.15, "w": 1.0, "h": 0.85}
        j, d, g = S.run("p5_replace", {"mode": "replace_subject", "prompt": "the man in the red jacket", "source_asset": A(man), "references": [{"asset": A(woman)}], "aspect": {"id": "source"},
                                       "selection": {"box": box, "feather": 0, "invert": False, "keep_outside": None}})
        if d:
            res = img(d); sheet([src_man, img(pic("woman")), res], f"{ctx.OUT}/p5_sheet.png")
            mode = (g.get("accounting") or {}).get("engine_mode")
            out_d, in_d = outside_diff(src_man, res, box)
            ctx.check("replace subject: ran as a masked edit with the selection sent", mode == "inpaint" and bool((g.get("accounting") or {}).get("mask")), f"engine_mode {mode}")
            ctx.check("replace subject: pixels outside the selection unchanged", out_d < 1.5, f"outside mean diff {out_d:.2f}, inside {in_d:.1f}")
            ctx.check("replace subject: inside the selection it changed", in_d > 20, f"inside mean diff {in_d:.1f}")
    except Exception as e:
        ctx.check("replace subject ran", False, f"{type(e).__name__}: {e}")

    # (6) upscale + refine: scaled 1280x720 -> 2048x1152, then the model pass
    try:
        j, d, g = S.run("p6_upscale_refine", {"mode": "upscale", "source_asset": A(kit), "aspect": {"id": "source"}, "size": {"native": None, "output": {"width": 2048, "height": 1152}},
                                              "upscale": {"refine": True, "strength": 0.3, "engine": "lanczos"}, "prompt": ""})
        if d:
            res = img(d); lz = src_kit.resize((2048, 1152), Image.LANCZOS); sheet([lz, res], f"{ctx.OUT}/p6_sheet.png", h=500)
            ctx.check("upscale + refine: exactly 2048x1152", res.size == (2048, 1152), f"{res.size}")
            ctx.check("upscale + refine: same picture (layout kept), texture re-drawn", corr_small(lz, res) > 0.9 and ImageStat.Stat(ImageChops.difference(lz, res).convert("L")).mean[0] > 0.8,
                      f"corr {corr_small(lz, res):.3f} diff {ImageStat.Stat(ImageChops.difference(lz, res).convert('L')).mean[0]:.2f}")
    except Exception as e:
        ctx.check("upscale + refine ran", False, f"{type(e).__name__}: {e}")

    # (7) inpaint with a mask picture from the Library
    try:
        W, H = src_kit.size; mk = Image.new("L", (W, H), 0); ImageDraw.Draw(mk).rectangle((int(W * .45), int(H * .62), int(W * .75), int(H * .80)), fill=255)
        b = io.BytesIO(); mk.save(b, "PNG"); mask = upload(ctx, "mask.png", b.getvalue())
        ctx.check("inpaint: mask picture uploaded", bool(mask.get("id")), mask)
        j, d, g = S.run("p7_inpaint", {"mode": "inpaint", "prompt": "A wooden cutting board with a loaf of bread", "source_asset": A(kit), "mask_asset": mask.get("id"), "aspect": {"id": "source"}})
        if d:
            res = img(d); sheet([src_kit, mk.convert("RGB"), res], f"{ctx.OUT}/p7_sheet.png")
            box = {"x": .45, "y": .62, "w": .30, "h": .18}; out_d, in_d = outside_diff(src_kit, res, box)
            ctx.check("inpaint: mask sent, outside the mask essentially unchanged, inside changed", bool((g.get("accounting") or {}).get("mask")) and out_d < 8 and in_d > 6,
                      f"model {(g.get('accounting') or {}).get('model')} outside {out_d:.2f} inside {in_d:.1f}")
    except Exception as e:
        ctx.check("inpaint ran", False, f"{type(e).__name__}: {e}")

    # (8) iPhone Photo: 9:16 exact size on the Fast tier and on the default (Best realism) tier
    for tier, tag in (("fast", "p8_iphone_fast"), (None, "p8_iphone_best")):
        try:
            j, d, g = S.run(tag, {"mode": "iphone_photo", "prompt": "A friend laughing at a night market", "aspect": {"id": "9:16"},
                                  "preset": {"id": "iphone_photo", "scenes": ["night"], "finish": False, "tier": tier}})
            if d:
                res = img(d); ctx.check(f"iPhone Photo ({tier or 'default tier'}): exactly 720x1280 (9:16)", res.size == (720, 1280), f"{res.size} model {(g.get('accounting') or {}).get('model')}")
        except Exception as e:
            ctx.check(f"iPhone Photo {tier} ran", False, f"{type(e).__name__}: {e}")

    # (9) Experimental tools: speech, background removal, 3D relief
    tools = {t.get("tool"): t for t in (u.call("GET", "/api/media/extras").get("tools") or [])}
    ctx.check("Experimental tools offered to the account", bool(tools), sorted(tools))

    def tool(tid, tag, inputs, files=None, timeout=900):
        t = tools.get(tid)
        ctx.check(f"tool {tid}: offered and ready", bool(t) and t.get("ready") is not False, (t or {}).get("reason") or "")
        if not t:
            return None, None
        fm = {"model_id": t["id"], "save": "true", "inputs": json.dumps(inputs)}
        for k, aid in (files or {}).items():
            fm[f"asset:{k}"] = aid
        r = u.call("POST", "/api/media/extras/jobs", multipart=fm)
        ctx.check(f"tool {tid}: job accepted", bool(r.get("id")), r if not r.get("id") else "")
        if not r.get("id"):
            return None, None
        j = ctx.wait(r["id"], timeout)
        ctx.check(f"tool {tid}: completed", j.get("status") == "completed", f"{j.get('_seconds')} s {j.get('error') or ''}")
        a = first_asset(j)
        return j, (ctx.asset(a["id"]) if a else None)

    try:
        text = "Imajutsu acceptance test. The quick brown fox jumps over the lazy dog."
        j, d = tool("kokoro-tts", "tts", {"text": text, "voice": "af_heart", "speed": 1.0})
        if d:
            p = f"{ctx.OUT}/p9_speech.wav"; open(p, "wb").write(d); info = ctx.probe(p); dur = float(info.get("format", {}).get("duration", 0)); mean, mx = ctx.volume(p)
            ctx.check("speech: a WAV of a plausible length for the text (about 4-9 s) that is not silent", 3 <= dur <= 12 and mean is not None and mean > -45, f"{dur:.2f} s mean {mean} dB peak {mx}")
    except Exception as e:
        ctx.check("speech ran", False, f"{type(e).__name__}: {e}")
    try:
        j, d = tool("bg-remove", "bg", {}, {"image": A(woman)})
        if d:
            im = Image.open(io.BytesIO(d)); im.save(f"{ctx.OUT}/p9_cutout.png")
            al = im.convert("RGBA").getchannel("A"); h = al.histogram(); tr = sum(h[:16]) / sum(h); op = sum(h[240:]) / sum(h)
            comp = Image.new("RGB", im.size, (255, 0, 255)); comp.paste(im.convert("RGBA"), mask=al); sheet([img(pic("woman")), comp], f"{ctx.OUT}/p9_bg_sheet.png")
            ctx.check("background removal: has an alpha channel with both transparent and opaque areas", im.mode == "RGBA" and tr > 0.1 and op > 0.1, f"mode {im.mode} transparent {tr:.2f} opaque {op:.2f}")
    except Exception as e:
        ctx.check("background removal ran", False, f"{type(e).__name__}: {e}")
    try:
        j, d = tool("moge-relief", "3d", {}, {"image": A(tea)}, timeout=900)
        if d:
            p = f"{ctx.OUT}/p9_relief.glb"; open(p, "wb").write(d)
            ok = len(d) > 12 and d[:4] == b"glTF"
            ver, ln = struct.unpack("<II", d[4:12]) if ok else (0, 0)
            clen, ctype = struct.unpack("<II", d[12:20]) if ok else (0, 0)
            doc = json.loads(d[20:20 + clen]) if ok and ctype == 0x4E4F534A else {}
            ctx.check("3D relief: a valid GLB (version 2, length matches, JSON chunk with a mesh)", ok and ver == 2 and ln == len(d) and bool(doc.get("meshes")), f"{len(d)} bytes meshes={len(doc.get('meshes') or [])} materials={len(doc.get('materials') or [])}")
    except Exception as e:
        ctx.check("3D relief ran", False, f"{type(e).__name__}: {e}")

    # (10) REA on a short generated clip: two shots made from the AI-generated stills
    try:
        clip = f"{ctx.OUT}/p10_clip.mp4"
        sh = ["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", "3", "-i", os.path.join(IN, "kitchen.png"), "-loop", "1", "-t", "3", "-i", os.path.join(IN, "teapot.png"),
              "-filter_complex", "[0:v]scale=640:360,setsar=1,fps=24[a];[1:v]scale=640:360,setsar=1,fps=24[b];[a][b]concat=n=2:v=1:a=0,format=yuv420p", "-c:v", "libx264", "-crf", "28", clip]
        subprocess.run(sh, check=True)
        va = upload(ctx, "rea_clip.mp4", open(clip, "rb").read())
        ctx.check("REA: clip uploaded", bool(va.get("id")), va)
        rm = ctx.model("reverse-video", "video")
        ctx.check("REA: the analysis profile is offered", bool(rm))
        if rm and va.get("id"):
            schema = ((rm.get("profile") or {}).get("inputs_schema")) or {}
            inputs = {**({"operation": "analyse"} if schema.get("operation") else {}), **({"vision": "auto"} if schema.get("vision") else {})}
            r = u.call("POST", "/api/media/profile-jobs", multipart={"model_id": rm["id"], "save": "true", "inputs": json.dumps(inputs), "asset:video": va["id"]})
            ctx.check("REA: job accepted", bool(r.get("id")), r if not r.get("id") else "")
            if r.get("id"):
                j = ctx.wait(r["id"], 900)
                ctx.check("REA: analysis completed", j.get("status") == "completed", f"{j.get('_seconds')} s {j.get('error') or ''}")
                full = u.call("GET", f"/api/media/jobs/{r['id']}/analysis")
                json.dump(full, open(f"{ctx.OUT}/p10_analysis.json", "w"), indent=1)
                an = full.get("analysis") or full
                shots = an.get("shots") or []
                ctx.check("REA: measured analysis with 2 shots found", len(shots) >= 2, f"shots {len(shots)}")
                v = an.get("vision") or {}
                described = [s for s in shots if ((s.get("vision") or {}).get("description") or (s.get("vision") or {}).get("text"))]
                if v.get("available") or described:
                    ctx.check("REA vision: every shot has a scene description", len(described) == len(shots) and len(shots) > 0, f"{len(described)}/{len(shots)} model {v.get('model')}")
                    for s in described[:3]:
                        print("   shot", s.get("index", ""), "->", str((s.get("vision") or {}).get("description") or (s.get("vision") or {}).get("text"))[:160], flush=True)
                else:
                    print(f"NOTE  REA vision not available on this instance: {str(v.get('reason') or v)[:200]} (technical analysis only)", flush=True)
                a = first_asset(j)
                if a:
                    open(f"{ctx.OUT}/p10_contact_sheet.jpg", "wb").write(ctx.asset(a["id"]))
    except Exception as e:
        ctx.check("REA ran", False, f"{type(e).__name__}: {e}")
