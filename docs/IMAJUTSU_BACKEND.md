# Imajutsu: Backend

What the Semechka server adds for Imajutsu (contract: `IMAJUTSU.md`; deviations and open points: `CONTRACT_NOTES_IMAJUTSU_BACKEND.md`).
The engine side is Sarness (native image runtime). The browser harness and the engine double are described in `web/e2e_media/STUDIO_HARNESS.md` ("Imajutsu").

## Files

| File | Role |
|---|---|
| `backend/semechka/imajutsu_routing.json` | the routing table: which engine model serves which mode, in order. DATA. |
| `backend/semechka/imajutsu_routing.py` | loader and validator of that file, the ten mode definitions |
| `backend/semechka/imajutsu_plan.py` | `plan()`: pure function (spec + model facts + source facts -> engine mode, native size, output size and scaling, final prompt, reference accounting, settings, warnings, errors) |
| `backend/semechka/imajutsu_presets.py` | the iPhone Photo prompt builder, the scene chips, the optional "phone finish" recipe |
| `backend/semechka/imajutsu_adjust.py` | photographic adjustments: ONE `render()` for the preview and the final file |
| `backend/semechka/imajutsu_pix.py` | ffmpeg + numpy pixel helpers (decode with EXIF, cover/scale, masks, outpaint canvas, `maskedmerge`) |
| `backend/semechka/imajutsu_svc.py` | spec validation, projects, capabilities, plan, generate, jobs, reopen, adjust / scale / keep-outside tasks |
| `backend/semechka/routers/imajutsu.py` | `/api/imajutsu/...` (HTTP only) |
| `backend/semechka/media_svc.py` | extended: `submit(extra_params=)`, `upload_inputs()`, `admit(n=)` |
| `backend/semechka/adapters/sarness_media.py`, `sarness.py` | `_IMAGE_KEYS` + `extra_references, mask_image, loras, upscale, batch_size`; `media_model_estimate`; `references` carried into `Model.meta.media` (also read from the registry) |
| `backend/semechka/media_lib.py` | origins `adjusted`, `scaled`, `composited`; asset JSON carries `imajutsu` |
| `backend/semechka/studio_inspect.py`, `routers/studio.py` | the inspector serves image jobs too (`graph_kind`, `workflow.available`) |
| `backend/semechka/routers/me.py` | `ui.operations.imajutsu = {enabled, reason}` |
| `backend/tests/fake_image.py`, `fake_sarness.py`, `web/e2e_media/fake_engine.py` | the engine double for native image jobs |

No new dependency: Pillow is not a Semechka dependency, so pixel work uses ffmpeg and numpy (both already required). No schema change: projects are rows of `studio_projects`
whose decrypted spec has `kind: "image"`.

## Routes (session cookie + CSRF; Image mode gate; owner only, a stranger's id answers exactly like a missing one)

```
GET    /api/imajutsu/capabilities
POST   /api/imajutsu/projects                 {mode?, source_asset?, title?}
GET    /api/imajutsu/projects                 summaries (image projects only), newest first
GET    /api/imajutsu/projects/{id}            spec, source, mask, jobs[]
PUT    /api/imajutsu/projects/{id}            {spec, expected_updated_at?, title?}   409 {code:"stale", project} when stale
DELETE /api/imajutsu/projects/{id}            never touches a picture
POST   /api/imajutsu/projects/{id}/plan       {spec?}  pure (+ the engine's estimate when it measured one)
POST   /api/imajutsu/projects/{id}/generate   {spec?, count?:1-4} -> {job, jobs[], job_detail, job_details[], batch, accounting, plan, forced_unsaved}
GET    /api/imajutsu/jobs                     jobs that kept their settings, with outputs and derived pictures
GET    /api/imajutsu/jobs/{job}               one row
POST   /api/imajutsu/jobs/{job}/reopen        -> {project, dropped[]}  the project set back to the job's snapshot (idempotent)
GET    /api/imajutsu/assets/{aid}/adjust/preview?recipe=<json>   image/jpeg, long edge <= 1024, cached by picture + recipe
POST   /api/imajutsu/assets/{aid}/adjust      {recipe, name?} -> {task}      new asset, origin "adjusted"
POST   /api/imajutsu/assets/{aid}/upscale     {width, height, name?} -> {task, scaling, method, label, ai:false, from, to}   origin "scaled"
POST   /api/imajutsu/assets/{aid}/keep-outside {feather?, name?} -> {task}   origin "composited"
```
Tasks are polled at `GET /api/media/edits/{task}` like any edit. Cancel and delete of a job: `/api/media/jobs/{id}/cancel`, `DELETE /api/media/jobs/{id}`.
The live inspector is the Studio's: `GET /api/studio/jobs/{job}/inspect | inspect/stream | graph | history | preview` accept image jobs (ownership by `MediaJob.user_id`, the mode gate of the job's kind).
For an image job: `graph_kind: "sarness-workflow"`, `comfy.available: false`, `workflow.available: true` (the page shows VIEW WORKFLOW and hides VIEW COMFYUI); `history` answers 404 `no_graph`.

