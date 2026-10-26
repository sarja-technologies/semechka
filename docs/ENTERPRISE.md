# Semechka Cloud Workspaces — architecture decisions and implementation ledger

This file is the durable record for the enterprise-cloud project (branch `feature/0.4.1-enterprise-cloud`).
**Status values are evidence-based:** a row is `done` only when code, tests and (where stated) deployment exist.
Planned work is never written as done.

## Ground rules (from the owner)

- CMSIT AI (the instance on port 8787, data in `~/semechka-deploy/roasted/data`) is **production**. Never run
  `semechka reset` on it. Salted (8788) is not touched.
- Every schema change: disposable-copy rehearsal → backup → deploy → verify. `semechka migrate --to N` is the rollback.
- `code`, `knowledge` and `agents` modes stay administratively switchable and are **not** globally enabled until the
  backends and controls they need exist.
- No fabricated progress, tool actions, documents or test results.

## Decisions

### D1. One database, one process, tenant column on every tenant-scoped row
SQLite today (Postgres optional), so no row-level security is available. Isolation is enforced by (a) one
authorisation module (`authz.py`) that every tenant-scoped read/write goes through, (b) a per-organisation content key,
(c) tests that attack the API with another tenant's identifiers.

### D2. Per-organisation content keys
Content for org `X` is encrypted with the vault purpose `org:<X>:content` (and `org:<X>:files` for binary
payloads, bound to the file-version id). A row fetched through a wrong org id decrypts to an authentication failure,
not to another tenant's plaintext. The master key stays in `master.key`; per-org keys are HKDF-derived, so org
deletion = delete rows + files (crypto-shredding by key is not claimed: the master key is shared).

### D3. Authorisation model
Platform → Organisation → Workspace (nestable) → Folder → File; Contexts, Agents and Jobs belong to a workspace.

* **Tenant boundary first.** A user can touch a resource only if they hold an *active membership* of its organisation.
  Platform roles (`owner`, `platform_admin`) do **not** confer membership.
* **Permissions** are namespaced (`org.*`, `ws.*`, `file.*`, `ctx.*`, `agent.*`, `job.*`, `mem.*`, `net.*`).
  Content permissions (`file.read`, `ctx.read`, `mem.read`, …) are **sensitive**: no wildcard grant covers them. They
  must come from a workspace role by name or an explicit ACL entry. An organisation administrator therefore manages
  people, groups, roles, quotas and policy but cannot read confidential content merely by being an administrator.
* **Sources of allow:** workspace role assignment (to a user or a group, inherited from a parent workspace when
  `inherit_members`), explicit ACL entries on any node of the resource chain.
* **Deny wins**, wherever it appears: ACL deny on any ancestor, org-role deny, workspace policy ceiling.
* **Default deny for protected resources.** A folder/file/context marked `restricted` stops inheritance; only explicit
  ACL entries (and workspace admins, who hold the content permissions by name) reach it.
* **Private workspaces:** only explicit members. Organisation admins cannot add themselves to a private workspace
  (they can add anyone else to shared ones); every membership change is written to the audit chain with org id.
* **Agents:** effective authority = invoking user's authority ∩ agent policy ∩ workspace policy ceiling, re-evaluated
  at every tool call and on job resume, using the user's *current* membership (a revoked user's queued job fails).

### D4. Existing installs
Migration 8 creates a **Default organisation** and makes every existing active user a member (platform owners →
organisation owner, holders of `admin.users.manage` → organisation admin, others → member), and copies teams to
groups. Existing conversations stay personal (not org-scoped), so nothing becomes visible across organisations.
New users created in a single-tenant deployment join the default organisation.

### D5. Retrieval (milestone 3)
Keep the encrypted-at-rest, in-memory FTS5 design; add embeddings (a real local embedding model from the engine, or
declared unavailable) stored encrypted per org; permission filtering happens in SQL *before* ranking and again before
prompt assembly; index entries carry `org_id/workspace_id/context_id/file_version_id` and a context `version`.

