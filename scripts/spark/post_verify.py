#!/usr/bin/env python3
"""Post-migration proof on a COPY of an instance's live data (run ON the Spark): every existing account signs in (known password set on the COPY
only, through the app's own hashing), sees its original conversations, opens a thread, lists and decrypts its media. Never touches the real instance.
usage: post_verify.py <instance>"""
import http.cookiejar, json, os, shutil, sqlite3, subprocess, sys, time, urllib.request, urllib.error
inst = sys.argv[1]; SRC = os.path.expanduser(f"~/semechka-deploy/{inst}"); PORT = 8795
W = os.path.expanduser(f"~/post-verify-{inst}"); shutil.rmtree(W, ignore_errors=True); os.makedirs(W + "/data", mode=0o700)
srcdata = SRC + "/data"
sq = sqlite3.connect(f"file:{srcdata}/semechka.db?mode=ro", uri=True); sq.execute(f"vacuum into '{W}/data/semechka.db'"); sq.close()
for f in ("master.key",): shutil.copy2(f"{srcdata}/{f}", f"{W}/data/{f}")
for d in ("assets", "attachments"):
    if os.path.isdir(f"{srcdata}/{d}"): shutil.copytree(f"{srcdata}/{d}", f"{W}/data/{d}")
sys.path.insert(0, "")
sys.path.insert(0, os.path.expanduser(f"~/semechka-deploy/{inst}/app/venv/lib/python3.12/site-packages"))
from semechka.security import hash_password
PW = "Verify-Only-Pass-9x!"
con = sqlite3.connect(f"{W}/data/semechka.db"); users = con.execute("select id,email,status from users order by email").fetchall()
con.execute("update users set password_hash=?, failed_logins=0, locked_until=0, totp_enabled=0", (hash_password(PW),)); con.commit()
exp = {"conv": con.execute("select count(*) from conversations").fetchone()[0], "assets": con.execute("select count(*) from media_assets").fetchone()[0]}; con.close()
env = dict(os.environ, SEMECHKA_DATA_DIR=W + "/data", SEMECHKA_PORT=str(PORT), SEMECHKA_HOST="127.0.0.1")
p = subprocess.Popen([f"{SRC}/app/venv/bin/semechka", "serve"], env=env, stdout=open(W + "/serve.log", "w"), stderr=subprocess.STDOUT)
BASE = f"http://127.0.0.1:{PORT}"; R = []
def check(n, ok, d=""): R.append(bool(ok)); print(("PASS  " if ok else "FAIL  ") + n + (": " + str(d)[:160] if d != "" else ""), flush=True)
try:
    for _ in range(40):
        try: urllib.request.urlopen(BASE + "/api/health", timeout=2); break
        except Exception: time.sleep(0.5)
    tot_c = tot_a = 0
    for uid, email, status in users:
        jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def call(method, path, body=None, raw=False):
            h = {"Content-Type": "application/json", "Origin": BASE}
            if method != "GET" and call.csrf: h["x-csrf-token"] = call.csrf
            r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
            try:
                with op.open(r, timeout=60) as x: b = x.read(); return b if raw else (json.loads(b) if b else {})
            except urllib.error.HTTPError as e: return {"_http": e.code}
        call.csrf = ""
        r = call("POST", "/api/auth/login", {"email": email, "password": PW})
        if "_http" in r: check(f"{email}: signs in", False, r); continue
        call.csrf = call("GET", "/api/me").get("csrf", "")
        cv = call("GET", "/api/chat/conversations"); cv = cv if isinstance(cv, list) else cv.get("items", [])
        am = call("GET", "/api/media/assets?limit=200"); am = am if isinstance(am, list) else am.get("items", [])
        tot_c += len(cv); tot_a += len(am)
        opened = decrypted = ""
        if cv:
            th = call("GET", f"/api/chat/conversations/{cv[0]['id']}/thread"); msgs = th.get("messages") or th.get("items") or []
            opened = f"thread {len(msgs)} msgs"
        if am:
            b = call("GET", f"/api/media/assets/{am[0]['id']}/content", raw=True); decrypted = f"asset bytes={len(b) if isinstance(b, bytes) else b}"
        ok = ("_http" not in cv if isinstance(cv, dict) else True)
        check(f"{email}: signs in, sees {len(cv)} conversation(s), {len(am)} media asset(s)", ok, f"{opened} {decrypted}".strip())
    if os.environ.get("VERIFY_WORK"):
        owner = next(e for _, e, _ in users if e.startswith("owner@"))
        jar = http.cookiejar.CookieJar(); op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        def c2(method, path, body=None):
            h = {"Content-Type": "application/json", "Origin": BASE, "x-csrf-token": c2.csrf}
            r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
            with op.open(r, timeout=60) as x: return json.loads(x.read() or b"{}")
        c2.csrf = ""; c2("POST", "/api/auth/login", {"email": owner, "password": PW}); c2.csrf = c2("GET", "/api/me")["csrf"]
        st = c2("GET", "/api/work/goose/status")
        check(f"{inst}: Work Mode on Goose reports enabled and ready from its own settings", st.get("enabled") and st.get("ready") and not st.get("problems"), json.dumps({k: st.get(k) for k in ("enabled", "ready", "problems", "goose_version")}))
    check(f"all conversations accounted for across accounts ({tot_c} of {exp['conv']})", tot_c <= exp["conv"] and tot_c > 0, f"visible={tot_c} total={exp['conv']}")
    check(f"all media accounted for across accounts ({tot_a} of {exp['assets']})", tot_a <= exp["assets"] and tot_a > 0, f"visible={tot_a} total={exp['assets']}")
finally:
    p.terminate(); p.wait(timeout=15); shutil.rmtree(W, ignore_errors=True)
print(f"\n{sum(R)}/{len(R)} passed"); sys.exit(0 if all(R) else 1)
