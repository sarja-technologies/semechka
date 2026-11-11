"""Quick regression: one image through the engine (Qwen-Image 2512)."""
NEEDS = {"perms": ["mode.image"], "models": [("qwen-image-2512", ["image"])]}


def run(ctx):
    q = ctx.model("qwen-image-2512", "image"); ctx.check("an image model is offered", bool(q))
    if not q: return
    r = ctx.u.call("POST", "/api/media/jobs", {"kind": "image", "model_id": q["id"], "prompt": "a green kettle on a wooden table, photo", "save": True})
    j = ctx.wait(r.get("id"), 900) if r.get("id") else {}
    ctx.check("image generated through the engine", j.get("status") == "completed", f"{j.get('_seconds')} s")
