"""`semechka` command line: serve, doctor, status, backup, restore, update, uninstall."""
from __future__ import annotations

import argparse
import getpass
import io
import json
import os
import shutil
import socket
import sqlite3
import sys
import tarfile
import tempfile
import time
import urllib.request
from pathlib import Path

from . import __version__
from .config import Config
from .crypto import open_with_passphrase, seal_with_passphrase


def _lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent
            ip = s.getsockname()[0]
            return None if ip.startswith("127.") else ip
    except OSError:
        return None


def _port_free(host: str, port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex((host, port)) != 0


def _configured_exposure(cfg: Config) -> str:
    p = cfg.data_dir / "semechka.db"
    if cfg.database_url or not p.exists():
        return "loopback"
    try:
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        row = con.execute("select value from setting_values where key='access.exposure' and scope_type='deployment'").fetchone()
        con.close()
        return json.loads(row[0])["v"] if row else "loopback"
    except (sqlite3.Error, ValueError, KeyError):
        return "loopback"


def cmd_serve(a, cfg: Config):
    import uvicorn

    from .app import create_app
    if "SEMECHKA_HOST" not in os.environ:
        # The bind address follows the stored 'Network exposure' setting; loopback unless the owner chose LAN.
        cfg.host = "0.0.0.0" if _configured_exposure(cfg) == "lan" else "127.0.0.1"
    if a.host:
        cfg.host = a.host
    if a.port:
        cfg.port = a.port
    if not cfg.web_dir:
        for cand in (Path(__file__).resolve().parent / "web", Path(__file__).resolve().parents[2] / "web" / "build"):
            if (cand / "index.html").exists():
                cfg.web_dir = cand
                break
    os.umask(0o077)  # database, uploads and backups are never group/world readable
    app = create_app(cfg)
    for f in cfg.data_dir.glob("semechka.db*"):
        os.chmod(f, 0o600)
    print(f"Semechka {__version__}")
    print(f"  Local:   http://127.0.0.1:{cfg.port}")
    if cfg.host not in ("127.0.0.1", "localhost", "::1"):
        ip = _lan_ip()
        print(f"  Network: http://{ip}:{cfg.port}   (plain HTTP: use a trusted network or put HTTPS in front)" if ip
              else f"  Network: listening on {cfg.host}:{cfg.port}")
    else:
        print("  Network: not exposed (loopback only). Change 'Network exposure' in Settings to allow other devices.")
    if not cfg.web_dir:
        print("  Note: no built web interface found; only the API is served. Run `pnpm build` in ./web.")
    uvicorn.run(app, host=cfg.host, port=cfg.port, log_level="warning", proxy_headers=False)


def cmd_doctor(a, cfg: Config):
    ok = True

    def line(level, msg):
        nonlocal ok
        if level == "FAIL":
            ok = False
        print(f"[{level:^4}] {msg}")
    v = sys.version_info
    line("OK" if v >= (3, 11) else "FAIL", f"Python {v.major}.{v.minor}.{v.micro} (3.11 or newer required)")
    line("INFO", f"Platform: {sys.platform} {os.uname().machine}")
    try:
        mem = [l for l in open("/proc/meminfo") if l.startswith("MemTotal")][0].split()[1]
        gb = int(mem) / 1048576
        line("OK" if gb >= 2 else "WARN", f"Memory: {gb:.1f} GiB")
    except OSError:
        line("WARN", "Memory: could not be read")
    d = cfg.data_dir
    d.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(d).free / 2**30
    line("OK" if free >= 2 else "WARN", f"Disk free where data lives ({d}): {free:.1f} GiB")
    mode = oct(d.stat().st_mode & 0o777)
    line("OK" if d.stat().st_mode & 0o077 == 0 else "WARN", f"Data directory permissions: {mode} (should be 0o700)")
    if cfg.key_path.exists():
        km = cfg.key_path.stat().st_mode & 0o777
        line("OK" if km & 0o077 == 0 else "FAIL", f"Master key file permissions: {oct(km)} (must not be readable by others)")
    line("OK" if _port_free("127.0.0.1", cfg.port) else "INFO", f"Port {cfg.port}: {'free' if _port_free('127.0.0.1', cfg.port) else 'in use (is Semechka already running?)'}")
    dbp = d / "semechka.db"
    if dbp.exists() and not cfg.database_url:
        try:
            con = sqlite3.connect(f"file:{dbp}?mode=ro", uri=True)
            r = con.execute("pragma integrity_check").fetchone()[0]
            con.close()
            line("OK" if r == "ok" else "FAIL", f"Database integrity: {r}")
        except sqlite3.Error as e:
            line("FAIL", f"Database could not be opened: {e}")
    from . import telemetry
    g = telemetry.gpus()
    line("INFO", "GPU: " + (", ".join(f"{x['name']} ({x['vram_total_mb']:.0f} MiB)" for x in g) if g else "no NVIDIA GPU tooling found (CPU or other accelerators are not detected)"))
    for label, port in (("Sarness Engine", 8742), ("Ollama", 11434), ("llama.cpp/OpenAI-compatible", 8080), ("vLLM", 8000)):
        line("INFO", f"{label} on 127.0.0.1:{port}: {'listening' if not _port_free('127.0.0.1', port) else 'not found'}")
    line("INFO", "TLS: Semechka serves plain HTTP. Use a reverse proxy for HTTPS before exposing it beyond a trusted network.")
    print("\nResult:", "ready" if ok else "problems found")
    sys.exit(0 if ok else 1)


def cmd_status(a, cfg: Config):
    url = f"http://127.0.0.1:{cfg.port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            up = r.status == 200
    except OSError:
        up = False
    print(f"Service: {'running' if up else 'not running'} (checked {url})")
    print(f"Data directory: {cfg.data_dir}")
    print(f"Configured exposure: {_configured_exposure(cfg)}")
    tok = cfg.setup_token_path
    if tok.exists():
        print(f"Setup pending: token is in {tok} (read it with: cat {tok})")
    else:
        print("Setup: complete")
    sys.exit(0 if up else 3)


def _snapshot(cfg: Config) -> bytes:
    if cfg.database_url and not cfg.database_url.startswith("sqlite"):
        raise SystemExit("Backup of external databases is not implemented. Use your database's own tooling together "
                         f"with a copy of {cfg.key_path}.")
    dbp = cfg.data_dir / "semechka.db"
    buf = io.BytesIO()
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "semechka.db"
        src = sqlite3.connect(dbp)
        dst = sqlite3.connect(copy)
        src.backup(dst)  # consistent online copy
        dst.close()
        src.close()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            tf.add(copy, arcname="semechka.db")
            tf.add(cfg.key_path, arcname="master.key")
            assets = cfg.data_dir / "assets"
            if assets.exists():
                tf.add(assets, arcname="assets")
            meta = json.dumps({"version": __version__, "created": time.time()}).encode()
            ti = tarfile.TarInfo("backup.json")
            ti.size = len(meta)
            tf.addfile(ti, io.BytesIO(meta))
    return buf.getvalue()


def cmd_backup(a, cfg: Config):
    pw = os.environ.get("SEMECHKA_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
    if len(pw) < 12:
        raise SystemExit("Use a passphrase of at least 12 characters. If you lose it the backup cannot be opened.")
    out = Path(a.output or f"semechka-backup-{time.strftime('%Y%m%d-%H%M%S')}.smck")
    blob = seal_with_passphrase(_snapshot(cfg), pw)
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(blob)
    print(f"Encrypted backup written to {out} ({len(blob) / 1024:.0f} KiB). It contains the master key, so keep the passphrase safe.")


def cmd_restore(a, cfg: Config):
    pw = os.environ.get("SEMECHKA_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
    try:
        raw = open_with_passphrase(Path(a.file).read_bytes(), pw)
    except Exception:  # noqa: BLE001
        raise SystemExit("Could not open the backup: wrong passphrase or damaged file. Nothing was changed.")
    if not _port_free("127.0.0.1", cfg.port):
        raise SystemExit("Stop the running service before restoring.")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    existing = (cfg.data_dir / "semechka.db").exists()
    if existing and not a.force:
        raise SystemExit("A database already exists here. Re-run with --force to replace it (the current data is kept as a .pre-restore copy).")
    with tempfile.TemporaryDirectory() as td, tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tf:
        for m in tf.getmembers():  # refuse path tricks
            if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                raise SystemExit("Backup contains an unsafe path; refusing.")
        tf.extractall(td, filter="data")
        con = sqlite3.connect(Path(td) / "semechka.db")
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise SystemExit("Backup database failed its integrity check. Nothing was changed.")
        con.close()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if existing:
            for n in ("semechka.db", "semechka.db-wal", "semechka.db-shm", "master.key"):
                p = cfg.data_dir / n
                if p.exists():
                    p.rename(cfg.data_dir / f"{n}.pre-restore-{stamp}")
        shutil.copy2(Path(td) / "semechka.db", cfg.data_dir / "semechka.db")
        shutil.copy2(Path(td) / "master.key", cfg.data_dir / "master.key")
        os.chmod(cfg.data_dir / "master.key", 0o600)
        if (Path(td) / "assets").exists():
            shutil.copytree(Path(td) / "assets", cfg.data_dir / "assets", dirs_exist_ok=True)
    print("Restored. Start the service with `semechka serve`.")


def cmd_update(a, cfg: Config):
    print(f"Installed version: {__version__}")
    print("Semechka does not update itself silently.\n"
          "  1. semechka backup\n"
          "  2. Docker Compose install:  docker compose pull && docker compose up -d\n"
          "     pip install:             pip install --upgrade semechka\n"
          "  3. semechka doctor\n"
          "Rollback: reinstall the previous version and run `semechka restore <backup> --force`.")


def cmd_uninstall(a, cfg: Config):
    print("This stops nothing automatically. To remove the program: `pip uninstall semechka` or `docker compose down`.")
    if a.purge_data:
        if input(f"Delete ALL data in {cfg.data_dir}? Type the word 'delete' to confirm: ").strip() != "delete":
            raise SystemExit("Cancelled.")
        shutil.rmtree(cfg.data_dir)
        print("Data deleted.")
    else:
        print(f"Your data in {cfg.data_dir} was kept. Re-run with --purge-data to delete it.")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="semechka", description="Semechka: a configurable, private AI platform for your own infrastructure.")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="run the server")
    s.add_argument("--host")
    s.add_argument("--port", type=int)
    s.set_defaults(fn=cmd_serve)
    sub.add_parser("doctor", help="check this machine").set_defaults(fn=cmd_doctor)
    sub.add_parser("status", help="show service state").set_defaults(fn=cmd_status)
    b = sub.add_parser("backup", help="write an encrypted backup")
    b.add_argument("-o", "--output")
    b.set_defaults(fn=cmd_backup)
    r = sub.add_parser("restore", help="restore from an encrypted backup")
    r.add_argument("file")
    r.add_argument("--force", action="store_true")
    r.set_defaults(fn=cmd_restore)
    sub.add_parser("update", help="how to update safely").set_defaults(fn=cmd_update)
    u = sub.add_parser("uninstall", help="remove, keeping data unless told otherwise")
    u.add_argument("--purge-data", action="store_true")
    u.set_defaults(fn=cmd_uninstall)
    a = ap.parse_args(argv)
    a.fn(a, Config())
