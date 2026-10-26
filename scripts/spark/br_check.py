"""Run on the Spark in the rehearsal venv: open a restored data directory and prove the workspace data survived."""
import pathlib
import sys

from semechka import contexts_svc as C
from semechka import files_svc as F
from semechka.authz import Authz
from semechka.config import Config
from semechka.models import User
from semechka.models_files import Context, File
from semechka.models_tenancy import Org
from semechka.state import AppState
from sqlalchemy import select

c = Config(data_dir=pathlib.Path(sys.argv[1])); c.background = False
st = AppState.build(c)
with st.db.session() as s:
    org = s.execute(select(Org).where(Org.slug == "e2e-rehearsal-org")).scalar_one()
    u = s.execute(select(User).order_by(User.created_at)).scalars().first()
    az = Authz(s, u.id)
    files = s.execute(select(File).where(File.org_id == org.id)).scalars().all()
    ok = 0
    for f in files:
        _, v, data = F.read_file(st, s, az, f.id)  # decrypts with the org key from the RESTORED master key
        assert len(data) == v.size; ok += 1
    cx = s.execute(select(Context).where(Context.org_id == org.id)).scalars().first()
    r = C.retrieve(st, s, az, "enterprise seat cost", context_ids=[cx.id])
    print(f"restored: {ok}/{len(files)} files decrypt; context '{cx.name}' v{cx.version}; retrieval mode={r.mode} top={r.hits[0].file_name if r.hits else None}")
    sys.exit(0 if ok == len(files) and r.hits else 1)
