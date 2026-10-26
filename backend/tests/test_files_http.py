"""Files, contexts and retrieval over the real HTTP API (Scenario C without containers, plus cross-tenant attacks)."""
import io

from semechka import jobs_svc
from semechka.app import create_app

from .conftest import make_user
from .test_files_contexts import docx_bytes, xlsx_bytes
from .test_orgs_http import login, org_with_owner


def up(c, wid, name, data, folder_id=None, ctype="application/octet-stream"):
    return c.post(f"/api/workspaces/{wid}/files", files={"file": (name, io.BytesIO(data), ctype)},
                  data={"folder_id": folder_id} if folder_id else {})


def company(owner, app, name, boss_email, staff):
    """An organisation with a boss and staff {email: org_role}; returns (org_id, {email: client})."""
    oid, boss = org_with_owner(owner, app, name, boss_email)
    clients = {boss_email: boss}
    for email, role in staff.items():
        make_user(owner, app, email)
        assert boss.post(f"/api/orgs/{oid}/members", json={"email": email, "roles": [role]}).status_code == 200
        clients[email] = login(app, email)
    return oid, clients


def test_persistent_knowledge_end_to_end_and_across_a_restart(owner, app, st, cfg):
    acme, c = company(owner, app, "Acme", "boss@acme.test", {"fin@acme.test": "dept_admin", "viewer@acme.test": "member"})
    fin, viewer = c["fin@acme.test"], c["viewer@acme.test"]
    wid = fin.post(f"/api/orgs/{acme}/workspaces", json={"name": "Finance"}).json()["id"]
    uid = {m["email"]: m["user_id"] for m in c["boss@acme.test"].get(f"/api/orgs/{acme}/members").json()}
    assert fin.put(f"/api/workspaces/{wid}/members", json={"principal_type": "user", "principal_id": uid["viewer@acme.test"], "role": "viewer"}).status_code == 200

    docs = {"policy.docx": docx_bytes([("h", "Expense Policy"), ("p", "Taxi rides over fifty dollars need director approval.")]),
            "q3.xlsx": xlsx_bytes({"Budget": [["dept", "spend"], ["marketing", 120000], ["finance", 80000]]}),
            "notes.md": b"# Audit\nThe external audit starts in november.\n"}
    ids = {}
    for name, data in docs.items():
        r = up(fin, wid, name, data)
        assert r.status_code == 200, r.text
        ids[name] = r.json()["id"]
    jobs = fin.get(f"/api/workspaces/{wid}/jobs").json()
    assert len(jobs) == 3 and {j["status"] for j in jobs} == {"queued"}  # truthful: nothing has run yet
    assert viewer.get(f"/api/workspaces/{wid}/jobs").json() == []  # a viewer sees none of someone else's jobs
    jobs_svc.run_pending(st)
    assert {j["status"] for j in fin.get(f"/api/workspaces/{wid}/jobs").json()} == {"succeeded"}
    info = fin.get(f"/api/files/{ids['q3.xlsx']}").json()
    assert info["text"]["status"] == "ok" and info["text"]["extractor"] == "xlsx"

    ctx_id = fin.post(f"/api/workspaces/{wid}/contexts", json={"name": "Finance knowledge", "description": "policies and budgets"}).json()["id"]
    for fid in ids.values():
        assert fin.put(f"/api/contexts/{ctx_id}/items/{fid}").status_code == 200
    r = viewer.post(f"/api/workspaces/{wid}/retrieve", json={"query": "taxi approval director"}).json()
    assert r["mode"] == "keyword" and r["passages"][0]["file"] == "policy.docx" and r["passages"][0]["loc"] == "Expense Policy"
    r = viewer.post(f"/api/workspaces/{wid}/retrieve", json={"query": "marketing spend", "context_ids": [ctx_id]}).json()
    assert r["passages"][0]["file"] == "q3.xlsx" and "sheet Budget" in r["passages"][0]["loc"]
    assert "120000" in r["passages"][0]["text"]
    s = viewer.get(f"/api/workspaces/{wid}/search", params={"q": "audit"}).json()
    assert [f["name"] for f in s["files"]] == [] and s["passages"][0]["file"] == "notes.md"

    # restart: a brand new application object on the same data directory
    app2 = create_app(cfg)
    v2 = login(app2, "viewer@acme.test")
    assert v2.get(f"/api/files/{ids['notes.md']}/content", params={"download": 1}).content.startswith(b"# Audit")
    r = v2.post(f"/api/workspaces/{wid}/retrieve", json={"query": "external audit november"}).json()
    assert r["passages"] and r["passages"][0]["file"] == "notes.md"
    assert v2.get(f"/api/contexts/{ctx_id}").json()["name"] == "Finance knowledge"


