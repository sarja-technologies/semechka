#!/usr/bin/env python3
"""Move one Semechka instance's chat models (and embeddings) from the Sarness Engine to the Spark's llama.cpp router.

    migrate_to_llamacpp.py <instance-data-dir> [--apply] [--rollback FILE]
        [--router-url http://127.0.0.1:8080/v1] [--api-key-file ~/spark-llm/api.key] [--spark-llm-dir ~/spark-llm]
        [--embedding-model qwen3-embedding-0.6b | --no-embeddings] [--name "Spark LLM router"]

Dry-run by default: it prints exactly what it would change and writes nothing (not even a backup). With --apply it
  1. takes a consistent backup of semechka.db (SQLite online backup, written next to the database, mode 0600),
  2. adds a `llamacpp` backend row for the router. Its key is read from --api-key-file INSIDE this script, encrypted with the
     instance's own master key (purpose "backend", like every backend key) and never printed or written anywhere else,
  3. moves every chat model that lives on the old engine as `local:<engine id>` onto the new backend and rewrites its `real_id`
     to the router id (`local:qwen3.8-27b@UD-Q6_K` -> `qwen3.8-27b`, from `legacy_id` in the router's models.json). The model ROW
     is kept: id, alias, enabled, pinned, sort, hidden, tags, system prompt, gen limits, defaults, profile, grants, group
     membership. So conversations, messages, usage events, agents and settings that name the model keep working. Only the fields
     the router is the authority on are refreshed: `capabilities`, `meta`, `available`,
  4. points `embeddings.url` / `embeddings.model` at the router's embedding model (same model name as the old embed service, same
     1024 dimensions, so stored vectors stay valid),
  5. writes <data-dir>/migrate-llamacpp-<time>.rollback.json with every ORIGINAL value it changed.
Image/video models (`image:` / `video:`) and remote endpoint models (`endpoint:`) are left exactly as they are (the media
workstream owns them); so is every model the router has no equivalent for (reported). Running it twice changes nothing the second
time. --rollback FILE puts every original value back (after a backup), removes the new backend row when nothing else uses it.

Stop the Semechka service of the instance first (SQLite is safe against a running one, but the settings cache and the model lists
of a running process only notice the change at restart). The router key and any password stay out of the output.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

DEFAULT_ROUTER = "http://127.0.0.1:8080/v1"
LLM_PREFIX = "local:"
MIGRATED_FIELDS = ("backend_id", "real_id", "capabilities", "meta", "available")
SETTING_KEYS = ("embeddings.url", "embeddings.model")


def die(msg: str, code: int = 2):
    print("ERROR:", msg, file=sys.stderr)
    sys.exit(code)


def ro(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    return con


def backup(db: Path, dest: Path) -> str:
    """A consistent copy through SQLite's own online backup (includes anything still in the WAL)."""
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    os.chmod(dest, 0o600)
    return hashlib.sha256(dest.read_bytes()).hexdigest()


def j(v):
    return json.loads(v) if isinstance(v, str) else v


def build_plan(db: Path, a) -> dict:
    """What would change. Reads the database only."""
    from semechka.adapters.llamacpp import LlamaCppAdapter, legacy_map, models_table, verified_table

    opts = {"spark_llm_dir": str(a.spark_llm_dir)} if a.spark_llm_dir else {}
    legacy, table = legacy_map(opts), models_table(opts)
    if not table:
        die(f"no models.json found in {a.spark_llm_dir or '~/spark-llm'}: cannot map the old model ids (use --spark-llm-dir)")
    router_ids = set(table)
    ad = LlamaCppAdapter(a.router_url, None, opts)
    con = ro(db)
    backends = [dict(r) for r in con.execute("select * from backends")]
    existing = next((b for b in backends if b["adapter"] == "llamacpp" and b["base_url"].rstrip("/") == a.router_url.rstrip("/")), None)
    models = [dict(r) for r in con.execute("select * from models order by backend_id, sort, alias")]
    items, untouched, unmapped = [], [], []
    for m in models:
        rid = m["real_id"]
        if not rid.startswith(LLM_PREFIX):
            if existing and m["backend_id"] == existing["id"]:
                untouched.append({"id": m["id"], "real_id": rid, "why": "already on the router"})
            else:
                untouched.append({"id": m["id"], "real_id": rid, "why": "not a local chat model (left as is)"})
            continue
        eid = rid[len(LLM_PREFIX):]
        new = legacy.get(eid) or (eid.split("@", 1)[0] if eid.split("@", 1)[0] in router_ids else None)
        if new is None:
            unmapped.append({"id": m["id"], "alias": m["alias"], "real_id": rid})
            continue
        e = table[new]
        d = ad.describe({"id": new, "status": {"value": "unloaded"},
                         "architecture": {"input_modalities": ["text"] + (["image"] if e.get("vision") else [])}},
                        table, verified_table(opts))
        meta = dict(d.meta)
        old_meta = j(m["meta"]) or {}
        if old_meta.get("manual"):
            meta["manual"] = True
        meta["loaded"] = False
        items.append({"id": m["id"], "alias": m["alias"], "enabled": bool(m["enabled"]), "from_backend": m["backend_id"],
                      "old": {k: j(m[k]) if k in ("capabilities", "meta") else m[k] for k in MIGRATED_FIELDS},
                      "new": {"real_id": new, "capabilities": d.capabilities, "meta": meta, "available": True}})
    # embeddings
    settings = {}
    if a.embeddings:
        want = {"embeddings.url": a.router_url.rstrip("/").removesuffix("/v1"), "embeddings.model": a.embedding_model}
        for k in SETTING_KEYS:
            row = con.execute("select * from setting_values where key=? and scope_type='deployment' and scope_id=''", (k,)).fetchone()
            old = dict(row) if row else None
            if old is not None:
                old["value"] = j(old["value"])
            cur = (old or {}).get("value", {}).get("v") if old else None
            if cur != want[k]:
                settings[k] = {"old_row": old, "new": want[k]}
    con.close()
    return {"existing_backend": existing, "items": items, "untouched": untouched, "unmapped": unmapped, "settings": settings,
            "old_backends": sorted({i["from_backend"] for i in items})}


