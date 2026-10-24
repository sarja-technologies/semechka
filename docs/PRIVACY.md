# What Semechka does with conversations (privacy disclosure)

This text matches what the product shows in "How is this chat protected?" and is derived from the configuration, not from marketing.

- **Encrypted in transit?** Only if you serve Semechka over HTTPS (reverse proxy). The panel says so per request.
- **Encrypted at rest?** Message text and titles are encrypted in the database (AES-256-GCM). The key is a file on the same server. Backups carry the same encryption and are sealed again under a passphrase.
- **Who can read a conversation?** The person who wrote it. No administrator screen exposes it. There is no content-review feature in this release. A person with direct access to the server's files and its key could read stored text.
- **Does the model server see it?** Always, in plain text, while it answers. That server is whichever backend the administrator configured; the panel states whether it is on the organisation's side or an **external service** (external backends are off by default and block until the owner confirms).
- **No-save conversations:** not written to the chat tables; the browser holds the transcript. Usage counts without text (time, model, token counts when the backend reports them) are still recorded for quotas. The model server and operating system may keep their own traces (logs, swap) that Semechka cannot control: check your inference engine's logging.
- **Retention:** conversations get an expiry when created from `privacy.chat_retention_days` (organisation, role or team scope; the shortest wins). A sweeper deletes expired conversations every 5 minutes. Backups made earlier still contain them until the backup itself is deleted; there is no automatic backup expiry yet.
- **Logs:** the audit log records who changed what, never message text. Application logs contain no message text.
- **Not provided:** end-to-end encryption, user-held keys, legal hold, certified compliance of any kind.

## Knowledge collections, assistants and the code workspace

- **At rest:** collection names and descriptions, document names, every passage of document text, assistant names/descriptions/instructions, and the list of sources attached to each reply are encrypted with the same server-held key as messages (separate derived keys). The database file, its WAL and backups contain no readable document text (tested by scanning the bytes).
- **No plaintext search index on disk.** SQLite FTS5 needs readable terms, so the search index exists only in memory: when you search, the collection's passages are decrypted into an in-memory FTS5 table, which is kept for up to 10 minutes of idleness (at most 8 indexes) and discarded when the collection changes or is deleted. Plaintext therefore lives in the Semechka process's memory (which already holds the key) and nowhere on disk. A memory dump of the running process would expose it. I chose this over blind/HMAC term indexes because those leak term frequencies and still need the key.
- **Who can read a collection:** the person who created it; for a team collection, that team's members (read), and the creator or team managers (change). Administrators, including the owner, do not get a view of other people's collections. Access is re-checked on every message: leaving the team, losing `mode.knowledge` or deleting the collection stops its use immediately (a denied permission blocks the conversation until it is detached).
- **What the model backend sees:** your question plus the best-matching passages, readable, like any other prompt text. The protection panel says this when knowledge is available.
- **No-save conversations** may use collections; the retrieval is read-only and nothing about the conversation or its sources is stored. Usage counts are still recorded.
- **Assistant instructions** are readable by their editors and, by definition, are sent to the model for everyone who uses the assistant; anyone using it can ask the model to repeat them.
- **Audit log:** records that a collection, document or assistant was created/changed/deleted and who did it, never names or text. Queue cancellations by operators are recorded without request content.

