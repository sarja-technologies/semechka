#!/usr/bin/env bash
# Operator script: build / inspect the Work Mode (Goose) image `semechka-goose:1` on the host that runs the sandbox runtime.
# The Goose binary is the upstream release (`goose --version` must print the expected version); it is copied into a throw-away
# build context, never modified. Run ON the Spark as the user that owns the relay socket:
#   GOOSE_BIN=~/.local/bin/goose scripts/spark/goose_image.sh build|info|smoke
set -euo pipefail
cd "$(dirname "$0")/../.."
TAG="${GOOSE_IMAGE:-semechka-goose:1}"
export DOCKER_HOST="${DOCKER_HOST:-unix:///run/user/$(id -u)/semechka-sandbox.sock}"
BIN="${GOOSE_BIN:-$HOME/.local/bin/goose}"
case "${1:-}" in
  build)
    CTX="$(mktemp -d)"; trap 'rm -rf "$CTX"' EXIT
    cp -r work-image/goose/. "$CTX/"; cp "$BIN" "$CTX/goose"
    echo "goose: $("$CTX/goose" --version)  sha256: $(sha256sum "$CTX/goose" | cut -c1-16)"
    docker build --network host -t "$TAG" "$CTX" ;;
  info)  docker image inspect "$TAG" --format 'id={{.Id}} size={{.Size}} arch={{.Architecture}}' ;;
  smoke) docker run --rm --pull never --network none --read-only --tmpfs /tmp --user 65534:65534 --cap-drop ALL \
           --entrypoint /usr/local/bin/goose "$TAG" --version ;;
  *) echo "usage: $0 build|info|smoke" >&2; exit 2 ;;
esac
