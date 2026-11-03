#!/usr/bin/env python3
"""Operator-side product naming for one Semechka instance (no sign-in): sets the user-facing names through the same audited
settings service the Branding admin page uses. Semechka stays the framework name internally; nothing about databases,
ports, accounts or imports changes.

    ~/semechka-deploy/<name>/app/venv/bin/python -I brand_rename.py --data ~/semechka-deploy/<name>/data \
        --name SARJA-AI [--footer "..."] [--signin-message "..."] [--dry-run]

Sets brand.app_name, brand.short_name and brand.tab_title to --name (plus footer/sign-in text when given), reads each back,
and proves accounts, conversations, messages and models are untouched."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys


def fingerprint(db: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "models", "roles")}
    n["users_hash"] = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash from users order by id").fetchall()).encode()).hexdigest()[:12]
    con.close()
    return n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--footer")
    ap.add_argument("--signin-message")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    if not any((data / "pre-update").glob("*")):
        sys.exit("no pre-update data snapshot found; refusing to change anything")
    want = {"brand.app_name": a.name, "brand.short_name": a.name, "brand.tab_title": a.name}
    if a.footer is not None:
        want["brand.footer_text"] = a.footer
    if a.signin_message is not None:
        want["brand.signin_message"] = a.signin_message
    before = fingerprint(data / "semechka.db")
    if a.dry_run:
        print("DRY RUN would set:", want, "\nbefore:", before)
        return 0
    from semechka.config import Config
    from semechka.state import AppState
    cfg = Config(data_dir=data)
    cfg.background = False
    st = AppState.build(cfg)
    for k, v in want.items():
        with st.db.session(write=True) as s:
            st.settings.set(s, "operator", k, v, ip="local")
        with st.db.session() as s:
            assert st.settings.deployment(s, k) == v, k
        print(f"set {k} = {v!r}")
    after = fingerprint(data / "semechka.db")
    ok = all(after[k] == before[k] for k in before)
    print("accounts, conversations, messages, models and roles unchanged:", ok)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
