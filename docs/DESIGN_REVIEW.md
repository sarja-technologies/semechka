# Design review

Two passes. **Pass 2 (below) supersedes pass 1** wherever they overlap: pass 1 was manual, iframe-based and partial; pass 2 is a repeatable suite driving real Google Chrome at real viewport sizes against real servers. Pass 1 is kept underneath as history.

## Pass 2: repeatable real-browser verification

**Method.** `web/e2e` (Playwright, system Chrome via `channel: 'chrome'`, no downloaded browsers). `pnpm e2e` starts a throwaway backend with a fresh data directory (never a mock of Semechka itself), plus a protocol-level fake inference server (`backend/tests/fakebackend.py`, the same test double the backend suite uses). One server is left at its first-run screen for the wizard test; another is seeded through the real setup API with an owner, member, technical-read-only member, platform admin and guest. Viewports are set with `page.setViewportSize` (a true browser resize, not an iframe). Screenshots are written to `web/shots-out/` (git-ignored); a curated set of 22 (1.5 MB) is committed in `docs/screenshots/`, and every one was opened and looked at.

**Result: 77 tests, 77 passed, run twice in a row with no flake after the fixes below.**

| Suite | Tests | What it proves |
|---|---|---|
| `wizard` | 1 | Fresh server redirects every route to setup and closes the API; wrong token rejected; all 9 steps work (identity validation, preset, real hardware read-out checked against `uname -m`, backend failure then success, model selection, smoke-test choice, tech-visibility preset, privacy/limits, sign-up mode, review, launch); smoke test passes; branding, theme and retention actually applied; only chosen models enabled; exactly one account exists; token spent. Each step is also resized to 375, 768, 1280 and 1920 px with a no-sideways-scroll assertion. |
| `auth` | 11 | Generic sign-in error that does not reveal which addresses exist; `?next=` return; reload keeps the session; sign-out invalidates a replayed cookie; instance-private httpOnly/SameSite cookie name; suspended account loses access at once; lock-out after repeated failures and audited; admin-only, public-with-approval, invite-only (single use) and approved-domain registration through the real pages; approval in People grants only the default role; MFA enrolment with a computed authenticator code, then the code step at sign-in; accounts do not cross instances. |
| `chat` | 10 | Real SSE streaming; saved and listed; survives reload; one polite live-region announcement per reply; Stop and Esc keep the partial reply marked Stopped; regenerate/edit branches with version arrows; search, export, rename, delete; no-save leaves the conversation list unchanged and nothing after reload; model picker; ordinary member sees no technical affordance and raw API responses contain none of the infrastructure vocabulary; a technical-read-only member sees exactly the granted fields with no controls and gets 403 on operate routes; another person cannot read, change or export a conversation even knowing its id. |
| `outage` | 1 | A real backend process is killed and restarted mid-session: plain-language error with no address or stack trace, composer stays usable, the admin connection test reports the failure, chat resumes when the backend returns. |
| `admin` | 16 | Every admin page loads with real content, no console errors and no failed API calls; system page memory matches `/proc/meminfo` within 2%; a role-limited operator sees only the pages their role allows, gets an explanation (not a failing page) on a forbidden URL, and the API agrees; roles and teams CRUD; team deny beats role grant, enforced on the server and explained; settings search; weakening change shows its impact, needs confirmation, can be cancelled without effect, and is rolled back from History; per-person override and the effective-value inspector; export has no secrets and a modified file previews then applies; audit chain verifies and the page leaks no hashes or secrets; branding (name, tab title, logo, favicon) is seen by other people and by the signed-out sign-in page, then reverts; Paper/Carbon/Graphite switch live and persist; a failing accent colour is refused; alias change reaches chat; backend test. |
| `palette` | 6 | Ctrl/Cmd+K and a visible Search button; focus in and back out; arrow keys and `aria-activedescendant`; new chat, new no-save chat, switch model, find a conversation, theme and density; commands respect permissions (members never see administration; a role without chat is not offered "New chat"); works in the admin console and finds settings by name. |
| `a11y` | 18 | axe-core (WCAG 2.0/2.1/2.2 A and AA tags) on **every route** in **all three themes**: 5 app screens plus a conversation with a reply, 20 admin pages, sign-in/sign-up/recovery, and with a dialog and the palette open. Keyboard-only: skip link, write/send/stop/search without a mouse, dialog focus trap in both directions with focus returned to the opener, a visible 2px focus indicator on every control reached by Tab on five representative screens. Structure: one `main`, one `h1`, `lang`, labelled navigation, no unnamed buttons, no images without alt on all 25 screens. Live regions. `prefers-reduced-motion` removes animation and transitions. On a touch phone profile every button, link, field and checkbox label is at least 40 px tall. |
| `viewports` | 13 | At 375, 768, 1280 and 1920 px: the chat screen (empty and with a reply), usage, help, account and unavailable-mode pages, and all 20 admin pages have no page-level sideways scroll and no element sticking out of the viewport outside a scroll container; the model picker shows its whole label; dialogs and the palette stay inside the viewport with their actions reachable; the admin menu opens on a phone and closes after choosing. |
| `smoke` | 1 | Sign in, brand title, sign out. |

