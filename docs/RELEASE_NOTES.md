# Release notes

## 0.4.0 (2026-10-25)

Semechka 0.4.0 is a self-hosted, single-organisation release. It has had no independent security review. Read
[KNOWN_LIMITATIONS.md](KNOWN_LIMITATIONS.md) before deploying. This is the first tagged release, and it also contains
everything built for 0.3.0, which was never merged or tagged separately.

### New since 0.3.0
- **Deployment presets:** Personal, Business and Managed Private Hosting, plus Custom, where the owner chooses
  each value in the first-run wizard.
- **Interface:** neutral Light and Dark themes (plus Graphite and Custom). A redesigned chat layout with a compact
  composer. Enter or Ctrl+Enter sends, as configured. Prompt history is optional (Alt+Up/Down).
- **Model picker** built only on what the engine reports: search, sections, favourites, administrator-controlled
  names, groups, order, pins, hidden and restricted models. Nothing is ranked. Developer logos appear only where
  their owners allow it; other developers get lettermarks.
- **Engine panel (Running tab):** live memory, the chat model the engine holds, the picture/video worker, and the
  engine's job queue. Other clients' jobs show their kind and status only. Administrators can unload a model
  (refused while a reply is being written) and stop the picture/video worker to get its memory back (refused while
  it works or jobs wait).
- **Model permissions split:** `models.view_all`, `models.use_all`, `models.status.view`, `ops.model.load`,
  `ops.queue.view`. All enforced on the server and audited.
- A real "Page not found" page. People can go back to the organisation's default theme.

### Release hardening
- **Replies are protected from model switches as far as the engine allows.** Engine 0.4.0 holds one chat model for
  every client and switches it on any request for another model, even mid-reply, with no way to reserve it. So:
  - Loading a chat model ahead of time is turned off for the Sarness Engine.
  - The queue never runs replies on two different chat models at once.
  - Before a reply that would make the engine switch, Semechka waits until no client is mid-reply. If that takes
    longer than the queue's wait limit, it gives up without switching.

  This narrows the window but does not close it; see "Known limitations" below.
- **Sign-in could answer before its session was stored.** With FastAPI 0.118 and later, changes were committed after
  the response was sent, so a client that called the API at once could get 401. A failed commit also answered 200.
  Every change is now committed before the answer. FastAPI 0.121 or later is required.
- **The setup token is no longer written to service logs.** It is shown only in a terminal; otherwise the server
  names the owner-only file that holds it.
- **Deployment verification removes what it creates.** It deletes only its own test conversation, by its id, and
  checks the list is unchanged.
- **Firefox:** the whole browser suite, including accessibility checks, passes in Firefox 158 as well as Chrome.

### Upgrading
- Database schema 5 → 6, migrated on first start. A program older than its data refuses to start.
- The installer keeps a snapshot of the data before every update. To roll back:
  - `./install.sh --rollback --restore-data` goes back to 0.3.0 together with its data.
  - `--restore-data` on its own is for a program that was already rolled back and refuses to start.

  Rehearsed three times on a copy of a production instance (DEPLOYMENT_EVIDENCE.md).
- Roles that managed models receive the new model permissions; other roles receive none.

### Known limitations (summary)
- **Engine-level limitation: cross-client scheduling is not completely safe.** The engine's status and the request
  that makes it switch are two calls, so a reply another program starts between them can still be cut off. Clients
  that do not run Semechka do not wait. A GPU-layer difference the engine does not report is not predicted. Only an
  engine-side lease or reservation would close this.
- **Large models are untested through Semechka:** Qwen3 Coder Next (about 55 GB by the engine's estimate),
  gpt-oss-120b, Hermes 4 70B, and the 50–126 GB video models.
- **The picture/video worker keeps memory** after its models are freed, until an administrator stops it. The next
  picture or video then starts it again, more slowly.
- **Not tested:** screen readers, Safari, phones measured directly, PostgreSQL, Docker packaging, the CI workflow.
  There is no load progress, because the engine reports none.
- **Setup tokens in old service logs:** journals of installs made before 0.4.0 can still contain previously printed
  setup tokens. Each stops working once the wizard is completed or the server restarts.
- **Superseded commit subject:** one development commit is titled "model load/unload that never cuts off a reply".
  That claim was withdrawn in the hardening pass above.

### Verification
- Backend: 296 passed, 8 skipped (opt-in real-backend tests).
- Playwright: 78 passed in Chrome and 78 passed in Firefox 158.
- Design lint and contrast check: pass.
- Deployed to a DGX Spark (two instances, one shared Sarness Engine 0.4.0). `verify_deployment.py`: 35 passed;
  `spark_salted_check.py`: 14 passed.

Details: DEPLOYMENT_EVIDENCE.md, REQUIREMENTS_TRACEABILITY.md.
