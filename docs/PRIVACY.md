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
