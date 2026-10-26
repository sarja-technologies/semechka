#!/usr/bin/env python3
"""Operator-side rollout of the cloud features, for the person who has shell access to the host (like `semechka org purge`).

    ~/semechka-deploy/roasted/app/venv/bin/python -I rollout_local.py --data ~/semechka-deploy/roasted/data \
        --backup <backup dir> [--expect-id m_...] [--dry-run] [--report FILE]

The trust is the one the host already gives: whoever can read the data directory and run the installed program is the operator.
There is no sign-in, no session and no account involved, and no password is read, stored or printed. Settings are written through
the same audited settings service the admin screens use (actor "operator"), the audit chain is appended under SQLite's write lock,
and the running service picks the settings up from the database. Nothing here changes an account, a conversation or a setting
outside cloud.*.

Order (each step read back; the first two are never undone): cloud.rollout=admins, cloud.agents.models=[the 35B model],
cloud.workspaces.enabled, cloud.agents.enabled. The sandbox switch must be off. If a critical check fails afterwards the two
switches are turned off again and the fence stays. Smoke tests: live HTTP (signed-out callers), the fence for every real account
by role, model enforcement, and a real pilot (organisation, workspace, file, context, agent run executed by the live service)."""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.request

RESULTS: list[tuple[str, bool, bool, str]] = []
CLOUD = ("cloud.rollout", "cloud.agents.models", "cloud.workspaces.enabled", "cloud.agents.enabled")


def check(name: str, ok: bool, seen: object = "", critical: bool = True) -> bool:
    RESULTS.append((name, bool(ok), critical, str(seen)[:200]))
    print(f"  {'PASS' if ok else 'FAIL'}{'' if critical or ok else ' (non-critical)'}  {name}" + (f"   [{str(seen)[:150]}]" if seen != "" else ""), flush=True)
    return bool(ok)