### D6. Execution (milestone 5)
Agents are rows, not containers. A job row (durable, DB-backed queue) is claimed by a worker, which starts an
ephemeral rootless container per task. Blocked today: the `fylume` user has no container runtime access
(see ledger "Blockers").

## Ledger

| # | Item | Status | Evidence |
|---|------|--------|----------|
| M1 | 0.4.1 image/video repair committed | done | commit f752efc; 299 tests; live on CMSIT AI (schema 7) |
| M1 | Disabled media models audited | done | below |
| M2 | Tenancy tables + migration 8 | done, **deployed to CMSIT AI 2026-10-26 (0.4.2, schema 8)** | `models_tenancy.py`, `migrations._up_8/_down_8`; tests `test_migration_7`, suite 326 passed |
| M2 | `authz` engine + tests | done | `authz.py`; `tests/test_authz.py` 21 tests (tenant boundary, deny-wins, sensitive wildcards, restricted items, foreign ids, agent intersection) |
| M2 | Org/group/workspace/member/ACL API | done (API only; UI is M7) | `routers/orgs.py`; `tests/test_orgs_http.py` Scenarios A and B at workspace level (file level arrives in M3) |
| M3a | Workspace filesystem, versions, trash, quotas, encrypted per-org store | done, **deployed 0.4.3 / schema 9** | `files_svc.py`, `routers/files.py`; `tests/test_files_contexts.py` (19), `tests/test_files_http.py` (4) |
| M3b | Durable jobs + ingestion (txt/md/code/csv/json/pdf/docx/xlsx/pptx; images/video/archives reported unsupported) | done, **deployed 0.4.3** | `jobs_svc.py`, `ingest_svc.py`, `extract_svc.py`; real generated Office/PDF files in tests; zip-bomb and path-traversal archives refused |
| M3c | Named contexts, versions, attach, export/import, permission-filtered **keyword (BM25)** retrieval with citations | done, **deployed 0.4.3** | `contexts_svc.py`; cache key = exact permitted (file, version) set |
| M3d | Semantic retrieval (embeddings) + hybrid ranking (BM25 + cosine, reciprocal-rank fusion) | done, **deployed 0.4.4, enabled on CMSIT AI** | `embed_svc.py`; Qwen3-Embedding-0.6B Q8 (Apache-2.0, sha256 verified) served by `semechka-embed.service` on 127.0.0.1:8760; real-model rehearsal on the Spark: 4/4 queries with no shared keywords, junk query returns nothing; `tests/test_semantic.py` (7) with a deterministic fake embedder |
| M4 | Agent framework: configs + versions, durable runs/steps, authority = user ∩ agent ∩ workspace, approvals, resume, memory; tools: search_context, list_files, read_file, read_table, table_stats, calc, create_document (docx), create_spreadsheet (xlsx), create_presentation (pptx), write_text_file, remember, recall | done in code + tests (13 agent tests, scripted model); **off by default** (`cloud.agents.enabled`) | `agent_svc.py`, `docgen_svc.py`, `routers/agents.py`, `models_agents.py`, migration 10; mutation-checked authority tests; **not yet exercised against a real language model** (planned with the sandbox in M5/M8) |
| M5 | Durable jobs (done in M3) + isolated container executor + `python`/`shell`/`fetch_url` agent tools | sandbox **installed on the Spark and probed: 18/18 hostile checks pass**; tools + 8 tests (local stand-in for the container); **sandbox switch OFF on CMSIT AI** | `sandbox_svc.py`, `sandbox/` (image, runner, setup scripts), `scripts/sandbox_probe.py`; privileged setup performed 2026-10-26 (below) |
| M6 | Model gateway (org + workspace allow-lists), quotas, usage records, platform diagnostics, memory-aware sandbox admission, agent memory | done in code + 5 tests; agent memory done in M4 | `gateway_svc.py`, `usage_svc.py`, `routers/cloud_admin.py`; scheduling reuses the shared generation scheduler (agents rank 400) and the durable job queue (per-kind concurrency, priority) |
| M7 | Workspace UI + admin UI | planned | |
| M8 | Security/load/recovery/e2e scenarios A–I | planned | |

