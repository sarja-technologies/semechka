"""Knowledge collections, assistants, code workspace: boundaries, encryption at rest, and real chat integration."""
import sqlite3

import pytest
from sqlalchemy import func, select

from semechka.models import Message
from semechka.models_knowledge import MessageSources

from . import fakebackend
from .conftest import make_user

SECRET_WORD = "zebraquartzunique"
DOC = (f"The maintenance window for the billing cluster is every second Tuesday at 02:00 UTC.\n\n"
       f"Contact the platform team about {SECRET_WORD} before changing it.\n\n"
       "Unrelated paragraph about lunch menus and parking permits.")


def enable(owner, *modes):
    # setup grants the model to owner+member only; team managers need an explicit grant to pass the mode gate
    mid = owner.get("/api/chat/models").json()[0]["id"]
    owner.put(f"/api/admin/models/{mid}/grants", json={"grants": [{"subject_type": "role", "subject_id": r, "effect": "allow"}
                                                                  for r in ("owner", "member", "team_manager")]})
    for m in modes:
        assert owner.put(f"/api/admin/settings/modes.{m}.enabled", json={"value": True}).status_code == 200


def make_pdf(text: str) -> bytes:
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode() if text else b""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    x = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, x)
    return out


def upload(api, cid, name, data, ctype="text/plain"):
    return api.post(f"/api/knowledge/collections/{cid}/documents", files={"file": (name, data, ctype)})


