#!/usr/bin/env python3
"""Operator-side: set deployment settings for ONE Semechka instance without a sign-in, through the same audited settings service the
Administration screens use. Proves that accounts, password hashes, sessions, conversations, messages and media are untouched.

    ~/semechka-deploy/<name>/app/venv/bin/python -I settings_set.py --data ~/semechka-deploy/<name>/data \
        --set goose.enabled=true --set goose.default_model=qwen3.6-35b-a3b [--dry-run]

Values are JSON (true, 12, "text"); anything that is not valid JSON is taken as a plain string. Needs a pre-update data snapshot."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys


def fingerprint(db: pathlib.Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "sessions", "conversations", "messages", "media_assets", "media_jobs")}
    n["users_hash"] = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash from users order by id").fetchall()).encode()).hexdigest()[:12]
    n["sessions_hash"] = hashlib.sha256(json.dumps(con.execute("select id_hash,user_id,created_at,expires_at from sessions order by id_hash").fetchall()).encode()).hexdigest()[:12]
    con.close()
    return n


def parse(v: str):
    try:
        return json.loads(v)
    except ValueError:
        return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    if not any((data / "pre-update").glob("*")):
        sys.exit("no pre-update data snapshot found; refusing to change anything")
    want = {}
    for item in a.set:
        k, sep, v = item.partition("=")
        if not sep or not k:
            sys.exit(f"bad --set {item!r}: use KEY=VALUE")
        want[k.strip()] = parse(v)
    if not want:
        sys.exit("nothing to set")
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
    same = {k: before[k] == after[k] for k in before}
    print("untouched:", same)
    return 0 if all(same.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
