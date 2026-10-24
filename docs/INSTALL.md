# Installing Semechka

Requirements: Linux (x86_64 verified; ARM64 untested), Python 3.11+, 2 GiB RAM for the control plane, and an inference server you run (llama.cpp, vLLM, Ollama or Sarness Engine). A GPU is needed by your inference engine, not by Semechka.

## 1. Verify, then run (recommended)
```
sha256sum -c semechka-0.2.0.tar.gz.sha256
tar xzf semechka-0.2.0.tar.gz && cd semechka-0.2.0
./install.sh --check      # preflight only: changes nothing
./install.sh              # installs a private venv + a systemd *user* service, loopback only
```
The installer prints the local URL and the location of the one-time setup token (`~/.local/share/semechka/setup-token`). Open the URL; the wizard creates the owner, connects your model backend, sets privacy defaults and runs a smoke test.
Checksums are SHA-256 over files you downloaded from the release page you trust; **there is no signature yet**. There is no hosted `curl | sh` endpoint.

## 2. Manual / development
```
python -m venv .venv && .venv/bin/pip install -e backend
cd web && pnpm install && pnpm build && cd ..
SEMECHKA_WEB_DIR=web/build .venv/bin/semechka serve
```
## 3. Docker Compose (written, not executed by the author)
`docker compose up -d` after `docker compose build`. The port is published on 127.0.0.1 only. A model server on the host is reachable at `http://host.docker.internal:PORT`.

## Day to day
`semechka doctor` (checks this machine) · `semechka status` · `semechka backup` (encrypted, passphrase) · `semechka restore FILE [--force]` · `semechka update` (prints the safe procedure; never silent) · `semechka uninstall [--purge-data]`.

## Network exposure
Default is loopback only. To let other devices connect: Admin → Settings → "Network exposure" → LAN, then restart the service. Put an HTTPS reverse proxy in front before any use beyond a network you control.

## Updating, rolling back, running more than one instance

Re-running `./install.sh` from a newer package updates in place. Before it changes anything it writes a snapshot
(database via the SQLite backup API, `master.key`, assets) to `<data>/pre-update/<time>-from-<version>/` and records the
program under `<prefix>/releases/<version>/`. Data is never replaced by an update.

```
./install.sh --rollback                  # program back to the previous recorded release
./install.sh --rollback --restore-data   # also restore the data snapshot taken before the last update
```

A program older than its database refuses to start (schema versioning), so rolling back across a schema change needs
`--restore-data`. Only installs made with 0.2.0 or later record releases; seed `<prefix>/releases/<ver>/` with the old
wheel to roll back to something older.

Several independent instances can share one machine. Give each its own `--name`, `--prefix`, `--data` and `--port`
(and `--host` to bind one LAN address instead of loopback); each gets its own systemd unit `semechka-<name>.service`,
database, master key and session-cookie name. See [DEPLOYMENT_SPARK.md](DEPLOYMENT_SPARK.md) for a worked example.
