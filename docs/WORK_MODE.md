# Work Mode: isolated, persistent agent sessions (design and contract)

Status: design for implementation. Branch `feature/work-mode`. Written 2026-11-04.

Work Mode is a **session** in which an AI model works on a task inside its own isolated, persistent container, with real tools and
real artifacts, and uses the Sarness Engine for every model and generation it needs. It is not another chat screen.

## 1. Principles (binding)

1. **Reuse, do not fork.** Runs, steps, approvals, durable job queue, heartbeat/recovery, encryption and the `TOOLS` registry in
   `agent_svc.py` already exist. A session is a sequence of `AgentRun`s (one per user message) that share a container, a workspace
   volume and a conversation. No second run loop.
2. **Isolation is enforced by the runtime, not by prompts.** One container per session; no Docker socket, no host mounts, no
   capabilities, no privilege escalation, read-only root, resource ceilings, no network unless explicitly granted.
3. **The Sarness Engine is the only inference and resource authority.** Containers never load models and have no route to the engine.
   Every model, image, video and delegation call is made by a **host-side tool** through Semechka's existing engine adapter, so
   Semechka's model access rules, quotas, audit and the engine's shared memory ledger all apply. Engine refusals are returned to the
   agent verbatim.
4. **Real or absent.** A tool either performs the operation or fails with the real reason. Nothing is simulated. Generation tools return
   only after the engine job completed and the file exists in the workspace.
5. **Persistent and recoverable.** Session, messages, tool history, jobs and workspace survive a browser refresh, a Semechka restart and
   a container restart. A run interrupted by a restart is resumable or ends in a clear `interrupted` state, never silently lost.
6. **Permissions are never widened by content.** Skills, files and tool results are data. They cannot grant tools, network, limits or
   approvals.
7. **Bounded.** Steps per run, wall time per tool, output size, delegation depth/count, concurrent sessions and workspace size all have
   limits with a visible reason when hit.

## 2. Data model (new tables; additive migration, schema version bump)

* `work_sessions`: id `ws_…`, user_id, org_id, title, objective_enc, model_id (Semechka Model.id of the main agent), status
  (`idle|running|awaiting_approval|paused|failed|cancelled`), container_name, volume_name, container_state (`absent|stopped|running|error`),
  network (`none|internet`), autonomy (`contained|ask`), skill_ids (json), limits (json, clamped to the platform ceilings),
  created_at, updated_at, last_active_at, deleted_at.
* `work_messages`: id, session_id, seq, role (`user|assistant|system`), content_enc, run_id (nullable), created_at.
* `work_events`: id (monotonic per session), session_id, kind (`message|tool_call|tool_result|job|delegate|artifact|approval|state|error`),
  run_id, step_n, summary (short, never secrets), payload_enc, created_at. This is the timeline the UI polls (`since=`).
