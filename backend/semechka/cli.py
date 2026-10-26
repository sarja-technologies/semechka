"""`semechka` command line: serve, doctor, status, backup, restore, update, uninstall, reset."""
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

from . import __version__, migrations
from .config import Config
from .crypto import (
    SMCK2,
    SealReader,
    SealWriter,
    open_with_passphrase,
)


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
    try:
        app = create_app(cfg)
    except migrations.SchemaTooNew as e:
        raise SystemExit(f"Refusing to start: {e}")
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


BACKUP_DIRS = ("assets", "attachments", "workspaces")  # everything on disk that the database refers to


def _add_tree(tf, root: Path, arc: str) -> None:
    """Add a directory to a tar stream, tolerating files that vanish while the service keeps running."""
    for dp, _dirs, files in os.walk(root):
        for fn in sorted(files):
            p = Path(dp) / fn
            try:
                tf.add(p, arcname=f"{arc}/{p.relative_to(root).as_posix()}", recursive=False)
            except FileNotFoundError:
                continue


def _write_snapshot(cfg: Config, sink, *, sealed_chunk: int | None = None, passphrase: str) -> None:
    if cfg.database_url and not cfg.database_url.startswith("sqlite"):
        raise SystemExit("Backup of external databases is not implemented. Use your database's own tooling together "
                         f"with a copy of {cfg.key_path}.")
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "semechka.db"
        src, dst = sqlite3.connect(cfg.data_dir / "semechka.db"), sqlite3.connect(copy)
        src.backup(dst)  # consistent online copy; files copied afterwards can only be newer than the rows
        dst.close()
        src.close()
        w = SealWriter(sink, passphrase, **({"chunk": sealed_chunk} if sealed_chunk else {}))
        with tarfile.open(fileobj=w, mode="w|gz") as tf:
            tf.add(copy, arcname="semechka.db")
            tf.add(cfg.key_path, arcname="master.key")
            for d in BACKUP_DIRS:
                if (cfg.data_dir / d).exists():
                    _add_tree(tf, cfg.data_dir / d, d)
            meta = json.dumps({"version": __version__, "created": time.time(), "format": 2, "dirs": list(BACKUP_DIRS)}).encode()
            ti = tarfile.TarInfo("backup.json")
            ti.size = len(meta)
            tf.addfile(ti, io.BytesIO(meta))
        w.close()