**Defects found by this pass and fixed** (each has a test that failed before the fix):

| Defect | Found by | Fix |
|---|---|---|
| Starting a new (no-save) chat just as a reply finished pushed the previous saved conversation's messages into the new no-save transcript (reproduced 7 times in 8) | palette spec, then a repeat-loop reproduction | Each generation now remembers its mode and conversation; a new chat or another conversation invalidates in-flight UI updates. |
| Esc is promised on the Stop button but only worked while the composer had focus (focus is on the button after clicking Send) | chat spec | Global Esc stops a running reply unless a dialog is open. |
| Saving one setting reloaded the list and silently discarded text typed in another setting's field | admin spec (timing) | A row re-syncs only when its own saved value changed. |
| A permission-less admin URL showed a bare "You do not have access" with a useless Retry | admin spec | The admin shell explains the section, names the missing permission and links back. |
| Model picker truncated its label at 375 px ("Second A") | viewports spec (the known issue from pass 1) | Full-width row on phones. |
| Uploaded favicon was stored but never applied to the tab | admin spec | Applied (and restored when removed). |
| Contrast: 6 text pairs below 4.5:1 (accent, danger, success and warn text on their tints in Paper; Graphite danger text) and 6 UI-boundary pairs below 3:1 (input and control borders were about 1.9:1 in Paper, 2.0 to 2.2 in the dark themes; WCAG 1.4.11) | axe + `scripts/contrast_check.py` | Tokens adjusted; `scripts/contrast_check.py` now fails CI on any text pair under 4.5:1 or boundary under 3:1 in all three themes; the admin accent validator now checks every surface the accent can sit on, not only white. The default accent changed from #087F78 to #087771. |
| Accessible name did not contain the visible text on the Home link and the admin search button (WCAG 2.5.3) | axe | Names come from content. |
| Active navigation item used accent-coloured text on an accent tint (contrast depended on the admin's accent) | axe | Foreground text plus an accent bar. |
| Checkboxes with long labels stacked the text under the box; last table row with a row header kept a stray border; filter fields had mixed widths on phones; 18 px checkboxes on touch | screenshots | CSS. |
| Palette search needed one exact substring ("theme carbon" found nothing) | palette spec | Every typed word must match, in any order. |
| Three Svelte `state_referenced_locally` warnings | `pnpm check` | Intentional one-time captures made explicit; **0 errors, 0 warnings**. |
| Admin model list exposed the raw server file path (llama.cpp uses paths as model ids) | pass 1 note | Everyone sees the file name; the raw value is returned only to holders of `tech.model.id`, and searching by it is limited the same way. |

**Not done, and why.** A real screen reader (NVDA, JAWS, VoiceOver, Orca) has **not** been run: none is available in this environment, and automated axe plus keyboard tests are not a substitute. Native HTML semantics, labels, landmarks, live regions and focus management are verified; how they are *spoken* is not. No independent accessibility audit. Only Chrome (the system build) was driven; Firefox and Safari are untested. The 375 px permission matrix still scrolls horizontally inside its own container (acceptable, not ideal). The sign-in MFA step, password change and account deletion screens are covered by API tests and the MFA flow above, but account deletion is not clicked through.

**Reproduce.** `cd web && pnpm build && pnpm e2e` (about 2 minutes). Python for the backend comes from `SEMECHKA_PY` or the repository `.venv`. `E2E_SHOTS=docs pnpm e2e` writes screenshots straight into `docs/screenshots/`. An `e2e` job and the contrast check are in `.github/workflows/ci.yml` but have **not** run on GitHub (no remote exists); only local runs are evidenced here.

---

# Pass 1 (manual; superseded where Pass 2 covers the same ground)

## User-facing screens (fork A)

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
