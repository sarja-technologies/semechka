#!/usr/bin/env python3
"""iPhone Photo battery on ONE instance through the chat API and the real GPU (run ON the Spark): the same prompts for Best realism (Qwen-Image
2512, 30 steps) and Fast (FLUX.2 klein), every picture saved to --out, so they can be looked at side by side for phones in hands, plastic skin,
heavy sharpening or any other sign of processing.  usage: preset_battery.py --base URL --creds FILE --user EMAIL --out DIR [--only qwen|klein]"""
import argparse, http.cookiejar, json, os, re, sys, time, urllib.request, urllib.error
from struct import unpack
ap = argparse.ArgumentParser(); ap.add_argument("--base", required=True); ap.add_argument("--creds", required=True); ap.add_argument("--user", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--only", choices=["qwen", "klein"]); A = ap.parse_args()
BASE = A.base.rstrip("/"); OUT = os.path.expanduser(A.out); os.makedirs(OUT, exist_ok=True)
CREDS = dict(l.split("\t", 1) for l in open(os.path.expanduser(A.creds)).read().splitlines() if "\t" in l and not l.startswith("#"))
jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)); csrf = ""
def call(m, p, body=None, raw=False):
    h = {"Content-Type": "application/json", "Origin": BASE}
    if csrf and m != "GET": h["x-csrf-token"] = csrf
    r = urllib.request.Request(BASE + p, method=m, data=json.dumps(body).encode() if body is not None else None, headers=h)
    try:
        with op.open(r, timeout=600) as x:
            b = x.read(); return b if raw else (json.loads(b) if b else {})
    except urllib.error.HTTPError as e: return {"_http": e.code, "raw": e.read()[:200].decode("utf8", "replace")}
call("POST", "/api/auth/login", {"email": A.user, "password": CREDS[A.user].strip()}); csrf = call("GET", "/api/me")["csrf"]
opts = call("GET", "/api/media/options?kind=image")
find = lambda rx: next((m for m in opts["models"] if re.search(rx, (m.get("alias") or "") + " " + (m.get("real_id") or ""), re.I)), None)
M = {"qwen": find(r"2512"), "klein": find(r"klein")}
BATTERY = [  # (name, words typed, scene chips)
    ("market", "my friend laughing at a street market", ["outdoor_day", "casual"]),
    ("family", "a family having dinner at home, the kids talking over each other", ["indoor", "casual"]),
    ("night", "two friends outside a kebab shop late at night", ["night", "casual"]),
    ("pet", "my dog asleep on the living room rug", ["indoor", "casual"]),
    ("beach", "my brother and his girlfriend walking along the beach at sunset", ["outdoor_day"]),
    ("cafe", "a coffee and a notebook on a cafe table by the window", ["indoor", "casual"]),
    ("selfie", "me and my friend at a birthday party", ["selfie", "indoor"]),
    ("mirror", "a woman checking her outfit in a hallway mirror before going out", ["mirror", "indoor"]),
]
conv = call("POST", "/api/chat/conversations", {"model_id": call("GET", "/api/models/catalog?operation=text")["default"]}); cid = conv["id"]
for key in ([A.only] if A.only else ["klein", "qwen"]):
    for name, words, scenes in BATTERY:
        t0 = time.time()
        r = call("POST", f"/api/chat/conversations/{cid}/generate", {"operation": "image", "content": words, "model_id": M[key]["id"], "preset": "iphone_photo", "scenes": scenes})
        jid = ((r.get("message") or {}).get("media") or {}).get("id")
        if not jid: print(f"{key}/{name}: not accepted {r.get('_http')} {r.get('raw') or r.get('detail')}"); continue
        while True:
            j = call("GET", f"/api/media/jobs/{jid}")
            if j.get("status") in ("completed", "failed", "cancelled") or time.time() - t0 > 1200: break
            time.sleep(3)
        for _ in range(20):          # the outputs can arrive a moment after the status says completed
            if j.get("status") != "completed" or j.get("outputs") or j.get("assets"): break
            time.sleep(2); j = call("GET", f"/api/media/jobs/{jid}")
        outs = j.get("outputs") or j.get("assets") or []; aid = outs[0].get("id") if outs and isinstance(outs[0], dict) else (outs[0] if outs else None)
        if j.get("status") == "completed" and aid:
            d = call("GET", f"/api/media/assets/{aid}/content", raw=True); open(f"{OUT}/{key}-{name}.png", "wb").write(d)
            w, h = unpack(">II", d[16:24]); print(f"{key}/{name}: {w}x{h} in {time.time()-t0:.0f}s", flush=True)
        else: print(f"{key}/{name}: {j.get('status')} {j.get('error')}", flush=True)
print("done ->", OUT)
