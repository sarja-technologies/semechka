#!/usr/bin/env bash
# Recovery point of one Semechka instance before an update (run ON the Spark): consistent database copy, master key, assets, attachments.
#   backup_instance.sh <roasted|salted|staging> <label>      -> ~/semechka-deploy/backups/<name>-pre-<label>-<timestamp>
# The data directory is never modified. A schema-compatible update needs no restore; this is for the unexpected.
set -euo pipefail
NAME=${1:?instance name}; LABEL=${2:?label, e.g. 0.10.1}
D=~/semechka-deploy/$NAME/data; B=~/semechka-deploy/backups/$NAME-pre-$LABEL-$(date +%Y%m%d-%H%M%S); umask 077
mkdir -p "$B"; sqlite3 -readonly "$D/semechka.db" ".backup $B/semechka.db"
cp -a "$D/master.key" "$B/"; [ -d "$D/assets" ] && cp -a "$D/assets" "$B/"; [ -d "$D/attachments" ] && cp -a "$D/attachments" "$B/" || true
[ -f "$D/backend-hints.json" ] && cp -a "$D/backend-hints.json" "$B/" || true
echo "RECOVERY POINT $B integrity=$(sqlite3 "$B/semechka.db" 'pragma integrity_check') sha256=$(sha256sum "$B/semechka.db" | cut -c1-16) files=$(find "$B" -type f | wc -l) size=$(du -sh "$B" | cut -f1)"
echo "counts: $(sqlite3 -readonly "$B/semechka.db" "select 'users',count(*) from users union all select 'conversations',count(*) from conversations union all select 'messages',count(*) from messages union all select 'media_jobs',count(*) from media_jobs union all select 'media_assets',count(*) from media_assets" | tr '\n' ' ')"
