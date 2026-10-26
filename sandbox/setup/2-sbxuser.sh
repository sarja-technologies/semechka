#!/usr/bin/env bash
# Stage 2 (run AS the unprivileged account `semsbx`): install and start rootless Docker with a dedicated socket.
#   sudo -u semsbx -H bash 2-sbxuser.sh
# The daemon has no bridge network ("bridge": "none"): the only network a container can ever have is none.
set -euo pipefail
export XDG_RUNTIME_DIR=/run/user/$(id -u)
export DBUS_SESSION_BUS_ADDRESS=unix:path=$XDG_RUNTIME_DIR/bus
mkdir -p ~/run ~/.config/docker
chmod 0750 ~/run
cat > ~/.config/docker/daemon.json <<'JSON'
{
  "hosts": ["unix:///home/semsbx/run/docker.sock"],
  "bridge": "none",
  "iptables": false,
  "ip6tables": false,
  "no-new-privileges": true,
  "default-ulimits": {"nofile": {"Name": "nofile", "Soft": 1024, "Hard": 2048}, "core": {"Name": "core", "Soft": 0, "Hard": 0}},
  "log-driver": "local",
  "log-opts": {"max-size": "5m", "max-file": "2"}
}
JSON
dockerd-rootless-setuptool.sh install --skip-iptables 2>&1 | tail -3 || true   # first start may fail until the config above is in place
mkdir -p ~/.config/systemd/user/docker.service.d
cat > ~/.config/systemd/user/docker.service.d/10-socket-acl.conf <<'CONF'
[Service]
# The socket is for the Semechka service user only: grant exactly that user (not a group), and re-grant at every start.
ExecStartPost=/bin/sh -c 'for i in 1 2 3 4 5 6 7 8 9 10; do [ -S /home/semsbx/run/docker.sock ] && break; sleep 1; done; setfacl -m u:fylume:x /home/semsbx /home/semsbx/run; setfacl -m u:fylume:rw /home/semsbx/run/docker.sock'
CONF
systemctl --user daemon-reload
systemctl --user enable docker.service >/dev/null 2>&1
systemctl --user reset-failed docker.service || true
systemctl --user restart docker.service
for i in $(seq 1 40); do [ -S /home/semsbx/run/docker.sock ] && DOCKER_HOST=unix:///home/semsbx/run/docker.sock docker version >/dev/null 2>&1 && break; sleep 1; done
DOCKER_HOST=unix:///home/semsbx/run/docker.sock docker info 2>&1 | grep -E "Server Version|Cgroup (Driver|Version)|rootless|Security Options" | head -6
