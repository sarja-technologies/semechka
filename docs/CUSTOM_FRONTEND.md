# Replacing or extending the interface

There are three levels, from least to most work:

1. **Theme and branding** (Administration → Theme, Branding, Interface). Colours, logos, names, the welcome text, and
   which parts of the interface each role or team sees. Nothing to build or deploy.
2. **Custom CSS** (Administration → Theme → Custom CSS). Small adjustments to the chat interface.
3. **A replacement frontend**: your own interface on the same API.

## Custom CSS: what it can and cannot do

The CSS is checked on the server before it is saved (`theme_svc.validate_css`). The server refuses:

- `@import`, `@font-face`, `@namespace` and `@charset`;
- `expression()`, `javascript:`, `vbscript:`, `-moz-binding` and `behavior:`;
- angle brackets and backslash escapes;
- unbalanced braces, and more than 20,000 characters.

`url()` may only point at an image uploaded under Branding (`/api/assets/…`). Remote URLs would reveal every viewer
to another server.

The CSS applies to the chat interface only. It is removed on administration and sign-in pages, so a mistake can never
hide the way back or a security control.

There is deliberately no way for an administrator to add JavaScript to the built-in interface. Script in the shared
page would run with every person's session. If you need behaviour, build a frontend (below), where your code is
reviewed and deployed like any other code.

## A replacement frontend

The server serves any static site from a directory you choose:

```bash
SEMECHKA_WEB_DIR=/srv/my-interface semechka serve
```

- Files in that directory are served as they are. Any other path that is not under `/api/` answers with your
  `index.html`, so client-side routing works.
- The server's Content-Security-Policy allows scripts from the same origin only. Inline `<script>` blocks in
  `index.html` are allowed by hash, computed when the server starts, so restart it after changing them. Inline script
  in any other file is blocked. Keep your code in `.js` files.
- The API is same-origin only and has no CORS. If you host the interface elsewhere, put it behind the same origin as
  the API with your reverse proxy.
- Signing in, CSRF, permissions, retention and audit all stay on the server. Your interface cannot weaken them.

### Start from the example

[`examples/minimal-frontend`](../examples/minimal-frontend) is a complete small interface, three files plus the SDK.
It signs in (with two-step codes), lists conversations, offers the person's models in the administrator's groups and
order, streams replies with Stop, makes new versions of a reply and moves between them, and paints the organisation's
theme. It sets all text from the server with `textContent`, so nothing a model writes can become markup.

```bash
mkdir -p /srv/my-interface
cp examples/minimal-frontend/* sdk/js/semechka.mjs /srv/my-interface/
SEMECHKA_WEB_DIR=/srv/my-interface semechka serve
```

### The SDK

[`sdk/js/semechka.mjs`](../sdk/js/semechka.mjs) is one dependency-free ES module with JSDoc types, so editors and
TypeScript (`checkJs`) understand it. It covers sign-in, the person and their capabilities, the model catalogue,
conversations and versions, streamed turns with stop, pictures and videos in the conversation, attachments, no-save
turns, exports and the theme. Every method maps to one documented call in [API.md](API.md). Use `request()` for
anything else.

```js
import { Semechka } from './semechka.mjs';
const api = new Semechka();
const me = await api.me();                          // 401 SemechkaError when signed out
const cat = await api.catalog('text');               // only models this person may use
const conv = await api.createConversation({ modelId: cat.default });
const run = api.send(conv.id, { content: 'Hello', modelId: cat.default, parentId: null },
  { onEvent: (e) => e.type === 'delta' && output.append(e.content) });
const done = await run.done;                         // run.stop() ends it; the reply is stored as stopped
```

### Adapting to the organisation's choices

Read `GET /api/me` (or `/api/capabilities`) and follow it:

- **`ui.operations`:** offer only the kinds of turn that are enabled, and show the given reason for the others.
- **`ui.ui`:** each flag is the administrator's choice per role or team (folders, search, model chooser, model
  details, ...).
- **`ui.composer`:** attachments, no-save chats, branching, export and delete.
- **`ui.retention`:** show retention only when `show` is true, and use its `text`, which comes from the real policy.
  Never write your own retention claim.
- **`ui.brand` and `ui.theme`:** names, logos and the token values to paint.

### Upgrades

The built-in interface ships with each release. A replacement frontend does not, so pin the release you tested
against. `GET /api/capabilities` reports `server.api`; a change that breaks this contract will change that value.
Fields are added within a contract version but not removed. The 0.2 names still answer: `ui.nav` and
`GET /api/chat/models`.
