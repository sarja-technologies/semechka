# Semechka — DESIGN.md (design contract)

Semechka is a tool people use every day for work. It should look like a well-made instrument, not a landing page.
This file is the contract. `theme/tokens.json` holds the tokens (see Colour); nothing in the UI may use a colour, radius or font
that is not defined there (admin-chosen brand values arrive as CSS variables and are validated server-side).

## Identity
- Wordmark: `semechka.` — lowercase, IBM Plex Mono semibold, the full stop in the accent colour. Organisations may replace it
  with their own logo or name; the shell never hard-codes "Semechka" outside the wordmark fallback and the optional "Powered by".
- Voice: plain, specific, short. Say what happened and what to do next. No "supercharge", no "unlock", no "AI-powered",
  no exclamation marks, no emoji. Errors name the cause ("The model backend is not reachable") rather than "Something went wrong".

## Colour
Exactly four themes: **Light**, **Dark**, **Graphite** (a dark grey for long sessions) and the organisation's
**Custom** theme. The single source is `backend/semechka/theme/tokens.json`, which is copied identically to
`web/src/lib/theme/tokens.json`; design-lint fails if they differ. It lists 27 tokens in
6 groups, the three presets, and 23 contrast pairs. The runtime writes the active
theme's tokens onto `:root`.

From 0.4.0 Light and Dark are neutral: an off-white page and a charcoal (never pure black) page, with surfaces told
apart by small steps in lightness rather than borders or colour. The table below is generated from `tokens.json`.

| token | Light | Dark | Graphite |
|---|---|---|---|
| `--brand` | #1F1F1F | #ECECEC | #E6E8E4 |
| `--accent` | #0B6E69 | #6CC3B9 | #7CC9BF |
| `--bg` | #FAFAF9 | #191919 | #24282A |
| `--bg-2` | #F1F1F0 | #222222 | #2B3032 |
| `--sidebar-bg` | #F3F3F2 | #151515 | #1F2325 |
| `--composer-bg` | #FFFFFF | #232323 | #2E3335 |
| `--card-bg` | #FFFFFF | #212121 | #2C3133 |
| `--user-msg-bg` | #EEEEEC | #2B2B2B | #353B3D |
| `--fg` | #171717 | #ECECEC | #E8EAE6 |
| `--fg-muted` | #5E5E5E | #9C9C9C | #A9B0AD |
| `--border` | #E5E5E3 | #2D2D2D | #3D4345 |
| `--border-strong` | #8A8A87 | #6B6B6B | #737A7D |
| `--btn-primary-bg` | #1F1F1F | #ECECEC | #E6E8E4 |
| `--link` | #0B6E69 | #6CC3B9 | #7CC9BF |
| `--success` | #23744F | #55C595 | #5CC99A |
| `--warn` | #875A0F | #D9A441 | #DCAA4A |
| `--danger` | #BC3631 | #EF6D67 | #F38E8A |
| `--info` | #36698A | #7DB3D4 | #84B9D9 |

The **Custom** theme is built in Administration → Theme. It starts from a preset, can re-derive surfaces from three
base colours (brand, secondary brand, accent), takes overrides per token, and has separate light and dark versions.
Publishing refuses any contrast pair below its minimum (4.5:1 for text, 3:1 for focus rings and control outlines).
`scripts/contrast_check.py` checks the same pairs for the three presets in CI. Text colours are never derived.

Hard bans for every first-party default, hover, focus, selection and chart colour: purple, violet, lavender, lilac, indigo,
gradients of any kind, glass/blur panels, glow, 3D blobs, sparkle/orb iconography, emoji as icons, fake gauges, invented
numbers, confetti, parallax, staggered fade-in. A unit test (`test_presets_meet_contrast_and_default_palette_is_not_purple`) checks every preset colour's hue and contrast; the
CSS lint in `scripts/design-lint.sh` greps the stylesheet and components for forbidden hue ranges, `gradient(` and `backdrop-filter`.

Accent is for the one primary action per view, links, focus rings and the active nav item. It is never decoration.
Charts and status use semantic colours **plus** a label or shape, never colour alone.

