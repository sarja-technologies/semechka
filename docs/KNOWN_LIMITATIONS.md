# Known limitations (read before deploying)

**Not implemented in this release** (the UI says so rather than showing inert controls):
- Audio (speech) mode: permissions, settings and capability detection exist; there is no execution path. **Audio cannot be built against the reference Spark**: its Sarness Engine has no text-to-speech or speech-to-text model installed, so there is nothing real to call.
- Image and video generation exist (see the next section) but are limited. Supported: text-to-image; editing a picture (img2img or edit); text-to-video; animating a picture (i2v or ti2v), each only where the engine's model reports that mode. Not supported: inpainting masks, LoRA selection and start/end-frame video. Only the Sarness adapter supports media; llama.cpp, vLLM and Ollama cannot generate it and report that. No-save chats cannot edit or animate a picture, because the source picture would have to be stored for the engine.
- **Knowledge is keyword search, not semantic search.** BM25 over passages (English stemming only). No embeddings, so synonyms, paraphrases and other languages' word forms are not matched. PDFs need a text layer; scans are refused (no OCR). Documents are decrypted into server memory to be searched (cached up to 8 indexes, 10 minutes idle); anyone who can read the server's memory can read them. Searching a very large collection costs memory and a rebuild after every change (bounded by `knowledge.max_chunks_per_collection`). Retrieved passages are sent to the model backend in readable form. Retrieval can miss relevant text and the model can ignore or misuse it: sources are shown as *given to the model*, not as proof of use.
- **Assistants have no tools.** They are a saved prompt, defaults and collections. No code execution, file access, web browsing, tool/MCP calling or autonomous agents. Assistants shared with the whole organisation cannot use collections. Assistant prompts are visible to anyone who can make the model repeat them.
- **Code workspace is a chat with a code prompt.** No sandbox, no running or testing, no repository or file-system access, no diff/apply. "Suggested for code" is based only on an admin tag or the model's name.
- Scheduler covers one control plane: requests queue per backend in memory (lost on restart, which also ends the streams). No reservations, retries, per-task-type priorities, cross-node placement or clustering. A running reply can be stopped by an operator only at its next token; a backend that goes silent mid-reply is not interrupted by the queue. Positions can change as higher-priority requests arrive.
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
- Accessibility: axe-core (WCAG 2.2 A/AA) is clean on every route in Light, Dark and Graphite (the Custom theme is gated by the editor's contrast check instead), keyboard-only flows, focus management, reduced motion and touch-target size are tested in real Chrome (DESIGN_REVIEW.md, pass 2). **Not done:** no real screen-reader pass (NVDA, JAWS, VoiceOver, Orca: none available here), no independent audit, only Chrome driven (Firefox and Safari untested). The permission matrix scrolls horizontally inside its container on phones.
- No independent security review or penetration test. Treat the threat model as the author's, not an assessment.

## Interface, themes and frontends (0.3.0)
- **Themes:** exactly four (Light, Dark, Graphite, Custom). A Custom theme cannot be published while any listed colour pair is below its contrast minimum, but text over uploaded logos or images is not checked. Custom CSS is bounded (see CUSTOM_FRONTEND.md) and can still make the chat interface ugly; it cannot reach administration or sign-in pages.
- **Model icons:** from 0.4.0 only the developer logos whose owners allow this use are bundled (MODEL_ICONS.md); OpenAI, Meta, Microsoft, NVIDIA, Stability AI, Google and developers without a published mark get lettermarks unless an administrator uploads an icon.
- **Catalogue ordering:** drag and drop on a very long list relies on the browser scrolling while dragging. Up and down buttons and keyboard moves work everywhere.
- **Replacement frontends** must be served from the same origin as the API: there is no CORS and no API-token authentication for third-party clients. The example frontend renders text only (no Markdown) and does not cover media, attachments or folders.
- **Conversation access by administrators** is off by default. When an organisation enables it, it is audited and shown to the person, but the person is not notified in real time.
- **Performance** figures in DEPLOYMENT_EVIDENCE.md are from one machine (i9-11900F, Chrome, with and without 4x CPU throttling). Phones and low-end laptops were not measured directly.

**Can be advertised as working** (with tests): see REQUIREMENTS_TRACEABILITY.md rows marked VERIFIED.


## Image and video generation (Sarness Engine only)
- Jobs run one at a time on the engine's GPU; Semechka passes the engine's own queue position, step progress and errors through and invents none. The per-person concurrent-job, daily and gallery limits are Semechka's; there is no fair-share, priority or reservation across people.
- **Prompt retention on the engine:** Sarness keeps a record of each job, including the prompt, in its own job history. Semechka deletes the result *file* from the engine as soon as it has copied it, but cannot delete that job record (the engine only offers a global "clear finished jobs", which would race other users' in-flight results). No-save and the retention window therefore govern Semechka's storage, not the engine's job history. The protection panel says so.
- Results are encrypted at rest with a key held on the same server (not end-to-end). No-save results live in process memory for 15 minutes and are lost on restart.
- Progress is polled (about every 2 s in the browser, every 3 s server side). A job survives a Semechka restart; the in-memory result of a no-save job does not.
- Since 0.3.0, pictures and videos are made inside the conversation; `/image` and `/video` only open a new conversation ready for that kind of turn. Making, editing and animating in the thread are tested against a fake engine (`test_media_chat.py`). The real-engine check is recorded in DEPLOYMENT_EVIDENCE.md.
- Video was exercised once on a real engine at a deliberately tiny size (smallest resolution, 5 frames, 4 steps). Longer or larger clips, and the 80+ GB models (e.g. LTX-2.3), were not run.

## Model management, picker and interface (0.4.0)
- **Load and unload** work only with the Sarness Engine. Only chat models can be loaded ahead of time; picture and video
  models load when first used, and Semechka can only free their memory. Engine 0.4.0 holds one chat model at a time,
  so loading one replaces the other.
- **Protecting replies in progress:** Semechka refuses a load or unload while the engine reports a reply streaming (from
  any client) or while this server has a reply about to start on another model. The check and the load are separate
  calls and the engine has no reservation, so a reply another client starts in the moment between them can still be
  cut off by the engine.
- **No load progress:** the engine reports no percentage for loading a model, so the panel shows the operation as
  running with the time it started. During the tests engine 0.4.0 reported 0 % progress for picture and video jobs
  until they finished, and that is what is shown.
- **Memory figures are the engine's own estimates.** A load is refused only when the estimate exceeds the engine's total
  memory. The engine may stop an idle picture/video worker to make room, which Semechka reports but cannot prevent.
  After a picture or video model is freed, the engine keeps its worker process running (memory in use stayed about
  5 GB above the starting level in the test); Semechka has no control to stop the worker.
- **Other clients' engine jobs** appear with their kind, model and status only; their prompts and titles never do.
- **Model operators without `admin.users.view`** can edit models but cannot pick roles, teams or people in a model's
  access rules, because those lists need that permission.
- **The picker ranks nothing.** Sorting by size or context uses what the engine reports; models that report nothing
  sort last. There is no quality or speed ranking.
- **Names:** a model whose alias is still the one made from its identifier shows the engine's display name. The
  quantisation part of that name is shown only with `tech.model.quant`, and identifiers only with `tech.model.id`.
- **Prompt history** (Alt+Up/Down, off by default) recalls messages from the open conversation and from this browser
  tab, not from other conversations.
- **The Codex-inspired layout** was compared with screenshots of the OpenAI Codex app only, not side by side with the
  app itself, and was checked in Chrome only.

