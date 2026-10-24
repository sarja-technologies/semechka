# API for interfaces

This is the contract the built-in interface uses. A replacement frontend can use the same calls, either directly or
through the client in [`sdk/js/semechka.mjs`](../sdk/js/semechka.mjs). For how to serve your own frontend, see
[CUSTOM_FRONTEND.md](CUSTOM_FRONTEND.md). There is a working example in
[`examples/minimal-frontend`](../examples/minimal-frontend).

The full machine-readable list of routes, administration included, is the OpenAPI schema. The server serves it at
`/api/openapi.json`, with an interactive reference at `/api/docs`, only when it is started with `SEMECHKA_API_DOCS=1`.
By default it is off, because it lists every route.

`GET /api/capabilities` reports `server.api` (currently `semechka/0.3`). Additions keep that value; a change that
breaks this contract will change it.

## Ground rules

- **Same origin.** The API sends no CORS headers. Serve your frontend from the Semechka server (`SEMECHKA_WEB_DIR`)
  or from the same origin behind your reverse proxy.
- **Session cookie.** `POST /api/auth/login` sets an HttpOnly cookie. Send requests with `credentials: 'same-origin'`.
- **CSRF.** Every request other than GET must carry the header `x-csrf-token`, with the value of `csrf` from
  `GET /api/me`. The token belongs to the session, so read it again after signing in.
- **Errors.** Errors are JSON of the form `{"detail": {"code": "...", "message": "..."}}`. Show the `message` to
  people. Branch on `code`, never on the message text. Common codes: `unauthenticated` (401), `bad_credentials`, `mfa_required`
  (401 at sign-in, 403 when the policy requires a factor not yet set up), `bad_totp`, `csrf`, `forbidden`,
  `disabled`, `model_forbidden`, `no_model`, `unsupported`, `not_found`, `rate_limited`, `quota_exceeded`, `limit`,
  `too_large`, `invalid`.
- **The server decides.** Which models a person may use, which kinds of turn are offered, and what is stored are all
  checked on every request. Hiding something in your interface grants and removes nothing.

## Sign-in and the person

| Call | What it does |
|---|---|
| `GET /api/public` | What a signed-out visitor may know: `brand`, `theme`, `signup` (`open`, `needs_invite`, `requires_approval`), `needs_setup`, `privacy_notice`. Single sign-on buttons come from `GET /api/sso/providers`. |
| `POST /api/auth/login` `{email, password, totp?, recovery_code?}` | Signs in. If two-step sign-in is on for the account, the first try answers 401 `mfa_required`; repeat it with `totp` (the 6-digit code) or `recovery_code`. |
| `POST /api/auth/logout` | Ends the session. |
| `GET /api/me` | The person (`user`, including `prefs`), their interface configuration (`ui`) and `csrf`. 401 when signed out. |
| `PATCH /api/me` `{display_name?, theme?, density?, show_tech?}` | Personal preferences. `theme` is one of `ui.theme.choices`, or `system`. |
| `GET /api/capabilities` | What this server offers and what this person may do: operations, interface flags, features, the active theme. |
| `GET /api/auth/mfa` | Two-step sign-in state: `enabled`, the organisation `policy`, whether it is `required`, recovery codes left. |
| `GET /api/auth/sessions`, `DELETE /api/auth/sessions/{id}` | The person's own sessions. |

`ui` in `/api/me` is what an interface needs to adapt instead of guessing:

- `ui.operations`: for each of `text`, `code`, `vision`, `image` and `video`, `{enabled, reason}`. A disabled
  operation includes the reason to show.
- `ui.ui`: interface flags the administrator set per role or team, for example `folders`, `search`, `pinning`,
  `model_chooser`, `model_meta` and `tool_indicators`. Respect them so your interface matches what the
  organisation chose.
- `ui.composer`: `attachments`, `ephemeral` (no-save chats allowed), `branching`, `export`, `delete` and `max_chars`.
- `ui.theme`: `default`, `active`, `choices`, `follow_system`, `user_choice`, and `custom` (the organisation's Custom
  theme, when it has one).
