#!/usr/bin/env python3
"""Operator-side: point one Semechka instance's image/video models at the Spark media engine (OpenSfield's ComfyUI behind http://127.0.0.1:8780/v1).

    cd backend && PYTHONPATH=$PWD <venv>/bin/python ../scripts/spark/migrate_media_to_engine.py <data-dir> \
        [--engine-url URL] [--token-file ~/opensfield/config/semechka-media.token | --token-value-from-env VAR] \
        [--catalog-file caps.json] [--backend-name 'Spark media engine'] [--grant-profiles] [--allow-active] [--apply]
    ... migrate_media_to_engine.py <data-dir> --rollback <data-dir>/pre-update-media-engine-<ts>/rollback.json

Dry run by default (the database is opened read-only and nothing is written): it prints, per image/video model, what would happen.

--apply, in one transaction after a backup (sqlite backup API) into <data-dir>/pre-update-media-engine-<ts>/ together with rollback.json:
  1. refuses while a media job is queued or running (--allow-active only for rehearsals on copies);
  2. creates (or finds, by adapter 'spark-media' + base URL) the backend row for the engine; the token is read here, encrypted with the instance's vault
     (purpose "backend") and never printed;
  3. every image:/video: model row of a 'sarness' backend is MOVED to it: same Model.id (so grants, settings, Imajutsu routing and the media_jobs that name it
     keep working). If the engine offers the same real id its meta/capabilities are replaced (like a catalogue sync) and it is available; otherwise it is
     moved too, marked available=false with meta.media.unavailable_reason, and keeps its enabled flag;
  4. engine models/profiles without a row are added disabled with no grants (like any newly discovered model);
  5. with --grant-profiles, the grants of the old OpenJutsu row (video:openjutsu-minimax-h3) are copied to video:opensfield-video-edit, which is then enabled.
media_jobs, media_assets and the asset files are never touched; neither is any non-media model, any chat/LLM backend row, any user or conversation.
Idempotent: a second --apply finds nothing to do. --rollback restores every touched row from rollback.json (and removes what was created); it is idempotent too.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sqlite3
import sys
import time
import uuid

NEW_ADAPTER = "spark-media"
OLD_ADAPTER = "sarness"
MEDIA_PREFIXES = ("image:", "video:")
OPENJUTSU_OLD = "video:openjutsu-minimax-h3"
PROFILE_EDIT = "video:opensfield-video-edit"
ACTIVE = ("queued", "running")
BACKUP_PREFIX = "pre-update-media-engine-"


def uid(prefix: str) -> str:
    return prefix + uuid.uuid4().hex[:20]


def is_media(real_id: str) -> bool:
    return (real_id or "").startswith(MEDIA_PREFIXES)


# ---------------------------------------------------------------------------------------------------------- reading
def connect_ro(db: pathlib.Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args)]


def h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:12]


def fingerprint(con: sqlite3.Connection, orig_model_ids: list[str] | None = None) -> dict:
    """What must not change (media_*, non-media models, other backends, users ...) and what a rollback must restore exactly (everything)."""
    out = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "media_assets", "media_jobs", "models", "backends", "model_grants")}
    out["media_assets_hash"] = h(rows(con, "select * from media_assets order by id"))
    out["media_jobs_hash"] = h(rows(con, "select * from media_jobs order by id"))
    out["users_hash"] = h(rows(con, "select id,email,status,password_hash from users order by id"))
    out["nonmedia_models_hash"] = h([r for r in rows(con, "select * from models order by id") if not is_media(r["real_id"])])
    out["other_backends_hash"] = h(rows(con, "select * from backends where adapter != ? order by id", (NEW_ADAPTER,)))
    out["all_models_hash"] = h(rows(con, "select * from models order by id"))
    out["all_backends_hash"] = h(rows(con, "select * from backends order by id"))
    out["all_grants_hash"] = h(rows(con, "select * from model_grants order by id"))
    if orig_model_ids is not None:
        q = ",".join("?" * len(orig_model_ids)) or "''"
        out["orig_grants_hash"] = h(rows(con, f"select * from model_grants where model_id in ({q}) order by id", orig_model_ids))
        out["orig_models_identity_hash"] = h(rows(con, f"select id,alias,enabled,pinned,tags,sort,hidden,defaults,system_prompt,profile,allow_tools,gen_limits from models where id in ({q}) order by id", orig_model_ids))
    return out


INVARIANT_KEYS = ("users", "conversations", "messages", "media_assets", "media_jobs", "media_assets_hash", "media_jobs_hash", "users_hash",
                  "nonmedia_models_hash", "other_backends_hash", "orig_grants_hash", "orig_models_identity_hash")


# ---------------------------------------------------------------------------------------------------------- the catalogue
def load_catalog(a) -> dict:
    if a.catalog_file:
        return json.loads(pathlib.Path(a.catalog_file).expanduser().read_text())
    import httpx
    tok = read_token(a)
    r = httpx.get(a.engine_url.rstrip("/") + "/v1/capabilities", headers={"Authorization": f"Bearer {tok}"}, timeout=30)
    if r.status_code != 200:
        sys.exit(f"the media engine answered HTTP {r.status_code} for /v1/capabilities")
    return r.json()


def read_token(a) -> str:
    if a.token_value_from_env:
        v = os.environ.get(a.token_value_from_env, "").strip()
    else:
        p = pathlib.Path(a.token_file).expanduser()
        try:
            v = p.read_text().strip()
        except OSError:
            v = ""
    if not v:
        sys.exit("no engine token: give --token-file (default ~/opensfield/config/semechka-media.token) or --token-value-from-env VAR")
    return v


# ---------------------------------------------------------------------------------------------------------- the plan
def make_plan(con: sqlite3.Connection, caps: dict, a) -> dict:
    """Pure read: what --apply would do. Contains the old values needed for the rollback."""
    from semechka.adapters.spark_media import discovered_from_capabilities
    from semechka.backends_svc import friendly_alias
    found = {d.real_id: d for d in discovered_from_capabilities(caps)}
    unavailable = {f"{u['kind']}:{u['id']}": u.get("reason") for u in caps.get("unavailable") or [] if isinstance(u, dict) and u.get("id")}
    base = a.engine_url.rstrip("/")
    new_b = next((b for b in rows(con, "select * from backends where adapter = ? and base_url = ?", (NEW_ADAPTER, base))), None)
    plan: dict = {"backend": {"id": new_b["id"] if new_b else uid("b_"), "created": new_b is None, "name": a.backend_name, "base_url": base},
                  "moves": [], "creates": [], "grants": [], "toggles": [], "notes": []}
    bid = plan["backend"]["id"]
    clash = next((x for x in rows(con, "select * from backends where name = ?", (a.backend_name,)) if x["id"] != bid), None)
    if clash:
        sys.exit(f"a backend named {a.backend_name!r} already exists (adapter {clash['adapter']}, {clash['base_url']}); choose another --backend-name")
    sarness = {b["id"] for b in rows(con, "select id from backends where adapter = ?", (OLD_ADAPTER,))}
    models = rows(con, "select * from models order by sort, id")
    have_new = {m["real_id"]: m for m in models if m["backend_id"] == bid}
    for m in models:
        if m["backend_id"] not in sarness or not is_media(m["real_id"]):
            continue
        d = found.get(m["real_id"])
        entry = {"id": m["id"], "real_id": m["real_id"], "alias": m["alias"], "enabled": bool(m["enabled"]), "old_backend_id": m["backend_id"],
                 "old": {"meta": m["meta"], "capabilities": m["capabilities"], "available": m["available"]}}
        if m["real_id"] in have_new:
            entry["action"] = "skip: the engine backend already has a row with this real id"
            plan["notes"].append(f"{m['real_id']}: a row already exists on the engine backend; {m['id']} left where it is")
            continue
        if d is not None:
            meta = dict(d.meta)
            if d.loaded is not None:
                meta["loaded"] = d.loaded
            entry.update(action="move + refresh (offered by the engine)", new={"meta": meta, "capabilities": list(d.capabilities), "available": 1})
        else:
            why = unavailable.get(m["real_id"]) or "not offered by the media engine"
            try:
                meta = json.loads(m["meta"]) if isinstance(m["meta"], str) else (m["meta"] or {})
            except ValueError:
                meta = {}
            meta = dict(meta) if isinstance(meta, dict) else {}
            media = dict(meta.get("media") or {})
            media["unavailable_reason"] = why
            meta["media"] = media
            entry.update(action="move + mark unavailable", reason=why, new={"meta": meta, "capabilities": None, "available": 0})
        plan["moves"].append(entry)
    moved_real = {e["real_id"] for e in plan["moves"]}
    for rid, d in found.items():
        if rid in have_new or rid in moved_real:
            continue
        plan["creates"].append({"id": uid("m_"), "real_id": rid, "alias": friendly_alias(rid), "capabilities": list(d.capabilities),
                                "meta": {**d.meta, **({"loaded": d.loaded} if d.loaded is not None else {})}, "enabled": 0})
    # profile grants
    if a.grant_profiles:
        src = next((m for m in models if m["real_id"] == OPENJUTSU_OLD), None)
        if src is None:
            plan["notes"].append(f"--grant-profiles: there is no {OPENJUTSU_OLD} row on this instance, so there is nothing to copy")
        elif PROFILE_EDIT not in found:
            plan["notes"].append(f"--grant-profiles: the engine does not offer {PROFILE_EDIT} right now")
        else:
            target = have_new.get(PROFILE_EDIT) or next((c for c in plan["creates"] if c["real_id"] == PROFILE_EDIT), None)
            tid = target["id"]
            existing = {(g["subject_type"], g["subject_id"]) for g in rows(con, "select * from model_grants where model_id = ?", (tid,))} if tid in {m["id"] for m in models} else set()
            for g in rows(con, "select * from model_grants where model_id = ? order by id", (src["id"],)):
                if (g["subject_type"], g["subject_id"]) not in existing:
                    plan["grants"].append({"model_id": tid, "subject_type": g["subject_type"], "subject_id": g["subject_id"], "effect": g["effect"], "modes": g["modes"]})
            if target in plan["creates"]:
                target["enabled"] = 1
            elif not have_new[PROFILE_EDIT]["enabled"]:
                plan["toggles"].append({"id": tid, "old_enabled": have_new[PROFILE_EDIT]["enabled"], "new_enabled": 1})
            if not plan["grants"]:
                plan["notes"].append(f"{OPENJUTSU_OLD} has no grants to copy")
    else:
        if any(c["real_id"] == PROFILE_EDIT for c in plan["creates"]):
            plan["notes"].append(f"{PROFILE_EDIT} is added disabled with no grants (use --grant-profiles to copy the old OpenJutsu grants and enable it)")
    plan["backend"]["needed"] = bool(plan["moves"] or plan["creates"])
    plan["changes"] = bool(plan["moves"] or plan["creates"] or plan["grants"] or plan["toggles"])
    if not plan["backend"]["needed"] and plan["backend"]["created"]:
        plan["backend"]["created"] = False        # nothing to attach to it: do not create an empty backend
    return plan


def print_plan(plan: dict) -> None:
    b = plan["backend"]
    print(f"engine backend: {b['name']} ({b['base_url']}) -> " + (f"CREATE {b['id']}" if b["created"] else f"existing {b['id']}" if b.get("needed") else "not needed"))
    for e in plan["moves"]:
        print(f"  {e['id']}  {e['real_id']:<34} alias={e['alias']!r:<28} enabled={int(e['enabled'])}  -> {e['action']}" + (f" ({e['reason']})" if e.get("reason") else ""))
    for c in plan["creates"]:
        print(f"  NEW {c['id']}  {c['real_id']:<34} alias={c['alias']!r:<28} enabled={c['enabled']}  -> add from the engine catalogue (no grants)")
    for g in plan["grants"]:
        print(f"  GRANT {g['subject_type']}:{g['subject_id']} {g['effect']} modes={g['modes']} -> {g['model_id']}")
    for t in plan["toggles"]:
        print(f"  ENABLE {t['id']}")
    for n in plan["notes"]:
        print("  note:", n)
    if not plan["changes"]:
        print("  nothing to do")


# ---------------------------------------------------------------------------------------------------------- applying
def _js(v) -> str:
    return json.dumps(v)


def insert_row(con, table: str, values: dict) -> None:
    cols = [r[1] for r in con.execute(f"pragma table_info({table})")]
    v = {k: x for k, x in values.items() if k in cols}
    con.execute(f"insert into {table} ({','.join(v)}) values ({','.join('?' * len(v))})", list(v.values()))


def apply_plan(con: sqlite3.Connection, plan: dict, token: str | None, data: pathlib.Path, orig_ids: list[str], before: dict) -> dict:
    from semechka.crypto import Vault, load_or_create_master_key
    now = time.time()
    b = plan["backend"]
    con.execute("begin immediate")
    try:
        if b["created"]:
            if not token:
                raise SystemExit("an engine token is required to create the backend row")
            vault = Vault(load_or_create_master_key(data / "master.key"))
            insert_row(con, "backends", {"id": b["id"], "name": b["name"], "adapter": NEW_ADAPTER, "base_url": b["base_url"], "api_key_enc": vault.encrypt(token, "backend"),
                                         "external": 0, "enabled": 1, "options": _js({}), "created_at": now})
        for e in plan["moves"]:
            n = e["new"]
            if n["capabilities"] is None:
                con.execute("update models set backend_id=?, meta=?, available=0 where id=?", (b["id"], _js(n["meta"]), e["id"]))
            else:
                con.execute("update models set backend_id=?, meta=?, capabilities=?, available=1 where id=?", (b["id"], _js(n["meta"]), _js(n["capabilities"]), e["id"]))
        for c in plan["creates"]:
            insert_row(con, "models", {"id": c["id"], "backend_id": b["id"], "real_id": c["real_id"], "alias": c["alias"], "enabled": c["enabled"], "pinned": 0, "tags": _js([]),
                                       "capabilities": _js(c["capabilities"]), "meta": _js(c["meta"]), "system_prompt": "", "gen_limits": _js({}), "allow_tools": 0, "available": 1,
                                       "profile": _js({}), "sort": 0, "hidden": 0, "defaults": _js({}), "created_at": now})
        for g in plan["grants"]:
            cur = con.execute("insert into model_grants (model_id, subject_type, subject_id, effect, modes) values (?,?,?,?,?)",
                              (g["model_id"], g["subject_type"], g["subject_id"], g["effect"], g["modes"]))
            g["row_id"] = cur.lastrowid
        for t in plan["toggles"]:
            con.execute("update models set enabled=? where id=?", (t["new_enabled"], t["id"]))
        after = fingerprint(con, orig_ids)
        bad = [k for k in INVARIANT_KEYS if before.get(k) != after.get(k)]
        if bad:
            raise SystemExit(f"invariant broken ({', '.join(bad)}); nothing was changed")
        con.execute("commit")
        return after
    except BaseException:
        if con.in_transaction:
            con.execute("rollback")
        raise


def backup_db(db: pathlib.Path, dest_dir: pathlib.Path) -> pathlib.Path:
    dest_dir.mkdir(parents=True, exist_ok=False, mode=0o700)
    out = dest_dir / "semechka.db"
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()
    os.chmod(out, 0o600)
    return out


def rollback_doc(plan: dict, data: pathlib.Path, backup: pathlib.Path) -> dict:
    return {"version": 1, "created_at": time.time(), "data_dir": str(data), "backup": str(backup),
            "backend": {"id": plan["backend"]["id"], "created": plan["backend"]["created"]},
            "moved": [{"id": e["id"], "old_backend_id": e["old_backend_id"], "old": e["old"]} for e in plan["moves"]],
            "created_models": [c["id"] for c in plan["creates"]],
            "grant_rows": [g["row_id"] for g in plan["grants"] if "row_id" in g],
            "toggled": plan["toggles"]}


def do_rollback(con: sqlite3.Connection, doc: dict) -> None:
    con.execute("begin immediate")
    try:
        for g in doc.get("grant_rows", []):
            con.execute("delete from model_grants where id=?", (g,))
        for mid in doc.get("created_models", []):
            con.execute("delete from model_grants where model_id=?", (mid,))
            con.execute("delete from models where id=?", (mid,))
        for t in doc.get("toggled", []):
            con.execute("update models set enabled=? where id=?", (t["old_enabled"], t["id"]))
        for m in doc.get("moved", []):
            o = m["old"]
            con.execute("update models set backend_id=?, meta=?, capabilities=?, available=? where id=?", (m["old_backend_id"], o["meta"], o["capabilities"], o["available"], m["id"]))
        b = doc.get("backend") or {}
        if b.get("created") and not con.execute("select 1 from models where backend_id=? limit 1", (b["id"],)).fetchone():
            con.execute("delete from backends where id=?", (b["id"],))
        con.execute("commit")
    except BaseException:
        if con.in_transaction:
            con.execute("rollback")
        raise


# ---------------------------------------------------------------------------------------------------------- main
def show(label: str, fp: dict) -> None:
    print(f"{label}: " + ", ".join(f"{k}={fp[k]}" for k in ("users", "conversations", "messages", "media_assets", "media_jobs", "models", "backends", "model_grants")))
    print(f"  hashes: " + ", ".join(f"{k.removesuffix('_hash')}={fp[k]}" for k in fp if k.endswith("_hash")))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("data_dir")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", metavar="FILE")
    ap.add_argument("--engine-url", default="http://127.0.0.1:8780")
    ap.add_argument("--token-file", default="~/opensfield/config/semechka-media.token")
    ap.add_argument("--token-value-from-env", metavar="VAR")
    ap.add_argument("--catalog-file")
    ap.add_argument("--backend-name", default="Spark media engine")
    ap.add_argument("--grant-profiles", action="store_true")
    ap.add_argument("--allow-active", action="store_true")
    a = ap.parse_args()
    data = pathlib.Path(a.data_dir).expanduser().resolve()
    db = data / "semechka.db"
    if not db.exists():
        sys.exit(f"{db} does not exist")

    if a.rollback:
        doc = json.loads(pathlib.Path(a.rollback).expanduser().read_text())
        con = sqlite3.connect(db, isolation_level=None)
        con.row_factory = sqlite3.Row
        before = fingerprint(con)
        do_rollback(con, doc)
        after = fingerprint(con)
        show("before rollback", before)
        show("after rollback ", after)
        orig = connect_ro(pathlib.Path(doc["backup"]) if pathlib.Path(doc["backup"]).exists() else db)
        want = fingerprint(orig)
        orig.close()
        same = all(want[k] == after[k] for k in want if k.startswith(("all_", "media_", "users", "nonmedia", "other_")) or k in ("conversations", "messages", "models", "backends", "model_grants"))
        print("restored to the backed-up state:", "YES" if same else "NO (compare the hashes above with the backup)")
        return 0 if same else 1

    caps = load_catalog(a)
    ro = connect_ro(db)
    orig_ids = [r["id"] for r in rows(ro, "select id from models order by id")]
    before = fingerprint(ro, orig_ids)
    plan = make_plan(ro, caps, a)
    active = rows(ro, "select id, kind, model_id, status from media_jobs where status in (?,?)", ACTIVE)
    ro.close()
    show("before", before)
    print("engine:", a.engine_url, "| catalogue:", f"{len(caps.get('models') or [])} models, {len(caps.get('profiles') or [])} profiles")
    print_plan(plan)
    if not a.apply:
        print("DRY RUN: nothing was written. Add --apply to migrate.")
        return 0
    if active and not a.allow_active:
        print("REFUSED: media jobs are still running or queued:", ", ".join(f"{j['id']} ({j['status']})" for j in active))
        return 2
    if not plan["changes"]:
        print("Nothing to change (already migrated).")
        return 0
    token = read_token(a) if plan["backend"]["created"] else None
    ts = time.strftime("%Y%m%d-%H%M%S")
    bdir = data / f"{BACKUP_PREFIX}{ts}"
    backup = backup_db(db, bdir)
    print("backup:", backup)
    rb = bdir / "rollback.json"
    rb.write_text(json.dumps(rollback_doc(plan, data, backup), indent=1))      # written first: a crash mid-way still leaves the way back (the backup copy is also there)
    os.chmod(rb, 0o600)
    con = sqlite3.connect(db, isolation_level=None, timeout=30)
    con.row_factory = sqlite3.Row
    after = apply_plan(con, plan, token, data, orig_ids, before)
    con.close()
    rb.write_text(json.dumps(rollback_doc(plan, data, backup), indent=1))      # again, now with the ids of the grant rows that were inserted
    show("after ", after)
    print("invariants hold:", ", ".join(INVARIANT_KEYS))
    print("rollback file:", rb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
