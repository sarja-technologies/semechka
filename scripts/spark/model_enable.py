#!/usr/bin/env python3
"""Operator-side: make one model available to named ROLES only, through the same services and audit trail as Admin > Models.

    ~/semechka-deploy/<name>/app/venv/bin/python -I model_enable.py --data ~/semechka-deploy/<name>/data \
        --model video:openjutsu-minimax-h3 --roles owner,platform_admin --modes video [--dry-run]
    (also image models, e.g. --model image:qwen-image-2512 --roles member,owner --modes image; the proof uses the first mode named)

1. discovers the backend's models (adds this one, disabled, with no grants, as for any new model)
2. enables it and allows exactly the named roles for exactly the named modes (existing rules for this model are kept)
3. proves the result with 'view as role' for EVERY role: only the named roles can use it; everyone else cannot
4. proves accounts, conversations, messages and every other model's enabled flag are unchanged.
No account, session or password is involved."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import sqlite3
import sys


def fingerprint(db: pathlib.Path, ignore_model: str) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "roles")}
    n["users_hash"] = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash from users order by id").fetchall()).encode()).hexdigest()[:12]
    others = con.execute("select id,alias,enabled,hidden from models where real_id != ? order by id", (ignore_model,)).fetchall()
    n["other_models_hash"] = hashlib.sha256(json.dumps(others).encode()).hexdigest()[:12]
    n["other_grants_hash"] = hashlib.sha256(json.dumps(con.execute(
        "select g.model_id,g.subject_type,g.subject_id,g.effect,g.modes from model_grants g join models m on m.id=g.model_id where m.real_id != ? order by 1,2,3", (ignore_model,)).fetchall()).encode()).hexdigest()[:12]
    con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", required=True, help="the model's real id as the backend reports it, e.g. video:openjutsu-minimax-h3")
    ap.add_argument("--roles", required=True)
    ap.add_argument("--modes", required=True, help="comma list, e.g. video")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    if not any((data / "pre-update").glob("*")):
        sys.exit("no pre-update data snapshot found; refusing to change anything")
    roles, modes = [r for r in a.roles.split(",") if r], [m for m in a.modes.split(",") if m]
    before = fingerprint(data / "semechka.db", a.model)
    print("before:", before)
    if a.dry_run:
        print(f"DRY RUN: would sync models, enable {a.model} and allow roles {roles} for modes {modes}. Nothing changed.")
        return 0
    from semechka import audit, services
    from semechka.backends_svc import sync_models
    from semechka.config import Config
    from semechka.models import Backend, Model, ModelGrant, Role
    from semechka.state import AppState
    from sqlalchemy import select

    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.session() as s:
        have = {r.id for r in s.execute(select(Role)).scalars()}
        bids = [b.id for b in s.execute(select(Backend)).scalars()]
    if [r for r in roles if r not in have]:
        sys.exit(f"unknown roles {[r for r in roles if r not in have]}; roles here: {sorted(have)}")
    for bid in bids:
        print("sync:", asyncio.run(sync_models(st.db.session, st, bid)))
    with st.db.session(write=True) as s:
        m = s.execute(select(Model).where(Model.real_id == a.model)).scalar_one_or_none()
        if m is None:
            sys.exit(f"the backend does not report {a.model}")
        m.enabled = True
        existing = {(g.subject_type, g.subject_id): g for g in s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id)).scalars()}
        for rid in roles:
            g = existing.get(("role", rid))
            if g is None:
                s.add(ModelGrant(model_id=m.id, subject_type="role", subject_id=rid, effect="allow", modes=modes))
            else:
                g.effect, g.modes = "allow", modes
        audit.record(s, "operator", "model.update", m.id, {"enabled": True, "source": "operator script model_enable.py"})
        audit.record(s, "operator", "model.grants", m.id, {"roles": roles, "modes": modes})
        mid = m.id
    print(f"enabled {a.model} ({mid}) for roles {roles}, modes {modes}")
    ok = True
    with st.db.session() as s:
        for rid in sorted(have):
            p = services.principal_for_role(s, rid)
            can = any(mm.id == mid for mm, _ in services.accessible_models(s, p, st, modes[0]))
            via_all = p.can("models.use_all")          # a built-in permission: such a role may use ANY enabled model, granted or not
            want = rid in roles or via_all
            flag = "OK" + (" (granted)" if rid in roles else " (built-in models.use_all, not a grant made here)" if via_all else "")
            if can != want:
                flag = "UNEXPECTED"
            ok &= can == want
            print(f"  view-as {rid:20} can use in {modes[0]}: {str(can):5}  {flag}")
        print("audit chain verifies:", audit.verify_chain(s).get("ok"))
    after = fingerprint(data / "semechka.db", a.model)
    same = all(after[k] == before[k] for k in before)
    print("accounts, conversations, messages, roles, other models and their grants unchanged:", same)
    return 0 if ok and same else 1


if __name__ == "__main__":
    sys.exit(main())
