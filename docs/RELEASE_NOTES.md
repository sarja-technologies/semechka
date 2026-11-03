# Release notes

## 0.7.0 (Work Mode: engine tools, delegation, skills; UI integrated)

Merges the Work Mode backend (0.6.0) with its web UI and adds the second phase. Schema unchanged (11). Off until an administrator switches on
`work.enabled` and `modes.work.enabled`. Design, contract and implementation notes: `docs/WORK_MODE.md` (sections 9.8 to 9.10).

- **Engine tools for the agent.** `list_models` (only models the person may use, with the engine registry's readiness, loaded state, memory and
  capabilities; profiles such as OpenJutsu with their inputs), `resources` (free unified memory, loaded models, ledger holders, the person's own jobs and
  limits; cached for at most 5 s, no hardware audit), `generate_image`, `generate_video` (plain models and profiles; profile file inputs are workspace
  paths), `job_status`, `wait_job`, `cancel_job`. They use the same machinery and limits as the Image and Video modes: one admission gate
  (concurrency, daily limit, Library size, retention), the same jobs and encrypted assets, so results also appear in the Library. A generation tool
  reports success only after the engine job completed and the file is in the workspace and listed as an artifact; the engine's refusals and
  failures reach the agent in its own words. The timeline shows the engine's real progress and phase.
- **Delegation.** `delegate(model, task, files?)` gives a bounded sub-task to another model the person may use, through the same chat path as
  everything else (the engine decides which chat model is loaded). Depth (`work.delegation_max_depth`, 2), count per run
  (`work.delegation_max_count`, 8), a time limit (`work.delegate_timeout_s`) and a token cap (`work.delegate_max_tokens`); waiting-for-memory and
  refusal texts are shown. Reasoning models are asked not to think unless the agent says so.
