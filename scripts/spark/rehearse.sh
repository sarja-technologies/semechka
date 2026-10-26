#!/usr/bin/env bash
# Run ON the Spark (deployment evidence; see docs/ENTERPRISE.md).  usage: rehearse.sh <version> <previous-schema>. Backs up live CMSIT AI data, then rehearses the
# upgrade, a real-document end-to-end run, the downgrade and a second upgrade on a COPY. Live data is only read.
set -euo pipefail
V=$1; PREV=${2:-7}; TS=$(date +%Y%m%d-%H%M%S); L=~/semechka-deploy/roasted/data
B=~/semechka-deploy/backups/roasted-cmsit-pre-$V-$TS; umask 077; mkdir -p $B
sqlite3 $L/semechka.db ".backup $B/semechka.db"; cp -a $L/master.key $B/; cp -a $L/assets $B/
[ -d $L/attachments ] && cp -a $L/attachments $B/ || true
echo "BACKUP=$B integrity=$(sqlite3 $B/semechka.db 'pragma integrity_check') schema=$(sqlite3 $B/semechka.db "select value from meta where key='schema_version'")"
cd ~/semechka-deploy/releases && sha256sum -c semechka-$V.tar.gz.sha256 >/dev/null && rm -rf semechka-$V && tar xzf semechka-$V.tar.gz
R=~/semechka-deploy/rehearsal; rm -rf $R; mkdir -p $R/data; chmod 700 $R $R/data; cp -a $B/. $R/data/
( cd semechka-$V && ./install.sh --no-service --name rehearsal --prefix $R/app --data $R/data --port 18787 >$R/install.log 2>&1 ) && echo "rehearsal install ok" || { tail -20 $R/install.log; exit 1; }
DB=$R/data/semechka.db; PY=$R/app/venv/bin/python
roles() { sqlite3 $DB "select id, grants from roles order by id" | md5sum | cut -c1-12; }
sv() { sqlite3 $DB "select value from meta where key='schema_version'"; }
cat > $R/step.py <<'PYEOF'
import pathlib, sys
from semechka.config import Config
from semechka.db import Database
from semechka import migrations
c = Config(data_dir=pathlib.Path(sys.argv[2])); c.background = False
if sys.argv[1] == "up":
    from semechka.state import AppState
    AppState.build(c).db.engine.dispose()
else:
    c.ensure(); db = Database(c.db_url); migrations.downgrade(db, int(sys.argv[3]), c.data_dir); db.engine.dispose()
PYEOF
echo "before: schema=$(sv) roles=$(roles) users=$(sqlite3 $DB 'select count(*) from users') convs=$(sqlite3 $DB 'select count(*) from conversations')"
$PY -I $R/step.py up $R/data
echo "after up: schema=$(sv) roles=$(roles)"
sqlite3 $DB "select 'org', slug, name from orgs; select 'members', count(*) from org_members; select 'brand', value from setting_values where key='brand.tab_title';"
if [ -f ~/semechka-deploy/releases/e2e_files.py ]; then
  echo "--- real-document end to end (aarch64, rehearsal copy only):"
  cp ~/semechka-deploy/releases/e2e_files.py $R/e2e_files.py; $PY -I $R/e2e_files.py $R/data
fi
$PY -I $R/step.py down $R/data $PREV
echo "after down: schema=$(sv) roles=$(roles) new_tables=$(sqlite3 $DB "select count(*) from sqlite_master where name in ('ws_files','jobs','contexts','orgs')") workspaces_dir=$([ -d $R/data/workspaces ] && echo present || echo absent)"
$PY -I $R/step.py up $R/data
echo "re-up: schema=$(sv) orgs=$(sqlite3 $DB 'select count(*) from orgs') members=$(sqlite3 $DB "select count(*) from org_members where org_id=(select id from orgs where is_default=1)")"
rm -rf $R; echo "rehearsal copy deleted"
