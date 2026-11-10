# Work Mode on upstream Goose: API contract (v1)

Status: contract committed 2026-11-10 by workstream `work` (branch `consol/work`). The web workstream codes against this file.
Changes after the first commit are **additive only**; anything that turns out different in reality is corrected in the
"Reality notes" section at the end and in the affected paragraph, with the date.

Work Mode is a **Chat / Work switch inside a conversation**. There is no separate workspace app. When a conversation is in
Work Mode, the agent loop is **Goose** (upstream `aaif-goose/goose` v1.58.1, the unmodified release binary). Semechka does
not contain an agent loop for this path: it starts Goose in a locked container, speaks the Agent Client Protocol (ACP) to
it, relays Goose's events to the browser, relays approval decisions back, and enforces isolation around it.

Everything below is under `/api/work/goose/...`. (The older `/api/work/sessions...` endpoints belong to the previous
custom Work Mode and are unrelated; see section 9.)

## 1. Concepts and identifiers

| Thing | Id | Notes |
|---|---|---|
| Semechka conversation | `cid` (`c_...`) | Unchanged. One Work session per conversation (1:1). |
| Work session | `wid` (`wg_...`) | Created lazily by `POST .../session`. Owned by the conversation's owner. |
| Goose session | `goose_session_id` (e.g. `20261110_1`) | Created by Goose (`session/new`), stored on the Work session row. Resumed with `session/load`. Never shown as an authority: the browser only uses `cid`. |
| Turn | `turn_id` (`t_...`) | One user prompt and everything Goose does until it stops. |
| Tool call | `tool_call_id` | Goose's id, opaque string. |
| Approval | `approval_id` (`ap_...`) | One permission question; answered once. |
| Event | `seq` | Integer, strictly increasing per Work session, never reused. |

The URL parameter is always the **conversation id**; someone else's conversation answers 404 exactly like a missing one.

Authentication/CSRF are the normal Semechka session cookie + `X-CSRF-Token` rules (`deps.current`). Gate (403 with
`{"code": "...", "message": "..."}` when off): `modes.work.enabled` + permission `mode.work` + deployment setting
`goose.enabled`. The feature ships **disabled** (`goose.enabled = false`).

Error body everywhere: `{"detail": {"code": "<machine code>", "message": "<safe text>"}}`.

## 2. Status and options

`GET /api/work/goose/status`
```json
{
  "enabled": true,
  "ready": true,
  "problems": [],                       // human strings when ready=false: "container runtime unreachable", "image missing", "router unreachable" ...
  "goose_version": "1.58.1",
  "models": [                           // ONLY coding_agent_compatible models the person may use
    {"id": "qwen3.8-27b", "label": "Qwen3.8 27B", "context": 65536, "state": "unloaded", "memory_mib": 28000, "default": true}
  ],                                    // state: "loaded" | "loading" | "unloaded"  (router status)
  "autonomy": [{"id": "ask", "label": "Ask before every action", "default": true},
               {"id": "smart", "label": "Ask only for sensitive actions"},
               {"id": "auto", "label": "Never ask"}],          // admin may remove entries
  "extensions": [{"id": "developer", "label": "Files and shell", "builtin": true, "required": true}],
  "mcp_servers": [{"id": "notes", "label": "Project notes", "description": "..."}],   // admin allowlist, selectable per session
  "skills": [{"id": "sk_...", "name": "code-review", "title": "...", "description": "...", "scope": "org"}],
  "limits": {"memory_mb": 4096, "cpus": 2, "workspace_max_mb": 2048, "approval_timeout_s": 900, "idle_stop_minutes": 30}
}
```

## 3. Session lifecycle

All idempotent unless noted.

