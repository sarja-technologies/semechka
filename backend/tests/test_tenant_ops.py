
# ruff: noqa: F811  (pytest fixtures are imported from test_files_contexts and used as parameters)
"""Operator tools for one organisation's data: sealed export and permanent purge, with the other tenant untouched."""
import json
import tarfile

import pytest
from sqlalchemy import select

from semechka import cli
from semechka import contexts_svc as C
from semechka import files_svc as F
from semechka.crypto import SealReader
from semechka.models import AuditEvent
from semechka.models_files import Chunk, DocText, File, FileVersion, Job
from semechka.models_tenancy import Org, OrgMember, Workspace

from .test_files_contexts import World, make_ctx, s, st, w  # noqa: F401  (fixtures and helpers)

PW = "a long export passphrase"


def a(**kw):
    base = {"action": "list", "slug": "", "output": None, "no_history": False, "confirm": "", "force": False}
    return type("A", (), {**base, **kw})


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", PW)


def two_companies(w):
    f = w.upload("alice", "acme-plan.md", b"ACME-ONLY: merger with initech in march")
    F.new_version(w.st, w.s, w.az("alice"), f.id, b"ACME-ONLY: merger with initech moved to april")
    g = w.upload("gina", "globex-notes.md", b"GLOBEX-ONLY: secret formula alpha", ws=w.gws)
    c = make_ctx(w, [f])
    w.ingest()
    w.s.commit()
    return f, g, c


def read_export(path):
    with open(path, "rb") as fh:
        r = SealReader(fh, PW)
        files = {}
        with tarfile.open(fileobj=r, mode="r|gz") as tf:
            for m in tf:
                files[m.name] = tf.extractfile(m).read()
        r.verify_end()
    return files


def test_export_contains_one_organisation_decrypted_and_nothing_of_another(w, st, tmp_path, s):
    _f, _g, _c = two_companies(w)
    out = tmp_path / "acme.smck"
    cli.cmd_org(a(action="export", slug="acme", output=str(out)), st.cfg)
    assert oct(out.stat().st_mode & 0o777) == "0o600" and b"ACME-ONLY" not in out.read_bytes()  # sealed on disk
    files = read_export(out)
    man = json.loads(files["manifest.json"])
    assert man["organisation"]["slug"] == "acme" and man["counts"]["files"] == 2  # current version + history
    blob = b"".join(files.values())
    assert b"moved to april" in blob and b"in march" in blob  # history is included by default
    assert b"globex" not in blob.lower() and b"secret formula" not in blob  # nothing of the other tenant, not even its name
    ws = man["workspaces"][0]
    assert ws["name"] == "Finance" and [m["email"] for m in man["members"] if "org_owner" in m["roles"]] == ["owner@example.test"]
    assert ws["contexts"][0]["files"] == ["acme-plan.md"] and ws["files"][0]["versions"][0]["sha256"]
    lite = tmp_path / "lite.smck"
    cli.cmd_org(a(action="export", slug="acme", output=str(lite), no_history=True), st.cfg)
    assert b"in march" not in b"".join(read_export(lite).values())


def test_export_needs_a_strong_passphrase_a_real_organisation_and_never_overwrites(w, st, tmp_path, monkeypatch, s):
    two_companies(w)
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", "short")
    with pytest.raises(SystemExit):
        cli.cmd_org(a(action="export", slug="acme", output=str(tmp_path / "x.smck")), st.cfg)
    monkeypatch.setenv("SEMECHKA_BACKUP_PASSPHRASE", PW)
    with pytest.raises(SystemExit) as e:
        cli.cmd_org(a(action="export", slug="no-such-company", output=str(tmp_path / "y.smck")), st.cfg)
    assert "No organisation" in str(e.value)
    out = tmp_path / "ok.smck"
    cli.cmd_org(a(action="export", slug="acme", output=str(out)), st.cfg)
    with pytest.raises(FileExistsError):
        cli.cmd_org(a(action="export", slug="acme", output=str(out)), st.cfg)


def test_purge_removes_one_tenant_completely_and_leaves_the_other_working(w, st, tmp_path, capsys, s):
    f, g, c = two_companies(w)
    gid, fid, cid = g.id, f.id, c.id
    globex_blobs = st.cfg.data_dir / "workspaces" / w.globex.id
    acme_blobs = st.cfg.data_dir / "workspaces" / w.acme.id
    assert globex_blobs.exists() and acme_blobs.exists()
    with pytest.raises(SystemExit) as e:
        cli.cmd_org(a(action="purge", slug="globex"), st.cfg)  # no confirmation
    assert "--confirm globex" in str(e.value) and globex_blobs.exists()
    with pytest.raises(SystemExit):
        cli.cmd_org(a(action="purge", slug="globex", confirm="acme"), st.cfg)  # confirming the wrong name
    cli.cmd_org(a(action="purge", slug="globex", confirm="globex"), st.cfg)
    assert "Purged 'globex'" in capsys.readouterr().out
    assert not globex_blobs.exists() and acme_blobs.exists()
    with st.db.session() as s2:
        assert s2.execute(select(Org).where(Org.slug == "globex")).scalar_one_or_none() is None
        for model in (File, FileVersion, Chunk, DocText, Job, Workspace, OrgMember):
            assert s2.execute(select(model).where(model.org_id == w.globex.id)).first() is None, model
        assert s2.get(File, gid) is None
        ev = [x.action for x in s2.execute(select(AuditEvent)).scalars()]
        assert "org.purge" in ev  # the fact is kept; no content is
        # Acme is untouched and still fully functional, including its search index
        from semechka.authz import Authz
        az = Authz(s2, w.u["alice"].id)
        assert F.read_file(st, s2, az, fid)[2].startswith(b"ACME-ONLY")
        assert C.retrieve(st, s2, az, "merger initech", context_ids=[cid]).hits
        assert s2.get(type(w.u["gina"]), w.u["gina"].id) is not None  # people are accounts of the server, not of the tenant


def test_the_default_organisation_needs_force(st, s, tmp_path):
    from semechka import tenancy_svc as TS
    o = TS.create_org(s, "", "Everyone", "everyone", is_default=True)
    s.commit()
    with pytest.raises(SystemExit) as e:
        cli.cmd_org(a(action="purge", slug="everyone", confirm="everyone"), st.cfg)
    assert "--force" in str(e.value)
    assert o.id
