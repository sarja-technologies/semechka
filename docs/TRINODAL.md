# Trinodal AI

Trinodal AI is a separate, modular AI system. Semechka works with it in two ways, and both are off until an administrator
configures them:

1. **As a chat model.** Trinodal is registered inside each Sarness Engine as a remote LLM endpoint, so Semechka's Sarness
   backend lists it as `endpoint:trinodal:trinodal` and chats with it through the engine like with any other model.
2. **As a management UI at `/trinodal/`.** Semechka serves Trinodal's own single-page UI, JSON API and event streams on its
   own origin through a reverse proxy, so the browser needs no second address, no CORS and no token.

## Permissions

All three are sensitive: no wildcard (`*`, `trinodal.*`) grants them, each must be granted by name.

| Permission | Allows | In the role templates of a **new** install |
|---|---|---|
| `trinodal.view` | Open the dashboard, Network Explorer, node inspector and evaluations. Read only. | Owner, Platform Admin |
| `trinodal.train` | Training Studio: datasets, jobs, deployment of checkpoints (every change that is not administration). | Owner, Platform Admin |
| `trinodal.admin` | Trinodal configuration, tokens and settings, and the setting `trinodal.url`. | Owner |

Whether someone sees **which nodes took part in a reply** in the technical-details panel is separate and uses the existing
technical-visibility permissions, because it is run metadata about a chat message:

| Part of the run record | Needs |
|---|---|
| `nodes`, `route`, `trained_components`, `llm_calls`, `refused`, `errors` | `tech.node.name` |
| `trace_id` | `tech.job.trace` |
| `language_core` (model and backend) | `tech.runtime.name` |
| `ms` | `tech.perf.duration` |

Nobody else receives any of it, in the live reply, the saved conversation, the thread, or an export.

### Upgrading an existing instance: nobody's access changes

Roles are seeded once, when an instance is created, so starting 0.5.8 on an existing instance **does not add** the new
permissions to anyone, including its Owner and Platform Admin roles. There is deliberately **no migration** that grants
them (the database schema stays at 10), because these permissions open an operational surface and, like
`admin.content.read`, should be handed out by a named, audited act. The way is the ordinary role edit, which is written
to the audit log as `role.update` with the old and new grants:

```
# as an owner, with the session cookie and CSRF token of a signed-in owner
GET  /api/admin/roles                      # copy the role's label, description, rank, grants, denies
PUT  /api/admin/roles/owner                # grants + ["trinodal.view","trinodal.train","trinodal.admin"]
PUT  /api/admin/roles/platform_admin       # grants + ["trinodal.view","trinodal.train"]
```

(Administration → Roles does the same.) An owner may grant sensitive permissions by name; anyone else may grant only what
they hold. Alternatively create a dedicated role (for example "Trinodal operator") and assign it to named people.

## Deployment-time configuration (per instance)

| What | Where | Notes |
|---|---|---|
| Address of the service | Setting `trinodal.url` (Administration → Settings → Trinodal AI, or `PUT /api/admin/settings/trinodal.url` with `confirm_weaker: true`) | An origin only, for example `http://127.0.0.1:8800`. Empty (the default) turns the feature off: `/trinodal/` answers 404 and the navigation entry is hidden. Editable only with `trinodal.admin`, because the service token is sent to this address. |
| Service token | A file on the server, path from the environment variable `SEMECHKA_TRINODAL_TOKEN_FILE`; default `<data dir>/trinodal-token` | One line, printable characters, mode `0600`, owned by the service user. Read on use (replace the file to rotate; no restart). Never stored in the database, never logged, never returned by any API. Without it the proxy answers 503. |
| Instance name | Environment variable `SEMECHKA_INSTANCE_NAME` (optional) | Used in `X-Trinodal-Actor: <instance>/<user id>`. Default: `smk-` plus eight characters derived from the instance's own key, which is stable and unique. Set it to the instance's name (for example `roasted`) so Trinodal's log is readable. |
| Limits | Settings `trinodal.max_upload_mb` (256) and `trinodal.max_download_mb` (512) | Request bodies and responses (live event streams excepted) above these are refused or cut off. |
| Trinodal as a local model | Backend option `local_endpoints` on the Sarness backend: `["trinodal"]` | See below. |
| Who may use the Trinodal model | The ordinary model access rules | See below. |

### Making the Trinodal model usable in chat

1. In the engine, register Trinodal as a remote endpoint (`llm_endpoints`: id `trinodal`, models `["trinodal"]`).
2. Declare it local on the Sarness backend, so the privacy setting "Allow external inference backends" does not block it and
   the interface does not say it runs on an external service:
   `PUT /api/admin/backends/{id}` with the backend's current `name`, `adapter`, `base_url` and `"local_endpoints": ["trinodal"]`
   (or Administration → Backends → Edit → Local endpoints). Omitting the field leaves the list as it is; `[]` clears it. The
   change is audited (`backend.update`, old and new list) and takes effect at once. Only the listed ids are affected; every
   other remote endpoint of the engine stays external. A backend marked external stays external whatever the list says.