### `POST /api/work/goose/conversations/{cid}/session`
Create the Work session for the conversation, or return the existing one. Does **not** start any process.
Body (all optional): `{"model": "qwen3.8-27b", "autonomy": "ask", "mcp": ["notes"], "skills": ["sk_..."]}`.
Response `200` = the **session object**:
```json
{
  "id": "wg_...", "conversation_id": "c_...",
  "state": "idle",                      // see states below
  "model": "qwen3.8-27b", "model_state": "unloaded",
  "autonomy": "ask", "mcp": ["notes"], "skills": [],
  "goose_session_id": null,             // set after the first prompt/start
  "container": "stopped",               // absent | stopped | running | error
  "turn": null,                         // {"id": "t_...", "started": 1790000000.0} while a turn is running
  "pending_approvals": [],              // approval objects (section 5) still waiting
  "last_seq": 0,
  "title": "", "created_at": ..., "updated_at": ..., "last_active_at": ...
}
```
Errors: 403 gate, 404 conversation, 409 `model_not_allowed`, 422 `bad_autonomy` / `bad_mcp` / `bad_skill`, 503 `unavailable` (runtime/image).

### `GET /api/work/goose/conversations/{cid}/session`
Same object; `404 {"code": "no_session"}` when the conversation has none (UI: show the Work switch as "start").

### `PATCH /api/work/goose/conversations/{cid}/session`
`{"model"?, "autonomy"?, "mcp"?, "skills"?}`. `model`, `mcp`, `skills` apply from the next **turn** (409 `turn_running` if
changed during a turn); `autonomy` applies immediately (sent to Goose as `session/set_mode`).

### `POST .../session/stop`
Ends the Goose process (and stops the container). Workspace, Goose session and event history stay. Cancels a running
turn first. The next prompt transparently restarts and resumes (`session/load`).

### `DELETE .../session`
Destroys the Work session: container, workspace volume, Goose data, events. The conversation and its chat messages stay.

### States (`state`)
`idle` (no turn, process may be down) | `starting` (container/Goose starting or resuming) | `loading_model` (router is
loading the model: first token can take 30-120 s) | `running` (Goose is working) | `awaiting_approval` | `cancelling` |
`error` (last start/turn failed; see the last `error` event; a new prompt retries) .

## 4. Prompting, cancel, resume

### `POST .../prompt`
`{"text": "Run the tests and fix the failing one", "attachments": ["at_..."]?}` -> `202 {"turn_id": "t_...", "seq": 41}`
(`seq` = last event seq at accept time). Starts container/Goose/model if needed; progress arrives as events. Errors: 409
`turn_running`, 409 `awaiting_approval` (answer or cancel first), 413 `too_long` (20000 chars), 503 `unavailable`.
The prompt text and the final assistant text are mirrored into the conversation as ordinary messages
(`meta.work = {"session": "wg_...", "turn": "t_..."}`), so Chat mode sees what happened; mirroring is part of the same call.

### `POST .../cancel`
Cancels the running turn (ACP `session/cancel`); pending approvals resolve as `cancelled`; running tool processes in the
container are killed. `200` session object (state `cancelling` then `idle`). No-op (200) when nothing runs.

### Resume
Nothing special: after a browser reload, call `GET .../session`, `GET .../transcript`, then open `GET .../events?since=<last_seq>`.
After a **Semechka restart**, a turn that was running is marked `interrupted` (event `turn_end` with
`stop_reason: "interrupted"`); pending approvals resolve as `cancelled`; the next prompt resumes the same Goose session with its
context (Goose persists it in the workspace volume).

## 5. Events

### Transport
`GET /api/work/goose/conversations/{cid}/events?since=N`
* `Accept: text/event-stream` (default for browsers using `EventSource`/fetch streaming): SSE. Each frame
  `id: <seq>`, `event: <type>`, `data: <json>`. `Last-Event-ID` is honoured like `since`. A comment line `: ping` every 15 s.
  The stream replays everything after `since` from the database and then stays open (ends only when the client leaves; it
  never ends because a turn ended).
* `?format=json&wait=20` (or `Accept: application/json`): long poll, `{"events": [...], "last_seq": N, "state": "running"}`;
  returns immediately when events exist, otherwise after `wait` seconds (max 25).
Cookies and CSRF as usual (GET needs no CSRF token). Events are encrypted at rest and visible only to the owner.

### Common envelope
```json
{"seq": 42, "type": "assistant_delta", "ts": 1790000000.12, "turn_id": "t_ab12", ...fields}
```