def new_collection(api, name="Runbooks", **kw):
    r = api.post("/api/knowledge/collections", json={"name": name, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def model_id(api):
    return api.get("/api/chat/models").json()[0]["id"]


def test_modes_are_off_until_the_administrator_turns_them_on(owner):
    r = owner.get("/api/knowledge/collections")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "disabled"
    assert owner.get("/api/assistants").status_code == 403
    enable(owner, "knowledge", "agents")
    assert owner.get("/api/knowledge/collections").status_code == 200
    assert owner.get("/api/assistants").status_code == 200


def test_upload_search_and_keyword_ranking(owner):
    enable(owner, "knowledge")
    cid = new_collection(owner)
    d = upload(owner, cid, "ops.md", DOC.encode()).json()
    assert d["passages"] >= 1 and d["kind"] == "text"
    r = owner.post(f"/api/knowledge/collections/{cid}/search", json={"query": "when is the billing maintenance window?"}).json()
    assert r["method"] == "keyword (BM25)"
    assert r["results"] and "second Tuesday" in r["results"][0]["text"] and r["results"][0]["file"] == "ops.md"
    assert owner.post(f"/api/knowledge/collections/{cid}/search", json={"query": "kubernetes helm"}).json()["results"] == []
    # operators and punctuation in the query can not break or inject into the FTS expression
    assert owner.post(f"/api/knowledge/collections/{cid}/search", json={"query": 'NEAR("a" b) OR * " AND'}).status_code == 200
    info = owner.get("/api/knowledge/info").json()
    assert info["search"] == "keyword" and "does not understand meaning" in info["search_note"]


def test_text_and_chunks_are_encrypted_at_rest(owner, cfg):
    enable(owner, "knowledge")
    cid = new_collection(owner, "Secret plans", description="describe-" + SECRET_WORD)
    upload(owner, cid, f"{SECRET_WORD}.txt", DOC.encode())
    owner.post(f"/api/knowledge/collections/{cid}/search", json={"query": SECRET_WORD})  # builds the in-memory index
    for f in cfg.data_dir.glob("semechka.db*"):
        assert SECRET_WORD.encode() not in f.read_bytes(), f"plaintext found in {f.name}"
    con = sqlite3.connect(cfg.data_dir / "semechka.db")
    assert con.execute("select count(*) from kn_chunks").fetchone()[0] >= 1
    assert "fts" not in " ".join(r[0] for r in con.execute("select name from sqlite_master")).lower()
    con.close()


def test_pdf_with_text_layer_is_searchable_and_scans_are_refused_honestly(owner):
    enable(owner, "knowledge")
    cid = new_collection(owner)
    assert upload(owner, cid, "policy.pdf", make_pdf("Refunds are issued within fourteen days"), "application/pdf").status_code == 200
    hit = owner.post(f"/api/knowledge/collections/{cid}/search", json={"query": "refunds fourteen"}).json()["results"]
    assert hit and "fourteen days" in hit[0]["text"]
    r = upload(owner, cid, "scan.pdf", make_pdf(""), "application/pdf")
    assert r.status_code == 422 and "no text layer" in r.json()["detail"]["message"] and "OCR" in r.json()["detail"]["message"]
    assert upload(owner, cid, "broken.pdf", b"%PDF-1.4 garbage").status_code == 422


def test_unsupported_oversized_and_limit_cases(owner):
    enable(owner, "knowledge")
    cid = new_collection(owner)
    assert upload(owner, cid, "tool.exe", b"MZ\x00\x00").status_code == 422
    assert upload(owner, cid, "data.txt", b"abc\x00def").status_code == 422
    r = upload(owner, cid, "latin.txt", "caf\xe9".encode("latin-1"))
    assert r.status_code == 422 and "UTF-8" in r.json()["detail"]["message"]
    assert upload(owner, cid, "empty.txt", b"  \n").status_code == 422
    owner.put("/api/admin/settings/knowledge.max_doc_kb", json={"value": 16})
    r = upload(owner, cid, "big.txt", b"x " * 20000)
    assert r.status_code == 422 and "limit" in r.json()["detail"]["message"]
    owner.put("/api/admin/settings/knowledge.max_docs_per_collection", json={"value": 1})
    assert upload(owner, cid, "one.txt", b"first doc").status_code == 200
    assert upload(owner, cid, "two.txt", b"second doc").status_code == 409
    owner.put("/api/admin/settings/knowledge.max_collections", json={"value": 2})
    new_collection(owner, "second")
    assert owner.post("/api/knowledge/collections", json={"name": "third"}).status_code == 409


def test_personal_collections_are_private_and_deletion_removes_everything(owner, app, cfg):
    enable(owner, "knowledge")
    alice = make_user(owner, app, "alice@example.test")
    bob = make_user(owner, app, "bob@example.test")
    cid = new_collection(alice)
    did = upload(alice, cid, "a.txt", DOC.encode()).json()["id"]
    for call in (lambda: bob.get(f"/api/knowledge/collections/{cid}/documents"),
                 lambda: bob.post(f"/api/knowledge/collections/{cid}/search", json={"query": "billing"}),
                 lambda: bob.delete(f"/api/knowledge/collections/{cid}"),
                 lambda: upload(bob, cid, "x.txt", b"hi"),
                 lambda: bob.delete(f"/api/knowledge/collections/{cid}/documents/{did}")):
        assert call().status_code == 404  # same answer as a collection that does not exist
    assert bob.get("/api/knowledge/collections").json() == []
    assert owner.get("/api/knowledge/collections").json() == []  # administrators do not see other people's collections
    assert alice.delete(f"/api/knowledge/collections/{cid}/documents/{did}").status_code == 200
    assert alice.delete(f"/api/knowledge/collections/{cid}").status_code == 200
    con = sqlite3.connect(cfg.data_dir / "semechka.db")
    assert con.execute("select count(*) from kn_chunks").fetchone()[0] == 0
    assert con.execute("select count(*) from kn_documents").fetchone()[0] == 0
    con.close()


def test_team_collections_follow_membership_and_write_rights(owner, app):
    enable(owner, "knowledge")
    t1 = owner.post("/api/admin/teams", json={"name": "Finance"}).json()["id"]
    t2 = owner.post("/api/admin/teams", json={"name": "Legal"}).json()["id"]
    creator = make_user(owner, app, "creator@example.test", teams=[t1])
    peer = make_user(owner, app, "peer@example.test", teams=[t1])
    manager = make_user(owner, app, "mgr@example.test", role="team_manager", teams=[t1])
    outsider = make_user(owner, app, "out@example.test", teams=[t2])
    assert outsider.post("/api/knowledge/collections", json={"name": "x", "team_id": t1}).status_code == 403
    cid = new_collection(creator, "Finance docs", team_id=t1)
    upload(creator, cid, "f.txt", DOC.encode())
    assert [c["name"] for c in peer.get("/api/knowledge/collections").json()] == ["Finance docs"]
    assert peer.get("/api/knowledge/collections").json()[0]["can_write"] is False
    assert peer.post(f"/api/knowledge/collections/{cid}/search", json={"query": "billing"}).json()["results"]
    assert peer.delete(f"/api/knowledge/collections/{cid}").status_code == 403  # can read, cannot change
    assert upload(peer, cid, "p.txt", b"nope").status_code == 403
    assert outsider.get(f"/api/knowledge/collections/{cid}/documents").status_code == 404
    assert outsider.get("/api/knowledge/collections").json() == []
    assert manager.get("/api/knowledge/collections").json()[0]["can_write"] is True
    assert upload(manager, cid, "m.txt", b"manager adds a note about invoices").status_code == 200
    # leaving the team removes access immediately
    owner.patch(f"/api/admin/users/{peer.user_id}", json={"team_ids": []})
    assert peer.get("/api/knowledge/collections").json() == []
    assert peer.get(f"/api/knowledge/collections/{cid}/documents").status_code == 404


def test_deny_precedence_blocks_knowledge_even_with_role_grant(owner, app):
    enable(owner, "knowledge")
    u = make_user(owner, app, "denied@example.test")
    assert u.get("/api/knowledge/collections").status_code == 200
    owner.put(f"/api/admin/users/{u.user_id}/override", json={"denies": ["mode.knowledge"]})
    r = u.get("/api/knowledge/collections")
    assert r.status_code == 403 and r.json()["detail"]["code"] == "forbidden"
    assert u.post("/api/knowledge/collections", json={"name": "x"}).status_code == 403


# ---- chat integration ----------------------------------------------------------------------------------
def last_system():
    msgs = fakebackend.calls[-1]["messages"]
    return msgs[0]["content"] if msgs[0]["role"] == "system" else ""


def gen(api, cid, text, **kw):
    code, ev = api.stream(f"/api/chat/conversations/{cid}/generate",
                          {"mode": "send", "content": text, "model_id": model_id(api), **kw})
    assert code == 200, ev
    return ev


def test_chat_retrieval_cites_sources_and_persists_them(owner, st):
    enable(owner, "knowledge")
    col = new_collection(owner)
    upload(owner, col, "ops.md", DOC.encode())
    conv = owner.post("/api/chat/conversations", json={"collection_ids": [col]}).json()["id"]
    ev = gen(owner, conv, "When is the billing maintenance window?")
    src = next(e for e in ev if e["type"] == "sources")["sources"]
    assert src[0]["n"] == 1 and src[0]["file"] == "ops.md" and "second Tuesday" in src[0]["excerpt"]
    sysmsg = last_system()
    assert "[1]" in sysmsg and "second Tuesday" in sysmsg and "untrusted reference material" in sysmsg
    assert fakebackend.calls[-1]["messages"][-1]["content"] == "When is the billing maintenance window?"  # question itself unchanged
    done = next(e for e in ev if e["type"] == "done")
    assert done["sources"] == src
    saved = owner.get(f"/api/chat/conversations/{conv}").json()
    asst = [m for m in saved["messages"] if m["role"] == "assistant"][0]
    assert asst["sources"][0]["file"] == "ops.md"
    assert saved["context"]["collection_ids"] == [col]
    # sources are stored encrypted
    with st.db.session() as s:
        row = s.execute(select(MessageSources)).scalars().first()
        assert "ops.md" not in row.sources_enc


def test_question_with_no_matching_passages_adds_nothing(owner):
    enable(owner, "knowledge")
    col = new_collection(owner)
    upload(owner, col, "ops.md", DOC.encode())
    conv = owner.post("/api/chat/conversations", json={"collection_ids": [col]}).json()["id"]
    ev = gen(owner, conv, "Tell me about quantum chromodynamics")
    assert not [e for e in ev if e["type"] == "sources"]
    assert "PASSAGES" not in last_system()


def test_attached_file_text_is_not_used_as_the_search_query(owner):
    enable(owner, "knowledge")
    col = new_collection(owner)
    upload(owner, col, "ops.md", DOC.encode())
    conv = owner.post("/api/chat/conversations", json={"collection_ids": [col]}).json()["id"]
    body = '<file name="x.txt">\nlunch parking permits lunch parking\n</file>\n\nwhen is the maintenance window'
    ev = gen(owner, conv, body)
    src = next(e for e in ev if e["type"] == "sources")["sources"]
    assert "second Tuesday" in src[0]["excerpt"]


def test_no_save_chat_can_use_knowledge_and_stores_nothing(owner, st):
    enable(owner, "knowledge")
    col = new_collection(owner)
    upload(owner, col, "ops.md", DOC.encode())
    with st.db.session() as s:
        before = (s.execute(select(func.count()).select_from(Message)).scalar(), s.execute(select(func.count()).select_from(MessageSources)).scalar())
    code, ev = owner.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "billing maintenance window?"}],
                                                              "model_id": model_id(owner), "collection_ids": [col]})
    assert code == 200
    assert next(e for e in ev if e["type"] == "done")["sources"][0]["file"] == "ops.md"
    with st.db.session() as s:
        after = (s.execute(select(func.count()).select_from(Message)).scalar(), s.execute(select(func.count()).select_from(MessageSources)).scalar())
    assert before == after