3. **Sync models** on the backend. The new model `endpoint:trinodal:trinodal` appears **disabled** with no access rules
   (like every newly discovered model). Its default name is `trinodal` and its developer is shown as "Trinodal AI" with a
   lettermark; rename it under Administration → Models if you like.
4. Enable it and grant it to roles, teams or people (Administration → Models → Access, or `PATCH /api/admin/models/{id}`
   `{"enabled": true}` plus `PUT /api/admin/models/{id}/grants`). Nothing about Trinodal bypasses model access rules.

## What `/trinodal/` does

| Request | Needs |
|---|---|
| `GET`/`HEAD` of the UI and of the read-only API (everything not listed below) | `trinodal.view` |
| `/api/admin/*`, any method | `trinodal.admin` |
| Any `POST`, `PUT`, `PATCH`, `DELETE`, notably under `/api/training`, `/api/datasets`, `/api/jobs`, `/api/checkpoints`, `/api/deploy`, and anything else not recognised | `trinodal.train` |
| `GET /trinodal/_semechka/session` (answered by Semechka, never forwarded) | any `trinodal.*` |
| `GET /api/trinodal/availability` | signed in; answers `{enabled, view, train, admin}` |

Rules enforced for every request, in the backend:

- A Semechka session is required (401 for a signed-out visitor; a page request is redirected to sign-in). The same MFA gate as
  every other route applies. Permission is checked per request class as above (403, with the permission named).
- Every change (`POST`, `PUT`, `PATCH`, `DELETE`) also needs a same-origin request (`Origin`, when sent, must match `Host`) and the
  session's CSRF token in `X-CSRF-Token`, exactly as for `/api/*`. **Contract for Trinodal's UI:** read the token (and the
  scopes the person holds) from `GET _semechka/session` relative to its own base, which answers
  `{"csrf", "scopes", "instance", "user"}`, and send it on every non-GET. `GET /api/me` also returns it.
- Paths are decoded and checked before any decision: `.`/`..` segments, `//`, backslashes and control characters are refused (400),
  so the permission decision and the forwarded request always describe the same resource.
- The request to Trinodal carries only an allow-list of the browser's headers (`Accept*`, `Content-Type`, `Range`,
  conditional and cache headers, `Last-Event-ID`) plus: `Authorization: Bearer <service token>`,
  `X-Trinodal-Actor: <instance>/<user id>`, `X-Trinodal-Scopes: <read,train,admin, as held>` and a fresh `X-Request-Id`. Cookies,
  `Authorization`, `Origin`, `Referer`, the CSRF token, forwarding headers and any `X-Trinodal-*` the browser sent are dropped.
- The response is an allow-list too. `Set-Cookie`, `Content-Security-Policy`, `X-Frame-Options`, CORS, HSTS and server banners
  are dropped, so **Semechka's own CSP and security headers apply unchanged** (`default-src 'self'`, no inline script). Caches are
  told `private` or `no-store`. A redirect is rewritten to stay under `/trinodal/`; one that leaves is replaced by `/trinodal/`.
  Trinodal answering 401 to the service token is reported as 502 `trinodal_auth`, never as the person's session expiring.
- Bodies stream. Event streams are not buffered; closing the browser's connection closes Trinodal's, and so does leaving
  before the first byte. Limits: 32 KiB of response headers, `trinodal.max_upload_mb`, `trinodal.max_download_mb`, 5 s to connect,
  120 s of silence, 15 minutes per ordinary response, 4 hours per stream, 24 requests in flight per person.
- Rate limits per person per minute: 1200 reads, 120 changes (429 `rate_limited`).
- Audit (no bodies, no query strings): every proxied change is `trinodal.proxy` (method, class, path, status, scope); every refusal
  is `trinodal.denied` (method, class, needed permission, reason: `forbidden`, `csrf`, `bad_origin`, `too_large`), at most 20 a
  minute per person. Reads are not audited.

## Trust boundary

Trinodal's UI is served from Semechka's origin, so its JavaScript runs with the same standing as Semechka's own: it can call
every Semechka API as the signed-in person (it can read `GET /api/me`, which includes the CSRF token). The CSP keeps scripts to
`'self'`, which stops injected inline script, but cannot separate Trinodal's own files from Semechka's. Point `trinodal.url` only
at a Trinodal you operate and trust, keep it on loopback or a private network, and keep `trinodal.admin` to people who may be
trusted with that. The service token is a credential for the Trinodal service only; it gives nothing in Semechka.

## What a chat message carries

Trinodal returns run metadata in `usage.trinodal` of the engine's final `done` event: `trace_id`, `nodes` (execution order),
`route`, `language_core` (`model`, `backend`), `trained_components`, `ms`, `llm_calls`, `refused`, `errors`. Semechka keeps only
those fields (bounded in size and type, so nothing like message text can ride along) and only for endpoints listed in
`local_endpoints`. The full record is stored with the assistant message in `stats.trinodal`, like the other measurements, and every
read filters it by the permissions in the table above. The interface shows `Path: conversation → mathematics → conversation`
and the other allowed fields in the technical-details panel, and only when the server sent them.