def ro(path: pathlib.Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def snapshot(db: pathlib.Path) -> dict:
    con = ro(db)
    n = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations", "messages", "models", "roles", "ws_files", "agents")}
    users = hashlib.sha256(json.dumps(con.execute("select id,email,status,password_hash,totp_enabled from users order by id").fetchall()).encode()).hexdigest()
    other = {k: v for k, v in con.execute("select key,value from setting_values where key not like 'cloud.%'")}
    brand = con.execute("select value from setting_values where key='brand.tab_title'").fetchone()
    con.close()
    return {"n": n, "users": users, "other_settings": other, "brand": brand[0] if brand else None}


def verify_backup(backup: pathlib.Path, data: pathlib.Path, max_age_h: float) -> str:
    """'' when the backup can be relied on, else why not."""
    if not (backup / "semechka.db").exists():
        return "no database in the backup directory"
    age = (time.time() - (backup / "semechka.db").stat().st_mtime) / 3600
    if age > max_age_h:
        return f"the backup is {age:.1f} h old (limit {max_age_h} h)"
    con = ro(backup / "semechka.db")
    if con.execute("pragma integrity_check").fetchone()[0] != "ok":
        return "the backup fails its integrity check"
    b = {t: con.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations")}
    con.close()
    live = ro(data / "semechka.db")
    l_ = {t: live.execute(f"select count(*) from {t}").fetchone()[0] for t in ("users", "conversations")}
    live.close()
    if b["users"] != l_["users"] or b["conversations"] > l_["conversations"]:
        return f"the backup does not match the live data ({b} vs {l_})"
    if hashlib.sha256((backup / "master.key").read_bytes()).digest() != hashlib.sha256((data / "master.key").read_bytes()).digest():
        return "the backup's master key differs from the live key"
    return ""


def engine_media_jobs(data: pathlib.Path) -> int:
    """Active image/video jobs of EVERY instance on this host: they all share one inference engine and one GPU memory pool, so
    a test run on staging must wait for production's video job just as a production run must."""
    n = 0
    for db in {data / "semechka.db", *data.parent.parent.glob("*/data/semechka.db")}:
        try:
            con = ro(db)
            n += con.execute("select count(*) from media_jobs where status in ('queued','running')").fetchone()[0]
            con.close()
        except sqlite3.Error:
            n += 0
    return n


def http_code(url: str) -> int:
    try:
        return urllib.request.urlopen(urllib.request.Request(url), timeout=20).status
    except urllib.error.HTTPError as e:
        return e.code


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--backup", required=True)
    ap.add_argument("--url", default="http://192.168.20.193:8787")
    ap.add_argument("--model", default="qwen3.6-35b-a3b@MXFP4_MOE")
    ap.add_argument("--expect-id", default="")
    ap.add_argument("--max-backup-age-h", type=float, default=24)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report")
    a = ap.parse_args()
    data, backup = pathlib.Path(a.data).expanduser(), pathlib.Path(a.backup).expanduser()

    from semechka import (
        agent_svc,
        audit,
        files_svc,
        gateway_svc,
        jobs_svc,
        services,
        tenancy_svc,
    )
    from semechka import contexts_svc as C
    from semechka.authz import Authz
    from semechka.config import Config
    from semechka.models import Model, User
    from semechka.models_agents import AgentRun, AgentStep
    from semechka.models_tenancy import Workspace
    from semechka.state import AppState
    from semechka.tenancy_svc import TenancyError
    from sqlalchemy import select

    print("== 1. recovery backup")
    why = verify_backup(backup, data, a.max_backup_age_h)
    if not check("the latest production backup is intact, recent, matches live and carries the live key", not why, why or backup.name):
        print("STOPPED before any change.")
        return 1
    before = snapshot(data / "semechka.db")
    print(f"   before: {before['n']}")

    cfg = Config(data_dir=data)
    cfg.background = False  # this process runs no pumps: the live service executes the jobs
    st = AppState.build(cfg)

    def read(key):
        with st.db.session() as s:
            return st.settings.deployment(s, key)

    def setv(key, value):
        with st.db.session(write=True) as s:
            st.settings.set(s, "operator", key, value, ip="local")
        got = read(key)
        if got != value:
            raise RuntimeError(f"{key} read back as {got!r}, expected {value!r}")
        print(f"   set {key} = {value!r} (read back ok)", flush=True)

    print("== 2. preconditions")
    with st.db.session() as s:
        models = s.execute(select(Model).where(Model.enabled == True, Model.available == True)).scalars().all()
        hits = [m for m in models if a.model in (m.alias, m.real_id, (m.real_id or "").split(":", 1)[-1])]
        owner = s.execute(select(User).where(User.status == "active")).scalars().all()
        owners = [u for u in owner if "owner" in [r.role_id for r in u.roles]]
    if not check("exactly one enabled, available model matches the 35B agent model", len(hits) == 1, [(m.id, m.alias) for m in hits]):
        return 1
    mid = hits[0].id
    if a.expect_id and not check("its id is the expected one", mid == a.expect_id, mid):
        return 1
    if not check("sandbox execution is off", not read("sandbox.enabled")):
        return 1
    if not check("an owner account exists to hold the pilot", len(owners) >= 1, [u.email for u in owners]):
        return 1
    owner_id = owners[0].id
    print(f"   current cloud settings: { {k: read(k) for k in CLOUD} }")
    if a.dry_run:
        print("DRY RUN: would set, in this order and reading each back: cloud.rollout='admins'; "
              f"cloud.agents.models=['{mid}']; cloud.workspaces.enabled=True; cloud.agents.enabled=True; then smoke-test. Nothing was changed.")
        return 0

    def rollback_flags():
        for k in ("cloud.agents.enabled", "cloud.workspaces.enabled"):
            try:
                setv(k, False)
            except Exception as e:  # noqa: BLE001
                print(f"   WARNING: {e}")

    print("== 3. apply (fence first)")
    try:
        setv("cloud.rollout", "admins")
        setv("cloud.agents.models", [mid])
        setv("cloud.workspaces.enabled", True)
        if read("cloud.rollout") != "admins" or read("cloud.agents.models") != [mid]:
            raise RuntimeError("the fence or the model list changed under us; agents were NOT enabled")
        setv("cloud.agents.enabled", True)
    except Exception as e:  # noqa: BLE001
        print(f"STOPPED: {e}\nThe fence, if it was set, is still on; agents are off unless listed above.")
        rollback_flags()
        return 1
    with st.db.session(write=True) as s:
        audit.record(s, "operator", "rollout.operator", "cloud", {"model": mid, "fence": "admins"})

    print("== 4. smoke tests")
    try:
        check("flags: fence on, workspaces on, agents on, sandbox off, only the 35B model for agents",
              read("cloud.rollout") == "admins" and read("cloud.workspaces.enabled") is True and read("cloud.agents.enabled") is True
              and not read("sandbox.enabled") and read("cloud.agents.models") == [mid])
        check("the live service answers", http_code(f"{a.url}/api/health") == 200)
        codes = [http_code(f"{a.url}{p}") for p in ("/api/orgs", "/api/workspaces/w_x/files", "/api/admin/cloud/status")]
        check("signed-out callers get 401 on the cloud endpoints", codes == [401, 401, 401], codes)
        with st.db.session() as s:
            who = {u.email: (services.cloud_open_to(st, s, services.load_principal(s, u)), [r.role_id for r in u.roles])
                   for u in s.execute(select(User).where(User.status == "active")).scalars()}
            fence_ok = all(open_ == bool({"owner", "platform_admin"} & set(roles)) for open_, roles in who.values())
        check("the fence: owner and platform-admin roles pass, every other account is refused (judged by role; no account is used)", fence_ok,
              {e.split("@")[0]: v[0] for e, v in who.items()})
        with st.db.session() as s:
            owner_p = services.load_principal(s, s.get(User, owner_id))
            only = agent_svc.agent_models(st, s)
            offered = [m["id"] for m in gateway_svc.models_for(st, s, owner_p, None, None, "chat", only=only or None)]
            others = [m.id for m in models if m.id != mid]
        check("agents are offered exactly the 35B model", offered == [mid], offered)
        try:
            with st.db.session() as s:
                agent_svc._check_agent_model(st, s, others[0])
            refused = False
        except TenancyError:
            refused = True
        check("an agent pinned to any other model is refused", refused)

        print("   -- pilot (organisation, workspace, file, context, agent run by the live service)")
        tag = time.strftime("%Y%m%d-%H%M%S")
        with st.db.session(write=True) as s:
            org = tenancy_svc.create_org(s, "operator", "CMSIT Pilot", owner_id=owner_id)
            oid = org.id
        with st.db.session(write=True) as s:
            az = Authz(s, owner_id)
            ws = tenancy_svc.create_workspace(s, az, oid, "Pilot " + tag)
            wid = ws.id
            doc = (f"# Pilot notes {tag}\nMonthly figures: January 4120, February 3875, March 5105.\n").encode()
            f, _v = files_svc.create_file(st, s, az, wid, "pilot-notes.md", doc)
            ctx = C.create_context(s, az, wid, "Pilot")
            C.add_item(s, az, ctx.id, f.id)
            fid = f.id
        check("workspace, file and context created", True, f"{oid} / {wid}")
        end = time.time() + 300
        while time.time() < end:
            with st.db.session() as s:
                js = s.execute(select(jobs_svc.Job).where(jobs_svc.Job.workspace_id == wid)).scalars().all()
                done = bool(js) and all(j.status in ("succeeded", "failed", "cancelled") for j in js)
            if done:
                break
            time.sleep(3)
        check("the live service ingested the file", done)
        with st.db.session() as s:
            owner_az = Authz(s, owner_id)
            res = C.retrieve(st, s, owner_az, "monthly figures January February March", workspace_id=wid)
            hit = any("4120" in h.text for h in res.hits)
        check("retrieval returns the uploaded text", hit)

        busy = ""
        for _ in range(40):  # never start the 35B model while anyone's image/video work is on the shared engine
            n = engine_media_jobs(data)
            busy = f"{n} image/video job(s) active on the shared engine" if n else ""
            if not busy:
                break
            print(f"   waiting: {busy}", flush=True)
            time.sleep(30)
        if not check("the engine is free of other people's image/video work", not busy, busy):
            raise RuntimeError("engine busy; agent run not started")
        with st.db.session(write=True) as s:
            az = Authz(s, owner_id)
            ag = agent_svc.create_agent(st, s, az, wid, "Pilot reader " + tag, instructions="Use the tools. Never invent numbers. Report exact figures.",
                                        tools=["search_context", "read_file", "list_files", "calc", "create_document"], approval="sensitive", context_ids=["*"])
            run = agent_svc.start_run(st, s, az, ag.id, "Read pilot-notes.md and tell me the total of the three monthly figures. Use the calculator tool for the sum.",
                                      services.load_principal(s, s.get(User, owner_id)))
            rid, jid = run.id, run.job_id
        end, status = time.time() + 1200, ""
        while time.time() < end:
            with st.db.session() as s:
                status = s.get(jobs_svc.Job, jid).status
            if status in ("succeeded", "failed", "cancelled", "awaiting_approval"):
                break
            time.sleep(5)
        with st.db.session() as s:
            r = s.get(AgentRun, rid)
            final = st.vault.decrypt(r.final_enc, f"org:{r.org_id}:content") if r.final_enc else ""
            used = (r.usage or {}).get("model")
            tools = [x.tool for x in s.execute(select(AgentStep).where(AgentStep.run_id == rid, AgentStep.kind == "tool")).scalars()]
        check("the agent run succeeded", status == "succeeded", status)
        check("the answer carries the correct total 13100", "13100" in final.replace(",", "").replace(" ", ""), final[:120])
        check("the run used the 35B model and nothing else", used == mid, used)
        check("the run used its tools", bool(tools), tools)

        with st.db.session(write=True) as s:
            tenancy_svc.update_workspace(s, Authz(s, owner_id), wid, policy={"file_access": "owner"})
        with st.db.session() as s:
            nodes = files_svc.nodes_for_file(s, files_svc.get_file(s, Authz(s, owner_id), fid)[0])
            mine = Authz(s, owner_id).check("file.read", s.get(Workspace, wid), nodes).allowed
        check("owner-only rule on the pilot workspace: the owner still reads their own file", mine)

        with st.db.session() as s:
            chain = audit.verify_chain(s)
        check("the audit hash chain verifies", chain.get("ok") is True, chain)
        after = snapshot(data / "semechka.db")
        check("accounts untouched (users table checksum identical)", after["users"] == before["users"])
        check("conversations and messages preserved (none lost)", after["n"]["conversations"] >= before["n"]["conversations"] and after["n"]["messages"] >= before["n"]["messages"],
              (before["n"]["conversations"], after["n"]["conversations"], before["n"]["messages"], after["n"]["messages"]))
        check("every setting outside cloud.* is unchanged, branding included", after["other_settings"] == before["other_settings"] and after["brand"] == before["brand"])
        check("models and roles unchanged", after["n"]["models"] == before["n"]["models"] and after["n"]["roles"] == before["n"]["roles"])
        ok = all(x[1] for x in RESULTS if x[2])
    except Exception as e:  # noqa: BLE001
        check("smoke tests ran to the end", False, repr(e)[:180])
        ok = False
    if not ok:
        print("\nA CRITICAL CHECK FAILED - turning agents and workspaces back off (fence and model list stay; nothing is deleted).")
        rollback_flags()
    print(f"\n{sum(1 for x in RESULTS if x[1])}/{len(RESULTS)} checks passed; final state: { {k: read(k) for k in CLOUD} }")
    if a.report:
        pathlib.Path(a.report).write_text("".join(f"{'PASS' if o else 'FAIL'}{'' if c else ' (non-critical)'}  {n}  [{seen}]\n" for n, o, c, seen in RESULTS))
    st.db.engine.dispose()
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
