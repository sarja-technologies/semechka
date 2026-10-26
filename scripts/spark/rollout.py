#!/usr/bin/env python3
"""Controlled rollout of the cloud features on one Semechka instance, through its own admin API (so every change is
authorised, validated and audited exactly like a click in Administration, Settings).

    ROLLOUT_URL=http://192.168.20.193:8787 ROLLOUT_ADMIN_EMAIL=you@example.com python3 scripts/spark/rollout.py status
    ... rollout.py apply      # rollout=admins FIRST, read it back, then workspaces, then agents. Never touches the sandbox.
    ... rollout.py rollback   # agents off, workspaces off. Leaves rollout=admins (the safe state). --open also sets it to everyone.

The administrator's password is read from the ROLLOUT_ADMIN_PASSWORD variable or prompted for (never echoed, printed, stored or
passed on a command line). If the account uses two-step sign-in the 6-digit code is prompted for too. Standard library only."""
from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("ROLLOUT_URL", "").rstrip("/")


class Admin:
    def __init__(self, email: str, password: str):
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""
        code, body = self.call("POST", "/api/auth/login", {"email": email, "password": password})
        if code == 401 and isinstance(body, dict) and body.get("detail", {}).get("code") == "mfa_required":
            code, body = self.call("POST", "/api/auth/login", {"email": email, "password": password, "totp": getpass.getpass("Authentication code: ").strip()})
        if code != 200:
            sys.exit(f"sign-in failed ({code}); nothing was changed")
        self.csrf = self.call("GET", "/api/me")[1]["csrf"]

    def call(self, method: str, path: str, body=None):
        h = {"Origin": BASE}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        try:
            with self.op.open(urllib.request.Request(BASE + path, data=data, method=method, headers=h), timeout=60) as r:
                raw, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        try:
            return code, json.loads(raw or b"null")
        except ValueError:
            return code, raw[:200]

    def put(self, key: str, value) -> None:
        code, body = self.call("PUT", f"/api/admin/settings/{key}", {"value": value})
        if code != 200:
            sys.exit(f"could not set {key} ({code}): {str(body)[:200]}. Stopped; later steps were not run.")
        print(f"  set {key} = {value!r}")


def state(a: Admin) -> dict:
    code, st = a.call("GET", "/api/admin/cloud/status")
    assert code == 200, (code, st)
    return {"rollout": st.get("rollout"), "workspaces": st.get("workspaces_enabled"),
            "agents": st["sandbox"]["agents_enabled"], "sandbox": st["sandbox"]["enabled"]}


def show(a: Admin, label: str) -> dict:
    s = state(a)
    print(f"{label}: rollout={s['rollout']}  workspaces={'on' if s['workspaces'] else 'off'}  agents={'on' if s['agents'] else 'off'}"
          f"  sandbox={'ON' if s['sandbox'] else 'off'}")
    return s


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in ("status", "apply", "rollback") or not BASE:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    email = os.environ.get("ROLLOUT_ADMIN_EMAIL") or input("Administrator email: ").strip()
    a = Admin(email, os.environ.get("ROLLOUT_ADMIN_PASSWORD") or getpass.getpass("Administrator password: "))
    before = show(a, "before")
    if cmd == "apply":
        if before["sandbox"]:
            print("  note: the sandbox switch is on; this script does not change it")
        a.put("cloud.rollout", "admins")  # the fence goes up before anything is switched on
        if show(a, "fence ")["rollout"] != "admins":
            sys.exit("the rollout setting did not read back as 'admins'; nothing was switched on")
        a.put("cloud.workspaces.enabled", True)
        a.put("cloud.agents.enabled", True)
    elif cmd == "rollback":
        a.put("cloud.agents.enabled", False)
        a.put("cloud.workspaces.enabled", False)
        if "--open" in sys.argv:
            a.put("cloud.rollout", "everyone")
    after = show(a, "after ")
    if cmd == "apply" and not (after["rollout"] == "admins" and after["workspaces"] and after["agents"]):
        sys.exit("final state is not what apply intends")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
