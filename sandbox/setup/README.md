# Sandbox setup (one-time, privileged, reviewed)

Run on the host that will execute agent code. Nothing here is run by Semechka itself.

1. `sudo bash 1-root.sh` - installs `uidmap`, `slirp4netns`, `docker-ce-rootless-extras`, `acl`, `dbus-user-session`; creates the
   locked, sudo-less account `semsbx` (own subuid range); an AppArmor profile that lets `rootlesskit` create its user
   namespace; and a hard resource ceiling for that account's slice (24 GiB memory, 3000 tasks, 800% CPU, no swap).
2. `sudo -u semsbx -H bash 2-sbxuser.sh` - rootless Docker with its socket at `/home/semsbx/run/docker.sock`, **no bridge
   network**, and an ACL granting that one socket to the Semechka service user (edit `fylume` for your user).
3. Build the image (from this directory's parent): `DOCKER_HOST=unix:///home/semsbx/run/docker.sock docker build --network host -t semechka-sandbox:1 sandbox/`
4. `python3 scripts/sandbox_probe.py backend/semechka` runs 18 hostile jobs against it and prints PASS/FAIL with evidence.

Rollback: `systemctl --user -M semsbx@ disable --now docker`, `userdel -r semsbx`, `groupdel semsbx-api`,
`rm /etc/apparmor.d/usr.bin.rootlesskit /etc/systemd/system/user-<uid>.slice.d/10-semechka-sandbox.conf`, `apparmor_parser -R`,
`apt-get remove docker-ce-rootless-extras slirp4netns`. The rootful Docker daemon and the `docker` group are never touched.
