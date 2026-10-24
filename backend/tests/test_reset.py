"""`semechka reset`: only a designated instance is reset, nothing is deleted, and the real program then starts as a
first install. systemd is replaced by a stand-in that runs the real server in-process for each unit."""
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
from semechka import reset
from semechka.app import create_app
from semechka.config import Config

from .conftest import Server, _free_port

FIXTURE = Path(__file__).parent / "fixtures" / "data-0.2.0"


class FakeSystemd:
    """`systemctl --user` for units in a temporary directory: start/stop run the real Semechka app."""

    def __init__(self, units: dict):
        self.units = units  # unit name -> (data_dir, port)
        self.running: dict[str, Server] = {}
        self.calls: list[tuple] = []

    def __call__(self, *args):
        self.calls.append(args)
        verb, unit = args[0], args[1]
        out = ""
        if verb == "start" and unit not in self.running:
            data, port = self.units[unit]
            cfg = Config(data_dir=data, port=port)
            cfg.background = False
            srv = Server(create_app(cfg), port)
            srv.start()
            self.running[unit] = srv
        elif verb == "stop" and unit in self.running:
            self.running.pop(unit).stop()
        elif verb == "is-active":
            out = "active" if unit in self.running else "inactive"
        elif verb == "show":
            out = str(os.getpid()) if unit in self.running else "0"
        return subprocess.CompletedProcess(args, 0, out, "")

    def holders(self, path):
        return {os.getpid()} if any(self.units[u][0] == path for u in self.running) else set()

    def stop_all(self):
        for u in list(self.running):
            self.running.pop(u).stop()


def unit(d: Path, port: int) -> str:
    return (f"[Unit]\nDescription=Semechka\n\n[Service]\nEnvironment=SEMECHKA_DATA_DIR={d}\nEnvironment=SEMECHKA_PORT={port}\n"
            f"Environment=SEMECHKA_HOST=127.0.0.1\nExecStart={d.parent}/app/venv/bin/semechka serve\n")


def tree_digest(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


@pytest.fixture
def two(tmp_path):
    """Two configured instances (copies of a real 0.2.0 data directory, upgraded on first start), both running."""
    units = tmp_path / "units"
    units.mkdir()
    sd = {}
    for name in ("roasted", "salted"):
        d = tmp_path / name / "data"
        shutil.copytree(FIXTURE, d)
        os.chmod(d, 0o700)
        (d / "master.key").chmod(0o600)
        port = _free_port()
        (units / f"semechka-{name}.service").write_text(unit(d, port))
        sd[f"semechka-{name}.service"] = (d, port)
    # an engine unit sharing the prefix is not an instance
    (units / "semechka-sarness.service").write_text("[Service]\nExecStart=/opt/sarness-engine serve --port 8752\n")
    fake = FakeSystemd(sd)
    for u in sd:
        fake("start", u)
    host = reset.Host(unit_dir=units, systemctl=fake, holders=fake.holders, wait=10)
    yield host, fake, {n: sd[f"semechka-{n}.service"][0] for n in ("roasted", "salted")}
    fake.stop_all()


def designate(host, name, *extra):
    return reset.main(["--instance", name, "--designate", "--yes", name, *extra], host)


def test_instances_come_from_units_and_only_designated_ones_are_disposable(two, capsys):
    host, fake, data = two
    assert set(reset.instances(host)) == {"roasted", "salted"}
    with pytest.raises(SystemExit, match="not designated disposable"):
        reset.main(["--instance", "salted", "--yes", "salted"], host)
    with pytest.raises(SystemExit, match="no instance 'nope'"):
        reset.main(["--instance", "nope", "--yes", "nope"], host)
    assert designate(host, "roasted") == 0
    reset.main(["--list"], host)
    out = capsys.readouterr().out
    assert "roasted" in out and "[disposable]" in out and "salted" in out and "protected" in out
    assert not any(c[0] == "stop" for c in fake.calls)


def test_confirmation_must_name_the_instance(two, monkeypatch):
    host, fake, data = two
    designate(host, "roasted")
    with pytest.raises(SystemExit, match="not the instance name"):
        reset.main(["--instance", "roasted", "--yes", "salted"], host)
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit, match="no terminal to confirm"):
        reset.main(["--instance", "roasted"], host)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "roasted ")  # typed with a stray space: still the name
    monkeypatch.setattr(reset, "_start", lambda h, i: False)  # stop after the data has moved
    assert reset.main(["--instance", "roasted"], host) == 1


def test_a_marker_naming_another_instance_or_readable_by_others_is_refused(two):
    host, fake, data = two
    (data["roasted"] / reset.MARKER).write_text(json.dumps({"instance": "salted"}))
    (data["roasted"] / reset.MARKER).chmod(0o600)
    with pytest.raises(SystemExit, match="names 'salted'"):
        reset.main(["--instance", "roasted", "--yes", "roasted"], host)
    (data["roasted"] / reset.MARKER).chmod(0o644)
    with pytest.raises(SystemExit, match="not an owner-only"):
        reset.main(["--instance", "roasted", "--yes", "roasted"], host)


