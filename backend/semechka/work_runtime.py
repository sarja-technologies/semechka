"""Work Mode runtime: one persistent, locked-down container per session (docs/WORK_MODE.md, section 3).

`WorkRuntime` is the only thing work_svc talks to. `DockerRuntime` is the production implementation: the Docker CLI with a
FIXED argument list built here (model text is never part of a host argv or shell string), through the same unprivileged
daemon and relay socket as the per-call sandbox (sandbox_svc). `LocalDirRuntime` is a TEST DOUBLE and is never selectable
in production: tests inject it into `AppState`; no setting or environment variable chooses it.

Every file operation and every command runs INSIDE the container, through one small helper program (FS_HELPER) fed over
stdin. The helper resolves each path with realpath against /work, refuses anything that ends up outside it (including
symlink escapes), caps what it returns, enforces the command timeout and kills the whole process group, and watches the
workspace size while a command runs. The same helper runs in both runtimes, so the path-safety logic that the tests
exercise is the code that runs in production.

Honest boundary (see docs/WORK_MODE.md): namespaces + cgroups + seccomp on a shared kernel behind an unprivileged daemon,
not a virtual machine. With `--network none` the container has no interface but loopback. A rootless daemon cannot isolate
an internet-enabled container from the local network.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from . import sandbox_svc

log = logging.getLogger(__name__)
WORK_ROOT = "/work"
NAME_RE = re.compile(r"^work-[A-Za-z0-9]{8,40}$")


class RuntimeError_(Exception):
    """The runtime could not do what was asked (not: the command failed). The message is safe to show."""


class PathRefused(RuntimeError_):
    """A path leaves /work (lexically or through a symlink), or is otherwise not allowed."""


class QuotaExceeded(RuntimeError_):
    pass


@dataclass
class ContainerSpec:
    session_id: str
    image: str = "semechka-work:1"
    memory_mb: int = 4096
    cpus: float = 2.0
    pids: int = 256
    network: str = "none"          # none | internet
    network_name: str = "bridge"   # the Docker network used when network == internet
    tmp_mb: int = 256
    instance: str = ""             # which Semechka install owns it (several installs share one daemon)

    @property
    def name(self) -> str:
        return container_name(self.session_id)

    @property
    def volume(self) -> str:
        return self.name


def container_name(session_id: str) -> str:
    return "work-" + re.sub(r"[^A-Za-z0-9]", "", session_id.split("_", 1)[-1])


@dataclass
class ExecResult:
    exit: int
    stdout: str
    stderr: str
    duration_s: float
    timed_out: bool = False
    quota_killed: bool = False
    oom_killed: bool = False
    interrupted: bool = False
    signal: str = ""
    truncated_stdout: int = 0   # bytes left out between the head and the tail kept
    truncated_stderr: int = 0


@dataclass
class FsEntry:
    name: str
    type: str          # file | dir | link
    size: int
    mtime: float
    link_ok: bool = True


# ---- the in-container helper ------------------------------------------------------------------------------
# Runs as `python3 -I -c <FS_HELPER>` inside the container (or on the host with a temp dir as root, for the test double).
# stdin: one JSON request line, then `nbytes` raw payload bytes (writes). stdout: one JSON header line, then raw bytes (reads).
FS_HELPER = r'''
import sys, os, json, re, time, shutil, hashlib, signal, subprocess, threading, fnmatch, stat as st_

def reply(obj, data=b""):
    obj = dict(obj); obj["nbytes"] = len(data)
    w = sys.stdout.buffer
    w.write(json.dumps(obj).encode() + b"\n"); w.write(data); w.flush()

class Refused(Exception):
    pass

req = json.loads(sys.stdin.buffer.readline())
ROOT = os.path.realpath(req["root"])
SKIP = {".git", ".home", "node_modules", "__pycache__", ".cache", ".npm", ".venv"}

def inside(p):
    return p == ROOT or p.startswith(ROOT + os.sep)

def resolve(rel, last_link=False):
    rel = rel or ""
    if "\x00" in rel:
        raise Refused("invalid path")
    parts = []
    for part in rel.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not parts:
                raise Refused("the path leaves /work")
            parts.pop()
            continue
        parts.append(part)
    full = os.path.join(ROOT, *parts)
    if last_link and parts:   # deleting a link removes the link itself: only the folder it sits in must be inside /work
        parent = os.path.realpath(os.path.join(ROOT, *parts[:-1]))
        if not inside(parent):
            raise Refused("the path resolves outside /work (symlink escape refused)")
        p = os.path.join(parent, parts[-1])
        return p, p, "/".join(parts)
    real = os.path.realpath(full)
    if not inside(real):
        raise Refused("the path resolves outside /work (symlink escape refused)")
    return full, real, "/".join(parts)

def du(root):
    try:
        r = subprocess.run(["du", "-sb", root], capture_output=True, timeout=25)
        if r.returncode == 0:
            return int(r.stdout.split()[0])
    except Exception:
        pass
    total = 0
    for d, dirs, files in os.walk(root, followlinks=False):
        for f in files:
            try:
                total += os.lstat(os.path.join(d, f)).st_size
            except OSError:
                pass
    return total

def etype(m):
    return "dir" if st_.S_ISDIR(m) else ("link" if st_.S_ISLNK(m) else "file")

def op_list():
    full, real, rel = resolve(req.get("path"))
    if not os.path.isdir(real):
        raise Refused("not a directory: " + (rel or "."))
    out = []
    with os.scandir(real) as it:
        for e in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            try:
                s = e.stat(follow_symlinks=False)
            except OSError:
                continue
            ok = True
            if st_.S_ISLNK(s.st_mode):
                ok = inside(os.path.realpath(e.path))
            out.append({"name": e.name, "type": etype(s.st_mode), "size": s.st_size if not st_.S_ISDIR(s.st_mode) else 0,
                        "mtime": s.st_mtime, "link_ok": ok})
            if len(out) >= int(req.get("max_entries", 2000)):
                break
    reply({"ok": True, "path": rel, "entries": out})

def op_stat():
    full, real, rel = resolve(req.get("path"))
    try:
        s = os.lstat(real)
    except FileNotFoundError:
        reply({"ok": True, "exists": False, "path": rel}); return
    reply({"ok": True, "exists": True, "path": rel, "type": etype(s.st_mode), "size": s.st_size, "mtime": s.st_mtime})

def op_read():
    full, real, rel = resolve(req.get("path"))
    if os.path.isdir(real):
        raise Refused("is a directory: " + rel)
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise Refused("no such file: " + rel)
    except IsADirectoryError:
        raise Refused("is a directory: " + rel)
    except OSError as e:
        raise Refused("cannot read " + rel + " (" + e.strerror + ")")
    with os.fdopen(fd, "rb") as f:
        s = os.fstat(f.fileno())
        if not st_.S_ISREG(s.st_mode):
            raise Refused("not a regular file: " + rel)
        off = max(0, int(req.get("offset", 0)))
        f.seek(off)
        data = f.read(int(req.get("limit", 1 << 20)))
        head = data[:8192] if off == 0 else b""
        h = None
        if req.get("sha256"):
            f.seek(0); hh = hashlib.sha256()
            for chunk in iter(lambda: f.read(1 << 20), b""):
                hh.update(chunk)
            h = hh.hexdigest()
    reply({"ok": True, "path": rel, "size": s.st_size, "offset": off, "binary": b"\x00" in head, "sha256": h,
           "mtime": s.st_mtime}, data)

def op_write():
    full, real, rel = resolve(req.get("path"))
    if real == ROOT:
        raise Refused("cannot write to the workspace root")
    n = int(req["size"])
    data = sys.stdin.buffer.read(n)
    if len(data) != n:
        raise Refused("upload was cut off")
    if os.path.isdir(real):
        raise Refused("is a directory: " + rel)
    if req.get("create_only") and os.path.lexists(real):
        raise Refused("already exists: " + rel)
    parent = os.path.dirname(real)
    os.makedirs(parent, exist_ok=True)
    if not inside(os.path.realpath(parent)):
        raise Refused("the path resolves outside /work (symlink escape refused)")
    tmp = os.path.join(parent, ".wtmp-" + hashlib.sha1(os.urandom(8)).hexdigest()[:10])
    try:
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, real)
    finally:
        if os.path.lexists(tmp):
            os.unlink(tmp)
    reply({"ok": True, "path": rel, "size": n, "sha256": hashlib.sha256(data).hexdigest()})

def op_mkdir():
    full, real, rel = resolve(req.get("path"))
    os.makedirs(real, exist_ok=True)
    reply({"ok": True, "path": rel})

def op_delete():
    full, real, rel = resolve(req.get("path"), last_link=True)
    if real == ROOT:
        if not req.get("allow_root"):
            raise Refused("refusing to delete the workspace root")
        n = 0
        for e in os.listdir(ROOT):
            p = os.path.join(ROOT, e)
            shutil.rmtree(p) if os.path.isdir(p) and not os.path.islink(p) else os.unlink(p)
            n += 1
        reply({"ok": True, "deleted": n}); return
    if not os.path.lexists(full):
        raise Refused("no such file or folder: " + rel)
    # delete the NAME, never what a symlink points at
    if os.path.islink(full) or not os.path.isdir(full):
        os.unlink(full); n = 1
    else:
        n = sum(len(f) + 1 for _, _, f in os.walk(full))
        shutil.rmtree(full)
    reply({"ok": True, "deleted": n, "path": rel})

def op_usage():
    reply({"ok": True, "bytes": du(ROOT)})

def op_search():
    full, real, rel = resolve(req.get("path"))
    pat = req.get("pattern") or ""
    glob = req.get("glob") or ""
    try:
        rx = re.compile(pat, re.I if req.get("ignore_case", True) else 0) if pat else None
    except re.error as e:
        raise Refused("bad pattern: " + str(e))
    maxr = int(req.get("max_results", 100)); out = []; scanned = 0; names_only = bool(req.get("names_only"))
    stack = [real]
    while stack and len(out) < maxr:
        d = stack.pop()
        try:
            entries = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in entries:
            try:
                if e.is_symlink():
                    continue
                if e.is_dir(follow_symlinks=False):
                    if e.name not in SKIP:
                        stack.append(e.path)
                    continue
                p = os.path.relpath(e.path, ROOT)
                if glob and not (fnmatch.fnmatch(p, glob) or fnmatch.fnmatch(e.name, glob)):
                    continue
                if names_only:
                    if rx is None or rx.search(p):
                        s2 = e.stat(follow_symlinks=False)
                        out.append({"path": p, "size": s2.st_size, "mtime": s2.st_mtime})
                    continue
                if rx is None or e.stat(follow_symlinks=False).st_size > 2_000_000:
                    continue
                scanned += 1
                with open(e.path, "rb") as f:
                    raw = f.read()
                if b"\x00" in raw[:4096]:
                    continue
                for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                    if rx.search(line):
                        out.append({"path": p, "line": i, "text": line[:300]})
                        if len(out) >= maxr:
                            break
            except OSError:
                continue
            if len(out) >= maxr:
                break
    reply({"ok": True, "matches": out, "scanned": scanned, "truncated": len(out) >= maxr})

def oom_count():
    for p in ("/sys/fs/cgroup/memory.events",):
        try:
            for line in open(p):
                if line.startswith("oom_kill "):
                    return int(line.split()[1])
        except OSError:
            pass
    return None

def op_exec():
    cmd = req.get("argv") or ["bash", "-c", req["cmd"]]
    cwd = resolve(req.get("cwd"))[1]
    if not os.path.isdir(cwd):
        raise Refused("working directory does not exist")
    home = os.path.join(ROOT, ".home")
    os.makedirs(home, exist_ok=True)
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin", "HOME": home, "LANG": "C.UTF-8",
           "MPLBACKEND": "Agg", "TERM": "dumb", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
           "PIP_DISABLE_PIP_VERSION_CHECK": "1", "GIT_TERMINAL_PROMPT": "0",
           "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "core.hooksPath", "GIT_CONFIG_VALUE_0": "/dev/null",
           "WORKSPACE": ROOT}
    for k, v in (req.get("env") or {}).items():
        if re.fullmatch(r"[A-Z_][A-Z0-9_]{0,40}", k):
            env[k] = str(v)[:2000]
    fsize = int(req.get("fsize_bytes") or 0)
    def pre():
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        if fsize:
            resource.setrlimit(resource.RLIMIT_FSIZE, (fsize, fsize))
        try:
            with open("/proc/self/oom_score_adj", "w") as f:
                f.write("1000")
        except OSError:
            pass
    maxout = int(req.get("max_output", 65536)); head_n = maxout * 6 // 10; tail_n = maxout - head_n
    bufs = {"out": [bytearray(), bytearray(), 0], "err": [bytearray(), bytearray(), 0]}
    t0 = time.monotonic(); oom0 = oom_count()
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             start_new_session=True, preexec_fn=pre)
    except OSError as e:
        raise Refused("cannot start the command: " + str(e.strerror or e))
    def pump(stream, key):
        head, tail, dropped = bufs[key]
        while True:
            c = stream.read1(65536)
            if not c:
                break
            if len(head) < head_n:
                take = head_n - len(head); head += c[:take]; c = c[take:]
            if c:
                tail += c
                if len(tail) > tail_n:
                    over = len(tail) - tail_n; del tail[:over]; bufs[key][2] += over
    threads = [threading.Thread(target=pump, args=(p.stdout, "out"), daemon=True), threading.Thread(target=pump, args=(p.stderr, "err"), daemon=True)]
    for t in threads:
        t.start()
    state = {"interrupted": False}
    def on_term(*_):
        state["interrupted"] = True
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
    signal.signal(signal.SIGTERM, on_term)
    timeout = float(req.get("cmd_timeout", 120)); quota = int(req.get("quota_bytes") or 0)
    timed_out = quota_killed = False; last_du = t0
    while p.poll() is None:
        now = time.monotonic()
        if now - t0 > timeout:
            timed_out = True
            break
        if quota and now - last_du >= 2.0:
            last_du = now
            if du(ROOT) > quota:
                quota_killed = True
                break
        time.sleep(0.05)
    if timed_out or quota_killed:
        try:
            os.killpg(p.pid, signal.SIGTERM)
        except OSError:
            pass
        try:
            p.wait(2)
        except subprocess.TimeoutExpired:
            pass
    try:
        os.killpg(p.pid, signal.SIGKILL)   # nothing the command started outlives it
    except OSError:
        pass
    try:
        p.wait(5)
    except subprocess.TimeoutExpired:
        pass
    for t in threads:
        t.join(2)
    rc = p.returncode if p.returncode is not None else -9
    sig = ""
    if rc < 0:
        try:
            sig = signal.Signals(-rc).name
        except ValueError:
            sig = "SIG" + str(-rc)
    oom1 = oom_count()
    def text(key):
        head, tail, dropped = bufs[key]
        return (bytes(head) + bytes(tail)).decode("utf-8", "replace"), dropped
    so, do = text("out"); se, de = text("err")
    reply({"ok": True, "exit": rc, "signal": sig, "stdout": so, "stderr": se, "truncated_stdout": do, "truncated_stderr": de,
           "timed_out": timed_out, "quota_killed": quota_killed, "interrupted": state["interrupted"],
           "oom_killed": bool(oom0 is not None and oom1 is not None and oom1 > oom0),
           "duration_s": round(time.monotonic() - t0, 3)})

def ppid_of(pid):
    try:
        s = open("/proc/" + str(pid) + "/stat").read()
        return int(s[s.rindex(")") + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return -1

def op_killall():
    # helpers get SIGTERM (they kill their own command's process group and still reply "interrupted"); everything else dies,
    # except init and the container's own main process (`sleep infinity`, a child of init): without it the container would stop
    me = os.getpid(); n = 0; rest = []
    for d in os.listdir("/proc"):
        if not d.isdigit() or int(d) in (1, me):
            continue
        try:
            cl = open("/proc/" + d + "/cmdline", "rb").read()
        except OSError:
            continue
        if b"\x00-I\x00-c\x00" in cl[:64]:
            try:
                os.kill(int(d), signal.SIGTERM); n += 1
            except OSError:
                pass
        elif cl == b"sleep\x00infinity\x00" and ppid_of(d) == 1:
            continue
        else:
            rest.append(int(d))
    for pid in rest:
        try:
            os.kill(pid, signal.SIGKILL); n += 1
        except OSError:
            pass
    reply({"ok": True, "killed": n})

try:
    globals()["op_" + req["op"]]()
except Refused as e:
    reply({"ok": False, "refused": True, "error": str(e)})
except Exception as e:
    reply({"ok": False, "refused": False, "error": type(e).__name__ + ": " + str(e)[:200]})
'''


def _parse_reply(raw: bytes) -> tuple[dict, bytes]:
    nl = raw.find(b"\n")
    if nl < 0:
        raise RuntimeError_("The workspace helper returned nothing usable.")
    try:
        head = json.loads(raw[:nl])
        n = int(head.get("nbytes", 0))
    except (ValueError, TypeError):
        raise RuntimeError_("The workspace helper returned an unreadable result.") from None
    return head, raw[nl + 1:nl + 1 + n]


# ---- interface ---------------------------------------------------------------------------------------------
class WorkRuntime(ABC):
    """Everything work_svc needs from a container runtime. All methods are blocking and thread-safe."""

    #: True only for the test double. work_svc refuses to run a double against anything but tests.
    is_test_double = False

    @abstractmethod
    def image_ready(self, image: str) -> tuple[bool, str]: ...

    @abstractmethod
    def state(self, name: str) -> str:
        """absent | stopped | running | error"""

    @abstractmethod
    def create(self, spec: ContainerSpec) -> None:
        """Create the workspace volume and the container and start it. Adopts an existing one with the same name."""

    @abstractmethod
    def start(self, name: str) -> None: ...

    @abstractmethod
    def stop(self, name: str) -> None: ...

    @abstractmethod
    def remove(self, name: str, *, volume: bool = True) -> None: ...

    @abstractmethod
    def _helper(self, name: str, req: dict, payload: bytes = b"", timeout: float = 60) -> tuple[dict, bytes]: ...

    @abstractmethod
    def interrupt(self, name: str) -> None:
        """Kill every process the session has running (cancel)."""

    @abstractmethod
    def list_managed(self, instance: str) -> list[dict]:
        """[{name, session_id, state, volume_only}] for every container/volume this install owns."""

    @abstractmethod
    def container_matches(self, spec: ContainerSpec) -> bool:
        """Whether the existing container was created with the limits/network in `spec`."""

    @abstractmethod
    def live_usage(self, name: str) -> dict: ...

    # ---- derived operations: identical for every runtime, so the tests cover the production code paths ----
    def _call(self, name: str, op: str, payload: bytes = b"", timeout: float = 60, **args) -> tuple[dict, bytes]:
        head, data = self._helper(name, {"op": op, "root": WORK_ROOT, **args}, payload, timeout)
        if not head.get("ok"):
            if head.get("refused"):
                raise PathRefused(head.get("error", "refused"))
            raise RuntimeError_(head.get("error", "The workspace operation failed."))
        return head, data

    def exec(self, name: str, cmd: str | None = None, *, argv: list[str] | None = None, timeout_s: int = 120,
             max_output: int = 65536, cwd: str = "", env: dict | None = None, quota_bytes: int = 0,
             fsize_mb: int = 512) -> ExecResult:
        """Run a command inside the container: exit code, duration, capped output, timeout and quota enforcement."""
        head, _ = self._call(name, "exec", timeout=timeout_s + 40, cmd=cmd, argv=argv, cmd_timeout=timeout_s, max_output=max_output,
                             cwd=cwd, env=env or {}, quota_bytes=quota_bytes, fsize_bytes=fsize_mb * 1048576)
        return ExecResult(head["exit"], head["stdout"], head["stderr"], head["duration_s"], head["timed_out"], head["quota_killed"],
                          head["oom_killed"], head["interrupted"], head.get("signal", ""), head["truncated_stdout"], head["truncated_stderr"])

    def list(self, name: str, path: str = "") -> list[FsEntry]:
        head, _ = self._call(name, "list", path=path)
        return [FsEntry(e["name"], e["type"], e["size"], e["mtime"], e.get("link_ok", True)) for e in head["entries"]]

    def stat(self, name: str, path: str) -> dict:
        head, _ = self._call(name, "stat", path=path)
        return head

    def get_file(self, name: str, path: str, *, offset: int = 0, limit: int = 1 << 20, sha256: bool = False) -> tuple[bytes, dict]:
        head, data = self._call(name, "read", path=path, offset=offset, limit=limit, sha256=sha256)
        return data, head

    def put_file(self, name: str, path: str, data: bytes, *, create_only: bool = False) -> dict:
        head, _ = self._call(name, "write", payload=data, path=path, size=len(data), create_only=create_only)
        return head

    def mkdir(self, name: str, path: str) -> None:
        self._call(name, "mkdir", path=path)

    def delete(self, name: str, path: str, *, allow_root: bool = False) -> int:
        head, _ = self._call(name, "delete", path=path, allow_root=allow_root)
        return int(head.get("deleted", 0))

    def usage_bytes(self, name: str) -> int:
        head, _ = self._call(name, "usage", timeout=40)
        return int(head["bytes"])

    def search(self, name: str, pattern: str, path: str = "", *, glob: str = "", names_only: bool = False, max_results: int = 100,
               ignore_case: bool = True) -> dict:
        head, _ = self._call(name, "search", pattern=pattern, path=path, glob=glob, names_only=names_only, max_results=max_results,
                             ignore_case=ignore_case, timeout=60)
        return head

    def reconcile(self, instance: str, known: dict[str, str], *, remove_orphans: bool = True) -> dict:
        """Compare this install's containers and volumes with the sessions the database knows (`known`: session_id ->
        container name). Orphans (no live session) are removed, never anything else; the result lists what was seen and done."""
        names_known = set(known.values())
        seen, removed, absent = [], [], []
        found = {m["name"]: m for m in self.list_managed(instance)}
        for name, m in found.items():
            seen.append(name)
            if name not in names_known and remove_orphans:
                try:
                    self.remove(name, volume=True)
                    removed.append(name)
                except RuntimeError_ as e:
                    log.warning("could not remove orphaned work container %s: %s", name, e)
        for sid, name in known.items():
            if name not in found:
                absent.append(sid)
        states = {sid: self.state(name) for sid, name in known.items() if name in found}
        return {"seen": seen, "removed": removed, "absent": absent, "states": states}


# ---- production ---------------------------------------------------------------------------------------------
class DockerRuntime(WorkRuntime):
    def __init__(self, docker_host: str):
        self.host = docker_host

    def _d(self, *args: str, timeout: float = 30, input: bytes | None = None) -> subprocess.CompletedProcess:
        try:
            return sandbox_svc._docker(self.host, *args, timeout=timeout, input=input)
        except subprocess.TimeoutExpired:
            raise RuntimeError_("The container runtime did not answer in time.") from None
        except OSError as e:
            raise RuntimeError_(f"The container runtime is not reachable ({type(e).__name__}).") from None

    @staticmethod
    def _missing(r: subprocess.CompletedProcess) -> bool:
        """Docker words "not there" differently across versions ("No such container", "error: no such object")."""
        return b"no such" in r.stderr.lower()

    @staticmethod
    def _err(r: subprocess.CompletedProcess) -> str:
        lines = r.stderr.decode(errors="replace").strip().splitlines()
        return (lines[-1] if lines else "unknown error")[:200]

    def image_ready(self, image: str) -> tuple[bool, str]:
        return sandbox_svc.available(self.host, image)

    def state(self, name: str) -> str:
        # --type container: the volume has the same name, and a bare `inspect` would find that instead
        r = self._d("inspect", "--type", "container", "-f", "{{.State.Status}}", name, timeout=15)
        if r.returncode != 0:
            return "absent" if self._missing(r) else "error"
        s = r.stdout.decode().strip()
        return {"running": "running", "exited": "stopped", "created": "stopped", "paused": "stopped"}.get(s, "error")

    def run_command(self, spec: ContainerSpec) -> list[str]:
        """The exact `docker run` argument list (public so a test can assert every flag the design requires)."""
        mem = f"{int(spec.memory_mb)}m"
        net = "none" if spec.network == "none" else spec.network_name
        return [
            "run", "-d", "--pull", "never", "--name", spec.name,
            "--label", f"semechka.work={spec.session_id}", "--label", f"semechka.work.instance={spec.instance}",
            "--network", net, "--read-only",
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={int(spec.tmp_mb)}m,mode=1777",
            "-v", f"{spec.volume}:{WORK_ROOT}",
            "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--pids-limit", str(int(spec.pids)), "--memory", mem, "--memory-swap", mem, "--cpus", str(float(spec.cpus)),
            "--ulimit", "nofile=1024:1024", "--ulimit", "core=0",
            "--init", "--hostname", "work", "--log-driver", "none", "--stop-timeout", "3",
            "-e", f"HOME={WORK_ROOT}/.home",
            spec.image,
        ]

    def create(self, spec: ContainerSpec) -> None:
        st = self.state(spec.name)
        if st in ("running", "stopped"):
            if st == "stopped":
                self.start(spec.name)
            return
        if st == "error":
            raise RuntimeError_("The session container is in an unusable state; remove it from the session menu.")
        r = self._d("volume", "create", "--label", f"semechka.work={spec.session_id}", "--label", f"semechka.work.instance={spec.instance}",
                    spec.volume)
        if r.returncode != 0:
            raise RuntimeError_(f"The workspace volume could not be created: {self._err(r)}")
        r = self._d(*self.run_command(spec), timeout=60)
        if r.returncode != 0:
            raise RuntimeError_(f"The session container could not be started: {self._err(r)}")

    def start(self, name: str) -> None:
        r = self._d("start", name, timeout=40)
        if r.returncode != 0:
            raise RuntimeError_(f"The session container could not be started: {self._err(r)}")

    def stop(self, name: str) -> None:
        r = self._d("stop", "-t", "3", name, timeout=40)
        if r.returncode != 0 and not self._missing(r):
            raise RuntimeError_(f"The session container could not be stopped: {self._err(r)}")

    def remove(self, name: str, *, volume: bool = True) -> None:
        if not NAME_RE.match(name):
            raise RuntimeError_("Refusing to remove a container that is not a Work Mode container.")
        r = self._d("rm", "-f", name, timeout=40)
        if r.returncode != 0 and not self._missing(r):
            raise RuntimeError_(f"The session container could not be removed: {self._err(r)}")
        if volume:
            r = self._d("volume", "rm", "-f", name, timeout=60)
            if r.returncode != 0 and not self._missing(r):
                raise RuntimeError_(f"The workspace volume could not be removed: {self._err(r)}")

    def _helper(self, name: str, req: dict, payload: bytes = b"", timeout: float = 60) -> tuple[dict, bytes]:
        body = json.dumps(req).encode() + b"\n" + payload
        try:
            r = sandbox_svc._docker(self.host, "exec", "-i", name, "python3", "-I", "-c", FS_HELPER, timeout=timeout + 15, input=body)
        except subprocess.TimeoutExpired:
            self.interrupt(name)
            raise RuntimeError_("The command did not finish in time and was stopped.") from None
        except OSError as e:
            raise RuntimeError_(f"The container runtime is not reachable ({type(e).__name__}).") from None
        if r.returncode != 0 and not r.stdout:
            err = r.stderr.decode(errors="replace").strip().splitlines()
            if r.returncode == 137 or not err:
                raise RuntimeError_("The command was killed (exit 137: out of memory or killed from outside).")
            raise RuntimeError_(f"The session container is not available: {err[-1][:160]}")
        return _parse_reply(r.stdout)

    def interrupt(self, name: str) -> None:
        try:
            self._d("exec", "-i", name, "python3", "-I", "-c", FS_HELPER, timeout=20,
                    input=json.dumps({"op": "killall", "root": WORK_ROOT}).encode() + b"\n")
        except RuntimeError_:
            pass

    def list_managed(self, instance: str) -> list[dict]:
        out: dict[str, dict] = {}
        flt = ["--filter", "label=semechka.work", "--filter", f"label=semechka.work.instance={instance}"]
        r = self._d("ps", "-a", *flt, "--format", '{{.Names}}|{{.Label "semechka.work"}}|{{.State}}', timeout=30)
        if r.returncode != 0:
            raise RuntimeError_(f"Could not list session containers: {self._err(r)}")
        for line in r.stdout.decode().splitlines():
            n, _, rest = line.partition("|")
            sid, _, state = rest.partition("|")
            if NAME_RE.match(n):
                out[n] = {"name": n, "session_id": sid, "state": "running" if state == "running" else "stopped", "volume_only": False}
        r = self._d("volume", "ls", *flt, "--format", '{{.Name}}|{{.Label "semechka.work"}}', timeout=30)
        if r.returncode == 0:
            for line in r.stdout.decode().splitlines():
                n, _, sid = line.partition("|")
                if NAME_RE.match(n) and n not in out:
                    out[n] = {"name": n, "session_id": sid, "state": "absent", "volume_only": True}
        return list(out.values())

    def container_matches(self, spec: ContainerSpec) -> bool:
        r = self._d("inspect", "--type", "container", "-f", "{{.HostConfig.NetworkMode}}|{{.HostConfig.Memory}}|{{.HostConfig.PidsLimit}}|{{.HostConfig.NanoCpus}}",
                    spec.name, timeout=15)
        if r.returncode != 0:
            return False
        net, mem, pids, nano = r.stdout.decode().strip().split("|")
        want_net = "none" if spec.network == "none" else spec.network_name
        return net == want_net and int(mem) == int(spec.memory_mb) * 1048576 and int(pids) == int(spec.pids) \
            and abs(int(nano) - int(float(spec.cpus) * 1e9)) < 1e6

    def live_usage(self, name: str) -> dict:
        r = self._d("stats", "--no-stream", "--format", "{{json .}}", name, timeout=15)
        if r.returncode != 0 or not r.stdout.strip():
            return {}
        try:
            s = json.loads(r.stdout.decode().splitlines()[0])
        except ValueError:
            return {}
        return {"memory": s.get("MemUsage", ""), "cpu": s.get("CPUPerc", ""), "pids": s.get("PIDs", "")}


# ---- TEST DOUBLE --------------------------------------------------------------------------------------------
class LocalDirRuntime(WorkRuntime):
    """TEST DOUBLE ONLY. A temporary directory per "container" and the same helper program run with the host's python.
    It enforces NOTHING about isolation (no network namespace, no limits, no read-only root): it exists so the tests can
    exercise session lifecycle, tool semantics, path handling, quota and recovery without a container runtime. It is never
    created by production code; tests assign it to `AppState.work_runtime`."""

    is_test_double = True

    def __init__(self, base: Path | None = None):
        self.base = Path(base) if base else Path(tempfile.mkdtemp(prefix="work-double-"))
        self.base.mkdir(parents=True, exist_ok=True)
        self.created: dict[str, ContainerSpec] = {}
        self.calls: list[tuple[str, str]] = []
        self.live: dict[str, list[subprocess.Popen]] = {}
        self._lock = threading.Lock()
        self.fail_next: str | None = None

    def _dir(self, name: str) -> Path:
        return self.base / name

    def _flag(self, name: str) -> Path:
        return self.base / (name + ".state")

    def image_ready(self, image: str) -> tuple[bool, str]:
        return True, "test-double"

    def state(self, name: str) -> str:
        if not self._dir(name).exists():
            return "absent"
        s = self._flag(name).read_text() if self._flag(name).exists() else "stopped"
        return "absent" if s == "gone" else s   # "gone": the container was removed but its volume (the directory) is kept

    def create(self, spec: ContainerSpec) -> None:
        self.calls.append(("create", spec.name))
        if self.fail_next == "create":
            self.fail_next = None
            raise RuntimeError_("The session container could not be started: simulated failure")
        self._dir(spec.name).mkdir(parents=True, exist_ok=True)
        (self._dir(spec.name) / ".home").mkdir(exist_ok=True)
        self.created[spec.name] = spec
        self._flag(spec.name).write_text("running")

    def start(self, name: str) -> None:
        self.calls.append(("start", name))
        if self.state(name) == "absent":
            raise RuntimeError_("No such container.")
        self._flag(name).write_text("running")

    def stop(self, name: str) -> None:
        self.calls.append(("stop", name))
        self.interrupt(name)
        if self._dir(name).exists():
            self._flag(name).write_text("stopped")

    def remove(self, name: str, *, volume: bool = True) -> None:
        self.calls.append(("remove", name))
        self.interrupt(name)
        if volume:
            shutil.rmtree(self._dir(name), ignore_errors=True)
            self._flag(name).unlink(missing_ok=True)
            self.created.pop(name, None)
        elif self._dir(name).exists():
            self._flag(name).write_text("gone")
            self.created.pop(name, None)

    def _helper(self, name: str, req: dict, payload: bytes = b"", timeout: float = 60) -> tuple[dict, bytes]:
        if self.state(name) != "running":
            raise RuntimeError_("The session container is not running.")
        req = {**req, "root": str(self._dir(name))}
        body = json.dumps(req).encode() + b"\n" + payload
        import sys
        p = subprocess.Popen([sys.executable, "-I", "-c", FS_HELPER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True, cwd=str(self._dir(name)),
                             env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
        with self._lock:
            self.live.setdefault(name, []).append(p)
        try:
            out, _err = p.communicate(body, timeout=timeout + 15)
        except subprocess.TimeoutExpired:
            p.kill()
            raise RuntimeError_("The command did not finish in time and was stopped.") from None
        finally:
            with self._lock:
                if p in self.live.get(name, []):
                    self.live[name].remove(p)
        if not out:
            raise RuntimeError_("The command was killed.")
        return _parse_reply(out)

    def interrupt(self, name: str) -> None:
        with self._lock:
            procs = list(self.live.get(name, []))
        for p in procs:
            try:
                p.terminate()  # the helper's SIGTERM handler kills the command's whole process group
            except OSError:
                pass

    def list_managed(self, instance: str) -> list[dict]:
        out = []
        for d in sorted(self.base.iterdir()):
            if d.is_dir() and NAME_RE.match(d.name):
                st = self.state(d.name)
                out.append({"name": d.name, "session_id": "", "state": st if st != "absent" else "absent", "volume_only": st == "absent"})
        return out

    def container_matches(self, spec: ContainerSpec) -> bool:
        have = self.created.get(spec.name)
        return have is not None and (have.network, have.memory_mb, have.pids, have.cpus) == (spec.network, spec.memory_mb, spec.pids, spec.cpus)

    def live_usage(self, name: str) -> dict:
        return {}
