"""Run ON the Spark inside the rehearsal venv: a REAL language model drives a cloud agent over real spreadsheets, with the
REAL sandbox for code execution. Nothing is scripted. Prints the evidence; every claim is checked against the files."""
import io
import pathlib
import sys
import time

from semechka import agent_svc as A
from semechka import files_svc as F
from semechka import jobs_svc
from semechka import tenancy_svc as T
from semechka.authz import Authz
from semechka.config import Config
from semechka.models import Model, User
from semechka.models_agents import AgentStep
from semechka.models_files import File, Job
from semechka.models_tenancy import OrgMember
from semechka.state import AppState
from sqlalchemy import select

MODEL_ALIAS = sys.argv[2] if len(sys.argv) > 2 else "qwen3.6-35b-a3b@MXFP4_MOE"
c = Config(data_dir=pathlib.Path(sys.argv[1])); c.background = False
st = AppState.build(c)
from openpyxl import Workbook


def book(rows):
    wb = Workbook(); ws = wb.active; ws.title = "P&L"
    for r in rows: ws.append(r)
    b = io.BytesIO(); wb.save(b); return b.getvalue()
sept = [["category", "amount_usd"], ["Revenue", 120000], ["Cost of goods", 50000], ["Marketing", 9000], ["Payroll", 38000], ["Rent", 6000], ["Software", 2500]]
octo = [["category", "amount_usd"], ["Revenue", 134000], ["Cost of goods", 56500], ["Marketing", 14500], ["Payroll", 38000], ["Rent", 6000], ["Software", 3100]]
def net(rows): return sum(r[1] if r[0] == "Revenue" else -r[1] for r in rows[1:])
exp = {"sept_net": net(sept), "oct_net": net(octo), "rev_pct": round((134000 - 120000) / 120000 * 100, 1), "mkt_pct": round((14500 - 9000) / 9000 * 100, 1)}
print("EXPECTED (computed here, independently):", exp)
with st.db.session() as s:
    st.settings.set(s, "e2e", "cloud.agents.enabled", True); st.settings.set(s, "e2e", "sandbox.enabled", True)
    st.settings.set(s, "e2e", "embeddings.url", "http://127.0.0.1:8760"); st.settings.set(s, "e2e", "embeddings.model", "qwen3-embedding-0.6b")
    u = s.execute(select(User).where(User.status == "active").order_by(User.created_at)).scalars().first()
    org = T.create_org(s, "", "E2E Finance Co", owner_id=u.id)
    m = s.get(OrgMember, (org.id, u.id)); m.role_ids = m.role_ids + [T.get_role(s, org.id, "dept_admin").id]
    s.flush(); az = Authz(s, u.id)
    ws = T.create_workspace(s, az, org.id, "Finance")
    for n, d in (("finance-2026-09.xlsx", book(sept)), ("finance-2026-10.xlsx", book(octo)),
                 ("reporting-policy.md", b"# Management report policy\nEvery management report must contain: an executive summary, a month-over-month table with absolute and percentage change, risks, and next steps. Currency is USD.\n")):
        F.create_file(st, s, az, ws.id, n, d)
    mdl = s.execute(select(Model).where(Model.alias == MODEL_ALIAS)).scalar_one()
    agent = A.create_agent(st, s, az, ws.id, "Finance analyst", instructions="You are a careful finance analyst. Compute figures with tools, never by guessing. Keep answers short.",
        model_id=mdl.id, tools=["list_files", "read_file", "read_table", "table_stats", "calc", "python", "create_document", "remember", "recall", "search_context"],
        memory={"persistent": True, "retention_days": 90}, approval="sensitive", limits={"max_steps": 16, "timeout_s": 1500})
    ids = {"org": org.id, "ws": ws.id, "agent": agent.id, "user": u.id}; s.commit()
print("setup ok; model:", MODEL_ALIAS); jobs_svc.run_pending(st, 20)  # ingest
from semechka import services


