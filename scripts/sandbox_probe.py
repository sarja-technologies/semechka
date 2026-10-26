"""Run ON the Spark: hostile jobs against the REAL sandbox. Every check prints PASS/FAIL with evidence."""
import json
import sys
import time

sys.path.insert(0, sys.argv[1])  # directory containing sandbox_svc.py
import sandbox_svc as S

import os

HOST, IMG = os.environ.get("SBX_HOST", "unix:///home/semsbx/run/docker.sock"), "semechka-sandbox:1"  # SBX_HOST: probe through the service proxy
LIM = S.Limits(memory_mb=512, timeout_s=12, pids=64, work_mb=64, tmp_mb=16, max_out_mb=8, max_file_mb=8)
results = []
def check(name, ok, evidence=""):
    results.append(ok); print(("PASS " if ok else "FAIL ") + name + (f"  [{evidence}]" if evidence else ""))
def py(code, inputs=None, lim=LIM):
    return S.run(HOST, IMG, {"mode": "python", "code": code}, inputs or {}, lim, job_label="probe")

ok, detail = S.available(HOST, IMG); check("image available", ok, detail)
r = py("import os,json\nopen(os.environ['OUT']+'/r.txt','w').write('hello '+open(os.environ['IN']+'/in.txt').read())\nprint('uid',os.getuid())", {"in.txt": b"world"})
check("python runs; input in, output out", r.exit == 0 and r.files.get("r.txt") == b"hello world" and "uid 65534" in r.stdout, f"stdout={r.stdout.strip()!r} files={list(r.files)}")

r = py('''
import socket, json
res = {}
for label, addr in [("lan-ssh", ("192.168.20.193", 22)), ("host-loopback-engine", ("127.0.0.1", 8752)), ("semechka", ("192.168.20.193", 8787)),
                    ("internet-ip", ("1.1.1.1", 443)), ("metadata", ("169.254.169.254", 80))]:
    s = socket.socket(); s.settimeout(2)
    try: s.connect(addr); res[label] = "CONNECTED"
    except Exception as e: res[label] = type(e).__name__
try: socket.getaddrinfo("example.com", 443); res["dns"] = "RESOLVED"
except Exception as e: res["dns"] = type(e).__name__
print(json.dumps(res)); print(open("/proc/net/dev").read())
''')
res = json.loads(r.stdout.splitlines()[0])
ifaces = [l.split(":")[0].strip() for l in r.stdout.splitlines()[3:] if ":" in l]
check("no network of any kind", all(v != "CONNECTED" and v != "RESOLVED" for v in res.values()) and set(ifaces) <= {"lo"}, f"{res} ifaces={ifaces}")

r = py('''
import os, json
res = {}
def attempt(label, fn):
    try: fn(); res[label] = "WROTE"
    except Exception as e: res[label] = type(e).__name__
attempt("etc", lambda: open("/etc/pwned", "w").write("x"))
attempt("usr", lambda: open("/usr/pwned", "w").write("x"))
attempt("root", lambda: open("/pwned", "w").write("x"))
attempt("opt-runner", lambda: open("/opt/semechka/runner.py", "a").write("x"))
attempt("work-ok", lambda: open("/work/ok", "w").write("x"))
attempt("tmp-exec", lambda: (open("/tmp/x.sh", "w").write("#!/bin/sh\\necho hi\\n"), os.chmod("/tmp/x.sh", 0o755), os.execv("/tmp/x.sh", ["x"])))
print(json.dumps(res))
''')
res = json.loads(r.stdout.splitlines()[0])
check("read-only root filesystem; image code immutable", all(res[k] != "WROTE" for k in ("etc", "usr", "root", "opt-runner")), str(res))
check("scratch is writable but not executable", res["work-ok"] == "WROTE" and res["tmp-exec"] != "WROTE", str(res))

r = py('''
import os, json
st = dict(l.split(":\\t", 1) for l in open("/proc/self/status").read().splitlines() if ":\\t" in l)
mounts = open("/proc/mounts").read()
print(json.dumps({"uid": os.getuid(), "gid": os.getgid(), "CapEff": st["CapEff"].strip(), "CapBnd": st["CapBnd"].strip(), "NoNewPrivs": st["NoNewPrivs"].strip(),
  "seccomp": st["Seccomp"].strip(), "docker_sock": os.path.exists("/var/run/docker.sock"), "run_docker": os.path.exists("/run/docker.sock"),
  "host_mounts": [l.split()[1] for l in mounts.splitlines() if l.split()[1].startswith(("/home", "/var/lib", "/mnt", "/media", "/root"))],
  "env_keys": sorted(os.environ), "pid1": open("/proc/1/cmdline").read().replace("\\0", " ")[:80],
  "proc_count": len([d for d in os.listdir("/proc") if d.isdigit()]), "ls_home": os.listdir("/home") if os.path.exists("/home") else None}))
''')
info = json.loads(r.stdout.splitlines()[0])
check("unprivileged: uid 65534, zero capabilities, no-new-privs, seccomp filter", info["uid"] == 65534 and info["CapEff"].strip("0") == "" and info["CapBnd"].strip("0") == "" and info["NoNewPrivs"] == "1" and info["seccomp"] == "2", f"CapEff={info['CapEff']} nnp={info['NoNewPrivs']} seccomp={info['seccomp']}")
check("no docker socket, no host mounts, nothing in /home", not info["docker_sock"] and not info["run_docker"] and info["host_mounts"] == [] and info["ls_home"] in ([], None), f"mounts={info['host_mounts']} home={info['ls_home']}")
check("own pid namespace, no secrets in the environment", info["proc_count"] < 12 and not any(k for k in info["env_keys"] if any(x in k.upper() for x in ("KEY", "TOKEN", "SECRET", "PASS"))), f"procs={info['proc_count']} env={info['env_keys']}")

