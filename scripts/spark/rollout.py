#!/usr/bin/env python3
"""Controlled rollout of the cloud features on one Semechka instance, through its own admin API (every change is authorised,
validated and audited exactly like a click in Administration, Settings).

    ROLLOUT_URL=http://192.168.20.193:8787 ROLLOUT_ADMIN_EMAIL=you@example.com python3 rollout.py <command> [options]

    status               show the switches (read-only)
    apply  [--dry-run]   fence, allow-list the agent model, then workspaces, then agents (see ORDER)
    rollback [--open]    agents off, workspaces off; the fence stays (--open also lifts it)
    go     [--dry-run]   apply + production smoke tests with this same signed-in session; if a critical check fails the
                         flags are rolled back automatically. Writes a report (no secrets) to --report.

ORDER (apply). Nothing is switched on before the fence and the model list are in place and read back:
    0. resolve the model: exactly one enabled model whose id/alias/real id matches --model (default qwen3.6-35b-a3b@MXFP4_MOE)
    1. cloud.rollout = admins          (read back; this is NEVER undone by a failure or by rollback)
    2. cloud.agents.models = [that id] (read back)
    3. cloud.workspaces.enabled = true (read back)
    4. cloud.agents.enabled = true     (only if 1 and 2 still read back as set; read back)
The sandbox switch is never touched; if it is on, apply stops without changing anything.

The administrator's password (and 2-step code, if the account has one) is prompted for with no echo; ROLLOUT_ADMIN_PASSWORD
is honoured for automation. It is never printed, written to a file or put on a command line. Standard library only."""
from __future__ import annotations

import getpass
import http.cookiejar
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid

BASE = os.environ.get("ROLLOUT_URL", "").rstrip("/")
DEFAULT_MODEL = "qwen3.6-35b-a3b@MXFP4_MOE"
RESULTS: list[tuple[str, bool, bool, str]] = []  # name, ok, critical, seen


class Stop(Exception):
    """A step failed; the message says which, and what state the instance is left in."""


class Admin:
    def __init__(self, email: str, password: str):
        self.email = email
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        self.csrf = ""
        code, body = self.call("POST", "/api/auth/login", {"email": email, "password": password})
        if code == 401 and isinstance(body, dict) and (body.get("detail") or {}).get("code") == "mfa_required":
            code, body = self.call("POST", "/api/auth/login", {"email": email, "password": password, "totp": getpass.getpass("Authentication code: ").strip()})
        if code != 200:
            sys.exit(f"sign-in failed ({code}); nothing was changed")
        self.csrf = self.call("GET", "/api/me")[1]["csrf"]

    def call(self, method: str, path: str, body=None, upload=None):
        h = {"Origin": BASE}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        data = None
        if upload:
            b = uuid.uuid4().hex
            name, blob = upload
            data = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode()
                    + blob + f"\r\n--{b}--\r\n".encode())
            h["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        try:
            with self.op.open(urllib.request.Request(BASE + path, data=data, method=method, headers=h), timeout=120) as r:
                raw, code = r.read(), r.status
        except urllib.error.HTTPError as e:
            raw, code = e.read(), e.code
        try:
            return code, json.loads(raw or b"null")
        except ValueError:
            return code, {"raw": raw[:200].decode("utf-8", "replace")}

    # -- settings --------------------------------------------------------------------------------------------
    def value(self, key: str):
        code, body = self.call("GET", "/api/admin/settings")
        if code != 200:
            raise Stop(f"could not read settings ({code})")
        return next(s["value"] for s in body["settings"] if s["id"] == key)

    def set(self, key: str, value) -> None:
        code, body = self.call("PUT", f"/api/admin/settings/{key}", {"value": value})
        if code != 200:
            raise Stop(f"could not set {key} ({code}): {str(body)[:200]}")
        got = self.value(key)
        if got != value:
            raise Stop(f"{key} read back as {got!r}, expected {value!r}")
        print(f"  set {key} = {value!r}  (read back ok)", flush=True)


def state(a: Admin) -> dict:
    code, st = a.call("GET", "/api/admin/cloud/status")
    if code != 200:
        raise Stop(f"status unavailable ({code})")
    return {"rollout": st.get("rollout"), "workspaces": st.get("workspaces_enabled"), "agents": st["sandbox"]["agents_enabled"],
            "sandbox": st["sandbox"]["enabled"], "agent_models": a.value("cloud.agents.models")}


def show(a: Admin, label: str) -> dict:
    s = state(a)
    print(f"{label}: rollout={s['rollout']}  workspaces={'on' if s['workspaces'] else 'off'}  agents={'on' if s['agents'] else 'off'}"
          f"  sandbox={'ON' if s['sandbox'] else 'off'}  agent-models={s['agent_models']}", flush=True)
    return s


def resolve_model(a: Admin, want: str) -> dict:
    code, body = a.call("GET", f"/api/admin/models?q={urllib.request.quote(want.split('@')[0])}&per_page=50")
    if code != 200:
        raise Stop(f"could not list models ({code})")
    hits = [m for m in body.get("items", []) if want in (m.get("alias"), m.get("real_id"), (m.get("real_id") or "").split(":", 1)[-1])]
    if len(hits) != 1:
        raise Stop(f"expected exactly one model matching {want!r}, found {len(hits)}: {[m.get('alias') for m in hits]}")
    m = hits[0]
    if not m.get("enabled"):
        raise Stop(f"model {want!r} is not enabled")
    return m


def apply(a: Admin, model_want: str, dry: bool) -> dict:
    before = show(a, "before")
    if before["sandbox"]:
        raise Stop("the sandbox switch is on; apply does not run in that state and changed nothing")
    m = resolve_model(a, model_want)
    print(f"  agent model: {m['alias']!r} ({m['id']}), real id {m.get('real_id')}", flush=True)
    steps = ["cloud.rollout = 'admins'", f"cloud.agents.models = [{m['id']!r}]", "cloud.workspaces.enabled = True", "cloud.agents.enabled = True"]
    if dry:
        print("  DRY RUN - would apply, in this order, reading each back:\n    " + "\n    ".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))
        print("  nothing was changed")
        return m
    a.set("cloud.rollout", "admins")  # the fence first; no later failure removes it
    a.set("cloud.agents.models", [m["id"]])
    a.set("cloud.workspaces.enabled", True)
    if a.value("cloud.rollout") != "admins" or a.value("cloud.agents.models") != [m["id"]]:  # re-check immediately before agents
        raise Stop("the fence or the model list changed under us; agents were NOT enabled")
    a.set("cloud.agents.enabled", True)
    after = show(a, "after ")
    if not (after["rollout"] == "admins" and after["workspaces"] and after["agents"] and not after["sandbox"] and after["agent_models"] == [m["id"]]):
        raise Stop("final state is not what apply intends")
    return m


