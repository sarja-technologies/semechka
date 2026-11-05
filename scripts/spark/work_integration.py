#!/usr/bin/env python3
"""Real integration run of the Work Mode runtime against the rootless Docker daemon. No mocks: every check below talks to
real containers created from the real `semechka-work:1` image through the real `DockerRuntime` (the same code Semechka runs).

Run it ON THE HOST that runs the sandbox daemon, with a Python that can import `semechka` (the staging venv does):

    SBX_HOST=unix:///run/user/1000/semechka-sandbox.sock \\
      ~/semechka-deploy/staging/app/venv/bin/python work_integration.py

It only ever touches containers and volumes it created itself (names `work-it...`, labelled with a private instance id) and
removes them at the end, then proves nothing named work-it* is left. Existing images and other containers are never touched.
Exit status 0 when every check passed. Prints evidence for each check; contains no secrets.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
import types

from semechka import work_svc  # noqa: F401  (import first: work_svc and work_tools import each other)
from semechka import sandbox_svc, work_runtime, work_tools
from semechka.work_runtime import ContainerSpec, DockerRuntime, PathRefused

HOST = os.environ.get("SBX_HOST", "unix:///run/user/1000/semechka-sandbox.sock")
IMAGE = os.environ.get("WORK_IMAGE", "semechka-work:1")
INSTANCE = "itest" + secrets.token_hex(4)
RESULTS: list[tuple[str, bool, str]] = []
MB = 1048576


def check(name: str, ok: bool, evidence: str = "") -> bool:
    RESULTS.append((name, bool(ok), evidence))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f"  -- {evidence}" if evidence else ""), flush=True)
    return bool(ok)


def sid() -> str:
    return "ws_it" + secrets.token_hex(8)


def spec(session_id: str, **kw) -> ContainerSpec:
    return ContainerSpec(session_id, image=IMAGE, instance=INSTANCE, **{"memory_mb": 512, "cpus": 1.0, "pids": 128, **kw})


def docker(*args: str) -> subprocess.CompletedProcess:
    return sandbox_svc._docker(HOST, *args, timeout=60)


def leftovers() -> list[str]:
    names = []
    for a in (("ps", "-a", "--format", "{{.Names}}"), ("volume", "ls", "--format", "{{.Name}}")):
        r = docker(*a)
        names += [n for n in r.stdout.decode().split() if n.startswith("work-it")]
    return names


def tail(s: str, n: int = 160) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[:n] + "..."


def main() -> int:
    rt = DockerRuntime(HOST)
    ok, why = rt.image_ready(IMAGE)
    check("image present on the daemon (never pulled)", ok, why)
    if not ok:
        return 1
    created: list[str] = []
    try:
        run(rt, created)
    finally:
        for n in created:
            try:
                rt.remove(n, volume=True)
            except work_runtime.RuntimeError_ as e:
                print("cleanup problem:", e)
    left = leftovers()
    check("cleanup removed the containers AND volumes; no work-it* leftovers", not left, f"leftovers={left}")
    failed = [n for n, ok_, _ in RESULTS if not ok_]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


def run(rt: DockerRuntime, created: list[str]) -> None:
    # ---- the container, exactly as designed ----------------------------------------------------------------
    s1 = sid()
    sp = spec(s1)
    created.append(sp.name)
    t0 = time.time()
    rt.create(sp)
    check("create: container + named volume start", rt.state(sp.name) == "running", f"{time.time() - t0:.1f}s")
    n = sp.name
    insp = docker("inspect", "-f", "{{json .HostConfig}}", n).stdout.decode()
    import json
    hc = json.loads(insp)
    sec = hc.get("SecurityOpt") or []
    check("flags: network none, read-only root, cap-drop ALL, no-new-privileges, init",
          hc["NetworkMode"] == "none" and hc["ReadonlyRootfs"] is True and hc["CapDrop"] == ["ALL"] and any("no-new-privileges" in x for x in sec)
          and hc["Init"] is True, f"net={hc['NetworkMode']} ro={hc['ReadonlyRootfs']} capdrop={hc['CapDrop']} secopt={sec} init={hc['Init']}")
    check("flags: memory 512m = swap, pids 128, cpus 1, no privileged, no extra binds",
          hc["Memory"] == 512 * MB and hc["MemorySwap"] == 512 * MB and hc["PidsLimit"] == 128 and hc["NanoCpus"] == 10**9
          and not hc["Privileged"] and hc["Binds"] == [f"{n}:/work"], f"mem={hc['Memory']} swap={hc['MemorySwap']} pids={hc['PidsLimit']} binds={hc['Binds']}")
    labels = json.loads(docker("inspect", "-f", "{{json .Config.Labels}}", n).stdout.decode())
    check("labels: semechka.work and the instance id", labels.get("semechka.work") == s1 and labels.get("semechka.work.instance") == INSTANCE, str(labels))
    who = rt.exec(n, "id -u; id -g; echo HOME=$HOME; pwd")
    check("runs as 65534:65534 in /work with HOME=/work/.home", who.stdout.split() == ["65534", "65534", "HOME=/work/.home", "/work"], tail(who.stdout))

    # ---- files: write / read / edit -------------------------------------------------------------------------
    h = rt.put_file(n, "notes/a.txt", b"line one\nline two\n")
    data, head = rt.get_file(n, "notes/a.txt")
    check("write + read a file through the container", data == b"line one\nline two\n" and head["size"] == 18 and h["size"] == 18, f"sha={h['sha256'][:12]}")
    text = data.decode().replace("two", "TWO")
    rt.put_file(n, "notes/a.txt", text.encode())
    check("edit (read-modify-write) a file", rt.get_file(n, "notes/a.txt")[0] == b"line one\nline TWO\n")
    check("list a folder", [e.name for e in rt.list(n, "notes")] == ["a.txt"])
    found = rt.search(n, "TWO", "")["matches"]
    check("search file contents", found == [{"path": "notes/a.txt", "line": 2, "text": "line TWO"}], str(found))

    # ---- shell + python -------------------------------------------------------------------------------------
    r = rt.exec(n, "echo out; echo err >&2; exit 5")
    check("shell: exit code, stdout, stderr, duration", (r.exit, r.stdout, r.stderr) == (5, "out\n", "err\n") and r.duration_s < 5, f"exit={r.exit} {r.duration_s}s")
    r = rt.exec(n, argv=["python3", "-u", "-c", "import sys; print(sum(range(101))); sys.exit(0)"])
    check("python: runs code", r.exit == 0 and r.stdout.strip() == "5050", tail(r.stdout))
    r = rt.exec(n, "(sleep 77.7 &) ; sleep 77.7", timeout_s=2)
    time.sleep(0.5)
    left_ = rt.exec(n, "pgrep -fc '[s]leep 77.7' || true").stdout.strip()
    check("timeout kills the command AND everything it started", r.timed_out and left_ == "0", f"timed_out={r.timed_out} survivors={left_}")
    r = rt.exec(n, "python3 -c \"print('H'*10 + 'x'*3000000 + 'T'*10)\"", max_output=2000)
    check("output cap keeps head and tail, counts the rest", r.stdout.startswith("HHHH") and r.stdout.rstrip().endswith("TTTT") and len(r.stdout) <= 2001
          and r.truncated_stdout > 2_900_000, f"kept={len(r.stdout)} dropped={r.truncated_stdout}")

    # ---- git ------------------------------------------------------------------------------------------------
    r = rt.exec(n, "git init -q repo && cd repo && echo hi > f.txt && git add f.txt && git commit -qm 'first commit' && git log --oneline && git status --short | wc -l")
    check("git init / add / commit / log inside the container", r.exit == 0 and "first commit" in r.stdout, tail(r.stdout))
    hook = rt.exec(n, "cd repo && printf '#!/bin/sh\\necho HOOK-RAN >&2\\n' > .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit && echo 2 >> f.txt && "
                      "git add f.txt && git commit -qm second && git log --oneline | wc -l")
    check("git hooks are disabled by default (a pre-commit hook in the repo does not run)", hook.exit == 0 and "HOOK-RAN" not in hook.stderr
          and hook.stdout.strip() == "2", tail(hook.stdout + hook.stderr))
    pre = rt.exec(n, "cd repo && echo 3 >> f.txt && git add f.txt && git -c core.hooksPath=.git/hooks commit -qm third 2>&1 | tail -1")
    check("(precondition) the same hook DOES run when hooks are switched back on, so the check above means something", "HOOK-RAN" in pre.stdout, tail(pre.stdout))

    # ---- media tools ------------------------------------------------------------------------------------------
    r = rt.exec(n, "ffmpeg -loglevel error -y -f lavfi -i testsrc=duration=1:size=64x64:rate=5 clip.mp4 && ffprobe -v error -show_entries stream=codec_name,width,height "
                   "-of csv=p=0 clip.mp4", timeout_s=60)
    check("ffmpeg encodes and ffprobe reads a clip", r.exit == 0 and "64,64" in r.stdout.replace("\n", ","), tail(r.stdout + r.stderr))
    r = rt.exec(n, argv=["python3", "-c", "from PIL import Image; im=Image.new('RGB',(40,30),'red'); im.save('t.png'); print(Image.open('t.png').size)"])
    check("PIL writes and reads an image", r.exit == 0 and "(40, 30)" in r.stdout, tail(r.stdout + r.stderr))
    r = rt.exec(n, argv=["python3", "-c", "import numpy, pandas, cv2, matplotlib, requests, yaml, openpyxl, docx; print('libs ok')"])
    check("data/science libraries import", r.stdout.strip() == "libs ok", tail(r.stdout + r.stderr))
    r = rt.exec(n, "node -e 'console.log(6*7)' && rg --version | head -1 && jq -n '1+1' && convert -version | head -1", timeout_s=60)
    check("node, ripgrep, jq, ImageMagick present", r.exit == 0 and "42" in r.stdout, tail(r.stdout))

    # ---- persistence ------------------------------------------------------------------------------------------
    rt.put_file(n, "persist.txt", b"survives")
    rt.stop(n)
    check("stop: container stopped, volume kept", rt.state(n) == "stopped", rt.state(n))
    try:
        rt.exec(n, "true")
        check("a stopped container refuses commands", False)
    except work_runtime.RuntimeError_ as e:
        check("a stopped container refuses commands", True, tail(str(e), 100))
    rt.start(n)
    check("workspace persists across container stop/start", rt.get_file(n, "persist.txt")[0] == b"survives" and rt.get_file(n, "notes/a.txt")[0].startswith(b"line one"))
    rt.put_file(n, "after-restart.txt", b"adopt me")
    # a new process: a fresh runtime object, reconcile by label, no recreation
    rt2 = DockerRuntime(HOST)
    rep = rt2.reconcile(INSTANCE, {s1: n}, remove_orphans=True)
    rt2.create(spec(s1))   # adopting must not recreate
    check("a restarted Semechka adopts the running container by label (no orphan removal, no recreate)",
          n in rep["seen"] and not rep["removed"] and rep["states"].get(s1) == "running" and rt2.get_file(n, "after-restart.txt")[0] == b"adopt me", str(rep))
    st_ = docker("inspect", "-f", "{{.State.StartedAt}}", n).stdout.decode().strip()
    rt2.stop(n)
    rt2.remove(n, volume=False)
    check("removing the container alone keeps the volume", rt2.state(n) == "absent" and n in [m["name"] for m in rt2.list_managed(INSTANCE)], "")
    rt2.create(spec(s1))
    check("a recreated container finds the old workspace on its volume", rt2.get_file(n, "persist.txt")[0] == b"survives" and rt2.get_file(n, "after-restart.txt")[0] == b"adopt me", st_)

    # ---- no network -------------------------------------------------------------------------------------------
    net = rt.exec(n, argv=["python3", "-c", r"""
