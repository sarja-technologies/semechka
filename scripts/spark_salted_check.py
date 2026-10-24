"""0.4.0 checks of Salted after an update, against the real engine. Runs ON the Spark (stdlib only); copy it to
~/semechka-deploy/bin/. Reads the test passwords from their 0600 file and never prints them. The one conversation it
creates is deleted again. Usage: python3 spark_salted_check.py 8788"""
import http.cookiejar
import json
import sys
import time
import urllib.error
import urllib.request

SP = sys.argv[1]
CREDS = dict(line.split("\t", 1) for line in open("/home/fylume/semechka-deploy/secrets/salted-credentials.txt").read().splitlines()
             if line and not line.startswith("#"))
fails = []


def check(ok, what):
    print(("[PASS] " if ok else "[FAIL] ") + what, flush=True)
    if not ok:
        fails.append(what)


class S:
    def __init__(self, base, email=None, headers=None):
        self.base, self.h = base, headers or {}
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""
        if email:
            self.call("POST", "/api/auth/login", {"email": email, "password": CREDS[email].strip()})
            self.me = self.call("GET", "/api/me")
            self.csrf = self.me["csrf"]

    def req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "content-type": "application/json", "origin": self.base, **self.h, **({"x-csrf-token": self.csrf} if self.csrf else {})})
        return self.op.open(r, timeout=600)

    def call(self, method, path, body=None):
        with self.req(method, path, body) as r:
            return json.loads(r.read() or b"null")

    def status(self, method, path, body=None):
        try:
            with self.req(method, path, body) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def stream(self, path, body):
        events, buf = [], b""
        with self.req("POST", path, body) as r:
            for chunk in iter(lambda: r.read(256), b""):
                buf += chunk
                while b"\n\n" in buf:
                    block, buf = buf.split(b"\n\n", 1)
                    for line in block.split(b"\n"):
                        if line.startswith(b"data: "):
                            events.append(json.loads(line[6:]))
        return events


SB = f"http://192.168.20.193:{SP}"
health = json.loads(urllib.request.urlopen(SB + "/api/health").read())
owner = S(SB, "owner@salted.test")
check(owner.me["user"]["email"] == "owner@salted.test", "Salted: owner signs in after the upgrade")
member = S(SB, "member@salted.test")
convs = member.call("GET", "/api/chat/conversations")
check(len(convs) > 0, f"member's conversations kept ({len(convs)})")
th = member.call("GET", f"/api/chat/conversations/{convs[-1]['id']}/thread")
check(bool(th["messages"] and th["messages"][0]["content"]), "oldest conversation opens and decrypts")

# picker: what the member is offered, and what they must not see
cat = member.call("GET", "/api/models/catalog?operation=text")
blob = json.dumps(cat)
check(len(cat["models"]) > 0 and "sections" in cat, f"member picker: {len(cat['models'])} models in {len(cat['sections'])} section(s): "
      + ", ".join(m["label"] for m in cat["models"]))
check("local:" not in blob and not any("real_id" in m or "state" in m for m in cat["models"]), "member picker carries no identifiers or engine state")
ocat = owner.call("GET", "/api/models/catalog?operation=text")
check(all("state" in m for m in ocat["models"]) and ocat["status"] is True, f"owner picker shows live state ({len(ocat['models'])} usable, {len(ocat['unusable'])} listed as not available)")
eng = owner.call("GET", "/api/engines")["engines"]
sar = next((e for e in eng if e["adapter"] == "sarness"), None)
check(bool(sar and sar["reachable"] and sar["status"]["memory"].get("total_mb")), "engine panel reads the real engine "
      + (f"(engine {sar['status']['engine'].get('version')}, chat model {sar['status']['llm']['state']}, {sar['status']['memory']['used_mb']} MB used)" if sar else ""))
check(member.status("GET", "/api/engines") == 403, "member cannot read engine state (403)")
some = ocat["models"][0]["id"]
check(member.status("POST", f"/api/admin/models/{some}/load") == 403, "member cannot load a model through the API (403)")
roles = {r["id"]: r for r in owner.call("GET", "/api/admin/roles")}
mo = roles.get("model_operator", {}).get("grants", [])
check(all(p in mo for p in ("models.view_all", "models.use_all", "models.status.view", "ops.queue.view")),
      "model_operator role received the new model permissions")
check(not any(p.startswith("models.") for p in roles.get("member", {}).get("grants", [])), "member role received none")

# a real reply on the engine through the upgraded copy
quick = next((m for m in cat["models"] if m["alias"] == "Quick"), cat["models"][0])
conv = member.call("POST", "/api/chat/conversations", {"model_id": quick["id"]})
t0 = time.time()
evs = member.stream(f"/api/chat/conversations/{conv['id']}/generate", {"mode": "send", "operation": "text",
                    "content": "Reply with one short sentence about the sea.", "model_id": quick["id"], "parent_id": None})
done = next((e for e in evs if e["type"] == "done"), None)
check(bool(done and done["status"] == "complete" and done["content"].strip()),
      f"member chat with {quick['label']} streamed a real reply in {time.time() - t0:.1f}s: " + (done["content"].strip()[:100] if done else str(evs[-1:])))

check(member.status("DELETE", f"/api/chat/conversations/{conv['id']}") in (200, 204), "the test conversation is removed again")
check(len(member.call("GET", "/api/chat/conversations")) == len(convs), "member's conversation count is back to what it was")
print("FAILED:" if fails else "ALL PASSED", fails if fails else "", flush=True)
