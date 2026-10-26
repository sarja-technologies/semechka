"""Run ON the Spark inside the rehearsal venv: real documents through upload -> ingest -> embed -> context -> retrieve,
using the REAL embedding service (Qwen3-Embedding-0.6B on 127.0.0.1:8760). Queries share no keywords with the passages."""
import io
import pathlib
import sys

from semechka import contexts_svc as C
from semechka import files_svc as F
from semechka import jobs_svc
from semechka import tenancy_svc as T
from semechka.authz import Authz
from semechka.config import Config
from semechka.models import User
from semechka.models_tenancy import OrgMember
from semechka.state import AppState
from sqlalchemy import select

c = Config(data_dir=pathlib.Path(sys.argv[1])); c.background = False
st = AppState.build(c)
from docx import Document
from openpyxl import Workbook
from pptx import Presentation

d = Document(); d.add_heading("Aurora Pricing", 1); d.add_paragraph("The enterprise tier costs 4200 dollars per seat each year."); b = io.BytesIO(); d.save(b); docx = b.getvalue()
d = Document(); d.add_heading("Travel", 1); d.add_paragraph("Taxi rides over fifty dollars need director approval."); b = io.BytesIO(); d.save(b); travel = b.getvalue()
wb = Workbook(); ws = wb.active; ws.title = "Revenue"; ws.append(["month", "total"]); ws.append(["september", 91500]); b = io.BytesIO(); wb.save(b); xlsx = b.getvalue()
p = Presentation(); sl = p.slides.add_slide(p.slide_layouts[1]); sl.shapes.title.text = "Launch"; sl.placeholders[1].text = "ship the falcon release in spring"; b = io.BytesIO(); p.save(b); pptx = b.getvalue()
with st.db.session() as s:
    st.settings.set(s, "rehearsal", "embeddings.url", "http://127.0.0.1:8760")
    st.settings.set(s, "rehearsal", "embeddings.model", "qwen3-embedding-0.6b")
    u = s.execute(select(User).where(User.status == "active").order_by(User.created_at)).scalars().first()
    o = T.create_org(s, "", "E2E Rehearsal Org", owner_id=u.id)
    m = s.get(OrgMember, (o.id, u.id)); m.role_ids = m.role_ids + [T.get_role(s, o.id, "dept_admin").id]
    s.flush(); az = Authz(s, u.id)
    w = T.create_workspace(s, az, o.id, "Rehearsal")
    ids = [F.create_file(st, s, az, w.id, n, data)[0].id for n, data in (("pricing.docx", docx), ("travel.docx", travel), ("rev.xlsx", xlsx), ("deck.pptx", pptx))]
    cx = C.create_context(s, az, w.id, "All")
    for i in ids:
        C.add_item(s, az, cx.id, i)
    s.commit()
print("ran", jobs_svc.run_pending(st), "jobs")
with st.db.session() as s:
    from semechka.models_files import Chunk, Job
    from sqlalchemy import func
    print("chunks", s.query(Chunk).count(), "embedded", s.query(Chunk).filter(Chunk.embedding.is_not(None)).count(), "jobs", {k: v for k, v in s.execute(select(Job.status, func.count()).group_by(Job.status)).all()})
    az = Authz(s, u.id)
    ok = 0
    for q, want in (("how much does the top plan cost for each user", "pricing.docx"), ("can I take a cab without asking my boss", "travel.docx"),
                    ("income for the ninth month", "rev.xlsx"), ("when do we release the new product", "deck.pptx")):
        r = C.retrieve(st, s, az, q, context_ids=[cx.id], k=3)
        top = r.hits[0] if r.hits else None
        good = bool(top and top.file_name == want); ok += good
        print(f"{q!r}: mode={r.mode} top={top and top.file_name} via={top and top.mode} cos={top and top.cosine and round(top.cosine, 2)} ok={good}")
    print("junk query:", [h.file_name for h in C.retrieve(st, s, az, "chocolate banana bread recipe", context_ids=[cx.id]).hits])
    print(f"SEMANTIC {ok}/4")
    s.rollback()
