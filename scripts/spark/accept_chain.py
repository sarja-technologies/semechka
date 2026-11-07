#!/usr/bin/env python3
"""Acceptance of the whole chain on ONE instance, through its real HTTP API and the real GPU (run ON the Spark; passwords come from a 0600
credentials file and are never printed):

  chat prompt -> model -> (reference) -> real GPU generation -> saved media -> the person's library -> the authorised administrator's explorer

    accept_chain.py --base http://192.168.20.193:8788 --creds ~/semechka-deploy/secrets/salted-credentials.txt \
                    --user member@salted.test --admin owner@salted.test --out ~/imajutsu-bench/accept-salted [--setup-admin] [--skip-edit]

  --setup-admin   set privacy.admin_content_access=audited and grant admin.content.read (+ .instance) BY NAME to --admin, as that admin,
                  through the same audited API as the Administration screens. Without it the instance's policy is left as it is.
  Models are found by what they are (klein, 2512, edit), whatever the instance calls them.
"""
import argparse, http.cookiejar, json, os, re, sys, time, urllib.request, urllib.error
from struct import unpack

ap = argparse.ArgumentParser()
ap.add_argument("--base", required=True); ap.add_argument("--creds", required=True); ap.add_argument("--user", required=True); ap.add_argument("--admin", required=True)
ap.add_argument("--out", required=True); ap.add_argument("--setup-admin", action="store_true"); ap.add_argument("--skip-edit", action="store_true")
A = ap.parse_args()
BASE = A.base.rstrip("/"); OUT = os.path.expanduser(A.out); os.makedirs(OUT, exist_ok=True)
CREDS = dict(l.split("\t", 1) for l in open(os.path.expanduser(A.creds)).read().splitlines() if "\t" in l and not l.startswith("#"))
RES = []


def check(n, ok, d=""):
    RES.append(bool(ok)); print(("PASS  " if ok else "FAIL  ") + n + (": " + str(d)[:200] if d != "" else ""), flush=True)


class C:
    def __init__(self, email):
        self.jar = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)); self.csrf = ""; self.email = email
        r = self.call("POST", "/api/auth/login", {"email": email, "password": CREDS[email].strip()})
        if "_http" in r: sys.exit(f"sign-in failed for {email}: {r['_http']}")
        self.csrf = self.call("GET", "/api/me")["csrf"]

    def call(self, method, path, body=None, raw=False):
        h = {"Content-Type": "application/json", "Origin": BASE}
        if self.csrf and method != "GET": h["x-csrf-token"] = self.csrf
        r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with self.op.open(r, timeout=600) as x:
                b = x.read(); return b if raw else (json.loads(b) if b else {})
        except urllib.error.HTTPError as e:
            b = e.read()
            try: d = json.loads(b)
            except Exception: d = {"raw": b[:200].decode("utf8", "replace")}
            return {"_http": e.code, **(d if isinstance(d, dict) else {"body": d})}


def wait_job(c, jid, limit=1800):
    t0 = time.time()
    while time.time() - t0 < limit:
        j = c.call("GET", f"/api/media/jobs/{jid}")
        if j.get("status") in ("completed", "failed", "cancelled"): return j, time.time() - t0
        time.sleep(3)
    return j, time.time() - t0


def first_asset(j):
    outs = j.get("outputs") or j.get("assets") or []
    return (outs[0].get("id") if outs and isinstance(outs[0], dict) else outs[0]) if outs else None


user, admin = C(A.user), C(A.admin)
opts = user.call("GET", "/api/media/options?kind=image")
find = lambda rx: next((m for m in opts.get("models", []) if re.search(rx, (m.get("alias") or "") + " " + (m.get("real_id") or ""), re.I)), None)
M = {"klein": find(r"klein"), "qwen": find(r"2512"), "edit": find(r"image[- ]?edit|edit[- ]?2511")}
check("the person sees Klein, Qwen-Image 2512 and Qwen-Image-Edit", all(M.values()), {k: (v or {}).get("alias") for k, v in M.items()})
if not all(M.values()): sys.exit(1)
conv = user.call("POST", "/api/chat/conversations", {"model_id": user.call("GET", "/api/models/catalog?operation=text")["default"]})
cid = conv["id"]; made = {}


def gen(key, prompt, **kw):
    return user.call("POST", f"/api/chat/conversations/{cid}/generate", {"operation": "image", "content": prompt, "model_id": M[key]["id"], **kw})


def finish(key, r, limit=1800):
    ok = "message" in r and (r["message"].get("media") or {}).get("id")
    check(f"chat accepted the {key} job", ok, r.get("_http") or r.get("detail") or "")
    if not ok: return None
    jid = r["message"]["media"]["id"]; j, secs = wait_job(user, jid, limit)
    check(f"{key} completed on the GPU", j["status"] == "completed", f"{secs:.0f} s, {j['status']} {j.get('error') or ''}")
    aid = first_asset(j)
    if aid:
        data = user.call("GET", f"/api/media/assets/{aid}/content", raw=True); open(f"{OUT}/{key}.png", "wb").write(data)
        w, h = unpack(">II", data[16:24]) if data[:4] == b"\x89PNG" else (0, 0)
        made[key] = (jid, aid, w, h, round(secs))
        return made[key]
    check(f"{key} has an output", False, json.dumps(j)[:200]); return None


