#!/usr/bin/env python3
"""Operator-side enablement of Trinodal AI on one Semechka instance (for the person with shell access to the host, like
`semechka org purge` and rollout_local.py): no sign-in, no session, no password involved. Every change goes through the same
services the admin screens use and is audited (actor "operator").

    ~/semechka-deploy/<name>/app/venv/bin/python -I trinodal_enable.py --data ~/semechka-deploy/<name>/data \
        --url http://127.0.0.1:8800 --model-roles owner,member --owner-roles owner --view-train-roles owner,platform_admin [--dry-run]

What it does, in order (each step read back):
  1. trinodal.url deployment setting
  2. the Sarness backend option local_endpoints += ["trinodal"]   (Trinodal runs on this host: not "external")
  3. model sync on that backend, which discovers endpoint:trinodal:trinodal (new models start disabled, with no grants)
  4. enable that one model, alias "Trinodal AI", grant it to --model-roles
  5. add trinodal.view + trinodal.train to --view-train-roles and trinodal.admin to --owner-roles
It touches no account, no conversation and no other model or setting, and prints before/after counts to prove it."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import sqlite3
import sys


def counts(db: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "models", "roles", "model_grants")}
    n["users_hash"] = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash from users order by id").fetchall()).encode()).hexdigest()[:12]
    n["other_models_hash"] = hashlib.sha256(json.dumps(con.execute("select id,alias,enabled from models where real_id not like 'endpoint:trinodal%' order by id").fetchall()).encode()).hexdigest()[:12]
    con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8800")
    ap.add_argument("--model-roles", required=True)
    ap.add_argument("--owner-roles", default="owner")
    ap.add_argument("--view-train-roles", default="owner,platform_admin")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    if not any((data / "pre-update").glob("*")):
        sys.exit("no pre-update data snapshot found; refusing to change anything")
    before = counts(data / "semechka.db")
    print("before:", before)

    from semechka import audit, backends_svc
    from semechka.config import Config
    from semechka.models import Backend, Model, ModelGrant, Role
    from semechka.state import AppState
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.session() as s:
        backends = s.execute(select(Backend).where(Backend.adapter == "sarness")).scalars().all()
        roles = {r.id for r in s.execute(select(Role)).scalars()}
    if len(backends) != 1:
        sys.exit(f"expected exactly one Sarness backend, found {len(backends)}")
    bid = backends[0].id
    want_roles = [x for x in (a.model_roles + "," + a.owner_roles + "," + a.view_train_roles).split(",") if x]
    missing = [r for r in want_roles if r not in roles]
    if missing:
        sys.exit(f"unknown roles {missing}; roles here: {sorted(roles)}")
    if a.dry_run:
        print(f"DRY RUN: would set trinodal.url={a.url!r}, local_endpoints on backend {bid}, sync, enable the Trinodal model for {a.model_roles}, "
              f"grant trinodal.view/train to {a.view_train_roles} and trinodal.admin to {a.owner_roles}. Nothing changed.")
        return 0

    with st.db.session(write=True) as s:
        st.settings.set(s, "operator", "trinodal.url", a.url, ip="local", confirm_weaker=True)  # opens the same-origin proxy: explicit operator confirmation
    with st.db.session() as s:
        assert st.settings.deployment(s, "trinodal.url") == a.url
    print("1. trinodal.url set and read back")

    with st.db.session(write=True) as s:
        b = s.get(Backend, bid)
        opts = dict(b.options or {})
        eps = list(dict.fromkeys([*(opts.get("local_endpoints") or []), "trinodal"]))
        b.options = {**opts, "local_endpoints": eps}
        flag_modified(b, "options")
        audit.record(s, "operator", "backend.update", bid, {"local_endpoints": eps})
    print("2. backend local_endpoints =", eps)

    res = asyncio.run(backends_svc.sync_models(st.db.session, st, bid))
    print("3. model sync:", res)
    with st.db.session(write=True) as s:
        m = s.execute(select(Model).where(Model.backend_id == bid, Model.real_id == "endpoint:trinodal:trinodal")).scalar_one_or_none()
        if m is None:
            sys.exit("the engine does not list endpoint:trinodal:trinodal (register Trinodal in the engine first)")
        if (m.meta or {}).get("external"):
            sys.exit("the model is still marked external; local_endpoints did not take effect")
        m.enabled, m.alias = True, "Trinodal AI"
        for rid in [x for x in a.model_roles.split(",") if x]:
            if not s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id, ModelGrant.subject_type == "role", ModelGrant.subject_id == rid)).first():
                s.add(ModelGrant(model_id=m.id, subject_type="role", subject_id=rid, effect="allow", modes=None))
        audit.record(s, "operator", "model.enable", m.id, {"alias": "Trinodal AI", "roles": a.model_roles})
        mid = m.id
    print("4. model enabled and granted:", mid)

    def add_perm(role_ids: str, perms: list[str]):
        for rid in [x for x in role_ids.split(",") if x]:
            with st.db.session(write=True) as s:
                r = s.get(Role, rid)
                new = [p for p in perms if p not in (r.grants or [])]
                if new:
                    r.grants = [*(r.grants or []), *new]
                    flag_modified(r, "grants")
                    audit.record(s, "operator", "role.update", rid, {"added": new})
                print(f"5. role {rid}: +{new or 'nothing (already held)'}")
    add_perm(a.view_train_roles, ["trinodal.view", "trinodal.train"])
    add_perm(a.owner_roles, ["trinodal.admin"])
    with st.db.session() as s:
        chain = audit.verify_chain(s)
    after = counts(data / "semechka.db")
    print("after: ", after)
    ok = (after["users_hash"] == before["users_hash"] and after["conversations"] == before["conversations"] and after["messages"] == before["messages"]
          and after["other_models_hash"] == before["other_models_hash"] and chain.get("ok") is True)
    print("accounts, conversations, messages and every other model unchanged; audit chain verifies:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
