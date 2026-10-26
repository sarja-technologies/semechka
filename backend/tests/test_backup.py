# ruff: noqa: F811  (pytest fixtures are imported from test_files_contexts and used as parameters)
"""Backups: streaming, authenticated per chunk, complete (database, key, gallery, attachments AND workspace files)."""
import io
import os
import tarfile

import pytest
from sqlalchemy import select

from semechka import cli
from semechka import contexts_svc as C
from semechka import files_svc as F
from semechka.app import create_app
from semechka.config import Config
from semechka.crypto import SealReader, SealWriter, seal_with_passphrase
from semechka.models_files import FileVersion

from .test_files_contexts import World, make_ctx, s, st, w  # noqa: F401  (fixtures and helpers)

PW = "a long backup passphrase"


def a(**kw):
    return type("A", (), kw)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", PW)
    monkeypatch.setattr(cli, "_port_free", lambda *x: True)


def seal(payload: bytes, chunk=4096, pw=PW) -> bytes:
    out = io.BytesIO()
    wr = SealWriter(out, pw, chunk=chunk)
    for i in range(0, len(payload), 1000):  # written in awkward pieces on purpose
        wr.write(payload[i:i + 1000])
    wr.close()
    return out.getvalue()


def unseal(blob: bytes, pw=PW) -> bytes:
    r = SealReader(io.BytesIO(blob), pw)
    out = r.read()
    r.verify_end()
    return out


# ---- the sealed stream ----------------------------------------------------------------------------------------
@pytest.mark.parametrize("size", [0, 1, 4095, 4096, 4097, 8192, 40_000])  # empty, below, at and across chunk boundaries
def test_stream_round_trips_at_every_boundary(size):
    data = os.urandom(size)
    assert unseal(seal(data)) == data