def rollback(a: Admin, open_: bool) -> None:
    show(a, "before")
    for k in ("cloud.agents.enabled", "cloud.workspaces.enabled"):
        try:
            a.set(k, False)
        except Stop as e:
            print(f"  WARNING: {e}", flush=True)
    if open_:
        a.set("cloud.rollout", "everyone")
    show(a, "after ")


# ---- production smoke tests ---------------------------------------------------------------------------------------
def check(name: str, ok: bool, seen: object = "", critical: bool = True) -> bool:
    RESULTS.append((name, bool(ok), critical, str(seen)[:200]))
    print(f"  {'PASS' if ok else 'FAIL'}{'' if critical or ok else ' (non-critical)'}  {name}" + (f"   [{str(seen)[:150]}]" if seen != "" else ""), flush=True)
    return bool(ok)


def gpu_busy(a: Admin, data_dir: str | None) -> str:
    """'' when the engine looks idle, else why not. Active image/video jobs and scheduler queues both count."""
    if data_dir and os.path.exists(f"{data_dir}/semechka.db"):
        con = sqlite3.connect(f"file:{data_dir}/semechka.db?mode=ro", uri=True)
        n = con.execute("select count(*) from media_jobs where status in ('queued','running')").fetchone()[0]
        con.close()
        if n:
            return f"{n} image/video job(s) are active"
    code, q = a.call("GET", "/api/queue/admin")
    if code == 200 and any(b.get("running") or b.get("waiting") for b in q.get("backends", [])):
        return "the inference queue is busy"
    if not data_dir:
        return "" if code == 200 else "could not read the queue"
    return ""


