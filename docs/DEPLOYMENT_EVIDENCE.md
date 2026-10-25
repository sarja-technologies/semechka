# Deployment evidence (DGX Spark)

The newest release comes first. Nothing here contains a secret.

## 0.4.0 release hardening (2026-10-25, same version, redeployed)

### What changed
- **Chat models are no longer loaded ahead of time on the Sarness Engine**, because the engine switches its one chat
  model for every client even mid-reply and offers no reservation. Instead the queue never runs two chat models at
  once on such an engine, and a reply that would make the engine switch waits until no client is mid-reply.
- **"Stop worker"** in the Running tab frees the memory the picture/video worker keeps after its models are freed.
- **Sign-in answered before its session was stored.** Every database change is now committed before the answer.
- The setup token is no longer written to the service log; the verification scripts remove what they create.

### The unexplained 401, explained
The 401 seen during the 0.4.0 rehearsal (`check.py` right after a re-upgrade) came from `GET /api/me` straight
after a successful sign-in. FastAPI 0.118 and later run a request's dependency clean-up, where Semechka committed,
after the response is sent, so the session cookie could arrive before its row existed. Shown three ways:
- locally, with commits slowed down on purpose: sign-in then `/api/me` fails every time before the fix and passes
  after it (`test_commit_before_response.py`, which also checks that sign-out has taken effect when it answers and
  that a failed commit answers 500 with no cookie);
- on a copy of Salted on the Spark with the previously deployed 0.4.0 build: 1 failure in 96 immediate sign-in
  checks spread over 16 restarts, on the first sign-in after the first start;
- on the same copy rolled back to 0.3.0: the first sign-in after every one of four starts failed the same way
  (restored, cold data makes the first commit slow).
With the hardened build: 0 failures in 96 checks over 16 restarts, plus every check after the 6 starts in the
rollback rehearsal and 5 restart cycles of sign-in, CSRF-protected write, sign-out, replay of the old cookie
(401) and a wrong password (401 `bad_credentials`). `test_restart_sessions.py` covers sessions across real
`semechka serve` restarts locally.

### Order of work
1. **Independent backup** of both instances: `~/semechka-deploy/backups/pre-0.4.0-hardening-20261025-105215/`
   (SQLite backup API, `PRAGMA integrity_check` = ok, schema 6, master keys, assets, `SHA256SUMS`, mode 700).
2. **Rehearsal on a copy of Salted** in `~/semechka-deploy/rehearsal-041/` (loopback port 8797, `--no-service`):
   deployed build → hardened build, the sign-in checks above, then three cycles of the documented rollback paths.
   The copy was deleted afterwards (it held a copy of the master key).
3. **Roasted**, then **Salted** once idle (no connections, last message 7 hours earlier, no job or reply running).
   Installer snapshots: `roasted/data/pre-update/20261025-110745-from-0.4.0/`, `salted/data/pre-update/20261025-111346-from-0.4.0/`.
   The deployed build's tarball is kept as `releases/superseded/semechka-0.4.0-f11066bf.tar.gz`.
4. Neither engine was restarted (`sarness-engine` pid 61700, `semechka-sarness` pid 352517 before and after).

### Rollback, rehearsed three times on the copy
| Step | Result (each of 3 cycles) |
| --- | --- |
| `--rollback --restore-data` | 0.4.0 → 0.3.0, data from `pre-update/…-034655-from-0.3.0` (schema 5); 0.3.0 starts |
| re-update to the hardened build | schema 6; sign-in checks pass |
| `--rollback` alone | 0.3.0 refuses the schema-6 data and says why |
| then `--restore-data` alone | same schema-5 snapshot restored; 0.3.0 starts |
| re-update | schema 6; sign-in checks pass |

### After deployment
`verify_deployment.py` (from another machine on the LAN): **35 passed, 0 failed**, including "verification
conversation removed — 1 of 1" and "Salted is left with exactly the conversations it had before the run — 9 before,
9 after". `spark_salted_check.py`: **14 passed**, ending with the same two checks. Roasted still shows its wizard,
accepts its current token, and its journal since the update names the token file but does not contain the token.