r = py('''
import os, ctypes, json
res = {}
libc = ctypes.CDLL(None, use_errno=True)
res["mount"] = libc.mount(b"none", b"/mnt", b"tmpfs", 0, None); res["mount_errno"] = ctypes.get_errno()
res["setuid0"] = libc.setuid(0); 
try: open("/proc/sys/kernel/hostname", "w").write("x"); res["sysctl"] = "WROTE"
except Exception as e: res["sysctl"] = type(e).__name__
try: os.listdir("/sys/kernel/debug"); res["debugfs"] = "LISTED"
except Exception as e: res["debugfs"] = type(e).__name__
try: open("/dev/mem"); res["devmem"] = "OPENED"
except Exception as e: res["devmem"] = type(e).__name__
print(json.dumps(res))
''')
res = json.loads(r.stdout.splitlines()[0])
check("cannot mount, become root, write sysctl or open raw devices", res["mount"] == -1 and res["setuid0"] == -1 and res["sysctl"] != "WROTE" and res["devmem"] != "OPENED", str(res))

t0 = time.time()
r = py("import time\nwhile True: pass", lim=S.Limits(memory_mb=512, timeout_s=5, pids=64, work_mb=32, tmp_mb=8))
check("runaway CPU loop is stopped at the time limit", r.timed_out and time.time() - t0 < 30, f"timed_out={r.timed_out} wall={time.time()-t0:.1f}s")

r = py('''
import os, json
kids = 0
try:
    for _ in range(2000):
        pid = os.fork()
        if pid == 0:
            import time; time.sleep(8); os._exit(0)
        kids += 1
except OSError as e:
    print(json.dumps({"forked": kids, "stopped_by": type(e).__name__}))
''', lim=S.Limits(memory_mb=512, timeout_s=15, pids=64, work_mb=32, tmp_mb=8))
res = json.loads(r.stdout.splitlines()[0]) if r.stdout.strip() else {}
check("fork bomb is capped by the pids limit", res.get("forked", 9999) < 64 and res.get("stopped_by") == "BlockingIOError", str(res))

r = py("x = bytearray(2 * 1024**3)\nprint('allocated')", lim=S.Limits(memory_mb=256, timeout_s=15, pids=64, work_mb=32, tmp_mb=8))
check("memory hog is killed at the memory limit", "allocated" not in r.stdout and (r.exit != 0 or r.killed == "oom"), f"exit={r.exit} killed={r.killed!r}")

r = py('''
import os, json
res = {}
try:
    with open("/work/big", "wb") as f:
        for _ in range(100): f.write(b"0" * 1024 * 1024)
    res["work"] = "WROTE 100MB"
except OSError as e: res["work"] = f"{type(e).__name__} errno {e.errno}"
try:
    with open("/tmp/big", "wb") as f:
        for _ in range(100): f.write(b"0" * 1024 * 1024)
    res["tmp"] = "WROTE 100MB"
except OSError as e: res["tmp"] = f"{type(e).__name__} errno {e.errno}"
print(json.dumps(res))
''', lim=S.Limits(memory_mb=512, timeout_s=15, pids=64, work_mb=32, tmp_mb=8, max_file_mb=64))
res = json.loads(r.stdout.splitlines()[0])
check("disk writes are bounded by the tmpfs sizes (no host disk used)", "WROTE" not in res.get("work", "") and "WROTE" not in res.get("tmp", ""), str(res))

a = py("import os; open(os.environ['OUT']+'/mine','w').write('tenant A secret'); print(sorted(os.listdir(os.environ['IN'])))", {"a-only.txt": b"A's data"})
b = py("import os, glob; print(sorted(os.listdir(os.environ['IN']))); print(glob.glob('/work/**/*', recursive=True)); print(os.path.exists('/work/out/mine'))")
check("a second job sees nothing of the first (fresh container each time)", "a-only" not in b.stdout and "mine" not in b.stdout.replace("True","").replace("False","") and "False" in b.stdout, b.stdout.replace("\n", " | ")[:140])

r = py("open('/tmp/x','w')\nimport os\nos.symlink('/etc/passwd', os.environ['OUT']+'/link')\nopen(os.environ['OUT']+'/real','w').write('ok')")
check("symlinks are not exported; plain files are", "link" not in r.files and r.files.get("real") == b"ok", f"files={list(r.files)}")

r = S.run(HOST, IMG, {"mode": "tool", "argv": ["ffprobe", "-version"]}, {}, LIM, job_label="probe")
check("allow-listed media tool runs (ffprobe)", r.exit == 0 and "ffprobe version" in r.stdout, r.stdout[:40])

r = py("print('x' * 500000)", lim=S.Limits(memory_mb=256, timeout_s=10, log_bytes=2000, work_mb=16, tmp_mb=8))
check("log output is truncated, not unbounded", len(r.stdout) <= 2000, f"len={len(r.stdout)}")

import subprocess

left = subprocess.run(["docker", "ps", "-a", "--filter", "label=semechka.sandbox=1", "--format", "{{.Names}}"], capture_output=True, env=S._env(HOST), check=False).stdout.decode().split()
check("no container survives its call", left == [], str(left))
print(f"\n{sum(results)}/{len(results)} passed"); sys.exit(0 if all(results) else 1)
