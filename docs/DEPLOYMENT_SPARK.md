# Deployment on an NVIDIA DGX Spark: Roasted and Salted

This documents the reference deployment that this repository was verified on. It contains no credentials. Addresses
are the LAN addresses of that machine; replace them for yours.

| Item | Value |
| --- | --- |
| Machine | NVIDIA DGX Spark ("edgexpert-7443"), NVIDIA GB10, 20 Arm cores, 124 GB unified memory |
| OS | Ubuntu 24.04.5 LTS, kernel 7.0.0-1019-nvidia, `aarch64` |
| Python | 3.12.3 (system) |
| Service manager | systemd **user** units with lingering enabled (no root needed; starts at boot) |
| Roasted | `http://192.168.20.193:8787`, the **disposable** fresh-install sandbox; reset to the first-run wizard on demand |
| Salted | `http://192.168.20.193:8788`, the **permanent** configured instance; never reset |
| Sarness Engine (existing) | untouched; `sarness-engine.service`, 8742 (HTTP) and 8744 (SNP) |
| Sarness Engine (for Semechka) | `semechka-sarness.service`, `127.0.0.1:8752`, own data dir, own token |

## Why a second Sarness engine

Sarness Engine stores one bearer token, kept only as a hash, and `token --rotate` replaces it. The pre-existing engine's
token is held by whatever clients already use it, so rotating it would break them. Instead of touching it,
`semechka-sarness.service` runs the **same installed program** against its own data directory with its own token,
bound to loopback only (nothing else on the network can reach it), SNP disabled. Model weights are shared by a
symlink to the existing models directory (read-only by convention; Semechka never calls the delete endpoint).
Both engines can use the GPU; load only one large model at a time.

## Layout (all under the service user's home)

```
~/semechka-deploy/
  sarness/            dedicated engine data dir (config.json 0600, logs, outputs; models -> existing models)
  roasted/app         program files (venv, recorded releases)       roasted/data   database, master.key, pre-update/
  salted/app          program files                                 salted/data    database, master.key, pre-update/
  secrets/            sarness.token, salted-credentials.txt          (0600)
  releases/           uploaded tarballs and their SHA-256 files
  backups/            independent copies taken before a release (e.g. pre-0.3.0-…, with SHA256SUMS)
  logs/sarness.log
~/.config/systemd/user/semechka-{roasted,salted,sarness}.service
```

Instances share nothing except the inference engine: separate databases, master keys (so separate encryption of
messages, titles, secrets and assets), settings, accounts, session-cookie names (derived from each instance's key, because
browsers share cookies across ports on one address), uploads/assets, audit chains and backups.

## Roasted: the disposable fresh-install sandbox

**Convention:** Roasted exists to experience a brand-new installation, as often as wanted. Salted is the permanent,
configured instance. A reset of Roasted never touches Salted.

Roasted's data directory carries the owner-only marker `.semechka-disposable.json`, written once with
`semechka reset --instance roasted --designate`. Salted has no marker, and the reset command refuses any instance
without one. To return Roasted to a factory-fresh first run (the installed release stays as it is):

```
ssh <spark>
semechka reset --instance roasted            # shows what will be erased, then asks you to type "roasted"
cat ~/semechka-deploy/roasted/data/setup-token
```

then open `http://192.168.20.193:8787`. What the command does:
1. It stops `semechka-roasted.service`. It refuses if any other process has files open in Roasted's data directory,
   or if that directory overlaps another instance's.
2. It moves everything in `roasted/data` except the marker into
   `roasted/data.reset-backups/<time>-before-reset/` (0700). The database's integrity is checked, every file is
   listed in `SHA256SUMS` and read back, and a `manifest.json` records what was there. Nothing is deleted.
3. It writes the engine hint (the key is read at that moment from `secrets/sarness.token`) and starts the service.
   The unchanged 0.3.0 program then creates a new database, a new master key, so old session cookies are dead, and
   a new one-time setup token.
4. It checks: the wizard is open, the version is unchanged, there are no accounts, conversations or sessions, the
   token file is owner-only, the master key is new, and the wizard's engine step reaches the engine.

Undo a reset with the `--restore` command it prints. `semechka reset --list` shows which instances may be reset.
Backups accumulate in `roasted/data.reset-backups/` and contain the old master key; delete old ones by hand when no
longer needed. Nothing resets Roasted automatically: not a reboot, not a service restart, not an update.

On this host, `semechka` is the standalone reset tool (`~/semechka-deploy/bin/semechka_reset.py`, from
`backend/semechka/reset.py`), because the installed 0.3.0 program predates the subcommand. For everything else
each instance has its own command, `semechka-roasted` and `semechka-salted`. In later releases the same code is
also `semechka reset` inside each instance's program.

