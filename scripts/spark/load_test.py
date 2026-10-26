#!/usr/bin/env python3
"""Load test against the STAGING instance, through the real HTTP API: many users, many workspaces, many contexts.

    .venv/bin/python scripts/spark/load_test.py [--users 100] [--files 3] [--workers 16] [--queries 4]

One organisation, USERS people, each with their own workspace, FILES documents (each carrying a unique sentinel sentence) and
CONTEXTS contexts. It measures upload, ingestion (until every file is searchable), and concurrent retrieval, and checks
while loaded that every answer comes from the caller's own workspace and never contains anyone else's sentinel.
Passwords are random per run, held in memory only, and the accounts are deleted afterwards when the API allows it."""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import secrets
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_scenarios import DOMAIN, Client, credentials  # noqa: E402  (shared HTTP client and credential reader)


def pct(xs: list[float], p: float) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else 0.0


def doc(user: int, n: int) -> bytes:
    topics = ["supplier contract renewal", "quarterly safety audit", "customer escalation handling", "warehouse stock count",
              "travel expense approval", "vendor onboarding checklist"]
    t = topics[(user + n) % len(topics)]
    return (f"# Note {n} of team {user}\n\nTeam {user} document {n} covers the {t}. SENTINEL-U{user:03d}-D{n} is unique to this file.\n"
            + ("Routine background text about processes, owners and deadlines. " * 60)).encode()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=100)
    ap.add_argument("--files", type=int, default=3)
    ap.add_argument("--contexts", type=int, default=3)
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--queries", type=int, default=4)
    a = ap.parse_args()
    root = Client(f"owner@{DOMAIN}", credentials()[f"owner@{DOMAIN}"])
    run_id = secrets.token_hex(3)
    pw = secrets.token_urlsafe(18)
    t0 = time.time()
    ids: list[str] = []
    print(f"run {run_id}: creating {a.users} accounts", flush=True)
    for i in range(a.users):
        email = f"load{run_id}-{i:03d}@{DOMAIN}"
        code, r = root.post("/api/admin/users", {"email": email, "display_name": f"Load {i}", "password": pw, "role_id": "member", "team_ids": []})
        assert code == 200, (code, r)
        ids.append(r["id"])
    code, org = root.post("/api/orgs", {"name": f"LoadCo {run_id}", "owner_email": f"load{run_id}-000@{DOMAIN}"})
    assert code == 200, (code, org)
    oid = org["id"]
    boss = Client(f"load{run_id}-000@{DOMAIN}", pw)
    for i in range(1, a.users):
        boss.post(f"/api/orgs/{oid}/members", {"email": f"load{run_id}-{i:03d}@{DOMAIN}", "roles": ["dept_admin"]})
    print(f"  accounts and organisation ready in {time.time() - t0:.0f}s", flush=True)

    def setup(i: int):
        c = Client(f"load{run_id}-{i:03d}@{DOMAIN}", pw)
        code, w = c.post(f"/api/orgs/{oid}/workspaces", {"name": f"Team {i}"})
        assert code == 200, (code, w)
        t = time.time()
        fids = []
        for n in range(a.files):
            code, f = c.upload(w["id"], f"note-{n}.md", doc(i, n))
            assert code == 200, (code, f)
            fids.append(f["id"])
        up = time.time() - t
        for k in range(a.contexts):
            code, ctx = c.post(f"/api/workspaces/{w['id']}/contexts", {"name": f"Context {k}"})
            assert code == 200, (code, ctx)
            for fid in fids[k % len(fids):][:2] or fids:
                c.put(f"/api/contexts/{ctx['id']}/items/{fid}")
        return i, c, w["id"], fids, up

    t1 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        users = list(ex.map(setup, range(a.users)))
    t_setup = time.time() - t1
    n_files, n_ctx = a.users * a.files, a.users * a.contexts
    ups = [u[4] for u in users]
    print(f"upload+contexts: {a.users} workspaces, {n_files} files, {n_ctx} contexts in {t_setup:.1f}s "
          f"(per user: upload p50 {statistics.median(ups):.2f}s p95 {pct(ups, .95):.2f}s)", flush=True)

    # ingestion: wait until every file of every workspace is searchable
    t2 = time.time()
    lat_ready: dict[int, float] = {}
    pending = set(range(a.users))
    while pending and time.time() - t2 < 1800:
        def probe(i):
            c, wid = users[i][1], users[i][2]
            code, jobs = c.get(f"/api/workspaces/{wid}/jobs")
            return i, code == 200 and len(jobs) >= a.files and all(j["status"] in ("succeeded", "failed") for j in jobs)
        with cf.ThreadPoolExecutor(a.workers) as ex:
            for i, done in ex.map(probe, sorted(pending)):
                if done:
                    lat_ready[i] = time.time() - t2
                    pending.discard(i)
        time.sleep(2)
    t_ingest = time.time() - t2
    ready = list(lat_ready.values())
    print(f"ingestion: {len(lat_ready)}/{a.users} workspaces fully searchable after {t_ingest:.1f}s "
          f"({n_files / max(t_ingest, 0.001):.1f} files/s; per-workspace p50 {statistics.median(ready) if ready else 0:.1f}s p95 {pct(ready, .95):.1f}s)", flush=True)

    # concurrent retrieval with a leak check
    def query(args):
        i, n = args
        c, wid = users[i][1], users[i][2]
        t = time.time()
        code, r = c.post(f"/api/workspaces/{wid}/retrieve", {"query": f"SENTINEL-U{i:03d}-D{n % a.files} {['supplier contract', 'safety audit', 'escalation', 'stock count'][n % 4]}"})
        dt = time.time() - t
        text = " ".join(p["text"] for p in r.get("passages", [])) if code == 200 else ""
        own = f"SENTINEL-U{i:03d}-D" in text
        foreign = any(f"SENTINEL-U{j:03d}-D" in text for j in range(a.users) if j != i)
        return dt, code, own, foreign, r.get("mode")

    jobs = [(i, n) for i in range(a.users) for n in range(a.queries)]
    t3 = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(query, jobs))
    t_q = time.time() - t3
    lat = [r[0] for r in res]
    bad_code = sum(1 for r in res if r[1] != 200)
    leaks = sum(1 for r in res if r[3])
    found = sum(1 for r in res if r[2])
    modes = {m: sum(1 for r in res if r[4] == m) for m in {r[4] for r in res}}
    print(f"retrieval: {len(res)} queries at concurrency {a.workers} in {t_q:.1f}s ({len(res) / t_q:.1f} q/s); "
          f"latency p50 {statistics.median(lat) * 1000:.0f} ms p95 {pct(lat, .95) * 1000:.0f} ms p99 {pct(lat, .99) * 1000:.0f} ms; "
          f"own-document found {found}/{len(res)}; errors {bad_code}; CROSS-WORKSPACE LEAKS {leaks}; modes {modes}", flush=True)

    # cleanup: accounts are synthetic; remove what the API lets us remove
    removed = 0
    for uid in ids:
        code, _ = root.delete(f"/api/admin/users/{uid}")
        removed += code in (200, 204)
    print(f"cleanup: {removed}/{len(ids)} accounts removed (organisation LoadCo {run_id} remains on staging)")
    ok = len(lat_ready) == a.users and bad_code == 0 and leaks == 0 and found >= int(len(res) * 0.9)
    print("RESULT:", "PASS" if ok else "FAIL", f"in {time.time() - t0:.0f}s")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