def test_downloads_are_attachments_with_safe_headers(owner, app, st):
    acme, c = company(owner, app, "Acme", "boss@acme.test", {"fin@acme.test": "dept_admin"})
    fin = c["fin@acme.test"]
    wid = fin.post(f"/api/orgs/{acme}/workspaces", json={"name": "Finance"}).json()["id"]
    svg = up(fin, wid, 'evil"name.svg', b"<svg onload=alert(1)></svg>").json()
    html = up(fin, wid, "page.html", b"<script>alert(1)</script>").json()
    png = up(fin, wid, "pic.png", b"\x89PNG\r\n\x1a\n" + b"0" * 50).json()
    for f in (svg, html):
        r = fin.get(f"/api/files/{f['id']}/content")
        assert r.headers["content-disposition"].startswith("attachment") and r.headers["content-type"].startswith("application/octet-stream")
        assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]
        assert r.headers["content-disposition"].count('"') == 2  # a quote in the name cannot break out of the header
    r = fin.get(f"/api/files/{png['id']}/content")
    assert r.headers["content-disposition"].startswith("inline") and r.headers["content-type"] == "image/png"
    assert fin.get(f"/api/files/{png['id']}/content", params={"download": 1}).headers["content-disposition"].startswith("attachment")
    assert fin.get(f"/api/files/{png['id']}/content", headers={"range": "bytes=0-3"}).status_code == 206


def test_two_companies_cannot_reach_each_others_files_contexts_or_jobs(owner, app, st):
    acme, a = company(owner, app, "Acme", "boss@acme.test", {"fin@acme.test": "dept_admin"})
    globex, g = company(owner, app, "Globex", "boss@globex.test", {"ops@globex.test": "dept_admin"})
    fin, ops = a["fin@acme.test"], g["ops@globex.test"]
    wid = fin.post(f"/api/orgs/{acme}/workspaces", json={"name": "Finance"}).json()["id"]
    gwid = ops.post(f"/api/orgs/{globex}/workspaces", json={"name": "Ops"}).json()["id"]
    fid = up(fin, wid, "secret.txt", b"acme private numbers").json()["id"]
    cid = fin.post(f"/api/workspaces/{wid}/contexts", json={"name": "Acme ctx"}).json()["id"]
    fin.put(f"/api/contexts/{cid}/items/{fid}")
    jobs_svc.run_pending(st)
    jid = fin.get(f"/api/workspaces/{wid}/jobs").json()[0]["id"]
    folder = fin.post(f"/api/workspaces/{wid}/folders", json={"name": "F"}).json()["id"]

    probes = [("get", f"/api/files/{fid}"), ("get", f"/api/files/{fid}/content"), ("delete", f"/api/files/{fid}"),
              ("post", f"/api/files/{fid}/untrash"), ("get", f"/api/contexts/{cid}"), ("get", f"/api/contexts/{cid}/export"),
              ("get", f"/api/contexts/{cid}/versions"), ("delete", f"/api/contexts/{cid}"), ("get", f"/api/jobs/{jid}"),
              ("get", f"/api/workspaces/{wid}/files"), ("get", f"/api/workspaces/{wid}/jobs"), ("get", f"/api/workspaces/{wid}/contexts"),
              ("get", f"/api/workspaces/{wid}/search?q=private")]
    for method, url in probes:
        assert getattr(ops, method)(url).status_code == 404, (method, url)
    assert ops.post(f"/api/workspaces/{wid}/retrieve", json={"query": "private"}).status_code == 404
    assert ops.post(f"/api/jobs/{jid}/cancel").status_code == 404
    assert ops.post(f"/api/workspaces/{wid}/folders", json={"name": "x"}).status_code == 404
    assert ops.patch(f"/api/folders/{folder}", json={"name": "x"}).status_code == 404
    assert up(ops, wid, "inject.txt", b"hello").status_code == 404
    assert ops.put(f"/api/contexts/{cid}/items/{fid}").status_code == 404
    # Globex cannot pull an Acme file into its own context, nor attach an Acme context to its workspace
    gctx = ops.post(f"/api/workspaces/{gwid}/contexts", json={"name": "G"}).json()["id"]
    assert ops.put(f"/api/contexts/{gctx}/items/{fid}").status_code == 404
    assert ops.post(f"/api/contexts/{cid}/attach", json={"workspace_id": gwid}).status_code == 404
    assert ops.post(f"/api/contexts/{gctx}/attach", json={"workspace_id": wid}).status_code == 404
    # and retrieval inside Globex never surfaces Acme text, even when asked for Acme's context id
    r = ops.post(f"/api/workspaces/{gwid}/retrieve", json={"query": "private numbers", "context_ids": [cid]}).json()
    assert r["passages"] == [] and r["searched_files"] == 0