- **Markdown skills.** `/api/work/skills` (list, create, read, update, delete, import, enable, disable), personal and organisation scope (organisation
  skills need `work.skills.manage`), name unique per scope, body up to 64 KB, text files up to 1 MB, encrypted. Front matter is read for four keys
  only; a skill can never change tools, network, approvals or limits. Sessions carry `skill_ids`; the prompt holds only each skill's name and
  description; `list_skills` and `read_skill` (which also copies the skill's files to `/work/.skills/<name>/`).
- **Seeded data (disclosed).** The first time an organisation lists skills after this upgrade, seven organisation skills are created: `coding`,
  `research`, `image-generation`, `video-production` (including how to call OpenJutsu and the MiniMax H3 territory note), `media-editing`,
  `document-creation`, `data-analysis`. Administrators can edit, switch off or delete them; deleted ones are not recreated. `semechka migrate --to 10`
  removes them with the skills table.
- **API now matches the UI.** Events are `{id, kind, run_id, step_n, summary, created_at, data}` with per-kind `data`; tool calls and results join by
  `call_id`; an answered approval appends a new approval event for the same step; `/state` has `jobs`, `active_run`, `usage`; `/files` is one level; times
  are ISO 8601; `/api/me` has `ui.operations.work`. A cancelled session accepts new messages. Full list of changes: `docs/WORK_MODE.md` 9.9.
- **UI.** Work screens (sessions, live timeline, files, artifacts, jobs, skills editor). Two fixes: approvals of different runs no longer merge, and a
  delegation shows why it is waiting.
- **Settings.** `work.delegate_timeout_s`, `work.delegate_max_tokens`, `work.engine_wait_s`. Work-only tools cannot be given to an assistant.

What is verified and what is not is stated in `docs/WORK_MODE.md` 9.10 (backend tests, and real runs on the Spark staging instance with real engine
models). The Work UI itself was exercised against a mock and in the mock screenshots; it has not been used by a person on staging.

## 0.6.0 (Work Mode: backend core)

Backend only (no UI yet); schema 10 -> 11, additive. Off until an administrator switches on `work.enabled` and `modes.work.enabled`.
Design and contract: `docs/WORK_MODE.md` (implementation notes in section 9).

- **Sessions with their own container.** Each Work session owns one locked-down container (no network, read-only root, `/tmp` tmpfs, user 65534,
  no capabilities, pids/memory/CPU limits) and a persistent named volume at `/work`. Created lazily, stopped when idle, adopted at start-up,
  removed with the session. Image `semechka-work:1` (`work-image/`, built by `scripts/spark/work_image.sh`).
- **Real tools, host-side.** `work_shell`, `work_python`, `work_read_file`, `work_write_file`, `work_edit_file`, `work_list_dir`,
  `work_search_files`, `work_git`, `work_publish_artifact`, `work_note`. Every path is resolved inside the container and must stay under `/work`
  (symlink escapes are refused); output is capped with head and tail kept; the workspace has a size quota that is checked before writes and
  commands and enforced while a command runs.
- **Runs on the existing loop.** A message becomes an agent run (job kind `work`), with the conversation so far and a bounded durable state
  block. Pause, resume, cancel, approvals (`contained` or `ask` autonomy), a timeline of events with long-poll, and recovery after a restart.
- **API.** `/api/work/...` (options, sessions, messages, pause/resume/cancel, events, state, approvals, files, artifacts, save-to-library).
  Owner-only, mode-gated, CSRF-protected.
- **Permissions and settings.** `mode.work`, `work.network` (by name, administrators), `work.skills.manage`; mode `work`; `work.*` settings.
  Upgrading grants the new permissions to administrators only; nobody else gains anything.
- **Schema.** `work_sessions`, `work_messages`, `work_events`, `work_artifacts`, `work_skills`; `agents.kind`; `agent_runs.session_id`.
  `semechka migrate --to 10` removes them (workspace volumes must be removed from the container runtime, see `docs/WORK_MODE.md`, 9.7).

## 0.5.12 (editing profiles: OpenJutsu and similar)

Backend and UI; no schema change. Needs a Sarness engine that provides `/v1/registry` and `/v1/profiles/{id}/jobs`; on an older engine nothing changes.

- **Profiles appear as video models.** A composite capability the engine publishes (for example "OpenJutsu - MiniMax H3") is offered in Video with the
  engine's own readiness. One the engine reports not ready cannot be started, and the reason it gives is shown.
- **A form from the engine's schema.** Choosing a profile replaces the picture/video settings with a form built from the `inputs_schema` the engine publishes:
  source video, reference pictures, numbers and choices. Send stays off, with a plain reason, until required inputs are present.
- **`POST /api/media/profile-jobs`** (multipart: `model_id`, `inputs` JSON, `file:<input>` parts). Inputs are validated against the engine's schema and
  `media.profile_upload_max_mb` (default 512), the files go to the engine, and an ordinary durable job runs; the result is collected and stored (encrypted)
  like any generation and appears in the Library. The plain `/api/media/jobs` refuses profiles.
- Model licences travel with the profile (OpenJutsu uses MiniMax H3, whose licence excludes the EU, UK, South Korea and the USA).

## 0.5.11 (embedding: inputs over the model's token limit)

Backend only; no schema change. A single input longer than the embedding server's per-slot context (for example 6000 characters of digits or
CJK) used to make the server refuse the whole batch, so all 16 vectors were lost and search fell back to keyword.

- **Isolation.** Only an HTTP 400/413/422 on a batch triggers recovery; the items are retried one at a time so good inputs keep exactly their
  vectors. Ordinary batches are unchanged: same requests, bit-identical vectors, no extra round trips.
- **Splitting.** An input the server still refuses is counted with the server's `/tokenize`, cut into overlapping token windows that fit the
  limit read from `/props` (per slot), embedded, and combined (mean, re-normalised) into one vector, so `embed()` still returns one vector per
  input. At most 8 windows; a longer tail is dropped with a warning and a `truncated` report. Failures name the input and are raised only after the
  others were attempted. `embed_with_report()` and `STATS` expose what happened; logs never contain text.

## 0.5.10 (model access labels, neutral product naming)

Backend and UI only; the database schema is unchanged.

- **Model badges describe access, not the model.** The picker's single "Restricted" tag was shown whenever someone reached a model
  only through `models.use_all`, which includes every newly discovered model (discovery inserts them disabled) and every model
  without a grant for the viewer's role. It never looked at what the model is, so abliterated, uncensored, fine-tuned and locally hosted
  models were affected only when an administrator had not enabled or granted them. Catalog rows now carry `access`
  (`offered`, `not_enabled`, `access_restricted`, `unavailable`), `access_label` and `access_note` that name the real cause. `restricted`
  stays in the response for older clients with the same meaning. Whether a model is loaded, loading or runnable is unchanged and comes from
  the engine's `state`. Access rules themselves are untouched.