- `ui.brand`: names, logos (asset ids), header layout, welcome title and text, footer.
- `ui.retention`: `{days, text, show, place}`. Show retention only when `show` is true; `text` is generated from
  the real policy.

## Models

`GET /api/models/catalog?operation=text|code|vision|image|video` lists the models this person may use for that kind
of turn, in the administrator's order:

```json
{ "operation": "text", "default": "m_...", "pinned": ["m_..."], "show_names": true,
  "groups": [{ "id": "g_...", "name": "Fast", "description": "", "collapsed": false, "models": ["m_..."] }],
  "ungrouped": ["m_..."],
  "models": [{ "id": "m_...", "alias": "Quick", "pinned": false, "capabilities": ["chat", "vision"], "external": false,
               "profile": { "provider": "...", "family": "...", "params": "7.6B", "icon": { "mark": "Qw", "tone": "#3D5A80" } },
               "media_modes": [], "loaded": null }] }
```

A disabled operation answers 403 `disabled` or `forbidden`, or 409 `no_model`, each with a message to show. The
`profile` fields follow the administrator's display flags; unknown details are absent rather than invented. For icons
see [MODEL_ICONS.md](MODEL_ICONS.md).

`GET /api/chat/models` (the 0.2 list) still works.

## Conversations

| Call | What it does |
|---|---|
| `GET /api/chat/conversations?q=&archived=&mode=chat\|code&limit=&before=&folder_id=` | Newest first; pinned rows first on the first page. To page, pass `before` = the last row's `updated_at`. |
| `POST /api/chat/conversations` `{model_id?, mode?, assistant_id?, collection_ids?}` | Creates an empty conversation. |
| `PATCH /api/chat/conversations/{id}` `{title?, pinned?, archived?, folder_id?, model_id?, ...}` | Rename, pin, archive, move. `folder_id: ""` takes it out of its folder. |
| `DELETE /api/chat/conversations/{id}` | Deletes it with every version, attachment, and picture or video made in it. |
| `GET /api/chat/conversations/{id}/thread?limit=40&before=` | The visible branch: the newest `limit` messages, or those before message `before`. |
| `POST /api/chat/conversations/{id}/branch` `{message_id, limit?}` | Shows another version; the thread continues to the newest reply under that message. |
| `GET /api/chat/conversations/{id}/export?format=md\|json` | Export (needs the export permission and setting). |
| `GET/POST /api/chat/folders`, `PATCH/DELETE /api/chat/folders/{id}` | Folders. Deleting one keeps its conversations. |

A thread is the conversation row plus `leaf`, `total`, `start`, `has_more` and `messages`. A message:

```json
{ "id": "...", "parent_id": "...", "role": "assistant", "kind": "text", "content": "...", "status": "complete",
  "created_at": 1790000000.0, "model": "Quick", "model_id": "m_...", "meta": {}, "tech": {},
  "attachments": [], "sources": [], "media": null,
  "siblings": { "index": 0, "count": 2, "prev": null, "next": "..." } }
```

- `status` is `complete`, `stopped`, `error` or `pending`.
- `siblings.index` is zero-based: show `index + 1` of `count`.
- `kind` is `image` or `video` for a reply that made media; `media` then holds the job (see below).
- `tech` holds only the technical fields this person has been granted. It is empty for most people.

## Sending a turn

`POST /api/chat/conversations/{id}/generate` takes:

```json
{ "mode": "send | regenerate | edit", "operation": "text | image | video", "content": "...",
  "parent_id": "...", "model_id": "...", "attachments": ["..."], "params": {}, "media": {}, "source_id": null }
```

- **send:** `parent_id` is the last visible message (null for the first).
- **regenerate:** `parent_id` is the assistant message to replace. The old version stays reachable through `siblings`.
- **edit:** `parent_id` is the user message to replace, and `content` is the new text. This starts a new branch with a
  new reply.

