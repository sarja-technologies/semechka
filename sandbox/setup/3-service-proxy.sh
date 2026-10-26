#!/usr/bin/env bash
# Step 3 (as the account that runs Semechka, no sudo): a private socket the hardened Semechka service CAN open.
#
# Why: the Semechka service unit uses mount-namespace hardening (PrivateTmp, ProtectSystem, ProtectHome). systemd then runs it
# in an unprivileged user namespace in which the sandbox account's uid/gid are unmapped, and the kernel refuses write access
# (connect) to a socket owned by an unmapped id, whatever its ACL says. A shell works; the service does not.
# The fix keeps the hardening: a small relay (relay.py), run as this account, listens on a mode-0600 socket and forwards to
# the runtime's socket. It is reachable by exactly the principal that already holds the ACL on the real socket, and by nothing
# else. Never mount or hand this path to a container.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SBX_SOCK="${SBX_SOCK:-/home/semsbx/run/docker.sock}"
DEST="$HOME/.local/share/semechka-sandbox-relay"
U="$HOME/.config/systemd/user"
mkdir -p "$DEST" "$U"
install -m 0644 "$HERE/relay.py" "$DEST/relay.py"
# an earlier attempt used systemd-socket-proxyd, which drops output after a half-close: remove its units if present
systemctl --user disable --now semechka-sandbox-proxy.socket semechka-sandbox-proxy.service 2>/dev/null || true
rm -f "$U/semechka-sandbox-proxy.socket" "$U/semechka-sandbox-proxy.service"
cat > "$U/semechka-sandbox-relay.service" <<UNIT
[Unit]
Description=Private relay to the Semechka sandbox runtime socket

[Service]
ExecStart=/usr/bin/python3 $DEST/relay.py %t/semechka-sandbox.sock $SBX_SOCK
Restart=on-failure
# no namespace options here on purpose: this unit must be able to open the runtime's socket

[Install]
WantedBy=default.target
UNIT
systemctl --user daemon-reload
systemctl --user enable --now semechka-sandbox-relay.service
echo "Now set the setting sandbox.docker_host to  unix:///run/user/$(id -u)/semechka-sandbox.sock"
