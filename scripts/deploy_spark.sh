#!/usr/bin/env bash
# Deploy (or update) the two Semechka instances on a remote host over SSH. Contains no secrets.
#
#   SPARK_SSH="ssh -F ~/.ssh/config.semechka spark" SPARK_SCP="scp -F ~/.ssh/config.semechka" \
#   SPARK_HOST_IP=192.168.20.193 ./scripts/deploy_spark.sh dist/semechka-X.Y.Z.tar.gz [roasted|salted|both]
#
# Layout on the remote host (everything under ~/semechka-deploy, nothing outside the user's home):
#   sarness/            dedicated Sarness Engine data dir (loopback only, own token)   -> semechka-sarness.service
#   <name>/app/venv     program files for the instance   (replaced on update)
#   <name>/data         database, master key, uploads    (never touched by an update)
#   secrets/            0600 files: sarness.token, salted-credentials.txt
# Roasted is installed with a one-time setup token and an engine "hint" so the wizard can find the engine; it is never
# completed by this script. Salted is provisioned afterwards by scripts/provision_salted.py.
set -euo pipefail
TARBALL="${1:?usage: deploy_spark.sh <tarball> [roasted|salted|both]}"; WHICH="${2:-both}"
SSH="${SPARK_SSH:?set SPARK_SSH}"; SCP="${SPARK_SCP:?set SPARK_SCP}"; IP="${SPARK_HOST_IP:?set SPARK_HOST_IP}"
ENGINE_URL="${SPARK_ENGINE_URL:-http://127.0.0.1:8752}"
[ -f "$TARBALL" ] && [ -f "$TARBALL.sha256" ] || { echo "tarball and $TARBALL.sha256 required" >&2; exit 1; }
( cd "$(dirname "$TARBALL")" && sha256sum -c "$(basename "$TARBALL").sha256" )
BASE="$(basename "$TARBALL" .tar.gz)"
$SSH 'mkdir -p ~/semechka-deploy/releases'
$SCP "$TARBALL" "$TARBALL.sha256" "$(echo "$SSH" | awk '{print $NF}'):semechka-deploy/releases/"

for NAME in roasted salted; do
  [ "$WHICH" = both ] || [ "$WHICH" = "$NAME" ] || continue
  PORT=8787; [ "$NAME" = salted ] && PORT=8788
  echo "== $NAME on $IP:$PORT"
  $SSH bash -s -- "$BASE" "$NAME" "$PORT" "$IP" "$ENGINE_URL" <<'REMOTE'
set -euo pipefail
BASE=$1; NAME=$2; PORT=$3; IP=$4; ENGINE=$5
R=~/semechka-deploy/releases; D=~/semechka-deploy/$NAME
( cd $R && sha256sum -c $BASE.tar.gz.sha256 >/dev/null && rm -rf $BASE && tar xzf $BASE.tar.gz )
mkdir -p $D/data && chmod 700 $D $D/data
FIRST=0; [ -f $D/data/semechka.db ] || FIRST=1
( cd $R/$BASE && ./install.sh --name $NAME --prefix $D/app --data $D/data --port $PORT --host $IP )
if [ "$NAME" = roasted ] && [ $FIRST = 1 ]; then
  umask 077
  python3 - "$ENGINE" > $D/data/backend-hints.json <<'PY'
import json, sys, pathlib
tok = pathlib.Path.home().joinpath("semechka-deploy/secrets/sarness.token").read_text().strip()
print(json.dumps([{"id": "spark-engine", "adapter": "sarness", "label": "Sarness Engine (this machine)",
                   "base_url": sys.argv[1], "api_key": tok}]))
PY
  chmod 600 $D/data/backend-hints.json
fi
systemctl --user restart semechka-$NAME.service
for _ in $(seq 1 40); do curl -fsS http://$IP:$PORT/api/health >/dev/null 2>&1 && break; sleep 0.5; done
curl -fsS http://$IP:$PORT/api/health && echo
REMOTE
done