For `operation: "text"`, the answer is a stream of Server-Sent Events: one JSON object per `data:` line, with events
separated by a blank line.

| `type` | Fields | Meaning |
|---|---|---|
| `start` | `assistant_id`, `user_message_id` | Stored ids of the new messages. |
| `queue` | `position`, `running` | Waiting for a free slot on the engine; `running: true` means it has started. |
| `sources` | `sources`, `skipped` | Knowledge passages used for this reply. |
| `trimmed` | `dropped` | Older messages left out to fit the model's context. |
| `delta` | `content`, `reasoning` | Text to append. |
| `error` | `message` | The reply failed; the message is safe to show. |
| `done` | `status`, `content`, `tech`, `message`, `user_message` | Final. `message` and `user_message` are the stored messages, as `/thread` would return them. |

To stop, abort the request. The server stores the text so far with status `stopped`.

For `operation: "image"` or `"video"`, the answer is plain JSON (`{message, user_message}`) as soon as the job is
queued. `message.media` is the job. Poll `GET /api/media/jobs/{job_id}` until `status` is `completed`, `failed` or
`cancelled`, then show each entry of `assets` from `GET /api/media/assets/{asset_id}/content`. Cancel with
`POST /api/media/jobs/{job_id}/cancel`. To edit or animate an existing picture, pass its id as `source_id`. Limits and
sizes per model come from `GET /api/media/options?kind=image|video`.

### Attachments

`POST /api/chat/attachments` (multipart field `file`; add `?ephemeral=true` for no-save chats) answers with
`{id, name, kind, mime, size, width, height}`. Pass the ids in `attachments`. Images are read by models that can see,
and a model that cannot is refused with a message rather than ignoring the image. `GET
/api/chat/attachments/{id}/content` serves an attachment, and `DELETE` removes one that has not been sent.

### No-save chats

`POST /api/chat/ephemeral/generate` `{messages: [{role, content, attachments?}], model_id}` streams like `generate`.
Your interface holds the transcript and sends it each time; nothing is written to the conversation tables. It is only
offered when `ui.composer.ephemeral` is true.

## Privacy

| Call | What it does |
|---|---|
| `GET /api/chat/protection` | "How is this handled?": statements generated from the live configuration. |
| `GET /api/me/export` | The person's data: account, preferences, every conversation (all branches), folders. |
| `GET /api/me/content-access` | Every time an administrator listed or opened this person's conversations, with who and why, plus the policy. |
| `DELETE /api/me` | Deletes the account, when the organisation allows it (`can_delete_account` in `/api/me`). |

## Theme

`GET /api/theme/tokens` is the design-token contract: token ids and groups, the Light, Dark and Graphite presets, the
contrast pairs, and the fonts. Paint the active theme as CSS custom properties named after the token ids (`--bg`,
`--fg`, `--accent`, ...). `Semechka.resolveTheme()` in the SDK applies the same rules as the built-in interface:

- A person's own Light or Dark choice is kept as chosen.
- "Match device", and the organisation default when the organisation turns on "follow the device", follow the
  device's setting.
- Custom switches between its two versions with the device.

## Administration

Every administration screen uses the routes under `/api/admin/`. They are guarded by the same permissions as the
screens, and every change is audited. Use the OpenAPI schema (`SEMECHKA_API_DOCS=1`) as the reference. Some
behaviour to know:

- `PUT /api/admin/settings/{id}` `{value, scope_type?, scope_id?, confirm_weaker?}`: a change that weakens privacy or
  security answers 409 `confirm_required` until it is repeated with `confirm_weaker: true`.
- The theme editor uses `PUT /api/admin/theme/draft`, `POST /api/admin/theme/publish` (refuses any colour pair below
  its contrast minimum), `POST /api/admin/theme/revert`, and the import and export routes.
- Access to other people's conversations (`/api/admin/content/...`) needs a permission that no role has by default,
  a privacy policy that allows it, and a reason of at least 10 characters. Every access is recorded and shown to the
  person.
