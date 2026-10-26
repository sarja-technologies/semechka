"""Execution sandbox: one short-lived, locked-down container per call (docs/ENTERPRISE.md, D6).

What a container gets, and why:
  --network none            no interface but loopback: no host services, no LAN, no internet, no DNS
  --read-only, tmpfs only   the image is immutable; every writable path is RAM-backed, size-capped and vanishes with the container
  --user 65534, no caps     unprivileged inside; the whole daemon is itself unprivileged (account `semsbx`, user namespace)
  no-new-privileges, seccomp default profile, pids/memory/cpu limits, ulimits, wall-clock timeout
  --rm + labels + reaper    nothing outlives its call; abandoned containers are found by label and removed
  stdin/stdout only         inputs are streamed in, outputs streamed out: no bind mounts, no volumes, no docker.sock
The Docker CLI is used with a fixed argument list built here (never a shell, never user-supplied flags).

Honest boundary: this is Linux namespace + seccomp + cgroup isolation sharing the host kernel, behind an unprivileged
daemon account. It is not a virtual machine. A kernel or runtime vulnerability could in principle cross it; the account
it would land in holds no Semechka data, keys or credentials. See docs/ENTERPRISE.md.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import subprocess
import tarfile
import threading
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)
NAME_OK = re.compile(r"^[A-Za-z0-9._ -]{1,120}$")


class SandboxError(Exception):
    """The sandbox could not run the job (not: the job failed). Safe to show."""


@dataclass
class Limits:
    memory_mb: int = 1024
    cpus: float = 2.0
    pids: int = 128
    timeout_s: int = 120
    work_mb: int = 300
    tmp_mb: int = 64
    max_in_mb: int = 128
    max_out_mb: int = 64
    max_file_mb: int = 64
    log_bytes: int = 65536

    def clamp(self, ceiling: Limits) -> Limits:
        """Never above the platform ceiling, whatever an agent asked for."""
        return Limits(**{k: min(getattr(self, k), getattr(ceiling, k)) for k in self.__dataclass_fields__})


@dataclass
class Result:
    exit: int
    timed_out: bool
    seconds: float
    stdout: str
    stderr: str
    files: dict[str, bytes] = field(default_factory=dict)
    files_truncated: bool = False
    usage: dict = field(default_factory=dict)
    killed: str = ""  # "" | "wall_clock" | "oom"


def _env(host: str) -> dict:
    return {"DOCKER_HOST": host, "PATH": "/usr/bin:/bin", "HOME": os.environ.get("HOME", "/tmp")}


def _docker(host: str, *args: str, timeout: float = 20, input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, timeout=timeout, env=_env(host), input=input, check=False)


def available(host: str, image: str) -> tuple[bool, str]:
    try:
        r = _docker(host, "image", "inspect", image, "--format", "{{.Id}}", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"The sandbox runtime is not reachable ({type(e).__name__})."
    if r.returncode != 0:
        msg = r.stderr.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
        return False, f"The sandbox is not ready: {msg[0][:160]}"
    return True, r.stdout.decode().strip()[:19]


def build_command(name: str, image: str, job_label: str, lim: Limits) -> list[str]:
    return [
        "docker", "run", "--rm", "-i", "--pull", "never", "--name", name,
        "--label", "semechka.sandbox=1", "--label", f"semechka.job={job_label[:40]}", "--label", f"semechka.started={int(time.time())}",
        "--network", "none", "--read-only",
        "--tmpfs", f"/work:rw,noexec,nosuid,nodev,size={lim.work_mb}m,mode=0755,uid=65534,gid=65534",
        "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={lim.tmp_mb}m,mode=1777",
        "--user", "65534:65534", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "--pids-limit", str(lim.pids), "--memory", f"{lim.memory_mb}m", "--memory-swap", f"{lim.memory_mb}m",
        "--cpus", str(lim.cpus),
        "--ulimit", "nofile=256:256", "--ulimit", f"nproc={lim.pids}:{lim.pids}", "--ulimit", f"fsize={lim.max_file_mb * 1048576}",
        "--ulimit", "core=0",
        "--init", "--hostname", "sandbox", "--log-driver", "none", "--stop-timeout", "1",
        image,
    ]


def _tar_inputs(inputs: dict[str, bytes], max_bytes: int) -> bytes:
    buf, total = io.BytesIO(), 0
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for name, data in inputs.items():
            if not NAME_OK.match(name) or name.startswith(".") or "/" in name:
                raise SandboxError(f"'{name}' cannot be used as an input file name.")
            total += len(data)
            if total > max_bytes:
                raise SandboxError("The input files are too large for the sandbox.")
            ti = tarfile.TarInfo(name)
            ti.size, ti.mode, ti.uid, ti.gid = len(data), 0o444, 65534, 65534
            tf.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _parse_frame(data: bytes, token: str, lim: Limits) -> tuple[dict, dict[str, bytes]]:
    """Treat the container's stdout as hostile: exact framing, size caps, plain files only."""
    t = token.encode()
    if not data.startswith(t + b"\n"):
        raise SandboxError("The sandbox did not return a result.")
    nl = data.index(b"\n", len(t) + 1)
    try:
        header = json.loads(data[len(t) + 1:nl])
        n = int(header["tar_bytes"])
    except (ValueError, KeyError, TypeError):
        raise SandboxError("The sandbox returned an unreadable result.") from None
    blob = data[nl + 1:nl + 1 + n]
    if len(blob) != n or data[nl + 1 + n:] != b"\n" + t + b"\n":
        raise SandboxError("The sandbox result was cut off or tampered with.")
    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tf:
            for m in tf:
                if not m.isreg() or m.size > lim.max_file_mb * 1048576:
                    continue
                name = os.path.normpath(m.name)
                if name.startswith(("..", "/")) or len(files) >= 50:
                    continue
                total += m.size
                if total > lim.max_out_mb * 1048576:
                    break
                files[name] = tf.extractfile(m).read()
    except tarfile.TarError:
        raise SandboxError("The sandbox returned unreadable output files.") from None
    return header, files