Real engine, local instance through the tunnel: one SDXL Turbo picture (512×512, 4 steps, 12 s) started the worker
(6,913 MiB of GPU memory; host memory in use 15,382 MB). "Stop worker" then ended it and memory in use went back to
5,684 MB (5,681 MB before the picture). Both audit entries were written. The test conversation was deleted.

### Secrets
A script compared the real values (Salted's test passwords, the engine token, both master keys in raw, hex and base64
form, Roasted's current setup token, and every setup token printed in a session transcript) with: the whole Git
history of every branch, the working tree including `docs/` and screenshots, ignored build and test output, the
release tarballs, this work's scratch logs, the Spark's user journal, deployment logs, installed releases, both
data directories (apart from the key and token files themselves) and the backups. Only names and counts were printed.
- Git history, working tree, docs, screenshots, tarballs: **no match**.
- The Spark user journal: the current and the previously exposed Roasted setup tokens, printed by the server at
  start (fixed in this build; old journal entries were left as they are).
- Roasted's `backend-hints.json` and its backup copy hold the engine token by design (the wizard's pre-filled engine
  connection; deleted when the wizard completes). The local development copy keeps the engine token in its own
  owner-only files.
- The setup token exposed earlier no longer works: Roasted answers 403 `bad_setup_token`, and its hash is not the
  stored one.

### Verification leftovers in Salted (not removed)
Nine conversations in the member test account were made by earlier verification and chat-probe runs (six with the
verification prompt "Reply with one short sentence about the sea.", three with an earlier wording of it). They were
not deleted: they are listed in the final report for the owner to decide.

### Browsers
Playwright: 78 passed in Chrome and 78 passed in Firefox 158 (system Firefox over WebDriver BiDi). Changes to make
the tests valid in Firefox, none to the product: fields of type email and number are cleared before refilling (the
BiDi driver appends otherwise); reduced motion and a touch screen come from Firefox preferences (the driver ignores
Playwright's emulation); the focus test opens each screen in a new tab (headless Firefox does not give focus back
after Tab leaves the page); axe on 23 administration pages gets three times the time; a test waits for a filtered
list before ticking a row.

## 0.4.0 (2026-10-25)

### Order of work
1. **Independent backup** of both instances: `~/semechka-deploy/backups/pre-0.4.0-20261025-033822/` (each database
   through the SQLite backup API, `PRAGMA integrity_check` = ok, schema 5, master keys, assets, `SHA256SUMS`).
2. **Rehearsal on copies** made from that backup, in `~/semechka-deploy/rehearsal-040/`, on loopback ports 8797
   (Salted copy) and 8798 (Roasted copy), installed with `--no-service`. The production services were not touched.
3. **Production**, Roasted first (it is the disposable instance), then Salted once nobody was connected to it (no
   open connections, last message 4 hours earlier, no unfinished picture or video job). The installer wrote its own
   snapshots: `roasted/data/pre-update/20261025-034543-from-0.3.0/` and `salted/data/pre-update/20261025-034655-from-0.3.0/`.
4. **Findings from the deployed build** were fixed, tested locally and redeployed to both (same version, snapshots
   `…-040417-from-0.4.0` and `…-040430-from-0.4.0`); see "Fixed after the first deployment".
5. The rehearsal directory was deleted, because it held copies of both master keys. Its logs were kept locally with
   the setup token redacted.

