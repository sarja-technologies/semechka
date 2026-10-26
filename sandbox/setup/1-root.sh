#!/usr/bin/env bash
# Stage 1 (runs as root): everything needed so that an UNPRIVILEGED account can run rootless Docker.
# Additive only: no existing service, user, group or file is modified, except installing packages.
set -euo pipefail
log() { echo "[stage1] $*"; }
export DEBIAN_FRONTEND=noninteractive
log "packages"
apt-get update -qq
apt-get install -y --no-install-recommends uidmap dbus-user-session slirp4netns docker-ce-rootless-extras acl >/dev/null
log "group + account (locked password, no sudo, no ssh)"
getent group semsbx-api >/dev/null || groupadd --system semsbx-api
if ! id semsbx >/dev/null 2>&1; then
  useradd --create-home --home-dir /home/semsbx --shell /bin/bash --gid semsbx-api \
          --comment "Semechka sandbox (rootless Docker, no privileges)" semsbx
fi
passwd -l semsbx >/dev/null
chmod 0750 /home/semsbx
grep -q '^semsbx:' /etc/subuid || usermod --add-subuids 300000-365535 --add-subgids 300000-365535 semsbx
echo "subuid: $(grep '^semsbx:' /etc/subuid)  subgid: $(grep '^semsbx:' /etc/subgid)"
loginctl enable-linger semsbx
log "apparmor: allow rootlesskit to create the user namespace (Ubuntu 24.04 restricts unprivileged userns)"
cat > /etc/apparmor.d/usr.bin.rootlesskit <<'AA'
abi <abi/4.0>,
include <tunables/global>

/usr/bin/rootlesskit flags=(unconfined) {
  userns,

  include if exists <local/usr.bin.rootlesskit>
}
AA
apparmor_parser -r /etc/apparmor.d/usr.bin.rootlesskit
log "resource ceiling for everything the sandbox account runs (shared machine: LLM engines live here too)"
UID_SBX=$(id -u semsbx)
systemctl set-property --runtime "user-${UID_SBX}.slice" MemoryHigh=20G MemoryMax=24G MemorySwapMax=0 TasksMax=3000 CPUQuota=800% IOWeight=50
mkdir -p /etc/systemd/system/user-${UID_SBX}.slice.d
cat > /etc/systemd/system/user-${UID_SBX}.slice.d/10-semechka-sandbox.conf <<CONF
[Slice]
MemoryHigh=20G
MemoryMax=24G
MemorySwapMax=0
TasksMax=3000
CPUQuota=800%
IOWeight=50
CONF
systemctl daemon-reload
log "done: uid=${UID_SBX}"
id semsbx
