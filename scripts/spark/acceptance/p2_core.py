"""Core regression: streamed chat with context, an image, H3 text-to-video, and the restored Wan 2.2 14B and HunyuanVideo 1.5 text-to-video (short clips)."""
import json
NEEDS = {"perms": ["mode.image", "mode.video"],
         "models": [("Qwen-Image 2512", ["image"]), ("minimax-h3", ["video"]), ("wan2.2-t2v-a14b", ["video"]), ("hunyuanvideo-1.5-t2v", ["video"])]}


def _video(ctx, alias, prompt, seconds, tag, expect_audio):
    m = ctx.model(alias, "video")
    ctx.check(f"{alias} is offered to the test account", bool(m))
    if not m: return
    r = ctx.u.call("POST", "/api/media/jobs", {"kind": "video", "model_id": m["id"], "prompt": prompt, "seconds": seconds, "save": True})
    p = r.get("params") or r.get("applied") or {}
    ctx.check(f"{alias}: job accepted ({seconds} s asked)", bool(r.get("id")), r if not r.get("id") else p)
    if not r.get("id"): return
    j = ctx.wait(r["id"], 2400)
    ctx.check(f"{alias}: completed on the real GPU", j.get("status") == "completed", f"{j.get('_seconds')} s {j.get('error') or ''}")
    outs = j.get("outputs") or j.get("assets") or []
    aid = (outs[0].get("id") if outs and isinstance(outs[0], dict) else (outs[0] if outs else None))
    if not aid: ctx.check(f"{alias}: has an output", False, j); return
    path = f"{ctx.OUT}/{tag}.mp4"; open(path, "wb").write(ctx.asset(aid))
    info = ctx.probe(path); dur = float(info.get("format", {}).get("duration", 0)); st = info.get("streams", [])
    v = next((s for s in st if s["codec_type"] == "video"), {}); a = next((s for s in st if s["codec_type"] == "audio"), None)
    ctx.check(f"{alias}: decodes, length close to what was asked", abs(dur - seconds) < 1.0 + 0.25 * seconds, f"{dur:.2f}s {v.get('width')}x{v.get('height')} frames={v.get('nb_frames')}")
    if expect_audio:
        mean, mx = ctx.volume(path); ctx.check(f"{alias}: audio present and not silent", a is not None and mean is not None and mean > -60, f"mean {mean} peak {mx}")
    else:
        ctx.check(f"{alias}: silent clip is declared silent (no audio stream)", a is None)
    dec = ctx.sh(["ffmpeg", "-v", "error", "-i", path, "-f", "null", "-"]).stderr.strip()
    ctx.check(f"{alias}: whole file decodes with no errors", dec == "", dec[:100])
    ctx.sheet(path, f"{ctx.OUT}/{tag}_sheet.png", every=max(1, int(v.get("nb_frames") or 30) // 6))


def run(ctx):
    u = ctx.u
    cat = u.call("GET", "/api/models/catalog?operation=text"); usable = [m for m in cat.get("models", []) if m.get("usable")]
    m = next((x for x in usable if x["alias"] in ("Balanced", "Quick")), usable[0] if usable else None); ctx.check("a text model is offered", bool(m), m and m["alias"])
    if m:
        cid = u.call("POST", "/api/chat/conversations", {"model_id": m["id"]})["id"]
        say = lambda t: u.stream(f"/api/chat/conversations/{cid}/generate", {"mode": "send", "content": t, "model_id": m["id"], "operation": "text"})
        say("Remember the code word PERSIMMON-3318. Reply with just OK."); say("What was the code word? Answer with the word only.")
        th = u.call("GET", f"/api/chat/conversations/{cid}/thread"); last = [x for x in (th.get("messages") or th.get("items") or []) if x.get("role") == "assistant"]
        ctx.check(f"real chat reply through the router ({m['alias']}) with multi-turn context", last and "PERSIMMON" in (last[-1].get("content") or "").upper())
    q = ctx.model("Qwen-Image 2512", "image"); ctx.check("an image model is offered", bool(q))
    if q:
        r = u.call("POST", "/api/media/jobs", {"kind": "image", "model_id": q["id"], "prompt": "a green kettle on a wooden table, photo", "save": True}); j = ctx.wait(r.get("id"), 900) if r.get("id") else {}
        ctx.check("image generated through the engine", j.get("status") == "completed", f"{j.get('_seconds')} s")
    _video(ctx, "minimax-h3", "rain on a quiet street at dusk, a red bicycle against a brick wall, steam rising from a grate, distant traffic hum", 5, "h3_t2v", True)
    _video(ctx, "wan2.2-t2v-a14b", "a small brown dog runs along a beach at sunset, waves rolling in, gentle camera follow", 2, "wan14_t2v", False)
    _video(ctx, "hunyuanvideo-1.5-t2v", "a paper boat floating on a calm pond, ripples spreading, soft morning light", 1, "hunyuan_t2v", False)