## Media model audit (CMSIT AI, 2026-10-26)

All 13 media models are registered `available=1`; none is broken in the registry. Enabled: flux2-klein-4b,
sdxl-turbo, hunyuanvideo-1.5-i2v, ltx-2.3, wan2.2-ti2v-5b. The rest were left disabled by an administrator
(the audit log shows deliberate per-model enable/disable and Image/Video groups restricted to owner+member).
Reasons that plausibly apply (engine-reported, not verified as the admin's intent): memory (estimates 32–72 GB on a
121 GB unified-memory machine shared with the LLM engine), duration (wan2.2 14B: ~40 min per 5 s clip at 480p), and
licence (Qwen-Image 2.1 is non-commercial research only).
Direct engine test, not user-visible, Semechka `enabled` untouched: **z-image-turbo** completed in 60 s, peak 32 GB
(worker released afterwards). Not tested (would crowd the other engine's resident LLM): qwen-image-2512/edit-2511,
hunyuanvideo-1.5-t2v, minimax-h3, wan2.2-t2v/i2v-a14b.

## Execution sandbox: findings and chosen design (2026-10-26)

Probed on the Spark (read-only): Docker 29.6.2 is installed and **rootful**; `fylume` is not in the `docker` group and
`sudo -n` needs a password; no podman, no `uidmap`/`rootlesskit`/`slirp4netns`. Unprivileged user namespaces are
restricted by AppArmor (`apparmor_restrict_unprivileged_userns=1`): `bwrap` starts but cannot set up a network
namespace (`loopback: RTM_NEWADDR: Operation not permitted`), so a no-root namespace sandbox with network isolation
is **not** possible. User-level cgroup limits do work (`cpu memory pids` delegated; `systemd-run --user --scope`).
`/home/dembele/Documents/logins/homelabs/dgxspark_creds.txt` exists (3 single-field lines); it will be used only via
`sudo -S` inside the remote shell and never printed.

**Design (M5):** rootless Docker under a dedicated unprivileged account `semsbx` (own subuid range, no sudo, no access
to Semechka's data or master key); a sandbox worker running as `semsbx` talks to Semechka only through an
authenticated job API (per-job token, permissions re-checked on every call). Containers get `--network none` by
default, read-only root, dropped capabilities, `no-new-privileges`, memory/pids/cpu/disk limits, a timeout, and only
the staged inputs for that job. Privileged steps needed once: install `uidmap` (+ `docker-ce-rootless-extras`),
create the account, an AppArmor profile for rootlesskit, enable lingering. Isolation guarantee to document honestly:
user-namespace + seccomp + cgroup isolation shared with the host kernel; **not** a VM boundary.

## Blockers (as of 2026-10-26)

* None for the sandbox any more (see "Sandbox: what was installed"). Remaining open items are listed in the milestone table.

## Decisions made while building M2

* **Auto-join is single-tenant only.** New platform users join the auto-join organisation (first joiner = owner).
  Creating a second organisation turns auto-join off and audits it; otherwise companies would share a directory.
* **Org owners/admins can list workspace names** (`org.workspaces.list_all`) and read their metadata, nothing else;
  a workspace nobody lets you see answers 404. Members' emails are shown only to people who manage members.
* **Orphan recovery:** only `org.workspaces.recover` (org owner) can appoint a `ws_admin` to a workspace with none;
  it cannot grant any other role and does not grant reading rights.
* Known gap: contributors/editors are not limited to "own files" (no per-owner rule yet).
* Known gap: platform `owner` is also org owner of the *default* organisation (that is how single-tenant installs
  work); in a hosted multi-tenant setup the operator should not use that account as a tenant member.
* Lint: baseline was 457 findings; adding ruff's FastAPI `extend-immutable-calls` setting brought the repo to 185;
  new files introduce none.

## Decisions made while building M3

* Workspace/folder/file/context reads that the caller cannot see answer 404 at the service layer too (not only HTTP).
* A context is read through its home workspace's rules; attaching it elsewhere never widens access. Adding a file to a
  context requires being able to read that file yourself (no side doors).
* The only deduplication is "identical re-upload to the same file creates no new version"; there is deliberately no
  cross-file or cross-tenant dedupe.
* Only the current version of a file is searchable; restoring an older version re-ingests it. Context item pinning to
  an old version is not offered (the column exists but is unused).
* Retention (per organisation, `Org.policy`): `trash_days` (30), `keep_versions` (all), `job_days` (30),
  `access_log_days` (365); run by the existing sweeper.
* Known gaps: no OCR for scans/images, no audio/video transcription; archives are stored, never opened; contributors
  may overwrite other people's files (no per-owner rule).

## Deployments

| When | Version / schema | Backup (on the Spark) | Rehearsal | Result |
|------|------------------|-----------------------|-----------|--------|
| 2026-10-26 | 0.4.1 / 7 | `backups/roasted-cmsit-pre-0.4.1-20261026-134427` | copy: up, down (roles hash identical) | healthy; member can list image/video models on the copy |
| 2026-10-26 | 0.4.2 / 8 | `backups/roasted-cmsit-pre-0.4.2-20261026-174016` | copy: up → 1 org, 3 members; down → tables and column gone, roles hash identical; up again | healthy; 3 users, 5 chats, CMSIT AI branding intact; new routes answer 401 unauthenticated; Salted untouched (0.4.0 / 6) |
| 2026-10-26 | 0.4.3 / 9 | `backups/roasted-cmsit-pre-0.4.3-20261026-175521` | copy on the Spark: up; real DOCX/XLSX/PPTX uploaded, ingested and retrieved with citations (aarch64); down to 8 removed the file tables and blob directory (roles hash identical); up again | healthy; 3 users, 5 chats, branding intact; new routes 401 unauthenticated; Salted untouched |
| 2026-10-26 | 0.4.4 / 9 | `backups/roasted-cmsit-pre-0.4.4-20261026-181149` | copy on the Spark with the real embedding model: 4/4 semantic queries, junk query empty | healthy; embeddings enabled via the audited settings service; 3 users, chats 5 → 7 during the session (live use), Salted untouched |

Rollback of the latest stage: on the Spark, `semechka migrate --to 8 --yes` with the 0.4.4 program (instance data dir
`~/semechka-deploy/roasted/data`), then install the 0.4.2 tarball (schema 8) from `~/semechka-deploy/releases/`; or restore the
backup directory above (database `.backup` + `master.key` + `assets`).

## Semantic retrieval notes (M3d)

* Model downloaded 2026-10-26 on the owner's instruction to proceed (641 MB, `~/semechka-deploy/models/embed/`) from
  `Qwen/Qwen3-Embedding-0.6B-GGUF`; checksum matched the hub's published sha256. Run by the existing llama.cpp binary
  as its own loopback-only user service (`MemoryMax=6G`); it does not touch the Sarness engine.
* `embeddings.min_score` (default 0.47) is a heuristic measured on this model: related 0.50-0.75, unrelated <= 0.45.
* Modes are reported per passage (`keyword` / `semantic` / `hybrid`) and for the whole search; when the service is
  down, unconfigured, or passages are not yet embedded, the response says so in `notes`.
* Vectors are encrypted per organisation and tagged with the model that made them; a model change makes them stale and
  they are ignored until the sweeper's backfill recomputes them.
* Mutation-testing the cache key showed the first cache-leak tests did not notice a key that ignored the permitted set
  (results were still re-filtered, so nothing leaked, but visible answers could be crowded out); a crowd-out test
  now covers it.
* Production configuration change, made through the audited settings service as `ops:claude`: `embeddings.url` and
  `embeddings.model` on CMSIT AI.
* Process note: an earlier edit of this file truncated the M2 decisions and the deployments table; both were restored
  from commit c06d045 and the missing rows added.

## Decisions made while building M4

* **Agent authority is structural.** `AgentAuthz.check()` = the invoking person's current check AND the agent's own:
  permissions implied by its enabled tools (a write tool implies read), its workspace (other workspaces only to read
  files of contexts it may search), no organisation administration. The existing file/context services accept it
  unchanged, so there is no second code path to forget.
* **Recomputed, not remembered.** Authority is rebuilt from the database before every model turn and every tool call
  (membership, workspace access, model access, agent still enabled/not archived, platform switch). A revoked person's
  queued run is cancelled before the model is called; revocation mid-run stops the next tool.
* **Durable by construction.** A model step and the tool step it requested are written in one transaction. A tool
  that had started when a worker died is reported to the model as "interrupted", never silently replayed; one that
  never started is run. A lease heartbeat stops long turns being mistaken for dead workers. Approval waits pause the
  job (it consumes no worker and no time budget).
* **Agents queue behind people.** Model calls go through the shared generation scheduler at rank 400 (lower priority
  than interactive chat) and the engine's single-chat-model coordination (`wait_for_chat_slot`).
* **Prompt injection.** Document text is framed as data; more importantly nothing the model says can exceed the gate:
  tools outside its list are refused, and the gate is the person's permissions. Test: an obedient scripted model
  following injected instructions creates no file and runs no code.
* **Revising is versioning.** `create_*` on an existing name adds a new version (source `agent`); history stays.
* Known gaps: no streaming of run progress to the browser yet (poll `GET /api/runs/{id}`); no `python`/`shell`/`network`
  tools until the sandbox exists (M5); generated PDF is not offered; `media` tool for agents is not built.
* Mutation checks run: removing the agent scope from `AgentAuthz`, and removing the per-turn authority recheck, each
  make a test fail.

## Sandbox: what was installed on the Spark (2026-10-26, authorised by the owner's brief)

Privileged steps were run once with `sudo -S` fed from the documented DGX Spark credential file over stdin; no secret was
printed, logged or committed. The scripts are `sandbox/setup/1-root.sh` and `2-sbxuser.sh`.

* Packages (apt): `uidmap`, `slirp4netns`, `docker-ce-rootless-extras`, `acl`, `dbus-user-session`. The existing rootful Docker
  daemon, the `docker` group and `fylume`'s groups were **not** touched (`fylume` is still denied on `/var/run/docker.sock`).
* Account `semsbx` (uid 1001): locked password, no sudo, no SSH, lingering enabled, own subuid/subgid range 165536+65536.
* AppArmor profile `/etc/apparmor.d/usr.bin.rootlesskit` (lets rootlesskit create its user namespace; Ubuntu 24.04 blocks it
  by default).
* Resource ceiling for everything that account runs (`user-1001.slice`): memory 24 GiB (high 20), no swap, 3000 tasks,
  800 % CPU, IO weight 50 - the machine's unified memory is shared with the LLM engines.
* Rootless Docker 29.6.2 for `semsbx`: socket `/home/semsbx/run/docker.sock` readable only by `semsbx` and, through a POSIX
  ACL re-applied at each daemon start, by the single user `fylume`. **`"bridge": "none"`** - the daemon cannot give a container
  any network. seccomp (builtin), rootless, cgroup namespaces and no-new-privileges are active.
* Image `semechka-sandbox:1` (from `python:3.12-slim`, digest `sha256:a0f57064...`; pandas, numpy, scipy, matplotlib, openpyxl,
  python-docx/pptx, pypdf, ffmpeg, poppler) built with `docker build --network host`.
* Why an ACL and not a group: adding `fylume` to a group only takes effect when its user manager restarts, which would
  have restarted the production Semechka instances and the engine.

### What a sandbox call gets (all verified by `scripts/sandbox_probe.py` on the Spark)

`--network none`; read-only root; `/work` and `/tmp` are size-capped tmpfs (noexec); uid 65534; all capabilities dropped;
no-new-privileges; seccomp; pids, memory (no swap), CPU, ulimit and wall-clock limits; `--rm`; labels + reaper; no bind
mounts, volumes or docker socket; inputs streamed in over stdin, outputs streamed out over stdout (framed with a per-call
random token, size-capped, regular files only, symlinks dropped); logs captured by the runner in capped memory, so a job
cannot destroy its own evidence by filling the disk; everything the job started is killed when it exits.

Probe result 2026-10-26, image `sha256:4481c987...`, 18/18 PASS: image available; python in/out round trip; no network of any
kind (LAN, host loopback engine, Semechka itself, internet IP, metadata IP, DNS - only `lo` exists); read-only root and
immutable runner; scratch writable but not executable; uid 65534 with CapEff 0, NoNewPrivs 1, seccomp filter; no docker
socket, no host mounts, nothing in /home; own pid namespace (3 processes), no secrets in env; cannot mount, setuid(0), write
sysctl or open /dev/mem; CPU loop stopped at the time limit; fork bomb capped at 59 processes (BlockingIOError); 2 GiB
allocation killed under a 256 MB limit; disk fills stop at the tmpfs size (ENOSPC); a second job sees nothing of the first;
symlinks not exported; allow-listed ffprobe runs; log output truncated at its cap; no container survives its call.
Two bugs found and fixed by the probe along the way (a log-capture design that a job could starve; an output reader that
waited for EOF).

### Isolation: what is and is not claimed

* Claimed: the controls above hold, as tested. The daemon runs as an unprivileged user in a user namespace, so a container
  escape would land in `semsbx`, which owns no Semechka data, keys or credentials, and whose resources are capped.
* Not claimed: protection against a Linux kernel or runtime vulnerability (shared kernel; not a VM); protection against
  side channels; fitness for hostile multi-tenant code beyond what the probe shows. For genuinely adversarial tenants,
  add a VM boundary (gVisor/Kata/Firecracker) - not available on this host today.
* `/sys/kernel/debug` lists as an empty masked directory and `/proc/kcore` is `/dev/null` (Docker's standard masking).
* `fetch_url` is the only agent tool with network access, runs on the host (not in the sandbox), needs a per-agent
  allow-list, `agent.tool.network`, and approval; resolves and re-checks every hop for private/loopback/link-local
  addresses and discards a response whose connected peer was internal. A DNS-rebinding race between our check and the
  connection remains possible for a blind GET to an internal address; the response is never returned to the model.

### Rollback of the sandbox

`systemctl --user -M semsbx@ disable --now docker`; `userdel -r semsbx`; `groupdel semsbx-api`; remove
`/etc/apparmor.d/usr.bin.rootlesskit` (then `apparmor_parser -R`) and
`/etc/systemd/system/user-1001.slice.d/10-semechka-sandbox.conf`; `apt-get remove docker-ce-rootless-extras slirp4netns`.
The installed `uidmap`, `acl`, `dbus-user-session` packages are harmless and may stay.

## First run against a REAL language model (2026-10-26, disposable copy on the Spark)

Model `qwen3.6-35b-a3b@MXFP4_MOE` via the Sarness engine, real finance spreadsheets, real embeddings. Run 1 (an
"analyse, compare, write a management report, remember context" task) used 11 model calls and 9 real tool calls in 257 s;
its arithmetic was correct. It also failed in ways no scripted test could show, all now fixed and covered by tests:

1. The model finished a long `create_document` call **one closing brace short**. The call did not parse, was not run, and
   the model then *announced* the report anyway. Fixes: (a) a reply that is only missing closing brackets is repaired by
   appending exactly those closers (never altering content); anything else gets a specific error returned to the model;
   (b) every file name in a final answer is checked against what the run really created or what exists in the workspace,
   and unverified claims are returned in `warnings` next to the answer.
2. Run 2 (a separate conversation) said it had no saved memory although one item existed: `recall` matched keywords only and
   the memory sat in the system prompt, where the model overlooked it. Fixes: saved memory now precedes the task, `recall`
   ranks by meaning when an embedding service is available, and never claims "nothing" when something is saved.
3. Process: while tidying lint I ran an unscoped `ruff --fix` that rewrote 54 unrelated files. Caught before commit,
   reverted file by file, and the scoped diff re-checked. Lint is only ever fixed on files this work owns.

Re-run after the fixes: see "Real-model re-run" below once recorded.