* `work_artifacts`: id `wa_…`, session_id, path (inside the workspace), name, mime, size, sha256, stored_asset (encrypted copy in
  Semechka's asset store so it outlives the container), created_by_step, created_at, saved_to_library (bool).
* `work_skills`: id `sk_…`, scope (`org|user`), owner_user_id, name (slug, unique per scope), title, description, category, body_md
  (≤ 64 KB), files (json: small supporting files, ≤ 1 MB total, stored encrypted), enabled, created_at, updated_at.
* Link: `agent_runs.session_id` (nullable) and a built-in hidden `Agent` per org with `kind='work'` whose `tools` are the Work tools
  (no per-user agent rows). Instructions of a work run are the platform system prompt + session objective + attached skills index.

## 3. Container design

* Image `semechka-work:1`, ARM64, built from the already present `python:3.12-slim` plus apt (git, nodejs, npm, ffmpeg, ripgrep, jq,
  unzip, ca-certificates, build-essential, imagemagick) and pip (numpy, pandas, pillow, matplotlib, requests, pytest, pyyaml, openpyxl,
  python-docx, opencv-python-headless). Dockerfile committed in `work-image/`; built once with `docker build` by an operator script,
  tagged, never pulled at run time (`--pull never`).
* `docker run -d --name work-<id> --label semechka.work=<id> --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=256m
  -v work-<id>:/work --user 65534:65534 --cap-drop ALL --security-opt no-new-privileges --pids-limit 256 --memory <limit> --memory-swap <limit>
  --cpus <limit> --init --log-driver none` with `HOME=/work/.home`. Defaults: 4 GiB, 2 CPUs; ceilings from settings.
* Persistent workspace = named volume mounted at `/work` (inside the unprivileged rootless daemon's storage, not a host path). Size is
  enforced by a quota check before every write/exec (settings `work.workspace_max_mb`, default 2048) because overlay volumes have no
  native quota. Over quota: tools refuse with the exact usage.
* Lifecycle: created lazily by the first tool call; `docker exec` per tool call (never a shell string built from model text outside
  `bash -c <cmd>` run **inside** the container); stopped after `work.idle_stop_minutes` (volume kept); started again on demand;
  removed with its volume on session delete; a reconciler adopts/cleans `work-*` containers by label at startup and every few minutes.
* `network: internet` is possible only if the user holds `work.network` AND the admin setting `work.network_allowed` is true. Rootless
  Docker cannot isolate the LAN from an internet-enabled container; this is documented in the UI and the mode is off by default.
* Path safety: every file operation resolves the path **inside** the container (`realpath -m`), must stay under `/work`, and refuses
  symlink escapes. No host path is ever derived from model output.

## 4. Tools (host-side, schema-validated; each registers in `TOOLS` with `perms` and `sensitive`)

Container: `shell(cmd, timeout_s)`, `python(code|file, timeout_s)`, `read_file(path, offset, limit)`, `write_file(path, content)`,
`edit_file(path, old, new, replace_all)`, `list_dir(path)`, `search_files(pattern, path)`, `git(args[])` (whitelist: init, status, diff, log,
add, commit, branch, checkout, switch, restore, show, tag, stash, merge, rebase without `--exec`; hooks disabled; `clone/fetch/pull/push`
only with network). Output capped; exit code, duration and truncation reported.

Engine (all via Semechka's adapter and access rules): `list_models(kind?)` (only models this user may use, with readiness and loaded state),
`resources()` (cached ≤ 5 s: free unified memory, loaded models, active engine jobs, own queue; no heavy audit),
`generate_image(prompt, model?, size?, steps?, seed?, source_file?, out?)`, `generate_video(prompt, model?, profile?, inputs{}, out?)`
(profiles such as OpenJutsu take workspace files as inputs), `job_status(id)`, `wait_job(id, timeout_s)`, `cancel_job(id)`,
`delegate(model, task, files?)` (bounded: depth ≤ 2, ≤ 8 per run, own timeout and token cap; the delegate runs through the engine like
any chat; parent/child recorded; vision models may be given workspace images).

Session: `list_skills()`, `read_skill(name)`, `publish_artifact(path, title?)`. Files written under `/work/artifacts/` are listed automatically.

Approvals: `autonomy=contained` (default) runs in-container actions and engine tools without asking (isolation and quotas are the
guard); network, `git push`, deleting more than N files or the workspace root, and anything marked `sensitive` ask. `autonomy=ask` asks
for every shell/python/write/git.

## 5. Skills (Markdown)

A skill is a `.md` file with YAML front matter (`name`, `description`, `category`; any other key is ignored) plus optional supporting
files. Categories: coding, research, image-generation, video-production, media-editing, document-creation, data-analysis, general.
The session prompt contains only the index (name + one-line description); the agent loads a skill's text with `read_skill`, which also
copies its supporting files into `/work/.skills/<name>/`. Users manage their own skills; admins (`work.skills.manage`) manage org skills.
A skill can never change tools, network, approvals or limits.

## 6. REST API (all under `/api/work`, session-cookie auth + CSRF like the rest of Semechka; owner-only; every route re-checks)

* `GET /options` → `{models:[{id,alias,tools,vision,context,ready}], skills:[…], limits:{…}, network_allowed, perms:{network,skills_manage}, image_ready}`
* `POST /sessions {title?, objective?, model_id?, skill_ids?, network?, autonomy?}` → session · `GET /sessions` · `GET /sessions/{id}` ·
  `PATCH /sessions/{id}` · `DELETE /sessions/{id}`
* `POST /sessions/{id}/messages {content}` → queues a run (or appends to the active run); `POST /sessions/{id}/pause|resume|cancel`
* `GET /sessions/{id}/events?since=<n>&wait=<s>` → ordered events (long-poll up to 25 s); `GET /sessions/{id}/state` (status, container, active run,
  engine jobs with progress, usage)
* `POST /sessions/{id}/approvals/{step} {approve}`
* `GET /sessions/{id}/files?path=` (tree) · `GET /sessions/{id}/files/content?path=&download=` · `PUT /sessions/{id}/files?path=` (multipart upload, size
  caps) · `DELETE /sessions/{id}/files?path=`
* `GET /sessions/{id}/artifacts` · `GET /artifacts/{aid}/content` · `POST /artifacts/{aid}/save-to-library`
* Skills: `GET/POST /skills`, `GET/PUT/DELETE /skills/{id}`, `POST /skills/import` (multipart .md), `POST /skills/{id}/enable|disable`

## 7. Permissions, settings, audit

Permissions: `mode.work` (use Work Mode), `work.network`, `work.skills.manage`. Mode `work` is added to `MODES` and gated by
`modes.work.enabled`. Settings: `work.enabled`, `work.max_sessions_per_user` (2 running), `work.max_running_total`, `work.memory_mb`,
`work.cpus`, `work.pids`, `work.workspace_max_mb`, `work.tool_timeout_s`, `work.idle_stop_minutes`, `work.network_allowed`,
`work.delegation_max_depth|count`, `work.max_steps`. Audit (hash-chained, existing): session create/delete, network grant, every
`shell/python/git/write` summary (command text is stored encrypted in the event payload, only a short summary in the audit), engine jobs
started, delegations.

## 8. Verification required before "done"

Backend tests with a runtime test double; a real integration run on the Spark against rootless Docker (persistence across container stop/
start, no network, limits, path escape attempts, git commit, ffmpeg/PIL, quota) and a real agent run with a small engine model that
creates, runs and fixes code and produces a real image through the engine. UI verified in a real browser by someone holding a session.

## 9. UI notes (response shapes the web UI is built against)

Section 6 fixes the routes. The Work Mode UI (`web/src/routes/(app)/work/`, `web/src/lib/work/`) was written against the bodies below. The
authoritative typed copy is `web/src/lib/work/types.ts`; a field marked optional may be absent and the UI then shows nothing for it
(it never fills in a guess). Errors use the usual `{"detail": {"code", "message"}}` (or a string `detail`); the UI shows `message` as written
and treats 401/403 as "no permission", 404 as "not found", 0/network failure as offline, 409/413/422/429 as a limit message.

* **Navigation:** the page is offered only when `/api/me` `ui.operations.work.enabled` is true (`reason` is shown if the person opens `/work`
  anyway). A `work` entry in `ui.pages` is ignored; the UI adds its own entry next to Assistants.
* `GET /options` as in section 6. Optional `limits.upload_max_mb` (used to refuse an oversized upload before sending it). `skills[]` are the
  skill summaries below (the UI offers only `enabled` ones). `models[].ready=false` is listed but not selectable.
* `POST /sessions` body: `network` is the string `"none"|"internet"` (same vocabulary as the session field), `autonomy` is `"contained"|"ask"`.
  Returns the session. A **session** is `{id, title, objective?, model_id, model_alias?, status, container_state, network, autonomy,
  skill_ids[], created_at, updated_at?, last_active_at}`. `GET /sessions` returns an array of these (a `{sessions: []}` wrapper is also accepted).
* `POST /sessions/{id}/messages {content}` returns any 2xx body. The UI never inserts the message locally: it appears when the `message`
  event arrives, so the order is always the server's. `pause|resume|cancel` return any 2xx body.
* `GET /sessions/{id}/events?since=<n>&wait=<s>` returns `{events: WorkEvent[], last_id?: number}` (a bare array is also accepted).
  Events have `id` (monotonic integer, the cursor), `kind`, `run_id?`, `step_n?`, `summary?`, `created_at` (ISO), and `data` (the decrypted
  payload, shaped per kind). The first request is `since=0&wait=0` (full history); afterwards the UI loops with `wait=25`. It returns at once when
  anything newer than `since` exists, else after `wait` seconds with `events: []`. The UI dedupes by `id`, so re-sending an event is harmless.
  `data` per kind (all fields except those in bold are optional):
  * `message`: `{role: "user"|"assistant"|"system", content}`. Complete messages, no token streaming.
  * `tool_call`: `{call_id, tool, command?, args?}` and `tool_result`: `{call_id, ok?, exit_code?, duration_ms?, output?, truncated?, error?}`.
    A result joins its call by `call_id`. A call with no result yet is shown as "Running".
  * `job`: `{job_id, job_kind?: "image"|"video", model?, status: "queued|running|completed|failed|cancelled", phase?, progress?: 0..1, error?}`.
    Repeated events with the same `job_id` update one card. **A progress bar is drawn only when `progress` is a number.**
  * `delegate`: `{delegate_id, model, task, status: "running|done|failed", result?, error?, depth?}`.
  * `artifact`: `{artifact_id, name, path?, mime?, size?}` (the id is the `wa_…` id used by `/artifacts/{aid}/...`).
  * `approval`: `{step, tool, status: "pending|approved|denied|expired", command?, reason?}`. The UI answers with `POST /approvals/{step}`.
    After the answer the backend should append another `approval` event for the same `step` with the new status; the UI folds them per step.
  * `state`: `{status, note?}` (session status changes). `error`: `{code?, message}`.
  Unknown kinds are skipped.
* `GET /sessions/{id}/state` returns `{status, container: {state, note?}, network?, autonomy?, model_id?, active_run?: {id, step_n?, max_steps?}|null,
  jobs?: EngineJob[], usage?: {steps?, tokens_in?, tokens_out?, workspace_bytes?, workspace_limit_bytes?}}` where
  `EngineJob = {id, kind?, model?, status, phase?, progress?: 0..1|null, queue_position?, error?, artifact_id?, started_at?, finished_at?}`.
  The UI polls it every 4 s while the session works or a job is active, otherwise every 20 s, and after each batch of new events.
* `GET /files?path=` lists **one directory level**: `{path, entries: [{name, path, type: "file"|"dir", size?, modified?, mime?}]}`; `path` is relative
  to `/work` (empty string is the root) and is sent back verbatim by the other file routes. `GET /files/content` returns raw bytes with a content type
  (`download=1` adds `Content-Disposition: attachment`). `PUT /files?path=<directory>` is multipart with the file in the field `file` and puts it
  in that directory. `DELETE /files?path=` removes a file or a directory tree.
* `GET /artifacts` returns an array of `{id, name, path?, mime, size, created_at, saved_to_library?}`. `GET /artifacts/{aid}/content` serves the bytes
  (the UI embeds it in `<img>`/`<video>`, so it must be same-origin and cookie-authenticated; `?download=1` for attachment).
* **Skills** (`GET /skills` returns an array of summaries): `{id, scope: "org"|"user", name, title, description, category, enabled, updated_at?,
  can_edit?}`. `GET /skills/{id}` adds `body_md` and `files: [{name, content}]` (contents included). `POST /skills` and `PUT /skills/{id}` take
  `{scope, name, title, description, category, enabled, body_md, files: [{name, content}]}` and return the full skill. `can_edit` tells the UI whether
  to show Edit or View (without it the UI assumes personal skills are editable and organisation skills need `perms.skills_manage`). `POST /skills/import`
  is multipart: field `file` (the .md) and field `scope` (`user|org`); it returns the created skill. `POST /skills/{id}/enable|disable` return the skill.
  Front matter is checked in the browser first (name slug `[a-z0-9-]`, description, category from the section 5 list, body at most 64 KB, supporting files
  at most 1 MB in total) so most mistakes never reach the server; the server remains the authority.

Development: `web/dev/work-mock/server.mjs` is a throwaway mock of this contract (canned timelines, no auth) used to build and screenshot the UI without a
backend; `node dev/work-mock/server.mjs --static build` after `pnpm build`. It is not part of the app bundle.
