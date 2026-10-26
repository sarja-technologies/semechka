#!/usr/bin/env bash
# Run ON the Spark. usage: rehearse_rollout.sh <version>
# Validates the cloud rollout against a COPY of CMSIT AI's real configuration (real settings, roles, models, branding, accounts).
# This is a rehearsal on a copy, NOT a test of the live service. The live database is only read (sqlite .backup); the copy's model
# backend is pointed at a dead address first so the copy can never touch the GPU engine or resubmit anyone's jobs; the throwaway
# accounts exist only in the copy, with random passwords held in memory and never printed; everything is deleted at the end.
set -euo pipefail
V=$1; TS=$(date +%Y%m%d-%H%M%S); L=~/semechka-deploy/roasted/data; REL=~/semechka-deploy/releases
B=~/semechka-deploy/backups/roasted-cmsit-pre-$V-rollout-$TS; R=~/semechka-deploy/rehearsal; umask 077
live() { sqlite3 -readonly $L/semechka.db "select 'users',count(*) from users union all select 'conversations',count(*) from conversations union all select 'messages',count(*) from messages union all select 'schema',value from meta where key='schema_version'" | tr '\n' ' '; }
echo "live before : $(live)"
mkdir -p $B; sqlite3 $L/semechka.db ".backup $B/semechka.db"; cp -a $L/master.key $B/; cp -a $L/assets $B/; [ -d $L/attachments ] && cp -a $L/attachments $B/ || true
echo "RECOVERY POINT $B integrity=$(sqlite3 $B/semechka.db 'pragma integrity_check') sha256=$(sha256sum $B/semechka.db | cut -c1-16)"
( cd $REL && sha256sum -c semechka-$V.tar.gz.sha256 >/dev/null && rm -rf semechka-$V && tar xzf semechka-$V.tar.gz )
rm -rf $R; mkdir -p $R/data; chmod 700 $R $R/data; cp -a $B/. $R/data/
( cd $REL/semechka-$V && ./install.sh --no-service --name rehearsal --prefix $R/app --data $R/data --port 18787 >$R/install.log 2>&1 ) || { tail -20 $R/install.log; exit 1; }
PY=$R/app/venv/bin/python; DB=$R/data/semechka.db
sqlite3 $DB "update backends set base_url='http://127.0.0.1:9'"   # the copy can never reach the real engine
export ADM_PW=$($PY -c 'import secrets;print(secrets.token_urlsafe(18))') MEM_PW=$($PY -c 'import secrets;print(secrets.token_urlsafe(18))')
cat > $R/mkusers.py <<'PYEOF'
import os, pathlib
from semechka.config import Config
from semechka.state import AppState
from semechka.models import User, UserRole
from semechka.security import hash_password
c = Config(data_dir=pathlib.Path(os.environ["SEMECHKA_DATA_DIR"])); c.background = False
st = AppState.build(c)
with st.db.session(write=True) as s:
    for email, pw, role in (("rehearsal-admin@rehearsal.invalid", os.environ["ADM_PW"], "owner"), ("rehearsal-member@rehearsal.invalid", os.environ["MEM_PW"], "member")):
        u = User(email=email, display_name="Rehearsal " + role, password_hash=hash_password(pw), status="active")
        s.add(u); s.flush(); s.add(UserRole(user_id=u.id, role_id=role))
st.db.engine.dispose()
PYEOF
SEMECHKA_DATA_DIR=$R/data $PY -I $R/mkusers.py
echo "copy: $(sqlite3 $DB 'select count(*) from users') accounts (2 are throwaway), schema $(sqlite3 $DB "select value from meta where key='schema_version'")"
SEMECHKA_DATA_DIR=$R/data nohup $R/app/venv/bin/semechka serve --port 18787 >$R/serve.log 2>&1 &
SRV=$!; trap 'kill $SRV 2>/dev/null || true' EXIT
for _ in $(seq 1 60); do curl -fs http://127.0.0.1:18787/api/health >/dev/null 2>&1 && break; sleep 1; done
cp ~/semechka-deploy/stage-scripts/rehearse_rollout_http.py ~/semechka-deploy/stage-scripts/rollout.py $R/
ROLLOUT_URL=http://127.0.0.1:18787 $PY -I $R/rehearse_rollout_http.py $R && RC=0 || RC=$?
kill $SRV 2>/dev/null || true; wait $SRV 2>/dev/null || true; trap - EXIT
echo "live after  : $(live)   (only .backup read it; counts may differ by real activity)"
echo "recovery point still intact: integrity=$(sqlite3 $B/semechka.db 'pragma integrity_check') sha256=$(sha256sum $B/semechka.db | cut -c1-16)"
rm -rf $R; echo "rehearsal copy and throwaway accounts deleted (exit $RC)"; exit $RC
