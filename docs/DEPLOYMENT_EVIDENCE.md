# Deployment evidence (DGX Spark, 0.2.0)

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

## Additional live checks (same day)

| Check | Result |
| --- | --- |
| Knowledge on Salted: upload a text file, keyword search, chat with the collection attached | Answer cited the uploaded passage `[1]`; `sources` event present; collection deleted afterwards |
| Disposable instance `scratch` on port 8799 (removed afterwards): install 0.1.0, update to 0.2.0 (pre-update snapshot written), rollback to 0.1.0 with `--restore-data` | pass |
| Same instance: full wizard run through the product API against the real engine, then `semechka backup`, wrong-passphrase restore refused, database and key destroyed, `semechka restore --force`, master key identical, service answers | pass |
| Same instance: engine stopped mid-use | chat reports "Backend unreachable", `/api/health` stays 200, chat works again within ~2 s of engine restart |
| Real Chrome (system Chrome, real viewport sizes 375, 768, 1280, 1920) on the live Roasted wizard and on Salted chat, knowledge, admin system, queue and image pages | No horizontal overflow on any page and size; screenshots inspected (Roasted 375, Salted chat 375, knowledge 1280, queue 768) |
| Local suites on the merged tree | backend 215 passed / 8 skipped; `pnpm check` 0 errors, 0 warnings; design lint pass; Playwright (real Chrome, 4 viewports, axe, keyboard) 77 passed |

## Not verified
Docker image and Compose, PostgreSQL, CI, a host reboot (units are enabled and lingering is on, but the machine was not
rebooted), a screen reader, browsers other than Chrome, SSO against a real identity provider, the original Sarness
Engine's token (a second engine was created instead, see DEPLOYMENT_SPARK.md).