def test_cannot_attach_someone_elses_collection(owner, app):
    enable(owner, "knowledge")
    alice = make_user(owner, app, "alice@example.test")
    bob = make_user(owner, app, "bob@example.test")
    col = new_collection(alice)
    owner.put(f"/api/admin/users/{bob.user_id}/override", json={"grants": ["chat.ephemeral"]})
    assert bob.post("/api/chat/conversations", json={"collection_ids": [col]}).status_code == 404
    conv = bob.post("/api/chat/conversations", json={}).json()["id"]
    assert bob.patch(f"/api/chat/conversations/{conv}", json={"collection_ids": [col]}).status_code == 404
    code, _ = bob.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "x"}], "model_id": model_id(bob), "collection_ids": [col]})
    assert code == 404


def test_access_withdrawn_after_attaching_is_honoured_on_the_next_message(owner, app):
    enable(owner, "knowledge")
    team = owner.post("/api/admin/teams", json={"name": "Ops"}).json()["id"]
    alice = make_user(owner, app, "alice@example.test", teams=[team])
    col = new_collection(alice, team_id=team)
    upload(alice, col, "ops.md", DOC.encode())
    peer = make_user(owner, app, "peer@example.test", teams=[team])
    conv = peer.post("/api/chat/conversations", json={"collection_ids": [col]}).json()["id"]
    assert [e for e in gen(peer, conv, "billing maintenance window") if e["type"] == "sources"]
    owner.patch(f"/api/admin/users/{peer.user_id}", json={"team_ids": []})
    ev = gen(peer, conv, "billing maintenance window again")
    assert "second Tuesday" not in last_system() and "PASSAGES" not in last_system()
    assert next(e for e in ev if e["type"] == "sources")["skipped"] == 1  # the UI is told something was skipped


