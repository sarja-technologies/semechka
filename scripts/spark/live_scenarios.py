#!/usr/bin/env python3
"""Live scenarios against the STAGING instance, over the network, as real signed-in users, with the real engine, embedding
model and sandbox. Runs on the developer machine; the synthetic credentials are read over ssh at run time and are never
printed or stored. Every check prints PASS/FAIL with the observed value; nothing is mocked.

    SPARK_SSH_CONFIG=~/.ssh/config.semechka .venv/bin/python scripts/spark/live_scenarios.py [A B C D F H ...]
"""
from __future__ import annotations

import http.cookiejar
import io
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))
from tests.test_files_contexts import (  # document builders shared with the unit tests
    docx_bytes,
    xlsx_bytes,
)

BASE = os.environ.get("STAGING_URL", "http://192.168.20.193:8790")
SSH = ["ssh", "-F", os.path.expanduser(os.environ.get("SPARK_SSH_CONFIG", "~/.ssh/config.semechka")), "spark"]
DOMAIN = "staging.test"
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, seen: object = "") -> bool:
    RESULTS.append((name, bool(ok), str(seen)[:200]))
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   [{str(seen)[:160]}]" if seen != "" else ""), flush=True)
    return bool(ok)


def ssh(cmd: str) -> str:
    return subprocess.run([*SSH, cmd], capture_output=True, text=True, timeout=300, check=False).stdout


def credentials() -> dict[str, str]:
    raw = ssh("cat ~/semechka-deploy/secrets/staging-credentials.txt")
    return dict(line.split("\t", 1) for line in raw.splitlines() if "\t" in line and not line.startswith("#"))


class Client:
    def __init__(self, email: str, password: str):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""
        r = self.req("POST", "/api/auth/login", {"email": email, "password": password})
        assert r[0] == 200, f"login failed for {email}: {r[0]}"
        self.csrf = self.req("GET", "/api/me")[1]["csrf"]

    def req(self, method, path, body=None, files=None, form=None, raw=False):
        h = {"Origin": BASE}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        data = None
        if files:
            b = uuid.uuid4().hex
            parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in (form or {}).items()]
            for name, (fname, blob) in files.items():
                parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{name}"; filename="{fname}"\r\n'
                             f'Content-Type: application/octet-stream\r\n\r\n'.encode() + blob + b"\r\n")
            data = b"".join(parts) + f"--{b}--\r\n".encode()
            h["Content-Type"] = f"multipart/form-data; boundary={b}"
        elif body is not None:
            data = json.dumps(body).encode()
            h["Content-Type"] = "application/json"
        rq = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
        try:
            with self.op.open(rq, timeout=120) as x:
                blob, code = x.read(), x.status
        except urllib.error.HTTPError as e:
            blob, code = e.read(), e.code
        if raw:
            return code, blob
        try:
            return code, (json.loads(blob) if blob else {})
        except ValueError:
            return code, {"raw": blob[:100]}

    def get(self, p, **kw):
        return self.req("GET", p, **kw)

    def post(self, p, body=None, **kw):
        return self.req("POST", p, body if body is not None else {}, **kw)

    def put(self, p, body=None):
        return self.req("PUT", p, body if body is not None else {})

    def delete(self, p):
        return self.req("DELETE", p)

    def upload(self, wid, name, blob, folder_id=None):
        code, r = self._upload(wid, name, blob, folder_id)
        if code == 409:  # re-run: the file is already there, use it
            have = self.get(f"/api/workspaces/{wid}/files")[1]["files"]
            old = next((f for f in have if f["name"] == name), None)
            if old:
                return 200, old
        return code, r

    def _upload(self, wid, name, blob, folder_id=None):
        return self.req("POST", f"/api/workspaces/{wid}/files", files={"file": (name, blob)}, form={"folder_id": folder_id} if folder_id else None)


