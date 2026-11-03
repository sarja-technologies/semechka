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

## 9. Implementation notes (phase A: backend core)

Status: implemented on branch `feature/work-mode`. Everything below is what *is*, not the plan.

### 9.1 Files

| File | What it holds |
|---|---|
| `backend/semechka/models_work.py` | `WorkSession`, `WorkMessage`, `WorkEvent`, `WorkArtifact`, `WorkSkill` (table only). |
| `backend/semechka/migrations.py` | Step 11: creates the tables, adds `agents.kind` (`cloud` \| `work`) and `agent_runs.session_id`, grants `mode.work` / `work.network` / `work.skills.manage` to `platform_admin` (and `work.network`, `work.skills.manage` to `owner`). `down` removes the tables, the Work agents/runs/jobs, the hidden workspaces, the `work.*` settings and the three permissions from every role/team/override. It cannot remove containers or volumes: see "Rollback" below. |
| `backend/semechka/work_runtime.py` | `WorkRuntime` interface, `DockerRuntime` (production), `LocalDirRuntime` (TEST DOUBLE), and `FS_HELPER`, the program that runs *inside* the container for every file operation and command. |
| `backend/semechka/work_svc.py` | Sessions, messages -> runs, pause/resume/cancel, approvals, container lifecycle (`ensure_running`, idle stop, `reconcile`, `startup`, `sweeper`), events, the durable session-state block, artifacts, save-to-library, and `WorkExtension` (the hook object for the run loop). |
| `backend/semechka/work_prompt.py` | The Work system prompt and the history of earlier turns. |
| `backend/semechka/work_tools.py` | The tools (registered in `agent_svc.TOOLS`), argument validation, quota, command-result formatting, artifact registration. |
| `backend/semechka/routers/work.py` | The REST API of section 6 (without skills). |
| `work-image/Dockerfile`, `scripts/spark/work_image.sh` | The `semechka-work:1` image and the operator script (build / info / smoke). |
| `scripts/spark/work_integration.py` | Real integration run against the rootless daemon (63 checks). |
| `backend/tests/test_work.py`, `test_work_runtime.py` | Tests with the test double (no container runtime needed). |

### 9.2 Deviations from the design text, and why

* **Tool names carry a `work_` prefix** (`work_shell`, `work_python`, `work_read_file`, `work_write_file`, `work_edit_file`,
  `work_list_dir`, `work_search_files`, `work_git`, `work_publish_artifact`, plus `work_note`). `shell`, `python` and `read_file`
  already exist in `TOOLS` as cloud-agent tools with different meaning; the registry is one flat namespace.
* **`work_note(text)`** is an addition: the model can pin a short note into the durable session-state block.
* **Jobs use kind `"work"`**, not `"agent"`, with the same handler (`agent_svc.handle_agent`). That gives Work its own
  concurrency limit (`work.max_running_total`, read at claim time through a callable in `jobs_svc.CONCURRENCY`) without starving
  cloud agents. The loop itself is shared, not forked.
* **`sensitive` vs approvals.** All phase-A container tools have `sensitive=False`; instead `ToolSpec.mutating` marks the tools
  `autonomy=ask` asks about (shell, python, write, edit, git). `sensitive=True` on any Work tool means "always ask, in every mode".
  Contained mode additionally asks for: network-looking commands in an internet session (heuristic regex, *not* a boundary),
  `git clone/fetch/pull/push/remote` in an internet session, and commands that look like deleting the workspace or most of it
  (`rm -rf /work/*`, `find . -delete`, ...; also a heuristic). In a no-network session git network subcommands are refused outright.
* **Session `limits`** can only lower the platform ceilings (`memory_mb`, `cpus`, `workspace_mb`, `tool_timeout_s`, `max_steps`),
  and are re-clamped on every read, so lowering a ceiling applies to existing sessions at once.
* **`work_sessions.state_enc`** (not in the original column list) holds the bounded session-state block; `has_workspace` records
  that a container/volume was created at least once (so a file listing never creates one by itself).
* **Where the Work agent lives.** `Agent` needs a workspace, and a run's authority check needs `ws.view`. Each organisation gets one
  internal workspace (`policy = {"system": "work"}`, visibility `shared`, never listed by `Authz.visible_workspaces`) and one
  `Agent(kind='work', approval='never')`. Work tools declare no workspace permissions: the gates are the person's CURRENT `mode.work`
  + `modes.work.enabled` + `work.enabled` (re-checked before every model turn and every tool call) and session ownership.
* **Artifacts are one row per path** (`UNIQUE(session_id, path)`); a changed file updates the same artifact (new `sha256`, new stored
  copy). The encrypted copy lives in `data/work_artifacts/<user_id>/<artifact_id>.bin` and outlives the container.