r = finish("qwen", gen("qwen", "two friends laughing outside a convenience store at night, wet pavement reflecting the shop signs", preset="iphone_photo", scenes=["night", "casual"]))
if r: check("Qwen-Image 2512 iPhone Photo is 720x1280 (9:16)", (r[2], r[3]) == (720, 1280), f"{r[2]}x{r[3]}")
r = finish("klein", gen("klein", "my friend laughing at a street market, afternoon", preset="iphone_photo", scenes=["outdoor_day", "casual"]))
if r: check("FLUX.2 Klein iPhone Photo is 720x1280 (9:16)", (r[2], r[3]) == (720, 1280), f"{r[2]}x{r[3]}")
if "qwen" in made and "klein" in made and not A.skip_edit:
    src, ref = made["qwen"][1], made["klein"][1]
    r = finish("edit", gen("edit", "Keep the first picture exactly as it is, but change the jacket of the person on the left to the jacket worn by the person in the second picture.", attachments=[src, ref], source_id=src, media={"width": 720, "height": 1280}), 2400)
lib = user.call("GET", "/api/media/assets?limit=100"); ids = {i["id"] for i in (lib if isinstance(lib, list) else lib.get("items", []))}
for k, v in made.items(): check(f"{k} result is in the person's own library", v[1] in ids)

# ---- administrator ------------------------------------------------------------------------------------------------------------------------------
r = user.call("POST", "/api/admin/content/explorer/media", {"reason": "an ordinary person must be refused", "filters": {}})
check("an ordinary person is refused by the explorer (403)", r.get("_http") == 403, r.get("_http"))
me = admin.call("GET", "/api/me"); aid_ = me.get("id") or me.get("user", {}).get("id")
if A.setup_admin:
    r = admin.call("PUT", "/api/admin/settings/privacy.admin_content_access", {"value": "audited", "confirm_weaker": True}); check("policy set to audited", "_http" not in r, r.get("_http") or "")
    ov = (admin.call("GET", f"/api/admin/users/{aid_}").get("override") or {})
    grants = sorted(set((ov.get("grants") or []) + ["admin.content.read", "admin.content.instance"]))
    r = admin.call("PUT", f"/api/admin/users/{aid_}/override", {"grants": grants, "denies": ov.get("denies") or []}); check("sensitive permissions granted by name to the named administrator", "_http" not in r, r.get("_http") or "")
R = lambda why: {"reason": f"Release acceptance: {why}"}
r = admin.call("POST", "/api/admin/content/explorer/conversations", {**R("find the test conversation"), "filters": {"user_id": (user.call("GET", "/api/me").get("id") or user.call("GET", "/api/me").get("user", {}).get("id"))}, "limit": 50})
check("the test conversation is discoverable by the administrator (filtered to the test person)", any(c["id"] == cid for c in r.get("items", [])), f"{len(r.get('items', []))} listed; scope={r.get('scope')}")
r = admin.call("POST", f"/api/admin/content/conversations/{cid}", R("open the conversation in context"))
msgs = r.get("messages", []); check("the conversation opens with its generated media in context", sum(1 for m in msgs if m.get("media")) >= len(made), f"{len(msgs)} messages")
r = admin.call("POST", "/api/admin/content/explorer/media", {**R("central media library"), "filters": {"conversation_id": cid, "status": "completed"}, "limit": 40})
check("the media library lists every test generation (filter conversation + status)", len(r.get("items", [])) >= len(made), f"{len(r.get('items', []))} jobs")
for k, v in made.items():
    d = admin.call("POST", f"/api/admin/content/explorer/media/jobs/{v[0]}", R(f"inspect the {k} generation")); p = d.get("params") or {}
    check(f"administrator opens the {k} job: parameters, workflow and operation", d.get("id") == v[0] and d.get("status") == "completed", f"workflow={d.get('workflow')} op={d.get('operation')} model={(d.get('model') or {}).get('alias')} {p.get('width')}x{p.get('height')} steps={p.get('steps')}")
    blob = json.dumps(d).lower(); check(f"{k}: no secrets in the response", not any(s in blob for s in ("api_key", "authorization", "password", "bearer ", "token")))
    outs = d.get("outputs") or []
    if outs:
        fa = admin.call("POST", "/api/admin/content/explorer/files/access", {**R(f"preview the {k} output"), "type": "asset", "id": outs[0]["id"], "mode": "view"})
        data = admin.call("GET", fa["url"].replace(BASE, ""), raw=True) if "url" in fa else b""
        check(f"administrator previews the {k} picture (streamed from the existing store)", data[:4] == b"\x89PNG" or data[:3] == b"\xff\xd8\xff", f"{len(data)//1024} KiB")
    if k == "edit":
        check("the edit job lists its source and reference pictures as inputs", len(d.get("inputs") or []) >= 2, [(i.get("role"), i.get("state")) for i in d.get("inputs") or []])
        check("an edit is labelled 'edit', not 'generate'", d.get("operation") == "edit", d.get("operation"))
if made:
    fa = admin.call("POST", "/api/admin/content/explorer/files/access", {**R("download"), "type": "asset", "id": list(made.values())[0][1], "mode": "download"}); check("download link issued (and audited)", "url" in fa, fa.get("_http"))
ca = user.call("GET", "/api/me/content-access"); acts = sorted({e.get("kind") or e.get("action") for e in (ca.get("events") or ca.get("items") or [])})
check("the content owner can see who accessed it", len(acts) >= 3, acts)
json.dump({"cid": cid, "made": made}, open(f"{OUT}/made.json", "w"))
print(f"\n{sum(RES)}/{len(RES)} checks passed"); sys.exit(0 if all(RES) else 1)