def test_limits_and_validation_over_http(owner, app, st):
    acme, c = company(owner, app, "Acme", "boss@acme.test", {"fin@acme.test": "dept_admin"})
    boss, fin = c["boss@acme.test"], c["fin@acme.test"]
    wid = fin.post(f"/api/orgs/{acme}/workspaces", json={"name": "Finance"}).json()["id"]
    assert fin.patch(f"/api/workspaces/{wid}", json={"quotas": {"max_upload_mb": 1}}).status_code == 200
    assert up(fin, wid, "big.txt", b"x" * 1_100_000).status_code == 413
    assert up(fin, wid, "run.exe", b"MZ" + b"\0" * 8 + b"PE\0\0").status_code == 422
    r = up(fin, wid, "a.txt", b"hello")
    assert r.status_code == 200
    assert up(fin, wid, "a.txt", b"again").status_code == 409  # same name: use a new version
    fid = r.json()["id"]
    assert fin.put(f"/api/files/{fid}/text", json={"text": "hello v2", "note": "edit"}).json()["n"] == 2
    assert [v["n"] for v in fin.get(f"/api/files/{fid}").json()["versions"]] == [2, 1]
    assert fin.post(f"/api/files/{fid}/restore", json={"version": 1}).json()["n"] == 3
    assert fin.get(f"/api/files/{fid}/content", params={"download": 1}).content == b"hello"
    assert boss.get(f"/api/files/{fid}").status_code == 404  # the org owner still reads nothing
    assert boss.post(f"/api/orgs/{acme}/reindex").status_code == 200
    z = fin.get("/api/workspaces/" + wid + "/files").json()
    assert [f["name"] for f in z["files"]] == ["a.txt"]


def test_jobs_left_running_by_a_dead_process_resume_at_start_up(owner, app, st, cfg):
    """A restart must not make people wait out a lease for work the dead process was doing; repeated crashers fail."""
    from sqlalchemy import select

    from semechka import jobs_svc
    from semechka.models_files import Job
    acme, c = company(owner, app, "Acme", "boss@acme.test", {"fin@acme.test": "dept_admin"})
    fin = c["fin@acme.test"]
    wid = fin.post(f"/api/orgs/{acme}/workspaces", json={"name": "Finance"}).json()["id"]
    up(fin, wid, "a.txt", b"hello")
    up(fin, wid, "b.txt", b"world")
    with st.db.session() as s:
        j1, j2 = s.execute(select(Job)).scalars().all()[:2]
        j1.status, j1.attempts, j1.lease_until = "running", 1, 9_999_999_999  # a lease that would not expire for centuries
        j2.status, j2.attempts = "running", jobs_svc.MAX_ATTEMPTS
        ids = (j1.id, j2.id)
    assert jobs_svc.recover_on_start(st) == 2
    with st.db.session() as s:
        assert s.get(Job, ids[0]).status == "queued" and s.get(Job, ids[0]).lease_until == 0
        assert s.get(Job, ids[1]).status == "failed" and "repeatedly" in s.get(Job, ids[1]).error
    jobs_svc.run_pending(st)
    assert {j["status"] for j in fin.get(f"/api/workspaces/{wid}/jobs").json()} == {"succeeded", "failed"}


def test_concurrent_writers_queue_for_the_database_instead_of_failing(tmp_path):
    """Seen live under load: login reads a user, then updates it; another writer committed in between, and SQLite's WAL mode
    answers 'database is locked' at once, ignoring the busy timeout. Writers that begin with the write lock wait their turn."""
    import threading

    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    from semechka.db import Database
    db = Database(f"sqlite:///{tmp_path}/t.db")
    with db.session() as s:
        s.execute(text("create table t (k text primary key, v integer)"))
        s.execute(text("insert into t values ('a', 0)"))

    def interleave(write):
        a = db.session(write=write)
        with a as sa:
            sa.execute(text("select v from t where k='a'")).scalar()  # A has read
            done = threading.Event()

            def other():
                with db.session(write=True) as sb:  # B writes and commits while A is still open
                    sb.execute(text("update t set v = v + 1 where k='a'"))
                done.set()
            th = threading.Thread(target=other)
            th.start()
            if not write:
                done.wait(5)  # for a deferred A, B gets in and commits first
            sa.execute(text("update t set v = v + 10 where k='a'"))
        th.join(10)

    with pytest.raises(OperationalError, match="locked"):
        interleave(False)  # the failure this guards against
    interleave(True)  # A took the write lock first; B waited, then both applied
    with db.session() as s:
        assert s.execute(text("select v from t where k='a'")).scalar() == 1 + 10 + 1  # first run added 1 + (A failed), second 10 + 1


def test_a_burst_of_logins_and_uploads_never_answers_500(owner, app, st, cfg):
    """The same failure through the real HTTP stack: many people signing in and uploading at once."""
    import concurrent.futures as cf

    from .conftest import Api, make_user
    acme, c = company(owner, app, "Burst", "boss@burst.test", {})
    boss = c["boss@burst.test"]
    people = []
    for i in range(8):
        email = f"p{i}@burst.test"
        make_user(owner, app, email)
        assert boss.post(f"/api/orgs/{acme}/members", json={"email": email, "roles": ["dept_admin"]}).status_code == 200
        people.append(email)

    def work(email):
        cl = Api(app)
        codes = []
        for _ in range(3):
            codes.append(cl.login(email).status_code)
        cl.csrf = cl.get("/api/me").json()["csrf"]
        wid = cl.post(f"/api/orgs/{acme}/workspaces", json={"name": f"ws-{email}"}).json()["id"]
        for n in range(3):
            codes.append(up(cl, wid, f"f{n}.txt", b"hello burst " * 50).status_code)
        return codes
    with cf.ThreadPoolExecutor(8) as ex:
        results = list(ex.map(work, people))
    flat = [c for r in results for c in r]
    assert set(flat) == {200}, sorted(set(flat))
