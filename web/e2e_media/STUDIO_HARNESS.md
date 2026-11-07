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

## Imajutsu (native image jobs)

`fake_engine.py` also publishes the six image models of the engine with their REAL capabilities (`backend/tests/fake_image.py`): `flux2-klein-4b` (txt2img, edit, inpaint; 4 pictures), `qwen-image-edit-2511` (edit; 3 pictures),
`qwen-image-2512` (txt2img, img2img, inpaint), `z-image-turbo` (txt2img, img2img, inpaint), `qwen-image-2.1` (txt2img), `sdxl-turbo`. **`web/e2e_media/setup.ts` must list them in `enable_models`**
(`image:flux2-klein-4b`, `image:qwen-image-edit-2511`, `image:qwen-image-2512`, `image:z-image-turbo`, `image:qwen-image-2.1`, `image:sdxl-turbo`); the double already serves them. `POST /__reset` keeps them.

What the double does (as the real engine will):
* `POST /v1/uploads` keeps pictures and masks byte for byte (with width/height of PNG/JPEG); `POST /v1/jobs {kind:"image", simple:{...}}` accepts `reference_image`, `extra_references` (list), `mask_image`, `denoise`, `batch_size`,
  `seed`, `steps`, `guidance`, `width`, `height`, `scheduler`, `negative_prompt`. It refuses with a 422 what the engine refuses: more pictures than the model's cap ("flux2-klein-4b takes at most 4 pictures (got 5); send fewer"),
  `extra_references` without `reference_image`, a mask without a picture or on a model that cannot inpaint, a picture for a text-only model, sizes that are not multiples of 16 (8 for sdxl-turbo), a negative prompt for a model without one.
* A job of these models is a scripted native workflow run, stepped like the OpenJutsu ones (same `POST /__control/advance?n=&job=` and `POST /__control/config`; the default is instant): ticks `queued`, `loading_model`, `encoding_prompt`,
  `sampling` (step 0, then up to 4 progress steps up to `steps`), `decoding`, `saving_output`, `done`. Inspect documents have `backend: "sarness-workflow"`, `graph_kind: "sarness-workflow"`, node states (`model, prompt, ref, ref_resize, encode, ref_N, mask,
  latent, sampler, decode, save`), `sampling {step, max}`, `conditioning.references` (+ `mask`), `output.file {width, height, bytes, format}`. `GET /v1/jobs/{id}/graph` -> `{graph_kind, nodes:{id:{class_type, title, inputs}}}`,
  `/history` -> 404 `no_graph`, `/preview` -> a real small JPEG from the first sampler step on (404 before), `/segments/n` -> 404.
* `variant_next: "fail"` (or a prompt containing `boom`) makes the NEXT image job fail inside the sampler: error naming node `sampler` / "Sample image", server path replaced by `<path>`. `inspect_down`, `forget_traces` work as for OpenJutsu.
* The result is a real PNG of exactly the requested size (a smooth, seed-dependent pattern; the same seed and size give the same bytes).
* `GET /v1/models/{id}/estimate?width=&height=&steps=`: `measured` for flux2-klein-4b at 720x1280x4 (4 s, 31500 MB) and z-image-turbo at 720x1280x9 (12 s) and qwen-image-2512 at 720x1280x50 (144 s), `extrapolated` for other sizes of those, `none` for the other models.
* `GET /__state` additionally returns `image_submissions` (for every accepted image job: `job, model, engine_mode, simple` = the exact params, `pictures[]` and `mask` with upload id, name, mime, size, sha256, width, height) and `model_estimates`.
  `/__state.uploads` has the size/sha256 of every upload.

From Semechka the UI calls `/api/imajutsu/...` (`docs/IMAJUTSU_BACKEND.md`) and the Studio's inspector routes for the live job. A typical browser test: create the project -> `PUT` the spec -> Generate -> `POST :8841/__control/config {"clock":"manual"}`
before generating, then `POST :8841/__control/advance?n=4` -> the strip shows sampling and the preview; `advance?n=100` -> completed and the pictures are in the gallery.
