# Migration rehearsal (copies only; live data untouched)

Data: `~/spark-reset-offbox/backup-20261110-181452/semechka/{salted,roasted,staging}` copied to a scratch folder (never edited in place).
Procedure per instance: (1) `migrate_media_to_engine.py --apply` against the catalogue the LIVE engine publishes today (no OpenJutsu profile: this reproduces
production's current state, OpenJutsu row parked unavailable), (2) the same script against the new engine's catalogue (dry run, then `--apply`), (3) `--rollback`.

| Instance | After (1) | Dry run (2) lists | After apply (2) | Rollback (3) |
|---|---|---|---|---|
| Salted | `video:openjutsu-minimax-h3` enabled=1 available=0, 2 grants | `RESTORE ... enabled=1 -> available again; grants and enabled flag unchanged` | available=1, enabled=1, 2 grants (unchanged); `openjutsu-reference-video` added disabled, no grants | "restored to the backed-up state: YES" |
| Roasted | same | same | same | YES |
| Staging copy (with its old `openjutsu-reference-video` row) | both OpenJutsu rows parked | two RESTOREs; `--grant-profiles` also grants/enables `opensfield-video-edit` | both available, enabled flags and grants unchanged; the Studio opened, a vehicle replacement ran (docs/MEDIA_ENGINE.md section 9) | not run |

Invariants checked by the script on every apply: users, conversations, messages, media assets/jobs (counts and hashes), password hashes, non-media models, other backends, grants
and identity of every pre-existing model. A second `--apply` reports "Nothing to change".

Operator notes: after `--apply` run the instance's own catalogue sync once (admin > backends > sync, or restart) so the Studio sees the engine's CURRENT operation statuses; `openjutsu-reference-video`
and `opensfield-video-edit` are new rows (disabled, no grants) on Salted/Roasted: enable them and grant them in admin when wanted (or `--grant-profiles` for the editing profile).