How a fresh install is claimed: on first start the server writes a one-time owner setup token to
`<data>/setup-token` (mode 0600) and refuses setup calls without it. Read it on the machine:

```
ssh <spark> cat ~/semechka-deploy/<instance>/data/setup-token
```

then open the instance's address and paste it into the first wizard step. The token is deleted when the wizard
completes. A restart before completion issues a new token.

To let the wizard find the engine (hardware step, model step) without asking you to copy a secret, the installer wrote
`<data>/backend-hints.json` (0600). The server uses it to probe the engine and to connect with the key; the key
is **never sent to the browser**. The file is ignored if its permissions are looser than 0600, and it is deleted when
the wizard completes (the key is then held only encrypted in the database). No models are enabled or exposed until
you choose them.

## Salted: the configured test environment

`scripts/provision_salted.py` drives the product's own HTTP API (no database writes): it completes the wizard, then
creates the roles, teams, accounts, per-user overrides, model aliases/grants and settings. Salted offers (as provisioned with 0.2.0 and
upgraded to 0.3.0):

- accounts at the reserved domain `salted.test`: owner, platform admin, model operator, team manager, analyst,
  member, support agent, technical reader (per-user override granting `tech.*`), restricted user (override denying
  technical fields, attachments and export), guest (low quotas, 7-day retention);
- teams Research and Support (Support denied the Large model and export);
- Knowledge (keyword search), assistants, the code workspace, image and video generation (Sketch: sdxl-turbo, Clip: wan2.2-ti2v-5b) and the generation queue, all enabled and working; audio is not available (the engine has no speech model) and `z-image-turbo` is not installed;
- model aliases Quick (thinking off), Balanced, Large and Coder over the engine's installed models, each with grants;
- registration by invitation only, signed-in sessions with an idle timeout, representative quotas, role-scoped
  retention, branding (the Dark theme; it was Carbon under 0.2.0, which the 0.3.0 migration maps to Dark), notice texts;
- every mode that is actually implemented and has a backend model.

Passwords are generated at provisioning time and stored only in `~/secrets/salted-credentials.txt` (0600) on the
Spark, never in Git and never printed. To read an account's password:

```
ssh <spark> "grep '^member@salted.test' ~/semechka-deploy/secrets/salted-credentials.txt"
```

These are different from the SSH password.

## Operating

```
systemctl --user status semechka-roasted semechka-salted semechka-sarness
journalctl --user -u semechka-salted -n 100       # logs carry no secrets (see docs/SECURITY.md)
~/semechka-deploy/roasted/app/venv/bin/semechka doctor   # run with SEMECHKA_DATA_DIR set to that instance's data dir
```

Update one instance without touching the other (`scripts/deploy_spark.sh` uploads a verified tarball and runs the
installer, which snapshots that instance's database and key first, installs, restarts only that instance, and
records the release):

```
SPARK_SSH="ssh -F ~/.ssh/config.semechka spark" SPARK_SCP="scp -F ~/.ssh/config.semechka" SPARK_HOST_IP=192.168.20.193 \
  ./scripts/deploy_spark.sh dist/semechka-X.Y.Z.tar.gz salted
```

Roll an instance back (program only, or program plus the pre-update data snapshot):

```
~/semechka-deploy/salted/app/../releases/…/install.sh --name salted --prefix ~/semechka-deploy/salted/app --data ~/semechka-deploy/salted/data --rollback [--restore-data]
```

A program older than its database refuses to start (schema versioning), so a rollback after a schema change
needs `--restore-data`. After a program-only rollback that refuses to start, run the same command with
`--restore-data` alone (no `--rollback`).

## Network exposure

The two Semechka services bind the LAN address only (not `0.0.0.0`). The Spark's host firewall (`ufw`) is
**inactive** and `iptables` has no filtering rules; enabling a default-deny firewall on a shared workstation can lock
out other services (SSH, the existing Sarness Engine), so it was not changed. Restrict 8787/8788 to the hosts that
need them if the LAN is not trusted. Semechka serves plain HTTP: do not expose it beyond a trusted network without an
HTTPS reverse proxy.

## Verification

`scripts/verify_deployment.py` (run from another machine on the LAN) checks the address, the services, the engine,
the release each instance runs, Roasted's setup state, a real streamed chat on Salted with proof from the engine and `nvidia-smi` that a model
process on the Spark's GPU produced it, cross-instance isolation, credential protection, security headers, and (with
`--restart`) persistence across service restarts. Its output for the last run is in
`docs/DEPLOYMENT_EVIDENCE.md`.