def test_stream_detects_wrong_passphrase_damage_truncation_extension_and_reordering():
    data = os.urandom(20_000)
    blob = seal(data)
    head, step = 5 + 16 + 8 + 4, 4096 + 16
    with pytest.raises(ValueError):
        unseal(blob, "another passphrase")
    flipped = bytearray(blob)
    flipped[head + step + 10] ^= 1
    with pytest.raises(ValueError):
        unseal(bytes(flipped))
    with pytest.raises(ValueError):
        unseal(blob[:head + 2 * step])  # cut cleanly at a chunk boundary: the "final" mark is missing
    with pytest.raises(ValueError):
        unseal(blob[:-3])
    with pytest.raises(ValueError):
        unseal(blob + os.urandom(step))  # something appended
    c = [blob[head + i * step: head + (i + 1) * step] for i in range(len(blob[head:]) // step + 1)]
    swapped = blob[:head] + c[1] + c[0] + b"".join(c[2:])
    with pytest.raises(ValueError):
        unseal(swapped)  # chunks cannot be reordered
    with pytest.raises(ValueError):
        unseal(b"NOTSMCK" + blob[7:])


def test_plaintext_never_appears_in_the_sealed_stream():
    marker = b"TOP-SECRET-MARKER-" * 40
    assert marker[:18] not in seal(marker + os.urandom(5000))


# ---- the commands -----------------------------------------------------------------------------------------------
def test_backup_and_restore_carry_workspace_files_versions_and_search(w, st, tmp_path):
    f = w.upload("alice", "plan.md", b"launch of the aurora product happens in spring")
    c = make_ctx(w, [f])
    w.ingest()
    F.new_version(st, w.s, w.az("alice"), f.id, b"launch of the aurora product moved to summer")
    w.ingest()
    w.s.commit()
    out = tmp_path / "b.smck"
    cli.cmd_backup(a(output=str(out)), st.cfg)
    assert oct(out.stat().st_mode & 0o777) == "0o600" and not list(tmp_path.glob("*.part"))
    blob = out.read_bytes()
    assert b"aurora" not in blob and blob.startswith(b"SMCK2")
    with pytest.raises(SystemExit):
        cli.cmd_backup(a(output=str(out)), st.cfg)  # never overwrites an existing backup

    fresh = Config(data_dir=tmp_path / "restored")
    cli.cmd_restore(a(file=str(out), force=False), fresh)
    assert (fresh.data_dir / "workspaces").is_dir()
    fresh.background = False
    app2 = create_app(fresh)
    st2 = app2.state.sem
    with st2.db.session() as s2:
        from semechka.authz import Authz
        az = Authz(s2, w.u["alice"].id)
        _, v, data = F.read_file(st2, s2, az, f.id)
        assert data.startswith(b"launch of the aurora product moved") and v.n == 2
        _, _, old = F.read_file(st2, s2, az, f.id, version=1)
        assert old.endswith(b"in spring")  # history survived
        r = C.retrieve(st2, s2, az, "aurora summer", context_ids=[c.id])
        assert r.hits and r.hits[0].file_name == "plan.md"  # the index (chunks) survived too
        assert s2.execute(select(FileVersion)).scalars().all()


def test_a_wrong_passphrase_or_damaged_file_changes_nothing(w, st, tmp_path, monkeypatch):
    w.upload("alice", "x.md", b"hello")
    w.s.commit()
    out = tmp_path / "b.smck"
    cli.cmd_backup(a(output=str(out)), st.cfg)
    fresh = Config(data_dir=tmp_path / "r1")
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "not the right passphrase")
    with pytest.raises(SystemExit) as e:
        cli.cmd_restore(a(file=str(out), force=False), fresh)
    assert "Nothing was changed" in str(e.value) and not (fresh.data_dir / "semechka.db").exists()
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", PW)
    blob = bytearray(out.read_bytes())
    blob[len(blob) // 2] ^= 0xFF
    bad = tmp_path / "bad.smck"
    bad.write_bytes(bytes(blob))
    fresh2 = Config(data_dir=tmp_path / "r2")
    with pytest.raises(SystemExit):
        cli.cmd_restore(a(file=str(bad), force=False), fresh2)
    trunc = tmp_path / "trunc.smck"
    trunc.write_bytes(out.read_bytes()[:-1000])
    with pytest.raises(SystemExit):
        cli.cmd_restore(a(file=str(trunc), force=False), Config(data_dir=tmp_path / "r3"))
    assert not (fresh2.data_dir / "semechka.db").exists() and not (tmp_path / "r3" / "semechka.db").exists()


def test_forced_restore_keeps_what_it_replaces(w, st, tmp_path):
    f = w.upload("alice", "keep.md", b"original contents")
    w.s.commit()
    out = tmp_path / "b.smck"
    cli.cmd_backup(a(output=str(out)), st.cfg)
    F.new_version(st, w.s, w.az("alice"), f.id, b"changed after the backup")
    w.s.commit()
    with pytest.raises(SystemExit):
        cli.cmd_restore(a(file=str(out), force=False), st.cfg)  # refuses to overwrite silently
    cli.cmd_restore(a(file=str(out), force=True), st.cfg)
    kept = [p.name for p in st.cfg.data_dir.iterdir() if "pre-restore" in p.name]
    assert any(n.startswith("workspaces.pre-restore") for n in kept) and any(n.startswith("semechka.db.pre-restore") for n in kept)
    with st.db.session() as s2:
        assert s2.execute(select(FileVersion).where(FileVersion.file_id == f.id)).scalars().all()[0].n == 1


def test_older_v1_backups_still_restore(w, st, tmp_path):
    """A backup written by 0.4.x (single sealed blob, database + key + gallery only) must remain restorable."""
    import sqlite3
    w.s.commit()
    buf = io.BytesIO()
    copy = tmp_path / "copy.db"
    src, dst = sqlite3.connect(st.cfg.data_dir / "semechka.db"), sqlite3.connect(copy)
    src.backup(dst)
    dst.close()
    src.close()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        tf.add(copy, arcname="semechka.db")
        tf.add(st.cfg.key_path, arcname="master.key")
    old = tmp_path / "old.smck"
    old.write_bytes(seal_with_passphrase(buf.getvalue(), PW))
    fresh = Config(data_dir=tmp_path / "from-v1")
    cli.cmd_restore(a(file=str(old), force=False), fresh)
    assert (fresh.data_dir / "semechka.db").exists() and (fresh.data_dir / "master.key").read_bytes() == st.cfg.key_path.read_bytes()


def test_a_backup_with_unsafe_paths_is_refused(tmp_path):
    out = io.BytesIO()
    wr = SealWriter(out, PW)
    with tarfile.open(fileobj=wr, mode="w|gz") as tf:
        meta = b"{}"
        ti = tarfile.TarInfo("../escape.txt")
        ti.size = len(meta)
        tf.addfile(ti, io.BytesIO(meta))
    wr.close()
    bad = tmp_path / "evil.smck"
    bad.write_bytes(out.getvalue())
    with pytest.raises(SystemExit) as e:
        cli.cmd_restore(a(file=str(bad), force=False), Config(data_dir=tmp_path / "t"))
    assert "unsafe" in str(e.value).lower() or "could not open" in str(e.value).lower()
    assert not (tmp_path / "escape.txt").exists()


def test_a_failed_backup_leaves_no_file_that_looks_complete(w, st, tmp_path, monkeypatch):
    def boom(*x, **k):
        raise OSError("disk full")
    monkeypatch.setattr(cli, "_add_tree", boom)
    w.upload("alice", "x.md", b"hello")
    w.s.commit()
    out = tmp_path / "b.smck"
    with pytest.raises(OSError):
        cli.cmd_backup(a(output=str(out)), st.cfg)
    assert not out.exists() and not list(tmp_path.glob("*.part"))


def test_files_deleted_while_backing_up_do_not_fail_the_backup(w, st, tmp_path, monkeypatch):
    f = w.upload("alice", "gone.md", b"temporary")
    w.s.commit()
    real = cli.tarfile.TarFile.add

    def vanishing(self, name, *args, **kw):
        if str(name).endswith(".sm"):
            raise FileNotFoundError(name)  # removed by retention between listing and reading
        return real(self, name, *args, **kw)
    monkeypatch.setattr(cli.tarfile.TarFile, "add", vanishing)
    out = tmp_path / "b.smck"
    cli.cmd_backup(a(output=str(out)), st.cfg)
    monkeypatch.undo()
    fresh = Config(data_dir=tmp_path / "restored")
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", PW)
    monkeypatch.setattr(cli, "_port_free", lambda *x: True)
    cli.cmd_restore(a(file=str(out), force=False), fresh)  # a complete, openable backup; the vanished blob is simply absent
    assert (fresh.data_dir / "semechka.db").exists() and not list((fresh.data_dir / "workspaces").rglob(f"{f.id}*"))
