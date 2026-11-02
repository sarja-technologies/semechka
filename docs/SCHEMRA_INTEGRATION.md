# Schemra integration (`/schemra/`)

Schemra is a separate application: a local AI training lab with its own API, database and GPU scheduling. Semechka puts
it behind its own sign-in: people open `https://<semechka>/schemra/`, Semechka checks their session and permissions, and
forwards the request to a Schemra running on the same machine with **signed identity headers**. The two programs share
nothing but that HTTP interface and one secret file. Semechka imports no Schemra code and opens none of its data; Schemra
imports nothing of Semechka. The Schemra side of the contract is `docs/LAB_API.md` §0 and §9 in the Schemra repository.

```
browser ──(Semechka session cookie)──▶ Semechka :8780 ──(signed headers, loopback)──▶ Schemra 127.0.0.1:8820
                                       /schemra/…                                     /…   (no cookie, no port exposed)
```

Off by default. With `schemra.enabled` off every `/schemra/…` address answers 404, signed in or not.

## Enable it

1. **Provision the shared secret** (once; both programs read the same bytes):

   ```bash
   # on the machine that runs both; the account that runs Semechka must own the file
   umask 077
   python3 -c 'import secrets; print(secrets.token_hex(32))' > ~/schemra-data/config/proxy.secret   # Schemra's copy
   install -m 600 ~/schemra-data/config/proxy.secret "$SEMECHKA_DATA_DIR/schemra-proxy.secret"        # Semechka's copy
   ```

   If both run as the same account you can instead point Semechka at Schemra's file and keep a single copy
   (`Environment=SEMECHKA_SCHEMRA_SECRET_FILE=%h/schemra-data/config/proxy.secret` in the systemd unit).
   Semechka refuses a file that is not a regular file, is not owned by its own account, is readable by group or others
   (`chmod 600`), or is shorter than 32 characters. **The key is the file's content with surrounding whitespace removed,
   taken as UTF-8 text** (the hex string itself, not decoded to bytes); Schemra must use the same rule. The file is
   re-read when it changes, so rotating it needs no restart: change both files, then expect a few seconds of 503/401.
   The secret is never stored in the database, never logged and never sent to a browser.

2. **Tell Schemra to trust the proxy**: `auth_mode: "proxy"` in Schemra, bound to `127.0.0.1` only.

3. **Switch it on** (Admin → Settings → Schemra, or the API; the change is audited and, because it widens what is
   reachable, asks for confirmation):

   ```bash
   curl -sS -X PUT https://<semechka>/api/admin/settings/schemra.enabled -H 'content-type: application/json' \
        -H "x-csrf-token: $CSRF" --cookie "$COOKIE_JAR" -d '{"value": true, "confirm_weaker": true}'
   ```

