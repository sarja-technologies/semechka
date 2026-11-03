#!/usr/bin/env python3
"""Operator-side enablement of the /schemra/ route on one Semechka instance (for the person with shell access to the host, like
trinodal_enable.py): no sign-in, no session, no password. Every change goes through the services the admin screens use and is
audited (actor "operator").

    ~/semechka-deploy/<name>/app/venv/bin/python -I schemra_enable.py --data ~/semechka-deploy/<name>/data \
        [--upstream http://127.0.0.1:8820] [--view-roles ...] [--operate-roles ...] [--admin-roles owner] [--dry-run]

What it does, each step read back:
  1. checks that the shared secret file exists with mode 0600 (the file's content is never printed)
  2. schemra.upstream (loopback only, validated by the settings registry) and schemra.enabled = true
  3. adds schemra.view / schemra.operate / schemra.admin to the named roles (Owner already holds them through its wildcard)
It touches no account, conversation or model, and prints before/after counts to prove it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import stat
import sys


def counts(db: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "models", "roles")}
    n["users_hash"] = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash from users order by id").fetchall()).encode()).hexdigest()[:12]
    n["models_hash"] = hashlib.sha256(json.dumps(con.execute("select id,alias,enabled from models order by id").fetchall()).encode()).hexdigest()[:12]
    con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--upstream", default="http://127.0.0.1:8820")
    ap.add_argument("--view-roles", default="")
    ap.add_argument("--operate-roles", default="")
    ap.add_argument("--admin-roles", default="")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    if not any((data / "pre-update").glob("*")):
        sys.exit("no pre-update data snapshot found; refusing to change anything")
    secret = pathlib.Path(os.environ.get("SEMECHKA_SCHEMRA_SECRET_FILE") or data / "schemra-proxy.secret")
    try:
        stt = secret.stat()
    except OSError:
        sys.exit(f"shared secret file {secret} does not exist (install -m 600 the same bytes Schemra uses)")
    if not stat.S_ISREG(stt.st_mode) or stt.st_mode & 0o077 or stt.st_uid != os.getuid() or len(secret.read_text().strip()) < 32:
        sys.exit("the secret file must be a regular file you own, mode 0600, at least 32 characters")
    before = counts(data / "semechka.db")
    print("before:", before)

    from semechka import audit
    from semechka.config import Config
    from semechka.models import Role
    from semechka.state import AppState
    from sqlalchemy import select
    from sqlalchemy.orm.attributes import flag_modified

    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.session() as s:
        roles = {r.id for r in s.execute(select(Role)).scalars()}
    want = [x for x in ",".join([a.view_roles, a.operate_roles, a.admin_roles]).split(",") if x]
    missing = [r for r in want if r not in roles]
    if missing:
        sys.exit(f"unknown roles {missing}; roles here: {sorted(roles)}")
    if a.dry_run:
        print(f"DRY RUN: would set schemra.upstream={a.upstream!r}, schemra.enabled=true and grant view/operate/admin to "
              f"{a.view_roles or '-'} / {a.operate_roles or '-'} / {a.admin_roles or '-'}. Nothing changed.")
        return 0
    with st.db.session(write=True) as s:
        st.settings.set(s, "operator", "schemra.upstream", a.upstream, ip="local")
    with st.db.session(write=True) as s:
        st.settings.set(s, "operator", "schemra.enabled", True, ip="local", confirm_weaker=True)    # widens what is reachable: explicit operator confirmation
    with st.db.session() as s:
        assert st.settings.deployment(s, "schemra.enabled") is True and st.settings.deployment(s, "schemra.upstream") == a.upstream
    print("1. schemra.upstream and schemra.enabled set and read back")

    for ids, perm in ((a.view_roles, "schemra.view"), (a.operate_roles, "schemra.operate"), (a.admin_roles, "schemra.admin")):
        for rid in [x for x in ids.split(",") if x]:
            with st.db.session(write=True) as s:
                r = s.get(Role, rid)
                if perm not in (r.grants or []):
                    r.grants = [*(r.grants or []), perm]
                    flag_modified(r, "grants")
                    audit.record(s, "operator", "role.update", rid, {"added": [perm]})
                    print(f"2. role {rid}: +{perm}")
                else:
                    print(f"2. role {rid}: {perm} already held")
    with st.db.session() as s:
        chain = audit.verify_chain(s)
    after = counts(data / "semechka.db")
    print("after: ", after)
    ok = (after["users_hash"] == before["users_hash"] and after["conversations"] == before["conversations"]
          and after["messages"] == before["messages"] and after["models_hash"] == before["models_hash"] and chain.get("ok") is True)
    print("accounts, conversations, messages and models unchanged; audit chain verifies:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