## Type
The device's own interface font by default (0.4.0); administrators can choose another (IBM Plex Sans, System interface font, Humanist (system), Serif (system)). IBM Plex Mono for
identifiers, numbers in tables and technical values, self-hosted via @fontsource (SIL OFL).
Scale (px): 12 / 13 / 14 (base) / 16 / 20 / 28 (the empty-chat welcome, regular weight). Line-height 1.5 body, 1.2
headings. Tabular figures on every number column. Chat column up to 768 px (640 / 1000 with the width setting).

## Space, shape, density
- 4 px base, steps 4/8/12/16/24/32/48. `--radius` 10 px (admin-editable 0–16) for controls and panels, `--radius-sm`
  8 px for rows, `--radius-menu` for popovers, `--radius-composer` (twice `--radius`) for the composer and suggestion
  cards. Fully round shapes are kept for: status chips, segmented switches, the model picker trigger, and the
  composer's circular buttons (attach, settings, send/stop). Cards are used only when a block needs its own boundary.
- Hairline 1 px borders. Shadows: `--shadow` for floating layers (menus, dialogs), `--shadow-sm` for the composer and
  buttons; both are set per light/dark variant.
- Density: comfortable (row 44 px) and compact (row 32 px); admin default and per-person override. Touch targets ≥ 40 px on touch.

## Layout rules
- App shell: a full-height sidebar (260 px, resizable, a sheet under 720 px) with New conversation, search, conversation
  history and the pages; beside it a slim 48 px header and the content region, with an optional right panel (technical
  drawer) that becomes a sheet under 1100 px.
- Chat: one centred column; your messages in a rounded bubble on the right, replies as unboxed text under the model's
  name. The composer is pinned at the bottom: text on top, then + (attach), the mode switch and the model picker, and a
  round send button that becomes Stop while a reply runs. The empty chat puts a one-line welcome just above it.
- Model picker: the server arranges and filters it (names, grouping, order, what each person may see); the picker only
  shows it, searches it and saves personal choices. Unknown details are left out, never guessed.
- Admin: section nav on the left, dense forms and tables on the right. Tables: sticky header, sortable, filter row, pagination,
  checkbox column with "this page" vs "all N matching" selection, keyboard row focus, honest loading / empty / error states.
- Every empty state says what the thing is and the one action that fills it. No illustrations, no stock imagery.

## Interaction
- One decisive primary button per context. Destructive actions need a confirmation naming the object and the count.
- Optimistic UI only when the server has confirmed; otherwise show a pending state. No fake progress bars or wait times:
  progress appears only when the backend reports it. Queue state says "sent straight to the backend" when there is no queue.
- Motion: 120–160 ms opacity/transform for state changes only; `prefers-reduced-motion` disables all of it.
- Keyboard: `/` focuses search, `Ctrl/⌘+K` opens the command palette (settings search), `Enter` sends and `Shift+Enter`
  starts a new line (with *Enter sends* off: `Enter` is a new line and `Ctrl/⌘+Enter` sends), the arrow keys always move
  the caret (earlier messages come back only with `Alt+↑/↓`, when prompt history is on), `Esc` stops generation or closes a layer, `g` then `c/a` jumps to chat/admin. Every control is reachable and visibly focused (2 px accent ring,
  2 px offset). Skip link on every page.
- A disabled control always explains why (title text + visible hint) — no inert decorative switches.

## Accessibility (WCAG 2.2 AA)
Contrast ≥ 4.5:1 text, 3:1 UI; focus not obscured; target size ≥ 24 px (we use 32+); form errors tied with `aria-describedby`;
live regions for streaming status and toasts; dialogs trap focus and restore it; tables have captions; no information by colour only;
reflow to 320 px without horizontal scroll.

## Design gate (run before release)
Screenshots at 375 / 768 / 1440 px, light and dark, for: first run, sign-in, sign-up (open and closed), empty chat, streaming chat, error,
offline, no-save chat, protection panel, technical drawer, admin overview, people, models, settings search. Fix: clipping, overflow,
weak hierarchy, missing focus, token drift, dead links, any slop pattern above. Results are recorded in `docs/DESIGN_REVIEW.md`
with pass/fail per check — not adjectives.
