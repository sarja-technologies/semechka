"""Content must stay out of general-purpose records: application logs, the audit chain, job rows, error messages.
A full realistic flow runs with unique marker strings in a file, an agent's instructions, a task and a memory, and every
place that is not an encrypted content column is searched for them."""
import json
import logging

from sqlalchemy import text

from . import fake_llm as L
from .test_agents import drive, marketing, mk_agent, run_agent
from .test_files_http import up

MARKERS = {"file": "ZEBRA-FILE-CONTENT-7731", "instr": "ZEBRA-INSTRUCTIONS-4410", "task": "ZEBRA-TASK-TEXT-9052", "mem": "ZEBRA-MEMORY-3318",
           "name": "ZEBRA-FILENAME-6624"}


def test_no_content_in_logs_audit_jobs_or_errors(env, app, st, caplog):
    caplog.set_level(logging.DEBUG)
    _acme, c, wid, _uid, _ids, ctx_id = marketing(env, app)
    mia = c["mia@acme.test"]
    fid = up(mia, wid, f"{MARKERS['name']}.md", f"# Notes\n{MARKERS['file']} is the launch codeword.\n".encode()).json()["id"]
    mia.put(f"/api/contexts/{ctx_id}/items/{fid}")
    drive(st)
    aid = mk_agent(mia, wid, name="Hygiene", instructions=MARKERS["instr"], tools=["search_context", "read_file", "remember", "create_document"],
                   memory={"persistent": True}, approval="never")
    L.say(L.tool("search_context", query="launch codeword"), L.tool("remember", text=MARKERS["mem"]),
          L.tool("create_document", name="out.docx", title="t", blocks=[{"type": "paragraph", "text": MARKERS["file"]}]),
          L.tool("read_file", file="does-not-exist-anywhere"), L.final("done"))
    rid = run_agent(mia, aid, f"Find the codeword. {MARKERS['task']}")
    drive(st)
    run = mia.get(f"/api/runs/{rid}", params={"detail": 1}).json()
    assert run["status"] == "succeeded" and MARKERS["task"] in run["task"]  # the owner can of course see their own content
    assert mia.post(f"/api/workspaces/{wid}/retrieve", json={"query": "launch codeword"}).json()["passages"]

    content = [v for k, v in MARKERS.items() if k != "name"]
    leaks = []
    for rec in caplog.records:
        line = rec.getMessage()
        leaks += [f"log: {m}" for m in content if m in line]
    with st.db.session() as s:
        for tbl, cols in (("audit_events", ["detail", "target", "action"]), ("jobs", ["payload", "result", "error", "progress_text"]),
                          ("ws_file_access", ["via"]), ("agent_versions", ["config"]), ("agent_steps", ["error", "meta"]), ("agent_runs", ["usage"])):
            for col in cols:
                for (val,) in s.execute(text(f'select {col} from {tbl}')):
                    blob = json.dumps(val) if not isinstance(val, str) else val
                    leaks += [f"{tbl}.{col}: {m}" for m in content if m in blob]
        # the file NAME is administrative metadata (listable, searchable); it may appear in the clear in file tables only
        enc = [r[0] for r in s.execute(text("select text_enc from ws_chunks"))] + [r[0] for r in s.execute(text("select text_enc from agent_memory"))]
        enc += [r[0] for r in s.execute(text("select task_enc from agent_runs"))] + [r[0] for r in s.execute(text("select instructions_enc from agents"))]
        leaks += [f"unencrypted content column: {e[:30]}" for e in enc if not e.startswith("v1:")]
        leaks += [f"ciphertext holds plaintext: {m}" for e in enc for m in content if m in e]
    assert leaks == []
