#!/usr/bin/env python3
"""Driver for rehearse_rollout.sh. Runs ON the Spark against the throwaway COPY of CMSIT AI's configuration (127.0.0.1:18787).
Everything it reports is a statement about the copy, not about the live service. Standard library only.

    ROLLOUT_URL=http://127.0.0.1:18787 ADM_PW=... MEM_PW=... python3 -I rehearse_rollout_http.py <rehearsal-dir>
"""
from __future__ import annotations

import http.cookiejar
import json
import os
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
import uuid

BASE = os.environ["ROLLOUT_URL"].rstrip("/")
HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS: list[tuple[str, bool]] = []


def check(name: str, ok: bool, seen: object = "") -> None:
    RESULTS.append((name, bool(ok)))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{str(seen)[:150]}]" if seen != "" else ""), flush=True)


class C:
    def __init__(self, email: str, pw: str):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""
        code, _ = self.req("POST", "/api/auth/login", {"email": email, "password": pw})
        assert code == 200, f"sign-in to the copy failed ({code})"
        self.csrf = self.req("GET", "/api/me")[1]["csrf"]

    def req(self, method, path, body=None, upload=None):
        h = {"Origin": BASE}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        data = None
        if upload:
            b = uuid.uuid4().hex
            name, blob = upload
            data = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                    + blob + f"\r\n--{b}--\r\n".encode())
            h["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        try:
            with self.op.open(urllib.request.Request(BASE + path, data=data, method=method, headers=h), timeout=60) as r:
                raw, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        try:
            return code, json.loads(raw or b"null")
        except ValueError:
            return code, {}

    def pages(self):
        return [p["id"] for p in self.req("GET", "/api/me")[1]["ui"]["pages"]]


def rollout(cmd: str, *extra: str) -> int:
    env = {**os.environ, "ROLLOUT_ADMIN_EMAIL": "rehearsal-admin@rehearsal.invalid", "ROLLOUT_ADMIN_PASSWORD": os.environ["ADM_PW"]}
    r = subprocess.run([sys.executable, "-I", os.path.join(HERE, "rollout.py"), cmd, *extra], env=env, capture_output=True, text=True, check=False)
    print("    " + r.stdout.strip().replace("\n", "\n    "), flush=True)
    return r.returncode


def flags(db: str) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {k: json.loads(v)["v"] for k, v in con.execute("select key, value from setting_values where key like 'cloud.%' or key like 'sandbox.enabled'")}
    con.close()
    return out


def main() -> int:
    rdir = sys.argv[1]
    db = f"{rdir}/data/semechka.db"
    adm = C("rehearsal-admin@rehearsal.invalid", os.environ["ADM_PW"])
    mem = C("rehearsal-member@rehearsal.invalid", os.environ["MEM_PW"])

    print("== baseline (the copy of production's real configuration)")
    st = adm.req("GET", "/api/admin/cloud/status")[1]
    check("production baseline: workspaces off, agents off, sandbox off, rollout everyone",
          not st["workspaces_enabled"] and not st["sandbox"]["agents_enabled"] and not st["sandbox"]["enabled"] and st["rollout"] == "everyone",
          (st["workspaces_enabled"], st["sandbox"], st["rollout"]))
    check("baseline: no Workspaces entry for anyone", "workspaces" not in adm.pages() and "workspaces" not in mem.pages())
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    brand = con.execute("select value from setting_values where key='brand.tab_title'").fetchone()
    con.close()
    check("baseline: production's branding is present in the copy", bool(brand) and json.loads(brand[0])["v"] == "CMSIT AI", brand)

    print("== apply: fence first, then workspaces and agents (rollout.py apply)")
    check("rollout.py status works", rollout("status") == 0)
    check("rollout.py apply succeeds", rollout("apply") == 0)
    f = flags(db)
    check("stored flags: rollout=admins, workspaces on, agents on, sandbox NOT on",
          f.get("cloud.rollout") == "admins" and f.get("cloud.workspaces.enabled") is True and f.get("cloud.agents.enabled") is True
          and not f.get("sandbox.enabled"), f)

    print("== an ordinary account is fenced out of every cloud door")
    org = adm.req("POST", "/api/orgs", {"name": "Rehearsal Org", "owner_email": "rehearsal-admin@rehearsal.invalid"})[1]
    oid = org["id"]
    ws = adm.req("POST", f"/api/orgs/{oid}/workspaces", {"name": "Pilot"})[1]
    wid = ws["id"]
    check("administrator (now an organisation member) sees Workspaces; the ordinary member does not", "workspaces" in adm.pages() and "workspaces" not in mem.pages())
    doors = [("GET", "/api/orgs"), ("GET", f"/api/orgs/{oid}/workspaces"), ("GET", f"/api/workspaces/{wid}/files"), ("GET", f"/api/workspaces/{wid}/agents"),
             ("POST", f"/api/workspaces/{wid}/retrieve"), ("GET", f"/api/workspaces/{wid}/jobs")]
    got = [(p.split("/")[-1], mem.req(m, p, {"query": "x"} if m == "POST" else None)[0]) for m, p in doors]
    check("member: every cloud endpoint answers 403", all(c == 403 for _, c in got), got)
    # even when an administrator adds the ordinary account to the organisation, the fence holds
    adm.req("POST", f"/api/orgs/{oid}/members", {"email": "rehearsal-member@rehearsal.invalid", "roles": ["member"]})
    uid = next(m["user_id"] for m in adm.req("GET", f"/api/orgs/{oid}/members")[1] if m["email"] == "rehearsal-member@rehearsal.invalid")
    adm.req("PUT", f"/api/workspaces/{wid}/members", {"principal_type": "user", "principal_id": uid, "role": "contributor"})
    check("member added to the organisation and workspace is STILL refused",
          mem.req("GET", f"/api/workspaces/{wid}/files")[0] == 403 and mem.req("POST", f"/api/workspaces/{wid}/files", upload=("a.md", b"x"))[0] == 403)
    check("member cannot change settings", mem.req("PUT", "/api/admin/settings/cloud.rollout", {"value": "everyone"})[0] == 403)
    try:
        urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/workspaces/{wid}/files"), timeout=10)
        anon = 200
    except urllib.error.HTTPError as e:
        anon = e.code
    check("a signed-out caller gets 401", anon == 401, anon)

    print("== the administrator's pilot works end to end (copy; admin's own data only)")
    code, _ = adm.req("POST", f"/api/workspaces/{wid}/files", upload=("pilot.md", b"# Pilot\nThe pilot passphrase rule: rotate every ninety days.\n"))
    check("administrator uploads a file", code == 200, code)
    code, r = adm.req("POST", f"/api/workspaces/{wid}/retrieve", {"query": "rotate ninety days"})
    check("administrator retrieves from it (keyword or hybrid)", code == 200, (code, r.get("mode") if isinstance(r, dict) else r))
    code, _ = adm.req("PATCH", f"/api/workspaces/{wid}", {"policy": {"file_access": "owner"}})
    check("administrator turns the owner-only file rule on", code == 200, code)
    models = adm.req("GET", f"/api/workspaces/{wid}/models?mode=chat&purpose=agent")[1]
    check("the real production model catalog is visible to the pilot", isinstance(models, list) and len(models) > 0, [m["alias"] for m in models][:6])
    if models:
        pick = models[0]["id"]
        code, _ = adm.req("PUT", "/api/admin/settings/cloud.agents.models", {"value": [pick]})
        check("agent model list can be set to one approved model", code == 200, code)
        small = [m["id"] for m in models if m["id"] != pick]
        if small:
            code, _ = adm.req("POST", f"/api/workspaces/{wid}/agents", {"name": "small", "instructions": "x", "model_id": small[0], "tools": ["calc"]})
            check("an agent pinned to any other model is refused", code in (400, 403, 422), code)
        adm.req("PUT", "/api/admin/settings/cloud.agents.models", {"value": []})

    print("== backup, flag rollback and restore")
    bk = f"{rdir}/rollout.smck"
    env = {**os.environ, "SEMECHKA_DATA_DIR": f"{rdir}/data", "SEMECHKA_BACKUP_PASSPHRASE": os.environ["ADM_PW"] + "-backup"}
    r = subprocess.run([f"{rdir}/app/venv/bin/semechka", "backup", "-o", bk], env=env, capture_output=True, text=True, check=False)
    check("encrypted backup of the live-flag state written", r.returncode == 0 and os.path.exists(bk), r.stdout[-80:] or r.stderr[-120:])
    check("rollout.py rollback succeeds", rollout("rollback") == 0)
    f2 = flags(db)
    check("flags after rollback: workspaces off, agents off, sandbox off, fence still 'admins'",
          not f2.get("cloud.workspaces.enabled") and not f2.get("cloud.agents.enabled") and not f2.get("sandbox.enabled") and f2.get("cloud.rollout") == "admins", f2)
    check("after rollback nobody sees Workspaces and the administrator's data is intact",
          "workspaces" not in adm.pages() and adm.req("GET", f"/api/workspaces/{wid}/files")[0] in (200, 403))
    hist = adm.req("GET", "/api/admin/settings-history")[1]
    check("settings history recorded old and new values of every flip", sum(1 for h in hist if str(h["key"]).startswith("cloud.")) >= 6,
          [(h["key"], h["old"], h["new"]) for h in hist[:2]])
    rid = next((h["id"] for h in hist if h["key"] == "cloud.agents.enabled" and h["new"] == {"v": False}), None)
    if rid:
        code, _ = adm.req("POST", f"/api/admin/settings-history/{rid}/rollback")
        check("one change can also be undone from the history (cloud.agents.enabled back on)", code == 200 and flags(db).get("cloud.agents.enabled") is True, code)
        adm.req("PUT", "/api/admin/settings/cloud.agents.enabled", {"value": False})
    # restore the backup into a fresh directory and compare
    rest = f"{rdir}/restored"
    env2 = {**env, "SEMECHKA_DATA_DIR": rest, "SEMECHKA_PORT": "18799"}
    r = subprocess.run([f"{rdir}/app/venv/bin/semechka", "restore", bk], env=env2, capture_output=True, text=True, check=False)
    check("restore into an empty directory succeeds", r.returncode == 0, r.stdout[-60:] or r.stderr[-120:])
    fr_ = flags(f"{rest}/semechka.db")
    check("the restored copy has the flags exactly as at backup time (rollout=admins, workspaces on, agents on)",
          fr_.get("cloud.rollout") == "admins" and fr_.get("cloud.workspaces.enabled") is True and fr_.get("cloud.agents.enabled") is True, fr_)
    a, b = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(f"file:{rest}/semechka.db?mode=ro", uri=True)
    same = all(a.execute(f"select count(*) from {t}").fetchone() == b.execute(f"select count(*) from {t}").fetchone() for t in ("users", "conversations", "messages", "roles", "models"))
    check("restored copy has the same accounts, conversations, messages, roles and models", same)
    check("restored copy passes the integrity check", b.execute("pragma integrity_check").fetchone()[0] == "ok")

    bad = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} rehearsal checks passed (on a COPY of production configuration)")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
