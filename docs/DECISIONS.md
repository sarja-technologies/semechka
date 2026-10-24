# Decisions log

Dates are 2026. "Verified" means exercised on this machine, not merely compiled.

## D1 — Stack (24 Oct)
FastAPI + SQLAlchemy 2 (control plane), SvelteKit 3 / Svelte 5 static SPA served by the same process, SQLite by default. **PostgreSQL is
the production-target database in the brief but PostgreSQL and Docker are not installed on the authoring machine**, so every test ran on SQLite
(WAL). The SQLAlchemy models use portable types only and `SEMECHKA_DATABASE_URL` accepts a Postgres URL (`pip install semechka[postgres]`), but
**Postgres is unverified**. The settings cache and rate limiter are in-process, so run one worker process until they are moved to the database.
Single-process is also what makes the generation-slot counter exact.

## D2 — One process, no queue service (24 Oct)
No Redis/Celery. Chat generation streams straight through the control plane to the backend with per-person concurrency and daily quotas. There is
**no scheduler, priority lanes, queue position or placement logic**: the API says "sent straight to the backend" instead of inventing queue state.
Cluster mode and the node agent are not implemented (see KNOWN_LIMITATIONS).

## D3 — Sarness integration is audited, optional (24 Oct)
Audited the Sarness Engine 0.4.0 HTTP API v1 (`~/Documents/Sarness/docs/API.md`, `engine/sarness_engine/api/chat.py`). Semechka uses only
`/v1/health`, `/v1/system`, `/v1/agent/models`, `/v1/agent/completions`, `/v1/llm/{status,load,unload}`; it does **not** use Sarness' conversation store, so
Sarness persists no Semechka chat. SNP/1 (binary protocol, TLS 1.3, pinned identity) is **not** used; HTTP with a bearer token only. Models Sarness reaches through
its own remote endpoints are flagged external so the egress policy applies. Verified against a real Sarness Engine 0.4.0 started from its checkout.

## D4 — Privacy architecture (24 Oct)
Message bodies and conversation titles are AES-256-GCM encrypted in the database with HKDF-derived purpose keys from a 32-byte master key in
`<data>/master.key` (0600). This protects copied database files and backups without the key. It does **not** protect against a compromised running host
and is **not** end-to-end encryption; the inference backend reads plaintext. No-save conversations are held by the browser and never written to the chat
tables (usage counts without text are still recorded for quotas). User-held-key mode (E) and E2EE messaging (F) are **not implemented**.
Search over history decrypts in process (bounded) because the database cannot search ciphertext.

## D5 — Permissions (24 Oct)
One evaluator: allowed iff granted by some role/team/user rule and denied by none; `*` excludes sensitive permissions. Deny wins at every level (simple, deterministic,
explainable). Consequence: to give one employee something their team lacks, **don't** deny it on the team—just don't grant it. Content-access permissions (review flagged
content, legal hold, raw logs) were deliberately **removed** from this release because no feature implements them; shipping them would be fake switches.

## D6 — Licence (24 Oct)
Apache-2.0 (permits white-label use, includes a patent grant). Dependencies are permissive (MIT/BSD/Apache/OFL); see docs/SBOM.md. No trademark rights are granted.

## D7 — Design (24 Oct)
IBM Plex Sans/Mono self-hosted via @fontsource (OFL). Tokens and bans in DESIGN.md; `scripts/design-lint.sh` and `test_default_palette_is_not_purple` enforce the no-purple rule mechanically.
The external skills named in the brief (anti-slop, taste-skill, anti-slop-atlas) were **not installed or executed**; their principles are applied directly via DESIGN.md.

## D8 — Installer (24 Oct)
`install.sh` installs a verified wheel into a private venv and a systemd *user* unit; no sudo, no drivers, loopback only. Verification is SHA-256 (`SHA256SUMS`), **not a signature**:
there is no release signing key yet. The `curl | sh` URL in the specification is illustrative and does not exist; none is advertised.