def wait_jobs(c: Client, wid: str, timeout=240) -> list[dict]:
    end = time.time() + timeout
    while time.time() < end:
        code, jobs = c.get(f"/api/workspaces/{wid}/jobs")
        if code == 200 and jobs and all(j["status"] in ("succeeded", "failed", "cancelled") for j in jobs):
            return jobs
        time.sleep(2)
    return c.get(f"/api/workspaces/{wid}/jobs")[1]


def wait_run(c: Client, rid: str, until=("succeeded", "failed", "cancelled", "awaiting_approval"), timeout=900) -> dict:
    end = time.time() + timeout
    r: dict = {}
    while time.time() < end:
        r = c.get(f"/api/runs/{rid}")[1]
        if r.get("status") in until:
            return r
        time.sleep(3)
    return r


class World:
    """Two companies on one server, created through the platform API like an operator would."""

    def __init__(self):
        creds = credentials()
        self.creds = creds
        self.root = Client(f"owner@{DOMAIN}", creds[f"owner@{DOMAIN}"])
        self.u = {k: Client(f"{k}@{DOMAIN}", creds[f"{k}@{DOMAIN}"]) for k in ("acme-owner", "acme-finance", "acme-eng", "globex-owner", "globex-analyst")}
        self.uid = {k: c.get("/api/me")[1]["user"]["id"] for k, c in self.u.items()}
        code, listed = self.root.get("/api/admin/orgs")
        listed = listed.get("items", []) if isinstance(listed, dict) else listed
        orgs = {o["name"]: o for o in listed} if code == 200 else {}
        self.acme = self._org("Acme Corp", "acme-owner", orgs)
        self.globex = self._org("Globex Inc", "globex-owner", orgs)
        for org, boss, staff in ((self.acme, "acme-owner", {"acme-finance": "dept_admin", "acme-eng": "dept_admin"}),
                                 (self.globex, "globex-owner", {"globex-analyst": "dept_admin"})):
            have = {m["email"] for m in self.u[boss].get(f"/api/orgs/{org}/members")[1]}
            for k, role in staff.items():
                if f"{k}@{DOMAIN}" not in have:
                    self.u[boss].post(f"/api/orgs/{org}/members", {"email": f"{k}@{DOMAIN}", "roles": [role]})
                else:
                    self.u[boss].req("PATCH", f"/api/orgs/{org}/members/{self.uid[k]}", {"roles": [role]})
        code, models = self.u["acme-finance"].get("/api/chat/models")
        self.model = {m["alias"]: m["id"] for m in models} if code == 200 else {}

    def _org(self, name, boss, orgs):
        if name in orgs:
            return orgs[name]["id"]
        code, r = self.root.post("/api/orgs", {"name": name, "owner_email": f"{boss}@{DOMAIN}"})
        assert code == 200, (code, r)
        return r["id"]

    def workspace(self, c: Client, org: str, name: str) -> str:
        code, lst = c.get(f"/api/orgs/{org}/workspaces")
        for w in (lst if code == 200 else []):
            if w["name"] == name:
                return w["id"]
        code, r = c.post(f"/api/orgs/{org}/workspaces", {"name": name})
        assert code == 200, (code, r)
        return r["id"]


FIN_NOTE = "ACME-FIN-7731 Taxi rides over fifty dollars need director approval. Quarterly budget owner is the finance lead."
GLX_NOTE = "GLOBEX-OPS-4418 Warehouse audits run every second Thursday. Spare parts budget is held by operations."