import socket
out = {}
for host in ("example.com", "1.1.1.1", "localhost"):
    try:
        out[host] = socket.gethostbyname(host)
    except OSError as e:
        out[host] = "ERR " + type(e).__name__
print(out)
res = []
for addr in (("1.1.1.1", 53), ("8.8.8.8", 443), ("192.168.20.193", 22), ("127.0.0.1", 8752), ("10.0.0.1", 80)):
    s = socket.socket(); s.settimeout(3)
    try:
        s.connect(addr); res.append((addr, "CONNECTED"))
    except OSError as e:
        res.append((addr, type(e).__name__))
    finally:
        s.close()
print(res)
print("IFACES", sorted(l.split(":")[0].strip() for l in open('/proc/net/dev').read().splitlines()[2:]))
"""], timeout_s=60)
    lines = net.stdout.splitlines()
    check("no network: DNS resolution fails", "ERR" in lines[0] and "example.com': 'ERR" in lines[0], tail(lines[0]))
    check("no network: TCP to the internet, the LAN, the host and loopback ports all fail",
          "CONNECTED" not in lines[1] and lines[1].count("OSError") + lines[1].count("Error") >= 5, tail(lines[1], 300))
    ifaces = [ln for ln in net.stdout.splitlines() if ln.startswith("IFACES")]
    check("no network: the only interface is loopback", ifaces == ["IFACES ['lo']"], str(ifaces))
    web = rt.exec(n, argv=["python3", "-c", "import urllib.request\ntry:\n    urllib.request.urlopen('https://example.com', timeout=6); print('REACHED')\n"
                                              "except Exception as e:\n    print('FAILED', type(e).__name__, getattr(e, 'reason', ''))"], timeout_s=60)
    pip = rt.exec(n, "pip download requests -d /tmp/x 2>&1 | tail -2", timeout_s=90)
    check("no network: an HTTPS request and pip both fail", web.stdout.startswith("FAILED") and "REACHED" not in web.stdout and "No matching distribution" in pip.stdout,
          tail(web.stdout + " | " + pip.stdout, 260))

    # ---- isolation: no docker.sock, no host paths -----------------------------------------------------------
    iso = rt.exec(n, "ls /var/run/docker.sock /run/docker.sock /var/run 2>&1 | head -5; echo ---; cat /proc/self/mountinfo; echo ---; env; echo ---; "
                     "ls / ; echo ---; touch /usr/x 2>&1; touch /etc/x 2>&1; echo ---; ls -ld /tmp; echo ---; (echo x > /tmp/t.sh; chmod +x /tmp/t.sh; /tmp/t.sh) 2>&1 | head -2",
                  timeout_s=60)
    out = iso.stdout
    check("no docker.sock inside", "docker.sock" not in out.split("---")[0].replace("No such file or directory", "") or "No such file" in out.split("---")[0], tail(out.split("---")[0]))
    mounts = out.split("---")[1]
    host_markers = [m for m in ("/home/", "/run/user", "fylume", "semsbx") if m in mounts]
    disk_mounts = sorted(ln.split()[4] for ln in mounts.splitlines() if " - ext4" in ln or " - xfs" in ln or " - btrfs" in ln)
    check("the only disk-backed mounts are the /work volume, docker's three per-container files and the init binary (no host directory is mounted)",
          set(disk_mounts) == {"/work", "/etc/resolv.conf", "/etc/hostname", "/etc/hosts", "/usr/sbin/docker-init"}, f"mount points={disk_mounts}")
    leak = [ln.split()[3] for ln in mounts.splitlines() if " - ext4" in ln and ln.split()[4] == "/work"]
    check("KNOWN LIMIT (informational): /proc/self/mountinfo shows the volume's source path inside the daemon's data directory",
          True, f"markers={host_markers}; /work root={leak}")
    envs = out.split("---")[2]
    check("environment holds no host information", not any(k in envs for k in ("DOCKER_HOST", "SEMECHKA", "TOKEN", "KEY=", "fylume", "/run/user")), tail(envs, 200))
    ro = out.split("---")[4]
    check("root filesystem is read-only; /tmp is noexec", ro.count("Read-only file system") >= 2, tail(ro))
    check("/tmp refuses to execute files (noexec)", "Permission denied" in out.split("---")[6] or "denied" in out.split("---")[6].lower(), tail(out.split("---")[6]))

    # ---- path escapes -----------------------------------------------------------------------------------------
    for label, op in [("get ../../etc/passwd", lambda: rt.get_file(n, "../../etc/passwd")), ("put ../x", lambda: rt.put_file(n, "../x", b"x")),
                      ("list ..", lambda: rt.list(n, "..")), ("put a/../../x", lambda: rt.put_file(n, "a/../../x", b"x"))]:
        try:
            op()
            check(f"path escape refused: {label}", False, "no error")
        except PathRefused as e:
            check(f"path escape refused: {label}", True, str(e))
    rt.exec(n, "ln -s / rootlink; ln -s /etc/passwd pw; ln -s /etc etclink; ln -s . inside; echo ok > real.txt; ln -s /nonexistent/new dangling")
    for label, op in [("read via link to /etc/passwd", lambda: rt.get_file(n, "pw")), ("read via dir link /", lambda: rt.get_file(n, "rootlink/etc/passwd")),
                      ("list via dir link /etc", lambda: rt.list(n, "etclink")), ("write through link to /", lambda: rt.put_file(n, "rootlink/tmp/pwn", b"x")),
                      ("write through dangling link", lambda: rt.put_file(n, "dangling", b"x")),
                      ("search through dir link", lambda: rt.search(n, "root", "etclink"))]:
        try:
            op()
            check(f"symlink escape refused: {label}", False, "no error")
        except PathRefused as e:
            check(f"symlink escape refused: {label}", True, str(e))
    check("a symlink that stays inside /work is fine", rt.get_file(n, "inside/real.txt")[0] == b"ok\n")
    rt.delete(n, "rootlink")
    check("deleting a link removes the link, not the target", rt.exec(n, "test -e /etc/passwd && echo target-intact").stdout.strip() == "target-intact"
          and rt.stat(n, "rootlink")["exists"] is False)
    try:
        rt.delete(n, "")
        check("the workspace root cannot be deleted", False)
    except PathRefused as e:
        check("the workspace root cannot be deleted", True, str(e))

    # ---- quota ------------------------------------------------------------------------------------------------
    rt.delete(n, "", allow_root=True)
    fake_st = types.SimpleNamespace(work_runtime=rt)
    w = work_tools.Wk(fake_st, s1, n, {"workspace_mb": 64, "memory_mb": 512, "output_chars": 12000, "tool_timeout_s": 120, "artifact_mb": 64},
                      "run", "org", "user", 1, "none", None)
    big = rt.exec(n, "head -c 70000000 /dev/zero > big.bin", timeout_s=60)
    used = rt.usage_bytes(n)
    try:
        work_tools.check_quota(w, n, 10, cleanup=False)
        check("quota: a write over the limit is refused with the exact usage", False)
    except Exception as e:  # ToolError
        check("quota: a write over the limit is refused with the exact usage", "workspace is full" in str(e) and f"{used / MB:.1f} MB used of 64 MB" in str(e), tail(str(e), 200))
    rt.delete(n, "big.bin")
    check("quota: cleanup frees space again", rt.usage_bytes(n) < 5 * MB, f"{rt.usage_bytes(n) / MB:.2f} MB")
    t0 = time.time()
    r = rt.exec(n, "for i in $(seq 1 60); do head -c 10000000 /dev/zero >> grow.bin; sleep 0.5; done", quota_bytes=int(64 * MB * 1.05), timeout_s=60)
    check("quota: a command that grows the workspace past the limit is killed while it runs", r.quota_killed and not r.timed_out and time.time() - t0 < 30,
          f"killed after {time.time() - t0:.1f}s, usage {rt.usage_bytes(n) / MB:.0f} MB")
    rt.delete(n, "grow.bin")
    r = rt.exec(n, "head -c 3000000 /dev/zero > one; ls -l one | awk '{print $5}'", fsize_mb=1)
    check("a single file cannot exceed the per-file limit (RLIMIT_FSIZE)", r.stdout.strip() == str(1 * MB), tail(r.stdout))

    # ---- artifacts folder -------------------------------------------------------------------------------------
    rt.exec(n, "mkdir -p artifacts && echo '2 3 5 7' > artifacts/primes.txt")
    listing = rt.search(n, "", "artifacts", names_only=True)["matches"]
    check("files under /work/artifacts are listed with size and mtime", listing and listing[0]["path"] == "artifacts/primes.txt" and listing[0]["size"] == 8, str(listing))

    # ---- interrupt (cancel) ---------------------------------------------------------------------------------------
    import threading
    box = {}
    th = threading.Thread(target=lambda: box.update(r=rt.exec(n, "sleep 120", timeout_s=130)))
    th.start()
    time.sleep(2)
    t0 = time.time()
    rt.interrupt(n)
    th.join(30)
    check("interrupt (cancel) stops a running command at once and reports it", not th.is_alive() and box["r"].interrupted and time.time() - t0 < 10,
          f"stopped in {time.time() - t0:.1f}s; container still {rt.state(n)}")
    check("the container survives an interrupt and still works", rt.exec(n, "echo alive").stdout.strip() == "alive")

    # ---- resource limits (own small containers) ---------------------------------------------------------------
    s2 = sid()
    sp2 = spec(s2, memory_mb=256, pids=64)
    created.append(sp2.name)
    rt.create(sp2)
    n2 = sp2.name
    r = rt.exec(n2, argv=["python3", "-c", "b = bytearray(); [b.extend(bytes(16 * 1024 * 1024)) for _ in range(40)]; print('allocated', len(b) >> 20, 'MB')"], timeout_s=60)
    check("memory limit kills an over-allocation (256 MB limit, 640 MB requested)", (r.oom_killed or r.signal == "SIGKILL" or r.exit in (-9, 137)) and "allocated" not in r.stdout,
          f"exit={r.exit} signal={r.signal} oom_killed={r.oom_killed} stdout={tail(r.stdout)} stderr={tail(r.stderr, 80)}")
    check("the container is still alive after the OOM kill", rt.state(n2) == "running" and rt.exec(n2, "echo alive").stdout.strip() == "alive")
    r = rt.exec(n2, "ok=0; fail=0; for i in $(seq 1 200); do if sleep 30 & then ok=$((ok+1)); else fail=$((fail+1)); fi; done 2>&1; echo ok=$ok; jobs | wc -l; "
                    "ps -e | wc -l; sleep 1", timeout_s=20)
    pids_seen = rt.exec(n2, "cat /sys/fs/cgroup/pids.max /sys/fs/cgroup/pids.peak 2>/dev/null || cat /sys/fs/cgroup/pids/pids.max 2>/dev/null").stdout.split()
    fork_fail = ("retry" in (r.stderr + r.stdout).lower() or "fork" in (r.stderr + r.stdout).lower() or "resource temporarily" in (r.stderr + r.stdout).lower())
    count = int(rt.exec(n2, "ps -e | wc -l").stdout.strip() or 0)
    check("pids limit stops a fork bomb (64 processes max)", fork_fail and count <= 66, f"fork errors={fork_fail} processes now={count} cgroup={pids_seen}")
    rt.interrupt(n2)
    time.sleep(1)
    check("the container recovers after the process flood is killed", rt.exec(n2, "echo alive").stdout.strip() == "alive")
    mem = docker("stats", "--no-stream", "--format", "{{.MemUsage}}|{{.PIDs}}", n2).stdout.decode().strip()
    check("live usage is readable (docker stats)", "|" in mem, mem)


if __name__ == "__main__":
    sys.exit(main())
