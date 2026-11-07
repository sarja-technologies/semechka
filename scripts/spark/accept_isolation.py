#!/usr/bin/env python3
"""Organisation isolation of the administrator explorer on a LIVE instance, read-only and without reading anyone's content (run ON the Spark).
The throwaway administrator belongs to ONE organisation; people of the other organisations must be invisible and unreachable: the people filter does
not list them, asking for their conversations or media by id answers 'not found' (indistinguishable from missing), and listing everything shows only
people of the administrator's own organisation.   accept_isolation.py --base URL --creds FILE --admin EMAIL --data DATA_DIR"""
import argparse, http.cookiejar, json, os, sqlite3, sys, urllib.request, urllib.error
ap = argparse.ArgumentParser(); [ap.add_argument(f"--{k}", required=True) for k in ("base", "creds", "admin", "data")]; A = ap.parse_args()
BASE = A.base.rstrip("/"); CR = dict(l.split("\t", 1) for l in open(os.path.expanduser(A.creds)).read().splitlines() if "\t" in l and not l.startswith("#"))
jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar)); csrf = ""
def call(m, p, b=None):
    h = {"Content-Type": "application/json", "Origin": BASE, **({"x-csrf-token": csrf} if csrf and m != "GET" else {})}
    try:
        with op.open(urllib.request.Request(BASE + p, method=m, data=json.dumps(b).encode() if b is not None else None, headers=h), timeout=60) as x: return x.status, json.load(x)
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, {}
call("POST", "/api/auth/login", {"email": A.admin, "password": CR[A.admin].strip()}); csrf = call("GET", "/api/me")[1]["csrf"]
con = sqlite3.connect(f"file:{os.path.expanduser(A.data)}/semechka.db?mode=ro", uri=True)
mine = {r[0] for r in con.execute("select om.user_id from org_members om where om.status='active' and om.org_id in (select org_id from org_members where user_id=(select id from users where email=?) and status='active')", (A.admin,))}
others = [(r[0], r[1]) for r in con.execute("select id,email from users") if r[0] not in mine]
RES = []
def check(n, ok, d=""): RES.append(bool(ok)); print(("PASS  " if ok else "FAIL  ") + n + (": " + str(d)[:160] if d != "" else ""))
R = {"reason": "Release acceptance: organisation isolation check (no content is read)"}
code, f = call("POST", "/api/admin/content/explorer/conversations", {**R, "filters": {}, "limit": 100})
listed = {c.get("owner", {}).get("id") or c.get("user_id") for c in f.get("items", [])}
check("listing everything shows only people of the administrator's own organisation", code == 200 and not (listed & {o[0] for o in others}), f"{len(f.get('items', []))} conversations, {len(listed)} owners, scope={f.get('scope')}")
code, fl = call("GET", "/api/admin/content/explorer/filters"); ppl = json.dumps(fl)
check("the people filter does not list anyone from another organisation", code == 200 and not any(e in ppl for _, e in others), [e for _, e in others if e in ppl] or "none of the others")
for uid, email in others:
    c1, r1 = call("POST", "/api/admin/content/explorer/conversations", {**R, "filters": {"user_id": uid}, "limit": 5})
    c2, r2 = call("POST", "/api/admin/content/explorer/media", {**R, "filters": {"user_id": uid}, "limit": 5})
    ok = all((c in (403, 404) or (c == 200 and not r.get("items"))) for c, r in ((c1, r1), (c2, r2)))
    check(f"a person of another organisation is unreachable by id ({email.split('@')[0][:3]}…)", ok, f"conversations {c1}/{len(r1.get('items', []))}, media {c2}/{len(r2.get('items', []))}")
print(f"\n{sum(RES)}/{len(RES)} checks passed"); sys.exit(0 if all(RES) else 1)
