# Administrator visibility: conversations and media

One governed explorer lets an authorised administrator inspect every conversation and every generated or stored picture and video of **this
Semechka instance**, whichever model or workflow produced it: chat, image mode, video mode, Imajutsu, OpenJutsu (direct and Studio), uploads,
edited and exported copies. It extends the existing governed content access (`routers/admin_content.py`); it does not replace or bypass it.

Admin app: **Conversations & media** (`/admin/content`), tabs *Conversations* and *Media library*.
Code: `backend/semechka/admin_media.py` (service), `backend/semechka/routers/admin_media.py` (API), `routers/admin_content.py` (gate, per-person
list, conversation viewer), `web/src/routes/admin/content/`, `web/src/lib/components/admin/` and `web/src/lib/admin/`.

## What it is not

* Not cross-instance. Roasted (:8787), Salted (:8788) and staging (:8790) are separate Semechka instances with their own database and data
  directory. Each one shows only its own data. There is no federation.
* Not a way around privacy choices. No-save generations (never stored), no-save conversations, expired or deleted items and other people's
  organisations are not exposed (see Retention and Scope).

## Enabling it on an instance

Three things must all be true, otherwise every explorer endpoint answers 403:

1. **Policy.** The deployment setting `privacy.admin_content_access` is `audited` (default `off`). An owner sets it in *Privacy & Retention*, or
   `PUT /api/admin/settings/privacy.admin_content_access {"value": "audited", "confirm_weaker": true}`. Switching it on is itself recorded, and
   changes what the "How is this chat protected?" panel tells everybody. While it is `off` the answer is `403 {"code": "disabled"}`.
2. **Permission, by name.** The administrator holds `admin.content.read`. It is sensitive: no wildcard (`*`, `admin.*`) grants it and it is in no
   role by default. Grant it through a role edit or a per-person override (both audited), e.g.
   `PUT /api/admin/users/{id}/override {"grants": ["admin.content.read"], "denies": []}`.
3. **Reach.** By default an administrator reaches people who belong to an organisation the administrator also belongs to (an organisation-scoped
   administrator). The sensitive permission `admin.content.instance` (also by name, new in this release) widens that to the whole instance: all
   organisations and people who belong to none. It has no effect without `admin.content.read`.

A single-organisation install (every person auto-joined to the default organisation) therefore needs only 1 and 2.

## Security model