4. **Grant the permissions** (Admin → Roles, teams or a person's override): `schemra.view`, `schemra.operate`,
   `schemra.admin`. Only the Owner role holds them out of the box (it holds `*`); nobody else, including Platform Admin,
   until you grant them.

5. **Verify from outside**, as a person with a known role:

   ```bash
   SEMECHKA_CHECK_EMAIL=you@example.org SEMECHKA_CHECK_PASSWORD=... \
     python3 scripts/schemra_proxy_check.py https://<semechka> --expect-role admin
   ```

   It prints no cookie or token. Exit status 0 means the role arrived as expected, a signed-out request is refused, a
   cross-site POST is refused and nothing from the lab's own port leaks.

## Settings

All in the registry (`docs/CONFIGURATION.md`, category Schemra): validated, audited, reversible. The secret file's path is
**not** a setting (an administrator who could point Semechka at any file on the machine would be a needless risk); it is the
environment variable `SEMECHKA_SCHEMRA_SECRET_FILE`, default `<data dir>/schemra-proxy.secret`.

| Setting | Default | Meaning |
|---|---|---|
| `schemra.enabled` | `false` | Master switch. Off: 404 for everyone. |
| `schemra.upstream` | `http://127.0.0.1:8820` | Where Schemra listens. Only `http://` with `127.0.0.0/8`, `::1` or `localhost`, optional port, no path, no credentials. Checked again on every request. |
| `schemra.max_upload_mb` | 200 | Larger bodies: 413, before anything is forwarded if the length is declared, otherwise cut off while streaming. |
| `schemra.rate_per_minute` | 1200 | Per person, same in-process limiter as sign-in and sign-up. Over it: 429 with `Retry-After`. |
| `schemra.read_timeout_s` | 120 (min 35) | Silence from Schemra before 504. Connect timeout is fixed at 5 s. Schemra pings live streams every 15 s. |
| `schemra.stream_max_seconds` | 3600 | A `text/event-stream` is closed after this long (0 = never) so the browser reconnects and the session and role are checked again. |
| `schemra.audit` | `api` | `writes`: changes, refusals and errors. `api`: also reads under `/api/`. `all`: everything. |
| `schemra.csrf` | `token_or_origin` | See CSRF below. `token` is stricter. |

## Role mapping

Permissions are Semechka's ordinary ones (`perms.py`), resolved exactly like every other: grants from roles, teams and
person overrides, and any deny wins, wildcards included (`schemra.*`).

| Semechka permission held | Role sent to Schemra | Schemra permissions (LAB_API §0) |
|---|---|---|
| `schemra.admin` | `admin` | everything |
| `schemra.operate` (and not admin) | `operator` | read, chat, board.write, agent.run, train.run, data.write |
| `schemra.view` only | `viewer` | read |
| none | request refused: 403 with an explanation (JSON or, for a page, a small HTML page) | |

The strongest role the person holds is sent. Schemra enforces its own per-permission rules; the proxy only has to assert the
right role. Not signed in: the same as the rest of Semechka (401 JSON; a page navigation is redirected to
`/login?next=/schemra/…`). A suspended or deleted account, an expired or revoked session, or a pending MFA enrolment where
Semechka requires it (403 `mfa_enrolment_required`) all stop access immediately for new requests.

## The header contract

Semechka removes from the browser's request: every `X-Schemra-*`, `X-Forwarded-*`, `Forwarded`, `Via`, `Cookie`,
`Authorization`, `Proxy-*`, `X-Real-IP`, `True-Client-IP`, `X-Csrf-Token` (Semechka's own token never goes to Schemra),
`Origin`, `Referer`, `Sec-*`, `CF-*`, method-override headers, hop-by-hop headers and anything named in `Connection:`. It sets
`Host` to the upstream and `Accept-Encoding: identity`, and adds:

```
X-Forwarded-Prefix: /schemra
X-Schemra-User:  alice@example.org        the account's e-mail, lower case (the account id u_… if it has characters unsuitable for a header)
X-Schemra-Role:  viewer|operator|admin
X-Schemra-Ts:    1700000000                integer Unix seconds, taken per request
X-Schemra-Nonce: 32 random hex characters, new per request
X-Schemra-Sig:   hex HMAC-SHA256(secret, "v1\n"+METHOD+"\n"+PATH_WITH_QUERY+"\n"+TS+"\n"+NONCE+"\n"+USER+"\n"+ROLE)
```

`PATH_WITH_QUERY` is the request target **exactly as sent to Schemra**: the path below the prefix with the browser's
percent-encoding untouched, then `?` and the raw query when there is one (`/schemra/api/lab/boards?x=1` is signed as
`/api/lab/boards?x=1`; `/schemra/` as `/`). Schemra should verify against the raw request target
(`scope["raw_path"]` + `scope["query_string"]` in ASGI), not a decoded or re-encoded form; a path containing `%20` or
non-ASCII characters is where the two differ. Schemra's check (LAB_API §9): loopback peer, signature (constant-time
compare), `|now − Ts| ≤ 60 s`, nonce unseen in the last 120 s.

Response side: Semechka passes through only `Content-Type/Length/Encoding/Disposition/Language/Range`, `Accept-Ranges`,
`Cache-Control`, `ETag`, `Last-Modified`, `Expires`, `Vary`, `Location` (rewritten), `Retry-After`, `Allow`,
`X-Frame-Options` (`DENY` or `SAMEORIGIN` only), `Content-Security-Policy`, `X-Content-Type-Options`, `Referrer-Policy` and
`X-Schemra-*`. Everything else is dropped: `Set-Cookie` (Schemra cannot set cookies on Semechka's origin), `Access-Control-*`
(it cannot open Semechka's origin to other sites), `Via`, `Server`, `X-Powered-By`, `Link`, `Refresh`, `Content-Location`,
`Permissions-Policy`. `Public` in `Cache-Control` becomes `private`, and a response with no `Cache-Control` gets
`private, no-cache`, so a shared cache never serves one person's page to another.

Redirects: a `Location` that is root-relative (`/api/x`) gets the prefix; an absolute one for Schemra's own address becomes
`/schemra/…`; one already below `/schemra/` stays; a relative one stays; anything else (other host, other scheme, control
characters) is answered 502 and never reaches the browser. The lab's port appears in no header or error message.

**Conflicts with Semechka's own security headers.** Semechka's middleware adds its headers only where the response has none
(`setdefault`). So: the lab's `X-Frame-Options: SAMEORIGIN` wins over Semechka's default `DENY` (a lab page may frame a lab page;
nobody else may frame either). The lab's own `Content-Security-Policy` replaces Semechka's default one; when it sends none, the
proxy applies a strict one (`default-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; …`, no inline
script), so **a lab page with inline scripts needs its own CSP** or it will not run. In both cases the proxy adds a second
policy, `object-src 'none'; base-uri 'self'; form-action 'self'`, which the browser enforces alongside the first, and for every
address under `/api/` a third, `sandbox`, so a file somebody uploaded to the lab and later opened by its address runs in an
opaque origin without Semechka's session. `X-Content-Type-Options: nosniff` is always present. `Permissions-Policy` is always
Semechka's.

## CSRF

Semechka's mechanism is the per-session token in `X-CSRF-Token` plus an `Origin`-must-equal-`Host` check on unsafe requests.
For `POST/PUT/PATCH/DELETE` to `/schemra/…` the proxy requires one of:

* the session's CSRF token in `X-CSRF-Token` (scripts, the check tool, Semechka's own pages), or
* with `schemra.csrf = token_or_origin` (default): an `Origin` header equal to `Host` and, when sent, `Sec-Fetch-Site:
  same-origin`. This is how Schemra's own pages call their API: they are a different application served from the same
  origin and do not know Semechka's token.

A request with a foreign `Origin`, `Sec-Fetch-Site: cross-site|same-site`, `Origin: null`, or neither proof is refused with
403 (`csrf` / `bad_origin`) before anything is forwarded. Cross-site pages cannot forge `Origin` or `Sec-Fetch-Site`. `GET` and
`HEAD` need no proof, so **Schemra must never change state on a GET**. Set `schemra.csrf = token` for the strict form; Schemra's
pages then cannot save anything unless Schemra's front end sends the token.

## Streaming and limits

Bodies are never buffered: the first chunk of an upload reaches Schemra before the browser has sent the last one (peak memory of
Semechka stayed flat through a 200 MB upload and a 200 MB download in the test). `text/event-stream` responses are forwarded
chunk by chunk, uncompressed (`Accept-Encoding: identity` upstream, `Cache-Control: no-cache, no-transform`,
`X-Accel-Buffering: no`). When the browser goes away or the stream reaches `schemra.stream_max_seconds`, the upstream response
is closed immediately, which Schemra sees as a disconnect and uses to free the generator state of that stream. A person may hold
at most 32 open responses at once. WebSocket upgrades are refused (501).

## Audit

Every request that the level selects (`schemra.audit`) and every refusal or error writes one row to Semechka's hash-chained
audit log: actor (account id), action `schemra.request` or `schemra.denied`, target = the path without the query string, and
`{role, method, status[, reason]}` plus the client address. Never a body, a query string, a header or a secret. Audit writes
happen once the status is known and a failure to write is logged but does not fail the request.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| 404 on `/schemra/…` for everyone | `schemra.enabled` is off | Enable it |
| 401 | not signed in / session ended | Sign in |
| 403 `forbidden` | account holds none of `schemra.view/operate/admin`, or a deny rule covers it | Grant the permission (Admin → Roles) |
| 403 `csrf` / `bad_origin` | unsafe request without proof, or from another site; behind a TLS proxy that rewrites `Host` | Send the token; make the proxy preserve `Host` |
| 400 `bad_path` | `..`, `//`, `\`, encoded `.` `/` `\` or controls, spaces, non-ASCII | Fix the address |
| 413 | body above `schemra.max_upload_mb` | Raise it or send less |
| 429 | rate limit or 32 open responses | Wait; raise `schemra.rate_per_minute` |
| 501 | WebSocket / `Upgrade` | Not supported |
| 502 `upstream_unreachable` | Schemra is not running, wrong `schemra.upstream` | Start Schemra; check the setting |
| 502 `bad_redirect` | Schemra redirected to another host | Fix Schemra's redirect |
| 503 `misconfigured` | secret file missing, wrong mode/owner, too short; or `schemra.upstream` not loopback (the log says which; it never prints the secret) | `chmod 600`, fix the path/owner |
| 504 | Schemra silent for `schemra.read_timeout_s` | Look at Schemra |
| Schemra answers 401 to everything | the two secret files differ, the clocks differ by more than 60 s, or Schemra is not in proxy mode | Compare file hashes (`sha256sum` both, never print them), `chronyc tracking` |
| Schemra answers 401 only for some paths | its verifier decodes the path instead of using the raw target | See the header contract |

## Disable, roll back

* Instant: set `schemra.enabled` to false (Admin → Settings, or the API above). All `/schemra/…` answers 404 on the next
  request; no restart. Streams already open end within `schemra.stream_max_seconds`.
* Also revoke access without disabling: remove `schemra.*` from roles, or add a `schemra.*` deny.
* Settings history can roll the switch back (`POST /api/admin/settings-history/{id}/rollback`).
* Take the secret out of play: delete or rotate the file; every request then answers 503 until a valid one exists.
* Remove the feature completely: revert the branch merge. Nothing in the database depends on it (the settings are ordinary rows,
  the permissions are strings in role grants).

## Security notes

* **Same origin.** Schemra's pages are served from Semechka's origin. Script that runs in a Schemra page can call Semechka's API as
  the signed-in person (including reading the CSRF token from `/api/me`). Anything that lets an attacker run script on a Schemra
  page (stored XSS in a note, a file preview, an agent's message) is therefore as serious as XSS in Semechka itself. Mitigations
  here: the `/api/` sandbox policy, the CSP floor, `nosniff`, no cookies from Schemra, a strict default CSP. Schemra must also
  render notes and agent output as text/sanitised markdown, serve uploads with `Content-Disposition: attachment` where it can, and
  send its own CSP without `unsafe-inline`. The strongest separation (a different origin such as `schemra.<domain>`) is not
  possible with a path-prefix mount and is the recommended follow-up if Schemra ever shows untrusted HTML.
* **The HMAC contract (reviewed).** Sound for a loopback hop: HMAC-SHA256 over method, target, time, nonce, user and role, a 60 s
  window and a nonce cache. Notes, none requiring a change today:
  1. *The body is not covered.* An attacker who can alter loopback traffic can swap a body. Such an attacker can already read the
     secret-bearing memory or the file; accepted. A body hash cannot be added without buffering uploads, which this proxy must not do.
  2. *`X-Forwarded-Prefix` is not covered.* Only matters to someone who can reach Schemra's port with a valid captured signature
     inside the window; it only changes the `<base href>`. Schemra should accept only a prefix it was configured with.
  3. *Replay.* Inside 60 s a captured request can be replayed unless the nonce cache is checked; it must be (LAB_API §9). Cache memory:
     120 s of nonces at 1200 requests/min/person is small, but bound it.
  4. *Raw versus decoded path* (above). Ambiguity here fails closed (401) rather than open.
  5. *Key encoding* is under-specified in LAB_API §9 ("the secret"); this document fixes it (stripped UTF-8 text of the file).
  6. *Revocation granularity.* Identity is checked once per request, and once per stream: an open stream outlives a sign-out until
     `schemra.stream_max_seconds`. Lower it if that is too long.
  7. *Anything that can read the secret file can act as any user.* Mode 0600, owned by the service account, not in backups that others read.
  8. *`X-Schemra-User` is an e-mail address.* Schemra should treat it as an opaque key; if an administrator changes an account's
     address, Schemra sees a new person (Semechka's account id would be stable, at the cost of readability).
* Schemra's own port must stay bound to loopback and firewalled; the signature is a second line, not the first.
* Existing-design observations (not changed here): Semechka's `Origin` check relies on `Host` surviving the TLS reverse proxy;
  the rate limiter and lockout counters are in-process and reset on restart; the audit chain proves tampering of included rows, not
  truncation of the tail (all already listed in `docs/SECURITY.md`).

## Tests

`backend/tests/test_schemra_proxy.py` (with the recording fake lab `backend/tests/fake_schemra.py`): unauthenticated, disabled,
every role mapping including deny and suspension, spoofed headers and cookies, signature checked by an independent
implementation (wrong secret, 120 s skew, changed path, method or role), traversal and encoded forms over a raw socket, CSRF,
header filtering and port leakage, redirects, timeouts, loopback-only upstream (also when the validator is bypassed), secret-file
problems and rotation, audit content, rate limit, SSE incrementality and upstream close on disconnect, stream time limit,
upload streaming and limits, downloads, and the check script. Run: `pytest backend/tests/test_schemra_proxy.py`.

---

## Future Work Mode / container runtime

Semechka has a separate workstream to overhaul Work Mode around a containerised runtime. **This section is a specification for
that work; nothing here is implemented.** The sandboxed workspace must not hold Semechka's session or the shared secret. The
interface it should use to call Schemra:

1. **Same signed headers, same Schemra interface.** Schemra cannot tell (and need not know) whether a request came from a browser
   or from Work Mode: it sees `X-Forwarded-Prefix`, `X-Schemra-User/Role/Ts/Nonce/Sig` exactly as above.
2. **Semechka signs; the container never holds the shared secret.** The runtime calls a Semechka-side gateway on a private
   address (for example `http://host.docker.internal:<port>/internal/schemra/…`, reachable only from the run's network namespace).
   The gateway verifies a **per-run credential**, then performs the same proxy steps (path normalisation, header hygiene, rate
   limit, audit) and signs with the shared secret.
3. **Per-run nonce/secret.** At run start Semechka mints a random run secret (32 bytes) plus a run id, binds them in memory (or in
   an encrypted row) to `{run_id, user_id, workspace_id, expiry}`, and gives the container only the run id and secret. Each call
   is `Authorization: Bearer <run_id>.<HMAC(run_secret, METHOD "\n" PATH "\n" run_nonce)>` or, simpler, the run secret itself as a
   bearer token over the private link; a per-call `run_nonce` (monotonic counter or random, remembered for the run's lifetime)
   stops replays. The credential dies with the run (stop, timeout, failure) and expires on its own.
4. **A dedicated service role limited to `operator`.** The gateway asserts `X-Schemra-Role: operator` at most: the minimum of the
   initiating person's own Schemra role and `operator`. It never asserts `admin`, so a run can never reach `destroy`, `admin`
   (limits) or `PUT api/lab/limits`. If the person has only `schemra.view`, the run is `viewer`. If they have none, the gateway
   refuses the run's Schemra calls (403).
5. **Identity.** `X-Schemra-User` stays the initiating person's address (so board and note ownership remain theirs). The run id
   and workspace id go in Semechka's audit log (`schemra.run_request`, with the run id as target prefix), not in the signature
   contract. If Schemra later wants to tell machines from people, the compatible way is a reserved user form such as
   `svc:work/<run_id>` plus a Schemra-side owner mapping; that needs a Schemra decision and is not assumed here.
6. **Endpoint allow-list** the gateway should accept (everything else: 403), from LAB_API.md. The sandbox gets reading of boards
   and artifacts and agent messaging, no lifecycle of GPU work:

   | Purpose | Methods and paths | Schemra permission needed |
   |---|---|---|
   | Identity check | `GET api/lab/whoami` | read |
   | List and read boards | `GET api/lab/boards`, `GET api/lab/boards/{id}`, `GET api/lab/boards/{id}/cards`, `GET api/lab/boards/{id}/events.json?after=` (polling form; SSE only if the runtime can hold it) | read |
   | Read notes and files | `GET api/lab/notes/{card_id}`, `…/versions`, `…/versions/{n}`; `GET api/lab/files/{file_id}/content|versions|preview` | read |
   | Read artifacts | `GET api/lab/boards/{id}/artifacts`, `GET api/lab/artifacts/{id}`, `GET api/lab/artifacts/{id}/content` | read |
   | Resolve references | `GET api/lab/resolve?board_id=&agent_id=&ref=` | read |
   | Read datasets | `GET api/lab/datasets`, `GET api/lab/datasets/{id}`, `GET …/versions/{n}/preview` | read |
   | Read experiments and models | `GET api/lab/experiments/{id}`, `…/log`, `…/metrics`, `…/evaluation`; `GET api/lab/models` | read |
   | Read an agent | `GET api/lab/agents/{id}`, `…/messages`, `…/tool_calls` | read |
   | **Message agents** | `POST api/lab/agents/{id}/messages` (202), `POST api/lab/agents/{id}/cancel` | agent.run |
   | Answer a human gate | `POST api/lab/decisions/{id}` | agent.run (consider leaving this to people) |
   | Write a note (optional) | `PUT api/lab/notes/{card_id}` (with `base_version`), `POST api/lab/boards/{id}/cards` for `note` | board.write |
   | System status | `GET api/lab/system`, `GET api/lab/limits` | read |

   Deliberately excluded: `DELETE` anything, `PUT api/lab/limits`, creating experiments or queueing/cancelling training
   (`train.run`), dataset import, rights decisions and cleaning (`data.write`), agent creation and tool edits. Add them only with a
   per-run, user-visible approval step. Response bodies from Schemra go back into the sandbox as data; the runtime must treat agent
   and note text as untrusted input.
7. **Limits for runs.** Their own rate limit and a cap on concurrent streams per run, smaller than a person's; upload size cap
   (artifact write-back goes through Semechka's file service, not this route); the same audit levels.
8. **No browser path.** The gateway is not reachable from outside and never sets or reads Semechka's session cookie; a run's
   credential is useless against `/schemra/` or any `/api/…` route.