def smoke(a: Admin, model: dict, data_dir: str | None, report: str | None) -> bool:
    tag = time.strftime("%Y%m%d-%H%M%S")
    mid = model["id"]
    users0 = a.call("GET", "/api/admin/users")[1]
    n_users = users0.get("total", len(users0.get("items", []))) if isinstance(users0, dict) else None
    print("== state and restrictions", flush=True)
    st = state(a)
    check("flags: fence on, workspaces on, agents on, sandbox OFF, only the 35B model for agents",
          st["rollout"] == "admins" and st["workspaces"] and st["agents"] and not st["sandbox"] and st["agent_models"] == [mid], st)
    pv = a.call("GET", "/api/admin/preview/member")[1]
    check("role preview 'member': no Workspaces entry and no settings permission (the fence's test)",
          "workspaces" not in [p["id"] for p in pv.get("ui", {}).get("pages", [])] and "admin.settings.edit" not in pv.get("permissions", []), "member preview")
    try:
        urllib.request.urlopen(urllib.request.Request(f"{BASE}/api/orgs"), timeout=15)
        anon = 200
    except urllib.error.HTTPError as e:
        anon = e.code
    check("signed-out callers get 401 on the cloud endpoints", anon == 401, anon)

    print("== workspace creation and retrieval (a dedicated pilot organisation holding only this administrator)", flush=True)
    code, org = a.call("POST", "/api/orgs", {"name": "CMSIT Pilot", "owner_email": a.email})
    check("pilot organisation created", code == 200, code)
    if code != 200:
        return False
    oid = org["id"]
    code, ws = a.call("POST", f"/api/orgs/{oid}/workspaces", {"name": "Pilot " + tag})
    check("workspace created", code == 200, code)
    if code != 200:
        return False
    wid = ws["id"]
    check("the administrator now sees Workspaces in navigation", "workspaces" in [p["id"] for p in a.call("GET", "/api/me")[1]["ui"]["pages"]])
    doc = (f"# Pilot notes {tag}\nMonthly figures: January 4120, February 3875, March 5105.\nThe pilot code word is PLUM-{tag}.\n").encode()
    code, f = a.call("POST", f"/api/workspaces/{wid}/files", upload=("pilot-notes.md", doc))
    check("file uploaded", code == 200, code)
    code, ctx = a.call("POST", f"/api/workspaces/{wid}/contexts", {"name": "Pilot"})
    if code == 200 and f.get("id"):
        a.call("PUT", f"/api/contexts/{ctx['id']}/items/{f['id']}")
    end = time.time() + 300
    while time.time() < end:
        jobs = a.call("GET", f"/api/workspaces/{wid}/jobs")[1]
        if isinstance(jobs, list) and jobs and all(j["status"] in ("succeeded", "failed", "cancelled") for j in jobs):
            break
        time.sleep(3)
    code, r = a.call("POST", f"/api/workspaces/{wid}/retrieve", {"query": f"pilot code word PLUM-{tag}"})
    check("retrieval returns the uploaded text", code == 200 and any(f"PLUM-{tag}" in p["text"] for p in r.get("passages", [])), (code, r.get("mode")))

    print("== model enforcement", flush=True)
    ag = a.call("GET", f"/api/workspaces/{wid}/models?mode=chat&purpose=agent")[1]
    check("the agent picker offers exactly the 35B model", [x["id"] for x in ag] == [mid], [x.get("alias") for x in ag])
    others = [x for x in a.call("GET", f"/api/workspaces/{wid}/models?mode=chat")[1] if x["id"] != mid]
    if others:
        code, _ = a.call("POST", f"/api/workspaces/{wid}/agents", {"name": "other-" + tag, "instructions": "x", "model_id": others[0]["id"], "tools": ["calc"]})
        check("an agent pinned to any other model is refused", code in (400, 403, 422), code)
    else:
        check("another chat model exists to test refusal", False, "none", critical=False)

    print("== agent execution (read-only tools; no code execution)", flush=True)
    why = ""
    for _ in range(40):  # up to ~20 minutes: never start the 35B model while someone's image/video work is on the engine
        why = gpu_busy(a, data_dir)
        if not why:
            break
        print(f"  waiting: {why}", flush=True)
        time.sleep(30)
    if why:
        check("agent run (skipped: engine busy with other people's work)", False, why)
        return False
    code, agent = a.call("POST", f"/api/workspaces/{wid}/agents", {
        "name": "Pilot reader " + tag, "instructions": "Use the tools. Never invent numbers. Report exact figures.",
        "tools": ["search_context", "read_file", "list_files", "calc", "create_document"], "approval": "sensitive", "context_ids": ["*"]})
    check("agent created without naming a model (it must default to the allowed one)", code == 200, code)
    if code != 200:
        return False
    code, run = a.call("POST", f"/api/agents/{agent['id']}/runs", {"task": "Read pilot-notes.md and tell me the total of the three monthly figures. Use the calculator tool for the sum."})
    check("run accepted", code == 200, code)
    if code != 200:
        return False
    rid, end, r = run["run_id"], time.time() + 1200, {}
    while time.time() < end:
        r = a.call("GET", f"/api/runs/{rid}")[1]
        if r.get("status") in ("succeeded", "failed", "cancelled", "awaiting_approval"):
            break
        time.sleep(5)
    answer = json.dumps(r)
    check("run succeeded", r.get("status") == "succeeded", r.get("status"))
    check("answer carries the correct total 13100", "13100" in answer.replace(",", "") or "13 100" in answer, (r.get("final") or r.get("answer") or "")[:120] if isinstance(r, dict) else "")
    detail = a.call("GET", f"/api/runs/{rid}?detail=true")[1]
    used = (detail.get("usage") or {}).get("model") if isinstance(detail, dict) else None
    check("the run used the 35B model (and no other)", used == mid, used)

    print("== owner-only files on the pilot workspace", flush=True)
    code, _ = a.call("PATCH", f"/api/workspaces/{wid}", {"policy": {"file_access": "owner"}})
    check("owner-only rule turned on", code == 200, code)
    names = [x["name"] for x in a.call("GET", f"/api/workspaces/{wid}/files")[1].get("files", [])]
    check("the owner still lists their own file under the rule", "pilot-notes.md" in names, names)
    code, _ = a.call("GET", f"/api/files/{f['id']}/content?download=1")
    check("and can still download it", code == 200, code, critical=False)

    print("== audit log and existing data", flush=True)
    items = a.call("GET", "/api/admin/audit?per_page=200")[1].get("items", [])
    acts = {i["action"] for i in items}
    check("audit records the pilot (organisation, workspace, file, agent run)", any(x.startswith("org.") for x in acts) and any(x.startswith("agent.") for x in acts), sorted(acts)[:10])
    hist = [h for h in a.call("GET", "/api/admin/settings-history")[1] if str(h["key"]).startswith("cloud.")]
    check("settings history has the flag changes with old and new values", len(hist) >= 4, [(h["key"], h["new"]) for h in hist[:4]])
    v = a.call("GET", "/api/admin/audit/verify")[1]
    check("the audit hash chain verifies", v.get("ok") is True, v)
    users1 = a.call("GET", "/api/admin/users")[1]
    n1 = users1.get("total", len(users1.get("items", []))) if isinstance(users1, dict) else None
    check("account count unchanged by the rollout", n1 == n_users, (n_users, n1))

    bad = [x for x in RESULTS if not x[1] and x[2]]
    if report:
        with open(report, "w") as fh:
            fh.write(f"rollout report {tag}  {BASE}\n")
            fh.writelines(f"{'PASS' if ok else 'FAIL'}{'' if crit else ' (non-critical)'}  {n}  [{seen}]\n" for n, ok, crit, seen in RESULTS)
            fh.write(f"pilot organisation {oid}, workspace {wid}\n")
        print(f"  report written to {report}")
    return not bad


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else ""
    if cmd not in ("status", "apply", "rollback", "go") or not BASE:
        sys.exit(__doc__)

    def opt(name, default=None):
        return args[args.index(name) + 1] if name in args else default
    email = os.environ.get("ROLLOUT_ADMIN_EMAIL") or input("Administrator email: ").strip()
    a = Admin(email, os.environ.get("ROLLOUT_ADMIN_PASSWORD") or getpass.getpass("Administrator password: "))
    dry = "--dry-run" in args
    try:
        if cmd == "status":
            show(a, "state ")
        elif cmd == "apply":
            apply(a, opt("--model", DEFAULT_MODEL), dry)
        elif cmd == "rollback":
            rollback(a, "--open" in args)
        else:
            model = apply(a, opt("--model", DEFAULT_MODEL), dry)
            if dry:
                return 0
            ok = False
            try:
                ok = smoke(a, model, opt("--data-dir"), opt("--report"))
            except Stop as e:
                print(f"  STOP during smoke tests: {e}")
            if not ok:
                print("\nA CRITICAL CHECK FAILED - rolling the rollout flags back (the admins-only fence stays on; no data is deleted).", flush=True)
                rollback(a, False)
                return 2
            bad = [x for x in RESULTS if not x[1]]
            print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} smoke checks passed; rollout stays ON for administrators only.")
    except Stop as e:
        print(f"\nSTOPPED: {e}\nThe admins-only fence, if it was set, is still on. Run 'status' to see the exact state.", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
