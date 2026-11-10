import http.cookiejar, json, os, re, sys, time, urllib.request, urllib.error
BASE = os.environ.get("SEM_BASE", "http://spark-host:8790").rstrip("/")
CREDS = dict(l.split("\t", 1) for l in open(os.path.expanduser("~/semechka-deploy/secrets/staging-credentials.txt")).read().splitlines() if "\t" in l and not l.startswith("#"))
RES = []
def check(n, ok, d=""):
    RES.append(bool(ok)); print(("PASS  " if ok else "FAIL  ") + n + (": " + str(d)[:230] if d != "" else ""), flush=True)
class C:
    def __init__(self, email):
        self.jar = http.cookiejar.CookieJar(); self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar)); self.csrf = ""; self.email = email
        r = self.call("POST", "/api/auth/login", {"email": email, "password": CREDS[email].strip()})
        self.ok = "_http" not in r
        if self.ok: self.csrf = self.call("GET", "/api/me")["csrf"]
    def call(self, method, path, body=None, raw=False, timeout=900):
        h = {"Content-Type": "application/json", "Origin": BASE}
        if self.csrf and method != "GET": h["x-csrf-token"] = self.csrf
        r = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None, headers=h)
        try:
            with self.op.open(r, timeout=timeout) as x:
                b = x.read(); return b if raw else (json.loads(b) if b else {})
        except urllib.error.HTTPError as e:
            b = e.read()
            try: d = json.loads(b)
            except Exception: d = {"raw": b[:200].decode("utf8", "replace")}
            return {"_http": e.code, **(d if isinstance(d, dict) else {"body": d})}
    def stream(self, path, body, timeout=900):
        """POST and parse an SSE stream into a list of (event, data)."""
        h = {"Content-Type": "application/json", "Origin": BASE, "x-csrf-token": self.csrf, "Accept": "text/event-stream"}
        r = urllib.request.Request(BASE + path, method="POST", data=json.dumps(body).encode(), headers=h)
        ev = []; cur = "message"
        try:
            with self.op.open(r, timeout=timeout) as x:
                for raw in x:
                    l = raw.decode("utf8", "replace").rstrip("\n")
                    if l.startswith("event:"): cur = l[6:].strip()
                    elif l.startswith("data:"):
                        try: ev.append((cur, json.loads(l[5:])))
                        except Exception: ev.append((cur, l[5:]))
        except urllib.error.HTTPError as e:
            return [("http_error", {"status": e.code, "body": e.read()[:300].decode("utf8", "replace")})]
        return ev
def shape(o, d=0):
    if isinstance(o, dict): return {k: (shape(v, d + 1) if d < 2 else type(v).__name__) for k, v in list(o.items())[:14]}
    if isinstance(o, list): return [shape(o[0], d + 1)] if o else []
    return type(o).__name__
