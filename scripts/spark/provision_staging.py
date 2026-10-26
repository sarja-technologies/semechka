#!/usr/bin/env python3
"""Provision the throw-away "staging" instance through Semechka's own HTTP API (no database writes).

Runs ON the host that runs staging. Staging holds synthetic accounts only and exists so the workspace, agent and sandbox
features can be exercised end to end through the real UI on the real hardware. Passwords are generated here, written to a
0600 file and never printed. Safe to re-run.

    python3 provision_staging.py --base http://192.168.20.193:8790 --data-dir ~/semechka-deploy/staging/data \
        --engine-url http://127.0.0.1:8752 --engine-token-file ~/semechka-deploy/secrets/sarness.token \
        --credentials ~/semechka-deploy/secrets/staging-credentials.txt
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provision_salted import Api, load_credentials, new_password, save_credentials  # noqa: E402  (shared helpers)

DOMAIN = "staging.test"
ACCOUNTS = [  # local part, display name, role: platform people; company people are created inside organisations
    ("admin", "Staging Platform Admin", "platform_admin"),
    ("acme-owner", "Acme Owner", "member"),
    ("acme-finance", "Acme Finance Lead", "member"),
    ("acme-eng", "Acme Engineer", "member"),
    ("globex-owner", "Globex Owner", "member"),
    ("globex-analyst", "Globex Analyst", "member"),
]
MODELS = [  # engine id fragment, alias, tags
    ("qwen3.5-0.8b", "Quick", ["fast"]),
    ("qwen3-4b-instruct", "Balanced", ["general"]),
    ("gpt-oss-120b", "Large", ["large", "reasoning"]),
    ("sdxl-turbo", "Sketch", ["image", "fast"]),
    ("wan2.2-ti2v-5b", "Clip", ["video", "small"]),
]
SWITCHES = {
    "cloud.workspaces.enabled": True,
    "cloud.agents.enabled": True,
    "sandbox.enabled": True,
    "embeddings.url": "http://127.0.0.1:8760",
    "embeddings.model": "Qwen3-Embedding-0.6B",
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--engine-url", required=True)
    ap.add_argument("--engine-token-file", required=True)
    ap.add_argument("--credentials", required=True)
    a = ap.parse_args()
    base, data, cred_path = a.base.rstrip("/"), Path(a.data_dir).expanduser(), Path(a.credentials).expanduser()
    engine_token = Path(a.engine_token_file).expanduser().read_text().strip()
    creds = load_credentials(cred_path)
    owner = f"owner@{DOMAIN}"
    api = Api(base)

    pub = api.call("GET", "/api/public")
    if pub["needs_setup"]:
        creds[owner] = new_password()
        save_credentials(cred_path, creds, base)  # persist before use so a crash cannot lose the password
        tok = (data / "setup-token").read_text().strip()
        probe = api.call("POST", "/api/setup/backend-test", {"adapter": "sarness", "base_url": a.engine_url, "api_key": engine_token},
                         headers={"x-setup-token": tok})
        assert probe["health"]["ok"], probe["health"]
        chat = [m["real_id"] for m in probe["models"] if "chat" in m["capabilities"]]
        pick = [i for i in chat if "qwen3.5-0.8b" in i or "qwen3-4b-instruct" in i]
        # MFA is off here: synthetic accounts on a throw-away instance
        r = api.call("POST", "/api/setup/complete", {
            "owner": {"email": owner, "password": creds[owner], "display_name": "Staging Owner"},
            "preset": "business",
            "settings": {"brand.org_name": "Staging", "brand.app_name": "Semechka Staging", "brand.short_name": "Staging",
                         "brand.tab_title": "Staging · Semechka", "signup.mode": "invite",
                         "auth.mfa_policy": "off"},
            "backend": {"adapter": "sarness", "base_url": a.engine_url, "api_key": engine_token},
            "backend_name": "Spark Sarness Engine", "enable_models": pick, "smoke_model": pick[0] if pick else None},
            headers={"x-setup-token": tok})
        print("wizard completed; smoke:", (r.get("smoke") or {}).get("ok"))
    else:
        print("wizard already completed; reconciling")
    if owner not in creds:
        sys.exit("owner password unknown (credentials file missing); re-provision a fresh instance")
    api.login(owner, creds[owner])

    users = {u["email"]: u for u in api.call("GET", "/api/admin/users?limit=200").get("items", [])}
    for local, display, role in ACCOUNTS:
        email = f"{local}@{DOMAIN}"
        if email not in creds:
            creds[email] = new_password()
            save_credentials(cred_path, creds, base)
        if email not in users:
            api.call("POST", "/api/admin/users", {"email": email, "display_name": display, "password": creds[email],
                                                   "role_id": role, "team_ids": []})
    save_credentials(cred_path, creds, base)

    backends = api.call("GET", "/api/admin/backends")
    bid = next(b["id"] for b in backends if b["adapter"] == "sarness")
    api.call("POST", f"/api/admin/backends/{bid}/sync", {})
    models = api.call("GET", "/api/admin/models?limit=500")
    items = models.get("items", models) if isinstance(models, dict) else models
    roles = ["owner", "platform_admin", "member"]
    for key, alias, tags in MODELS:
        m = next((m for m in items if key in m["real_id"]), None)
        if not m:
            print(f"  model {key}: not on the engine, skipped")
            continue
        api.call("PATCH", f"/api/admin/models/{m['id']}", {"alias": alias, "tags": tags, "enabled": True})
        api.call("PUT", f"/api/admin/models/{m['id']}/grants",
                 {"grants": [{"subject_type": "role", "subject_id": r, "effect": "allow"} for r in roles]})

    for mode in ("chat", "image", "video", "knowledge", "agents"):
        try:
            api.call("PUT", f"/api/admin/settings/modes.{mode}.enabled",
                     {"value": True, "scope_type": "deployment", "scope_id": "", "confirm_weaker": True})
        except RuntimeError as e:
            print(f"  mode {mode} not applied: {e}")
    for key, value in SWITCHES.items():
        api.call("PUT", f"/api/admin/settings/{key}", {"value": value, "scope_type": "deployment", "scope_id": "", "confirm_weaker": True})
    print("switches on:", ", ".join(SWITCHES), f"\naccounts: {len(creds)}; credentials file {cred_path} (mode {oct(os.stat(cred_path).st_mode & 0o777)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