def test_knowledge_permission_revoked_blocks_generation_with_attached_collections(owner, app):
    enable(owner, "knowledge")
    u = make_user(owner, app, "u@example.test")
    col = new_collection(u)
    conv = u.post("/api/chat/conversations", json={"collection_ids": [col]}).json()["id"]
    owner.put(f"/api/admin/users/{u.user_id}/override", json={"denies": ["mode.knowledge"]})
    code, body = u.stream(f"/api/chat/conversations/{conv}/generate", {"mode": "send", "content": "hi", "model_id": model_id(u)})
    assert code == 403
    # detaching restores plain chat
    assert u.patch(f"/api/chat/conversations/{conv}", json={"collection_ids": []}).status_code == 200
    assert gen(u, conv, "hi")


# ---- assistants ----------------------------------------------------------------------------------------
def test_assistant_prompt_params_and_model_reach_the_backend(owner):
    enable(owner, "agents")
    r = owner.post("/api/assistants", json={"name": "Terse", "description": "Short answers", "system_prompt": "Answer in one sentence.",
                                            "params": {"temperature": 0.2, "max_tokens": 64}, "model_id": model_id(owner)})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert r.json()["system_prompt"] == "Answer in one sentence."
    conv = owner.post("/api/chat/conversations", json={"assistant_id": aid}).json()["id"]
    gen(owner, conv, "hello")
    call = fakebackend.calls[-1]
    assert call["messages"][0] == {"role": "system", "content": "Answer in one sentence."}
    assert call["temperature"] == 0.2 and call["max_tokens"] == 64
    # the person's own explicit setting wins over the assistant's default
    owner.put("/api/admin/settings/modes.chat.enabled", json={"value": True})
    gen(owner, conv, "again", params={"temperature": 0.9})
    assert fakebackend.calls[-1]["temperature"] == 0.9