def cmd_backup(a, cfg: Config):
    pw = os.environ.get("SEMECHKA_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
    if len(pw) < 12:
        raise SystemExit("Use a passphrase of at least 12 characters. If you lose it the backup cannot be opened.")
    out = Path(a.output or f"semechka-backup-{time.strftime('%Y%m%d-%H%M%S')}.smck")
    part = out.with_name(out.name + ".part")  # a failed backup never leaves a file that looks complete
    try:
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            _write_snapshot(cfg, f, passphrase=pw)
            f.flush()
            os.fsync(f.fileno())
        if out.exists():
            raise SystemExit(f"{out} already exists; choose another name.")
        os.replace(part, out)
    finally:
        part.unlink(missing_ok=True)
    print(f"Encrypted backup written to {out} ({out.stat().st_size / 1048576:.1f} MiB), including workspace files. "
          "It contains the master key, so keep the passphrase safe.")


def _safe_extract(tf, dest: str) -> None:
    for m in tf:  # stream mode: members are visited once, in order
        if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
            raise SystemExit("Backup contains an unsafe path; refusing.")
        tf.extract(m, dest, filter="data")


def cmd_restore(a, cfg: Config):
    pw = os.environ.get("SEMECHKA_BACKUP_PASSPHRASE") or getpass.getpass("Backup passphrase: ")
    path = Path(a.file)
    if not _port_free("127.0.0.1", cfg.port):
        raise SystemExit("Stop the running service before restoring.")
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    existing = (cfg.data_dir / "semechka.db").exists()
    if existing and not a.force:
        raise SystemExit("A database already exists here. Re-run with --force to replace it (the current data is kept as a .pre-restore copy).")
    with tempfile.TemporaryDirectory(dir=cfg.data_dir) as td:  # same filesystem as the data, so placing it is a rename
        with open(path, "rb") as f:
            magic = f.read(5)
            f.seek(0)
            try:
                if magic == SMCK2:
                    r = SealReader(f, pw)
                    with tarfile.open(fileobj=r, mode="r|gz") as tf:
                        _safe_extract(tf, td)
                    r.verify_end()  # the stream's final chunk must authenticate: a truncated file is refused
                else:
                    with tarfile.open(fileobj=io.BytesIO(open_with_passphrase(path.read_bytes(), pw)), mode="r:gz") as tf:
                        for m in tf.getmembers():
                            if m.name.startswith(("/", "..")) or ".." in Path(m.name).parts or not (m.isfile() or m.isdir()):
                                raise SystemExit("Backup contains an unsafe path; refusing.")
                        tf.extractall(td, filter="data")
            except SystemExit:
                raise
            except (ValueError, tarfile.TarError, OSError, EOFError):
                raise SystemExit("Could not open the backup: wrong passphrase or damaged file. Nothing was changed.") from None
        con = sqlite3.connect(Path(td) / "semechka.db")
        if con.execute("pragma integrity_check").fetchone()[0] != "ok":
            raise SystemExit("Backup database failed its integrity check. Nothing was changed.")
        con.close()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if existing:
            for n in ("semechka.db", "semechka.db-wal", "semechka.db-shm", "master.key", *BACKUP_DIRS):
                p = cfg.data_dir / n
                if p.exists():
                    p.rename(cfg.data_dir / f"{n}.pre-restore-{stamp}")
        shutil.copy2(Path(td) / "semechka.db", cfg.data_dir / "semechka.db")
        shutil.copy2(Path(td) / "master.key", cfg.data_dir / "master.key")
        os.chmod(cfg.data_dir / "master.key", 0o600)
        for d in BACKUP_DIRS:
            if (Path(td) / d).exists():
                shutil.move(str(Path(td) / d), cfg.data_dir / d)
    print("Restored. Start the service with `semechka serve`.")


def _state(cfg: Config):
    from .state import AppState
    cfg.background = False
    return AppState.build(cfg)


def cmd_org(a, cfg: Config):
    """Operator commands for one organisation's data: list, export (sealed), purge (permanent)."""
    from . import tenant_ops_svc as T
    st = _state(cfg)
    with st.db.session() as s:
        if a.action == "list":
            from sqlalchemy import select

            from .models_tenancy import Org
            for o in s.execute(select(Org).order_by(Org.name)).scalars():
                print(f"{o.slug:<24} {o.status:<10} {o.name}")
            return
        try:
            org = T.find_org(s, a.slug)
        except LookupError as e:
            raise SystemExit(str(e)) from None
        if a.action == "export":
            pw = os.environ.get("SEMECHKA_BACKUP_PASSPHRASE") or getpass.getpass("Passphrase for the export: ")
            if len(pw) < 12:
                raise SystemExit("Use a passphrase of at least 12 characters.")
            out = Path(a.output or f"semechka-org-{org.slug}-{time.strftime('%Y%m%d-%H%M%S')}.smck")
            fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                c = T.export_org(st, s, org, f, pw, history=not a.no_history)
            print(f"Exported {c['files']} file version(s), {c['bytes'] / 1048576:.1f} MiB, to {out}. Open it with the same passphrase.")
        elif a.action == "purge":
            if a.confirm != org.slug:
                raise SystemExit(f"This permanently deletes every file, passage, agent and person-link of '{org.name}'. "
                                 f"Re-run with  --confirm {org.slug}  to proceed. There is no undo; take a backup first.")
            if org.is_default and not a.force:
                raise SystemExit("This is the default organisation. Add --force if you really mean it.")
            c = T.purge_org(st, s, org)
            print(f"Purged '{org.slug}': {c['files']} files, {c['workspaces']} workspaces, {c['members']} memberships removed.")


def cmd_update(a, cfg: Config):
    print(f"Installed version: {__version__}   (data schema {migrations.SCHEMA_VERSION})")
    print("Semechka does not update itself silently. To update:\n"
          "  1. Download the new release tarball and verify it:  sha256sum -c semechka-X.Y.Z.tar.gz.sha256\n"
          "  2. Extract it and run its install.sh with the same --prefix/--data (and --name) you installed with.\n"
          "     The installer first saves a consistent snapshot of your database and key under <data>/pre-update/,\n"
          "     installs the new program, restarts the service and applies any database migrations once.\n"
          "  3. semechka doctor\n"
          "Rollback: install.sh --rollback (program only), --rollback --restore-data (program and the snapshot), or\n"
          "  --restore-data alone (the program was already rolled back and refuses to start on the newer data).\n"
          "A program older than its data refuses to start rather than risk corrupting it.\n"
          "Docker Compose installs: docker compose pull && docker compose up -d  (take `semechka backup` first).")


def cmd_uninstall(a, cfg: Config):
    print("This stops nothing automatically. To remove the program: `pip uninstall semechka` or `docker compose down`.")
    if a.purge_data:
        if input(f"Delete ALL data in {cfg.data_dir}? Type the word 'delete' to confirm: ").strip() != "delete":
            raise SystemExit("Cancelled.")
        shutil.rmtree(cfg.data_dir)
        print("Data deleted.")
    else:
        print(f"Your data in {cfg.data_dir} was kept. Re-run with --purge-data to delete it.")


def cmd_migrate(a, cfg: Config):
    """Show the data schema, or move it to another version. Downgrades must run with the NEWER program, before the
    older program is reinstalled; stop the service first."""
    from .db import Database
    if not (cfg.data_dir / "semechka.db").exists() and not cfg.database_url:
        raise SystemExit(f"No database in {cfg.data_dir}.")
    db = Database(cfg.db_url)
    with db.engine.connect() as c:
        have = migrations.current_version(c)
    print(f"Program {__version__} understands schema 1..{migrations.SCHEMA_VERSION}; the data is at schema {have}.")
    if a.to is None:
        for v, step in sorted(migrations.MIGRATIONS.items()):
            print(f"  {v}: {step.description}{'  (applied)' if have and v <= have else ''}")
        return
    if a.to > migrations.SCHEMA_VERSION:
        raise SystemExit(f"This program cannot migrate past schema {migrations.SCHEMA_VERSION}.")
    if have is not None and a.to < have:
        if not a.yes:
            raise SystemExit("A downgrade removes data that only the newer release uses (listed above for each step). "
                             "Take a backup first (`semechka backup`), stop the service, then re-run with --yes.")
        for line in migrations.downgrade(db, a.to, cfg.data_dir):
            print("  " + line)
    else:
        db.create_all()
        for line in migrations.apply(db, cfg.data_dir):
            print("  " + line)
    with db.engine.connect() as c:
        print(f"Data is now at schema {migrations.current_version(c)}.")


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
    mg = sub.add_parser("migrate", help="show or change the data schema version (downgrade before a program rollback)")
    mg.add_argument("--to", type=int)
    mg.add_argument("--yes", action="store_true")
    mg.set_defaults(fn=cmd_migrate)
    u = sub.add_parser("uninstall", help="remove, keeping data unless told otherwise")
    u.add_argument("--purge-data", action="store_true")
    u.set_defaults(fn=cmd_uninstall)
    # Listed here for --help; its own parser handles the options (see reset.py).
    og = sub.add_parser("org", help="operator tools for one organisation's data: list, export (sealed), purge (permanent)")
    og.add_argument("action", choices=["list", "export", "purge"])
    og.add_argument("slug", nargs="?", default="")
    og.add_argument("-o", "--output")
    og.add_argument("--no-history", action="store_true", help="export only current file versions")
    og.add_argument("--confirm", default="", help="for purge: repeat the organisation's short name")
    og.add_argument("--force", action="store_true")
    og.set_defaults(fn=cmd_org)
    sub.add_parser("reset", help="return a disposable instance to the first-run wizard (semechka reset --help)", add_help=False)
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["reset"]:
        from . import reset
        sys.exit(reset.main(argv[1:]))
    a = ap.parse_args(argv)
    a.fn(a, Config())
