#!/usr/bin/env python3
"""End-to-end proof of Work Mode on upstream Goose against a real dev Semechka instance, the real router and the real Goose.

Run ON the Spark, inside the shared GPU lock, with a dev instance already serving (see docs/WORK_MODE_GOOSE_SECURITY.md, section 5):

    ~/spark-reset/gpu-llm python3 scripts/spark/goose_e2e.py --base http://127.0.0.1:8794 --creds ~/work-dev/creds.json --out ~/work-dev/evidence

Phases (each writes JSONL event logs and a summary to --out; credentials and keys are read from files and never printed):
  isolation  two people's sessions, cross-read and /home/spark probes from INSIDE the containers, network probes
  repo       inspect a small repo, deny one edit, approve the rest, fix the bug, run the test, report (diff + test output)
  cancel     cancel a running tool
  mcp        a tool from an allowlisted MCP server
  skill      a skill applied
  restart    (separate invocation: --phase restart_check) resume the same Goose session after the dev instance restarted
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

DOCKER = {"DOCKER_HOST": f"unix:///run/user/{os.getuid()}/semechka-sandbox.sock", "PATH": "/usr/bin:/bin"}


class Api:
    def __init__(self, base: str):
        self.base = base
        self.jar = CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = ""

    def req(self, method: str, path: str, body=None, params=None, raw: bytes | None = None, ctype: str | None = None):
        url = self.base + path + (("?" + urllib.parse.urlencode(params)) if params else "")
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = {"Content-Type": ctype or "application/json"} if data is not None else {}
        if self.csrf and method != "GET":
            h["X-CSRF-Token"] = self.csrf
        r = urllib.request.Request(url, data=data, method=method, headers=h)
        try:
            with self.op.open(r, timeout=120) as resp:
                b = resp.read()
                return resp.status, (json.loads(b) if b[:1] in (b"{", b"[") else b)
        except urllib.error.HTTPError as e:
            b = e.read()
            try:
                return e.code, json.loads(b)
            except ValueError:
                return e.code, b

    def login(self, email: str, pw: str):
        st, _ = self.req("POST", "/api/auth/login", {"email": email, "password": pw})
        assert st == 200, f"login failed for {email}: {st}"
        self.csrf = self.req("GET", "/api/me")[1]["csrf"]

    def upload(self, cid: str, path: str, content: bytes):
        boundary = "----e2e" + str(time.time_ns())
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{os.path.basename(path)}"\r\n'
                f"Content-Type: application/octet-stream\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
        return self.req("PUT", f"/api/work/goose/conversations/{cid}/files", params={"path": path}, raw=body, ctype=f"multipart/form-data; boundary={boundary}")


def docker(*args: str, timeout=60) -> tuple[int, str]:
    r = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout, env=DOCKER)
    return r.returncode, (r.stdout + r.stderr)


class Run:
    def __init__(self, out: Path):
        self.out = out
        out.mkdir(parents=True, exist_ok=True)
        self.summary: list[str] = []

    def note(self, line: str):
        print(line, flush=True)
        self.summary.append(line)
        (self.out / "SUMMARY.txt").write_text("\n".join(self.summary) + "\n")

    def dump(self, name: str, obj):
        (self.out / name).write_text(obj if isinstance(obj, str) else json.dumps(obj, indent=1, default=str))


def wait_idle_turn(api: Api, cid: str, run: Run, tag: str, since: int, policy, timeout=900):
    """Poll events until a turn_end after `since`; answer approvals with `policy(approval) -> decision`."""
    base = f"/api/work/goose/conversations/{cid}"
    seen = since
    log = []
    answered = set()
    t0 = time.time()
    while time.time() - t0 < timeout:
        st, body = api.req("GET", base + "/events", params={"format": "json", "since": seen, "wait": 10})
        assert st == 200, (st, body)
        for e in body["events"]:
            seen = e["seq"]
            log.append(e)
            if e["type"] == "approval_request" and e["approval"]["id"] not in answered:
                ap = e["approval"]
                d = policy(ap)
                answered.add(ap["id"])
                st2, _ = api.req("POST", f"{base}/approvals/{ap['id']}", {"decision": d})
                run.note(f"  [{tag}] approval {ap['tool']} {json.dumps(ap['input'])[:90]} -> {d} (HTTP {st2})")
            if e["type"] == "turn_end":
                run.dump(f"events-{tag}.jsonl", "\n".join(json.dumps(x, default=str) for x in log))
                return log, seen
    raise TimeoutError(tag)


def text_of(log):
    return "".join(e.get("text", "") for e in log if e["type"] == "assistant_delta")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8794")
    ap.add_argument("--creds", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--phase", default="all")
    ap.add_argument("--model", default="qwen3.6-35b-a3b")
    a = ap.parse_args()
    run = Run(Path(a.out))
    creds = json.loads(Path(a.creds).read_text())
    alice, bob = Api(a.base), Api(a.base)
    alice.login("alice@dev.test", creds["alice"])
    bob.login("bob@dev.test", creds["bob"])
    state_file = Path(a.out) / "state.json"
    state = json.loads(state_file.read_text()) if state_file.exists() else {}

    def new_conv(api):
        st, b = api.req("POST", "/api/chat/conversations", {"mode": "chat"})
        assert st in (200, 201), (st, b)
        return b["id"]

    if a.phase in ("all", "isolation"):
        run.note("== isolation")
        ca, cb = state.get('alice_conv') or new_conv(alice), state.get('bob_conv') or new_conv(bob)
        sa = alice.req("POST", f"/api/work/goose/conversations/{ca}/session", {"model": a.model, "autonomy": "auto"})[1]
        sb = bob.req("POST", f"/api/work/goose/conversations/{cb}/session", {"model": a.model, "autonomy": "auto"})[1]
        assert alice.upload(ca, "alice-secret.txt", b"ALICE-SECRET-1\n")[0] == 200 and bob.upload(cb, "bob-secret.txt", b"BOB-SECRET-2\n")[0] == 200
        run.note(f"  sessions: alice={sa['id']} bob={sb['id']}")
        # HTTP level: bob cannot see alice's session, files, events
        for path in ("/session", "/transcript", "/events?format=json", "/files", "/files/content?path=alice-secret.txt"):
            st, _ = bob.req("GET", f"/api/work/goose/conversations/{ca}{path}")
            run.note(f"  bob GET alice's {path.split('?')[0]} -> HTTP {st}")
            assert st == 404
        st, _ = bob.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "hi"})
        run.note(f"  bob POST prompt on alice's conversation -> HTTP {st}")
        assert st == 404
        # container level: what a shell INSIDE alice's container (what Goose's shell tool is) can reach
        na, nb = "gwork-" + sa["id"].split("_", 1)[1], "gwork-" + sb["id"].split("_", 1)[1]
        probes = [
            ("whoami / uid", ["id"]),
            ("see own file", ["cat", "/work/alice-secret.txt"]),
            ("see bob's file (path guess)", ["sh", "-c", f"cat /work/../{nb}/bob-secret.txt; find / -name bob-secret.txt 2>/dev/null | head -3; echo find-done"]),
            ("read /home/spark", ["sh", "-c", "ls -la /home/spark; cat /home/spark/.bash_history; ls /home; echo done"]),
            ("read host semechka data", ["sh", "-c", "ls /home/spark/semechka-deploy 2>&1; ls /run/user 2>&1; ls /proc/1/root/home 2>&1; echo done"]),
            ("mounts of interest", ["sh", "-c", "mount | grep -E ' /(work|run/llm|home|root) ' | cut -c1-150"]),
            ("network devices", ["sh", "-c", "cat /proc/net/dev | cut -c1-30"]),
            ("reach router/LAN/internet", ["python3", "-c", "import socket\nfor h,p in [('192.0.2.10',8080),('127.0.0.1',8080),('127.0.0.1',22),('8.8.8.8',53),('192.0.2.10',8830)]:\n s=socket.socket(); s.settimeout(2)\n try: s.connect((h,p)); print(h,p,'OPEN')\n except Exception as e: print(h,p,'blocked:',type(e).__name__)"]),
            ("environment has no router key", ["sh", "-c", "env | cut -d= -f1 | sort | tr '\\n' ' '"]),
            ("model gateway: embeddings path", ["python3", "-c", "import socket\ns=socket.socket(socket.AF_UNIX); s.connect('/run/llm/llm.sock'); s.sendall(b'POST /v1/embeddings HTTP/1.1\\r\\nHost:x\\r\\nContent-Length: 2\\r\\n\\r\\n{}'); print(s.recv(300).decode().split('\\r\\n')[0])"]),
            ("model gateway: another model", ["python3", "-c", "import socket,json\nb=json.dumps({'model':'gpt-oss-120b','messages':[]}).encode()\ns=socket.socket(socket.AF_UNIX); s.connect('/run/llm/llm.sock'); s.sendall(b'POST /v1/chat/completions HTTP/1.1\\r\\nHost:x\\r\\nContent-Length: '+str(len(b)).encode()+b'\\r\\n\\r\\n'+b); print(s.recv(400).decode().replace('\\r\\n','|')[:300])"]),
            ("model gateway: model list", ["python3", "-c", "import socket\ns=socket.socket(socket.AF_UNIX); s.connect('/run/llm/llm.sock'); s.sendall(b'GET /v1/models HTTP/1.1\\r\\nHost:x\\r\\n\\r\\n'); print(s.recv(600).decode().split('\\r\\n\\r\\n')[-1])"]),
        ]
        for label, cmd in probes:
            rc, out = docker("exec", na, *cmd)
            run.note(f"  [{label}] rc={rc}: {out.strip()[:420]!r}")
        rc, out = docker("exec", nb, "sh", "-c", "ls /work; echo ---; cat /work/alice-secret.txt 2>&1")
        run.note(f"  [bob's container: own files, then alice's] rc={rc}: {out.strip()!r}")
        rc, out = docker("inspect", na, "-f", "{{.HostConfig.NetworkMode}} readonly={{.HostConfig.ReadonlyRootfs}} caps_drop={{.HostConfig.CapDrop}} pids={{.HostConfig.PidsLimit}} mem={{.HostConfig.Memory}} user={{.Config.User}} privileged={{.HostConfig.Privileged}}")
        run.note(f"  [container flags] {out.strip()}")
        state.update(alice_conv=ca, bob_conv=cb)
        state_file.write_text(json.dumps(state))

    if a.phase in ("all", "repo"):
        run.note("== repo task (ask mode: approve everything, deny the first edit)")
        ca = state.get("alice_conv") or new_conv(alice)
        alice.req("PATCH", f"/api/work/goose/conversations/{ca}/session", {"autonomy": "ask", "model": a.model, "mcp": ["repo-stats"]})
        files = {
            "shop/pricing.py": "def total(prices, tax_rate):\n    \"\"\"Sum of prices with tax applied once.\"\"\"\n    subtotal = sum(prices)\n    return round(subtotal + tax_rate, 2)\n",
            "shop/__init__.py": "",
            "tests/test_pricing.py": "from shop.pricing import total\n\n\ndef test_total_applies_tax_rate_as_a_fraction():\n    assert total([10.0, 5.0], 0.2) == 18.0\n\n\ndef test_total_without_tax():\n    assert total([1.0, 2.0], 0.0) == 3.0\n",
            "README.md": "# shop\nTiny pricing helper. Run tests with `python -m pytest`.\n",
        }
        for p, c in files.items():
            assert alice.upload(ca, p, c.encode())[0] == 200
        st, b = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")
        since = b["last_seq"]
        t0 = time.time()
        st, b = alice.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "Inspect this repository, find out why the tests fail, fix the code (not the tests), run the tests again and report what you changed."})
        assert st == 202, (st, b)
        denied = {"n": 0}

        def policy(ap):
            if ap["tool"] in ("edit", "write") and denied["n"] == 0:
                denied["n"] += 1
                return "deny"
            return "allow_once"
        log, since = wait_idle_turn(alice, ca, run, "repo", since, policy)
        run.note(f"  turn took {time.time() - t0:.0f}s; events={len(log)}; tools={[e['tool'] for e in log if e['type'] == 'tool_start']}")
        run.note(f"  approvals resolved: {[(e['decision'], e['by']) for e in log if e['type'] == 'approval_resolved']}")
        te = [e for e in log if e["type"] == "turn_end"][-1]
        run.note(f"  turn_end: {te['stop_reason']} usage={te.get('usage')}")
        run.note("  assistant said: " + text_of(log).strip().replace("\n", " ")[:700])
        fc = [e for e in log if e["type"] == "files_changed"]
        if fc:
            st, ch = alice.req("GET", f"/api/work/goose/conversations/{ca}/changes/{fc[-1]['turn_id']}")
            run.dump("repo-diff.txt", ch["diff"])
            run.note(f"  files_changed: {[(f['path'], f['status'], f['additions'], f['deletions']) for f in fc[-1]['files']]}")
        st, b = alice.req("GET", f"/api/work/goose/conversations/{ca}/files/content", params={"path": "shop/pricing.py"})
        run.dump("repo-pricing.py.after", b if isinstance(b, str) else b.decode())
        rc, out = docker("exec", "gwork-" + alice.req("GET", f"/api/work/goose/conversations/{ca}/session")[1]["id"].split("_", 1)[1], "python3", "-m", "pytest", "-q", "/work/tests")
        run.dump("repo-pytest-after.txt", out)
        run.note(f"  independent pytest run in the container after the task: rc={rc} -> {out.strip().splitlines()[-1] if out.strip() else ''}")
        state.update(alice_conv=ca)
        state_file.write_text(json.dumps(state))

    if a.phase in ("all", "cancel"):
        run.note("== cancel")
        ca = state["alice_conv"]
        alice.req("PATCH", f"/api/work/goose/conversations/{ca}/session", {"autonomy": "auto"})
        since = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")[1]["last_seq"]
        st, b = alice.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "Run the shell command `sleep 300` and wait for it, then tell me it is done."})
        assert st == 202
        t0 = time.time()
        while time.time() - t0 < 300:
            evs = alice.req("GET", f"/api/work/goose/conversations/{ca}/events", params={"format": "json", "since": since, "wait": 5})[1]["events"]
            if any(e["type"] == "tool_start" and "sleep" in json.dumps(e.get("input")) for e in evs):
                break
        time.sleep(3)
        sid = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")[1]["id"].split("_", 1)[1]
        rc, before = docker("exec", "gwork-" + sid, "sh", "-c", "ps -eo pid,args | grep -c '[s]leep 300'")
        t1 = time.time()
        alice.req("POST", f"/api/work/goose/conversations/{ca}/cancel")
        log, _ = wait_idle_turn(alice, ca, run, "cancel", since, lambda ap: "deny")
        te = [e for e in log if e["type"] == "turn_end"][-1]
        rc, after = docker("exec", "gwork-" + sid, "sh", "-c", "ps -eo pid,args | grep -c '[s]leep 300'")
        run.note(f"  sleep processes before cancel={before.strip()} after={after.strip()}; turn_end={te['stop_reason']} {time.time() - t1:.1f}s after cancel; "
                 f"session state={alice.req('GET', f'/api/work/goose/conversations/{ca}/session')[1]['state']}")

    if a.phase in ("all", "mcp"):
        run.note("== MCP tool")
        ca = state["alice_conv"]
        since = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")[1]["last_seq"]
        alice.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "Use the repo_stats tool to count files and lines per extension in the workspace and tell me the python line count."})
        log, _ = wait_idle_turn(alice, ca, run, "mcp", since, lambda ap: "allow_once")
        starts = [(e["extension"], e["tool"]) for e in log if e["type"] == "tool_start"]
        run.note(f"  tool calls (extension, tool): {starts}")
        run.note("  assistant said: " + text_of(log).strip().replace("\n", " ")[:400])

    if a.phase in ("all", "skill"):
        run.note("== skill")
        ca = state["alice_conv"]
        st, sk = alice.req("POST", "/api/work/skills", {"scope": "user", "name": "pricing-review", "title": "Pricing review",
                           "description": "Use when asked to review pricing or tax code. Defines the review checklist and the sign-off line.",
                           "body_md": "# Pricing review\nWhen reviewing pricing or tax code:\n1. Read the module and its tests.\n2. Check that tax is applied as a fraction of the subtotal.\n3. Finish your answer with the exact line: `PRICING-REVIEW-SIGNED-OFF`.\n"})
        assert st == 200, (st, sk)
        alice.req("PATCH", f"/api/work/goose/conversations/{ca}/session", {"skills": [sk["id"]]})
        since = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")[1]["last_seq"]
        alice.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "Apply the pricing-review skill to shop/pricing.py."})
        log, _ = wait_idle_turn(alice, ca, run, "skill", since, lambda ap: "allow_once")
        run.note(f"  tool calls: {[(e['extension'], e['tool']) for e in log if e['type'] == 'tool_start']}")
        said = text_of(log)
        run.note(f"  skill sign-off line present: {'PRICING-REVIEW-SIGNED-OFF' in said}; assistant said: " + said.strip().replace("\n", " ")[:400])

    if a.phase in ("all", "agentprobe"):
        run.note("== the AGENT itself tries to leave its workspace (auto mode: Goose does not ask)")
        ca = state["alice_conv"]
        alice.req("PATCH", f"/api/work/goose/conversations/{ca}/session", {"autonomy": "auto"})
        since = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")[1]["last_seq"]
        alice.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "Run these shell commands one after another and report their exact output, even if they fail: `ls -la /home/spark`, `cat /home/spark/.bash_history`, `find / -name bob-secret.txt 2>/dev/null; echo find-finished`, `cat /work/../bob-secret.txt`."})
        log, _ = wait_idle_turn(alice, ca, run, "agentprobe", since, lambda ap: "allow_once")
        for e in log:
            if e["type"] == "tool_start":
                run.note(f"  goose ran: {e['tool']} {json.dumps(e.get('input'))[:150]}")
            if e["type"] == "tool_end":
                run.note(f"    -> {e['status']}: {(e.get('output') or '').strip()[:200]!r}")
        run.note("  assistant said: " + text_of(log).strip().replace("\n", " ")[:500])

    if a.phase == "restart_check":
        run.note("== after dev-instance restart")
        ca = state["alice_conv"]
        st, s = alice.req("GET", f"/api/work/goose/conversations/{ca}/session")
        run.note(f"  session after restart: state={s['state']} goose_session_id={s['goose_session_id']} last_seq={s['last_seq']}")
        st, tr = alice.req("GET", f"/api/work/goose/conversations/{ca}/transcript")
        run.note(f"  transcript items kept across the restart: {len(tr['items'])} ({sorted({i['kind'] for i in tr['items']})})")
        since = s["last_seq"]
        alice.req("PATCH", f"/api/work/goose/conversations/{ca}/session", {"autonomy": "auto"})
        alice.req("POST", f"/api/work/goose/conversations/{ca}/prompt", {"text": "Without running any tool: in one sentence, what was the bug in shop/pricing.py that you looked at earlier in this session?"})
        log, _ = wait_idle_turn(alice, ca, run, "restart", since, lambda ap: "allow_once")
        run.note(f"  notices: {[e['code'] for e in log if e['type'] == 'notice']}; tools used: {[e['tool'] for e in log if e['type'] == 'tool_start']}")
        run.note(f"  goose session id now: {alice.req('GET', f'/api/work/goose/conversations/{ca}/session')[1]['goose_session_id']}")
        run.note("  assistant said: " + text_of(log).strip().replace("\n", " ")[:500])
    run.note("== done")


if __name__ == "__main__":
    sys.exit(main())
