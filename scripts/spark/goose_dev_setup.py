#!/usr/bin/env python3
"""Provision a FRESH dev Semechka (port 8794 by default) for the Work-on-Goose end-to-end run: owner, two people (alice, bob),
the router as the only backend, Work on Goose switched on. Random passwords go to a 0600 file; keys are read from files, never printed.

    python3 scripts/spark/goose_dev_setup.py --data ~/work-dev/data --creds ~/work-dev/creds.json
"""
import argparse
import json
import os
import secrets
import sys
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8794")
ap.add_argument("--data", required=True)
ap.add_argument("--creds", required=True)
ap.add_argument("--router", default="http://127.0.0.1:8080")
ap.add_argument("--key-file", default=os.path.expanduser("~/spark-llm/api.key"))
ap.add_argument("--verified", default=os.path.expanduser("~/spark-llm/models.verified.json"))
ap.add_argument("--model", default="qwen3.6-35b-a3b")
a = ap.parse_args()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
csrf = [""]


def call(method, path, body=None, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    if csrf[0] and method != "GET":
        h["X-CSRF-Token"] = csrf[0]
    r = urllib.request.Request(a.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
    try:
        with op.open(r, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


creds = {"owner": secrets.token_urlsafe(18), "alice": secrets.token_urlsafe(18), "bob": secrets.token_urlsafe(18)}
tok = (Path(a.data) / "setup-token").read_text().strip() if (Path(a.data) / "setup-token").exists() else ""
if not tok:
    cand = [p for p in Path(a.data).iterdir() if "setup" in p.name and "token" in p.name]
    tok = cand[0].read_text().strip()
key = Path(a.key_file).read_text().strip()
coding = [m for m, v in json.loads(Path(a.verified).read_text())["models"].items() if v.get("coding_agent_compatible")]
st, b = call("POST", "/api/setup/complete", {
    "owner": {"email": "owner@dev.test", "password": creds["owner"], "display_name": "Dev Owner"}, "preset": "business",
    "settings": {"brand.org_name": "Dev", "brand.app_name": "Semechka dev", "brand.tab_title": "Semechka dev", "signup.mode": "admin",
                 "privacy.chat_retention_days": 30, "auth.mfa_policy": "optional"},
    "backend": {"adapter": "openai_compat", "base_url": a.router + "/v1", "api_key": key, "external": False},
    "enable_models": coding}, {"x-setup-token": tok})
assert st == 200, f"setup failed {st} {str(b)[:200]}"
st, b = call("POST", "/api/auth/login", {"email": "owner@dev.test", "password": creds["owner"]})
assert st == 200
csrf[0] = call("GET", "/api/me")[1]["csrf"]
for who in ("alice", "bob"):
    st, b = call("POST", "/api/admin/users", {"email": f"{who}@dev.test", "display_name": who.title(), "password": creds[who], "role_id": "platform_admin", "team_ids": []})
    assert st == 200, (who, st, b)
settings = {"goose.enabled": True, "modes.work.enabled": True, "goose.router_url": a.router, "goose.router_key_file": a.key_file,
            "goose.verified_file": a.verified, "goose.default_model": a.model, "goose.docker_host": f"unix:///run/user/{os.getuid()}/semechka-sandbox.sock",
            "goose.approval_timeout_s": 600, "goose.max_turns": 40}
for k, v in settings.items():
    st, b = call("PUT", f"/api/admin/settings/{k}", {"value": v, "confirm_weaker": True})
    assert st == 200, (k, st, b)
fd = os.open(a.creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(creds, f)
print("dev instance provisioned; credentials written to", a.creds, "(not shown); coding models enabled:", coding)