def show(plan: dict, a):
    print(f"router: {a.router_url}   key file: {a.api_key_file}")
    print("backend:", ("reuse existing llamacpp backend " + plan["existing_backend"]["id"]) if plan["existing_backend"]
          else f"create a llamacpp backend '{a.name}'")
    print(f"models to move: {len(plan['items'])}")
    for i in plan["items"]:
        o, n = i["old"], i["new"]
        capchg = "" if o["capabilities"] == n["capabilities"] else f"  caps {o['capabilities']} -> {n['capabilities']}"
        print(f"  {i['id']}  {i['alias']!r:42} {'on ' if i['enabled'] else 'off'}  {o['real_id']}  ->  {n['real_id']}{capchg}")
    if plan["unmapped"]:
        print(f"models with NO router equivalent (left on the old engine): {len(plan['unmapped'])}")
        for u in plan["unmapped"]:
            print("  ", u["id"], u["alias"], u["real_id"])
    print(f"left untouched: {len(plan['untouched'])} (image/video/endpoint models, or already on the router)")
    for k, v in plan["settings"].items():
        print(f"setting {k}: {((v['old_row'] or {}).get('value') or {}).get('v')!r} -> {v['new']!r}")
    if not plan["settings"] and a.embeddings:
        print("embedding settings already point at the router")


