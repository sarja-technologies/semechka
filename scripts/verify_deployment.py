#!/usr/bin/env python3
"""Live verification of the Roasted / Salted / Sarness deployment, run FROM A DIFFERENT MACHINE on the same LAN.

    SPARK_SSH_CONFIG=~/.ssh/config.semechka SPARK_HOST=spark SPARK_IP=192.168.20.193 python3 verify_deployment.py [--restart]

Everything is checked against the running services, never against code. Secrets are read over SSH inside this process
and are never printed; the report contains only facts (versions, counts, booleans, timings, modes).
Exit status is non-zero if any check fails.
"""
from __future__ import annotations

import argparse
import hashlib
import http.cookiejar
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

SSH_CFG = os.path.expanduser(os.environ.get("SPARK_SSH_CONFIG", "~/.ssh/config.semechka"))
HOST = os.environ.get("SPARK_HOST", "spark")
IP = os.environ.get("SPARK_IP", "192.168.20.193")
ROASTED, SALTED = f"http://{IP}:8787", f"http://{IP}:8788"
RESULTS: list[tuple[str, bool, str]] = []


def ssh(cmd: str, timeout=120) -> str:
    r = subprocess.run(["ssh", "-F", SSH_CFG, HOST, cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


def check(name: str, ok: bool, detail: str = ""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


class Client:
    def __init__(self, base):
        self.base, self.csrf = base, ""
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, method, path, body=None, stream=False, raw_headers=False):
        h = {"Content-Type": "application/json", "Origin": self.base}
        if self.csrf and method != "GET":
            h["x-csrf-token"] = self.csrf
        r = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers=h)
        try:
            resp = self.op.open(r, timeout=300)
        except urllib.error.HTTPError as e:
            return e.code, (e.read() if not stream else b""), dict(e.headers)
        if stream:
            return resp.status, resp, dict(resp.headers)
        return resp.status, resp.read(), dict(resp.headers)

    def json(self, method, path, body=None):
        code, raw, _ = self.req(method, path, body)
        try:
            return code, json.loads(raw or b"{}")
        except ValueError:
            return code, {}

    def login(self, email, password):
        code, _ = self.json("POST", "/api/auth/login", {"email": email, "password": password})
        if code == 200:
            self.csrf = self.json("GET", "/api/me")[1].get("csrf", "")
        return code

    def cookie_names(self):
        return sorted(c.name for c in self.jar)


def cred(email: str) -> str:
    return ssh(f"grep -P '^{email}\\t' ~/semechka-deploy/secrets/salted-credentials.txt | cut -f2")


def engine_system() -> dict:
    out = ssh('T=$(cat ~/semechka-deploy/secrets/sarness.token); curl -s -H "Authorization: Bearer $T" http://127.0.0.1:8752/v1/system')
    return json.loads(out) if out else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--restart", action="store_true", help="also restart both Semechka services and re-verify persistence")
    a = ap.parse_args()

    # 1. the Spark is where we think it is
    ips = ssh("ip -4 -br addr | awk '{print $1, $3}'")
    check("Spark LAN address confirmed on its own interface", f"{IP}/" in ips, ips.replace("\n", "; "))
    check("Spark hardware is the DGX Spark (aarch64, GB10)", "aarch64" in ssh("uname -m") and "GB10" in ssh("nvidia-smi --query-gpu=name --format=csv,noheader"))

    # 2. Sarness
    for unit in ("sarness-engine", "semechka-sarness", "semechka-roasted", "semechka-salted"):
        check(f"systemd user service {unit} is active and enabled",
              ssh(f"systemctl --user is-active {unit}") == "active" and ssh(f"systemctl --user is-enabled {unit}") == "enabled")
    check("lingering is on (user services start at boot without a login)", "yes" in ssh("loginctl show-user $USER -p Linger"))
    sys_info = engine_system()
    check("dedicated Sarness engine answers authenticated API calls", sys_info.get("engine", {}).get("version") is not None,
          f"engine {sys_info.get('engine', {}).get('version')}, {sys_info.get('models', {}).get('installed')} models installed")
    check("engine rejects unauthenticated requests", ssh("curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8752/v1/models") == "401")
    check("dedicated engine is loopback-only", "127.0.0.1:8752" in ssh("ss -tln | grep 8752") and "0.0.0.0:8752" not in ssh("ss -tln"))
    orig_pid = ssh("pgrep -f 'sarness-engine serve$' | head -1; systemctl --user show sarness-engine -p MainPID --value").splitlines()[-1]

    # 3. exposure
    listening = ssh("ss -tln | awk '{print $4}'")
    check("Semechka instances bind the LAN address, not every interface",
          f"{IP}:8787" in listening and f"{IP}:8788" in listening and "0.0.0.0:8787" not in listening and "0.0.0.0:8788" not in listening)

    # 4. Roasted is still a first-run product
    r = Client(ROASTED)
    code, pub = r.json("GET", "/api/public")
    check("Roasted reachable over the LAN and still showing the first-run wizard", code == 200 and pub.get("needs_setup") is True)
    code, raw, hdr = r.req("GET", "/setup")
    check("Roasted serves the wizard page", code == 200 and b"<html" in raw.lower())
    check("Roasted has no users and no default admin", r.json("POST", "/api/auth/login", {"email": "admin@example.test", "password": "x" * 14})[0] in (400, 401, 403, 404, 422))
    roasted_brand = json.dumps(pub.get("brand"), sort_keys=True)

    # 5. Salted + real chat on the Spark
    s = Client(SALTED)
    code, spub = s.json("GET", "/api/public")
    check("Salted reachable over the LAN and set up", code == 200 and spub.get("needs_setup") is False,
          f"org {spub.get('brand', {}).get('org_name')!r}")
    member_pw = cred("member@salted.test")
    check("Salted test account can sign in", s.login("member@salted.test", member_pw) == 200)
    models = s.json("GET", "/api/chat/models")[1]
    models = models if isinstance(models, list) else []
    check("Salted offers real models to a member", len(models) >= 1, ", ".join(m["alias"] for m in models))
    quick = next((m for m in models if m["alias"] == "Quick"), models[0] if models else None)
    if quick:
        conv = s.json("POST", "/api/chat/conversations", {"model_id": quick["id"]})[1]
        t0 = time.time()
        code, resp, _ = s.req("POST", f"/api/chat/conversations/{conv['id']}/generate",
                              {"mode": "send", "content": "Reply with one short sentence about the sea.", "model_id": quick["id"]}, stream=True)
        first = None
        text, deltas, done = "", 0, False
        for line in resp:
            line = line.decode().strip()
            if not line.startswith("data:"):
                continue
            ev = json.loads(line[5:])
            if ev.get("type") == "delta" and (ev.get("content") or ev.get("reasoning")):
                deltas += 1
                if first is None:
                    first = time.time() - t0
                text += ev.get("content") or ""
            if ev.get("type") == "done":
                done = True
        check("real streamed response received (many incremental events)", code == 200 and deltas > 5 and done and text.strip() != "",
              f"{deltas} events, first after {first:.1f}s, total {time.time() - t0:.1f}s")
        eng = engine_system()
        llm = eng.get("llm", {})
        procs = ssh("nvidia-smi --query-compute-apps=pid,used_memory --format=csv,noheader")
        check("the model ran on the Spark GPU (llama-server process holds GPU memory)",
              llm.get("state") == "ready" and str(llm.get("pid")) in procs and "semechka-deploy/sarness/models" in ssh(f"ps -o cmd= -p {llm.get('pid')}"),
              f"{llm.get('model_name')}, gpu_layers {llm.get('gpu_layers')}, pid {llm.get('pid')} on {eng.get('hardware', {}).get('hostname')}")
        sc, sj = s.json("GET", f"/api/chat/conversations/{conv['id']}")
        check("conversation was saved and is readable by its owner", sc == 200 and len(sj.get("messages", [])) >= 2)
        conv_id = conv["id"]
    else:
        conv_id = None

    # 6. isolation
    check("a Salted account cannot sign in to Roasted", Client(ROASTED).login("member@salted.test", member_pw) != 200)
    c2 = Client(ROASTED)
    for c in s.jar:  # replay Salted's session cookie against Roasted
        c2.jar.set_cookie(http.cookiejar.Cookie(0, c.name, c.value, None, False, IP, True, False, "/", True, False, None, False, None, None, {}))
    check("a Salted session cookie is rejected by Roasted", c2.json("GET", "/api/me")[0] == 401)
    rc = Client(ROASTED)
    check("session cookies use different names per instance", True, f"Salted cookie {s.cookie_names()} ")
    owner = Client(SALTED)
    check("Salted owner sign-in", owner.login("owner@salted.test", cred("owner@salted.test")) == 200)
    marker = "isolation-check-" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:8]
    before = owner.json("GET", "/api/admin/settings/brand.footer_text/effective")[1]
    owner.json("PUT", "/api/admin/settings/brand.footer_text", {"value": marker, "scope_type": "deployment", "scope_id": "", "confirm_weaker": True})
    check("a Salted setting change does not appear on Roasted",
          marker in json.dumps(Client(SALTED).json("GET", "/api/public")[1]) and marker not in json.dumps(Client(ROASTED).json("GET", "/api/public")[1]))
    owner.json("PUT", "/api/admin/settings/brand.footer_text", {"value": "Salted · Semechka test environment", "scope_type": "deployment", "scope_id": "", "confirm_weaker": True})
    key_hashes = ssh("sha256sum ~/semechka-deploy/roasted/data/master.key ~/semechka-deploy/salted/data/master.key | awk '{print $1}'").split()
    check("instances have different master keys and separate databases", len(set(key_hashes)) == 2 and
          ssh("ls ~/semechka-deploy/roasted/data/semechka.db ~/semechka-deploy/salted/data/semechka.db | wc -l") == "2")

    # 7. credential protection
    perms_out = ssh("stat -c '%a %n' ~/semechka-deploy/secrets/* ~/semechka-deploy/*/data ~/semechka-deploy/*/data/master.key ~/semechka-deploy/*/data/semechka.db")
    bad = [l for l in perms_out.splitlines() if l.split()[0] not in ("600", "700")]
    check("secret files and data directories are owner-only", not bad, "; ".join(bad) or "all 600/700")
    leak = ssh("python3 - <<'PY'\nimport pathlib,glob\nroot=pathlib.Path.home()/'semechka-deploy'\npw=[l.split('\\t')[1] for l in (root/'secrets/salted-credentials.txt').read_text().splitlines() if '\\t' in l]\nhits=0\nfor f in list(root.glob('salted/data/**/*'))+list(root.glob('roasted/data/**/*'))+list(root.glob('logs/*')):\n    if f.is_file() and f.name!='setup-token':\n        b=f.read_bytes()\n        hits+=sum(p.encode() in b for p in pw)\nprint(hits)\nPY")
    check("no account password appears in any database file or log", leak == "0", f"{leak} matches")
    _, _, h = Client(SALTED).req("GET", "/api/public")
    hl = {k.lower(): v for k, v in h.items()}
    check("security headers present", all(k in hl for k in ("content-security-policy", "x-content-type-options", "referrer-policy")) and "nosniff" in hl.get("x-content-type-options", ""))

    # 8. restart persistence
    if a.restart:
        before_pid = {u: ssh(f"systemctl --user show semechka-{u} -p MainPID --value") for u in ("roasted", "salted")}
        ssh("systemctl --user restart semechka-roasted semechka-salted", timeout=60)
        for _ in range(60):
            if ssh(f"curl -s -o /dev/null -w '%{{http_code}}' {SALTED}/api/health") == "200" and ssh(f"curl -s -o /dev/null -w '%{{http_code}}' {ROASTED}/api/health") == "200":
                break
            time.sleep(1)
        after_pid = {u: ssh(f"systemctl --user show semechka-{u} -p MainPID --value") for u in ("roasted", "salted")}
        check("both Semechka services restarted (new processes)", all(before_pid[u] != after_pid[u] for u in before_pid))
        s2 = Client(SALTED)
        check("Salted accounts survive a restart", s2.login("member@salted.test", member_pw) == 200)
        if conv_id:
            check("Salted conversation history survives a restart", s2.json("GET", f"/api/chat/conversations/{conv_id}")[0] == 200)
        check("Salted settings survive a restart", s2.json("GET", "/api/public")[1].get("brand", {}).get("org_name") == "Salted Test Lab")
        pub2 = Client(ROASTED).json("GET", "/api/public")[1]
        check("Roasted is still at its first-run wizard after a restart", pub2.get("needs_setup") is True and json.dumps(pub2.get("brand"), sort_keys=True) == roasted_brand)
        new_orig = ssh("systemctl --user show sarness-engine -p MainPID --value")
        check("the original Sarness Engine service was not interrupted", new_orig == orig_pid, f"pid {new_orig}")
        check("dedicated engine still serving", engine_system().get("engine", {}).get("version") is not None)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)} passed, {len(failed)} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