### Rehearsal on copies
| Check | Result |
| --- | --- |
| Migration 0.3.0 → 0.4.0 (schema 5 → 6) on both copies | pass; integrity ok; no table lost; row counts identical except `meta` (+1) |
| 14 functional checks on the upgraded copies (the same script as "After deployment", plus the Roasted copy's wizard) | all passed, including a real streamed reply from the engine |
| `--rollback` (program only) | 0.3.0 refuses to start on schema-6 data, as designed |
| `--rollback --restore-data` | rejected with "use --restore-data alone" when the program was already rolled back |
| `--restore-data` after a program-only rollback **and another update** | **failed at first**: it restored a snapshot already at schema 6, so 0.3.0 still refused to start |
| Same sequence from a fresh copy with the fixed installer | pass: the schema-6 snapshot was skipped with a message, the schema-5 one restored, 0.3.0 started on it with every row (10 users, 8 conversations, 16 messages, 43 model grants), then the update to 0.4.0 again: schema 6, all 14 checks pass |

**Installer bug found by the rehearsal.** A snapshot is named after the *program* that was installed. After a
program-only rollback, the next update writes a snapshot labelled `from-0.3.0` that already contains schema-6 data,
and `--restore-data` picked the newest one by name. It now restores the newest snapshot whose schema the installed
program can open, and says which ones it skipped (commit "installer restores a snapshot the program can open").

### After deployment
Both instances run 0.4.0 (wheel sha256 `31cba579…`, tarball `f11066bf…`), schema 6, integrity ok. Releases 0.2.0,
0.3.0 and 0.4.0 are recorded for `--rollback`. Neither engine was restarted: the original Sarness Engine is still
process 61700 (running since 22 October) and the dedicated engine still runs from 24 October. Roasted is still at its
first-run wizard (0 users), offering Personal, Business, Managed Private Hosting and Custom, with the installer's engine
connection; a wrong setup token gets 403. The wizard was **not** completed.

`scripts/spark_salted_check.py` against production Salted (signs in as the test accounts; the one conversation it creates
is deleted again):

```
[PASS] Salted: owner signs in after the upgrade
[PASS] member's conversations kept (8)
[PASS] oldest conversation opens and decrypts
[PASS] member picker: 2 models in 1 section(s): Balanced, Quick
[PASS] member picker carries no identifiers or engine state
[PASS] owner picker shows live state (13 usable, 0 listed as not available)
[PASS] engine panel reads the real engine (engine 0.4.0, chat model ready, 5850 MB used)
[PASS] member cannot read engine state (403)
[PASS] member cannot load a model through the API (403)
[PASS] model_operator role received the new model permissions
[PASS] member role received none
[PASS] member chat with Quick streamed a real reply in 0.3s (text omitted)
[PASS] the test conversation is removed again
[PASS] member's conversation count is back to what it was
ALL PASSED
```

`scripts/verify_deployment.py` (run without `--restart`; the update itself restarted both services, and the checks above
show accounts, conversations and settings survived it):

```
[PASS] Spark LAN address confirmed on its own interface — lo 127.0.0.1/8; enP7s7 192.168.20.193/24; docker0 172.17.0.1/16
[PASS] Spark hardware is the DGX Spark (aarch64, GB10)
[PASS] systemd user service sarness-engine is active and enabled
[PASS] systemd user service semechka-sarness is active and enabled
[PASS] systemd user service semechka-roasted is active and enabled
[PASS] systemd user service semechka-salted is active and enabled
[PASS] lingering is on (user services start at boot without a login)
[PASS] dedicated Sarness engine answers authenticated API calls — engine 0.4.0, 28 models installed
[PASS] engine rejects unauthenticated requests
[PASS] dedicated engine is loopback-only
[PASS] Semechka instances bind the LAN address, not every interface
[PASS] roasted runs the expected release — 0.4.0
[PASS] salted runs the expected release — 0.4.0
[PASS] Roasted reachable over the LAN and still showing the first-run wizard
[PASS] Roasted serves the wizard page
[PASS] Roasted has no users and no default admin
[PASS] Salted reachable over the LAN and set up — org 'Salted Test Lab'
[PASS] Salted test account can sign in
[PASS] Salted offers real models to a member — Balanced, Quick
[PASS] real streamed response received (many incremental events) — 21 events, first after 0.1s, total 0.2s
[PASS] the model ran on the Spark GPU (llama-server process holds GPU memory) — Qwen3.5 0.8B · Q8_0, gpu_layers 999, pid 462108 on edgexpert-7443
[PASS] conversation was saved and is readable by its owner
[PASS] a Salted account cannot sign in to Roasted
[PASS] a Salted session cookie is rejected by Roasted
[PASS] session cookies use different names per instance — Salted cookie ['smk_708e189b0d'] 
[PASS] Salted owner sign-in
[PASS] a Salted setting change does not appear on Roasted
[PASS] the Salted setting is restored
[PASS] instances have different master keys and separate databases
[PASS] secret files and data directories are owner-only — all 600/700
[PASS] no account password appears in any database file or log — 0 matches
[PASS] security headers present

32 passed, 0 failed
```

The verifier has 32 checks instead of 0.3.0's 38. Seven of the 38 come from `--restart`, which was not repeated.
Roasted is at its wizard now, so its two "set up by its owner" checks were replaced by three wizard checks.

### Real engine, local instance (0.4.0 build, same Spark engine through an SSH tunnel)
| Check | Result |
| --- | --- |
| Load Qwen3.5 0.8B from the Running tab, chat, unload | loaded in 2.1 s; real reply; engine back to "stopped" |
| Picture, SDXL Turbo ("Sketch") | 512×512, 4 steps, about 14 s; memory then freed from the Running tab |
| Video, Wan 2.2 TI2V 5B ("Clip") | 960×544, 17 frames at 12 fps (1.42 s), 8 steps, about 60 s; memory freed |
| After freeing | the engine keeps its picture/video worker process alive (memory in use about 5 GB above the start) |

### Fixed after the first deployment
Real-browser screenshots of production Salted (`docs/screenshots/0.4.0/`) found two things, and the screenshot script
found a third:
- The Running tab offered "Free memory…" for picture and video models the engine was not holding (it could only
  answer "Not loaded"), and "Load…" for remote models (always refused). Those rows now say "Loads when someone uses it"
  or "Remote: nothing to load".
- Unknown addresses showed the framework's bare "404 Not Found". There is now a "Page not found" page with a way back.
- `PATCH /api/me {"theme": null}` was ignored, so nobody could go back to the organisation's default theme. The
  screenshot script relied on it to restore the test accounts' preference, and left the owner and member test
  accounts on "Dark" (Salted's default is also Dark, so nothing looked different). After the fix was deployed, both
  preferences were cleared through the API and checked.

### Local suites
Backend: 281 passed, 8 skipped (opt-in real-backend tests). Playwright (real Chrome): 78 passed. `pnpm check`:
0 errors, 0 warnings. Design lint and contrast check: pass, 0 failing pairs.

### Incidents during this work
- **The installer printed Roasted's setup token** into the deployment output, because the redaction filter only
  matched strings of 32 or more characters. Roasted was restarted straight away. A new token is generated at every
  start while setup is pending, so the printed one stopped working (checked: the token file was rewritten and only the
  new token's hash is stored). Later deployments filter every line that mentions a token.
- **Earlier in this work, test passwords of the local development copy were printed** into the working session's
  output. They belong only to that local copy: compared by hash, none of them is a password of Salted's accounts.

### Not verified (0.4.0)
A screen reader; browsers other than Chrome (Firefox was run in the hardening pass above); phones measured directly; a host reboot; load progress (the engine
reports none); the 80+ GB models; side-by-side comparison with the Codex app itself (only its published screenshots).

## Roasted factory reset (2026-10-25)

Roasted became the disposable fresh-install sandbox, and Salted the permanent configured instance (see
DEPLOYMENT_SPARK.md). Its owner, who had set it up as "Banana Corporation Private AI", authorised erasing it.

| Step | Result |
| --- | --- |
| Instance boundaries, from the units rather than the ports | Roasted: `semechka-roasted.service`, `roasted/data`, its own venv. Salted: `semechka-salted.service`, `salted/data`, its own venv. They share only the dedicated engine (127.0.0.1:8752). |
| Independent backup before anything else | `backups/pre-reset-roasted-20261025-003806/`: database copied through the backup API (integrity ok, 1 user, 1 conversation), master key, the installer's pre-update snapshots, the unit file; 15 files in SHA256SUMS, 0700 |
| `semechka reset --instance salted` | refused, because Salted is not designated disposable. Salted kept the same process and 0 restarts |
| `semechka reset --instance roasted` | stopped, moved to `roasted/data.reset-backups/20261025-004324-before-reset/` (14 files, integrity ok, SHA256SUMS verified), started. Its own checks: wizard open, version 0.3.0 unchanged, 0 accounts, conversations and sessions, owner-only token, new master key, engine reachable from the wizard |
| Independent checks afterwards | `/api/public`: needs_setup true, default brand. `/api/me` without a session: 401. Session cookie name changed with the key (old cookies are no longer recognised). A wrong setup token is refused (403). With the real token, the hardware step shows the GB10 and the engine reachable, and the engine-test step lists 26 models. Setup was **not** completed. The unit file is byte-identical, and no unit or cron job refers to the reset |
| Browser (headless system Chrome, 1280 and 375 wide) | `/` goes to `/setup`, "Set up this server", token step of 9. No console errors, no failed requests, no old branding, no horizontal overflow |
| Salted, fingerprinted before and after (read-only) | no difference: process and restart count; row counts of every table; digests of users, conversations, settings, master key and assets |

## 0.3.0 (2026-10-24)

### Order of work
1. **Independent backup** of both instances before anything else: `~/semechka-deploy/backups/pre-0.3.0-20261024-233248/`
   (each database through the SQLite backup API, `PRAGMA integrity_check` = ok, master keys, assets, `SHA256SUMS`).
2. **Rehearsal on copies.** The copies came from backup-API copies of both production databases and keys. They ran in
   a scratch prefix on loopback ports 8797 and 8798, with the real 0.2.0 programs, and the 0.3.0 installer upgraded
   them. The production services were not touched during this step.
3. **Production**, one instance at a time with `scripts/deploy_spark.sh`: Salted first, then Roasted after Salted
   verified. The installer wrote its own pre-update snapshots, `salted/data/pre-update/20261024-233254-from-0.2.0/`
   and `roasted/data/pre-update/20261024-233409-from-0.2.0/`, and recorded `releases/0.3.0` beside `releases/0.2.0`.
4. **Verification** with `scripts/verify_deployment.py --restart` (output below).
5. The scratch copies were deleted, because they held copies of both master keys. The backup from step 1 and the
   installer's snapshots remain.

### Rehearsal on copies (before production)
| Check | Result |
| --- | --- |
| Migration 0.2.0 → 0.3.0 (schema 1 → 5) on both copies | pass; no table lost; row counts identical in every table except `meta` and the replaced setting rows |
| Roasted copy: `auth.mfa_required_admins = true` | became `auth.mfa_policy = admins`; the owner already had an authenticator, so nobody was locked out |
| Roasted copy: theme `paper`, Salted copy: theme `carbon` | became `light` and `dark`; the old values are kept in `meta.m5_theme` for the down-migration |
| Conversations written by 0.2.0 | open and decrypt under 0.3.0 |
| Member chat on the real engine (Salted copy, Quick) | streamed; first text after 2.2 s |
| Picture made in the same conversation (Sketch, `sdxl-turbo`) | completed in 15 s; 512×512, served as a valid PNG |
| Vision: "What colour is the boat?" with the picture attached | answered "Red" in 7.5 s. Salted offers no vision model, so as owner on the **copy only** `qwen3.6-35b-a3b` was enabled as "Seer". Production was not changed |
| Video animated from that picture (Clip, `wan2.2-ti2v-5b`) | completed in 48 s at the smallest size (960×544, 5 frames, 4 steps); `video/mp4`, 156,873 bytes |
| `--rollback` (program only) | refused to start (data schema 5, program schema 1), as designed |
| `--restore-data` alone after that | restored the 0.2.0 data; 0.2.0 started with its accounts and conversations |
| `--rollback --restore-data` in one step | pass |
| Re-upgrade of the rolled-back copy to 0.3.0 | pass |

The rehearsal found that `--rollback` could move *forward* after a program-only rollback, and that `--restore-data`
needed `--rollback`. The installer was fixed before production (commit "Installer: rollback never moves forward…").

### After deployment
Both production databases are at schema 5. No table has fewer rows than before. Roasted's MFA policy is `admins`.

```
[PASS] Spark LAN address confirmed on its own interface — lo 127.0.0.1/8; enP7s7 192.168.20.193/24; docker0 172.17.0.1/16
[PASS] Spark hardware is the DGX Spark (aarch64, GB10)
[PASS] systemd user service sarness-engine is active and enabled
[PASS] systemd user service semechka-sarness is active and enabled
[PASS] systemd user service semechka-roasted is active and enabled
[PASS] systemd user service semechka-salted is active and enabled
[PASS] lingering is on (user services start at boot without a login)
[PASS] dedicated Sarness engine answers authenticated API calls — engine 0.4.0, 28 models installed
[PASS] engine rejects unauthenticated requests
[PASS] dedicated engine is loopback-only
[PASS] Semechka instances bind the LAN address, not every interface
[PASS] roasted runs the expected release — 0.3.0
[PASS] salted runs the expected release — 0.3.0
[PASS] Roasted reachable over the LAN and set up by its owner — app 'Banana Corporation Private AI'
[PASS] Roasted refuses setup calls once set up
[PASS] Salted reachable over the LAN and set up — org 'Salted Test Lab'
[PASS] Salted test account can sign in
[PASS] Salted offers real models to a member — Balanced, Quick
[PASS] real streamed response received (many incremental events) — 24 events, first after 0.1s, total 0.2s
[PASS] the model ran on the Spark GPU (llama-server process holds GPU memory) — Qwen3.5 0.8B · Q8_0, gpu_layers 999, pid 439048 on edgexpert-7443
[PASS] conversation was saved and is readable by its owner
[PASS] a Salted account cannot sign in to Roasted
[PASS] a Salted session cookie is rejected by Roasted
[PASS] session cookies use different names per instance — Salted cookie ['smk_708e189b0d'] 
[PASS] Salted owner sign-in
[PASS] a Salted setting change does not appear on Roasted
[PASS] the Salted setting is restored
[PASS] instances have different master keys and separate databases
[PASS] secret files and data directories are owner-only — all 600/700
[PASS] no account password appears in any database file or log — 0 matches
[PASS] security headers present
[PASS] both Semechka services restarted (new processes)
[PASS] Salted accounts survive a restart
[PASS] Salted conversation history survives a restart
[PASS] Salted settings survive a restart
[PASS] Roasted keeps its state and branding across a restart
[PASS] the original Sarness Engine service was not interrupted — pid 61700
[PASS] dedicated engine still serving

38 passed, 0 failed
```

**Roasted had been set up before this work** (by its owner, as they later confirmed). It was expected to be at the first-run wizard. In fact its wizard was
completed on 2026-10-24 at 19:31 AEDT from a machine on the LAN, before the 0.3.0 work began, and no step of this
work called the setup API. Since then it has been treated as an owner-managed instance: the deployment never signed in
to it, and the verifier checks only its public state. Its configuration came through the upgrade unchanged: the
organisation name, its user and conversation, 26 enabled models, and its MFA requirement for administrators.

### Client performance, before and after
Measured with `web/perf/bench.mjs`. It ran on the same machine (Intel i9-11900F, headless system Chrome), against the
same local instance and data, and served the 0.2.0 build and then the 0.3.0 build. Each figure is a range over
3 runs. The long conversation has 200 messages, written by a real model, with code, tables and lists.

| Measure | 0.2.0 | 0.3.0 |
| --- | --- | --- |
| Open the long conversation | 166–250 ms | 152–189 ms |
| Longest main-thread task while opening | 63–87 ms | 0 (none over 50 ms) |
| DOM nodes after opening | ~3,140 | ~630 |
| Open, 4× CPU slowdown | 494–728 ms | 242–401 ms |
| Longest task, 4× CPU slowdown | 259–346 ms | 0–80 ms |
| Frame time p95 while a reply streams, 4× CPU | 50 ms | 16.8 ms |
| Frames over 50 ms while streaming, 4× CPU | 3–4 | 0 |
| Keystroke-to-paint p95 while streaming, 4× CPU | 56 ms | 16–32 ms |
| Keystroke-to-paint p95 while streaming, no slowdown | 32–40 ms | 16 ms |

Most of the gain in opening comes from rendering only the newest 40 messages, with "Show earlier messages" for the
rest; 0.2.0 rendered all 200. The streaming gains come from rendering Markdown incrementally and coalescing updates
to one per frame. These numbers are from one desktop machine; phones and low-end laptops were not measured directly.
Raw results: the JSON output of each run (not committed).

### Local suites on the release branch
Backend: 246 passed, 8 skipped (the opt-in real-backend tests). The 0.2.0 → 0.3.0 upgrade and downgrade run against
a real 0.2.0 database fixture (`test_upgrade_020.py`). Playwright (real Chrome, 4 viewports, axe, keyboard): 77
passed. `pnpm check`: 0 errors. Design lint and the contrast check: pass, 0 failing pairs.

### Not verified (0.3.0)
A screen reader; browsers other than Chrome (Firefox was run in the hardening pass above); phones measured directly; PostgreSQL; a host reboot; vision and video on
Salted's own configuration (it offers no vision model, so that run used the copy); the example frontend on the Spark (it was verified locally on a copy).

## 0.2.0 (2026-10-24)

Produced by `scripts/verify_deployment.py --restart`, run from a different machine on the LAN, against the live
services. It contains no secrets. Date: 2026-10-24.

```
[PASS] Spark LAN address confirmed on its own interface — lo 127.0.0.1/8; enP7s7 192.168.20.193/24; docker0 172.17.0.1/16
[PASS] Spark hardware is the DGX Spark (aarch64, GB10)
[PASS] systemd user service sarness-engine is active and enabled
[PASS] systemd user service semechka-sarness is active and enabled
[PASS] systemd user service semechka-roasted is active and enabled
[PASS] systemd user service semechka-salted is active and enabled
[PASS] lingering is on (user services start at boot without a login)
[PASS] dedicated Sarness engine answers authenticated API calls — engine 0.4.0, 28 models installed
[PASS] engine rejects unauthenticated requests
[PASS] dedicated engine is loopback-only
[PASS] Semechka instances bind the LAN address, not every interface
[PASS] Roasted reachable over the LAN and still showing the first-run wizard
[PASS] Roasted serves the wizard page
[PASS] Roasted has no users and no default admin
[PASS] Salted reachable over the LAN and set up — org 'Salted Test Lab'
[PASS] Salted test account can sign in
[PASS] Salted offers real models to a member — Balanced, Quick
[PASS] real streamed response received (many incremental events) — 25 events, first after 2.2s, total 2.4s
[PASS] the model ran on the Spark GPU (llama-server process holds GPU memory) — Qwen3.5 0.8B · Q8_0, gpu_layers 999, pid - on edgexpert-7443
[PASS] conversation was saved and is readable by its owner
[PASS] a Salted account cannot sign in to Roasted
[PASS] a Salted session cookie is rejected by Roasted
[PASS] session cookies use different names per instance — Salted cookie ['smk_708e189b0d'] 
[PASS] Salted owner sign-in
[PASS] a Salted setting change does not appear on Roasted
[PASS] instances have different master keys and separate databases
[PASS] secret files and data directories are owner-only — all 600/700
[PASS] no account password appears in any database file or log — 0 matches
[PASS] security headers present
[PASS] both Semechka services restarted (new processes)
[PASS] Salted accounts survive a restart
[PASS] Salted conversation history survives a restart
[PASS] Salted settings survive a restart
[PASS] Roasted is still at its first-run wizard after a restart
[PASS] the original Sarness Engine service was not interrupted — pid -
[PASS] dedicated engine still serving

36 passed, 0 failed
```

### Additional live checks (same day)

| Check | Result |
| --- | --- |
| Knowledge on Salted: upload a text file, keyword search, chat with the collection attached | Answer cited the uploaded passage `[1]`; `sources` event present; collection deleted afterwards |
| Disposable instance `scratch` on port 8799 (removed afterwards): install 0.1.0, update to 0.2.0 (pre-update snapshot written), rollback to 0.1.0 with `--restore-data` | pass |
| Same instance: full wizard run through the product API against the real engine, then `semechka backup`, wrong-passphrase restore refused, database and key destroyed, `semechka restore --force`, master key identical, service answers | pass |
| Same instance: engine stopped mid-use | chat reports "Backend unreachable", `/api/health` stays 200, chat works again within ~2 s of engine restart |
| Real Chrome (system Chrome, real viewport sizes 375, 768, 1280, 1920) on the live Roasted wizard and on Salted chat, knowledge, admin system, queue and image pages | No horizontal overflow on any page and size; screenshots inspected (Roasted 375, Salted chat 375, knowledge 1280, queue 768) |
| Local suites on the merged tree | backend 215 passed / 8 skipped; `pnpm check` 0 errors, 0 warnings; design lint pass; Playwright (real Chrome, 4 viewports, axe, keyboard) 77 passed |

### Not verified (0.2.0)
Docker image and Compose, PostgreSQL, CI, a host reboot (units are enabled and lingering is on, but the machine was not
rebooted), a screen reader, browsers other than Chrome, SSO against a real identity provider, the original Sarness
Engine's token (a second engine was created instead, see DEPLOYMENT_SPARK.md).
