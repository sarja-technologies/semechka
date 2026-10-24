#!/usr/bin/env bash
# Lists installed Python and web dependencies with versions (inventory, not a signed SPDX/CycloneDX SBOM).
cd "$(dirname "$0")/.."
{ echo "# Dependency inventory"; echo; echo "Generated $(date -u +%F) by scripts/sbom.sh. Licences: check each package; this is an inventory, not a legal review."; echo; echo "## Python"; .venv/bin/pip list --format=freeze | sed 's/^/- /'; echo; echo "## Web"; (cd web && pnpm list --prod --depth 0 2>/dev/null | tail -n +2 | sed 's/^/- /'); } > docs/SBOM.md