- User-facing messages no longer name the framework ("Cannot reach the server."); the product name comes from `brand.*` settings.
- `scripts/spark/brand_rename.py`: operator-side, audited update of the product name on one instance.

## 0.5.9 (Schemra integration)

Adds the authenticated `/schemra/` route to Schemra, the separate local AI training lab, and brings the earlier proxy branch onto the
0.5.8 line. **Off by default**: `schemra.enabled` is false (every `/schemra/...` answers 404), and no existing role gains a permission
on upgrade. The database schema is unchanged. Guide: [SCHEMRA_INTEGRATION.md](SCHEMRA_INTEGRATION.md).

- **Same-origin reverse proxy at `/schemra/`** to a Schemra on this machine (loopback only), streaming bodies and event streams, with
  HMAC-signed identity headers (user, role, time, nonce) that Schemra verifies; Semechka's session, CSRF/same-origin rule, rate limit
  and hash-chained audit apply; cookies and browser-supplied identity headers never reach Schemra.
- **Permissions** `schemra.view` (read-only), `schemra.operate` (chat, boards, agents, training), `schemra.admin`; the strongest one
  held decides the role sent. Schemra enforces the role per route on its side, including direct API routes.
- Settings `schemra.enabled`, `schemra.upstream`, upload/rate/timeout/audit/CSRF options; the shared secret is a file
  (`SEMECHKA_SCHEMRA_SECRET_FILE`, default `<data dir>/schemra-proxy.secret`, mode 0600), never a setting.
- Sign-in with `?next=/schemra/...` loads the lab after login instead of routing to it inside the app.

## 0.5.8 (Trinodal AI integration)

Adds the Semechka side of Trinodal AI, a separate modular AI system. Everything is inert until an administrator configures it:
`trinodal.url` is empty by default, no existing role gains a permission, and the database schema is unchanged (10).
Guide: [TRINODAL.md](TRINODAL.md).

- **Same-origin management UI at `/trinodal/`.** A streaming reverse proxy serves Trinodal's single-page UI, JSON API and
  event streams on Semechka's origin. Permissions are checked in the backend per request class (`trinodal.view` reads,
  `trinodal.train` changes, `trinodal.admin` for `/api/admin/*`); changes need the CSRF token and a same-origin request; the
  service token lives in a file on the server and is never exposed; headers are allow-listed in both directions so Semechka's CSP
  stays unchanged; limits, rate limits and audit events (no bodies) apply.
- **Three new sensitive permissions**: `trinodal.view`, `trinodal.train`, `trinodal.admin`. No wildcard grants them. New
  installs give Owner all three and Platform Admin view and train. **Existing instances are not changed on upgrade**: an owner
  grants them through the ordinary, audited role edit (TRINODAL.md).
- **Trinodal as a local chat model.** The Sarness backend option `local_endpoints` (for example `["trinodal"]`) marks the models
  of those engine endpoints as not external, so the egress policy and the "external service" notices no longer apply to a service
  on this machine. Other endpoints of the same engine stay external. Editable in Administration → Backends.
- **Node path in the technical details.** A Trinodal reply's run record (nodes, route, trace id, language core, timings) is kept
  with the message and shown only to people holding `tech.node.name`, `tech.job.trace`, `tech.runtime.name` or
  `tech.perf.duration` for the matching part, as "Path: conversation → mathematics → conversation" and rows, in the live reply,
  the saved thread and exports.
- **Navigation**: a "Trinodal" entry (Dashboard, Network Explorer, Training Studio, Training Jobs, Evaluations; training links only
  with `trinodal.train`) for people with `trinodal.view`, in the conversation sidebar and the account menu, and in the command
  palette.
- New settings `trinodal.url`, `trinodal.max_upload_mb`, `trinodal.max_download_mb` (edit permission `trinodal.admin`), and the
  environment variables `SEMECHKA_TRINODAL_TOKEN_FILE` and `SEMECHKA_INSTANCE_NAME`.
- New models named like their endpoint (`endpoint:trinodal:trinodal`) are called `trinodal`, and the developer "Trinodal AI" is
  recognised (lettermark).

Not verified: the real Trinodal service, UI and engine registration (test doubles only; see KNOWN_LIMITATIONS.md).

## 0.4.0 (2026-10-25)

Semechka 0.4.0 is a self-hosted, single-organisation release. It has had no independent security review. Read
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) before deploying. This is the first tagged release, and it also contains
everything built for 0.3.0, which was never merged or tagged separately.

