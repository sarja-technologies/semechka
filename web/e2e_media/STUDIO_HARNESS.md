# OpenJutsu Studio test harness (engine double)

The Playwright media harness (`web/playwright.media.config.ts`) starts two servers: the REAL Semechka backend on `:8842` and an engine double on `:8841`
(`web/e2e_media/fake_engine.py`, built on `backend/tests/fake_sarness.py`). Run: `cd web && npx vite build && npx playwright test -c playwright.media.config.ts`.
To run only the double by hand:

    cd backend && PYTHONPATH=.:../web /home/dembele/Documents/GitHub/semechka/.venv/bin/python -m uvicorn e2e_media.fake_engine:app --app-dir ../web --port 8841

Token: `fake-sarness-token` (Bearer). The profile is `openjutsu-minimax-h3`, a copy of the real definition (`web/e2e_media/openjutsu-minimax-h3.json`)
with the contract 2.1 additions: `modes[8]`, `limits`, `reference_policy`, `subject_selection` (text + box, no points), `operation_status`,
`refs.max_files = 64`, and the new inputs `short_edge`, `subject_box`, `subject_points`, `subject_kind`.

## The scripted run

Every job posted to `/v1/profiles/openjutsu-minimax-h3/jobs` gets a deterministic script. One step = one TICK:

    tick 0 queued
    tick 1 starting_backend
    tick 2 loading_models        (loader nodes running)
    tick 3 tracking_subject      (SAM 3.1 detect running)
    tick 4-5 tracking done       -> conditioning known
    then per scene k = 1..N: stage "generating_scene" + sampler progress 3/6, next tick: 6/6, scene_saved (segment k now exists) + Loop End
    then assembling, saving_output (output.state "writing"), done (output.state "ready", status "completed", result file available)

N = ceil(used source seconds / clip_seconds (default 6)), 1..12; the double ffprobes the uploaded video. Force it with `{"scenes": 3}`.
The DEFAULT clock of the browser harness is "instant" (`clock: "calls"`, `step: 1000`): a job runs its whole script at its first status poll and simply completes, which is what the
older media specs expect. A Studio test that wants to LOOK at the states starts with `POST :8841/__control/config {"clock": "manual"}` and then steps with `/__control/advance`
(every `POST /__reset` returns to the instant default). The pytest double (`backend/tests/fake_sarness.py`) is also instant by default; the Studio tests switch it to manual in `studio_helpers.boot`.

Control endpoints on the engine port (no auth):

| Call | Effect |
|---|---|
| `POST /__control/advance?n=1&job=j_0001` | move the run(s) forward `n` ticks (all unfinished runs when `job` is omitted) |
| `POST /__control/config` `{"clock": "manual"\|"calls"\|"timed", "step": 1, "tick_s": 1.0, "scenes": 3, "variant_next": "fail", "inspect_down": true, "forget_traces": true, "forget_history": true, "break_agree": true}` | `calls`: every status poll `GET /v1/jobs/{id}` (what Semechka does every few seconds) advances `step` ticks (1 = slow walk, 1000 = instant). `timed`: one tick per `tick_s` wall seconds (for demos). `variant_next: "fail"` makes the NEXT job fail in scene 2 at node 63 "Trim Overlap" (any job whose text contains `boom` fails too). `inspect_down`: inspect/graph/history answer 503 (a hiccup; the Semechka inspector shows `stale`). `forget_traces`: they answer 404 `no_trace` (engine restarted). `break_agree`: `conditioning.agree` becomes false |
| `GET /__control/runs` | `{job: {tick, last_tick, status, scenes, variant}}` |
| `GET /__state` | `submissions` (EXACT `inputs` posted to the engine plus `files` = name/mime/size/sha256 of every uploaded file, `job`), `uploads`, `control`, `estimates` (calls to `/estimate`) |
| `POST /__reset` | forget submissions, uploads, runs; control settings back to the instant default |

Cancel: `POST /api/media/jobs/{id}/cancel` on Semechka cancels the run at its current tick (status `cancelled`, segments already saved stay readable).

## What the engine routes return

`GET /v1/jobs/{id}/inspect?after=` (contract 2.2 as the real engine implements it: `events` only with `seq > after`, `last_seq` = the cursor to pass back as `after`, `next_seq` = last_seq + 1; Semechka's own proxy maps this so the UI passes `next_seq`), `/graph`, `/history`, `/segments/{n}` (REAL tiny
64x112 H.264 MP4, Range supported, 404 until saved), `/preview` (JPEG from the newest segment, 404 before the first), and
`GET /v1/profiles/{id}/estimate?operation=&width=&height=&short_edge=&seconds=&refs=`. The final result is a real 1 s H.264+AAC MP4 at the
processing size (e.g. 1312x736), so players, export and ffprobe work on it. Times are deterministic (`T0 = 1793700000`, 7.5 s per tick).
Jobs of other profiles keep the old behaviour (finish after two polls) and answer inspect with 404 `no_trace`.

## From Semechka (what the UI calls)

The UI never talks to the engine: use `/api/studio/...` (see `docs/OPENJUTSU_STUDIO.md` section 3 and `docs/OPENJUTSU_STUDIO_BACKEND.md`).
A typical browser test: create the project through the UI -> Generate -> `POST :8841/__control/advance?n=5` -> the inspector shows tracking and
scene 1 -> `advance?n=100` -> completed.