def test_assistant_validation_and_visibility_rules(owner, app):
    enable(owner, "agents", "knowledge")
    t1 = owner.post("/api/admin/teams", json={"name": "Finance"}).json()["id"]
    t2 = owner.post("/api/admin/teams", json={"name": "Legal"}).json()["id"]
    mem = make_user(owner, app, "mem@example.test", teams=[t1])
    peer = make_user(owner, app, "peer@example.test", teams=[t1])
    outsider = make_user(owner, app, "out@example.test", teams=[t2])
    mgr = make_user(owner, app, "mgr@example.test", role="team_manager", teams=[t1])
    base = {"name": "Helper", "system_prompt": "SECRET-PROMPT"}
    assert mem.post("/api/assistants", json={**base, "params": {"temperature": 9}}).status_code == 422
    assert mem.post("/api/assistants", json={**base, "visibility": "team", "team_id": t2}).status_code == 403
    assert mem.post("/api/assistants", json={**base, "visibility": "org"}).status_code == 403  # needs team-manager rights
    private = mem.post("/api/assistants", json={**base, "name": "Mine"}).json()["id"]
    team_a = mem.post("/api/assistants", json={**base, "name": "Team one", "visibility": "team", "team_id": t1}).json()["id"]
    org_a = mgr.post("/api/assistants", json={**base, "name": "Everyone", "visibility": "org"}).json()["id"]
    names = lambda api: sorted(a["name"] for a in api.get("/api/assistants").json())  # noqa: E731
    assert names(mem) == ["Everyone", "Mine", "Team one"]
    assert names(peer) == ["Everyone", "Team one"]
    assert names(outsider) == ["Everyone"]
    assert outsider.get(f"/api/assistants/{team_a}").status_code == 404
    assert peer.get(f"/api/assistants/{private}").status_code == 404
    # people who can only use an assistant never read its prompt back or change it
    got = peer.get(f"/api/assistants/{team_a}").json()
    assert "system_prompt" not in got and got["can_edit"] is False
    assert peer.put(f"/api/assistants/{team_a}", json={**base, "name": "hijack"}).status_code == 403
    assert peer.delete(f"/api/assistants/{team_a}").status_code == 403
    assert mgr.get(f"/api/assistants/{team_a}").json()["system_prompt"] == "SECRET-PROMPT"  # team manager may edit
    # knowledge rules: org assistants carry no collections; team assistants only that team's collections
    personal = new_collection(mem)
    teamcol = new_collection(mem, "t", team_id=t1)
    assert mgr.post("/api/assistants", json={**base, "visibility": "org", "collection_ids": [teamcol]}).status_code == 422
    assert mem.post("/api/assistants", json={**base, "visibility": "team", "team_id": t1, "collection_ids": [personal]}).status_code == 422
    assert mem.post("/api/assistants", json={**base, "visibility": "team", "team_id": t1, "collection_ids": [teamcol]}).status_code == 200
    assert mem.post("/api/assistants", json={**base, "collection_ids": [personal]}).status_code == 200
    assert outsider.post("/api/assistants", json={**base, "collection_ids": [personal]}).status_code == 404
    # deleting the assistant leaves an existing conversation with a clear message, not a crash
    conv = peer.post("/api/chat/conversations", json={"assistant_id": team_a}).json()["id"]
    assert mem.delete(f"/api/assistants/{team_a}").status_code == 200
    code, body = peer.stream(f"/api/chat/conversations/{conv}/generate", {"mode": "send", "content": "hi", "model_id": model_id(peer)})
    assert code == 409 and body["detail"]["code"] == "assistant_gone"


