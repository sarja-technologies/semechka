#!/usr/bin/env python3
"""Operator-side: throwaway acceptance accounts on a LIVE instance, so the whole chain can be tested on the real GPU without anyone's real login.

    ~/semechka-deploy/<name>/app/venv/bin/python -I accept_accounts.py create --data ~/semechka-deploy/<name>/data \
        --org "CMSIT Pilot" --creds ~/semechka-deploy/secrets/<name>-accept-credentials.txt
    ... accept_accounts.py delete --data ... --creds ...

create: two accounts (<prefix>-user and <prefix>-admin, ordinary Member role, no other permission) put in ONE chosen organisation, so the
        throwaway administrator's organisation-scoped reach is that organisation only (choose the smallest one). The admin gets the named
        permission admin.content.read as a per-person override, nothing else. Passwords are generated, written to a 0600 file, never printed.
delete: removes both accounts and everything they made (their files, conversations, media), exactly as Administration > People > Delete does.
        The audit log keeps its rows (it is append-only and hash-chained)."""
from __future__ import annotations

import argparse
import os
import pathlib
import secrets
import sys

DOMAIN = "accept.invalid"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["create", "delete"])
    ap.add_argument("--data", required=True)
    ap.add_argument("--creds", required=True)
    ap.add_argument("--org", default="")
    ap.add_argument("--prefix", default="release-acceptance")
    a = ap.parse_args()
    data, creds = pathlib.Path(a.data).expanduser(), pathlib.Path(a.creds).expanduser()
    emails = {"user": f"{a.prefix}-user@{DOMAIN}", "admin": f"{a.prefix}-admin@{DOMAIN}"}
    from semechka import audit, services, work_svc
    from semechka.config import Config
    from semechka.models import User, UserOverride, UserRole
    from semechka.models_tenancy import Org, OrgMember, OrgRole
    from semechka.security import hash_password
    from semechka.state import AppState
    from sqlalchemy import select

    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    if a.cmd == "create":
        if not a.org:
            sys.exit("--org is required")
        pw = {k: secrets.token_urlsafe(18) for k in emails.values()}
        with st.db.session(write=True) as s:
            org = s.execute(select(Org).where(Org.name == a.org)).scalar_one_or_none()
            if org is None:
                sys.exit(f"no organisation named {a.org!r}")
            role = s.execute(select(OrgRole).where(OrgRole.org_id == org.id, OrgRole.key == "member")).scalar_one()
            for kind, email in emails.items():
                if s.execute(select(User).where(User.email == email)).scalar_one_or_none():
                    sys.exit(f"{email} already exists; run delete first")
                u = User(email=email, display_name=f"Release acceptance ({kind})", password_hash=hash_password(pw[email]), status="active")
                s.add(u)
                s.flush()
                s.add(UserRole(user_id=u.id, role_id="member"))
                s.add(OrgMember(org_id=org.id, user_id=u.id, status="active", role_ids=[role.id], added_by="operator"))
                if kind == "admin":
                    s.add(UserOverride(user_id=u.id, grants=["admin.content.read"], denies=[]))
                audit.record(s, "operator", "user.create", u.id, {"source": "operator script accept_accounts.py", "kind": "throwaway acceptance account"})
        creds.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(creds, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write("# throwaway acceptance accounts (deleted after the run). owner-only file.\n# email\tpassword\n" + "\n".join(f"{e}\t{p}" for e, p in pw.items()) + "\n")
        print(f"created {sorted(emails.values())} in organisation {a.org!r}; credentials in {creds} (0600)")
        return 0
    with st.db.session(write=True) as s:
        for email in emails.values():
            u = s.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if u is None:
                print(f"{email}: not present")
                continue
            audit.record(s, "operator", "user.delete", u.id, {"source": "operator script accept_accounts.py"})
            services.purge_user_files(st, u.id)
            work_svc.purge_user(st, s, u.id)
            s.delete(u)
            print(f"{email}: deleted")
    if creds.exists():
        creds.unlink()
    with st.db.session() as s:
        left = s.execute(select(User).where(User.email.like(f"%@{DOMAIN}"))).scalars().all()
        print("acceptance accounts left:", len(left), "| audit chain verifies:", audit.verify_chain(s).get("ok"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
