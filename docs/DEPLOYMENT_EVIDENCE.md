# Deployment evidence (DGX Spark)

The newest release comes first. Nothing here contains a secret.

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
A screen reader; browsers other than Chrome; phones measured directly; a host reboot; load progress (the engine
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
A screen reader; browsers other than Chrome; phones measured directly; PostgreSQL; a host reboot; vision and video on
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