def scenario_AB(w: World):
    print("== A/B: two companies, departments inside one company")
    fin = w.workspace(w.u["acme-finance"], w.acme, "Finance")
    eng = w.workspace(w.u["acme-eng"], w.acme, "Engineering")
    glx = w.workspace(w.u["globex-analyst"], w.globex, "Operations")
    w.u["acme-finance"].delete(f"/api/workspaces/{fin}/members/user/{w.uid['acme-eng']}")  # start from a known state (re-runnable)
    code, f1 = w.u["acme-finance"].upload(fin, "policy.docx", docx_bytes([("h", "Expense Policy"), ("p", FIN_NOTE)]))
    check("A: finance uploads a document", code == 200, code)
    w.u["acme-finance"].upload(fin, "budget.xlsx", xlsx_bytes({"Budget": [["dept", "spend"], ["marketing", 120000], ["finance", 80000]]}))
    w.u["acme-eng"].upload(eng, "runbook.md", b"# Runbook\nENG-RUN-5512 restart the build farm before the release.\n")
    w.u["globex-analyst"].upload(glx, "ops.docx", docx_bytes([("h", "Operations"), ("p", GLX_NOTE)]))
    for c, wid in ((w.u["acme-finance"], fin), (w.u["acme-eng"], eng), (w.u["globex-analyst"], glx)):
        wait_jobs(c, wid)
    fid = f1["id"]

    # A: another company cannot see, read or guess anything
    g = w.u["globex-analyst"]
    check("A: Globex cannot open Acme's workspace (404)", g.get(f"/api/workspaces/{fin}")[0] == 404)
    check("A: Globex cannot open Acme's file by id (404)", g.get(f"/api/files/{fid}")[0] == 404)
    check("A: Globex cannot download Acme's file (404)", g.get(f"/api/files/{fid}/content", raw=True)[0] == 404)
    code, r = g.post(f"/api/workspaces/{glx}/retrieve", {"query": "taxi rides director approval quarterly budget"})
    seen = json.dumps(r)
    check("A: Globex retrieval never returns Acme text", code == 200 and "ACME-FIN-7731" not in seen and "director approval" not in seen, f"{len(r.get('passages', []))} passages")
    check("A: Globex cannot retrieve from Acme's workspace (404)", g.post(f"/api/workspaces/{fin}/retrieve", {"query": "taxi"})[0] == 404)
    check("A: Globex cannot list Acme's members (404)", g.get(f"/api/orgs/{w.acme}/members")[0] in (403, 404))
    check("A: Acme's boss cannot read Globex either", w.u["acme-owner"].get(f"/api/workspaces/{glx}")[0] == 404)
    code, r = w.u["globex-analyst"].post(f"/api/workspaces/{glx}/retrieve", {"query": "warehouse audits"})
    check("A: Globex finds its own document", code == 200 and any("GLOBEX-OPS-4418" in p["text"] for p in r.get("passages", [])), r.get("mode"))

    # B: departments in one company
    e = w.u["acme-eng"]
    check("B: engineering cannot open the finance workspace (404)", e.get(f"/api/workspaces/{fin}")[0] == 404)
    code, r = e.post(f"/api/workspaces/{eng}/retrieve", {"query": "taxi rides director approval"})
    check("B: engineering retrieval does not leak finance text", code == 200 and "ACME-FIN-7731" not in json.dumps(r))
    b = w.u["acme-owner"]
    check("B: the organisation owner administers people but cannot read finance files", b.get(f"/api/files/{fid}/content", raw=True)[0] in (403, 404))
    check("B: finance lead reads own file", w.u["acme-finance"].get(f"/api/files/{fid}/content", raw=True)[0] == 200)
    # share explicitly, then revoke
    code, _ = w.u["acme-finance"].put(f"/api/workspaces/{fin}/members", {"principal_type": "user", "principal_id": w.uid["acme-eng"], "role": "viewer"})
    check("B: finance lead shares the workspace read-only with engineering", code == 200, code)
    code, r = e.post(f"/api/workspaces/{fin}/retrieve", {"query": "taxi rides director approval"})
    check("B: engineering now retrieves finance text, with citation", code == 200 and any(p["file"] == "policy.docx" for p in r.get("passages", [])))
    check("B: a viewer cannot upload", e.upload(fin, "x.txt", b"x")[0] == 403)
    code, _ = w.u["acme-finance"].delete(f"/api/workspaces/{fin}/members/user/{w.uid['acme-eng']}")
    check("B: access revoked", code in (200, 204), code)
    check("B: engineering is locked out again at once (404)", e.get(f"/api/workspaces/{fin}")[0] == 404)
    return {"fin": fin, "eng": eng, "glx": glx, "fid": fid}


