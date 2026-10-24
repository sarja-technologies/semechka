"""Real host telemetry only. Anything that cannot be measured is returned as None and labelled unavailable."""
from __future__ import annotations

import os
import platform
import shutil
import subprocess


def _read(path: str) -> str | None:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return None


def host() -> dict:
    out: dict = {"os": platform.platform(), "arch": platform.machine(), "cpu_count": os.cpu_count()}
    mi = _read("/proc/meminfo")
    if mi:
        kv = {l.split(":")[0]: int(l.split()[1]) for l in mi.splitlines() if ":" in l and len(l.split()) > 1}
        out["memory_total_mb"] = kv["MemTotal"] // 1024
        out["memory_available_mb"] = kv.get("MemAvailable", 0) // 1024
    la = _read("/proc/loadavg")
    out["load_1m"] = float(la.split()[0]) if la else None
    model = None
    ci = _read("/proc/cpuinfo")
    if ci:
        for l in ci.splitlines():
            if l.lower().startswith("model name"):
                model = l.split(":", 1)[1].strip()
                break
    if not model and (exe := shutil.which("lscpu")):  # aarch64 has no "model name" in /proc/cpuinfo
        try:
            r = subprocess.run([exe], capture_output=True, text=True, timeout=3, env={**os.environ, "LC_ALL": "C"})
            names = []
            for l in r.stdout.splitlines():
                if l.strip().startswith("Model name:"):
                    n = l.split(":", 1)[1].strip()
                    if n and n not in names:
                        names.append(n)
            model = " + ".join(names) or None
        except (OSError, subprocess.SubprocessError):
            pass
    out["cpu_model"] = model
    return out


def storage(path: str) -> dict:
    try:
        u = shutil.disk_usage(path)
        return {"total_gb": round(u.total / 2**30, 1), "free_gb": round(u.free / 2**30, 1)}
    except OSError:
        return {"total_gb": None, "free_gb": None}


def gpus() -> list[dict] | None:
    """NVIDIA only (via nvidia-smi). None means 'no supported accelerator tooling found'."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        r = subprocess.run([exe, "--query-gpu=index,name,memory.total,memory.used,utilization.gpu,temperature.gpu,"
                            "driver_version", "--format=csv,noheader,nounits"], capture_output=True, text=True,
                           timeout=5)
        if r.returncode != 0:
            return None
        out = []
        for line in r.stdout.strip().splitlines():
            p = [x.strip() for x in line.split(",")]

            def num(x):
                try:
                    return float(x)
                except ValueError:
                    return None
            g = {"index": int(p[0]), "name": p[1], "vram_total_mb": num(p[2]), "vram_used_mb": num(p[3]),
                 "utilization_pct": num(p[4]), "temperature_c": num(p[5]), "driver": p[6]}
            # Grace-class (aarch64) parts share system RAM with the CPU and nvidia-smi reports memory as [N/A]:
            # say so instead of inventing a VRAM figure.
            g["unified_memory"] = g["vram_total_mb"] is None and platform.machine() in ("aarch64", "arm64")
            out.append(g)
        return out
    except (OSError, subprocess.SubprocessError):
        return None