* **Save to library** works for PNG/JPEG/WebP and MP4/WebM (it creates a completed `MediaJob` + `MediaAsset`, so the gallery shows
  it) and needs the image/video mode to be available to the person. Other file types have no library yet: they stay downloadable.
* **Skills API is not implemented**; `GET /api/work/options` returns `skills: []`.

### 9.3 Seams for phase B (engine tools, delegation, skills)

* **Run loop hooks**: `agent_svc.RunExtension` (hooks: `disabled_reason`, `before_turn`, `on_interrupted`, `limits`, `resolve_model`,
  `messages`, `needs_approval`, `repair_action`, `on_model`, `on_tool_requested`, `before_tool`, `on_tool_done`, `on_final`, `on_end`) and
  `agent_svc.EXTENSIONS['work']`. Phase B subclasses or edits `work_svc.WorkExtension`; it never edits `_prepare_turn` & co.
* **Adding a tool**: `@work_tool(name, summary, args_example, mutating=...)` in `work_tools.py` (or a module imported at the bottom
  of `work_svc.py`), then append the name to `work_tools.WORK_TOOLS`. The function gets `(tc, w, a)`: `tc` the `ToolCtx` (DB session
  `tc.s`, `tc.run`, `tc.step`), `w` a `Wk` (`w.sid`, `w.name`, `w.lim`, `w.run_id`, `w.step_n`, `w.rt`, `w.container()`), `a` the
  arguments. Helpers: `a_str/a_int/a_bool`, `norm_path`, `put(w, name, rel, data)` (quota-checked write into the workspace),
  `register_artifact(w, name, rel)`, `sync_artifacts`, `run_command`. The decorator releases the DB transaction before the tool
  runs, maps `TenancyError`/`RuntimeError_` to a `ToolError` with host paths scrubbed, and publishes `w.done(...)` into the
  `tool_result` event. Report created artifacts through `ToolOut(artifacts=[{"file_id": id, "name": n, "version": 1}])`.
* **Engine tools** must call Semechka's engine adapters from the host (`adapter_for(backend, st)`; see `agent_svc._call_model_once`
  for the scheduler slot, `media_svc.submit` for generation jobs), check access with `gateway_svc.check(st, s, principal, org, ws,
  model_id, mode)`, and write results into the workspace with `put(...)`. Long engine jobs should emit `job` events with
  `work_svc.emit(st, s, sess, "job", summary, detail, run_id=..., step_n=...)` and appear in `work_svc.state_of(...)["engine_jobs"]`
  (currently `[]`).
* **Delegation** (`delegate`): the depth/count ceilings are already settings (`work.delegation_max_depth`, `work.delegation_max_count`);
  record parent/child through `AgentRun.usage` or a new column; emit `delegate` events.
* **Skills**: the `work_skills` table and the permission `work.skills.manage` exist. `work_prompt.system_prompt` has no skills section
  yet: add the index there (`sess.skill_ids` holds the ids), add `list_skills` / `read_skill` tools, and `routers/work.py` routes.
  `read_skill` should copy supporting files with `put(...)` into `/work/.skills/<name>/`.
* **Events**: kinds `message|tool_call|tool_result|job|delegate|artifact|approval|state|error`; the UI contract is
  `GET /api/work/sessions/{id}/events?since=&wait=` -> `{events, last_id, status, container_state}`; each event is
  `{id, kind, run_id, step, summary, detail, at}` with `detail` decrypted for the owner.

### 9.3b What the real-model runs taught us (and what was changed because of it)

Real runs against the Spark engine (staging, `scripts/spark/work_e2e.py`) found three things that mocks could not:

* **Small models slip on JSON syntax**, not on content: a markdown fence around the action, `\'` (an invalid escape), real line breaks in a
  string, `}]` where `}}` was meant. `RunExtension.repair_action` (implemented by `work_prompt.repair`, used only after the strict parse failed)
  fixes exactly those and reports `repaired: true` in the step meta; it never edits the content. When a reply still cannot be parsed, the
  reason names the OUTERMOST problem (the shared parser used to report the last inner object, which was misleading) and tells the model
  how to write JSON strings.
* **A long run overflowed the model's context window** (HTTP 400 from the engine ended the run). `work_prompt.fit_steps` now sizes every turn:
  it uses the window Semechka knows (administrator setting or learned from an engine refusal; otherwise it assumes 16K), shortens old tool
  results, and finally collapses the oldest steps into a one-line-per-step digest. The session-state block, the history and the workspace
  carry the rest. An engine refusal still teaches `chat_svc.learned` the real window for the next turn.
* **Capability, not plumbing, limits tiny models**: see 9.6.

### 9.4 Seams for phase C (UI)

