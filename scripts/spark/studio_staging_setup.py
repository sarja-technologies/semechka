#!/usr/bin/env python3
"""Point the STAGING Semechka instance at a dedicated staging Sarness engine and enable the OpenJutsu / Imajutsu models for its synthetic
roles. Runs ON the Spark through Semechka's own admin API (no database writes). Never touches Roasted or Salted. Passwords are read from the
0600 credentials file and never printed. Safe to re-run.

    python3 studio_staging_setup.py --base http://192.168.20.193:8790 --engine-url http://127.0.0.1:8764 \
        --engine-token-file ~/sarness-stage/token --credentials ~/semechka-deploy/secrets/staging-credentials.txt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from provision_salted import Api, load_credentials  # noqa: E402

DOMAIN = "staging.test"
ROLES = ["owner", "platform_admin", "member"]
MODELS = [  # engine id fragment (exact real_id suffix), alias, tags
    ("openjutsu-minimax-h3", "OpenJutsu", ["video", "editing"]),
    ("openjutsu-reference-video", "OpenJutsu Reference", ["video", "editing"]),
    ("flux2-klein-4b", "FLUX.2 klein", ["image", "fast", "photoreal"]),
    ("z-image-turbo", "Z-Image Turbo", ["image", "fast"]),
    ("qwen-image-2512", "Qwen-Image 2512", ["image", "photoreal"]),
    ("qwen-image-edit-2511", "Qwen-Image Edit", ["image", "edit"]),
    ("wan2.2-ti2v-5b", "Clip", ["video", "small"]),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--engine-url", required=True)
    ap.add_argument("--engine-token-file", required=True)
    ap.add_argument("--credentials", required=True)
    ap.add_argument("--only", default="", help="comma-separated engine ids to enable (default: all in the list)")
    a = ap.parse_args()
    base = a.base.rstrip("/")
    token = Path(a.engine_token_file).expanduser().read_text().strip()
    creds = load_credentials(Path(a.credentials).expanduser())
    owner = f"owner@{DOMAIN}"
    if owner not in creds:
        sys.exit("owner password unknown; this is not a provisioned staging instance")
    api = Api(base)
    api.login(owner, creds[owner])

    backends = api.call("GET", "/api/admin/backends")
    b = next(x for x in backends if x["adapter"] == "sarness")
    if b["base_url"].rstrip("/") != a.engine_url.rstrip("/"):
        api.call("PUT", f"/api/admin/backends/{b['id']}", {"name": b["name"], "adapter": "sarness", "base_url": a.engine_url, "api_key": token,
                                                           "external": b["external"], "enabled": True})
        print("backend now points at", a.engine_url)
    else:
        print("backend already points at", a.engine_url)
    api.call("POST", f"/api/admin/backends/{b['id']}/sync", {})
    models = api.call("GET", "/api/admin/models?limit=500")
    items = models.get("items", models) if isinstance(models, dict) else models
    only = {x for x in a.only.split(",") if x}
    for key, alias, tags in MODELS:
        if only and key not in only:
            continue
        m = next((m for m in items if m["real_id"].split(":", 1)[-1] == key and (m.get("backend_id") in (None, b["id"]) or True)), None)
        if not m:
            print(f"  {key}: not on the engine, skipped")
            continue
        api.call("PATCH", f"/api/admin/models/{m['id']}", {"alias": alias, "tags": tags, "enabled": True})
        api.call("PUT", f"/api/admin/models/{m['id']}/grants", {"grants": [{"subject_type": "role", "subject_id": r, "effect": "allow"} for r in ROLES]})
        print(f"  {key}: enabled as '{alias}' for {', '.join(ROLES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
