#!/usr/bin/env python3
"""Provision the "Salted" test instance through Semechka's own HTTP API (no database writes).

Runs ON the host that runs Salted (it reads the local engine token and the one-time setup token from files).
Safe to re-run: it completes the wizard once, then reconciles accounts, teams, roles, models and settings.
Passwords are generated here, written to a 0600 file and never printed or logged.

    python3 provision_salted.py --base http://192.168.20.193:8788 --data-dir ~/semechka-deploy/salted/data \
        --engine-url http://127.0.0.1:8752 --engine-token-file ~/semechka-deploy/secrets/sarness.token \
        --credentials ~/semechka-deploy/secrets/salted-credentials.txt
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import secrets
import string
import sys
import urllib.error
import urllib.request
from pathlib import Path

DOMAIN = "salted.test"  # reserved TLD: these addresses can never receive or send real mail

# (key, [substrings of the engine's model id in order of preference]) -> alias, tags, who may use it
MODEL_PLAN = [  # engine id fragment, alias, tags, audience, generation defaults
    ("qwen3.5-0.8b", "Quick", ["fast", "small"], "everyone", {"thinking": False}),
    ("qwen3-4b-instruct", "Balanced", ["general"], "everyone", {}),
    ("gpt-oss-120b", "Large", ["large", "reasoning"], "staff", {}),
    ("qwen3-coder-next", "Coder", ["code", "large"], "staff", {}),
    # media models (enabled only if the engine has them): small and fast ones, so a test run does not occupy the GPU for long
    ("sdxl-turbo", "Sketch", ["image", "fast"], "everyone", {}),
    ("z-image-turbo", "Studio", ["image"], "everyone", {}),
    ("wan2.2-ti2v-5b", "Clip", ["video", "small"], "staff", {}),
]
STAFF_ROLES = ["owner", "platform_admin", "org_admin", "model_operator", "team_manager", "analyst"]

ACCOUNTS = [  # local part, display name, role, teams, override (grants, denies)
    ("admin", "Salted Platform Admin", "platform_admin", [], ([], [])),
    ("operator", "Salted Model Operator", "model_operator", [], ([], [])),
    ("manager", "Salted Team Manager", "team_manager", ["Research"], ([], [])),
    ("analyst", "Salted Analyst", "analyst", ["Research", "Creative"], ([], [])),
    ("member", "Salted Member", "member", ["Research", "Creative"], ([], [])),
    ("support", "Salted Support Agent", "member", ["Support"], ([], [])),
    ("technical", "Salted Technical Reader", "member", ["Creative"], (["tech.*"], [])),
    ("restricted", "Salted Restricted User", "member", [], ([], ["tech.*", "chat.attach", "chat.export"])),
    ("guest", "Salted Guest", "guest", [], ([], [])),
]


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def call(self, method: str, path: str, body=None, headers=None, ok=(200,)):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json", "Origin": self.base, **(headers or {})}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        try:
            with self.op.open(req, timeout=60) as r:
                raw = r.read()
                code = r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        try:
            js = json.loads(raw) if raw else {}
        except ValueError:
            js = {"raw": raw[:200].decode("utf8", "replace")}
        if code not in ok:
            detail = js.get("detail", js) if isinstance(js, dict) else js
            raise RuntimeError(f"{method} {path} -> {code}: {json.dumps(detail)[:300]}")
        return js

    def login(self, email: str, password: str):
        self.jar.clear()
        self.csrf = ""
        self.call("POST", "/api/auth/login", {"email": email, "password": password})
        self.csrf = self.call("GET", "/api/me")["csrf"]


def new_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(24))


def load_credentials(path: Path) -> dict[str, str]:
    creds: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if "\t" in line and not line.startswith("#"):
                k, v = line.split("\t", 1)
                creds[k] = v
    return creds


def save_credentials(path: Path, creds: dict[str, str], base: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# Salted test accounts for {base}. Generated; owner-only file (0600). Not stored in Git.",
             "# email<TAB>password"] + [f"{k}\t{v}" for k, v in creds.items()]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--engine-url", required=True)
    ap.add_argument("--engine-token-file", required=True)
    ap.add_argument("--credentials", required=True)
    ap.add_argument("--modes", default="chat,code,image,video,knowledge,agents")
    a = ap.parse_args()
    base, data, cred_path = a.base.rstrip("/"), Path(a.data_dir).expanduser(), Path(a.credentials).expanduser()
    engine_token = Path(a.engine_token_file).expanduser().read_text().strip()
    creds = load_credentials(cred_path)
    owner_email = f"owner@{DOMAIN}"
    api = Api(base)

    # ---- 1. wizard (first run only) -----------------------------------------------------------------
    pub = api.call("GET", "/api/public")
    if pub["needs_setup"]:
        creds[owner_email] = new_password()
        save_credentials(cred_path, creds, base)  # persist before use so a crash cannot lose the password
        tok = (data / "setup-token").read_text().strip()
        probe = api.call("POST", "/api/setup/backend-test", {"adapter": "sarness", "base_url": a.engine_url, "api_key": engine_token},
                         headers={"x-setup-token": tok})
        assert probe["health"]["ok"], probe["health"]
        # Enable only the two small general models here; the rest are configured below with aliases and grants.
        chat_ids = [m["real_id"] for m in probe["models"] if "chat" in m["capabilities"]]
        pick = [i for i in chat_ids if "qwen3.5-0.8b" in i or "qwen3-4b-instruct" in i]
        r = api.call("POST", "/api/setup/complete", {
            "owner": {"email": owner_email, "password": creds[owner_email], "display_name": "Salted Owner"},
            "preset": "business",
            "settings": {"brand.org_name": "Salted Test Lab", "brand.app_name": "Semechka Salted", "brand.short_name": "Salted",
                         "brand.tab_title": "Salted · Semechka test environment", "brand.theme_default": "dark",
                         "signup.mode": "invite"},
            "backend": {"adapter": "sarness", "base_url": a.engine_url, "api_key": engine_token},
            "backend_name": "Spark Sarness Engine", "enable_models": pick, "smoke_model": pick[0] if pick else None},
            headers={"x-setup-token": tok})
        print("wizard completed:", {k: r[k] for k in ("models_enabled",) if k in r}, "smoke:", (r.get("smoke") or {}).get("ok"))
    else:
        print("wizard already completed; reconciling")
    if owner_email not in creds:
        sys.exit("owner password unknown (credentials file missing); restore it or re-provision a fresh instance")
    api.login(owner_email, creds[owner_email])

    def put_setting(key, value, scope_type="deployment", scope_id="", quiet=False):
        try:
            api.call("PUT", f"/api/admin/settings/{key}", {"value": value, "scope_type": scope_type, "scope_id": scope_id, "confirm_weaker": True})
        except RuntimeError as e:
            if not quiet:
                print(f"  setting {key} not applied: {e}")
            return False
        return True

    # ---- 2. roles, teams -----------------------------------------------------------------------------
    roles = {r["id"]: r for r in api.call("GET", "/api/admin/roles")}
    if "analyst" not in roles:
        base_member = roles["member"]
        api.call("POST", "/api/admin/roles", {"label": "Analyst", "description": "Member plus read-only performance and usage detail.",
                                              "rank": 40, "grants": sorted(set(base_member["grants"]) | {"tech.perf.ttft", "tech.perf.tps",
                                                                                                       "tech.perf.duration", "tech.usage.tokens",
                                                                                                       "tech.model.family", "tech.model.context"}),
                                              "denies": []})
        roles = {r["id"]: r for r in api.call("GET", "/api/admin/roles")}
        # The role id is derived from the label; make sure the plan's id exists
        assert "analyst" in roles, list(roles)
    teams = {t["name"]: t for t in api.call("GET", "/api/admin/teams")}
    for name, grants, denies in (("Research", ["mode.knowledge", "mode.agents", "mode.code"], []),
                                 ("Creative", ["mode.image", "mode.video"], []),
                                 ("Support", [], ["chat.export"])):
        if name not in teams:
            api.call("POST", "/api/admin/teams", {"name": name, "grants": grants, "denies": denies})
        else:
            api.call("PUT", f"/api/admin/teams/{teams[name]['id']}", {"name": name, "grants": grants, "denies": denies})
    teams = {t["name"]: t for t in api.call("GET", "/api/admin/teams")}

    # ---- 3. accounts and per-user overrides ----------------------------------------------------------
    users = {u["email"]: u for u in api.call("GET", "/api/admin/users?limit=200").get("items", [])}
    for local, display, role, team_names, (og, od) in ACCOUNTS:
        email = f"{local}@{DOMAIN}"
        if email not in creds:
            creds[email] = new_password()
            save_credentials(cred_path, creds, base)
        if email not in users:
            api.call("POST", "/api/admin/users", {"email": email, "display_name": display, "password": creds[email],
                                                   "role_id": role, "team_ids": [teams[t]["id"] for t in team_names]})
            users = {u["email"]: u for u in api.call("GET", "/api/admin/users?limit=200").get("items", [])}
        else:
            api.call("PATCH", f"/api/admin/users/{users[email]['id']}",
                     {"role_ids": [role], "team_ids": [teams[t]["id"] for t in team_names]})
        if og or od:
            api.call("PUT", f"/api/admin/users/{users[email]['id']}/override", {"grants": og, "denies": od})
    save_credentials(cred_path, creds, base)

    # ---- 4. models: discover, alias, enable, grants ---------------------------------------------------
    backends = api.call("GET", "/api/admin/backends")
    bid = next(b["id"] for b in backends if b["adapter"] == "sarness")
    api.call("POST", f"/api/admin/backends/{bid}/sync", {})
    models = api.call("GET", "/api/admin/models?limit=500")
    items = models.get("items", models) if isinstance(models, dict) else models
    by_key = {}
    for m in items:
        for key, *_ in MODEL_PLAN:
            if key in m["real_id"]:
                by_key[key] = m
    for key, alias, tags, who, limits in MODEL_PLAN:
        m = by_key.get(key)
        if not m:
            print(f"  model {key}: not installed on the engine, skipped")
            continue
        api.call("PATCH", f"/api/admin/models/{m['id']}", {"alias": alias, "tags": tags, "enabled": True, **({"gen_limits": limits} if limits else {})})
        subjects = [("role", "owner"), ("role", "member"), ("role", "guest")] if who == "everyone" else [("role", r) for r in STAFF_ROLES]
        if who == "everyone":
            subjects += [("role", r) for r in STAFF_ROLES if r not in ("owner", "member")]
        api.call("PUT", f"/api/admin/models/{m['id']}/grants",
                 {"grants": [{"subject_type": t, "subject_id": i, "effect": "allow"} for t, i in subjects if t != "role" or i in roles]
                  + ([{"subject_type": "team", "subject_id": teams["Support"]["id"], "effect": "deny"}] if key == "gpt-oss-120b" else [])})

    # ---- 5. settings ----------------------------------------------------------------------------------
    for mode in a.modes.split(","):
        put_setting(f"modes.{mode}.enabled", True, quiet=True)
    put_setting("auth.idle_minutes", 480)
    put_setting("quota.daily_messages", 500)
    put_setting("quota.daily_messages", 20, "role", "guest")
    put_setting("quota.max_tokens", 2048, "role", "guest")
    put_setting("quota.concurrent_requests", 1, "role", "guest")
    put_setting("privacy.chat_retention_days", 7, "role", "guest")
    put_setting("privacy.usage_retention_days", 180)
    put_setting("privacy.notice", "Salted is a test environment. Do not enter real personal data.")
    put_setting("brand.signin_message", "Salted test environment. Accounts are created by an administrator.")
    put_setting("brand.footer_text", "Salted · Semechka test environment")
    put_setting("nav.suggested_prompts", ["Explain what you can see about this deployment", "Summarise this text in three bullet points",
                                          "Write a short Python function that parses an ISO date", "Give me a checklist for testing a chat product"])

    # ---- 6. report (no secrets) -----------------------------------------------------------------------
    me = api.call("GET", "/api/me")
    print("modes as seen by the owner:")
    for k, v in (me["ui"].get("modes") or {}).items():
        print(f"  {k:10s} {'enabled' if v.get('enabled') else 'unavailable: ' + str(v.get('reason'))}")
    print("accounts:", ", ".join(sorted(creds)), f"\ncredentials file: {cred_path} (0600)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