def scenario_C(w: World, ids):
    print("== C: persistent knowledge, across a service restart")
    c, fin = w.u["acme-finance"], ids["fin"]
    code, ctxs = c.get(f"/api/workspaces/{fin}/contexts")
    ctx = next((x for x in ctxs if x["name"] == "Finance knowledge"), None) if code == 200 else None
    if not ctx:
        ctx = c.post(f"/api/workspaces/{fin}/contexts", {"name": "Finance knowledge", "description": "policies and budgets"})[1]
    files = c.get(f"/api/workspaces/{fin}/files")[1]["files"]
    for f in files:
        c.put(f"/api/contexts/{ctx['id']}/items/{f['id']}")
    code, before = c.post(f"/api/workspaces/{fin}/retrieve", {"query": "who has to approve an expensive taxi ride", "context_ids": [ctx["id"]]})
    # (earlier runs leave generated summaries of the same policy in this workspace; they may rank first, so ask for the source)
    got = [p.get("file") for p in (before.get("passages") or [])[:4]]
    check("C: question by meaning finds the policy passage", code == 200 and "policy.docx" in got, f"mode={before.get('mode')} files={got}")
    check("C: retrieval used the embedding model (hybrid)", before.get("mode") == "hybrid", before.get("mode"))
    ssh("systemctl --user restart semechka-staging.service")
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=3).read()
            break
        except OSError:
            time.sleep(1)
    c2 = Client(f"acme-finance@{DOMAIN}", w.creds[f"acme-finance@{DOMAIN}"])
    code, after = c2.post(f"/api/workspaces/{fin}/retrieve", {"query": "who has to approve an expensive taxi ride", "context_ids": [ctx["id"]]})
    got2 = [(p.get("file"), p.get("loc")) for p in (after.get("passages") or [])[:4]]
    check("C: after the service restarted the same knowledge answers the same way", code == 200 and got2 == [(p.get("file"), p.get("loc")) for p in (before.get("passages") or [])[:4]] and "policy.docx" in [g[0] for g in got2], got2[:2])
    check("C: context membership survived", len(c2.get(f"/api/contexts/{ctx['id']}")[1].get("items", [])) == len(files))
    return ctx["id"]


def ensure_agent(c: Client, wid: str, name: str, model: str, tools: list[str], approval="sensitive", ctx_ids=None, **extra) -> str:
    code, lst = c.get(f"/api/workspaces/{wid}/agents")
    aid = next((a["id"] for a in (lst if code == 200 else []) if a["name"] == name), None)
    body = {"name": name, "instructions": extra.pop("instructions", "You are a careful finance analyst. Use tools; never invent numbers."),
            "model_id": model, "tools": tools, "approval": approval, "context_ids": ctx_ids or [], **extra}
    if aid:
        c.req("PATCH", f"/api/agents/{aid}", body)
        return aid
    code, r = c.post(f"/api/workspaces/{wid}/agents", body)
    assert code == 200, (code, r)
    return r["id"]


def start(c: Client, aid: str, task: str) -> str:
    code, r = c.post(f"/api/agents/{aid}/runs", {"task": task})
    assert code == 200, (code, r)
    return r["run_id"]


def run_agent(c: Client, wid: str, name: str, task: str, model: str, tools: list[str], approval="sensitive", ctx_ids=None, **extra):
    aid = ensure_agent(c, wid, name, model, tools, approval, ctx_ids, **extra)
    return aid, start(c, aid, task)


