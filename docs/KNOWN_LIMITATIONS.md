# Known limitations (read before deploying)

**Not implemented in this release** (the UI says so rather than showing inert controls):
- Audio mode (and image/video unless the MEDIA work is merged): permissions, settings and capability detection exist; there is no execution path.
- **Knowledge is keyword search, not semantic search.** BM25 over passages (English stemming only). No embeddings, so synonyms, paraphrases and other languages' word forms are not matched. PDFs need a text layer; scans are refused (no OCR). Documents are decrypted into server memory to be searched (cached up to 8 indexes, 10 minutes idle); anyone who can read the server's memory can read them. Searching a very large collection costs memory and a rebuild after every change (bounded by `knowledge.max_chunks_per_collection`). Retrieved passages are sent to the model backend in readable form. Retrieval can miss relevant text and the model can ignore or misuse it: sources are shown as *given to the model*, not as proof of use.
- **Assistants have no tools.** They are a saved prompt, defaults and collections. No code execution, file access, web browsing, tool/MCP calling or autonomous agents. Assistants shared with the whole organisation cannot use collections. Assistant prompts are visible to anyone who can make the model repeat them.
- **Code workspace is a chat with a code prompt.** No sandbox, no running or testing, no repository or file-system access, no diff/apply. "Suggested for code" is based only on an admin tag or the model's name.
- Scheduler covers one control plane: requests queue per backend in memory (lost on restart, which also ends the streams). No reservations, retries, job priorities per task type, cross-node placement or clustering. A running reply can be stopped by an operator only at its next token; a backend that goes silent mid-reply is not interrupted by the queue. Positions are the scheduler's current order and can change as higher-priority requests arrive. Admission counts a person's waiting requests toward `quota.concurrent_requests`.
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
- Accessibility: automated and manual checks listed in DESIGN_REVIEW.md only; no screen-reader (NVDA/VoiceOver/Orca) pass and no independent audit.
- No independent security review or penetration test. Treat the threat model as the author's, not an assessment.

**Can be advertised as working** (with tests): see REQUIREMENTS_TRACEABILITY.md rows marked VERIFIED.
