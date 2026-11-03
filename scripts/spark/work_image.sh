#!/usr/bin/env bash
# Operator script: build, inspect and smoke-test the Work Mode image `semechka-work:1` on the host that runs the sandbox
# runtime (the rootless Docker daemon of docs/sandbox/setup). Contains no secrets and never touches other images or containers.
#
#   scripts/spark/work_image.sh build    # docker build --network host -t semechka-work:1 work-image/
#   scripts/spark/work_image.sh info     # size, id, layers summary
#   scripts/spark/work_image.sh smoke    # run the image with the production flags and exercise every tool it ships
#
# On the host itself:   DOCKER_HOST=unix:///run/user/1000/semechka-sandbox.sock scripts/spark/work_image.sh build
# From another machine: SPARK_SSH="ssh -F ~/.ssh/config.semechka spark" scripts/spark/work_image.sh build
#                       (the build context is streamed over ssh; nothing is left behind on the host but the image)
set -euo pipefail
cd "$(dirname "$0")/../.."
TAG="${WORK_IMAGE:-semechka-work:1}"
HOSTSOCK="${DOCKER_SOCK:-unix:///run/user/1000/semechka-sandbox.sock}"
if [ -n "${SPARK_SSH:-}" ]; then
  D() { $SPARK_SSH "DOCKER_HOST=$HOSTSOCK /usr/bin/docker $(printf '%q ' "$@")"; }
  CTX() { tar -C work-image -c Dockerfile | $SPARK_SSH "DOCKER_HOST=$HOSTSOCK /usr/bin/docker build --network host -t $TAG -"; }
else
  export DOCKER_HOST="${DOCKER_HOST:-$HOSTSOCK}"
  D() { docker "$@"; }
  CTX() { docker build --network host -t "$TAG" work-image/; }
fi
case "${1:-}" in
  build) CTX ;;
  info)  D image inspect "$TAG" --format 'id={{.Id}} size={{.Size}} created={{.Created}} arch={{.Architecture}}' ;;
  smoke)
    N="work-smoke-$$"
    # production flags, minus the named volume (a throw-away tmpfs stands in for /work)
    D run -d --name "$N" --pull never --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m \
      --tmpfs /work:rw,nosuid,size=256m,uid=65534,gid=65534,mode=0755 --user 65534:65534 --cap-drop ALL \
      --security-opt no-new-privileges --pids-limit 128 --memory 1g --memory-swap 1g --cpus 1 --init --log-driver none \
      --label semechka.work.smoke=1 -e HOME=/work/.home "$TAG" >/dev/null
    trap 'D rm -f "$N" >/dev/null 2>&1 || true' EXIT
    D exec "$N" bash -c '
      set -e; mkdir -p "$HOME"; cd /work
      echo "uid=$(id -u) gid=$(id -g) home=$HOME"
      python3 --version; node --version; npm --version; git --version | head -1; ffmpeg -version | head -1
      rg --version | head -1; jq --version; unzip -v | head -1; convert -version | head -1; gcc --version | head -1
      python3 -c "import numpy,pandas,PIL,matplotlib,requests,pytest,yaml,openpyxl,docx,cv2; print(\"py libs ok: numpy\",numpy.__version__,\"pandas\",pandas.__version__,\"PIL\",PIL.__version__,\"cv2\",cv2.__version__)"
      git init -q t && cd t && echo hi > a.txt && git add a.txt && git commit -qm first && git log --oneline | head -1 && cd ..
      ffmpeg -loglevel error -f lavfi -i testsrc=duration=1:size=64x64:rate=5 t.mp4 && ls -l t.mp4
      python3 -c "from PIL import Image; Image.new(\"RGB\",(8,8),\"red\").save(\"t.png\"); print(\"PIL write ok\")"
      if (exec 3<>/dev/tcp/1.1.1.1/53) 2>/dev/null; then echo "NETWORK REACHABLE (unexpected)"; exit 1; else echo "no network (expected)"; fi
      touch /usr/x 2>/dev/null && { echo "ROOT FS WRITABLE (unexpected)"; exit 1; } || echo "root filesystem read-only (expected)"
      ls /var/run/docker.sock 2>/dev/null && { echo "docker.sock present (unexpected)"; exit 1; } || echo "no docker.sock (expected)"
    ' ;;
  *) sed -n 2,12p "$0"; exit 2 ;;
esac