def scenario_D(w: World, ids, ctx_id):
    print("== D: cloud document agent (real model, real sandbox)")
    c, fin = w.u["acme-finance"], ids["fin"]
    sept = [["category", "amount_usd"], ["Revenue", 120000], ["Cost of goods", 50000], ["Marketing", 9000], ["Payroll", 38000], ["Rent", 6000], ["Software", 2500]]
    octo = [["category", "amount_usd"], ["Revenue", 134000], ["Cost of goods", 56500], ["Marketing", 14500], ["Payroll", 38000], ["Rent", 6000], ["Software", 3100]]
    c.upload(fin, "pnl-september.xlsx", xlsx_bytes({"P&L": sept}))
    c.upload(fin, "pnl-october.xlsx", xlsx_bytes({"P&L": octo}))
    wait_jobs(c, fin)
    net = lambda rows: sum(r[1] if r[0] == "Revenue" else -r[1] for r in rows[1:])
    exp_oct, exp_sep = net(octo), net(sept)
    model = w.model.get(os.environ.get("AGENT_MODEL", "Large")) or next(iter(w.model.values()))
    task = ("Compare pnl-september.xlsx and pnl-october.xlsx. Use the python tool to compute net profit for both months and the "
            "percentage change in marketing spend. Then create a Word document named october-review.docx with the findings.")
    aid, rid = run_agent(c, fin, "Monthly reviewer", task, model, ["list_files", "read_table", "calc", "python", "create_document"], ctx_ids=[ctx_id])
    t0 = time.time()
    r = wait_run(c, rid)
    approvals = 0
    while r.get("status") == "awaiting_approval" and approvals < 6:
        full = c.get(f"/api/runs/{rid}", )[1]
        pend = next((s for s in c.get(f"/api/runs/{rid}?detail=1")[1].get("steps", []) if s.get("status") == "awaiting_approval"), None)
        if not pend:
            break
        approvals += 1
        c.post(f"/api/runs/{rid}/approve", {"step": pend["n"], "approve": True})
        r = wait_run(c, rid)
        del full
    detail = c.get(f"/api/runs/{rid}?detail=1")[1]
    check("D: run finished", r.get("status") == "succeeded", f"{r.get('status')} in {time.time() - t0:.0f}s, {approvals} approval(s)")
    n_py = sum(1 for s in detail.get("steps", []) if s.get("tool") == "python")
    check("D: every code execution waited for a person's approval", n_py >= 1 and approvals >= n_py, f"{approvals} approvals for {n_py} python steps")
    tools_used = [s.get("tool") for s in detail.get("steps", []) if s.get("tool")]
    check("D: the real sandbox ran code", "python" in tools_used, tools_used)
    arts = detail.get("artifacts") or []
    check("D: a document artifact was saved", any(a["name"].endswith(".docx") for a in arts), [a["name"] for a in arts])
    if arts:
        fid = next(a for a in arts if a["name"].endswith(".docx"))["file_id"]
        code, blob = c.get(f"/api/files/{fid}/content?download=1", raw=True)
        import zipfile
        text = ""
        if code == 200:
            with zipfile.ZipFile(io.BytesIO(blob)) as z:
                text = z.read("word/document.xml").decode("utf8", "ignore")
        digits = "".join(ch for ch in text if ch.isdigit() or ch in ".,")
        check("D: the document states the correct October net profit", str(exp_oct) in digits.replace(",", ""), f"expected {exp_oct}")
        check("D: the document states the correct September net profit", str(exp_sep) in digits.replace(",", ""), f"expected {exp_sep}")
    check("D: the final answer carries no unverified-file warning", not (detail.get("warnings") or []), detail.get("warnings"))
    check("D: usage and sources recorded", bool(detail.get("usage")), detail.get("usage"))
    return aid


