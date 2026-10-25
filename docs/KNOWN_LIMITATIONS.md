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
- **Before 0.4.0 a sign-in could answer before its session was stored** (FastAPI 0.118+ commits a request's changes
  after the response is sent). A client that called the API at once could get 401, most often on the first sign-in
  after a start; the same applied to every other write. Fixed in 0.4.0 (changes are committed before the answer, and
  a failed commit is an error, not a success). 0.3.0 and earlier behave the old way whenever they are installed with FastAPI 0.118 or newer (the Spark's 0.3.0 was: reproduced there).
- **The first-run setup token is in the service log of installs made before 0.4.0**: the server printed it on every
  start, and systemd keeps that output in the user journal (readable by the same account, and by members of the
  `adm`/`systemd-journal` groups). From 0.4.0 the server and installer print the token only to a terminal and
  otherwise name the owner-only file. The token stops working when the wizard is completed; older journal entries are
  not removed.
- Accessibility: axe-core (WCAG 2.2 A/AA) is clean on every route in Light, Dark and Graphite (the Custom theme is gated by the editor's contrast check instead), keyboard-only flows, focus management, reduced motion and touch-target size are tested in real Chrome (DESIGN_REVIEW.md, pass 2) and, from 0.4.0, the same 78 browser tests in Firefox 158 (`E2E_FIREFOX=1`, driven over WebDriver BiDi). In Firefox, reduced motion and a touch screen are set through Firefox's own preferences, because the driver ignores Playwright's emulation; that is a stand-in for a phone, not a phone. **Not done:** no real screen-reader pass (NVDA, JAWS, VoiceOver, Orca: none available here), no independent audit, Safari and mobile browsers untested. The permission matrix scrolls horizontally inside its container on phones.
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
- **Loading a chat model ahead of time is turned off for the Sarness Engine.** Engine 0.4.0 holds one chat model for
  every client and switches it on any request for another model (or the same model with another context size), even
  while a reply is being written, and it offers no lock or reservation. A check before a manual load could not make
  that safe, so the action is not offered (the API answers 501 with the reason). Chat models load when someone first
  uses them. Unloading stays: the engine itself refuses it while a reply streams.
- **How replies are protected instead:** on an engine like this, Semechka's queue never runs replies on two different
  chat models at once (a reply for another model waits, shown as "Waiting for replies from another model to finish"),
  and before a reply that would make the engine switch, Semechka waits until the engine reports no reply streaming
  from any client ("Waiting for the engine to finish another reply"), up to the queue's wait limit, then gives up
  without switching. Tested against an engine double that switches the way the real one does
  (`test_engine_coordination.py`: no reply was cut off).
- **Engine-level limitation: cross-client scheduling is not completely safe.** Semechka narrows the window but
  cannot close it, because the engine offers nothing to reserve its chat model. What remains unprotected: (1) the
  engine's status and the request that makes it switch are two calls, so a reply another program starts in the round trip between them can still be cut off; (2) clients that do not run
  Semechka (or another Semechka server sharing the engine) do not wait for Semechka's replies; (3) Semechka predicts a
  switch from the model and context size it asks for; a difference in GPU layers or the engine's own default context
  that it cannot see is not predicted; (4) a reply that starts while someone unloads the model fails at its start
  with the engine's error. Only an engine-side lease would close these.
- **No load progress:** the engine reports no percentage for loading a model, so the panel shows the operation as
  running with the time it started. During the tests engine 0.4.0 reported 0 % progress for picture and video jobs
  until they finished, and that is what is shown.
- **Memory figures are the engine's own estimates.** A load is refused only when the estimate exceeds the engine's total
  memory. The engine may stop an idle picture/video worker to make room, which Semechka reports but cannot prevent.
  After a picture or video model is freed, the engine keeps its worker process running (memory in use stayed about
  5 GB above the starting level in the test). Administrators with `ops.model.load` can now stop the worker from the
  Running tab; it is refused while the worker is making something or jobs are waiting (the engine refuses too), and
  the worker starts again by itself for the next picture or video, which then takes longer.
- **Large models have not been run through Semechka.** Only small chat models (Qwen3.5 0.8B, which is Salted's "Quick",
  and on a copy Qwen3.6 35B-A3B for vision), SDXL Turbo and Wan 2.2 TI2V 5B were exercised on the real engine. The
  high-memory models installed on the reference Spark were not: Qwen3 Coder Next (about 48 GB of weights, 54.7 GB by the engine's estimate; the coding
  model most likely to be chosen for the code workspace), gpt-oss-120b (about 63 GB), Hermes 4 70B (about 43 GB),
  and the 50 to 126 GB video models. Load times, memory estimates against the Spark's unified memory, and what the
  engine does when a large model does not fit alongside a running worker are untested.
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
  app itself.
- **Deployment verification** (`verify_deployment.py`, `spark_salted_check.py`) now deletes the one conversation it
  creates, by its id, and checks the member's list is exactly what it was. Nine conversations left by verification
  and chat-probe runs before this change are still in Salted's member test account; they were not removed (see
  DEPLOYMENT_EVIDENCE.md).

