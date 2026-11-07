#!/usr/bin/env python3
"""Operator-side: switch on the audited administrator content explorer for NAMED administrators only, through the same services and audit trail
as Administration > Privacy and People > Overrides. No account, session or password is involved.

    ~/semechka-deploy/<name>/app/venv/bin/python -I admin_content_enable.py --data ~/semechka-deploy/<name>/data \
        --admin owner@example.com [--admin other@example.com] [--instance] [--dry-run]

1. privacy.admin_content_access = 'audited' (a weaker setting: the confirmation is passed on purpose, and the change is recorded)
2. admin.content.read granted BY NAME to each named administrator (a per-person override, merged with what they already have);
   --instance also grants admin.content.instance (all organisations); without it the administrator reaches only their own organisations
3. proof: each named person can use the explorer; EVERY other person cannot; a role that holds a wildcard still cannot (it is by name only)
4. proof: accounts, conversations, messages, roles and every other setting are unchanged, and the audit chain still verifies.
Re-running is harmless. To switch it off again: set the policy back to 'off' in Administration, Privacy & Retention."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys


def fingerprint(db: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "roles", "media_jobs", "media_assets")}
    n["users_hash"] = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash from users order by id").fetchall()).encode()).hexdigest()[:12]
    n["other_settings_hash"] = hashlib.sha256(json.dumps(con.execute(
        "select key,scope_type,scope_id,value from setting_values where key != 'privacy.admin_content_access' order by 1,2,3").fetchall()).encode()).hexdigest()[:12]
    con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--admin", action="append", required=True, help="e-mail of an administrator to authorise (repeat for several)")
    ap.add_argument("--instance", action="store_true", help="also grant admin.content.instance (every organisation of this instance)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    if not any((data / "pre-update").glob("*")):
        sys.exit("no pre-update data snapshot found; refusing to change anything")
    before = fingerprint(data / "semechka.db")
    print("before:", before)
    want = ["admin.content.read"] + (["admin.content.instance"] if a.instance else [])
    if a.dry_run:
        print(f"DRY RUN: would set privacy.admin_content_access=audited and grant {want} by name to {a.admin}. Nothing changed.")
        return 0
    from semechka import audit, services
    from semechka.config import Config
    from semechka.models import User, UserOverride
    from semechka.state import AppState
    from sqlalchemy import select

    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    with st.db.session(write=True) as s:
        people = {}
        for email in a.admin:
            u = s.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
            if u is None:
                sys.exit(f"no such account: {email}; nothing was changed")
            people[email] = u.id
        st.settings.set(s, "operator", "privacy.admin_content_access", "audited", confirm_weaker=True)
        for email, uid in people.items():
            o = s.get(UserOverride, uid)
            grants = sorted(set((o.grants if o else []) + want))
            if o is None:
                s.add(UserOverride(user_id=uid, grants=grants, denies=[]))
            else:
                o.grants = grants
            audit.record(s, "operator", "user.override", uid, {"grants": grants, "source": "operator script admin_content_enable.py"})
    print("policy: audited;", "granted by name:", want, "to", sorted(people))
    ok = True
    with st.db.session() as s:
        for u in s.execute(select(User)).scalars():
            p = services.load_principal(s, u)
            has = p.can("admin.content.read")
            should = u.email in people
            flag = "OK" + (" (named administrator)" if should else "")
            if has != should:
                flag, ok = "UNEXPECTED", False
            print(f"  {u.email:36} admin.content.read: {str(has):5} instance: {str(p.can('admin.content.instance')):5} {flag}")
        print("audit chain verifies:", audit.verify_chain(s).get("ok"))
    after = fingerprint(data / "semechka.db")
    same = all(after[k] == before[k] for k in before)
    print("accounts, conversations, messages, media, roles and every other setting unchanged:", same)
    return 0 if ok and same else 1


if __name__ == "__main__":
    sys.exit(main())