### Event types
| `type` | Fields | Meaning |
|---|---|---|
| `state` | `state`, `detail?` | Session state changed (section 3 states). |
| `model_loading` | `model`, `phase`: `"loading"\|"ready"\|"failed"`, `message?` | The router is loading the model (long first wait). |
| `turn_start` | `text` | A prompt was accepted (echo, so every client shows it). |
| `assistant_delta` | `message_id`, `text` | Append `text` to the assistant message `message_id`. Coalesced (about 10 per second). |
| `reasoning_delta` | `message_id`, `text` | Model reasoning text (if the model emits any). Show collapsed. |
| `tool_start` | `tool_call_id`, `tool` (e.g. `shell`, `edit`, `write`, `tree`), `extension` (`developer`, MCP name), `title`, `input` (object, values truncated at 4 KB), `kind` | Goose is about to run a tool. |
| `tool_output` | `tool_call_id`, `stream`: `stdout\|stderr`, `text` | Live output chunk of a running tool (shell). Append. |
| `tool_end` | `tool_call_id`, `status`: `completed\|failed\|cancelled`, `output` (text, tail-truncated at 16 KB), `exit_code?`, `locations?`, `diff?` | Tool finished. |
| `approval_request` | `approval` (object, below) | Goose wants permission. **Blocks Goose until answered.** |
| `approval_resolved` | `approval_id`, `decision`: `allow_once\|allow_always\|deny\|deny_always\|cancelled\|timeout`, `by`: `user\|cancel\|timeout\|restart` | Closed (so every open tab updates). |
| `files_changed` | `turn_id`, `files`: `[{"path", "status": "added\|modified\|deleted", "additions", "deletions", "binary"}]`, `truncated` | Workspace diff of the whole turn (git checkpoint, section 7). Full text via `GET .../changes/{turn_id}`. Sent right before `turn_end` when something changed. |
| `usage` | `used`, `size` | Context tokens used / window (Goose's `usage_update`). |
| `title` | `title` | Goose named the session. |
| `turn_end` | `stop_reason`: `end_turn\|cancelled\|max_turns\|refusal\|error\|interrupted`, `usage?` `{input,output,total}` | The turn is over. |
| `error` | `code`, `message`, `retryable` | Something failed (start, model load, protocol, container). Codes in section 8. |
| `notice` | `code`, `message` | Informational (session resumed, container restarted, idle process stopped). |

`diff` on `tool_end` (only for tools whose input carries both sides, e.g. Goose `edit`): `{"path", "unified": "--- a/x\n+++ b/x\n@@ ..."}`.

### Approval object
```json
{"id": "ap_9f..", "tool_call_id": "...", "tool": "shell", "extension": "developer",
 "title": "shell . python3 -m pytest", "input": {"command": "python3 -m pytest"},
 "options": ["allow_once", "allow_always", "deny", "deny_always"],
 "created_at": 1790000000.0, "expires_at": 1790000900.0}
```
`allow_always` / `deny_always` are Goose's own persistent permission levels for that tool (stored in the session workspace),
not a Semechka policy. In `autonomy: "auto"` no approvals are asked at all.

### `POST .../approvals/{approval_id}`
`{"decision": "allow_once" | "allow_always" | "deny" | "deny_always"}` -> `200 {"ok": true}`. `404` unknown/other owner,
`409 already_resolved`. An unanswered request resolves as `timeout` (= deny) after `goose.approval_timeout_s`.

## 6. Transcript, files, changes

* `GET .../transcript` -> folded history for rendering after reload:
  `{"items": [{"kind": "user", "turn_id", "text"}, {"kind": "assistant", "turn_id", "message_id", "text", "reasoning"?}, {"kind": "tool", "turn_id", "tool_call_id", "tool", "title", "input", "status", "output", "diff"?}, {"kind": "approval", ...approval, "decision"?}, {"kind": "files", "turn_id", "files": [...]}, {"kind": "error"|"notice", ...}], "last_seq": N}`.
* `GET .../files?path=` -> `{"path", "entries": [{"name","path","type":"file|dir","size","modified"}]}`.
* `GET .../files/content?path=&download=0|1` (inline only for safe text/image types, nosniff, CSP sandbox).
* `PUT .../files?path=` (multipart `file`), `DELETE .../files?path=`. Size caps from `goose.upload_max_mb`, workspace cap `goose.workspace_max_mb`.
* `GET .../changes/{turn_id}` -> `{"turn_id", "files": [...], "diff": "<unified diff text, capped 512 KB>", "truncated": false}`.

## 7. Where things run, and what they can reach (summary; details in `docs/WORK_MODE_GOOSE_SECURITY.md`)

* One rootless-Docker container per conversation (account `semsbx`, via `semechka-sandbox-relay`), `--network none`, read-only root,
  tmpfs `/tmp`, `--cap-drop ALL`, `no-new-privileges`, memory/CPU/pids limits, user 65534. The workspace is a named volume at `/work`.
  The container has the **real Goose binary** and nothing of Semechka. It has no network at all.
* Goose reaches its model only through a per-session **unix socket** (bind-mounted) that Semechka serves: it forwards
  `/v1/chat/completions` (and `/v1/models`) to the llama.cpp router with a router key the container never sees, pins the model to
  the session's allowed list and rejects everything else.
* Goose runs as `goose acp` (ACP over the stdio of `docker exec -i`). Sessions persist in the volume; `session/load` resumes them.
* Workspace checkpoints (`files_changed`) use a shadow git repository outside the workspace tree (`/work/.home/shadow.git`).
* Admin controls (Settings -> Work Mode (Goose)): `goose.enabled`, model allowlist, MCP server catalog, autonomy choices,
  limits, idle timeouts. Skills are the existing organisation/personal skills, exported as Goose skills.

## 8. Error codes
`no_session` 404 | `turn_running` 409 | `awaiting_approval` 409 | `already_resolved` 409 | `model_not_allowed` 409 |
`unavailable` 503 (runtime/image/router) | `model_load_failed` (event) | `goose_start_failed` (event) | `goose_exited` (event, retryable) |
`protocol` (event) | `workspace_full` (event) | `container_oom` (event) | `turn_timeout` (event) | `limit_sessions` 429 (too many
running sessions; message says which limit) | `disabled`/`forbidden` 403.

## 9. Relation to the old custom Work Mode
The previous implementation (`work_svc.py`, `work_runtime.py`, `work_tools.py`, `work_engine.py`, `work_delegate.py`, `agent_svc.py`'s
run loop, `/api/work/sessions...`) is a separate agent harness. It is left untouched by this change and **not** used by the endpoints above.
See the removal recommendation in `docs/WORK_MODE_GOOSE_SECURITY.md`.

## 10. Reality notes
(Updated honestly as implementation and the Spark run teach us something. Dated entries.)

* 2026-11-10: Verified on the Spark with Goose 1.58.1 over `goose acp` stdio: `initialize` (protocolVersion 1, `loadSession: true`),
  `session/new` returns `modes` (`auto`, `approve`, `smart_approve`, `chat`), `session/update` kinds seen: `agent_message_chunk`,
  `tool_call`, `tool_call_update` (incl. live output in `_meta.toolNotification`), `usage_update`, `session_info_update`,
  `available_commands_update`; `session/request_permission` offers `allow_always|allow_once|reject_once|reject_always`.
* 2026-11-10 (after the Spark run with Goose 1.58.1 and qwen3.6-35b-a3b; additive clarifications, nothing above changed):
  * **Several approvals can be pending at once.** Goose issues parallel tool calls (4 `read_image` calls were seen in one step), each with its own
    `approval_request`. `session.pending_approvals` is a list; the UI must render all of them and answer each independently. `state` is
    `awaiting_approval` while any is open.
  * Tool names seen from the built-in `developer` extension: `tree`, `shell`, `edit`, `write`, `read_image`; an MCP tool arrives as `extension: "<server id>"`,
    `tool: "<server id>__<tool name>"`.
  * `reasoning_delta` is emitted for models that stream `reasoning_content` (qwen3.6 does), at about 5-10 events per second; clients should append and collapse them.
  * After a `deny`, Goose continues the turn (the tool call ends `failed`); the model usually tries another tool. The turn is not ended by a denial.
  * `session.container` may also be `"unknown"` when the container has not been touched since the Semechka process started (no Docker call is made on a plain GET).
  * `tool_end.diff` is present for `edit` calls only; `files_changed` (turn-level checkpoint diff) is the general mechanism and also covers files written by `shell`/`write`.
  * Cancel while a shell tool runs: `turn_end` with `stop_reason: "cancelled"` arrived 0.4 s after `POST .../cancel` and the tool's process was gone.
