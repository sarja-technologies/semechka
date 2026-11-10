# Work Mode on Goose: feasibility, isolation design, deployment, evidence

Companion to `docs/WORK_MODE_GOOSE.md` (the API contract). Written 2026-11-10 by workstream `work`.

## 1. Verdict

Integrating upstream Goose (v1.58.1, the unmodified release binary) as Semechka's Work Mode agent **is feasible through its
maintained API** (the Agent Client Protocol), and it is implemented and was run end to end on the Spark (section 5). Semechka
contains no agent loop for this path: no prompt assembly, tool selection, tool execution or model-call loop. Goose does all
of that. Semechka's code is the host around it: containers, a model gateway, an ACP transport client, an event relay, approvals,
limits, persistence and audit.

The feature ships **disabled** (`goose.enabled = false`).

## 2. What upstream offers, and what was chosen

| Option | Verdict |
|---|---|
| `goose serve` (ACP over HTTP + WebSocket, `GOOSE_SERVER__SECRET_KEY`) | Evaluated, **not used**. One server process serves all sessions in one OS context; its own docs say permission callbacks "are not a sandbox: its tools run with the backend's access to the machine". Per-user isolation would have to come from file permissions on a shared account. In a rootless container runtime the HTTP port is also unreachable from the host (container networks live inside the daemon's namespace; the daemon cannot publish ports of a `--network none` container). |
| `goose serve` as `semsbx` with per-conversation directories + Goose permission system | Rejected for the same reason: no isolation between people; `semsbx` would hold every person's workspace and could read the model sockets. |
| **`goose acp` inside a per-conversation rootless-Docker container, ACP over the stdio of `docker exec -i`** | **Chosen.** It is the documented "Build an app with goose" path (the same ACP, a different transport). Isolation is enforced by the container runtime, not by Goose's permission mode. |
| Goose REST server | Goose's HTTP surface is ACP (`/acp`) plus `/status`/`/health`; there is no separate REST agent API in v1.58.1. |

ACP facts verified against the real binary (`initialize` protocolVersion 1, `loadSession: true`, modes `auto|approve|smart_approve|chat`,
`session/new|load|prompt|cancel|set_mode`, `session/update` kinds, `session/request_permission` with
`allow_always|allow_once|reject_once|reject_always`) are recorded in the contract's "Reality notes".

## 3. Isolation design

Threat: user A (or an LLM acting on A's behalf, or a hostile file in A's workspace) tries to read or modify B's workspace or
Semechka's data, reach `/home/spark`, use the LAN or the internet, or obtain the router key.

```
browser --HTTPS/cookie--> Semechka (service user, ports 87xx)
                              |  ACP JSON-RPC over `docker exec -i` stdio          \
                              |  (Semechka owns the pipe; Goose cannot open anything on the host)
                              v
        rootless Docker daemon (account semsbx, user namespace) via semechka-sandbox-relay
          container gwork-<session>  --network none  --read-only  --user 65534  --cap-drop ALL  no-new-privileges
                                     memory/cpu/pids limits, tmpfs /tmp, volume only for THIS session at /work
             goose acp  (HOME=/work/.home, cwd /work)  --HTTP--> 127.0.0.1:8081 (llm_forward.py, 40 lines, bytes only)
                                                                      |  unix socket /run/llm/llm.sock (bind mount of ONE directory)
                              Semechka model gateway (this process) <-+
                                |  attaches the router key, pins the model, allows two requests only
                                v
                       llama.cpp router (127.0.0.1:8080)
```

* **Per-conversation container, volume and socket.** Nothing is shared between sessions: another session's volume or socket
  directory is not mounted, so there is no path to it. Names are derived from the unguessable session id.
* **No network at all** (`--network none`; only loopback). The router is reached through the bind-mounted unix socket, never
  through a network. Rootless Docker cannot restrict a networked container to one destination (in this install even
  `--network host` shares the real host namespace, which is why no Goose container ever gets a network of any kind).
* **The router key never enters the container.** The gateway adds it; any `Authorization` header from inside is dropped. The
  environment of the container carries only a dummy value Goose's provider configuration requires.
* **The gateway is not an agent.** It forwards `POST /v1/chat/completions` and answers `GET /v1/models` locally (allowed models
  only). The `model` in each request must equal the session's pinned model, so editing Goose's configuration inside the container
  gains nothing. Other paths: 404, other methods: 405, bodies over 16 MB refused, 4 requests at once.
* **Models are restricted** to `coding_agent_compatible: true` entries of `models.verified.json`, then to what the router serves,
  then to what the person may use (Semechka model grants; people holding `models.use_all` also see verified models that are not
  in the catalogue). Optional narrowing: setting `goose.models`.
* **Semechka data is unreachable**: the container has no host mounts besides its own volume and socket directory, no Docker
  socket, no shared kernel interfaces beyond a default container's. `/home/spark` does not exist inside it (section 5 shows the
  attempt). Nothing in the container runs as the service user: processes inside are uid 65534, which the daemon maps to a sub-uid of
  `semsbx`.
* **Approvals**: `ask` (Goose mode `approve`) asks for every tool call, `smart` (`smart_approve`) for sensitive ones, `auto` never.
  Approvals are Goose's own `session/request_permission`; Semechka relays them and enforces a timeout (deny). They are a consent
  mechanism for the person, not the security boundary; the container is.
* **MCP servers** come only from the administrator's allowlist (`goose.mcp_servers`: id -> command inside the image). Sessions
  choose ids; they cannot supply commands. They are passed to Goose in `session/new` / `session/load` (`mcpServers`).
* **Skills** are the existing Work skills (organisation / personal), exported per session into `~/.agents/skills/<name>/SKILL.md`,
  which is where Goose discovers them. Only the skills chosen for the session, and only ones that person may see, are exported.