* `GET /api/me` -> `ui.modes.work` = `{enabled, reason, code}` (from `services.mode_state`; includes the `work.enabled` switch).
* `GET /api/work/options` -> models, limits (ceilings), `network_allowed`, `perms`, `image_ready`/`image_problem`.
* Session screen = events timeline (poll with `wait=25`) + `GET .../state` (status, container, pending approvals with their `args`, usage)
  + `GET .../files` tree + `GET .../artifacts`. Approvals: `POST .../approvals/{step} {approve}` using `pending_approvals[].step`.
* Statuses: `idle|running|awaiting_approval|paused|failed|cancelled`; `container_state`: `absent|stopped|running|error`.
* Error shape everywhere: `{detail: {code, message}}`; `429 busy` (concurrency), `429 quota` (stored sessions), `413` (uploads, workspace full),
  `422 path` (path refused), `409 busy` (change network while working), `503 image` (Work image missing).

### 9.5 Behaviour worth knowing

* **Concurrency**: one active run per session; a message sent while a run is active is appended to it and read at its next model turn
  (placed where it happened). A message that arrives as the run finishes starts a follow-up run. Per-person (`work.max_sessions_per_user`),
  server-wide (`work.max_running_total`, applied when jobs are claimed) and stored-session (`work.max_sessions_stored`) limits.
* **Restart recovery**: the shared loop resumes a requeued run; a tool that was mid-flight is marked `interrupted` (never replayed), the
  timeline says so, and the model is told. `work_svc.startup(st)` (called from the app lifespan) settles sessions whose job already
  ended (`failed`/`cancelled`, for example after too many attempts) and adopts or cleans containers. `work_svc.sweeper` stops idle
  containers every minute and reconciles every five.
* **Pause** takes effect between steps (the current step finishes); a paused job is parked as `awaiting_approval` with
  `progress_text = "Paused"`. **Cancel** sets `cancel_requested` and kills what the container is running.
* **Reconcile only touches this install's containers**: they carry `semechka.work.instance=<hmac of the master key>` as well as
  `semechka.work=<session id>`; several installs (roasted, salted, staging) share one daemon on the Spark.
* **Quota**: size is measured with `du` before every write/command (3 s cache), a command is refused when the workspace is full unless it
  looks like cleanup (`rm`, `find`, `du`, `ls`, `df`, `git gc|clean`), and a watchdog inside the container kills a command whose
  workspace growth passes 105 % of the limit. A single file is capped by `RLIMIT_FSIZE`.
* **Output**: head and tail of each stream are kept; the result says how many bytes in the middle were left out.
* **Secrets**: the command environment is built from scratch (PATH, HOME, LANG, a few fixed variables); nothing from the Semechka process
  is inherited. Tool errors have host paths replaced by `[host path]`.

### 9.6 Known gaps and caveats (honest list)

* **Rootless Docker cannot isolate an internet-enabled container from the LAN**, and the unprivileged daemon installed for the sandbox
  has no bridge network at all, so `network: internet` fails with the runtime's own message on the Spark (`work.network_name`, default
  `bridge`). The default and tested configuration is `--network none`.
* **A volume's source path is readable inside the container** (`/proc/self/mountinfo` shows `/home/semsbx/.local/share/docker/volumes/.../_data`
  for `/work`). It reveals the daemon account's directory layout, nothing readable (the container cannot open it).
* The approval heuristics for network use and destructive commands are regexes over the command text, not a boundary. The boundary is
  the network flag, the container, and quotas.
* A command that starts a process which escapes its process group with `setsid` survives the end of the command (not a timeout or
  cancel: those kill every process of the container except init).
* The pause/cancel granularity is one model turn: a cancel during a long model call is noticed when that call returns.
* `agent_runs.session_id` has no foreign key (like the other loose references in this schema).
* **Model capability.** With `qwen3-4b-instruct-2507@Q4_K_M` ("Balanced" on staging) the task "first 20 primes -> artifacts/primes.txt" succeeded in
  every run (see the verification record in the release report); a harder multi-file pytest-and-git task did not (the model kept editing the wrong
  file and ended honestly with a failure report). `qwen3.5-0.8b@Q8_0` ("Quick") drives the tool loop but computed the wrong thing (eight primes instead
  of twenty) in 3 of 3 runs, sometimes returns an empty reply (counted by the loop as an invalid action; three in a row ends the run), and once
  answered with `{"tool": "final"}` (a tool-not-found message, which it recovers from). Work Mode does not judge a model's answer: it reports what
  the tools really did.

### 9.7 Rollback

`semechka migrate --to 10` removes the Work tables and data. Workspace volumes live in the container runtime, not in Semechka's
database: list them with `docker ps -a --filter label=semechka.work` and `docker volume ls --filter label=semechka.work`, and remove with
`docker rm -f <name>; docker volume rm <name>` (only the ones labelled `semechka.work.instance=<your instance>`).
