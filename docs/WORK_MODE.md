# Work Mode: isolated, persistent agent sessions (design and contract)

Status: implemented (0.7.0: backend core 0.6.0, engine tools, delegation and skills, web UI, reconciled). Branch `feature/work-mode-integration`. Written 2026-11-04.

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
| `backend/semechka/work_engine.py` | Phase B: `list_models`, `resources`, `generate_image`, `generate_video`, `job_status`, `wait_job`, `cancel_job`; the bridge to the run's event loop. |
| `backend/semechka/work_delegate.py` | Phase B: `delegate`. |
| `backend/semechka/work_skills.py` | Phase B: the Markdown skills service, front matter parser, `list_skills`, `read_skill`, the built-in organisation skills. |
| `backend/semechka/routers/work.py` | The REST API of section 6, including `/skills`. |
| `work-image/Dockerfile`, `scripts/spark/work_image.sh` | The `semechka-work:1` image and the operator script (build / info / smoke). |
| `scripts/spark/work_integration.py` | Real integration run against the rootless daemon (63 checks). |
| `scripts/spark/work_e2e.py` | A real agent session through the staging HTTP API with a small engine model (timeline, artifact check, other-account probes, cleanup, model unload). |
| `scripts/spark/work_phase_b.py` | Real phase B scenarios on staging (models, image, delegate, OpenJutsu, skill) with real checks of the files. |
| `backend/tests/test_work.py`, `test_work_runtime.py` | Tests with the test double (no container runtime needed). |
| `backend/tests/test_work_contract.py`, `test_work_engine.py`, `test_work_delegate.py`, `test_work_skills.py`, `work_helpers.py` | Phase B tests: the UI contract, the engine tools against the Sarness double, delegation, skills. `fake_sarness.py` gained a registry, resources and scripted chat. |

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
* (Phase A shipped without the Skills API; phase B implements it, see 9.8.)

### 9.3 Seams for phase B (engine tools, delegation, skills)

Implemented in 0.7.0 (section 9.8). The text below is what phase A left, kept for the reasons behind the shape.

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
  `work_svc.emit(st, s, sess, "job", summary, detail, run_id=..., step_n=...)` and appear in `work_svc.state_of(...)["jobs"]`
  (it was `engine_jobs` in phase A; the UI contract calls it `jobs`).
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

### 9.6b Phase B: what the real runs showed (model capability, honestly)

* `qwen3-4b-instruct-2507@Q4_K_M` ("Balanced") as the agent: 12 of 12 sampled runs (3 each of list_models+resources, image, delegation and skill reading, run after the fixes below) reached the right outcome on the Spark (see 9.10); in 6 of the 12 it gave one plain-text reply where the loop wants JSON (counted as an invalid action, recovered on the next turn).
* `qwen3.5-0.8b@Q8_0` ("Quick") as the HELPER works (it answered "42" in about 3 s) once thinking is switched off. As the AGENT it is not reliable: it produced the picture both times,
  but one run repeated the same call 25 times and one ended without a final answer after 40 steps; a delegation run copied the example task from the tool text instead of
  the task it was given (so the argument examples are placeholders now); and a run that looped on `list_models` filled the engine's 8192-token window and failed with the
  engine's own message. Work Mode reports what the tools really did; it does not make a small model competent.

### 9.7 Rollback

`semechka migrate --to 10` removes the Work tables and data. Workspace volumes live in the container runtime, not in Semechka's
database: list them with `docker ps -a --filter label=semechka.work` and `docker volume ls --filter label=semechka.work`, and remove with
`docker rm -f <name>; docker volume rm <name>` (only the ones labelled `semechka.work.instance=<your instance>`).

**Rolling back 0.7.0 to 0.6.0** (no schema change, so no migration step): install the 0.6.0 package over 0.7.0 (on the Spark: `scripts/deploy_spark.sh
releases/semechka-0.6.0.tar.gz staging`; every update keeps a data snapshot under `<instance>/data/pre-update/<time>-from-<version>`). 0.6.0 ignores what 0.7.0
added: the seeded organisation skills and personal skills stay in `work_skills` (the 0.6.0 API has no routes for them), job events and delegate events stay in
`work_events` (0.6.0 skips event kinds it does not know; the 0.6.0 UI is not part of that release). Setting values `work.delegate_*` and `work.engine_wait_s` become
unknown keys and are ignored. To remove the seeded skills as well: `semechka migrate --to 10` (which removes ALL Work data), or delete the `org` skills in 0.7.0 first.
Engine jobs started from Work sessions are ordinary media jobs: they stay in the Library until their retention ends or the person deletes them.


