#!/usr/bin/env python3
"""Rehearse migrate_to_llamacpp.py on a COPY of an instance's database and prove what it did and did not change.

    prove_llamacpp_migration.py <dir with semechka.db + master.key> --work <scratch dir> --spark-llm-dir DIR --api-key-file FILE \
        [--router-url URL]

Never touches the source directory. Steps, each checked (exit code 1 on the first failed check):
  1. copy the source to <work>/<name>; snapshot A (row counts and a hash of every table)
  2. dry-run: the copy is byte-identical afterwards
  3. --apply: snapshot B. Counts of users, conversations, messages, media_assets, media_jobs, model_grants, usage_events,
     org_members, ... identical to A; users.password_hash and sessions rows byte-identical; every table other than
     backends/models/setting_values/setting_revisions/audit_events identical; no model row id lost; alias/enabled/pinned/sort/hidden/
     tags/system_prompt/gen_limits/allow_tools/defaults/profile identical; every migrated model's real_id is the router id
  4. --apply again: nothing changes (idempotent)
  5. --rollback: models, backends and setting_values rows equal snapshot A exactly
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MIGRATE = HERE / "migrate_to_llamacpp.py"
CHANGEABLE = {"backends", "models", "setting_values", "setting_revisions", "audit_events"}
KEEP_COLS = ("alias", "enabled", "pinned", "sort", "hidden", "tags", "system_prompt", "gen_limits", "allow_tools", "defaults", "profile",
             "created_at")
FAILS: list[str] = []


def check(ok: bool, what: str):
    print(("  PASS  " if ok else "  FAIL  ") + what)
    if not ok:
        FAILS.append(what)


def tables(con):
    return [r[0] for r in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by 1")]


def snap(db: Path) -> dict:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = {"counts": {}, "hash": {}}
    for t in tables(con):
        rows = con.execute(f'select * from "{t}"').fetchall()
        out["counts"][t] = len(rows)
        out["hash"][t] = hashlib.sha256(json.dumps(sorted(map(repr, rows))).encode()).hexdigest()
    out["pw"] = hashlib.sha256(repr(con.execute("select id, password_hash from users order by id").fetchall()).encode()).hexdigest()
    out["sessions"] = hashlib.sha256(repr(con.execute("select * from sessions order by 1").fetchall()).encode()).hexdigest()
    out["models"] = {r[0]: r for r in con.execute("select id, backend_id, real_id, " + ", ".join(KEEP_COLS) + " from models")}
    out["models_cols"] = ["id", "backend_id", "real_id", *KEEP_COLS]
    out["backends_n"] = con.execute("select count(*) from backends").fetchone()[0]
    con.close()
    return out


def run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(MIGRATE), *args], capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--work", required=True)
    ap.add_argument("--spark-llm-dir", required=True)
    ap.add_argument("--api-key-file", required=True)
    ap.add_argument("--router-url", default="http://127.0.0.1:8080/v1")
    a = ap.parse_args()
    src = Path(a.src).expanduser()
    work = Path(a.work).expanduser() / (src.name + "-rehearsal")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for f in ("semechka.db", "master.key"):
        shutil.copy2(src / f, work / f)
    common = [str(work), "--spark-llm-dir", a.spark_llm_dir, "--api-key-file", a.api_key_file, "--router-url", a.router_url]
    key = Path(a.api_key_file).expanduser().read_text().strip()
    A = snap(work / "semechka.db")
    h0 = hashlib.sha256((work / "semechka.db").read_bytes()).hexdigest()
    print(f"== {src.name}: users={A['counts']['users']} conversations={A['counts']['conversations']} messages={A['counts']['messages']}")

    print("-- dry run")
    p = run(common)
    check(p.returncode == 0 and "DRY RUN" in p.stdout, "dry-run exits 0 and says it changed nothing")
    check(hashlib.sha256((work / "semechka.db").read_bytes()).hexdigest() == h0, "database file byte-identical after the dry run")
    check(not list(work.glob("*.rollback.json")) and not list(work.glob("semechka.db.pre-*")), "dry run wrote no backup and no rollback file")
    check(key not in p.stdout + p.stderr, "router key not in the dry-run output")

    print("-- apply")
    p = run([*common, "--apply"])
    print("   " + p.stdout.strip().replace("\n", "\n   ").splitlines()[-1] if p.stdout.strip() else p.stderr[-400:])
    check(p.returncode == 0, "apply exits 0" + ("" if p.returncode == 0 else ": " + p.stderr[-300:]))
    check(key not in p.stdout + p.stderr, "router key not in the apply output")
    rbs = sorted(work.glob("*.rollback.json"))
    baks = sorted(work.glob("semechka.db.pre-llamacpp-*"))
    check(len(rbs) == 1 and len(baks) == 1, "one rollback file and one pre-migration backup written")
    if baks:
        check(snap(baks[0])["hash"] == A["hash"], "the backup is identical to the original, table by table")
    check(key not in rbs[0].read_text() if rbs else False, "router key not in the rollback file")
    B = snap(work / "semechka.db")
    for t in ("users", "conversations", "messages", "media_assets", "media_jobs", "model_grants", "usage_events", "org_members", "models",
              "model_group_members", "model_groups", "sessions", "conversation_contexts", "message_sources", "chat_attachments", "roles"):
        if t in A["counts"]:
            check(A["counts"][t] == B["counts"][t], f"count {t}: {A['counts'][t]} -> {B['counts'][t]}")
    check(A["pw"] == B["pw"], "users.password_hash byte-identical")
    check(A["sessions"] == B["sessions"], "sessions rows byte-identical")
    same = [t for t in A["hash"] if t not in CHANGEABLE]
    bad = [t for t in same if A["hash"][t] != B["hash"][t]]
    check(not bad, f"every other table identical ({len(same)} tables)" + (f"; DIFFER: {bad}" if bad else ""))
    check(set(A["models"]) == set(B["models"]), "no model row lost or added (same ids)")
    cols = A["models_cols"]
    lost = []
    for mid, ra in A["models"].items():
        rb_ = B["models"][mid]
        for c in KEEP_COLS:
            if ra[cols.index(c)] != rb_[cols.index(c)]:
                lost.append((mid, c))
    check(not lost, "alias/enabled/pinned/sort/hidden/tags/system_prompt/gen_limits/allow_tools/defaults/profile unchanged on every model" +
          (f"; CHANGED: {lost[:5]}" if lost else ""))
    moved = [(mid, A["models"][mid][2], B["models"][mid][2]) for mid in A["models"] if A["models"][mid][2] != B["models"][mid][2]]
    local = [mid for mid, r in A["models"].items() if r[2].startswith("local:")]
    check(len(moved) == len(local) and all(not new.startswith(("local:", "image:", "video:", "endpoint:")) for _, _, new in moved),
          f"{len(moved)} of {len(local)} local models now carry router ids")
    media_same = all(A["models"][m][1:3] == B["models"][m][1:3] for m in A["models"] if A["models"][m][2].startswith(("image:", "video:", "endpoint:")))
    check(media_same, "image/video/endpoint model rows untouched (backend and real_id)")
    check(B["backends_n"] == A["backends_n"] + 1, "exactly one backend row added")

    print("-- apply again (idempotent)")
    p = run([*common, "--apply"])
    check(p.returncode == 0 and "Nothing to do" in p.stdout, "second apply says there is nothing to do")
    C = snap(work / "semechka.db")
    check(C["hash"] == B["hash"], "database tables identical after the second apply")
    check(len(sorted(work.glob("*.rollback.json"))) == 1, "second apply wrote no second rollback file")

    print("-- rollback")
    p = run([str(work), "--rollback", str(rbs[0])])
    check(p.returncode == 0, "rollback exits 0" + ("" if p.returncode == 0 else ": " + p.stderr[-300:]))
    D = snap(work / "semechka.db")
    for t in ("models", "backends", "setting_values"):
        check(D["hash"][t] == A["hash"][t], f"table {t} exactly as before the migration")
    other = [t for t in A["hash"] if t not in CHANGEABLE]
    check(all(D["hash"][t] == A["hash"][t] for t in other), "all user data tables still identical")
    check(D["pw"] == A["pw"] and D["sessions"] == A["sessions"], "password hashes and sessions identical")
    p = run([str(work), "--rollback", str(rbs[0])])
    check(p.returncode == 0 and snap(work / "semechka.db")["hash"]["models"] == A["hash"]["models"], "a second rollback is harmless")
    print(f"\n{'ALL CHECKS PASSED' if not FAILS else str(len(FAILS)) + ' CHECK(S) FAILED'}   (copy kept in {work})")
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main())