## The routing table

`imajutsu_routing.json` (or `<data_dir>/imajutsu_routing.json`, which wins when it is valid; a broken one is reported in `capabilities.routing.problem` and ignored):

```
{ "version", "updated", "pixels": 921600, "avoid_words": ["phone", "9:16", ...],
  "models": { "<engine_id>": {"native": {"9:16": [720,1280], ...}, "quality": {"draft":2,"standard":4,"high":8}, "avoid_phone_words": true, "slow": true, "seconds_hint": "..."} },
  "modes":  { "<mode>": {"models": [ {"engine_id", "why", "evidence": [...], "engine_mode"?, "avoid_phone_words"?, "native"?, "quality"?, "pixels"?} ], "refine": [...] (upscale), "scaling": {...} (upscale)} } }
```
An entry is merged over the shared facts of its model (`native`, `quality` {draft, high}, `wording` natural|plain, `avoid_words`, `tier` fast|best|polished, `slow`, `seconds_hint`). The standard step count is never written here: it is the engine's own default for the model.
iPhone Photo lists three tiers (fast = flux2-klein-4b, the default; best = qwen-image-2512; polished = z-image-turbo); `preset.tier` in the spec picks one.
An entry is merged over the shared facts of its model. The list is ordered by preference. Rules: a model is **operational** for a mode when it is installed, the person may use it and the engine's
`capabilities.modes` really contains the entry's engine mode (default per mode: generate/iphone_photo txt2img, edit/replace_subject/reference edit, inpaint/outpaint inpaint, restyle img2img or the entry's own;
Replace Subject and Reference also need room for the pictures: `capabilities.references`). The **recommended** model is the first operational one; its `why` and `evidence` are shown. A mode with no operational model is
`unavailable` with the reason. Models installed but not listed appear as `available` (`routed: false`): they can be chosen by hand, are never recommended. Reordering the file is all it takes to change the recommendation.

## Spec v1 (images)

`version, kind:"image", mode, prompt, negative, preset {id, scenes[], finish, tier}, aspect {id, w, h}, size {native|null, output|null}, quality draft|standard|high, seed|null, count 1-4, source_asset, mask_asset,
selection {box|null (fractions x,y,w,h), feather px, invert, keep_outside}, outpaint {pad{left,top,right,bottom}|null, target{width,height}|null, anchor{x,y}, overlap px}, upscale {refine, strength},
references [{asset, role|null (identity|appearance|composition|lighting|style), preserve, active, label}] (any number up to 200), model|null (auto), advanced {steps, guidance, scheduler, denoise}, adjustments {recipe}`.
Unknown top-level keys are ignored; wrong values and unknown `advanced` keys are a 422 with a sentence. `aspect.id` is `9:16, 16:9, 4:5, 3:4, 1:1, custom (w,h any numbers, 1:5 to 5:1)` or `source`
(the shape of the source picture; the default for modes that start from one).

## The plan

