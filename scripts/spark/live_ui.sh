#!/usr/bin/env bash
# Run the live UI specs (web/e2e-live) against the staging instance. The synthetic credentials are read over ssh into this
# process's environment only: never written to disk, never printed.
set -euo pipefail
cd "$(dirname "$0")/../../web"
SSHC="${SPARK_SSH_CONFIG:-$HOME/.ssh/config.semechka}"
STAGING_CREDS="$(ssh -F "$SSHC" spark 'cat ~/semechka-deploy/secrets/staging-credentials.txt' | python3 -c '
import json, sys
print(json.dumps(dict(l.rstrip("\n").split("\t", 1) for l in sys.stdin if "\t" in l and not l.startswith("#"))))')"
export STAGING_CREDS
export PATH="$HOME/.local/bin:$PATH"
exec npx playwright test -c playwright.live.config.ts "$@"
