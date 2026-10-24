# Semechka — DESIGN.md (design contract)

Semechka is a tool people use every day for work. It should look like a well-made instrument, not a landing page.
This file is the contract. `web/src/app.css` holds the tokens; nothing in the UI may use a colour, radius or font
that is not defined there (admin-chosen brand values arrive as CSS variables and are validated server-side).

## Identity
- Wordmark: `semechka.` — lowercase, IBM Plex Mono semibold, the full stop in the accent colour. Organisations may replace it
  with their own logo or name; the shell never hard-codes "Semechka" outside the wordmark fallback and the optional "Powered by".
- Voice: plain, specific, short. Say what happened and what to do next. No "supercharge", no "unlock", no "AI-powered",
  no exclamation marks, no emoji. Errors name the cause ("The model backend is not reachable") rather than "Something went wrong".

## Colour
Three first-party profiles (all tokens editable by an admin; accent is contrast-checked to 4.5:1):

| token | Paper | Carbon | Graphite |
|---|---|---|---|
| `--bg` | #F7F7F5 | #111415 | #252A2A |
| `--surface` | #FFFFFF | #1A1E1F | #303637 |
| `--fg` | #202322 | #F0F2EF | #E9ECE8 |
| `--border` | #D9DBD7 | #343838 | #475150 |
| `--accent` | #087771 | #64BEB5 | #64BEB5 |
| `--danger` | #C83C36 | (lifted for contrast) | |
| `--success` | #267E59 | | |
| `--warn` | #A36A12 | | |

Hard bans for every first-party default, hover, focus, selection and chart colour: purple, violet, lavender, lilac, indigo,
gradients of any kind, glass/blur panels, glow, 3D blobs, sparkle/orb iconography, emoji as icons, fake gauges, invented
numbers, confetti, parallax, staggered fade-in. A unit test (`test_default_palette_is_not_purple`) checks the accent hue; the
CSS lint in `scripts/design-lint.sh` greps the stylesheet and components for forbidden hue ranges, `gradient(` and `backdrop-filter`.

Accent is for the one primary action per view, links, focus rings and the active nav item. It is never decoration.
Charts and status use semantic colours **plus** a label or shape, never colour alone.

## Type
IBM Plex Sans (UI) and IBM Plex Mono (identifiers, numbers in tables, technical values), self-hosted via @fontsource (SIL OFL).
Scale (px): 12 / 13 / 14 (base) / 16 / 20 / 26. Line-height 1.45 body, 1.2 headings. Tabular figures on every number column.
Max reading width 72ch in chat and prose. Technical values (model IDs, tokens/s) are always set in Plex Mono.

## Space, shape, density
- 4 px base, steps 4/8/12/16/24/32/48. Radius 6 px controls, 8 px panels (`--radius`, admin-editable 0–16). No pills except
  status chips (radius 4). Cards are used only when a block needs its own boundary; otherwise a hairline divider.
- Hairline 1 px borders. Shadows only for floating layers (menus, dialogs): one soft 0 8px 24px rgba(0,0,0,.12).
- Density: comfortable (row 44 px) and compact (row 32 px); admin default and per-person override. Touch targets ≥ 40 px on touch.

## Layout rules
- App shell: left rail 232 px (collapsible to a 56 px icon rail; becomes a bottom bar of ≤ 5 items under 720 px),
  content region, optional right panel (technical drawer, protection panel) that becomes a sheet under 1024 px.
- Chat: single column ≤ 72ch, composer pinned, one primary action (Send ↔ Stop). Model picker shows friendly names only unless the
  person holds technical fields. The protection badge sits beside the composer.
- Admin: section nav on the left, dense forms and tables on the right. Tables: sticky header, sortable, filter row, pagination,
  checkbox column with "this page" vs "all N matching" selection, keyboard row focus, honest loading / empty / error states.
- Every empty state says what the thing is and the one action that fills it. No illustrations, no stock imagery.

## Interaction
- One decisive primary button per context. Destructive actions need a confirmation naming the object and the count.
- Optimistic UI only when the server has confirmed; otherwise show a pending state. No fake progress bars or wait times:
  progress appears only when the backend reports it. Queue state says "sent straight to the backend" when there is no queue.
- Motion: 120–160 ms opacity/transform for state changes only; `prefers-reduced-motion` disables all of it.
- Keyboard: `/` focuses search, `Ctrl/⌘+K` opens the command palette (settings search), `Enter` sends, `Shift+Enter` newline, `Esc` stops
  generation or closes a layer, `g` then `c/a` jumps to chat/admin. Every control is reachable and visibly focused (2 px accent ring,
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
