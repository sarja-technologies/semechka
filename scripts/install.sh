#!/usr/bin/env bash
# Semechka installer (Linux). Installs into a private virtualenv, prints the setup URL and one-time token location.
# Safe by default: checks first, changes nothing outside --prefix and ~/.config/systemd/user, never uses sudo,
# never installs drivers, never exposes the service beyond this machine.
#
# Recommended (verify, then run):
#   sha256sum -c semechka-X.Y.Z.tar.gz.sha256 && tar xzf semechka-X.Y.Z.tar.gz && cd semechka-X.Y.Z && ./install.sh
set -euo pipefail

PREFIX="${SEMECHKA_PREFIX:-$HOME/.local/share/semechka-app}"
DATA="${SEMECHKA_DATA_DIR:-$HOME/.local/share/semechka}"
PORT="${SEMECHKA_PORT:-8780}"
NAME=""; HOST="${SEMECHKA_HOST:-}"
DRY=0; NO_SERVICE=0; ROLLBACK=0; RESTORE_DATA=0
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() { echo "Usage: install.sh [--check] [--no-service] [--rollback [--restore-data]] [--name NAME] [--prefix DIR] [--data DIR] [--port N] [--host ADDR]"; echo "  --name NAME   run a second, independent instance (own unit semechka-NAME, own command, own data)"; echo "  --host ADDR   bind address, e.g. a LAN IP (default: follows the Network exposure setting, loopback at first)"; exit "${1:-0}"; }
while [ $# -gt 0 ]; do case "$1" in
  --check) DRY=1;; --no-service) NO_SERVICE=1;; --prefix) PREFIX="$2"; shift;; --data) DATA="$2"; shift;; --port) PORT="$2"; shift;; --name) NAME="$2"; shift;; --rollback) ROLLBACK=1;; --restore-data) RESTORE_DATA=1;; --host) HOST="$2"; shift;;
  -h|--help) usage 0;; *) echo "Unknown option: $1" >&2; usage 2;; esac; shift; done