### 9.8 Phase B: engine tools, delegation and skills (0.7.0)

Everything below runs on the host and is bounded by the person's CURRENT permissions, re-read on every call. None of it loads or unloads a
model: the engine decides residency, memory and admission, and what it says is passed on in its own words.

**How a tool reaches the engine.** Tools run in worker threads (`asyncio.to_thread`). `agent_svc.RUN_LOOP` (a context variable set by the run
loop) names the event loop the run lives on; `work_engine.bridge(coro, timeout)` runs engine coroutines ON that loop, so the generation
scheduler and the media block's per-job locks are only ever woken from the loop that owns them. Without a known loop (unit tests) a coroutine
gets a loop of its own.

**Who may use what.** `work_engine.models_of` is the one answer: `services.accessible_models` (grants, availability, egress policy) for the mode
(`work` for chat, `image`, `video`), narrowed by the organisation's allow-list (`gateway_svc.models_for`), and for image/video only when the
person's mode switch is on. `list_models` shows exactly that set; a call naming anything else answers as if the model did not exist (the
message does not say which). Hidden models are neither listed nor callable.

| Tool | What it does |
|---|---|
| `list_models(kind?)` | chat, image and video models the person may use: `ready` and `reason` (the engine registry's readiness, read live), `loaded` (registry residency), `memory_mb`, `vision`, `context`, media `modes`, `defaults`, `limits`; a profile (OpenJutsu) carries its `inputs_schema`. One JSON line per model. |
| `resources()` | `GET /v1/resources` summarised: free and usable unified memory, chat model state, picture/video worker (loaded models), ledger holders, the engine's active jobs (everyone's, no prompts), plus the person's own limits (running of concurrent, started today of daily, Library items of cap). Registry and resources are cached for at most `CACHE_S` = 5 s per backend. No hardware audit. |
| `generate_image(prompt, model?, size?, steps?, seed?, source_file?, out?, ...)` | Defaults to a ready model, preferring a loaded one. Validated by `media_svc.validate_params`. `source_file` is read from the container and uploaded to the engine (models with img2img/edit/inpaint). |
| `generate_video(model, prompt?, inputs{}, out?)` | A plain model: `inputs` width, height, frames, fps, steps, guidance, seed, negative_prompt, `start_image` (a /work path). A profile: `inputs` as the engine's schema says; inputs of type `file` are /work paths, read from the container and uploaded (`media_svc.validate_profile_inputs`: one reference picture means one, required inputs, enum and range checks, unknown keys refused). |
| `job_status(id)`, `wait_job(id, timeout_s)`, `cancel_job(id)` | Only for jobs THIS session started for THIS person (the job id must appear in the session's job events); any other id is "was not started in this session". `wait_job` waits at most `timeout_s` (1 to 600), emits real progress, and when the job completed writes the result like the generation tools do (to the `out` path given at the start). |

*Admission and records are the Video/Image modes'.* `media_svc.admit` (extracted from the route, used by both) applies `ui.<kind>_gen`, no-save rules,
`media.max_concurrent_jobs`, `media.daily_generations`, `media.max_assets` and the retention days; a refusal is returned in its exact text
("Daily limit of N generations reached."). The job is a `MediaJob`; `media_svc.submit` / `submit_profile` hand it to the engine, `refresh` advances
and collects it (the result becomes an encrypted `MediaAsset`, so the Library shows it and retention applies; with 0 retention days the result is held
in memory, exactly as in Video mode), usage events count toward the daily limit. Cancelling the Work run cancels the engine job the run is waiting for.

*Success only with a file.* A generation tool waits up to `work.engine_wait_s` (default 1800, `work_engine.wait_cap`). It reports success only when the engine job
completed AND the file is in the workspace (default `/work/artifacts/<kind>-<job>.<ext>`, or `out`) AND its size matches AND it is listed as an artifact
(then the artifact is marked as already in the Library). Failed or cancelled jobs, a result that is missing, or an artifact over `work.artifact_max_mb`
are errors that say what happened; the engine's failure text is returned with server paths removed. A job still running when the wait ends is NOT an
error and NOT success: the tool says it is still running, that nothing was written yet, and names `wait_job`.

*No duplicates in one run.* A request is named by model, validated settings, `out` and the CONTENT of its input files. Within ONE run an identical request is
not started a second time (a real 0.8B agent called `generate_image` 20+ times with the same arguments, each a job, a daily-quota unit and a Library item): the agent
is told "Not started again", with the file it already has or the job still running. A failed or cancelled job, a deleted file, a different seed, prompt, model or `out`,
and a new message (a new run) all allow a new job.

*Job events.* `{job_id, job_kind, model, status, phase, progress, queue_position?, error?, artifact_id?, out?}`: one event per real change (status, the engine's progress
text, progress in 2 % steps at most once per second). `progress` is a number only when the engine reported one (null otherwise; never invented).
`state_of()["jobs"]` is built from those events joined with the live `MediaJob` rows (so a job that outlives its tool still shows its real state).

**Delegation (`delegate(model, task, files?)`).** A plain chat call through `agent_svc._call_model_once` (scheduler slot, adapter, learned context window)
on a chat model the person may use. Bounds, all in `work_delegate`:
- `work.delegation_max_depth` (default 2) and `work.delegation_max_count` per run (default 8, counted from the run's `Delegating to ...` events);
  0 switches delegation off. The agent's call is depth 1. A helper may consult ONE more model by answering with a single JSON object
  `{"delegate": {"model", "task"}}`, offered only while depth < max (that is how depth 2 happens); the nested answer is handed back to it as data.
- **Refusal rule:** a target that is the model already working (the session model for the agent, the helper for a nested call) is refused when the
  call would be AT the depth limit (`depth >= max`), with the reason; beyond the limit nothing is allowed; a model the person may not use is refused
  like a missing one.
- Reasoning models are asked NOT to think (`thinking: false`) unless the agent passes `think: true`: on the real engine qwen3.5-0.8b spent a 1024-token budget thinking and returned an
  empty answer; with thinking off it answers in about 3 s.
- `work.delegate_timeout_s` (default 300) for the whole call including waiting for memory, `work.delegate_max_tokens` (default 1024) per reply, answers cut at 8000
  characters, at most 4 files (text up to 20 KB each, 60 KB together; at most 3 pictures of 4 MB, sent as `image_url` parts only to a model whose vision access
  the person has; otherwise refused with the reason).
- When the engine says its memory is held by someone else, the call waits and asks again (`agent_svc.BUSY_POLL_S`), puts "Waiting for the engine: <its words>" on
  the `delegate` event, and at the deadline fails with its words. Any other refusal ("The model backend failed: Sarness: ...") is returned verbatim.
- `delegate` events: `{delegate_id (dl_...), model, model_id, task, depth, parent_id?, status running|done|failed, result?, error?, note?, seconds?, tokens_in?, tokens_out?}`.

**Skills.** A skill is Markdown plus optional small text files; it is DATA. `work_skills` implements the contract of section 5/6:
- Fields: `name` (slug `[a-z0-9-]`, 1 to 64, unique per scope: per organisation for `org`, per owner for `user`), `title`, `description` (at most 300 characters; the index shows only name and
  description), `category` (`coding research image-generation video-production media-editing document-creation data-analysis general`), `enabled`, body at most 64 KB, up to 20
  supporting files of at most 1 MB together (UTF-8 text; `SKILL.md` is reserved); body and files are encrypted with the organisation's content key. At most 200 per scope.
- Scopes and permissions: `user` skills are visible and editable only to their owner (anyone else gets the same 404 as for a missing id); `org` skills are visible to every member and
  created, changed, enabled/disabled and deleted only with `work.skills.manage`. A skill cannot move between scopes.
- Front matter: `parse_markdown` reads flat `key: value` lines for `name`, `description`, `category`, `title` (block scalars `|`/`>` too) and ignores every other line, so `network:`, `tools:`,
  `autonomy:`, `limits:` or anything else in a skill (or in the JSON of the API) is dropped, never stored and never read. Nothing is evaluated (no YAML library is involved).
- API: `GET/POST /api/work/skills`, `GET/PUT/DELETE /api/work/skills/{id}`, `POST /api/work/skills/import` (multipart `file`, `scope`), `POST .../enable|disable`; audit entries
  `work.skill.create|update|delete|import|enable|disable`. `/options.skills` lists the person's visible skills with `can_edit`.
- Sessions: `skill_ids` (at most 20) are checked when a session is created or changed: each must be visible to the owner and enabled. The system prompt carries the INDEX of the
  attached, still enabled skills (`- name: description`) and nothing else. `list_skills()` returns that index; `read_skill(name)` returns the body (cut to the tool output limit, with the
  full text at `/work/.skills/<name>/SKILL.md`) and copies the supporting files to `/work/.skills/<name>/`. Only attached skills can be read.
- **Built-in organisation skills (disclosed data).** The first time an organisation lists skills after upgrading, seven `org` skills are created once: `coding`, `research`,
  `image-generation`, `video-production` (plain video models, the OpenJutsu inputs `prompt` = who is replaced, `video`, ONE `refs` picture, `resolution`, `duration`, `start`, `mask_margin`, `seed`,
  and the MiniMax H3 territory note), `media-editing` (ffmpeg and PIL recipes), `document-creation`, `data-analysis`. They are ordinary rows: an administrator may edit,
  switch off or delete them, and a deleted one is not recreated (`meta` key `work_builtin_skills:<org>`). `semechka migrate --to 10` removes them with the table and the marker.

**System prompt.** Short on purpose (small models): one paragraph on models and delegation ("check `list_models` and `resources` before a heavy image or video job, prefer a loaded or small
model, one heavy job at a time, read the engine's refusal and choose another model; delegate for a bounded second opinion or simple step, check its answer; at most N per run, depth D") and the skills index.
The tool list grew by about 2 KB; with a 16K window that is fine, and `fit_steps` already accounts for the system prompt.

**New settings** (deployment scope, `admin.quotas.edit`): `work.delegate_timeout_s`, `work.delegate_max_tokens`, `work.engine_wait_s`. **No schema change** (the `work_skills` table already existed).

**Hardening.** A tool marked work-only (all Work tools, including these) cannot be given to an assistant (`agent_svc.normalize`).

**Caveats of phase B (known gaps and security notes).**
- `resources()` shows the engine's ledger holders (model labels and sizes) and counts other people's jobs by kind and status; ids, models, progress and prompts of other people's jobs are not shown.
  The registry/resources cache (5 s) is shared by everyone on a backend and holds nothing personal.
- Organisation skills reach every member's agent as guidance. They carry no authority (9.8), but a person holding `work.skills.manage` can steer what agents are told to do; the same goes for a
  person's own skills in their own sessions. Treat skill authoring like prompt authoring.
- `delegate` sends the task and the chosen files to the other model. An external model is reachable only when the egress setting allows it (the normal `model_access` rule); otherwise it is neither listed nor callable.
  The helper's answer is data and can be wrong; the system prompt and the tool text say so.
- Session deletion removes the workspace, artifacts and events, not the Library copies of generated media (they follow their own retention) and not source files uploaded to the engine (the engine has no delete endpoint for uploads).
- A cancel is noticed within `POLL_S` (2 s) while waiting for an engine job (and then cancels that job), and when the call returns while a delegation is in flight.
- The duplicate guard is per run and per exact request; it protects quota and the Library, it is not a loop breaker for a model that keeps asking for different things.
- Readiness is read live before a job, but can change between the check and the engine's own admission; then the engine's refusal text is what the agent gets.
- A generation result larger than `work.artifact_max_mb` (64 MB by default) is written to the workspace but reported as an error because it cannot be listed as an artifact.

### 9.9 Reconciling the backend with the UI (decisions)

The UI agent built against section 10; where the phase A backend differed, the BACKEND changed, except for two small UI fixes.

| Topic | Phase A backend | Now (and why) |
|---|---|---|
| Event envelope | `{id, kind, run_id, step, summary, detail, at (epoch)}` | `{id, kind, run_id, step_n, summary, created_at (ISO), data}`; `data` is mapped per kind from the stored payload (older rows too). |
| tool_call / tool_result | no join key; `text`, `exit`, `seconds`, `status` | `call_id` = `<run id>:<step>` (unique across runs), `command`, `output`, `exit_code`, `duration_ms`, `ok`, `truncated`, `error`. |
| artifact | the whole artifact row | `{artifact_id, name, path, mime, size}` |
| approval | request without `status`; the answer `{approved}` | request `status: pending` with `command`, `reason`; the answer appends ANOTHER `approval` event for the same step with `approved` or `denied`; a question left behind by a cancelled or failed run becomes `expired`. |
| state / error | `state` without status; `error` with `why` | `state` always `{status, note}` (status = the session's status at that moment; "Cancelling" keeps `running`); `error` `{message, code}` (`bad_action`, `tool`, `run_failed`, ...). |
| Times | epoch floats | ISO 8601 (UTC, `...Z`) on events, sessions, artifacts, files, skills and jobs. |
| Session | no alias | `model_alias` added. |
| `/state` | `engine_jobs: []`, `active_run {id, job_status, model_calls...}` | `jobs: [{id, kind, model, status, phase, progress, queue_position, error, artifact_id, started_at, finished_at}]` (live), `active_run` adds `step_n`, `max_steps`, `usage {steps, tokens_in, tokens_out, workspace_bytes, workspace_limit_bytes}`, top-level `network`, `autonomy`, `model_id`. |
| `/files` | entries with `mtime`, `type` file/dir/link | `{path, entries[{name, path, type file|dir, size, modified (ISO), mime, link_ok}]}` (a link is a `file` with `link: true`). |
| `/options` | `limits.upload_mb`, `workspace_mb`, `skills: []` | `limits.upload_max_mb`, `workspace_max_mb`, `max_sessions_per_user`, `max_sessions_stored`, `delegation_max_depth|count`, plus the earlier ones; `skills` is real. |
| `/api/me` | no Work entry | `ui.operations.work = {enabled, reason}` (the Work navigation entry). |
| Cancelled session | accepted new messages in practice | pinned by a test: it starts a fresh run. A message sent while a cancel is still being carried out gets 409 `cancelling` (it would otherwise join a run that is ending). |
| Approvals of different runs | UI folded by step number alone | UI fix: folded per run and step. |
| Delegation note | none | optional `note` on `delegate` events; the UI shows it while the delegation runs. |

The tests in `backend/tests/test_work_contract.py` pin these shapes; `web/src/lib/work/types.ts` and the UI tests are the other half.


### 9.10 Verification record (0.7.0)

Backend tests (`cd backend && PYTHONPATH=$PWD python -m pytest -q tests`, `semechka.__file__` resolved to this worktree): the full suite passes, 734 passed and 8 skipped (the skips are the opt-in real-backend tests). New: 63 tests in
`test_work_contract.py` (12), `test_work_engine.py` (26), `test_work_delegate.py` (11) and `test_work_skills.py` (14); `test_work.py` follows the new event shape and its migration test covers the seeded skills.
They use the scripted agent model, the LocalDirRuntime double for the container and `tests/fake_sarness.py` (now with `/v1/registry`, `/v1/resources` and scripted chat) for the engine.
Web: `svelte-check` 0 errors (425 files), `pnpm test` 22 passing, `scripts/design-lint.sh` pass; the 66 mock screenshots were re-run (no page errors or horizontal overflow).

Real runs on the Spark, staging instance 0.7.0 (`http://192.168.20.193:8790`), engine 0.4.0 at 127.0.0.1:8752, driven through the HTTP API by `scripts/spark/work_phase_b.py`,
agent `qwen3-4b-instruct-2507@Q4_K_M`. Before each heavy run the preflight showed no `trinodal-job-*` unit, no engine jobs and the ledger from `GET /v1/resources`; the idle
Schemra/Trinodal service workers were present and the GPU was at 0 %.

| # | Scenario | Result | Evidence |
|---|---|---|---|
| 1 | `list_models` + `resources` in a session | PASS (first run + 3 sampled repeats) | both tools ok; the answer listed the six image models, readiness, loaded state and free memory (for example 99.6 GiB of 113.7 GiB usable). |
| 2 | real image, sdxl-turbo | PASS (first run + 3 sampled repeats) | job `mj_6fe9d933...`: queued, "Starting", "Loading model", completed in 15.2 s (42 s wall); `/work/artifacts/fox.png`, 339,361 bytes, decoded with PIL as PNG 512x512 RGB; artifact listed, copy in the Library, job in `state.jobs`. The engine reported progress numbers only at the end for this fast model. |
| 3 | delegation Balanced to `qwen3.5-0.8b@Q8_0` | first attempt FAIL, then PASS (first rerun + 3 sampled repeats) | The first run returned "Quick returned an empty answer": the 0.8B model had spent its 1024-token budget thinking (confirmed directly against the engine: reasoning 4320 characters, content empty). With thinking off: `Answer from Quick (depth 1, 2.6 s, 119+3 tokens): 42`, events running then done, depth 1. |
| 4 | OpenJutsu from workspace files (src_3s_480p.mp4, ref_woman_yellow_coat.png, 480p, mask_margin 40) | PASS (2 runs) | job `mj_4f841cd7...` 144.9 s engine time, 173 s wall; `artifacts/swap.mp4` 909,011 bytes, ffprobe: h264 864x480 24 fps 56 frames 2.33 s + aac, in the workspace, listed as an artifact and in the Library. Real phases and progress on the timeline (Starting ComfyUI 2 %, TextGenerate, SAM3_VideoTrack 7/56 ... 50/56, MiniMaxH3ReferenceToVideo, SamplerCustomAdvanced 1/6 ... 5/6, VAEDecode, Done). The second run, on the final build and with sdxl-turbo still loaded, `mj_3e53346e...`: 145.7 s, 893,683 bytes, same ffprobe facts. |
| 5 | skill-driven task (video-production attached) | PASS (first run + 3 sampled repeats) | `read_skill` ok; the answer named the OpenJutsu inputs and which are required. |
| - | `qwen3.5-0.8b` as the AGENT | mixed | image: file produced 2 of 2, but 1 run repeated the call 25 times and 1 ran out of steps; delegation 1 of 2 (copied the example task). See 9.6b. After the duplicate guard: one real run started ONE job instead of 20+, then failed on the engine's 8192-token window. |

Cleanup: sessions deleted (containers and volumes removed by the session delete), `POST /v1/models/{sdxl-turbo, qwen3.5-0.8b@Q8_0, qwen3-4b-instruct-2507@Q4_K_M, openjutsu-minimax-h3}/unload`
on the Semechka engine, then `GET /v1/resources` showed `holders: []`, chat model stopped, worker not alive, no ComfyUI process, no engine jobs. The staging Library still holds the generated images and videos.

Not verified: the Work UI in a real browser against staging (only against the mock); cooperative memory eviction (engine side, not re-tested; the second OpenJutsu run happened to start
with a small picture model resident and succeeded); waits longer than 5 minutes inside one tool call on the real engine (the longest was 148 s; the run job's lease is renewed by a heartbeat
on the run's loop, which the tests and the 148 s run exercise but a 30-minute wait was not); `network: internet` sessions; the Docker runtime path of `put`/`get` for a video over 64 MB.

## 10. UI notes (response shapes the web UI is built against)

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
  * `job`: `{job_id, job_kind?: "image"|"video", model?, status: "queued|running|completed|failed|cancelled", phase?, progress?: 0..1, error?, artifact_id?, queue_position?, out?}`.
    Repeated events with the same `job_id` update one card. **A progress bar is drawn only when `progress` is a number.**
  * `delegate`: `{delegate_id, model, task, status: "running|done|failed", result?, error?, depth?, parent_id?, note?}` (`note` is the engine's waiting-for-memory text while running).
  * `artifact`: `{artifact_id, name, path?, mime?, size?}` (the id is the `wa_…` id used by `/artifacts/{aid}/...`).
  * `approval`: `{step, tool, status: "pending|approved|denied|expired", command?, reason?}`. The UI answers with `POST /approvals/{step}`.
    After the answer the backend appends another `approval` event for the same `step` with the new status (done, 9.9); a question left behind by a cancelled or failed run becomes `expired`. The UI folds them per run and step.
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
