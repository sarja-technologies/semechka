# Known limitations (read before deploying)

**Not implemented in this release** (the UI says so rather than showing inert controls):
- Image, video, audio, code workspace, knowledge/RAG and agents/tools modes. Their permissions, settings and capability detection exist; there is no execution path.
- Scheduler: no queues, priority lanes, fair-share, reservations, placement or retries. Only per-person daily quota and concurrent-generation limits.
- Cluster mode: no node agent, enrolment, signed node traffic or heterogeneous routing. The DGX Spark + RTX 4070 Ti scenario is **not tested**.
- Multi-tenant hosting (several isolated organisations). Single-organisation only. Do not offer shared hosting on this.
- SSO/OIDC/SAML/SCIM, passkeys, CAPTCHA, geographic restrictions, signed templates, per-conversation settings scope, custom domains/TLS termination.
- Content-policy engine (allow/warn/block/hold/redact/escalate), flagged-content review, legal hold, dual approval, data-subject export/delete workflows.
- User-held-key encryption, customer-managed keys / KMS, E2EE. **Do not describe conversations as end-to-end encrypted.**
- Backups: encrypted file backup/restore of the SQLite database + key + assets is implemented and tested; scheduled backups, off-host destinations, Postgres backups, retention of backups and restore drills are not.
- Audit retention (pruning would break the hash chain; needs checkpointing). Usage rows are pruned by `privacy.usage_retention_days`.
- Email: SMTP delivery (verification, password reset) is implemented but was only exercised against a protocol stub in tests, not a real mail provider.
- Telemetry: NVIDIA via `nvidia-smi` and Linux `/proc` only. AMD/Intel/Apple accelerators report "unavailable". ARM64 has not been run.
- PostgreSQL, Docker/Compose packaging and the GitHub Actions workflow were written but **not executed** on the authoring machine.
- Rate limiting, settings cache and generation counters are in-process: run one worker. A restart clears limiter state.
- Accessibility: axe-core (WCAG 2.2 A/AA) is clean on every route in all three themes, keyboard-only flows, focus management, reduced motion and touch-target size are tested in real Chrome (DESIGN_REVIEW.md, pass 2). **Not done:** no real screen-reader pass (NVDA, JAWS, VoiceOver, Orca: none available here), no independent audit, only Chrome driven (Firefox and Safari untested). The permission matrix scrolls horizontally inside its container on phones.
- No independent security review or penetration test. Treat the threat model as the author's, not an assessment.

**Can be advertised as working** (with tests): see REQUIREMENTS_TRACEABILITY.md rows marked VERIFIED.
