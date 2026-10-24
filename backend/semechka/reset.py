#!/usr/bin/env python3
"""`semechka reset`: put a disposable instance back to a factory-fresh first run.

    semechka reset --list
    semechka reset --instance NAME --designate [--engine-url URL --engine-key-file FILE]   (once, marks it disposable)
    semechka reset --instance NAME                       (asks you to type NAME)
    semechka reset --instance NAME --restore BACKUP_DIR  (undo a reset)

Safety rules, in order:
- An instance is a systemd user unit `semechka[-NAME].service` that runs `semechka serve`; its data directory is the
  unit's SEMECHKA_DATA_DIR. Ports are never used to tell instances apart.
- Only an instance whose data directory holds the owner-only marker `.semechka-disposable.json` naming that instance
  can be reset. The marker is written by `--designate`, never automatically, and survives resets.
- The data directory must not be shared with, contain, or sit inside another instance's data directory, and no
  process other than the unit's own may have files open in it.
- You type the instance name (or pass `--yes NAME` when there is no terminal).
- Nothing is deleted: everything in the data directory except the marker is moved into a backup directory outside
  it (`<data>.reset-backups/<time>/`, owner-only), integrity-checked and listed in SHA256SUMS before the service
  starts again.
- The program is not touched. On start it finds an empty data directory and does what a first install does: a new
  database at the current schema, a new master key (so every old session cookie and encrypted value is dead) and
  a new one-time setup token.

Nothing here runs on boot or on service restart; a reset happens only when this command is run.
This module uses the standard library only, so it also runs as a standalone script on hosts whose installed release
predates the command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

MARKER = ".semechka-disposable.json"


@dataclass
class Instance:
    name: str
    unit: str
    unit_path: Path
    data_dir: Path
    program: Path | None
    host: str
    port: int
    env: dict[str, str]

    @property
    def probe(self) -> str:
        h = "127.0.0.1" if self.host in ("", "0.0.0.0", "::") else self.host
        return f"http://{h}:{self.port}"


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True)


def _holders(path: Path) -> set[int]:
    """Processes with a file open under `path` (from /proc; only processes this user can inspect)."""
    root = str(path.resolve())
    out = set()
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            for fd in (p / "fd").iterdir():
                t = os.readlink(fd)
                if t == root or t.startswith(root + "/"):
                    out.add(int(p.name))
                    break
        except OSError:
            continue
    return out


def _get(url: str, headers: dict | None = None, timeout: float = 5) -> tuple[int, dict | None]:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (OSError, ValueError):
        return 0, None


@dataclass
class Host:
    """Everything that touches the machine, so tests can stand in for systemd."""
    unit_dir: Path = field(default_factory=lambda: Path.home() / ".config/systemd/user")
    systemctl: Callable[..., subprocess.CompletedProcess] = _systemctl
    holders: Callable[[Path], set[int]] = _holders
    get: Callable[..., tuple[int, dict | None]] = _get
    wait: float = 60.0


class Refused(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"Refused: {msg}\nNothing was changed.")


def _say(*a):
    print(*a, flush=True)


# ---- instances -----------------------------------------------------------------------------------------------------

def _unit_values(text: str) -> tuple[dict[str, str], list[str]]:
    env: dict[str, str] = {}
    cmd: list[str] = []
    section = ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
        elif section == "[Service]" and line.startswith("Environment="):
            for item in shlex.split(line[len("Environment="):]):
                if "=" in item:
                    k, v = item.split("=", 1)
                    env[k] = v
        elif section == "[Service]" and line.startswith("ExecStart="):
            cmd = shlex.split(line[len("ExecStart="):].lstrip("-@+!:"))
    return env, cmd


def instances(host: Host) -> dict[str, Instance]:
    out = {}
    for p in sorted(host.unit_dir.glob("semechka*.service")):
        env, cmd = _unit_values(p.read_text())
        if len(cmd) < 2 or Path(cmd[0]).name != "semechka" or cmd[1] != "serve":
            continue  # e.g. a Sarness engine unit that happens to share the prefix
        name = p.stem[len("semechka-"):] if p.stem.startswith("semechka-") else "default"
        data = Path(env.get("SEMECHKA_DATA_DIR") or Path.home() / ".local/share/semechka").expanduser()
        out[name] = Instance(name, p.name, p, data, Path(cmd[0]), env.get("SEMECHKA_HOST", "127.0.0.1"),
                             int(env.get("SEMECHKA_PORT", "8780")), env)
    return out


def _overlaps(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a in b.parents or b in a.parents


def marker(inst: Instance) -> dict | None:
    p = inst.data_dir / MARKER
    try:
        st = p.lstat()
    except FileNotFoundError:
        return None
    if not p.is_file() or p.is_symlink() or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused(f"{p} exists but is not an owner-only regular file; fix or remove it by hand.")
    try:
        m = json.loads(p.read_text())
    except ValueError:
        raise Refused(f"{p} is not valid JSON.")
    return m


def _check_target(inst: Instance, all_: dict[str, Instance], need_marker: bool = True) -> dict | None:
    d = inst.data_dir
    if "SEMECHKA_DATABASE_URL" in inst.env:
        raise Refused(f"{inst.unit} uses an external database (SEMECHKA_DATABASE_URL); reset handles SQLite data directories only.")
    if not d.is_absolute() or d.is_symlink() or not d.is_dir():
        raise Refused(f"the data directory {d} is not an existing, real, absolute directory.")
    if d.resolve() in (Path("/"), Path.home().resolve()) or d.resolve() in Path.home().resolve().parents:
        raise Refused(f"the data directory {d} is the home directory or above it.")
    st = d.stat()
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise Refused(f"the data directory {d} must be owned by you and not readable by others (0700).")
    for other in all_.values():
        if other.name != inst.name and _overlaps(other.data_dir, d):
            raise Refused(f"{inst.name}'s data directory {d} overlaps {other.name}'s ({other.data_dir}).")
    m = marker(inst)
    if need_marker:
        if m is None:
            raise Refused(f"{inst.name} is not designated disposable. Only an instance marked with "
                          f"`semechka reset --instance {inst.name} --designate` can be reset.")
        if m.get("instance") != inst.name:
            raise Refused(f"the disposable marker in {d} names '{m.get('instance')}', not '{inst.name}'.")
    return m


def _describe(inst: Instance) -> dict:
    info: dict = {"version": None, "setup": "no database", "app_name": None, "users": None, "conversations": None,
                  "sessions": None}
    if inst.program and inst.program.exists():
        r = subprocess.run([str(inst.program), "--version"], capture_output=True, text=True)
        info["version"] = (r.stdout.strip().split() or [None])[-1]
    db = inst.data_dir / "semechka.db"
    if db.exists():
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            n = lambda t: c.execute(f"select count(*) from {t}").fetchone()[0]  # noqa: E731
            info.update(users=n("users"), conversations=n("conversations"), sessions=n("sessions"))
            info["setup"] = "complete" if c.execute("select 1 from meta where key='setup_complete'").fetchone() else "pending"
            row = c.execute("select value from setting_values where key='brand.app_name' and scope_type='deployment'").fetchone()
            info["app_name"] = json.loads(row[0]).get("v") if row else None
        except sqlite3.Error as e:
            info["setup"] = f"unreadable ({e})"
        finally:
            c.close()
    return info


def _confirm(name: str, given: str | None, action: str) -> None:
    if given is not None:
        if given != name:
            raise Refused(f"--yes was given '{given}', which is not the instance name '{name}'.")
        return
    if not sys.stdin.isatty():
        raise Refused(f"no terminal to confirm on. Pass --yes {name} to confirm without one.")
    if input(f"Type the instance name ({name}) to {action}: ").strip() != name:
        raise Refused("the name typed did not match.")


# ---- service control -----------------------------------------------------------------------------------------------

def _main_pid(host: Host, inst: Instance) -> int:
    r = host.systemctl("show", inst.unit, "-p", "MainPID", "--value")
    try:
        return int(r.stdout.strip() or 0)
    except ValueError:
        return 0


def _stop(host: Host, inst: Instance) -> None:
    pid = _main_pid(host, inst)
    strangers = host.holders(inst.data_dir) - {pid}
    if strangers:
        raise Refused(f"processes {sorted(strangers)} other than {inst.unit} have files open in {inst.data_dir}.")
    r = host.systemctl("stop", inst.unit)
    if r.returncode != 0:
        raise Refused(f"`systemctl --user stop {inst.unit}` failed: {r.stderr.strip()}")
    deadline = time.time() + host.wait
    while time.time() < deadline:
        if host.systemctl("is-active", inst.unit).stdout.strip() != "active" and not host.holders(inst.data_dir):
            return
        time.sleep(0.25)
    host.systemctl("start", inst.unit)
    raise Refused(f"{inst.unit} did not let go of {inst.data_dir} within {host.wait:.0f} s; it was started again.")


def _start(host: Host, inst: Instance) -> bool:
    host.systemctl("start", inst.unit)
    deadline = time.time() + host.wait
    while time.time() < deadline:
        if host.get(inst.probe + "/api/health", timeout=2)[0] == 200:
            return True
        time.sleep(0.5)
    return False


# ---- backup --------------------------------------------------------------------------------------------------------

def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _seal(dest: Path) -> int:
    """Owner-only permissions, then SHA256SUMS over every file, then read it all back to check."""
    for p in [dest, *dest.rglob("*")]:
        if not p.is_symlink():
            os.chmod(p, 0o700 if p.is_dir() else 0o600)
    files = sorted(p for p in dest.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    lines = [f"{_sha256(p)}  {p.relative_to(dest)}" for p in files]
    fd = os.open(dest / "SHA256SUMS", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write("\n".join(lines) + "\n")
    verify_sums(dest)
    return len(files)


def verify_sums(dest: Path) -> None:
    for line in (dest / "SHA256SUMS").read_text().splitlines():
        digest, rel = line.split("  ", 1)
        if _sha256(dest / rel) != digest:
            raise SystemExit(f"Backup check failed: {dest / rel} does not match SHA256SUMS.")


def _backup_root(inst: Instance) -> Path:
    return inst.data_dir.parent / f"{inst.data_dir.name}.reset-backups"


def move_out(inst: Instance, label: str, before: dict) -> Path:
    """Move everything except the marker into a fresh owner-only backup directory; return it. Nothing is deleted."""
    root = _backup_root(inst)
    if _overlaps(root, inst.data_dir):
        raise Refused(f"the backup location {root} overlaps the data directory.")
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    stem, n = f"{time.strftime('%Y%m%d-%H%M%S')}-{label}", 1
    dest = root / stem
    while dest.exists():  # two resets within one second
        n += 1
        dest = root / f"{stem}-{n}"
    dest.mkdir(mode=0o700)
    same_fs = os.stat(dest).st_dev == os.stat(inst.data_dir).st_dev
    moved = []
    for entry in sorted(inst.data_dir.iterdir()):
        if entry.name == MARKER:
            shutil.copy2(entry, dest / MARKER)
            continue
        if same_fs:
            os.rename(entry, dest / entry.name)
        elif entry.is_dir() and not entry.is_symlink():
            shutil.copytree(entry, dest / entry.name, symlinks=True)
        else:
            shutil.copy2(entry, dest / entry.name, follow_symlinks=False)
        moved.append(entry)
    db = dest / "semechka.db"
    integrity = "no database"
    if db.exists():
        c = sqlite3.connect(db)  # closing it folds a moved WAL file back into the database
        integrity = c.execute("pragma integrity_check").fetchone()[0]
        c.close()
        if integrity != "ok":
            raise SystemExit(f"The moved database failed its integrity check ({integrity}). The service is stopped and "
                             f"the data is in {dest}; move it back by hand to recover.")
    key = dest / "master.key"
    manifest = {"instance": inst.name, "unit": inst.unit, "data_dir": str(inst.data_dir), "label": label,
                "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "program_version": before.get("version"),
                "setup": before.get("setup"), "app_name": before.get("app_name"), "users": before.get("users"),
                "conversations": before.get("conversations"), "database_integrity": integrity,
                "master_key_sha256_prefix": _sha256(key)[:12] if key.exists() else None}
    fd = os.open(dest / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(manifest, f, indent=1)
    n = _seal(dest)
    if not same_fs:  # copied across file systems: remove the originals only once the copy has verified
        for entry in moved:
            shutil.rmtree(entry) if entry.is_dir() and not entry.is_symlink() else entry.unlink()
    _say(f"  [ ok ] backup: {dest}  ({n} files, database integrity {integrity}, SHA256SUMS verified)")
    return dest


def _write_hints(inst: Instance, m: dict) -> list[str]:
    """Rebuild the installer's engine hints so the wizard can connect without anyone copying a secret. The key is read
    from its owner-only file at reset time; the marker holds only the file's path."""
    rows = []
    for h in m.get("backend_hints") or []:
        kf = Path(h["api_key_file"]) if h.get("api_key_file") else None
        row = {k: h[k] for k in ("id", "adapter", "label", "base_url") if k in h}
        if kf:
            st = kf.stat()
            if st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise SystemExit(f"{kf} must be owned by you and owner-only (0600); engine hint not written.")
            row["api_key"] = kf.read_text().strip()
        rows.append(row)
    if rows:
        fd = os.open(inst.data_dir / "backend-hints.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(rows, f)
    return [r.get("label") or r["adapter"] for r in rows]


# ---- commands ------------------------------------------------------------------------------------------------------

def cmd_list(host: Host) -> int:
    all_ = instances(host)
    if not all_:
        _say(f"No Semechka units in {host.unit_dir}.")
    for i in all_.values():
        try:
            m = marker(i)
            tag = "disposable" if m and m.get("instance") == i.name else "protected (not designated disposable)"
        except SystemExit:
            tag = "marker unreadable"
        _say(f"{i.name:<12} {i.unit:<28} {i.host}:{i.port:<6} {i.data_dir}  [{tag}]")
    return 0


def cmd_designate(host: Host, inst: Instance, a) -> int:
    all_ = instances(host)
    if _check_target(inst, all_, need_marker=False) is not None:
        raise Refused(f"{inst.name} is already designated disposable.")
    d = _describe(inst)
    _say(f"Designating {inst.name} ({inst.unit}, data {inst.data_dir}, '{d['app_name'] or 'unnamed'}', "
         f"{d['users']} users) as DISPOSABLE: from now on `semechka reset --instance {inst.name}` may wipe it.")
    _confirm(inst.name, a.yes, f"mark {inst.name} disposable")
    m: dict = {"instance": inst.name, "designated": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    if a.engine_url:
        if not a.engine_url.startswith(("http://", "https://")):
            raise Refused("--engine-url must start with http:// or https://")
        m["backend_hints"] = [{"id": "local-engine", "adapter": a.engine_adapter, "label": a.engine_label,
                               "base_url": a.engine_url.rstrip("/"),
                               **({"api_key_file": str(Path(a.engine_key_file).expanduser().resolve())} if a.engine_key_file else {})}]
    fd = os.open(inst.data_dir / MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(m, f, indent=1)
    _say(f"  [ ok ] {inst.data_dir / MARKER} written. Nothing else was changed.")
    return 0


def cmd_reset(host: Host, inst: Instance, a) -> int:
    all_ = instances(host)
    m = _check_target(inst, all_)
    before = _describe(inst)
    others = ", ".join(f"{o.name} ({o.data_dir})" for o in all_.values() if o.name != inst.name) or "none"
    _say(f"FACTORY RESET of instance '{inst.name}'\n"
         f"  unit           {inst.unit}\n  data directory {inst.data_dir}\n  address        {inst.host}:{inst.port}\n"
         f"  program        {inst.program} (version {before['version']}; not changed)\n"
         f"  current state  '{before['app_name'] or 'unnamed'}', setup {before['setup']}, {before['users']} users, "
         f"{before['conversations']} conversations, {before['sessions']} sessions\n"
         f"  not touched    {others}")
    _confirm(inst.name, a.yes, "erase it and start again from the first-run wizard")
    _stop(host, inst)
    _say(f"  [ ok ] {inst.unit} stopped; nothing has files open in {inst.data_dir}")
    dest = move_out(inst, "before-reset", before)
    hints = _write_hints(inst, m)
    if hints:
        _say(f"  [ ok ] engine connection offered to the wizard: {', '.join(hints)}")
    if not _start(host, inst):
        _say(f"  [FAIL] {inst.unit} did not answer on {inst.probe}/api/health. Data is safe in {dest}. Undo with:\n"
             f"         semechka reset --instance {inst.name} --restore {dest}")
        return 1
    return 0 if _verify_fresh(host, inst, before, dest, hints) else 1


def _verify_fresh(host: Host, inst: Instance, before: dict, dest: Path, hints: list[str]) -> bool:
    ok = True

    def check(cond: bool, msg: str) -> None:
        nonlocal ok
        ok &= bool(cond)
        _say(f"  [{' ok ' if cond else 'FAIL'}] {msg}")
    now = _describe(inst)
    status, body = host.get(inst.probe + "/api/setup/status")
    check(status == 200 and body and body.get("needs_setup") is True, "the first-run wizard is open (setup pending)")
    check(now["version"] == before["version"], f"program version unchanged ({now['version']})")
    check(now["users"] == 0 and now["conversations"] == 0 and now["sessions"] == 0,
          "no accounts, conversations or sessions")
    tok = inst.data_dir / "setup-token"
    check(tok.exists() and not tok.stat().st_mode & 0o077, "a new one-time setup token exists (owner-only)")
    key, old = inst.data_dir / "master.key", dest / "master.key"
    check(key.exists() and (not old.exists() or _sha256(key) != _sha256(old)),
          "a new master key (old session cookies and encrypted values cannot be used)")
    if hints and tok.exists():
        st, hw = host.get(inst.probe + "/api/setup/hardware", {"x-setup-token": tok.read_text().strip()}, timeout=30)
        found = [b for b in (hw or {}).get("backends", []) if b.get("hint_id")]
        check(st == 200 and found and all(b.get("reachable") for b in found),
              "the wizard's engine step reaches the engine: " + (", ".join(f"{b['label']} ({'reachable' if b.get('reachable') else 'unreachable'})" for b in found) or "none found"))
    _say(f"\n{'Reset complete.' if ok else 'Reset finished with problems (see FAIL above).'}\n"
         f"  Open:        http://{inst.host}:{inst.port}\n"
         f"  Setup token: cat {tok}     (one time; deleted when the wizard completes)\n"
         f"  Backup:      {dest}\n"
         f"  Undo:        semechka reset --instance {inst.name} --restore {dest}")
    return ok


def cmd_restore(host: Host, inst: Instance, a) -> int:
    all_ = instances(host)
    _check_target(inst, all_)
    src = Path(a.restore).expanduser().resolve()
    try:
        man = json.loads((src / "manifest.json").read_text())
    except (OSError, ValueError):
        raise Refused(f"{src} is not a reset backup (no readable manifest.json).")
    if man.get("instance") != inst.name:
        raise Refused(f"{src} is a backup of '{man.get('instance')}', not '{inst.name}'.")
    verify_sums(src)
    before = _describe(inst)
    _say(f"RESTORE instance '{inst.name}' from {src}\n  backup of      '{man.get('app_name') or 'unnamed'}', setup "
         f"{man.get('setup')}, {man.get('users')} users, taken {man.get('created')}\n  current data   is moved to a "
         f"new backup first ('{before['app_name'] or 'unnamed'}', setup {before['setup']}, {before['users']} users)")
    _confirm(inst.name, a.yes, "replace its current data with this backup")
    _stop(host, inst)
    keep = move_out(inst, "before-restore", before)
    for entry in sorted(src.iterdir()):
        if entry.name in ("manifest.json", "SHA256SUMS", MARKER):
            continue
        if entry.is_dir():
            shutil.copytree(entry, inst.data_dir / entry.name, symlinks=True)
        else:
            shutil.copy2(entry, inst.data_dir / entry.name)
    if not _start(host, inst):
        _say(f"  [FAIL] {inst.unit} did not answer after the restore. The data it replaced is in {keep}.")
        return 1
    status, body = host.get(inst.probe + "/api/setup/status")
    _say(f"  [ ok ] restored and running; setup {'pending' if body and body.get('needs_setup') else 'complete'}. "
         f"The data it replaced is in {keep}.")
    return 0


def main(argv: list[str] | None = None, host: Host | None = None) -> int:
    ap = argparse.ArgumentParser(prog="semechka reset", description="Return a disposable instance to a factory-fresh "
                                 "first run. Refuses any instance not designated disposable.")
    ap.add_argument("--instance", help="instance name (the NAME in semechka-NAME.service; 'default' for semechka.service)")
    ap.add_argument("--list", action="store_true", help="list instances and whether each may be reset")
    ap.add_argument("--yes", metavar="NAME", help="confirm without a terminal; must equal the instance name")
    ap.add_argument("--restore", metavar="BACKUP_DIR", help="put back the data from a reset backup")
    ap.add_argument("--designate", action="store_true", help="mark the instance disposable (one time)")
    ap.add_argument("--engine-url", help="with --designate: an engine the fresh wizard should offer")
    ap.add_argument("--engine-key-file", help="with --designate: owner-only file holding that engine's key")
    ap.add_argument("--engine-adapter", default="sarness")
    ap.add_argument("--engine-label", default="Sarness Engine (this machine)")
    a = ap.parse_args(argv)
    host = host or Host()
    if a.list:
        return cmd_list(host)
    if not a.instance:
        ap.error("--instance is required (see --list)")
    all_ = instances(host)
    if a.instance not in all_:
        raise Refused(f"no instance '{a.instance}'. Known: {', '.join(all_) or 'none'} (units in {host.unit_dir}).")
    inst = all_[a.instance]
    if a.designate:
        return cmd_designate(host, inst, a)
    if a.restore:
        return cmd_restore(host, inst, a)
    return cmd_reset(host, inst, a)


def _script() -> None:
    """Standalone use: `semechka-reset --instance NAME`, or installed as a host's `semechka` command that offers only
    `reset` (each instance keeps its own `semechka-NAME` command for everything else)."""
    args = sys.argv[1:]
    if args[:1] == ["reset"]:
        args = args[1:]
    elif args and not args[0].startswith("-"):
        names = ", ".join(f"semechka-{n}" for n in instances(Host()) if n != "default")
        sys.exit(f"This `semechka` command provides only `reset`. Each instance has its own command for everything "
                 f"else: {names or 'see ~/.local/bin'}.")
    sys.exit(main(args))


if __name__ == "__main__":
    _script()