def scenario_F(w: World, ids, ctx_id):
    print("== F: revoke access while a run is queued")
    c, fin = w.u["acme-finance"], ids["fin"]
    e = w.u["acme-eng"]
    c.put(f"/api/workspaces/{fin}/members", {"principal_type": "user", "principal_id": w.uid["acme-eng"], "role": "contributor"})
    for who in (c, e):  # leftovers of an earlier aborted run would occupy the per-user limit
        listed = who.get(f"/api/workspaces/{fin}/runs")[1]
        for r in (listed if isinstance(listed, list) else listed.get("runs", listed.get("items", []))):
            if r.get("status") in ("queued", "running", "awaiting_approval"):
                who.post(f"/api/runs/{r['id']}/cancel")
    time.sleep(2)
    model = w.model.get(os.environ.get("AGENT_MODEL", "Large")) or next(iter(w.model.values()))
    aid = ensure_agent(c, fin, "Queue filler", model, ["search_context", "read_file"], approval="never", ctx_ids=[ctx_id])
    fill = [start(c, aid, f"Summarise policy.docx in detail, attempt {i}.") for i in range(2)]  # occupy both agent slots
    runs = fill + [start(e, aid, f"Summarise budget.xlsx in detail, attempt {i}.") for i in range(2)]  # these two must wait
    time.sleep(1)
    queued = [r for r in runs[2:] if e.get(f"/api/runs/{r}")[1].get("status") == "queued"]
    check("F: some runs are genuinely queued", bool(queued), len(queued))
    c.delete(f"/api/workspaces/{fin}/members/user/{w.uid['acme-eng']}")
    if queued:
        final = wait_run(c, queued[-1], timeout=300)
        check("F: the queued run was cancelled, not executed", final.get("status") == "cancelled", final.get("status"))
        wt = c.get(f"/api/runs/{queued[-1]}?detail=1")[1]
        check("F: it did no work", not [s for s in wt.get("steps", []) if s.get("tool")], len(wt.get("steps", [])))
    for r in runs:
        c.post(f"/api/runs/{r}/cancel")


def scenario_H(w: World, ids, ctx_id):
    print("== H: the service restarts while an agent is running")
    c, fin = w.u["acme-finance"], ids["fin"]
    model = w.model.get(os.environ.get("AGENT_MODEL", "Large")) or next(iter(w.model.values()))
    _aid, rid = run_agent(c, fin, "Restart survivor", "Read policy.docx and budget.xlsx, then write a short summary document called restart-check.docx.", model,
                         ["list_files", "read_file", "read_table", "create_document"], approval="never", ctx_ids=[ctx_id])
    status, nsteps = "", 0
    for _ in range(240):  # in flight = running AND at least one step already written, so the restart interrupts real work
        d = c.get(f"/api/runs/{rid}?detail=1")[1]
        status, nsteps = d.get("status"), len(d.get("steps", []))
        if status == "running" and nsteps >= 1:
            break
        if status in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.5)
    check("H: the run is in flight when the service is restarted", status == "running" and nsteps >= 1, f"{status}, {nsteps} step(s) written")
    ssh("systemctl --user restart semechka-staging.service")
    for _ in range(60):
        try:
            urllib.request.urlopen(BASE + "/api/health", timeout=3).read()
            break
        except OSError:
            time.sleep(1)
    c = Client(f"acme-finance@{DOMAIN}", w.creds[f"acme-finance@{DOMAIN}"])
    r = wait_run(c, rid, timeout=900)
    check("H: the run resumed and finished after the restart", r.get("status") == "succeeded", r.get("status"))
    detail = c.get(f"/api/runs/{rid}?detail=1")[1]
    steps = detail.get("steps", [])
    check("H: no started tool call was replayed", len({(s.get("n")) for s in steps}) == len(steps), len(steps))
    check("H: exactly one document was produced (not zero, not duplicated)", [a["name"] for a in detail.get("artifacts") or []].count("restart-check.docx") == 1, [a["name"] for a in detail.get("artifacts") or []])


def main() -> int:
    wanted = set(sys.argv[1:]) or set("ABCDFH")
    w = World()
    print(f"staging {BASE}; models: {sorted(w.model)}")
    ids = scenario_AB(w) if wanted & set("ABCDFH") else {}
    ctx = scenario_C(w, ids) if wanted & set("CDFH") else None
    if "D" in wanted:
        scenario_D(w, ids, ctx)
    if "F" in wanted:
        scenario_F(w, ids, ctx)
    if "H" in wanted:
        scenario_H(w, ids, ctx)
    bad = [r for r in RESULTS if not r[1]]
    print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed")
    for n, _, s in bad:
        print("  FAILED:", n, "|", s)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
