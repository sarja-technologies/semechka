# Architecture

```
 Browser (SvelteKit SPA, same origin)
    │  cookie session + CSRF header, SSE for streaming
    ▼
 ┌──────────────────────────── semechka process (FastAPI) ───────────────────────────┐
 │ routers: setup · auth · me · chat · media · catalog · theme · admin_* · misc · sso │
 │ deps: session → Principal (roles, teams, user override)    perms.py (one evaluator)│
 │ settings_reg (typed contract) → settings_svc (scopes, merge, impact, audit, rollback)
 │ services: model access, mode state, technical-visibility filter, quotas             │
 │ crypto.Vault (AES-GCM, HKDF purposes)   audit (hash chain)   telemetry (host/NVIDIA)│
 │ adapters/: sarness · ollama · openai_compat   ── request / stream / cancel ──┐      │
 └───────────────┬───────────────────────────────────────────────────────────────┼─────┘
                 │ SQLAlchemy                                                     ▼
          SQLite (WAL) / Postgres*                          inference servers you operate
          <data>/master.key (0600)                          (llama.cpp, vLLM, Ollama, Sarness Engine)
```
\* Postgres is supported by the schema but unverified (DECISIONS D1).

The four logical subsystems in the brief map as follows: web client (`web/`), control plane (`backend/semechka`), scheduler/node management
(**not built**: single-node direct dispatch only), inference adapter runtime (`backend/semechka/adapters`).

## Request path for a chat message
1. `current()` loads the session, checks expiry/idle, builds a `Principal`; unsafe methods need `X-CSRF-Token`; an Origin mismatch is refused in middleware.
2. `_need_mode()` → `services.mode_state()`: deployment/role/team/user setting `modes.chat.enabled` (a deployment-level off is a hard ceiling), permission `mode.chat`, and at least one model the person may use.
3. `model_access()`: model enabled and available, backend enabled, capability present, a grant for the person's role/team/user with **no** deny, external-egress policy.
4. `_effective_params()`: generation parameters only if `ops.gen.edit_params`; caps from `quota.max_tokens` and per-model limits.
5. `_admit()`: daily message quota and concurrent-generation limit.
6. The adapter streams deltas; Semechka measures TTFT and elapsed time itself and takes tokens/s and token counts from the backend when supplied (labelled `backend` or `measured by Semechka`).
7. The full stats record is stored on the assistant message; **every read** passes through `filter_tech()` so only fields the reader holds a `tech.*` permission for are returned. Fields permitted but not supplied are listed as unavailable, never estimated.
8. Closing the connection cancels the generator; the partial reply is stored with status `stopped` and the concurrency slot is released.

## Data
Tables: users, roles, user_roles, teams, team_members, user_overrides, sessions, invites, email_tokens, setting_values, setting_revisions, backends, models, model_grants,
conversations, messages, usage_events, audit_events, meta. Encrypted columns: `messages.content_enc`, `conversations.title_enc`, `backends.api_key_enc`, `users.totp_secret_enc`,
secret setting values. Audit rows are hash-chained and contain no message text.

## Interface layer (0.3.0)
- **One conversation engine.** Text, vision, picture and video turns are messages in the same tree. Each one is a
  `POST /conversations/{id}/generate` with an `operation`. Text streams over SSE. Picture and video turns create a
  media job (`media_svc`) linked to their assistant message, and the thread returns the job with the message.
  Regenerate and edit create sibling messages for every kind.
- **Model catalogue** (`catalog_svc`, `routers/catalog.py`): ordering, groups, pins and hidden flags live on the server.
  Access rules (`model_grants`) can be limited to modes (chat, code, vision, image, video), and every turn re-checks
  them. The chooser gets only usable models. Provider recognition picks a label and an icon, never a capability.
- **Themes** (`theme_svc`, `web/src/lib/theme`): one token contract in `theme/tokens.json`, copied identically to
  the web client (design-lint fails if they differ). Light, Dark and Graphite are presets. The Custom theme is
  stored as complete token sets, and publishing it refuses any contrast pair below its minimum. The client writes
  the tokens as CSS custom properties. Custom CSS is validated on the server and loaded on chat pages only.
- **Interface flags** (`ui.*` settings, scoped per deployment, role and team) decide what each person's interface
  shows. They are display only; permissions are checked independently on every request.
- **Governed content access** (`routers/admin_content.py`): permission `admin.content.read` (in no role by default and
  never granted by a wildcard), plus the policy `privacy.admin_content_access`, plus a reason. Each list and each
  open is an audit event the person sees at `GET /api/me/content-access`.
- **MFA policy** (`auth.mfa_policy`: off, optional, admins, roles or everyone), with one-time recovery codes. A person
  who must use MFA and has not set it up can reach only their account page. An administrator's reset of someone's
  second factor needs a reason and is audited.

## Trinodal AI (0.5.8)
`routers/trinodal.py` is a streaming reverse proxy from `/trinodal/` to the service at the setting `trinodal.url`, with its
own permission classes (`trinodal.view|train|admin`), the standard CSRF and same-origin rules, a server-side service token and
audit events; `trinodal_svc.py` holds the pure decisions (permission class of a request, header allow-lists, token file).
The Sarness adapter marks models of endpoints listed in its `local_endpoints` option as not external and forwards the
`usage.trinodal` run record, which `services.filter_tech` filters by `tech.*` permission. See [TRINODAL.md](TRINODAL.md).

## Extension points
- Replacement frontend: any static site served by `SEMECHKA_WEB_DIR` on the documented API. See API.md,
  CUSTOM_FRONTEND.md, `sdk/js/semechka.mjs` and `examples/minimal-frontend`.
- Adapters: see ADAPTERS.md.
- Settings: add a `Def` in `settings_reg.py`; it appears in the API, admin Settings page, export/import, search, CONFIGURATION.md (run `scripts/gen_docs.py`).
- Permissions: add to `perms.REGISTRY`; the matrix doc regenerates.
