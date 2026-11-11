# Work Mode on Roasted (CMSIT-AI): admin-controlled rollout plan

Status 2026-11-11: Work Mode (upstream Goose in a locked container, `docs/WORK_MODE_GOOSE.md`) is **off for ordinary users on Roasted**
(`goose.enabled` and `modes.work.enabled` both stay at their shipped default `false` there). It runs on Salted and on staging. Nothing below has been done on Roasted.

## What was verified (before any rollout)

| Requirement | Result | Evidence |
|---|---|---|
| Isolation between two people | Bob gets **404** on Alice's session, transcript, events, files, file content and prompt; each container sees only its own `/work`; neither can see the other's file, `/home/spark`, the host data directory or `/run/user`; no network device but `lo`; router, LAN and internet unreachable; the router key is not in the container environment | `docs/WORK_MODE_GOOSE_EVIDENCE.md`, re-run on staging 2026-11-11 (`scripts/spark/accept_isolation.py`) |
| Container hardening | read-only root, `cap_drop ALL`, 512 pids, 4 GiB memory, uid/gid 65534, not privileged | same run, "container flags" |
| Per-user authorisation | Gate = `modes.work.enabled` + permission `mode.work` + `goose.enabled`; only a group that holds `mode.work` can start a session; the URL key is the conversation id and ownership is checked | `docs/WORK_MODE_GOOSE_SECURITY.md` section 3 |
| Approvals | Allow and deny both proven with a real model: a denied write left the file unchanged; 13 approvals answered over the API | `docs/WORK_MODE_GOOSE_EVIDENCE.md` |
| Cancel and resume | Cancel stops the running process in 0.4 s; the session resumes after a restart | same |
| Organisation separation | Salted and Roasted are separate instances with separate databases; inside an instance the Work session belongs to the conversation's owner (a second person in the same organisation gets 404) | isolation run |

## Not yet verified (decide before enabling for staff)

* The systemd conflicts in `WORK_MODE_GOOSE_SECURITY.md` section 6 step 3 (`PrivateTmp`, `ProtectHome`) on the **Roasted unit**; they were solved on Salted, so repeat the same drop-in and check the socket directory.
* A full Work session by a non-admin Roasted person (needs the owner's go-ahead; use a throwaway account, never a real person's).
* Memory: a Work session loads a 24 to 35 GB coding model; two people at once on top of chat models is possible but Flash-Next (94 GB) models are exclusive.

## Rollout steps (all done by an administrator in Roasted > Admin > Settings; each step is reversible)

1. **Prerequisites (no user impact):** build the image once (`scripts/spark/goose_image.sh build`, already built for Salted); apply the same unit drop-in as Salted; choose `goose.default_model` among models flagged `coding_agent_compatible`.
2. **Dry run with a throwaway admin:** `goose.enabled=true`, `modes.work.enabled=true`, and the permission `mode.work` **only for the Platform administrators group**. Ordinary users do not see the Chat/Work switch (permission-gated). Run `accept_isolation.py` on Roasted with two throwaway accounts, then delete them.
3. **Pilot:** add `mode.work` to one named group (for example "CMSIT Pilot - Work") and move only the people the owner names into it. Keep `work.network` off. Watch Admin > Audit for `work.*` events and the Spark Control memory view.
4. **General availability:** grant `mode.work` to the wider staff group. Optional: require approvals for every tool (the default) and keep it that way for non-administrators.
5. **Rollback at any step:** set `goose.enabled=false` (immediate; sessions stay on disk, containers are removed with `docker ps -a --filter label=semechka.goose`). The tables are additive and unused by older releases.

Do not enable steps 2 to 4 without the owner's explicit decision; this plan only prepares them.
