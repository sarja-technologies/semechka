"""Runs INSIDE the sandbox container, as an unprivileged user, with no network and a read-only root filesystem.

Protocol on stdin:  one JSON line (the job), then a tar archive of input files.
Protocol on stdout: the host's random TOKEN, a JSON header line, the raw tar of /work/out, then TOKEN again.
Everything the job prints goes to files under /tmp, never to this process's stdout, so a job cannot forge the frame;
and the host treats the whole frame as untrusted data anyway (size-capped, regular files only).
"""
import io
import json
import os
import resource
import subprocess
import sys
import tarfile
import threading
import time

WORK = "/work"
IN, OUT = f"{WORK}/in", f"{WORK}/out"
MAX_IN_FILES = 200


def safe_extract(blob: bytes, dest: str, max_bytes: int) -> int:
    n = total = 0
    with tarfile.open(fileobj=io.BytesIO(blob), mode="r:") as tf:
        for m in tf:
            if not m.isreg():  # no links, devices, fifos: only plain files
                continue
            name = os.path.normpath(m.name).lstrip("/")
            if name.startswith("..") or "/../" in f"/{name}/":
                continue
            n += 1
            total += m.size
            if n > MAX_IN_FILES or total > max_bytes:
                raise SystemExit("input too large")
            path = os.path.join(dest, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(tf.extractfile(m).read())
    return n


def pack_out(max_bytes: int) -> tuple[bytes, list[str], bool]:
    buf, names, total, truncated = io.BytesIO(), [], 0, False
    with tarfile.open(fileobj=buf, mode="w:") as tf:
        for root, _dirs, files in os.walk(OUT, followlinks=False):
            for fn in sorted(files):
                p = os.path.join(root, fn)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                size = os.path.getsize(p)
                if total + size > max_bytes or len(names) >= 50:
                    truncated = True
                    continue
                tf.add(p, arcname=os.path.relpath(p, OUT), recursive=False)
                names.append(os.path.relpath(p, OUT))
                total += size
    return buf.getvalue(), names, truncated


def main() -> None:
    raw = sys.stdin.buffer
    job = json.loads(raw.readline())
    token = job["token"].encode()
    os.makedirs(IN, exist_ok=True)
    os.makedirs(OUT, exist_ok=True)
    safe_extract(raw.read(), IN, job["max_in_bytes"])
    if job["mode"] == "python":
        code_path = f"{WORK}/main.py"
        with open(code_path, "w") as f:
            f.write(job["code"])
        argv = [sys.executable, "-I", "-B", code_path]
    else:
        argv = job["argv"]
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": WORK, "LANG": "C.UTF-8", "MPLBACKEND": "Agg", "MPLCONFIGDIR": "/tmp/mpl",
           "IN": IN, "OUT": OUT}
    t0 = time.monotonic()
    timed_out = False
    cap = job["max_log_bytes"]
    logs = {"out": bytearray(), "err": bytearray()}
    cut = {"out": False, "err": False}

    def drain(stream, key):  # keep the first `cap` bytes, discard the rest, never block the job and never touch disk
        while True:
            chunk = stream.read1(65536)  # returns what is available now; read() would wait for 64 KiB or EOF
            if not chunk:
                return
            room = cap - len(logs[key])
            if room > 0:
                logs[key] += chunk[:room]
            if len(chunk) > room:
                cut[key] = True
    p = subprocess.Popen(argv, cwd=WORK, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         start_new_session=True, preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
    readers = [threading.Thread(target=drain, args=(p.stdout, "out"), daemon=True),
               threading.Thread(target=drain, args=(p.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()
    try:
        code = p.wait(timeout=job["timeout"])
    except subprocess.TimeoutExpired:
        timed_out = True
        code = p.wait(timeout=5) if os.killpg(p.pid, 9) is None else 0
    try:  # nothing the job started may outlive it: end the whole process group, then read what it wrote
        os.killpg(p.pid, 9)
    except ProcessLookupError:
        pass
    for r in readers:
        r.join(3)
    out_text, out_cut = logs["out"].decode("utf-8", "replace"), cut["out"]
    err_text, err_cut = logs["err"].decode("utf-8", "replace"), cut["err"]
    blob, names, truncated = pack_out(job["max_out_bytes"])
    header = {"exit": code, "timed_out": timed_out, "seconds": round(time.monotonic() - t0, 3), "stdout": out_text, "stderr": err_text,
              "stdout_cut": out_cut, "stderr_cut": err_cut, "files": names, "files_truncated": truncated, "tar_bytes": len(blob)}
    w = sys.stdout.buffer
    w.write(token + b"\n" + json.dumps(header).encode() + b"\n" + blob + b"\n" + token + b"\n")
    w.flush()


if __name__ == "__main__":
    main()