### New since 0.3.0
- **Deployment presets:** Personal, Business and Managed Private Hosting, plus Custom, where the owner chooses
  each value in the first-run wizard.
- **Interface:** neutral Light and Dark themes (plus Graphite and Custom). A redesigned chat layout with a compact
  composer. Enter or Ctrl+Enter sends, as configured. Prompt history is optional (Alt+Up/Down).
- **Model picker** built only on what the engine reports: search, sections, favourites, administrator-controlled
  names, groups, order, pins, hidden and restricted models. Nothing is ranked. Developer logos appear only where
  their owners allow it; other developers get lettermarks.
- **Engine panel (Running tab):** live memory, the chat model the engine holds, the picture/video worker, and the
  engine's job queue. Other clients' jobs show their kind and status only. Administrators can unload a model
  (refused while a reply is being written) and stop the picture/video worker to get its memory back (refused while
  it works or jobs wait).
- **Model permissions split:** `models.view_all`, `models.use_all`, `models.status.view`, `ops.model.load`,
  `ops.queue.view`. All enforced on the server and audited.
- A real "Page not found" page. People can go back to the organisation's default theme.

### Release hardening
- **Replies are protected from model switches as far as the engine allows.** Engine 0.4.0 holds one chat model for
  every client and switches it on any request for another model, even mid-reply, with no way to reserve it. So:
  - Loading a chat model ahead of time is turned off for the Sarness Engine.
  - The queue never runs replies on two different chat models at once.
  - Before a reply that would make the engine switch, Semechka waits until no client is mid-reply. If that takes
    longer than the queue's wait limit, it gives up without switching.

  This narrows the window but does not close it; see "Known limitations" below.
- **Sign-in could answer before its session was stored.** With FastAPI 0.118 and later, changes were committed after
  the response was sent, so a client that called the API at once could get 401. A failed commit also answered 200.
  Every change is now committed before the answer. FastAPI 0.121 or later is required.
- **The setup token is no longer written to service logs.** It is shown only in a terminal; otherwise the server
  names the owner-only file that holds it.
- **Deployment verification removes what it creates.** It deletes only its own test conversation, by its id, and
  checks the list is unchanged.
- **Firefox:** the whole browser suite, including accessibility checks, passes in Firefox 158 as well as Chrome.

### Upgrading
- Database schema 5 → 6, migrated on first start. A program older than its data refuses to start.
- The installer keeps a snapshot of the data before every update. To roll back:
  - `./install.sh --rollback --restore-data` goes back to 0.3.0 together with its data.
  - `--restore-data` on its own is for a program that was already rolled back and refuses to start.

  Rehearsed three times on a copy of a production instance (DEPLOYMENT_EVIDENCE.md).
- Roles that managed models receive the new model permissions; other roles receive none.

### Known limitations (summary)
- **Engine-level limitation: cross-client scheduling is not completely safe.** The engine's status and the request
  that makes it switch are two calls, so a reply another program starts between them can still be cut off. Clients
  that do not run Semechka do not wait. A GPU-layer difference the engine does not report is not predicted. Only an
  engine-side lease or reservation would close this.
- **Large models are untested through Semechka:** Qwen3 Coder Next (about 55 GB by the engine's estimate),
  gpt-oss-120b, Hermes 4 70B, and the 50–126 GB video models.
- **The picture/video worker keeps memory** after its models are freed, until an administrator stops it. The next
  picture or video then starts it again, more slowly.
- **Not tested:** screen readers, Safari, phones measured directly, PostgreSQL, Docker packaging, the CI workflow.
  There is no load progress, because the engine reports none.
- **Setup tokens in old service logs:** journals of installs made before 0.4.0 can still contain previously printed
  setup tokens. Each stops working once the wizard is completed or the server restarts.
- **Superseded commit subject:** one development commit is titled "model load/unload that never cuts off a reply".
  That claim was withdrawn in the hardening pass above.

### Verification
- Backend: 296 passed, 8 skipped (opt-in real-backend tests).
- Playwright: 78 passed in Chrome and 78 passed in Firefox 158.
- Design lint and contrast check: pass.
- Deployed to a DGX Spark (two instances, one shared Sarness Engine 0.4.0). `verify_deployment.py`: 35 passed;
  `spark_salted_check.py`: 14 passed.

Details: DEPLOYMENT_EVIDENCE.md, REQUIREMENTS_TRACEABILITY.md.
