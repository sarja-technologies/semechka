#!/usr/bin/env python3
"""End-to-end Work Mode run through the HTTP API of a Semechka STAGING instance with a REAL engine model.

Run it on the host that serves staging. It reads the synthetic staging credentials from the owner-only file itself and never
prints them. It switches Work Mode on for staging (settings only), picks a small model by alias, runs one session, prints the
real event timeline, checks the artifact, and (with --cleanup) deletes the session and unloads the model it used.

    python3 work_e2e.py --base http://192.168.20.193:8790 --model Balanced --task "Write a Python script ..." [--cleanup] [--unload]
    python3 work_e2e.py --base ... --session ws_... --events          # print the timeline of an existing session
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

CREDS = os.path.expanduser("~/semechka-deploy/secrets/staging-credentials.txt")
PRIMES = [2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37, 41, 43, 47, 53, 59, 61, 67, 71]


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def req(self, method: str, path: str, body=None, timeout=60, raw=False):
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, method=method)
        if body is not None:
            r.add_header("Content-Type", "application/json")
        if self.csrf and method != "GET":
            r.add_header("X-CSRF-Token", self.csrf)
        try:
            with self.op.open(r, timeout=timeout) as resp:
                b = resp.read()
                return resp.status, (b if raw else (json.loads(b) if b else None))
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b)
            except ValueError:
                return e.code, b.decode(errors="replace")

    def login(self, email: str) -> None:
        pw = None
        for line in open(CREDS):
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 2 and parts[0] == email:
                pw = parts[1]
        assert pw, "no such synthetic account"
        code, _ = self.req("POST", "/api/auth/login", {"email": email, "password": pw})
        assert code == 200, f"login failed ({code})"
        self.csrf = self.req("GET", "/api/me")[1]["csrf"]


def show(ev: dict) -> str:
    d = ev.get("detail") or {}
    k = ev["kind"]
    t = time.strftime("%H:%M:%S", time.localtime(ev["at"]))
    if k == "tool_call":
        args = json.dumps(d.get("args", {}))
        return f"{t} #{ev['id']:<4} tool_call    {d.get('tool')} {args[:260]}"
    if k == "tool_result":
        txt = (d.get("text") or d.get("error") or "").strip().replace("\n", " | ")
        return f"{t} #{ev['id']:<4} tool_result  {ev['summary']}  {d.get('seconds', '')}s  {txt[:300]}"
    if k == "message":
        return f"{t} #{ev['id']:<4} message      [{d.get('role')}] {str(d.get('content'))[:400]}"
    if k == "error":
        return f"{t} #{ev['id']:<4} ERROR        {ev['summary']}  {json.dumps(d)[:300]}"
    return f"{t} #{ev['id']:<4} {k:<12} {ev['summary']}"


def timeline(api: Api, sid: str, until_idle: bool, max_s: int) -> list[dict]:
    seen, out, t0 = 0, [], time.time()
    while time.time() - t0 < max_s:
        code, body = api.req("GET", f"/api/work/sessions/{sid}/events?since={seen}&wait=20", timeout=60)
        assert code == 200, body
        for ev in body["events"]:
            print(show(ev), flush=True)
            out.append(ev)
            seen = ev["id"]
        if until_idle and body["status"] in ("idle", "failed", "cancelled") and not body["events"]:
            break
        if body["status"] == "awaiting_approval":
            print("   (waiting for approval: approving)", flush=True)
            st = api.req("GET", f"/api/work/sessions/{sid}/state")[1]
            for p in st["pending_approvals"]:
                api.req("POST", f"/api/work/sessions/{sid}/approvals/{p['step']}", {"approve": True})
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://192.168.20.193:8790")
    ap.add_argument("--account", default="owner@staging.test")
    ap.add_argument("--model", default="Balanced", help="alias of an enabled model")
    ap.add_argument("--task", default="Write a Python script that computes the first 20 prime numbers, run it, and save its output to artifacts/primes.txt.")
    ap.add_argument("--objective", default="")
    ap.add_argument("--autonomy", default="contained")
    ap.add_argument("--max-seconds", type=int, default=900)
    ap.add_argument("--session", default="")
    ap.add_argument("--events", action="store_true")
    ap.add_argument("--message", default="", help="with --session: send this message to the existing session and print the timeline")
    ap.add_argument("--delete", action="store_true", help="with --session: delete that session (container and volume are removed)")
    ap.add_argument("--cleanup", action="store_true", help="delete the session afterwards (removes its container and volume)")
    ap.add_argument("--unload", action="store_true", help="unload the model afterwards (through Semechka's admin lifecycle)")
    ap.add_argument("--probe-others", default="", help="comma-separated synthetic accounts that must NOT reach the new session (404/403 expected)")
    ap.add_argument("--enable", action="store_true", help="switch Work Mode on (work.enabled, modes.work.enabled) first")
    a = ap.parse_args()
    api = Api(a.base)
    api.login(a.account)
    if a.enable:
        for k in ("work.enabled", "modes.work.enabled"):
            code, body = api.req("PUT", f"/api/admin/settings/{k}", {"value": True})
            print(f"setting {k} = true -> {code}")
    code, opts = api.req("GET", "/api/work/options")
    print("options:", code, json.dumps({"models": [m["alias"] for m in opts.get("models", [])], "image_ready": opts.get("image_ready"),
                                         "network_allowed": opts.get("network_allowed"), "limits": opts.get("limits")}) if code == 200 else opts)
    if code != 200:
        return 2
    if a.session and a.delete:
        code, r = api.req("DELETE", f"/api/work/sessions/{a.session}")
        print("session deleted ->", code, r)
        return 0
    if a.session and a.message:
        last = api.req("GET", f"/api/work/sessions/{a.session}/state")[1]["last_event_id"]
        code, r = api.req("POST", f"/api/work/sessions/{a.session}/messages", {"content": a.message})
        assert code == 200, r
        events = []
        seen, t0 = last, time.time()
        while time.time() - t0 < a.max_seconds:
            code, body = api.req("GET", f"/api/work/sessions/{a.session}/events?since={seen}&wait=20", timeout=60)
            for ev in body["events"]:
                print(show(ev), flush=True)
                seen = ev["id"]
            if body["status"] in ("idle", "failed", "cancelled") and not body["events"]:
                break
        return 0
    if a.session:
        timeline(api, a.session, False, 5)
        return 0
    model = next((m for m in opts["models"] if m["alias"] == a.model), None)
    assert model, f"model {a.model!r} is not offered to this account"
    code, sess = api.req("POST", "/api/work/sessions", {"title": "e2e primes", "objective": a.objective, "model_id": model["id"], "autonomy": a.autonomy})
    assert code == 200, sess
    sid = sess["id"]
    print(f"session {sid} model={a.model} autonomy={a.autonomy}")
    for other in [x for x in a.probe_others.split(",") if x]:
        o = Api(a.base)
        o.login(other)
        codes = {}
        for label, method, path, body in [("get session", "GET", f"/api/work/sessions/{sid}", None), ("events", "GET", f"/api/work/sessions/{sid}/events", None),
                                          ("state", "GET", f"/api/work/sessions/{sid}/state", None), ("files", "GET", f"/api/work/sessions/{sid}/files", None),
                                          ("send message", "POST", f"/api/work/sessions/{sid}/messages", {"content": "hijack"}),
                                          ("delete", "DELETE", f"/api/work/sessions/{sid}", None), ("list", "GET", "/api/work/sessions", None)]:
            c, b = o.req(method, path, body)
            codes[label] = c if label != "list" else f"{c} ({len(b) if isinstance(b, list) else '-'} sessions)"
        print(f"probe as {other}: {codes}")
    t0 = time.time()
    code, r = api.req("POST", f"/api/work/sessions/{sid}/messages", {"content": a.task})
    assert code == 200, r
    events = timeline(api, sid, True, a.max_seconds)
    wall = time.time() - t0
    state = api.req("GET", f"/api/work/sessions/{sid}/state")[1]
    arts = api.req("GET", f"/api/work/sessions/{sid}/artifacts")[1]
    print(f"\nfinished in {wall:.0f}s: status={state['status']} container={state['container']['state']} artifacts={[x['path'] for x in arts]}")
    tool_calls = [e for e in events if e["kind"] == "tool_call"]
    results = [e for e in events if e["kind"] == "tool_result"]
    errors = [e for e in events if e["kind"] == "error"]
    print(f"tool calls={len(tool_calls)} results ok={sum(1 for e in results if e['detail'].get('status') == 'done')} "
          f"tool errors={sum(1 for e in results if e['detail'].get('status') != 'done')} invalid-model-replies={sum(1 for e in errors if 'not a valid action' in e['summary'])}")
    ok = False
    for x in arts:
        if x["path"].endswith("primes.txt"):
            code, raw = api.req("GET", f"/api/work/artifacts/{x['id']}/content", raw=True)
            text = raw.decode()
            nums = [int(t) for t in text.replace(",", " ").replace("[", " ").replace("]", " ").split() if t.isdigit()]
            ok = nums == PRIMES
            print(f"artifact {x['path']} ({x['size']} bytes): {text.strip()[:200]!r} -> {'CORRECT first 20 primes' if ok else 'WRONG'}")
    final = [e for e in events if e["kind"] == "message" and e["detail"].get("role") == "assistant"]
    print("final answer:", (final[-1]["detail"]["content"][:500] if final else "(none)"))
    if a.cleanup:
        code, _ = api.req("DELETE", f"/api/work/sessions/{sid}")
        print("session deleted ->", code)
    if a.unload:
        code, r = api.req("POST", f"/api/admin/models/{model['id']}/unload")
        print("model unload ->", code, json.dumps(r)[:200])
    return 0 if ok and state["status"] == "idle" else 1


if __name__ == "__main__":
    sys.exit(main())
