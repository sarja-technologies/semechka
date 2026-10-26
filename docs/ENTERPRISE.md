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
| M3d | Semantic retrieval (embeddings) + hybrid ranking | **not started** | needs a real embedding model; none installed, engine exposes no embedding endpoint. Nothing in the product claims semantic search. |
| M4 | Document tools (docx/xlsx/pptx/pdf) | planned | |
| M5 | Durable jobs + container executor | planned, **blocked** on runtime | |
| M6 | Model gateway, scheduling, agent memory | planned | |
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

* Container runtime: the privileged one-time setup above has not been done yet (done at M5, with rollback notes).
* Embeddings: the engine exposes no embedding endpoint and no embedding model is installed (see M3d).

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