def run_task(task):
    with st.db.session() as s:
        az = Authz(s, ids["user"]); user = s.get(User, ids["user"]); pr = services.load_principal(s, user)
        run = A.start_run(st, s, az, ids["agent"], task, pr); rid = run.id; s.commit()
    t0 = time.time()
    while time.time() - t0 < 1500:
        jobs_svc.run_pending(st, 5)
        with st.db.session() as s:
            job = s.get(Job, s.get(A.AgentRun, rid).job_id)
            if job.status == "awaiting_approval":
                stp = s.execute(select(AgentStep).where(AgentStep.run_id == rid, AgentStep.status == "awaiting_approval")).scalars().first()
                print(f"   [approval requested for {stp.tool}: {A._dec(st, stp.run_id and ids['org'], stp.args_enc)[:160]!r}] -> approving as workspace administrator")
                A.decide_approval(s, Authz(s, ids["user"]), rid, stp.n, True); s.commit()
            elif job.status in ("succeeded", "failed", "cancelled"):
                return rid, job.status, job.error, round(time.time() - t0, 1)
    return rid, "timeout", "", 1500
def show(rid):
    with st.db.session() as s:
        run = s.get(A.AgentRun, rid)
        for stp in A._steps(s, rid):
            body = A._dec(st, run.org_id, stp.args_enc if stp.kind == "tool" else stp.result_enc)
            print(f"   step {stp.n:>2} {stp.kind:<5} {stp.tool or '':<16} {stp.status:<9} {(stp.error or '')[:70]} | {body[:110].replace(chr(10),' ')!r}")
        print("   usage:", run.usage); print("   FINAL:", A._dec(st, run.org_id, run.final_enc)[:700]); return run.artifacts
task1 = "Analyse our company financial spreadsheets, compare October with September, create a management report (a Word document) following our reporting policy, and remember the key project context for next month."
print("\n=== RUN 1:", task1)
rid, status, err, secs = run_task(task1); print(f"=> {status} in {secs}s {err}"); arts = show(rid)
ok_doc = False
if arts:
    with st.db.session() as s:
        f = s.get(File, arts[0]["file_id"]); _, _, raw = F.read_file(st, s, Authz(s, ids["user"]), f.id, action="read")
        from docx import Document
        d = Document(io.BytesIO(raw)); text = "\n".join(p.text for p in d.paragraphs) + "\n" + "\n".join(c.text for t in d.tables for r in t.rows for c in r.cells)
        print(f"\n--- report '{f.name}' v{f.current} ({len(raw)} bytes) ---\n{text[:1800]}\n---")
        import re
        nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", text.replace(",", ""))]
        near = lambda want, tol=0.06: any(abs(n - want) <= tol for n in nums)  # a figure may be rounded to 1 or 2 decimals
        checks = {"october net profit": near(exp["oct_net"], 0.5), "september net profit": near(exp["sept_net"], 0.5),
                  "revenue growth %": near(exp["rev_pct"]), "marketing growth %": near(exp["mkt_pct"]), "has risks": "risk" in text.lower(), "has next steps": "next step" in text.lower()}
        for k, v in checks.items(): print(("PASS " if v else "FAIL ") + "document contains " + k)
        ok_doc = all(checks.values())
with st.db.session() as s:
    mem = A.live_memory(s, st, s.get(A.Agent, ids["agent"]))
    print("\nmemory items:", [t for _, t in mem])
print("\n=== RUN 2 (a separate, later conversation) ===")
task2 = "What do you remember about our reporting needs? Then list the files in the workspace."
rid2, status2, err2, secs2 = run_task(task2); print(f"=> {status2} in {secs2}s {err2}"); show(rid2)
print("\nSUMMARY: run1", status, "| run2", status2, "| document checks", "ALL PASS" if ok_doc else "NOT ALL PASSED", "| memory items", len(mem))