Native size: a size written in the routing file for the aspect and model when it is valid for the engine's `limits` (`multiple` - 16 when the engine says nothing, `min_side`, `max_side`), else computed:
multiples of `limits.multiple`, about `pixels` (default 0.92 MP), the shape as exact as the multiples allow (9:16 -> 720x1280). Output = `size.output` (default = native) and `scaling` is `none | upscale | downscale`
(a different shape is an error). **Generate returns the engine's native size**; the output size is reached with `POST /assets/{aid}/upscale` (Lanczos, labelled "Scaled, no new detail").
Pictures: the source is picture 1 for edit-style modes and counts against `capabilities.references`; only ACTIVE references, in order, up to the room left are sent; every other one is in `references.not_used` with the reason;
`prompt.sentences` is one sentence per used picture (the roles are words in the prompt; there is no role input in the engine). `estimate` appears only when `GET /v1/models/{id}/estimate` says `measured` or `extrapolated`.

## Generate

Order of checks (each a plain 422): spec; plan errors (`plan_errors`, with the plan); model access; engine parameters through `media_svc.validate_params`; quotas for ALL variations at once (`admit(n=count)`: all or none).
Then: source, mask and references are prepared (EXIF orientation applied, source cover-fitted to the native size, outpaint canvas padded with neutral grey and its mask of the new area, box masks drawn, feather/invert applied, references
scaled to at most 2048 px and sent as PNG/JPEG), uploaded to the engine ONCE, and `media_svc.submit` creates ONE ordinary image job per variation (seeds `seed+i`; random base when no seed). Mask polarity: white = change.
`params.imajutsu` of every job keeps `{project_id, mode, engine_mode, model, spec (ALL references, active or not, with roles), mask_asset, selection, adjustments, plan, accounting, batch {id,index,count,seed}}`;
the same block rides on the result assets (`asset.params.imajutsu`). Organisations that store nothing (`privacy.chat_retention_days = 0`) keep no block: their jobs are not listed but run, can be inspected live and give in-memory results.

## Adjustments (recipe v1)

`exposure -2..2 EV (linear light), contrast, saturation, temperature, tint, highlights, shadows -100..100, sharpness 0..100, grain 0..1 (+ optional grain_seed), vignette 0..1, rotate 0|90|180|270 (clockwise), flip,
crop {x,y,w,h} (pixels after turning/flipping), straighten -10..10 degrees (clockwise, largest same-shape rectangle kept), output {width,height} (same shape, Lanczos)`. Order: geometry (turn, flip, crop, straighten, output size), then
exposure, white balance, highlights/shadows, contrast, saturation, sharpness, vignette, grain. The preview and the final call the same `imajutsu_adjust.render()`; the preview only adds a last downscale (long edge 1024, JPEG).
Grain is a seeded field on a canonical 1024-pixel grid resampled to the picture. The original is never touched; the result's metadata carries `imajutsu: {kind:"adjusted", recipe, parent}`.
EXIF orientation is applied on every source (the stored pixels are turned; the result carries no orientation tag).

## Tests

`cd backend && /home/dembele/Documents/GitHub/semechka/.venv/bin/python -m pytest -q -p no:cacheprovider tests/test_imajutsu_*.py` (needs ffmpeg and ffprobe; real pictures are made on the fly with numpy + ffmpeg; the only double is the engine, `tests/fake_sarness.py` + `tests/fake_image.py`).
Fixture `im` (conftest): a signed-in owner on an install whose engine publishes the six image models with their real capabilities; the scripted runs are stepped with `fs.advance(n)`.
Files: `test_imajutsu_plan.py` (pure: sizes, scaling, reference accounting 1/3/4/5/9/12, prompt builder, routing), `test_imajutsu_adjust.py` (numbers on real pixels), `test_imajutsu_generate.py` (what the engine received),
`test_imajutsu_inspect.py`, `test_imajutsu_caps.py` (matrix, reorder, navigation, estimate, isolation), `test_imajutsu_double.py` (the double itself).

## Rollback

Additive only: new routes, new optional fields, new asset origins (`adjusted`, `scaled`, `composited`), image projects as rows of the existing `studio_projects` table. No migration, no schema change.
Installing the previous version over this one leaves those rows unused (the previous Studio listed every project of the table, so on a downgrade an image project would show up in the OpenJutsu list and fail to open;
delete such rows by hand if wanted: `spec.kind == "image"` is inside the encrypted spec, so use the Imajutsu list before downgrading and delete them there). Adjusted, scaled and composited pictures stay in the Library as ordinary items.