ok() { printf '  [ ok ] %s\n' "$*"; }; warn() { printf '  [warn] %s\n' "$*"; }; bad() { printf '  [FAIL] %s\n' "$*"; FAIL=1; }
FAIL=0
case "$NAME" in ""|[a-z0-9-]*) ;; *) echo "--name must be lowercase letters, digits and dashes" >&2; exit 2;; esac
SUFFIX=""; [ -n "$NAME" ] && SUFFIX="-$NAME"
PROBE="${HOST:-127.0.0.1}"; [ "$PROBE" = "0.0.0.0" ] && PROBE=127.0.0.1
# ---- rollback: reinstall the previous recorded version; optionally restore the data snapshot taken before the update
if [ "$ROLLBACK" -eq 1 ]; then
  [ -x "$PREFIX/venv/bin/semechka" ] || { echo "Nothing installed at $PREFIX"; exit 1; }
  CUR=$("$PREFIX/venv/bin/semechka" --version | awk '{print $NF}')
  PREV=$(ls "$PREFIX/releases" 2>/dev/null | grep -v "^$CUR\$" | sort -V | tail -1 || true)
  [ -n "$PREV" ] || { echo "No earlier version is recorded in $PREFIX/releases"; exit 1; }
  echo "Rolling back $CUR -> $PREV"
  systemctl --user stop "semechka$SUFFIX.service" 2>/dev/null || true
  "$PREFIX/venv/bin/pip" install --quiet --force-reinstall "$PREFIX"/releases/"$PREV"/semechka-*.whl
  if [ "$RESTORE_DATA" -eq 1 ]; then
    SNAP=$(ls -d "$DATA"/pre-update/*-from-"$PREV" 2>/dev/null | sort | tail -1 || true)
    [ -n "$SNAP" ] || { echo "No data snapshot taken before an update from $PREV was found. Program rolled back; data untouched."; exit 1; }
    for f in semechka.db semechka.db-wal semechka.db-shm; do [ -e "$DATA/$f" ] && mv "$DATA/$f" "$DATA/$f.after-update"; done
    cp -p "$SNAP/semechka.db" "$DATA/semechka.db"; cp -p "$SNAP/master.key" "$DATA/master.key"
    [ -d "$SNAP/assets" ] && { rm -rf "$DATA/assets.after-update"; [ -d "$DATA/assets" ] && mv "$DATA/assets" "$DATA/assets.after-update"; cp -a "$SNAP/assets" "$DATA/assets"; }
    echo "  [ ok ] data restored from $SNAP (changes made since the update are kept as *.after-update)"
  fi
  systemctl --user start "semechka$SUFFIX.service" 2>/dev/null || true
  echo "  [ ok ] now running $("$PREFIX/venv/bin/semechka" --version)"
  exit 0
fi

echo "Semechka installer — preflight"

[ "$(uname -s)" = "Linux" ] && ok "Linux $(uname -m)" || bad "Linux is required (found $(uname -s))"
case "$(uname -m)" in x86_64|aarch64|arm64) ;; *) warn "Architecture $(uname -m) is untested";; esac
[ "$(id -u)" -ne 0 ] && ok "running as a normal user" || warn "running as root: a dedicated unprivileged user is recommended"

PYBIN=""
for c in python3.14 python3.13 python3.12 python3.11 python3; do
  if command -v "$c" >/dev/null && "$c" -c 'import sys; sys.exit(0 if sys.version_info>=(3,11) else 1)' 2>/dev/null; then PYBIN="$c"; break; fi
done
[ -n "$PYBIN" ] && ok "Python: $($PYBIN --version) ($PYBIN)" || bad "Python 3.11 or newer not found"
[ -n "$PYBIN" ] && ! "$PYBIN" -c 'import venv, ensurepip' 2>/dev/null && bad "python venv module missing (Debian/Ubuntu: apt install python3-venv)"

MEM_KB=$(awk '/MemTotal/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
[ "$MEM_KB" -ge 2000000 ] && ok "memory: $((MEM_KB/1024)) MiB" || warn "memory: $((MEM_KB/1024)) MiB (2 GiB or more recommended for the control plane alone)"
mkdir -p "$(dirname "$DATA")" 2>/dev/null || true
FREE_KB=$(df -Pk "$(dirname "$DATA")" 2>/dev/null | awk 'NR==2 {print $4}' || echo 0)
[ "${FREE_KB:-0}" -ge 2000000 ] && ok "disk free at $(dirname "$DATA"): $((FREE_KB/1048576)) GiB" || warn "less than 2 GiB free at $(dirname "$DATA")"

if command -v nvidia-smi >/dev/null && nvidia-smi -L >/dev/null 2>&1; then ok "NVIDIA: $(nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader | head -1)"
else warn "no NVIDIA GPU tooling found: Semechka itself does not need a GPU, but your inference engine will"; fi

portbusy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0 || return 1; }
portbusy "$PORT" && bad "port $PORT is already in use (use --port)" || ok "port $PORT is free"
for e in "8742 Sarness Engine" "11434 Ollama" "8080 llama.cpp server" "8000 vLLM"; do set -- $e; p=$1; shift
  portbusy "$p" && ok "found $* on 127.0.0.1:$p" || true; done
if [ -n "$HOST" ] && [ "$HOST" != "127.0.0.1" ]; then ok "network exposure: binding to $HOST (chosen with --host)"; else ok "network exposure: loopback only (change later in Settings → Network exposure)"; fi
warn "TLS: Semechka serves HTTP. Put a reverse proxy with HTTPS in front before exposing it beyond a trusted network."

WHEEL=$(ls "$HERE"/semechka-*.whl 2>/dev/null | head -1 || true)
[ -n "$WHEEL" ] && ok "package: $(basename "$WHEEL")" || bad "no semechka-*.whl next to install.sh"
if [ -f "$HERE/SHA256SUMS" ]; then
  ( cd "$HERE" && sha256sum --quiet -c SHA256SUMS 2>/dev/null ) && ok "checksums verified (SHA256SUMS)" || bad "checksum verification FAILED: do not install"
else warn "no SHA256SUMS found: the package cannot be verified"; fi

[ "$FAIL" -eq 0 ] || { echo; echo "Preflight failed. Nothing was changed."; exit 1; }
[ "$DRY" -eq 1 ] && { echo; echo "Preflight passed. (--check: nothing installed)"; exit 0; }

# ---- existing install: snapshot data before touching the program (consistent SQLite copy + key)
if [ -x "$PREFIX/venv/bin/semechka" ]; then
  OLDVER=$("$PREFIX/venv/bin/semechka" --version 2>/dev/null | awk '{print $NF}' || echo unknown)
  if [ -f "$DATA/semechka.db" ]; then
    SNAP="$DATA/pre-update/$(date +%Y%m%d-%H%M%S)-from-$OLDVER"; mkdir -p "$SNAP" && chmod 700 "$DATA/pre-update" "$SNAP"
    "$PYBIN" - "$DATA/semechka.db" "$SNAP/semechka.db" <<'PYSNAP'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1]); dst = sqlite3.connect(sys.argv[2]); src.backup(dst); dst.close(); src.close()
PYSNAP
    cp -p "$DATA/master.key" "$SNAP/master.key"
    [ -d "$DATA/assets" ] && cp -al "$DATA/assets" "$SNAP/assets" 2>/dev/null || true
    ok "existing install $OLDVER found; data snapshot kept in $SNAP"
  fi
fi

echo; echo "Installing to $PREFIX (data in $DATA)"
"$PYBIN" -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --quiet --upgrade pip
"$PREFIX/venv/bin/pip" install --quiet "$WHEEL"
"$PREFIX/venv/bin/pip" install --quiet --force-reinstall --no-deps "$WHEEL"   # same-version rebuilds must replace the files
mkdir -p "$DATA" && chmod 700 "$DATA"
mkdir -p "$HOME/.local/bin" && ln -sf "$PREFIX/venv/bin/semechka" "$HOME/.local/bin/semechka$SUFFIX"
NEWVER=$("$PREFIX/venv/bin/semechka" --version | awk '{print $NF}')
mkdir -p "$PREFIX/releases/$NEWVER" && cp "$WHEEL" "$PREFIX/releases/$NEWVER/"  # kept so --rollback can reinstall it
ok "installed $("$PREFIX/venv/bin/semechka" --version)"

if [ "$NO_SERVICE" -eq 0 ] && command -v systemctl >/dev/null && systemctl --user show-environment >/dev/null 2>&1; then
  UNIT="$HOME/.config/systemd/user/semechka$SUFFIX.service"; mkdir -p "$(dirname "$UNIT")"
  cat > "$UNIT" <<UNIT
[Unit]
Description=Semechka${NAME:+ ($NAME)}
After=network.target

[Service]
Environment=SEMECHKA_DATA_DIR=$DATA
Environment=SEMECHKA_PORT=$PORT
${HOST:+Environment=SEMECHKA_HOST=$HOST
}ExecStart=$PREFIX/venv/bin/semechka serve
Restart=on-failure
NoNewPrivileges=true
UMask=0077
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$DATA
ProtectHome=read-only

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload && systemctl --user enable semechka$SUFFIX.service && systemctl --user restart semechka$SUFFIX.service
  ok "service enabled (systemctl --user status semechka$SUFFIX)"
  for _ in $(seq 1 30); do curl -fsS "http://$PROBE:$PORT/api/health" >/dev/null 2>&1 && break; sleep 0.5; done
else
  warn "no systemd user session: start it with  SEMECHKA_DATA_DIR=$DATA semechka serve --port $PORT"
fi

echo
if curl -fsS "http://$PROBE:$PORT/api/health" >/dev/null 2>&1; then
  echo "Semechka is running."
  echo "  Open:         http://$PROBE:$PORT"
  echo "  Setup token:  $(cat "$DATA/setup-token" 2>/dev/null || echo '(not created yet)')   (one time; file: $DATA/setup-token)"
  echo "  Next:         the browser wizard creates the owner account, connects a model backend and sets privacy defaults."
else
  echo "Installed, but the service is not answering on port $PORT yet. Run: semechka doctor"
fi