def test_team_assistant_with_team_knowledge_works_for_a_teammate(owner, app):
    enable(owner, "agents", "knowledge")
    t = owner.post("/api/admin/teams", json={"name": "Ops"}).json()["id"]
    author = make_user(owner, app, "author@example.test", teams=[t])
    mate = make_user(owner, app, "mate@example.test", teams=[t])
    col = new_collection(author, team_id=t)
    upload(author, col, "ops.md", DOC.encode())
    aid = author.post("/api/assistants", json={"name": "Ops bot", "system_prompt": "You are the Ops bot.", "visibility": "team",
                                                "team_id": t, "collection_ids": [col]}).json()["id"]
    conv = mate.post("/api/chat/conversations", json={"assistant_id": aid}).json()["id"]
    ev = gen(mate, conv, "billing maintenance window")
    assert next(e for e in ev if e["type"] == "sources")["sources"][0]["file"] == "ops.md"
    assert "You are the Ops bot." in last_system() and "second Tuesday" in last_system()
    assert len([m for m in fakebackend.calls[-1]["messages"] if m["role"] == "system"]) == 1  # one system message


# ---- code workspace ------------------------------------------------------------------------------------
def test_code_workspace_is_separate_and_uses_the_code_prompt(owner):
    enable(owner, "code")
    conv = owner.post("/api/chat/conversations", json={"mode": "code"}).json()["id"]
    gen(owner, conv, "write fizzbuzz")
    assert "programming assistant" in last_system() and "cannot run code" in last_system()
    plain = owner.post("/api/chat/conversations", json={}).json()["id"]
    assert [c["id"] for c in owner.get("/api/chat/conversations", params={"mode": "code"}).json()] == [conv]
    assert [c["id"] for c in owner.get("/api/chat/conversations").json()] == [plain]
    gen(owner, plain, "hi")
    assert "programming assistant" not in last_system()
    owner.put("/api/admin/settings/code.system_prompt", json={"value": "Custom code rules."})
    gen(owner, conv, "again")
    assert last_system() == "Custom code rules."
    code, ev = owner.stream("/api/chat/ephemeral/generate", {"messages": [{"role": "user", "content": "x"}], "model_id": model_id(owner), "workspace": "code"})
    assert code == 200 and last_system() == "Custom code rules."


def test_code_workspace_requires_its_own_permission(owner, app):
    enable(owner, "code")
    u = make_user(owner, app, "u@example.test")
    owner.put(f"/api/admin/users/{u.user_id}/override", json={"denies": ["mode.code"]})
    assert u.post("/api/chat/conversations", json={"mode": "code"}).status_code == 403
    assert u.get("/api/chat/conversations", params={"mode": "code"}).status_code == 403
    assert u.post("/api/chat/conversations", json={}).status_code == 200  # plain chat unaffected
    other = make_user(owner, app, "o@example.test")
    c = other.post("/api/chat/conversations", json={"mode": "code"}).json()["id"]
    assert u.get(f"/api/chat/conversations/{c}").status_code == 404


def test_code_model_hint_comes_from_tags_or_name_and_says_which(owner):
    enable(owner, "code")
    models = owner.get("/api/chat/models", params={"mode": "code"}).json()
    assert models and models[0]["code_hint"] is None  # nothing is claimed without a reason
    mid = models[0]["id"]
    assert owner.patch(f"/api/admin/models/{mid}", json={"tags": ["code"]}).status_code == 200
    assert owner.get("/api/chat/models", params={"mode": "code"}).json()[0]["code_hint"] == "tagged for code by your administrator"


def test_protection_panel_discloses_how_knowledge_is_handled(owner):
    topics = lambda: {l["topic"]: l["text"] for l in owner.get("/api/chat/protection").json()["lines"]}  # noqa: E731
    assert "Knowledge" not in topics()  # nothing is said about a feature that is off
    enable(owner, "knowledge")
    t = topics()["Knowledge"]
    assert "memory" in t and "readable form" in t and "keyword" in t


def test_stopword_only_questions_match_nothing_and_stemming_is_english_only(owner):
    from semechka.knowledge_svc import fts_query
    assert fts_query("what is the") is None
    assert fts_query("Tell me about the billing window") == '"billing" OR "window"'
    assert fts_query('NEAR("x") AND * "') == '"near" OR "x"'  # operators are neutralised into plain quoted words