def apply_plan(data: Path, plan: dict, a) -> int:
    from semechka import audit
    from semechka.crypto import Vault, load_or_create_master_key
    from semechka.db import Database
    from semechka.models import Backend, Model, SettingValue
    from semechka.settings_svc import SettingsService

    if not plan["items"] and not plan["settings"] and plan["existing_backend"]:
        print("Nothing to do: this instance is already on the router.")
        return 0
    if not plan["items"] and not plan["settings"]:
        print("Nothing to move.")
        return 0
    db_path = data / "semechka.db"
    ts = time.strftime("%Y%m%d-%H%M%S")
    bak = data / f"semechka.db.pre-llamacpp-{ts}"
    sha = backup(db_path, bak)
    print(f"backup: {bak.name} sha256 {sha[:16]}...")
    key_path = Path(a.api_key_file).expanduser()
    try:
        key = key_path.read_text().strip()
    except OSError as e:
        die(f"cannot read the router key file {key_path}: {e.strerror}")
    if not key:
        die("the router key file is empty")
    vault = Vault(load_or_create_master_key(data / "master.key"))
    db = Database(f"sqlite:///{db_path}")
    settings = SettingsService(db, vault)
    rb = {"version": 1, "created_at": time.time(), "data_dir": str(data.resolve()), "backup": bak.name, "backup_sha256": sha,
          "router_url": a.router_url, "created_backend": None, "models": [], "settings": []}
    with db.session(write=True) as s:
        b = s.get(Backend, plan["existing_backend"]["id"]) if plan["existing_backend"] else None
        if b is None:
            b = Backend(name=a.name, adapter="llamacpp", base_url=a.router_url.rstrip("/"), api_key_enc=vault.encrypt(key, "backend"),
                        external=False, enabled=True, options={"spark_llm_dir": str(a.spark_llm_dir)} if a.spark_llm_dir else {})
            s.add(b)
            s.flush()
            rb["created_backend"] = b.id
        key = None
        for it in plan["items"]:
            m = s.get(Model, it["id"])
            if m is None or m.real_id != it["old"]["real_id"] or m.backend_id != it["old"]["backend_id"]:
                die(f"model {it['id']} changed while planning; run again")
            rb["models"].append({"id": m.id, "old": it["old"]})
            m.backend_id, m.real_id = b.id, it["new"]["real_id"]
            m.capabilities, m.meta, m.available = it["new"]["capabilities"], it["new"]["meta"], True
        for k, v in plan["settings"].items():
            rb["settings"].append({"key": k, "old_row": v["old_row"]})
        audit.record(s, "operator", "backend.migrate_llamacpp", b.id,
                     {"models": len(plan["items"]), "settings": list(plan["settings"]), "backup": bak.name}, "local")
    for k, v in plan["settings"].items():
        with db.session(write=True) as s:
            settings.set(s, "operator", k, v["new"], ip="local")
    rbf = data / f"migrate-llamacpp-{ts}.rollback.json"
    fd = os.open(rbf, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(rb, f, indent=1, default=str)
    print(f"moved {len(plan['items'])} models; rollback file: {rbf}")
    return 0


def rollback(data: Path, rbf: Path) -> int:
    from semechka import audit
    from semechka.db import Database
    from semechka.models import Backend, Model, SettingValue
    from sqlalchemy import func, select

    rb = json.loads(rbf.read_text())
    if Path(rb["data_dir"]).resolve() != data.resolve():
        die(f"this rollback file belongs to {rb['data_dir']}, not {data}")
    db_path = data / "semechka.db"
    bak = data / f"semechka.db.pre-rollback-{time.strftime('%Y%m%d-%H%M%S')}"
    print(f"backup: {bak.name} sha256 {backup(db_path, bak)[:16]}...")
    db = Database(f"sqlite:///{db_path}")
    restored = 0
    with db.session(write=True) as s:
        for e in rb["models"]:
            m = s.get(Model, e["id"])
            if m is None:
                print("  model", e["id"], "no longer exists; skipped")
                continue
            o = e["old"]
            if m.backend_id == o["backend_id"] and m.real_id == o["real_id"]:
                continue
            m.backend_id, m.real_id, m.capabilities, m.meta, m.available = (o["backend_id"], o["real_id"], o["capabilities"], o["meta"],
                                                                            o["available"])
            restored += 1
        s.flush()
        for e in rb["settings"]:
            row = s.get(SettingValue, (e["key"], "deployment", ""))
            old = e["old_row"]
            if old is None:
                if row is not None:
                    s.delete(row)
            else:
                if row is None:
                    row = SettingValue(key=e["key"], scope_type="deployment", scope_id="", value=old["value"])
                    s.add(row)
                row.value, row.updated_at, row.updated_by = old["value"], old["updated_at"], old["updated_by"]
        cb = rb.get("created_backend")
        if cb:
            left = s.execute(select(func.count()).select_from(Model).where(Model.backend_id == cb)).scalar_one()
            b = s.get(Backend, cb)
            if b is not None and left == 0:
                s.delete(b)
                print("removed the router backend row")
            elif b is not None:
                print(f"kept the router backend row: {left} model rows still use it (added by a later sync)")
        audit.record(s, "operator", "backend.rollback_llamacpp", cb or "", {"models": restored, "file": rbf.name}, "local")
    print(f"restored {restored} models and {len(rb['settings'])} setting rows. Restart the service.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("data_dir")
    ap.add_argument("--apply", action="store_true", help="make the change (default: only show it)")
    ap.add_argument("--rollback", metavar="FILE", help="undo a migration with the rollback file it wrote")
    ap.add_argument("--router-url", default=os.environ.get("SEMECHKA_ROUTER_URL", DEFAULT_ROUTER))
    ap.add_argument("--api-key-file", default=os.environ.get("SEMECHKA_ROUTER_KEY_FILE", "~/spark-llm/api.key"))
    ap.add_argument("--spark-llm-dir", default=os.environ.get("SEMECHKA_SPARK_LLM_DIR"), help="holds models.json / models.verified.json")
    ap.add_argument("--embedding-model", default="qwen3-embedding-0.6b")
    ap.add_argument("--no-embeddings", dest="embeddings", action="store_false")
    ap.add_argument("--name", default="Spark LLM router")
    a = ap.parse_args()
    data = Path(a.data_dir).expanduser()
    if not (data / "semechka.db").is_file() or not (data / "master.key").is_file():
        die(f"{data} has no semechka.db and master.key")
    if a.rollback:
        return rollback(data, Path(a.rollback))
    a.spark_llm_dir = Path(a.spark_llm_dir).expanduser() if a.spark_llm_dir else None
    plan = build_plan(data / "semechka.db", a)
    show(plan, a)
    if not a.apply:
        print("\nDRY RUN: nothing was changed. Add --apply to do it.")
        return 0
    return apply_plan(data, plan, a)


if __name__ == "__main__":
    sys.exit(main())
