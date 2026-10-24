# Design review: user-facing screens (fork A)

Method: real Chrome via the Claude-in-Chrome tools against a live backend (port 8781) backed by a real llama.cpp server (Qwen2.5-0.5B). The browser window could not be resized, so 375 px and 768 px were checked by loading the app in fixed-width iframes of the same origin (screenshot scaled ~0.74). Pass/fail is what was observed, not a judgement of beauty.

| Screen | Viewport / theme | Check | Result |
|---|---|---|---|
| Setup wizard (token, identity, preset, hardware, models, review, launch) | desktop, Paper | Completes end to end; real backend test; real smoke reply "ready"; owner session created | PASS |
| Setup wizard | 375 / 768, Carbon | Not screenshotted | NOT CHECKED |
| Sign-in | desktop Paper | Generic error "Email or password is incorrect."; no signup link when closed; invite-mode shows invitation link | PASS |
| Sign-in MFA step | any | Code step implemented; not exercised in the browser | NOT CHECKED |
| Sign-up open (invite) / closed | desktop Paper | Closed shows "Accounts are created by your administrator"; open shows invitation field | PASS |
| Chat, empty | desktop Paper, 768 | Layout, protection badge shows "Stored for 30 days" | PASS |
| Chat, streaming + markdown + code highlight | desktop Paper | Real streamed reply, highlighted code, copy/regenerate | PASS |
| Chat branching (regenerate, edit, 1/2 arrows) | desktop | New sibling per regenerate/edit; arrows switch version | PASS |
| Chat stop | desktop | Partial text kept, "Stopped" chip, composer returns to Send | PASS |
| Chat, Technical details (Li Wei) | desktop Paper | Only granted fields shown (no family/runtime/node); live GPU row from real nvidia-smi; no controls | PASS |
| Chat, member without tech grants | desktop Paper | No drawer button, no technical chips | PASS |
| No-save chat | desktop Paper | Panel shown before first send; DB has no conversation and no message text; usage row marked ephemeral | PASS |
| Chat with mode disabled | desktop Carbon | Chat removed from nav; page shows server reason | PASS (page-level message added after the screenshot; rebuilt and type-checked, not re-screenshotted) |
| Unavailable mode page | desktop Paper | Shows "Not available in this release." | PASS |
| Chat mobile (375) | iframe | Bottom bar with 4 items, list becomes sheet, composer fits | PASS; model select is truncated to "qwen2.5-" (minor) |
| Account and security | desktop Paper | Renders; password, MFA, sessions, delete sections present | PASS (MFA enrolment, password change, delete not exercised in the browser) |
| Usage, Help | - | Built, not screenshotted | NOT CHECKED |
| Carbon / Graphite | desktop | Carbon checked on chat-disabled screen only; Graphite not checked | PARTIAL |
| Keyboard shortcuts (`/`, `g c`, Esc) | - | Esc-to-stop and Enter-to-send exercised; `/` and `g` chords not exercised | PARTIAL |
| Focus visibility, reduced motion, contrast | - | Relies on app.css tokens; no automated axe/contrast run | NOT CHECKED |
| Slop scan | - | grep for `gradient(`, `backdrop-filter`, purple-range hex in owned files: no matches | PASS |

Known gaps: no automated accessibility run; no offline-state screenshot; wizard not checked below desktop width.

## Admin console (fork B) — checks run against a live backend (port 8782) and Vite dev (5182)

Method: Chrome driven through the Claude-in-Chrome tools. The browser window could not be resized, so 375 px layouts were checked in a 375-wide same-origin iframe (scroll-width measured numerically). Screenshots were viewed, not stored.

| Check | Result |
|---|---|
| Overview shows only real counts, audit-chain status, setup hints | pass (desktop 1288 px, Paper) |
| Retention 30 → -1 shows impact dialog ("changes from 30 to -1"), needs confirmation | pass (dialog opened; cancelled, not applied) |
| Brief 2: model restricted to Finance + Marketing via UI; maya sees it, sam gets `no_model` | pass (UI + direct API) |
| Brief 3: only `tech.queue.position` granted to one person; `/api/me` lists exactly that field, `/api/system/resources` 403 | pass |
| Brief 1: role renamed "AI Technician"; id `model_operator` unchanged | pass |
| Brief 4: tab title changed in Branding; document title updates immediately; preview reflects it | pass |
| Brief 7: registration mode → admin; `/api/public` closed, signup API 403 | pass |
| Accent contrast error from the server is shown inline (import dry-run showed 1.07:1 message) | pass |
| Import dry-run lists problems and changes, applying blocked while errors exist; weakening change needs ticking a confirm box | pass (not applied) |
| Add Sarness Engine as 2nd backend through UI; test + sync; version 0.4.0 shown; manifest table honest | pass |
| System page: host, GPU (RTX 3060 VRAM/util/temp), backend health are real; "Unavailable" paths coded | pass for available data; unavailable path not exercised |
| Bulk select: page vs "all 31 matching" with count in confirm dialog | pass (dialog shown; action not run in UI; API count-mismatch covered by backend tests) |
| Command palette Ctrl+K: pages + settings, arrow/Enter navigates to `/admin/settings?q=` | pass |
| 375 px: no horizontal page scroll on 17 admin pages (Carbon) | pass after fixes (grid `minmax(0,1fr)`, `.stack`, `.panel-h` wrap, matrix scroller) |
| 375 px: permission matrix Deny column needs inner horizontal scroll | partial — fails "no scrolling", acceptable inside a scroller, to improve |
| Mobile nav menu opens, items ≥ 44 px tall | pass (viewed) |
| Carbon theme legible, focus ring visible on matrix radios | pass (one 375 px view) |
| 768 px layouts | not separately inspected (layout switches at 860 px; 1288 and 375 inspected) |
| Screen-reader / full keyboard pass | not done; controls are native elements with labels, dialogs trap focus (Modal) |
| Slop scan (`gradient(`, `backdrop-filter`, emoji, purple) in admin files | pass (grep) |
| `pnpm build` and `pnpm check` | 0 errors; 3 warnings (Svelte state_referenced_locally on props captured once) |