| Rule | Enforcement |
|---|---|
| Ordinary users and administrators without the named permission get 403 | `require("admin.content.read")` on every route, server side |
| Policy not `audited` gives 403 `disabled` with the existing message | `admin_media.gate`, first check of every route including the byte route |
| A stated reason (10 to 500 characters) on every list, open, preview, download | request body, validated; 422 otherwise; nothing is audited for a refused call |
| One audit row per list and per item opened, previewed or downloaded; never any content | `admin_media.record`, hash-chained log (`audit.py`); `detail` holds ids, counts, enumerated filters, the reason |
| The owner sees every access | `GET /api/me/content-access` lists the new actions; aggregate lists name each owner shown in `detail.owners` |
| Organisation scope | `Scope`: org-scoped unless `admin.content.instance`; out-of-scope ids answer 404, indistinguishable from missing |
| No secrets | lists are metadata only; job parameters are a whitelist (known generation keys plus the inputs the model's profile declares); keys that look like credentials and token-like values are dropped or replaced by `<removed>`; backends are shown by name only; password hashes, API keys, provider credentials and session data never appear |
| No path traversal | files are addressed by database id only; ids must match `[A-Za-z0-9_]{1,40}`; the resolved path must stay inside `data_dir/assets` or `data_dir/attachments` |
| Rate limit | 240 list/open calls and 1500 file fetches per administrator per minute (in process, like the login limiter); 429 `rate_limited` |
| Organisation audit | an access to one person's item carries that person's organisation (when they belong to exactly one), so the organisation's own audit log shows it |

The audit log never contains message text, titles, prompts or file names. The free-text title query is recorded only as `text_query: true`.

## API

All under `/api/admin/content/explorer` unless stated. Bodies are JSON. Every POST needs `reason`.

| Call | Audit action | Notes |
|---|---|---|
| `GET /filters` | none | People in reach and models, for the filter bars. Directory metadata only (no content, no reason). |
| `POST /conversations` | `content.list` | `{reason, filters, cursor, limit}`. Filters: `user_id`, `model_id`, `mode`, `status` (`active`, `archived`, `expired`), `media` (`image`, `video`, `any`), `q` (title words), `since`, `until` (epoch seconds, on last update). |
| `POST /media` | `media.admin.list` | `{reason, filters, cursor, limit}`. Filters: `user_id`, `model_id`, `kind` (`image`, `video`), `workflow`, `status` (`queued`, `running`, `completed`, `failed`, `cancelled`, `expired`), `conversation_id`, `since`, `until` (on creation). |
| `POST /media/jobs/{id}` | `media.admin.view` | One generation: prompt, whitelisted parameters, workflow details, inputs (source, mask, references, prepared video, chat attachments), outputs, derived final versions, timestamps, status, error. |
| `POST /media/assets/{id}` | `media.admin.view` | A stored item no job produced (upload, edited copy) with its original; or the job of a generated asset. |
| `POST /files/access` | `media.admin.view` or `media.admin.download` | `{reason, type: asset or attachment, id, mode: view or download}`. Returns a link valid 10 minutes, bound to this administrator. |
| `GET /blob/{ticket}` | none (the access was recorded when the link was issued) | Streams the file from the existing encrypted store, with byte ranges (`Accept-Ranges`, 206, 416). Re-checks policy, permission, scope, expiry and file presence on every request. `Cache-Control: private, no-store`, `nosniff`, sandboxing CSP. |
| `POST /api/admin/content/conversations/{id}` | `content.read` | The existing viewer, extended: each message carries `model`, `status`, `media` (its generation job with outputs and thumbnails) and `attachment_items` (attachments and reference pictures). Only the current branch is shown. |
| `POST /api/admin/content/users/{id}/conversations` | `content.list` | Unchanged; now also scoped. |

Pagination is by opaque cursor (`next_cursor`, newest first). Page size is capped (conversations 100, media 60; default 30). A call examines at
most **1500 rows**; if filters that cannot be applied in SQL (workflow, title text) keep it from filling a page, it returns `truncated: true`
with a cursor so the caller can continue.

### Workflow classification

A generation job is classified from data already stored: `imajutsu` (Imajutsu project block), `openjutsu` (Studio block, or a model that
publishes an editing profile), `chat_image` / `chat_video` (started from a conversation), otherwise `image_generation` / `video_generation`.
Stored items no job produced are classified by origin: `upload`, `edit`, `prepared`, `export`, `adjusted`, `scaled`, `composited`.
The operation (`edit`, `swap`, `replace` ...) is shown with each item.

### Thumbnails

Lists return `thumb_url` links for items with a file. They are covered by the list's audit row and by the owner notice; they serve the existing
preview sidecar when one exists, otherwise the original of a small picture (6 MiB or less). No preview is generated or written on an
administrator's behalf, so videos without a stored preview show a placeholder until opened.

## What is and is not searchable

* **Searchable:** conversation titles (decrypted in process for the rows examined, at most 1500 per call), and all metadata: person, model, mode,
  date, status, media type, workflow, conversation.
* **Not searchable:** message text, prompts, negative prompts and file names. They are encrypted at rest; a server-side search would have to
  decrypt every message or prompt of every person, which this feature deliberately does not do. Open the conversation or generation to read it.

## Retention and deletion

* A conversation, job, asset or attachment whose `expires_at` has passed is shown as **expired** (filter `status=expired`) and cannot be read or
  streamed, even before the retention sweep removes the rows; its prompt and parameters are withheld. Links issued earlier stop working (410).
* A file missing from disk is shown as **missing** (410 `gone`). Rows removed by the sweep are simply gone. Nothing is recreated.
* No-save generations store no prompt, parameters or file: they are listed as records and say so.
* Files streamed here are the ones the owner's library uses; nothing is copied.

## What an owner sees

`GET /api/me/content-access` (shown on the account page) lists, with who and the stated reason: "listed your conversations", "listed
conversations that included yours", "opened a conversation", "listed your generated pictures and videos", "opened one of your pictures, videos or
generation records", "downloaded one of your pictures, videos or attachments".

## Tests

* Backend: `backend/tests/test_admin_explorer.py` (authorisation matrix, policy off, reasons, filters, pagination, audit rows without content,
  owner visibility, organisation scope, expired/deleted/missing/no-save media, failed and cancelled jobs, ranges, tickets, traversal, secrets, rate limit).
* Frontend logic: `web/src/lib/admin/explorer.test.ts` (`npm test`).
* Browser: `web/e2e_media/admin-explorer.spec.ts` with `web/playwright.admin.config.ts`
  (`cd web && npx vite build && npx playwright test -c playwright.admin.config.ts`); screenshots in `web/e2e_media/screens/admin-*.png`.

## Known limits

* The rate limiter and the preview links are in process (one control-plane process, like the login limiter); a link survives a restart only
  because it is signed with the instance key, and is valid for 10 minutes.
* Only the current branch of a conversation is shown; earlier versions of a reply are counted, not shown.
* Files uploaded straight with a profile request (rather than picked from the library) are handed to the engine and not kept, so the run's
  inputs list says so.
* In-memory (no-save) results are never exposed.
