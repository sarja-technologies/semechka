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
| XSS | defence in depth | Markdown rendered with DOMPurify; CSP `default-src 'self'` with no inline script; assets served with `sandbox` CSP, `nosniff`; SVG uploads containing scripts, event-handler attributes, `foreignObject`/`iframe`/`use`/`image`/animation elements, `javascript:` or entities are rejected; logo/favicon settings accept only the 24-hex-digit asset ids the upload endpoint issues; support/public URLs must be http(s)/mailto. | A future bug in rendering code; SVG sanitisation is pattern-based, SVGs are shown only through `<img>`. |
| Ordinary logged-in user | yes | Server-side permission checks on every route; conversations are owner-scoped and other people's ids return 404; model ids cannot be probed (unknown and forbidden are identical); technical fields filtered server-side on every read; quotas server-side. | |
| Admin with partial rights | yes | No privilege escalation: you can only grant permissions you hold; only owners assign/alter owners; last active owner cannot be removed or suspended; invitations cannot exceed the inviter's rights; sign-up default role cannot carry admin rights; setting MFA-required cannot lock the acting admin out. | An owner is fully trusted. |
| Stolen database file or backup | yes | Message text/titles, API keys, TOTP secrets and secret settings are AES-256-GCM encrypted; password hashes are Argon2id; backups are additionally sealed with scrypt + AES-256-GCM under a passphrase. | The key is inside the backup (needed for restore): protect the passphrase. |
| Host compromise / root on the server | **no** | A running process holds the key and sees plaintext; the inference backend sees plaintext by design. | Everything. Limit host access. |
| Rogue/compromised inference backend | partly | Backends are explicitly configured; `external` backends are blocked unless `privacy.external_egress` is on; backend responses are streamed as text, not executed. | The backend reads every prompt sent to it. |
| Operator reading chats | partly | No admin screen shows others' conversations; `tech.*`, `ops.*`, `admin.*` never include content access; audit log has no content. | Someone with file/DB access plus the key. A dual-approval content-review workflow is **not built**. |

## Adversarial test suite

`backend/tests/test_security_hardening.py` (31 tests) and `web/e2e` run these attacks against a live app:
CSRF (missing, forged, another session's token, cross-origin `Origin`, including unauthenticated endpoints) · stored script payloads in names, titles, messages and branding returned only as JSON with `nosniff`, a CSP without `unsafe-inline` for scripts and `frame-ancestors 'none'` · active-content SVGs and path traversal on the asset route · IDOR across conversations, messages (send/edit/regenerate/active-leaf parents from another conversation), exports, usage and invites · **every route in the OpenAPI schema**: all non-public routes refuse an anonymous caller (401) and all `/api/admin/*` routes refuse an ordinary member (403), with real parameters, so a new unguarded route fails the build · setup endpoints closed after installation · technical read grants never confer operate rights · no secret (password, API key, TOTP secret, master key, password hash) in settings export, audit log, history, backend listings, user listings, logs, the database file bytes or a sealed backup · saved chat text is ciphertext in the database file and bound to its purpose key; no-save chat leaves no trace in the database file bytes · retention sweep · logout, expiry, idle timeout, suspension and deletion each invalidate a replayed cookie · lock-out, per-address login rate limit, sign-up and recovery rate limits · backup round trip, wrong passphrase, tampered file, short passphrase, nothing written on failure · two instances share no accounts, keys or cookie names.

Findings from this pass, all fixed: `brand.logo_asset`/`favicon_asset` accepted any string (now validated against issued ids); the SVG filter missed event-handler attributes and several active elements (now rejected); a message could be parented to a message id from another person's conversation (rejected with 404; before, only existence was checked, and the thread builder ignored it, so nothing leaked, but data integrity was wrong); the OpenAPI schema and Swagger UI were served to anyone (now off unless `SEMECHKA_API_DOCS=1`); the admin model list returned server file paths to every admin (now sanitised unless `tech.model.id`).

## Controls summary
Argon2id passwords (rehash on login) · session ids 256-bit random, stored hashed, rotated on login and password change, revoked on password reset/suspension · TOTP MFA (RFC 6238) with
server-enforced enrolment gate · rate limiting (signup, login, TOTP, forgot, setup) · security headers (CSP, nosniff, frame-deny, referrer, permissions-policy) · hash-chained audit log with
verification endpoint · settings changes validated, audited, reversible, with confirmation for weakening changes · secrets write-only and excluded from exports · bind to loopback unless
the owner sets `access.exposure=lan` (applied at next start) · systemd unit with `NoNewPrivileges`, `ProtectSystem=strict`.

## Residual risks to review before real use
1. No TLS in-process. 2. Single-process in-memory limiter/lockout counters reset on restart. 3. SQLite file locking limits write concurrency. 4. No CAPTCHA/passkeys/SSO.
5. Audit chain proves tampering of included rows, not truncation of the tail (no external anchor). 6. Search decrypts in process. 7. Master key lives beside the data; no KMS/HSM option.
