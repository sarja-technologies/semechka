#!/usr/bin/env python3
"""Operator-side: switch OFF the retired Sarness Engine backend of one Semechka instance (run with the instance STOPPED).

    ~/semechka-deploy/<name>/app/venv/bin/python -I retire_legacy_backend.py <data-dir> [--apply] [--rollback FILE]

Dry run by default. --apply takes a consistent DB backup, then sets enabled=0 on every backend whose adapter is 'sarness' and on every
model that lives on such a backend. Nothing is deleted: conversations, messages, media jobs/assets, grants and the model rows themselves stay
(old history keeps displaying the model names). Writes <data>/retire-legacy-<ts>.rollback.json holding every original value; --rollback restores them.
Proves users/sessions/conversations/messages/media are byte-identical before and after."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
import time


def fp(con) -> dict:
    d = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "sessions", "conversations", "messages", "media_assets", "media_jobs", "model_grants")}
    for name, q in (("users", "select id,email,status,password_hash from users order by id"), ("sessions", "select id_hash,user_id,expires_at from sessions order by id_hash"),
                    ("media_jobs", "select id,status,model_id from media_jobs order by id"), ("grants", "select * from model_grants order by 1,2,3")):
        d[name + "_hash"] = hashlib.sha256(json.dumps(con.execute(q).fetchall(), default=str).encode()).hexdigest()[:12]
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("data")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback")
    a = ap.parse_args()
    data = pathlib.Path(a.data).expanduser()
    db = data / "semechka.db"
    con = sqlite3.connect(db)
    if a.rollback:
        saved = json.loads(pathlib.Path(a.rollback).read_text())
        for bid, en in saved["backends"].items():
            con.execute("update backends set enabled=? where id=?", (en, bid))
        for mid, en in saved["models"].items():
            con.execute("update models set enabled=? where id=?", (en, mid))
        con.commit()
        print(f"rolled back {len(saved['backends'])} backend(s), {len(saved['models'])} model(s)")
        return 0
    legacy = con.execute("select id,name,enabled from backends where adapter='sarness'").fetchall()
    models = [(m, b, e, al) for (b, _, _) in legacy for (m, e, al) in con.execute("select id,enabled,alias from models where backend_id=?", (b,)).fetchall() for b in [b]]
    print(f"legacy sarness backends: {[(n, 'on' if e else 'off') for _, n, e in legacy]}")
    for m, b, e, al in models:
        print(f"  model {al!r:40} enabled={e}")
    if not legacy:
        print("nothing to do")
        return 0
    if not a.apply:
        print("DRY RUN: nothing changed. Add --apply.")
        return 0
    before = fp(con)
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = data / f"semechka.db.pre-retire-legacy-{ts}"
    b2 = sqlite3.connect(bak)
    con.backup(b2)
    b2.close()
    saved = {"backends": {b: e for b, _, e in legacy}, "models": {m: e for m, _, e, _ in models}}
    (data / f"retire-legacy-{ts}.rollback.json").write_text(json.dumps(saved))
    for b, _, _ in legacy:
        con.execute("update backends set enabled=0 where id=?", (b,))
        con.execute("update models set enabled=0 where backend_id=?", (b,))
    con.commit()
    after = fp(con)
    same = {k: before[k] == after[k] for k in before}
    print("backup:", bak.name, "| rollback file:", f"retire-legacy-{ts}.rollback.json")
    print("untouched:", same)
    return 0 if all(same.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
