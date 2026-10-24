# Architecture

```
 Browser (SvelteKit SPA, same origin)
    │  cookie session + CSRF header, SSE for streaming
    ▼
 ┌──────────────────────────── semechka process (FastAPI) ───────────────────────────┐
 │ routers: setup · auth · me · chat · admin_people · admin_config · misc             │
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

## Extension points
- Adapters: see ADAPTERS.md.
- Settings: add a `Def` in `settings_reg.py`; it appears in the API, admin Settings page, export/import, search, CONFIGURATION.md (run `scripts/gen_docs.py`).
- Permissions: add to `perms.REGISTRY`; the matrix doc regenerates.
