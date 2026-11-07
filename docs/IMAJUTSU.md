# Imajutsu: image generation and editing (0.9.x work in progress)

Imajutsu is the image counterpart of OpenJutsu. It lives inside Semechka (route `/imajutsu`, nav id `imajutsu`), runs on the Sarness Engine's NATIVE image runtime
(diffusers pipelines driven by the engine's node-graph workflows; no ComfyUI, no duplicate weights) and reuses the OpenJutsu Studio infrastructure:
the project API (`studio_projects`), prepared/export assets, the job inspector (VIEW PROCESS), and the reference gallery.

Read first: `docs/OPENJUTSU_STUDIO.md` (layers, rules, inspect contract), `docs/OPENJUTSU_STUDIO_BACKEND.md`, `docs/MEDIA_COMPOSER.md`.

## What the benchmarks established (real runs on the DGX Spark, 2026-11-06; images and metadata in `docs/imajutsu-bench/`)
* Installed image models: `flux2-klein-4b` (Apache-2.0, 16 GB, txt2img + edit with up to 4 references + inpaint, 4 steps, ~20 s per 720x1280 image),
  `qwen-image-2512` (Apache-2.0, 58 GB, txt2img/img2img/inpaint, 50 steps with true CFG: far too slow to be a default), `qwen-image-edit-2511` (Apache-2.0, edit with up to 3 references),
  `z-image-turbo` (Apache-2.0, 8 steps, ~76 s per 720x1280 image; BREAKS on phone-style wording: "phone snapshot" / "9:16" make it draw hands holding phones or poster graphics, plain prompts are good),
  `qwen-image-2.1` (non-commercial, txt2img only), `sdxl-turbo`. FLUX.2 klein 9B and FLUX.2 dev are gated on Hugging Face (login + licence) and no token is configured: not installable here.
* iPhone-look generation: FLUX.2 klein 4B was clearly the most convincing and consistent (natural light, believable geometry, complex scenes, night, people), so it is the default for
  **Generate Image** and **iPhone Photo**. The routing table (`backend/semechka/imajutsu_routing.json`, written by the lead from the benchmark) is DATA: models, evidence, reasons. Code never hard-codes a model name.
* Cost model on the GB10 (unified memory): time is roughly linear in pixels x steps x parameters; 720x1280 is the practical native size for 9:16; larger outputs are produced by a separate scaling step, labelled as scaling.

## Modes (labels exactly as shown in the UI)
| Mode | What the engine runs | Needs |
|---|---|---|
| Generate Image | txt2img | prompt |
| iPhone Photo | txt2img with the phone preset (prompt builder + optional finish adjustments); 9:16 default | prompt (scene chips optional) |
| Edit Image | instruction edit (`edit` model mode) with the source picture (+ optional references) | source picture, instruction |
| Replace Subject | edit with the source + 1..N reference pictures (any subject: person, motorcycle, car, animal, product ...); optional mask/box selection => inpaint path | source, subject selection (words and/or mask/box), references |
| Inpaint | mask + prompt on the source (`inpaint` mode) | source, mask, prompt |
| Outpaint | the canvas is extended (Semechka pads the source and builds the mask of the new area) then `inpaint` | source, target size/aspect, prompt |
| Reference Image | txt2img/edit conditioned on up to the model's reference limit; roles are expressed in the prompt (see below) | references |
| Restyle | img2img (or edit) with a style instruction | source, style |
| Upscale | NOT a model: Lanczos scaling + light unsharp, labelled "scaled, no new detail"; optional "refine" pass = img2img at low strength on models that have it | source, target size |
| Advanced Workflow | all engine parameters (steps, guidance, scheduler, seed, negative prompt, batch) + VIEW PROCESS | anything |

A mode is operational only when the routing table has a model for it whose engine capabilities (`capabilities.modes`, `capabilities.references`) really include the needed engine mode; otherwise it is shown unavailable with the reason.
Nothing is composited and called AI: the only non-model pixel operations are (a) the optional "keep everything outside the selection exactly as it was" blend after an edit/inpaint (clearly named; off by default for Edit, on for Replace Subject when a selection exists), (b) photographic adjustments, (c) Upscale scaling, (d) outpaint padding.

## Reference roles are honest
Image models take pictures as conditioning without a "role" input. A role (appearance, identity, composition, lighting, style) is turned into a sentence in the prompt for that picture ("Use the person in image 2 for identity only"); the UI says so, shows the generated sentence, and counts the pictures against the model's limit
(`flux2-klein-4b` 4, `qwen-image-edit-2511` 3). More pictures than the limit are NEVER silently dropped: the UI keeps them as "not used", the backend sends only the active ones, the engine refuses more than the cap with a 422.

## Engine contract (Sarness, worktree ~/Documents/Sarness-imajutsu, branch feat/imajutsu)
1. `image_workflow()` (`workflow/templates.py`) + the sampler (`workflow/nodes/diffusion.py`): `extra_references` accepts as many pictures as the model's `capabilities.references` minus the source; the sampler gets sockets `reference_2..reference_N`. More than the cap => `JobError 422` "model takes at most N pictures"; no `[:2]`, no `images[:limit]` truncation anywhere. The recorded job `annotate()` lists the references actually used.
2. Inpaint with references for models that support `image_reference` (FLUX.2 klein), explicit refusal otherwise.
3. **Trace for native workflow jobs** (reuse `backends/trace.py`; same `GET /v1/jobs/{id}/inspect?after=` document as OpenJutsu, section 2.2 of OPENJUTSU_STUDIO.md): `backend: "sarness-workflow"`, `nodes` = the workflow's nodes (ids and titles from the graph), per-node state from the executor, `sampling` from the sampler's step callback (`step/max`), `stage` in {queued, loading_model, encoding_prompt, sampling, decoding, saving_output, done, failed}, `memory` as for ComfyUI jobs, `output.state` ready only after the image file is saved and probed, `conditioning.references` (used/not used with reason), `error` with the failing node. `graph` returns the workflow as `{nodes:{id:{class_type,title,inputs}}}` (links `[node_id, output_index]`) so the graph view can draw it; `history` answers 404 `no_graph` ... EXCEPT `graph` is available (`graph_kind: "sarness-workflow"`), the Semechka button reads "VIEW WORKFLOW" for it and "VIEW COMFYUI" is hidden. `GET /v1/jobs/{id}/preview` serves the newest latent preview the sampler produced (the engine already passes `preview` to `ctx.progress`) as JPEG.
4. Estimates: `GET /v1/models/{id}/estimate?width=&height=&steps=` -> `{seconds: {estimate, basis: "measured"|"none"}, memory: {...}}` from the engine's own recorded job timings per model (see `docs/imajutsu-bench/timings.json`); `basis: "none"` when nothing was measured.
5. Tests with fake pipelines like `tests/test_image_families.py`; run the full engine suite (`--noconftest`, venv `~/Documents/Sarness/engine/.venv`).

## Semechka backend contract (`/api/imajutsu`, same auth/ownership as `/api/studio`)
```
GET  /api/imajutsu/capabilities   -> {modes:[{id,label,engine_mode,status,reason,recommended:{model_id,why,evidence},models:[{model_id,alias,status,reason,limits:{references,min_side,max_side,multiple,native_pixels},needs}]}],
                                      presets:[{id,label,description,aspect,scene_chips:[...],model_id}], aspects:[{id,label,w,h}], limits:{max_pictures,...}}
POST /api/imajutsu/projects {mode?, source_asset?} -> project            (kind "image"; stored in studio_projects, spec.kind = "image")
GET/PUT/DELETE /api/imajutsu/projects[/{id}]                             (autosaved spec, optimistic concurrency)
POST /api/imajutsu/projects/{id}/plan {spec?} -> {native:{width,height,why}, output:{width,height,scaling}, model:{id,why}, prompt:{final, sentences:[{ref,sentence}]}, references:{used:[...],not_used:[{asset,reason}]}, memory/time:{...basis}, warnings, errors}
POST /api/imajutsu/projects/{id}/generate {spec?, count?:1-4} -> {job, accounting}    (prepares inputs: outpaint padding, mask PNG, reference preparation; uploads to the engine; submits ONE engine image job per variation through media_svc; saves params.imajutsu)
POST /api/imajutsu/assets/{aid}/adjust  {recipe} -> {task}              (non-destructive: writes a NEW asset origin "adjusted" from the recipe; the original is untouched)
GET  /api/imajutsu/assets/{aid}/adjust/preview?recipe=... -> image/jpeg  (small server-rendered preview with the exact same pipeline as the final)
POST /api/imajutsu/assets/{aid}/upscale {width,height} -> {task}        (Lanczos+unsharp, labelled)
GET  /api/imajutsu/jobs -> list (reopen with all references, mask, adjustments, results)
```
Inspector: the existing `/api/studio/jobs/{job}/inspect|inspect/stream|graph|history|preview` routes must work for image jobs too (ownership by MediaJob.user_id), mapping `graph_kind`.
Adjustment recipe v1 (all optional, neutral = absent): `exposure` (-2..2 EV), `contrast`, `saturation`, `temperature` (white balance K shift), `tint`, `highlights`, `shadows`, `sharpness`, `grain` (0..1, seeded so the preview equals the export), `vignette`, `rotate` (0/90/180/270 + straighten -10..10), `crop` {x,y,w,h in source pixels}, `flip`, `output` {width,height}. Implemented with ffmpeg filters or numpy/Pillow already installed (decide; add no dependency); the preview and the export must use the SAME code path. Adjustments are photographic (no inference) and never mixed into the generation parameters.

iPhone Photo preset (backend, `imajutsu_presets.py`, data-driven): a prompt BUILDER (not a suffix): scene chips (outdoor day, indoor, night, flash, selfie, mirror, casual) each add one natural sentence about light and framing, never the words "9:16" or "phone snapshot" for models flagged `avoid_phone_words` (Z-Image), default aspect 9:16 at the model's native 720x1280, 1 image, seed random; optional "phone finish" adjustment recipe (subtle: gentle S-curve, mild local contrast, fine grain, light sharpening) that the user can switch off and that is applied through the same adjust pipeline. No fake EXIF.

## Web contract (`/imajutsu`)
Layout like OpenJutsu Studio (stage left, panels right/stacked on mobile). Top: mode bar (the ten modes; unavailable ones disabled with reason). Panels: **Prompt** (text, iPhone scene chips, negative prompt under Advanced), **Source** (drop/paste/library; compare slider before/after on results), **Selection** (mask editor: brush, eraser, box, polygon-free; brush size, feather, invert, clear, zoom/pan, undo; "describe what to select" text is part of the prompt, not a model call; mask saved as a PNG asset), **References** (gallery: add many, reorder, remove, roles, "keep"/preserve notes, active vs not used against the model limit, generated sentence shown), **Size** (9:16 default for iPhone Photo, 16:9, 4:5, 3:4, 1:1, Custom; native vs output vs upscale shown as in OpenJutsu's readout; quality = steps preset), **Model** (recommended + why; manual choice under Advanced; other models listed with reason), **Adjust** (sliders with live preview; Photographic, separate from AI; Reset; Apply creates the adjusted asset), **Generate** (count 1-4), **Results** (gallery with seed/params, open in viewer, use as source, download, adjust), **Live job** (compact JobInspector strip + VIEW PROCESS [+ VIEW WORKFLOW]; intermediate previews from `/preview`), **Jobs** drawer. Everything saved in the project; reload restores it.
