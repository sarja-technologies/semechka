# Known limitations (read before deploying)

**Not implemented in this release** (the UI says so rather than showing inert controls):
- Audio (speech), code workspace, knowledge/RAG and agents/tools modes. Their permissions, settings and capability detection exist; there is no execution path. **Audio cannot be built against the reference Spark**: its Sarness Engine has no text-to-speech or speech-to-text model installed (only image, video, LoRA and LLM categories), so there is nothing real to call.
- Image and video generation exist (see the next section) but are limited: text-to-image and text-to-video only, no image-to-image, inpainting, editing, LoRA selection or start/end-frame video yet, and only the Sarness adapter supports them (llama.cpp, vLLM and Ollama cannot generate media and report that).
- Scheduler: no queues, priority lanes, fair-share, reservations, placement or retries. Only per-person daily quota and concurrent-generation limits.
- Cluster mode: no node agent, enrolment, signed node traffic or heterogeneous routing. The DGX Spark + RTX 4070 Ti scenario is **not tested**.
- Multi-tenant hosting (several isolated organisations). Single-organisation only. Do not offer shared hosting on this.
- SAML, SCIM and passkeys/WebAuthn are not built (SAML needs XML-signature handling that has not been reviewed; SCIM needs a provisioning client to test against; WebAuthn needs real authenticators). OpenID Connect sign-in exists but has only been tested against a mock provider, so real-provider quirks are unverified; refresh tokens, `private_key_jwt` client authentication and back-channel logout are not implemented; the DNS check before a provider fetch is not pinned to the connection. Also not built: CAPTCHA, geographic restrictions, signed templates, per-conversation settings scope, custom domains/TLS termination.
- Content-policy engine (allow/warn/block/hold/redact/escalate), flagged-content review, legal hold, dual approval, data-subject export/delete workflows.
- User-held-key encryption, customer-managed keys / KMS, E2EE. **Do not describe conversations as end-to-end encrypted.**
- Backups: encrypted file backup/restore of the SQLite database + key + assets is implemented and tested; scheduled backups, off-host destinations, Postgres backups, retention of backups and restore drills are not.
- Audit retention (pruning would break the hash chain; needs checkpointing). Usage rows are pruned by `privacy.usage_retention_days`.
- Email: SMTP delivery (verification, password reset) is implemented but was only exercised against a protocol stub in tests, not a real mail provider.
- Telemetry: NVIDIA via `nvidia-smi` and Linux `/proc` only. AMD/Intel/Apple accelerators report "unavailable". ARM64 has not been run.
- PostgreSQL, Docker/Compose packaging and the GitHub Actions workflow were written but **not executed** on the authoring machine.
- Rate limiting, settings cache and generation counters are in-process: run one worker. A restart clears limiter state.
- Accessibility: automated and manual checks listed in DESIGN_REVIEW.md only; no screen-reader (NVDA/VoiceOver/Orca) pass and no independent audit.
- No independent security review or penetration test. Treat the threat model as the author's, not an assessment.

**Can be advertised as working** (with tests): see REQUIREMENTS_TRACEABILITY.md rows marked VERIFIED.


## Image and video generation (Sarness Engine only)
- Jobs run one at a time on the engine's GPU; Semechka passes the engine's own queue position, step progress and errors through and invents none. The per-person concurrent-job, daily and gallery limits are Semechka's; there is no fair-share, priority or reservation across people.
- **Prompt retention on the engine:** Sarness keeps a record of each job, including the prompt, in its own job history. Semechka deletes the result *file* from the engine as soon as it has copied it, but cannot delete that job record (the engine only offers a global "clear finished jobs", which would race other users' in-flight results). No-save and the retention window therefore govern Semechka's storage, not the engine's job history. The protection panel says so.
- Results are encrypted at rest with a key held on the same server (not end-to-end). No-save results live in process memory for 15 minutes and are lost on restart.
- Progress is polled (about every 2 s in the browser, every 3 s server side). A job survives a Semechka restart; the in-memory result of a no-save job does not.
- Not verified in a real browser in this pass: the /image and /video pages were built, type-checked and linted, and the underlying API was exercised end to end (fake engine and real engine), but their layout at 375/768/1280/1920 px and screen-reader behaviour have not been inspected.
- Video was exercised once on a real engine at a deliberately tiny size (smallest resolution, 5 frames, 4 steps). Longer or larger clips, and the 80+ GB models (e.g. LTX-2.3), were not run.