def _monitor(host: str, name: str, stop: threading.Event, usage: dict) -> None:
    peak = 0.0
    while not stop.wait(1.0):
        try:
            r = _docker(host, "stats", "--no-stream", "--format", "{{json .}}", name, timeout=6)
            if r.returncode == 0 and r.stdout.strip():
                s = json.loads(r.stdout.decode().splitlines()[0])
                used = s.get("MemUsage", "0B / 0B").split("/")[0].strip()
                m = re.match(r"([\d.]+)\s*([KMG]i?B)", used)
                if m:
                    mb = float(m.group(1)) * {"KB": 1 / 1024, "KiB": 1 / 1024, "MB": 1, "MiB": 1, "GB": 1024, "GiB": 1024}[m.group(2)]
                    peak = max(peak, mb)
                    usage["peak_memory_mb"] = round(peak, 1)
                usage["cpu_percent_last"] = s.get("CPUPerc", "")
                usage["pids_last"] = s.get("PIDs", "")
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError):
            continue


def run(host: str, image: str, spec: dict, inputs: dict[str, bytes], lim: Limits, *, job_label: str = "") -> Result:
    """Run one job. `spec` is {"mode": "python", "code": str} or {"mode": "tool", "argv": [..]}."""
    token = secrets.token_hex(16)
    name = f"smx-{token[:14]}"
    payload = {**spec, "token": token, "timeout": lim.timeout_s, "max_in_bytes": lim.max_in_mb * 1048576,
               "max_out_bytes": lim.max_out_mb * 1048576, "max_log_bytes": lim.log_bytes}
    stdin = json.dumps(payload).encode() + b"\n" + _tar_inputs(inputs, lim.max_in_mb * 1048576)
    cmd = build_command(name, image, job_label, lim)
    cap = lim.max_out_mb * 1048576 + lim.log_bytes * 2 + 65536
    t0 = time.monotonic()
    usage: dict = {}
    stop = threading.Event()
    killed = ""
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_env(host))
    except OSError as e:
        raise SandboxError(f"The sandbox runtime could not be started ({type(e).__name__}).") from None
    mon = threading.Thread(target=_monitor, args=(host, name, stop, usage), daemon=True)
    mon.start()
    out_chunks: list[bytes] = []

    def pump():
        got = 0
        while True:
            c = p.stdout.read(65536)
            if not c:
                return
            got += len(c)
            if got <= cap:
                out_chunks.append(c)
    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    errbuf: list[bytes] = []
    threading.Thread(target=lambda: errbuf.append(p.stderr.read(8192)), daemon=True).start()
    try:
        try:
            p.stdin.write(stdin)
            p.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            p.wait(timeout=lim.timeout_s + 20)
        except subprocess.TimeoutExpired:
            killed = "wall_clock"
            _docker(host, "kill", name, timeout=10)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        reader.join(5)
    finally:
        stop.set()
        _docker(host, "rm", "-f", name, timeout=15)  # no-op when --rm already removed it
    data = b"".join(out_chunks)
    seconds = round(time.monotonic() - t0, 2)
    if not data:
        err = (errbuf[0].decode(errors="replace").strip()[-200:] if errbuf else "")
        if p.returncode == 137 or "OOM" in err:
            return Result(137, False, seconds, "", "", usage=usage, killed="oom")
        raise SandboxError("The sandbox produced no result." + (f" ({err})" if err else ""))
    header, files = _parse_frame(data, token, lim)
    if header["exit"] in (-9, 137) and not header["timed_out"]:
        killed = killed or "oom"
    usage["seconds_total"] = seconds
    return Result(header["exit"], bool(header["timed_out"]), header["seconds"], header["stdout"], header["stderr"], files,
                  bool(header.get("files_truncated")), usage, killed)


def reap(host: str, older_than_s: int = 600) -> int:
    """Remove sandbox containers that outlived their call (a crashed worker). Matched by label and age only."""
    r = _docker(host, "ps", "-a", "--filter", "label=semechka.sandbox=1", "--format", '{{.Names}} {{.Label "semechka.started"}}', timeout=15)
    n, now = 0, time.time()
    for line in r.stdout.decode().splitlines():
        name, _, started = line.partition(" ")
        stale = name.startswith("smx-") and started.isdigit() and now - int(started) > older_than_s
        if stale and _docker(host, "rm", "-f", name, timeout=15).returncode == 0:
            n += 1
    return n