* **Limits** (admin settings): memory, CPUs, pids, workspace size, prompt length, steps (`GOOSE_MAX_TURNS`), prompt wall-clock,
  running sessions per server, stored sessions per person, idle stop (process and container are stopped, workspace is kept).
* **Audit** (hash-chained log): session create/update/stop/delete, each prompt (length and model, never text), each approval decision
  (tool, extension, decision), cancel. Content is never written to the audit log.
* **Existing permissions**: `modes.work.enabled` + `mode.work` + `goose.enabled`; the URL key is the conversation id and ownership
  is checked on every route (someone else's conversation answers 404, like a missing one).

### Honest boundaries

* Namespaces, cgroups and seccomp on a shared kernel behind an unprivileged daemon; not a virtual machine. A kernel or runtime
  escape would land in account `semsbx`, which owns no Semechka data, keys or credentials.
* The agent has full control of its own `/work` and `/tmp`, can burn its CPU and memory quota, and can write anything it likes there
  (including prompt-injection text for the next turn). It can also edit Goose's configuration in its volume; the gateway makes that harmless.
* Per-session socket files are mode 0666 inside a directory the container user can traverse (sub-uids cannot otherwise connect). A
  second local account on the Spark that learns a session's socket path could use that session's model allowance; the parent directory is 0711 and the
  names are unguessable. For production, create the directory under an account-private path with an ACL for `semsbx` (section 6).
* Model output from a verified model is still model output: it can be wrong, and `auto` mode lets it act inside its container without asking.
* Goose phones home nowhere (no network); telemetry is disabled by environment as well.

## 4. What the router needs from the orchestrator

The gateway reads a router key from a file (`goose.router_key_file`). **Recommended:** issue a second key for Semechka's Work Mode
(the router supports several `--api-key` values / key files), so it can be revoked without touching the admin key. Until then the
dev run used the existing router key. No router configuration was changed by this workstream.

## 5. Evidence

See `docs/WORK_MODE_GOOSE_EVIDENCE.md` (real run on the Spark; event logs and diffs are attached under `docs/evidence/work-goose/`).

## 6. Deployment checklist for the orchestrator

1. Build the image on the Spark as the service user: `scripts/spark/goose_image.sh build` (uses `~/.local/bin/goose`, v1.58.1; image `semechka-goose:1`;
   builds on `semechka-work:1`, which must exist).
2. Per-instance settings (Admin -> Settings -> Work Mode (Goose)): `goose.enabled=true`, `goose.router_url=http://127.0.0.1:8080`,
   `goose.router_key_file=<file>`, `goose.verified_file=/home/spark/spark-llm/models.verified.json`, `goose.docker_host=unix:///run/user/1000/semechka-sandbox.sock`,
   `goose.default_model=<id>`; also `modes.work.enabled=true` and the `mode.work` permission for the groups that should have it.
3. **systemd hardening conflicts** (staging/Roasted/Salted units): `PrivateTmp=true` hides `/tmp` from the container daemon, and `ProtectHome=read-only`
   blocks the socket directory. Choose a directory that the daemon account can traverse and the service can write, e.g.
   `setfacl -m u:semsbx:--x /home/spark /home/spark/semechka-deploy` plus `mkdir -m 0711 /home/spark/semechka-deploy/goose-sock`
   and `ReadWritePaths=/home/spark/semechka-deploy/goose-sock`, then set `goose.sock_root` to it. (The dev run used `/tmp` because it had no `PrivateTmp`.)
4. The service must be able to read `models.verified.json` and the key file (`ProtectHome=read-only` allows reads).
5. Rollback: set `goose.enabled=false` (immediate). The tables `work_goose_sessions` / `work_goose_events` are additive and unused by older releases;
   containers are labelled `semechka.goose` and can be listed/removed with `docker ps -a --filter label=semechka.goose`.

## 7. The older custom Work Mode agent loop: what remains, what to remove

Still in the tree, untouched and **not used** by `/api/work/goose/*`:

| Part | Size | Recommendation |
|---|---|---|
| `work_svc.py` (sessions, run loop glue, approvals, artifacts, reconcile) | 1285 lines | Remove after the web switches to the Goose endpoints. Move the three helpers Goose uses (`enc`, `dec`, `user_org`, `instance_id`) into a small neutral module first. |
| `work_engine.py`, `work_delegate.py`, `work_prompt.py` (Sarness engine tools, delegation, prompt assembly) | 1225 lines | Remove: this is the inferior-harness part (and depends on the Sarness Engine being retired). |
| `work_tools.py` (custom tool registry for the old loop) | 550 lines | Remove, keeping `norm_path` and `guess_mime` (used by the file routes). |
| `work_runtime.py` (container runtime + in-container file helper) | 1095 lines | **Keep**: `goose_runtime.py` builds on it (volumes, file listing/upload/delete, limits). |
| `work_skills.py` + `WorkSkill` + `/api/work/skills*` | 516 lines | **Keep**: shared with the Goose path (skills are exported to Goose). |
| `routers/work.py` session/message/approval/file/artifact routes | ~330 lines | Remove with the web switch; keep the skills routes (already split into `skills_router`). |
| `agent_svc.py` run loop (1400 lines) | | Keep: it also drives cloud Assistants/agents; only its Work-specific tool registrations go away with `work_tools.py`. |
| Tables `work_sessions/messages/events/artifacts` | | Keep the data (never drop user data); stop writing; offer export, then drop in a later release. |
| Tests `test_work*.py` (3464 lines) | | Delete with the code they test. |

Settings `work.*` (old) can be hidden once the old routes are gone; `modes.work.enabled` and `mode.work` are shared and stay.
