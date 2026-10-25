#!/usr/bin/env bash
# Build a release tarball: wheel (with the web UI bundled) + install.sh + SHA256SUMS. Reproducible inputs: lockfiles.
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=$(python3 - <<'PY'
import re;print(re.search(r'__version__ = "(.+?)"', open("backend/semechka/__init__.py").read()).group(1))
PY
)
OUT="dist/semechka-$VERSION"
rm -rf dist && mkdir -p "$OUT"
( cd web && pnpm install --frozen-lockfile >/dev/null && pnpm build >/dev/null )
rm -rf backend/semechka/web && cp -r web/build backend/semechka/web
rm -rf backend/build backend/*.egg-info  # setuptools keeps old files here, and the wheel would carry them
PY=${PYTHON:-.venv/bin/python}
"$PY" -m pip install -q build >/dev/null
"$PY" -m build --wheel --outdir "$OUT" backend >/dev/null
cp scripts/install.sh "$OUT/install.sh"
( cd "$OUT" && sha256sum *.whl install.sh > SHA256SUMS )
tar -C dist -czf "dist/semechka-$VERSION.tar.gz" "semechka-$VERSION"
( cd dist && sha256sum "semechka-$VERSION.tar.gz" > "semechka-$VERSION.tar.gz.sha256" )
echo "Built dist/semechka-$VERSION.tar.gz"; cat "dist/semechka-$VERSION.tar.gz.sha256"
