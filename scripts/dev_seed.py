#!/usr/bin/env python3
"""Seed a throwaway Semechka dev instance with realistic (clearly fake) people so UI work has something to show.

  SEMECHKA_DATA_DIR=/tmp/sem-a .venv/bin/semechka serve --port 8781 &
  .venv/bin/python scripts/dev_seed.py --api http://127.0.0.1:8781 --data /tmp/sem-a \
      --backend openai_compat=http://127.0.0.1:18080/v1

Everything created here is demo fixture data for development only. Never run this against a real deployment.
"""
import argparse
import json
import sys
import urllib.error
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

PW = "demo passphrase 2026"


class Client:
    def __init__(self, base):
        self.base, self.csrf = base, ""
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def call(self, method, path, body=None, headers=None):
        h = {"content-type": "application/json", **(headers or {})}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        req = urllib.request.Request(self.base + path, method=method, headers=h, data=json.dumps(body).encode() if body is not None else None)
        try:
            with self.op.open(req) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            sys.exit(f"{method} {path} -> {e.code} {e.read().decode()[:300]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--backend", default="", help="adapter=url, e.g. openai_compat=http://127.0.0.1:18080/v1")
    a = ap.parse_args()
    owner = Client(a.api)
    token = (Path(a.data) / "setup-token").read_text().strip()
    body = {"owner": {"email": "owner@demo.test", "password": PW, "display_name": "Olga Owner"}, "preset": "business",
            "settings": {"brand.org_name": "Banana Corporation", "brand.app_name": "Banana Corporation Private AI", "brand.short_name": "Banana AI",
                         "brand.tab_title": "Banana AI", "signup.mode": "invite", "auth.mfa_policy": "optional", "nav.suggested_prompts": ["Summarise this paragraph in two sentences", "Draft a polite reply declining a meeting", "Explain what a hash chain is"]}}
    if a.backend:
        ad, url = a.backend.split("=", 1)
        probe = owner.call("POST", "/api/setup/backend-test", {"adapter": ad, "base_url": url}, {"x-setup-token": token})
        ids = [m["real_id"] for m in probe["models"]]
        body.update(backend={"adapter": ad, "base_url": url}, enable_models=ids, smoke_model=ids[0] if ids else None)
    r = owner.call("POST", "/api/setup/complete", body, {"x-setup-token": token})
    owner.csrf = owner.call("GET", "/api/me")["csrf"]
    teams = {n: owner.call("POST", "/api/admin/teams", {"name": n})["id"] for n in ("Finance", "Marketing", "Support", "IT")}
    people = [("maya@demo.test", "Maya Okafor", "member", ["Finance"]), ("li@demo.test", "Li Wei", "member", ["Marketing"]),
              ("sam@demo.test", "Sam Dubois", "member", ["Support"]), ("ines@demo.test", "Ines Alvarez", "model_operator", ["IT"]),
              ("tomas@demo.test", "Tomas Novak", "org_admin", []), ("priya@demo.test", "Priya Raman", "team_manager", ["Support"])]
    ids = {}
    for em, name, role, tms in people:
        ids[em] = owner.call("POST", "/api/admin/users", {"email": em, "display_name": name, "password": PW, "role_id": role, "team_ids": [teams[t] for t in tms]})["id"]
    # Li Wei is the 'curious employee' (Brief 3/J): a few technical fields, nothing operational.
    owner.call("PUT", f"/api/admin/users/{ids['li@demo.test']}/override", {"grants": ["tech.model.id", "tech.model.quant", "tech.model.context", "tech.perf.ttft", "tech.perf.tps", "tech.queue.position", "tech.resources.gpu"], "denies": []})
    owner.call("POST", "/api/admin/invites", {"role_id": "member", "email": "newhire@demo.test"})
    print(json.dumps({"password_for_everyone": PW, "owner": "owner@demo.test", "people": [p[0] for p in people], "setup": r}, indent=1))


main()