def test_overlapping_data_directories_are_refused(two, tmp_path):
    host, fake, data = two
    designate(host, "roasted")
    inner = data["roasted"] / "nested"
    inner.mkdir(mode=0o700)
    (host.unit_dir / "semechka-inner.service").write_text(unit(inner, 1))
    with pytest.raises(SystemExit, match="overlaps inner"):
        reset.main(["--instance", "roasted", "--yes", "roasted"], host)


def test_another_process_holding_the_data_refuses_before_stopping(two):
    host, fake, data = two
    designate(host, "roasted")
    host.holders = lambda p: {os.getpid(), 999999}
    with pytest.raises(SystemExit, match=r"processes \[999999\]"):
        reset.main(["--instance", "roasted", "--yes", "roasted"], host)
    assert not any(c[0] == "stop" for c in fake.calls)


def test_reset_moves_everything_to_a_verified_backup_and_starts_a_first_install(two, tmp_path, capsys):
    host, fake, data = two
    key = tmp_path / "engine.token"
    key.write_text("engine-secret\n")
    key.chmod(0o600)
    designate(host, "roasted", "--engine-url", "http://127.0.0.1:9", "--engine-key-file", str(key))
    assert "engine-secret" not in (data["roasted"] / reset.MARKER).read_text()  # only the file's path is kept
    salted_before = tree_digest(data["salted"])
    old_files = tree_digest(data["roasted"])
    capsys.readouterr()

    assert reset.main(["--instance", "roasted", "--yes", "roasted"], host) == 1  # 1: the hinted engine is unreachable
    out = capsys.readouterr().out
    assert "[ ok ] the first-run wizard is open" in out and "[ ok ] no accounts, conversations or sessions" in out
    assert "[ ok ] a new one-time setup token" in out and "[ ok ] a new master key" in out
    assert "[FAIL] the wizard's engine step reaches the engine: Sarness Engine (this machine) (unreachable)" in out
    assert "engine-secret" not in out

    d = data["roasted"]
    backups = list((d.parent / "data.reset-backups").iterdir())
    assert len(backups) == 1
    b = backups[0]
    assert oct(b.stat().st_mode & 0o777) == "0o700" and all(not p.stat().st_mode & 0o077 for p in b.rglob("*"))
    man = json.loads((b / "manifest.json").read_text())
    assert man["instance"] == "roasted" and man["setup"] == "complete" and man["users"] >= 1 and man["database_integrity"] == "ok"
    reset.verify_sums(b)
    assert old_files[reset.MARKER] == hashlib.sha256((b / reset.MARKER).read_bytes()).hexdigest()
    assert hashlib.sha256((b / "master.key").read_bytes()).hexdigest() == old_files["master.key"]

    # what is left is a first install made by the real program, plus the marker and the engine hint
    assert (d / reset.MARKER).exists() and (d / "setup-token").exists()
    hints = json.loads((d / "backend-hints.json").read_text())
    assert hints[0]["api_key"] == "engine-secret" and not (d / "backend-hints.json").stat().st_mode & 0o077
    assert tree_digest(data["salted"]) == salted_before
    assert "semechka-salted.service" in fake.running and not any(c[1] == "semechka-salted.service" and c[0] == "stop" for c in fake.calls)

    # repeatable: a second reset of the fresh instance makes a second backup
    assert reset.main(["--instance", "roasted", "--yes", "roasted"], host) == 1
    assert len(list((d.parent / "data.reset-backups").iterdir())) == 2


def test_restore_puts_the_configured_instance_back(two, capsys):
    host, fake, data = two
    designate(host, "roasted")
    assert reset.main(["--instance", "roasted", "--yes", "roasted"], host) == 0
    b = next((data["roasted"].parent / "data.reset-backups").iterdir())
    with pytest.raises(SystemExit, match="backup of 'roasted', not 'salted'"):
        designate(host, "salted")
        reset.main(["--instance", "salted", "--restore", str(b), "--yes", "salted"], host)
    (b / "master.key").write_bytes(os.urandom(32))
    with pytest.raises(SystemExit, match="does not match SHA256SUMS"):
        reset.main(["--instance", "roasted", "--restore", str(b), "--yes", "roasted"], host)


def test_restore_round_trip(two, capsys):
    host, fake, data = two
    designate(host, "roasted")
    key_before = (data["roasted"] / "master.key").read_bytes()
    reset.main(["--instance", "roasted", "--yes", "roasted"], host)
    b = next((data["roasted"].parent / "data.reset-backups").iterdir())
    capsys.readouterr()
    assert reset.main(["--instance", "roasted", "--restore", str(b), "--yes", "roasted"], host) == 0
    assert "setup complete" in capsys.readouterr().out
    assert (data["roasted"] / "master.key").read_bytes() == key_before
    assert not (data["roasted"] / "setup-token").exists()
    assert len(list((data["roasted"].parent / "data.reset-backups").iterdir())) == 2  # the fresh install was kept too
