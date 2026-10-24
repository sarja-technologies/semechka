# Security model and threat model

Author's threat model. **Not independently reviewed.** Report vulnerabilities privately to the Sarja Technologies maintainers (open a private security advisory on the repository).

## Assets
Conversation text and titles, uploaded branding assets, credentials (password hashes, TOTP secrets, backend API keys, SMTP password), the master key, audit log, configuration.

## Adversaries and what is and is not defended
| Adversary | Defended | How | Not defended |
|---|---|---|---|
| Network eavesdropper on the LAN | partly | Semechka serves HTTP; **you must terminate TLS in a reverse proxy**. Cookies become `Secure` when the request arrives over HTTPS (or `X-Forwarded-Proto: https`). The protection panel tells users when their connection is plain HTTP. | Anything on an HTTP-only network. |
| Unauthenticated internet user | yes | No default account; first owner needs a one-time token (file mode 0600, deleted when used); signup endpoint is closed unless the mode allows it; per-IP and per-address rate limits; generic login errors with equal-effort password verification; lockout after 8 failures. | Distributed brute force across many addresses (no CAPTCHA). |
| Malicious website (CSRF) | yes | `SameSite=Lax` session cookie, per-session CSRF token required on every unsafe authenticated request, `Origin` must match `Host` on unsafe requests. | |
| XSS | defence in depth | Markdown rendered with DOMPurify; CSP `default-src 'self'` with no inline script; assets served with `sandbox` CSP, `nosniff`; SVG uploads with script constructs rejected. | A future bug in rendering code; SVG sanitisation is pattern-based, SVGs are shown only through `<img>`. |
| Ordinary logged-in user | yes | Server-side permission checks on every route; conversations are owner-scoped and other people's ids return 404; model ids cannot be probed (unknown and forbidden are identical); technical fields filtered server-side on every read; quotas server-side. | |
| Admin with partial rights | yes | No privilege escalation: you can only grant permissions you hold; only owners assign/alter owners; last active owner cannot be removed or suspended; invitations cannot exceed the inviter's rights; sign-up default role cannot carry admin rights; setting MFA-required cannot lock the acting admin out. | An owner is fully trusted. |
| Stolen database file or backup | yes | Message text/titles, API keys, TOTP secrets and secret settings are AES-256-GCM encrypted; password hashes are Argon2id; backups are additionally sealed with scrypt + AES-256-GCM under a passphrase. | The key is inside the backup (needed for restore): protect the passphrase. |
| Host compromise / root on the server | **no** | A running process holds the key and sees plaintext; the inference backend sees plaintext by design. | Everything. Limit host access. |
| Rogue/compromised inference backend | partly | Backends are explicitly configured; `external` backends are blocked unless `privacy.external_egress` is on; backend responses are streamed as text, not executed. | The backend reads every prompt sent to it. |
| Operator reading chats | partly | No admin screen shows others' conversations; `tech.*`, `ops.*`, `admin.*` never include content access; audit log has no content. | Someone with file/DB access plus the key. A dual-approval content-review workflow is **not built**. |

## Controls summary
Argon2id passwords (rehash on login) · session ids 256-bit random, stored hashed, rotated on login and password change, revoked on password reset/suspension · TOTP MFA (RFC 6238) with
server-enforced enrolment gate · rate limiting (signup, login, TOTP, forgot, setup) · security headers (CSP, nosniff, frame-deny, referrer, permissions-policy) · hash-chained audit log with
verification endpoint · settings changes validated, audited, reversible, with confirmation for weakening changes · secrets write-only and excluded from exports · bind to loopback unless
the owner sets `access.exposure=lan` (applied at next start) · systemd unit with `NoNewPrivileges`, `ProtectSystem=strict`.

## Residual risks to review before real use
1. No TLS in-process. 2. Single-process in-memory limiter/lockout counters reset on restart. 3. SQLite file locking limits write concurrency. 4. No CAPTCHA/passkeys/SSO.
5. Audit chain proves tampering of included rows, not truncation of the tail (no external anchor). 6. Search decrypts in process. 7. Master key lives beside the data; no KMS/HSM option.
