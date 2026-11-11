# Media engine: Semechka on the OpenSfield backend

Status: written 2026-11-10 on branch `consol/media`. Part 1 (inventory and mapping) is the contract the other workstreams read; the "Evidence" section at the end
is filled in from real runs and says what was and was not proven.

The Sarness Engine is being retired. Every Semechka image and video generation now runs on the **OpenSfield backend** (ComfyUI + the workflows that were rendered and
looked at on this Spark), reached through one loopback API, the **media engine**, `http://127.0.0.1:8780/v1`. Nothing was rebuilt: the engine is a thin API
(`app/semechka_engine.py`, `app/semechka_catalog.py`, `app/engine_main.py`) over OpenSfield's existing job queue, ComfyUI runner (real websocket progress), memory gate
(`gate.py`), model state (`/osf/unload`) and the Reverse Engineer Video analysis. Code lives on the Spark, repo `~/opensfield/app`, branch `semechka-engine` (worktree
`~/opensfield/wt-engine`). Semechka reaches it through a new adapter `spark-media` (`backend/semechka/adapters/spark_media.py`) that implements the same `media_*`
interface `media_svc`, the routers and the existing UI already use, so none of them changed.

**Rule that governs everything below:** only features listed in `config/verified.json` (rendered here, output reviewed) are offered. A feature that is installed but not
verified is reported as `unverified` and refused. A thing with no ComfyUI workflow in the verified set is **dropped/unavailable** and listed with the reason in section 5;
nothing is substituted silently.

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 1. What Semechka uses today (inventory)

| Surface | Code | What it does with the engine |
|---|---|---|
| Media adapter | `adapters/sarness_media.py` (`SarnessMediaMixin`) | `media_submit(kind, params, client_key)`, `media_submit_profile`, `media_upload`, `media_status`, `media_cancel`, `media_fetch`, `media_fetch_to`, `media_discard`, `media_preview`, `media_estimate`, `media_model_estimate`, `media_inspect/graph/history/segment_open` |
| Catalog | `adapters/sarness.py` `discover()` -> `media_meta()`/`profile_meta()` | engine models become `Model` rows (`real_id` = `image:<id>` / `video:<id>`, `meta.media` = modes, references, defaults, limits, profile) |
| Lifecycle of a job | `media_svc.py` | validate (limits), submit, poll (`refresh`), copy result into the encrypted asset store, discard from the engine, cancel, retention |
| Image/video API | `routers/media.py` | `/api/media/options|jobs|profile-jobs|assets`; iPhone Photo preset; chat media cards |
| Imajutsu (images) | `routers/imajutsu.py`, `imajutsu_svc.py`, `imajutsu_plan.py`, `imajutsu_routing.json`, `imajutsu_presets.py` | 10 modes: generate, iphone_photo, edit, replace_subject, inpaint, outpaint, reference, restyle, upscale, advanced. Picks an engine model per mode from the routing file, plans size/steps/prompt, uploads source/mask/refs, submits `kind=image` |
| OpenJutsu Studio (video editing) | `routers/studio.py`, `studio_svc.py`, `studio_plan.py`, `studio_inspect.py` | composite **profile** `openjutsu-minimax-h3` (8 modes), trim/crop prepared locally, `media_submit_profile`, live trace via `media_inspect` |
| Upscale | `opensfield_upscale.py` | already calls OpenSfield (SeedVR2, Real-ESRGAN) through its narrow `/api/internal/upscale*` API; the engine process serves the same routes (same token file), so only the URL changes |
| Work Mode / agent tools | `work_engine.py` (`generate_image`, `generate_video`) | go through `media_svc.submit/submit_profile` -> unchanged |
| Duration | `video_duration.py` | seconds <-> frames from the model's published limits (`frames`, `frame_step`, `fps`, `durations`) |
| Local-only (never sent to an engine) | `media_lib.py`, `imajutsu_pix.py`, `imajutsu_adjust.py` | trim, crop, Lanczos scale, phone finish, adjustments, thumbnails, filmstrips |

The engines Semechka knew (from the three off-box backups): image `flux2-klein-4b`, `qwen-image-2512`, `qwen-image-edit-2511`, `qwen-image-2.1` (off everywhere), `z-image-turbo`,
`sdxl-turbo` ("Sketch"); video `ltx-2.3`, `minimax-h3`, `wan2.2-ti2v-5b` ("Clip", the enabled default), `wan2.2-i2v-a14b`, `wan2.2-t2v-a14b`, `hunyuanvideo-1.5-t2v/i2v`;
profiles `openjutsu-minimax-h3` (all three orgs), `openjutsu-reference-video` (staging).

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 2. Engine models (what Semechka sees) and the feature behind each

`GET /v1/capabilities` publishes these (live; a mode that is not ready or not verified disappears from `modes` and shows in `mode_states`). Model ids are the **old ids where
the model is the same** so existing Semechka rows (ids, grants, Imajutsu routing) carry over by `real_id`.

| Engine model id (= old id) | kind | modes -> OpenSfield feature | Measured (one reviewed run) |
|---|---|---|---|
| `z-image-turbo` | image | txt2img -> `soul.t2i`; **img2img** -> `image.img2img-zimage` (restyle, upscale refine; phase 2) | 6 s |
| `qwen-image-2512` | image | txt2img -> `image.qwen-2512` | 27 s |
| `flux2-klein-4b` | image | txt2img -> `image.t2i-open`; **edit** -> `image.edit-klein-1..4` (1-4 pictures, phase 2); **inpaint** -> `image.inpaint-klein-0..3` (selection + 0-3 reference pictures, phase 2) | 33 s; edit/inpaint: see "Phase 2" |
| `krea2-turbo` (new) | image | txt2img -> `soul.cinematic` | 30 s |
| `qwen-image-edit-2511` | image | edit -> `image.edit-open` (ONE picture), `image.edit-qwen-2` (two), `image.edit-qwen-3` (three, the most its encoder reads) | 35 s (Fast) / 221 s (Quality) |
| `qwen-image-2.1` | image | edit -> `soul.cast` (exactly TWO pictures) | 27 s |
| `flux1-fill-dev` (new) | image | inpaint -> `image.inpaint-brush` (picture + mask) | 30 s |
| `ltx-2.3` | video | t2v -> `video.t2v-i2v-open`; i2v -> `cinema.camera-rig`; flf -> `cinema.first-last-frame` | 105 / 45 / 48 s |
| `wan2.2-ti2v-5b` ("Clip") | video | t2v -> `video.wan22-t2v` (text only) | 57 s |
| `wan2.2-i2v-a14b` | video | i2v -> `video.wan22-i2v` (needs a picture) | 57 s |
| `minimax-h3` | video | t2v -> `video.h3-t2v`; i2v -> `video.h3-i2v`; r2v -> `video.h3-r2v` (two pictures) | 51 / 57 / 36 s |
| `wan2.2-t2v-a14b` (restored, phase 2) | video | t2v -> `video.wan22-t2v14` (Fast 4-step LightX2V / Quality 20 steps; converted weights) | 44 s (2 s clip) |
| `hunyuanvideo-1.5-t2v`, `hunyuanvideo-1.5-i2v` (restored, phase 2) | video | t2v -> `video.hunyuan15-t2v`; i2v -> `video.hunyuan15-i2v` (needs a picture); 848x480 at 24 fps, 1-5 s, no sound | 106 s (1 s) / 797 s (5 s) |

Profiles (video in, video out; same `inputs_schema` contract as before, **no** Studio `modes`):

| Profile id | operations -> feature | Notes |
|---|---|---|
| `opensfield-video-edit` | `replace_object` -> `genjutsu.replace-object` (SCAIL-2; video, reference picture, prompt, target); `motion_transfer` -> `genjutsu.motion-control` (Wan Animate 2; driving video, character picture, character prompt, motion prompt); `restyle` -> `genjutsu.styles` (LTX; video, picture, prompt); `edit_video` -> `video.edit` (Wan 2.2 Bernini-R; video, picture, prompt) | measured 439 / 364 / 177 / 664 s. Size and length follow the source; no mask, box or size control. Replace and motion produce two videos, only the last is delivered |
| `opensfield-video-upscale` | `upscale` -> `video.upscale` (SeedVR2) | 192 s |
| `opensfield-smooth-motion` | `interpolate` -> `video.interpolate` (FILM, 2x/4x) | 6 s |
| `reverse-video` | `analyse` -> `video.reverse` (REA, Reverse Engineer Video) | 1-2 s CPU analysis; with scene descriptions (default) plus the vision model's time, see section 7 |
| `openjutsu-minimax-h3` (restored, phase 2) | `swap` (one person, 1 picture), `advanced_swap` (1-4 pictures, one per person), `object_edit` (change / remove / add / background), `check_mask` (SAM 3.1 preview) -> `openjutsu.swap|advanced|object|mask` | the validated OpenJutsu H3 masked swap (SAM 3.1 + MiniMax H3 + Context-Loop chain), ported into this engine; publishes the Studio contract (`modes`, `limits`, `reference_policy`, `subject_selection`, `operation_status`). Details and evidence: section 11 |
| `openjutsu-reference-video` (restored) | `reference_video` (1-9 pictures + prompt, no source video) -> `openjutsu.reference-video` | H3 reference-to-video with up to 9 pictures |

Engine-only extras that exist in OpenSfield but Semechka does not use (kept available in the engine, no Semechka surface): background removal (`image.bg-remove`), image layers
(`image.decompose`), style reference (`soul.styles`), ACE-Step music, Kokoro TTS, camera/effects LTX presets, `iphone-photo` finish (Semechka has its own finish and it is off by default).

### Limits and parameters per model (published in `limits`/`defaults`)

| Model | Size | Length (exact clips on offer) | Other controls |
|---|---|---|---|
| `z-image-turbo` | any width/height 512-2048, multiple of 16 | n/a | seed. No steps/guidance/negative |
| `qwen-image-2512` | 512-1664, multiple of 16 | n/a | seed; `steps` <= 8 = **Fast** (Lightning, default), > 8 = **Quality** (the workflow's own full-step branch; its step count is the workflow's, reported in `applied.sampler_steps_last`) |
| `flux2-klein-4b` | any width/height 512-1536, multiple of 16: the engine sets the verified graph's two size values (`PrimitiveInt` nodes `75:68`/`75:69`), nothing else changes; the render is checked by eye in the Evidence section | n/a | seed |
| `krea2-turbo` | fixed shapes (8 aspects, about 1 MP): a request snaps to the nearest shape within 4 % (otherwise 422 listing the shapes) and the job reports `applied.size_snapped_from` | n/a | seed |
| `qwen-image-edit-2511`, `qwen-image-2.1`, `flux1-fill-dev` | the output follows the source picture (`size_policy: source_shape`): width/height are ignored, the workflow picks its own size (about 1 MP, close to the source's shape: measured 720x1280 -> 752x1392 for Qwen-Image-Edit) and the asset records the real size; `applied.size_ignored` says so | n/a | edit: `steps` Fast/Quality; inpaint: mask white = repaint |
| `ltx-2.3` | 24 sizes (aspect x quality, 384-1344 px) | whole seconds 1-10 -> **24 s + 1 frames at 24 fps** (1.042 s ... 10.042 s) | seed; audio generated |
| `wan2.2-ti2v-5b` | 16 sizes (448-1376 px) | 1-8 s -> **24 s + 1 frames at 24 fps** (1.042 ... 8.042 s) | seed |
| `wan2.2-i2v-a14b` | 24 sizes (416-1440 px) | 1-8 s -> **16 s + 1 frames at 16 fps** (1.062 ... 8.062 s) | seed; Fast/Quality |
| `minimax-h3` | 24 sizes (416-1216 px; 512 px = Draft, 864x480 = Standard 16:9) | t2v 2-10 s, i2v 1-10 s: `max(5, round(24 s))` frames rounded **up** to the 17k + 5 grid at 24 fps -> **1.625, 2.333, 3.042, 4.458, 5.167, 6.583, 7.292, 8.0, 9.417, 10.125 s** | seed; Fast/Quality; audio generated |

**Duration behaviour (what the UI must state).** Every workflow takes whole seconds and converts them itself. The engine therefore publishes `limits.durations` = the real clip
lengths, `limits.frames`/`frame_step` and a fixed `fps`; Semechka's `video_duration` snaps the person's request to the nearest of them and the job returns the real numbers:
`applied.frames/fps/seconds` (what the workflow was told) and, after completion, `generations[0].duration/frames/fps` **probed from the file with ffprobe**. Examples measured
by ffprobe on this Spark before this work: H3 2 s -> 56 frames = 2.333 s; H3 6 s -> 158 frames = 6.583 s; Wan 5B 2 s -> 49 frames = 2.0417 s. A frame count that is not on a
model's grid is refused with the list of lengths that exist (never rounded in secret). `fps` cannot be changed on any model.

### Memory and speed

`GET /v1/models/{id}/estimate` and `POST /v1/admission` reuse `gate.py`'s own arithmetic (weights of the model's stack on disk + working-space headroom, minus a 6 GB reserve; idle
resident weights of the same stack are reusable). A job that does not fit **waits** (`phase=waiting_memory`, reason in `progress_text`) for up to 15 minutes and then fails with the
numbers. Typical needs from the live catalog: H3 51 GB of weights (about 66 GB with headroom), LTX 42 GB, Qwen-Image-Edit 30 GB, Z-Image 20 GB. Time estimates are `basis: measured`
only where a reviewed run exists (the table above) and say so.

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 3. Capability map: Semechka feature -> engine

| Semechka capability | Status | Engine route |
|---|---|---|
| Text-to-image (chat, Images page, Work Mode) | **works** | `z-image-turbo`, `qwen-image-2512`, `flux2-klein-4b`, `krea2-turbo` |
| iPhone Photo preset (tiers Fast / Best realism / Polished) | **works** | Fast = klein (720x1280 exactly), Best = Qwen-Image 2512, Polished = Z-Image (720x1280 exactly). Prompt builder unchanged |
| Imajutsu `generate`, `advanced` (text) | **works** | as above |
| Imajutsu `edit` | **works, 1-4 pictures (phase 2)** | `flux2-klein-4b` (1-4 pictures, exact source size) or `qwen-image-edit-2511` (1-3 pictures) |
| Imajutsu `inpaint`, `outpaint` (padded canvas + mask) | **works (new routing entry)** | `flux1-fill-dev`; the engine writes the mask into the picture's alpha channel (white = repaint) |
| Imajutsu `replace_subject`, `reference` | **works (phase 2)** | source + 1-3 reference pictures: `image.edit-klein-N` / `image.edit-qwen-N`; with a selection, `image.inpaint-klein-N` repaints only the selection (everything outside is the source's own pixels) |
| Imajutsu `restyle` | **works (phase 2)** | instruction edit (`flux2-klein-4b` best, optional style-reference picture); Z-Image image-to-image exists but restyles poorly (measured), so it is last in the routing |
| Imajutsu `upscale` + refine | **works (phase 2)** | lanczos scaling, then `image.img2img-zimage` at low strength (<= 2048 px a side); it can change small facial detail (measured) and is labelled as a model pass |
| Imajutsu `upscale` (Lanczos / SeedVR2 / Real-ESRGAN) | **works** | Lanczos stays in Semechka; AI methods use the narrow `/api/internal/upscale*` routes the engine serves |
| Text-to-video | **works** | `minimax-h3` (t2v), `ltx-2.3`, `wan2.2-ti2v-5b` |
| Image-to-video | **works** | `minimax-h3` (i2v), `wan2.2-i2v-a14b`, `ltx-2.3` (i2v, and first/last frame at the engine level) |
| Reference-to-video (two pictures) | **works at the engine level** | `minimax-h3` r2v (`extra_references` = 2 ids). Semechka's composer sends it only if the web part exposes two references for video |
| Video editing profile (Composer "profile" tray) | **works, different models** | `opensfield-video-edit` operations above |
| OpenJutsu Studio (modes, SAM mask/points, H3 context loop, size presets) | **restored (phase 2)** | profiles `openjutsu-minimax-h3` and `openjutsu-reference-video` (section 11). The live node trace (VIEW PROCESS / VIEW COMFYUI) is still not published: the Studio shows its fallback |
| Reverse Engineer Video (REA) | **works**, scene descriptions + reconstructed prompt **restored** (llama.cpp router vision model) | profile `reverse-video`; analysis served at `GET /v1/reverse/{job}`; Semechka Experimental -> Video -> REA |
| Live job inspector / workflow graph / history / segments (Studio trace) | **not offered** | those traces belong to the old engine's node graph; the routes answer 404 and Semechka shows "no trace" |
| Cancel | works | `POST /v1/jobs/{id}/cancel` -> ComfyUI `/interrupt` + queue delete (verified on the GPU in the evidence section) |
| Release memory | works | `POST /v1/release` -> ComfyUI `/osf/unload` |
| Speech from text (Kokoro), music (ACE-Step 1.5) | **works, Experimental > Audio** (2026-11-10, section 9) | tools `kokoro-tts`, `ace-step-music` (`POST /v1/tools/{id}/jobs`) |
| Remove background, split into layers, style from a picture | **works, Experimental > Tools** (section 9) | tools `bg-remove`, `image-layers`, `style-reference` |
| Picture to 3D relief (MoGe-2, GLB + depth map) | **Experimental > 3D** (section 9) | tool `moge-relief` (new feature `scene3d.moge-mesh`) |
| Video upscale, smooth motion | **Experimental > Video tools** (page added 2026-11-10) | profiles `opensfield-video-upscale`, `opensfield-smooth-motion` |

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 4. The engine API (loopback, Bearer token)

Base `http://127.0.0.1:8780/v1`. `Authorization: Bearer <token>` where the token is the first line of `~/opensfield/config/semechka-media.token` (0600; read on every request;
absent or empty file = 503, never open). Independent of the browser UI's access key. Every non-loopback client gets 403 even if it reached the port.

| Route | Purpose |
|---|---|
| `GET /health` (no auth) | `{ok, version, api_version, comfy_reachable}` |
| `GET /system` | memory (total/available/reserve), ComfyUI queue and resident models, job counts |
| `GET /capabilities` `GET /models` `GET /models/{id}` `GET /profiles` | models/profiles with `limits`, `defaults`, `modes`, `mode_states`, `timing`, `unavailable[]` |
| `GET /models/{id}/estimate` `GET /profiles/{id}/estimate` `POST /admission` | time (measured only) and memory (gate arithmetic, `fits_now`) |
| `POST /uploads` (multipart `file`) `DELETE /uploads/{id}` | pictures/videos for jobs; same type check and size cap as the UI |
| `POST /jobs {kind, simple, client_key, title}` | `simple` keys: model, prompt, width, height, seed, steps, frames, fps, seconds, reference_image, extra_references, mask_image, start_image, end_image (+ refused-with-a-reason: negative_prompt, scheduler, guidance, loras, batch_size, denoise). `client_key` makes it idempotent |
| `POST /profiles/{id}/jobs {inputs, client_key, title}` | file inputs are upload ids |
| `GET /jobs/{id}` `GET /jobs?status=` | `status` queued/running/completed/failed/cancelled, `progress` (monotonic, ComfyUI node + sampler step; null while queued), `progress_text`, `queue_position`, `applied`, `generations[]` |
| `POST /jobs/{id}/cancel` | queued: dropped; running: ComfyUI interrupted, queue freed |
| `GET /jobs/{id}/preview` | live frame if ComfyUI streams previews (it needs `--preview-method`; not enabled on this Spark, so while running this is 404), else a thumbnail of the result |
| `GET /files/{id}` (Range) `GET /files/{id}/thumb` `DELETE /generations/{id}` | result bytes, JPEG thumbnail, discard after copy |
| `POST /release {force?}` | free ComfyUI models; returns MemAvailable before/after |
| `GET /reverse/{job}` `GET /reverse/{job}/files/{name}` | REA analysis + plan, frames, contact sheet |
| `/api/internal/upscale*` | the narrow upscale API (own token file `upscale.token`), unchanged |
| `GET /tools` `POST /tools/{id}/jobs {inputs, client_key, title}` `GET /tools-runtime` | Experimental tools (section 9): the list with `inputs_schema` and readiness; a job (file inputs are upload ids; idempotent by `client_key`); the speech child process state |

Durability: the job list and prompt ids are saved (`OPENSFIELD_JOBS_FILE`); after an engine restart queued jobs run again and a running job whose ComfyUI prompt is still in
ComfyUI is **re-attached** (no resubmission, no second memory-gate wait). Retention: uploads 2 days, output folders 3 days, job records 30 days (env overridable), results are
normally discarded by Semechka right after it has copied them.

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 5. Dropped / unavailable (no verified ComfyUI equivalent) and why

| Old capability | Why it is not offered |
|---|---|
| `sdxl-turbo` ("Sketch") | exists only as a Diffusers pipeline on the old engine; OpenSfield has no verified SDXL workflow |
| `hunyuanvideo-1.5-t2v`, `hunyuanvideo-1.5-i2v` | **restored in phase 2** (section 12) |
| `wan2.2-t2v-a14b` | **restored in phase 2** (section 12) |
| `openjutsu-minimax-h3`, `openjutsu-reference-video` | **restored in phase 2** (section 11): the workflow was ported into this engine's ComfyUI (additive node packs read through `extra_model_paths`) and re-validated by real runs |
| ~~Image-to-image (Imajutsu restyle, refine pass)~~ | **restored in phase 2** (`image.img2img-zimage`, klein edit); see section 9 |
| ~~Multi-reference editing with FLUX.2 klein, edit with extra references~~ | **restored in phase 2** (klein 1-4 pictures, Qwen 1-3); see section 9 |
| Negative prompt, steps, guidance | **partly restored in phase 2** (section 12): they exist, and are proven by A/B runs, only for Wan 2.2 5B, Wan 2.2 14B (Quality) and LTX-2.3 (negative prompt and guidance above 1). Scheduler, LoRAs and batches stay refused everywhere |
| Arbitrary video fps / frame counts | each workflow fixes its fps and frame grid |
| Studio live trace / graph / history / segments | belong to the old engine's graph |
| Live sampler previews | ComfyUI here runs without `--preview-method`; the engine forwards frames if that ever changes and never fakes one |

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 5b. Recommended engine unit (NOT installed; the orchestrator installs it)

`~/.config/systemd/user/semechka-media-engine.service` on the Spark (after branch `semechka-engine` is merged into `spark-local` in `~/opensfield/app`; until then point `WorkingDirectory`
at `~/opensfield/wt-engine`). The token file `~/opensfield/config/semechka-media.token` already exists (0600) from the dev runs; `mkdir -p ~/opensfield/state/semechka-engine/outputs` first.

```
[Unit]
Description=Semechka media engine (OpenSfield backend, loopback API on 127.0.0.1:8780)
After=opensfield-comfyui.service
Wants=opensfield-comfyui.service

[Service]
Type=simple
WorkingDirectory=%h/opensfield/app
EnvironmentFile=%h/opensfield/config/opensfield.env
Environment=OPENSFIELD_OUTPUTS_DIR=%h/opensfield/state/semechka-engine/outputs
Environment=OPENSFIELD_JOBS_FILE=%h/opensfield/state/semechka-engine/jobs.json
Environment=OPENSFIELD_SEMECHKA_TOKEN_FILE=%h/opensfield/config/semechka-media.token
Environment=OPENSFIELD_RECOVER_JOBS=1
Environment=OPENSFIELD_CLIENT_PREFIX=semechka-
Environment=OPENSFIELD_REAP=0
Environment=OPENSFIELD_IDLE_UNLOAD=900
Environment=PYTHONUNBUFFERED=1
ExecStart=%h/opensfield/venv-app/bin/uvicorn app.engine_main:app --host 127.0.0.1 --port 8780 --no-server-header --timeout-keep-alive 30
Restart=on-failure
RestartSec=5
StandardOutput=append:%h/opensfield/logs/semechka-engine.log
StandardError=append:%h/opensfield/logs/semechka-engine.log
NoNewPrivileges=yes
PrivateTmp=yes
UMask=0027

[Install]
WantedBy=default.target
```

Why these settings: its own outputs/jobs file (a separate history from the browser UI); `RECOVER_JOBS` resumes unfinished jobs; a **different ComfyUI client-id prefix** so a restart of the
`opensfield` UI service (whose orphan reaper interrupts every `opensfield-*` prompt) can never kill an engine job; the engine's own reaper off because it re-attaches instead;
idle unload frees ComfyUI's models 15 minutes after the last job. It reads the live `config/verified.json`. **Transition risk:** while the `opensfield` UI service still runs, it
and the engine each have their own single worker and memory gate; ComfyUI serialises execution, but two heavy jobs could both pass the gate at once. Retire the UI service (or stop using it)
before relying on H3 alongside LLMs.

## 6. Semechka side

* `adapters/spark_media.py`: adapter `spark-media` (`SparkMediaAdapter(SparkMediaMixin, Adapter)`), registered in `adapters.REGISTRY`. `discover()` turns `/v1/capabilities`
  into `DiscoveredModel` rows with the **same `meta.media` shape** the Sarness adapter produced, so `media_svc.option_defaults/validate_params`, the Composer, Imajutsu and
  `video_duration` are untouched. Unavailable models are reported with `meta.media.unavailable_reason` and `available=False`.
* Small additive changes in shared files: the adapter registry; `media_svc.unavailable_reason()` wording is backend-neutral; `imajutsu_routing.json` gains `flux1-fill-dev`
  as the first model for inpaint and outpaint.
* `scripts/spark/migrate_media_to_engine.py <data-dir> [--apply] [--rollback FILE]`: dry-run by default. Takes a DB backup, creates a **new** backend row `spark-media`
  (token read from the token file inside the script, never printed), moves each existing media `Model` row whose `real_id` the engine knows to the new backend (same
  `Model.id`, so grants, settings and Imajutsu keep working), marks rows with no equivalent `available=False` with the reason, adds engine-only models disabled with no grants,
  and copies the old OpenJutsu row's grants to `opensfield-video-edit` as disabled-by-default (`--grant-profiles` opts in). `media_jobs`/`media_assets` are never modified,
  old assets still open (the store is local and encrypted). Idempotent; the rollback file restores every touched row. The LLM backend row is not touched.

## 6b. Handoff to the web part (what the pages must say or hide)

* **Length**: show `duration.options` (the exact clip lengths, e.g. H3 5.167 s / 6.583 s) and the asset's own `duration` after the run; never the length that was asked for.
  `GET /api/media/options` already returns it per model (`duration.options`, `declared: true`, `fps` fixed).
* **Controls**: negative prompt (`negative_prompt: true` only on Wan 5B/14B and LTX-2.3), steps and guidance where the model publishes `limits.steps` / `limits.guidance` (Advanced video shows a field only then); scheduler, LoRAs, batch and frame rate do not exist. **Steps** exist only on models with `speed_switch: true`
  (Qwen-Image 2512, Qwen-Image-Edit, Wan 14B, H3): label it "Fast / Quality" (<= 8 = Fast), not a step count. Imajutsu's draft/high step numbers therefore map to Fast/Quality and the
  plan text "N steps" is not what the workflow does.
* **Sizes**: free sizes (Z-Image, Qwen-Image, FLUX.2 klein) vs fixed shapes (Krea: `limits.sizes`, `size_policy: snap_to_shape`; the result states its real size) vs `size_policy: source_shape`
  (edit and inpaint: the workflow picks its own size from the picture; width/height are ignored).
* **Imajutsu**: since phase 2 every mode has a model (section 9). The catalogue publishes `mode_pictures` (pictures per mode, source included) and Semechka's planner caps the pictures by it;
  `inpaint`/`outpaint` run on FLUX.1 Fill (non-commercial licence, `caution` on the model) or, with reference pictures, on FLUX.2 klein (Apache-2.0).
* **Studio**: the OpenJutsu profiles carry the Studio contract again (section 11), so `/studio` (Experimental > Video editor) lists their modes with the statuses the engine computes; the other
  profiles (`opensfield-video-edit` ...) have no `modes` and run from the Composer's profile tray (video + one reference picture + prompt).
* **Reverse Engineer Video** has no page yet: `GET /api/media/jobs/{id}/analysis` returns the analysis and plan; the library item is the contact sheet.
* **Live previews** do not exist while a job runs (ComfyUI has no preview method enabled); progress is real: queue position, then "Loading the model", "Generating, step N of M".

## 7. Reverse Engineer Video (REA)

Profile `reverse-video`, operation `analyse`, input `video` (+ `vision` `auto` | `off`, **`auto` by default**). The upload is validated like OpenSfield's own (ffprobe,
500 MB, 10 minutes, 4K). The job's delivered file is the contact sheet (JPEG); `GET /v1/reverse/{job}` returns the full analysis (shots, camera, light, colour,
transitions) and the editable plan; keyframes and the contact sheet at `/v1/reverse/{job}/files/{name}`.

**Scene descriptions (restored, now on the llama.cpp router).** With `vision=auto` the engine sends every shot's keyframes (1 for a shot under 3 s, first/mid/last
from 3 s, 5 from 12 s) in one request to a vision model on `spark-llm` (`qwen3.6-35b-a3b` preferred, `qwen3.8-27b` second) and gets a JSON answer per shot:
description, subjects, action, setting, mood, shot size, angle, light, what changes, and a prompt for that shot; then one text-only request writes a
**reconstructed prompt** for the whole clip from those descriptions. Fields are additive (`shot.vision.*`, `analysis.vision.{model, described, frames_sent,
clip: {summary, style, prompt}, loaded_by_us, unloaded, memory_gb, reason, note}`), so older readers keep working. The reconstructed prompt is a NEW prompt for a
similar clip; the page and the analysis say that it is not the prompt or the model that made the video.

**Etiquette** (engine `docs/REVERSE_VIDEO.md`, "Vision etiquette"): dedicated router key from `OPENSFIELD_VISION_KEY_FILE` (default `~/spark-llm/media.key`, never
logged or returned); shared GPU lock (`~/spark-reset/gpu.lock`, the `gpu-llm` lock) taken by the engine itself, bounded wait (120 s) while a heavy media run holds it;
an already loaded vision model is reused and left loaded; otherwise admission first (MemAvailable minus a 6 GB reserve must cover the measured 25.3 GB + 4 GB headroom;
never Flash-Next; never past the router's 3-model limit, so nobody's model is pushed out); unloaded afterwards only if REA loaded it and no stream is running on it;
cancel works mid-description and still unloads. When it cannot run, the analysis is complete without descriptions and `vision.reason` says why in plain words (no key,
key refused, router down, not enough memory with the numbers, lock busy).

**Semechka.** Experimental -> Video -> REA (`/experimental/rea`): upload or pick a video, "Describe the scenes with a vision model" (on by default; sends
`vision=auto|off`), Analyse. The result shows the vision note ("Vision used: qwen3.6-35b-a3b, N keyframes from M shots ..." or the reason), the reconstructed prompt
(read-only text box + Copy), one card per shot (keyframe thumbnails, description, subjects, shot size/angle/light/mood/setting, the measured camera/light/colours, the
shot's prompt + Copy), and every measurement under "All measurements". Keyframes come through `GET /api/media/jobs/{id}/analysis/files/{name}` (owner only, completed
`reverse-video` jobs only, JPEG names from a fixed pattern, proxied from the engine).

**Measured** (evidence `docs/evidence/rea/`): H3 5 s clip, 1 shot, 3 keyframes, qwen3.6 loaded by REA in 23 s, whole job 33 s, MemAvailable 111.7 -> 87.8 -> 112.2 GB;
4-shot clip, 14-16 keyframes, about 5 s per shot, 41 s job; cancel during "Describing shot 2" ends the job in 1.5 s and unloads; wrong key / router down / no key /
lock busy each return the full measurements with a plain reason. Descriptions were checked against the frames: subjects, settings, light and pan direction were
right; mismatches recorded there (a measured push-in called "static" before the camera fact was enforced, a background bicycle omitted, a garbled sign transcribed).
Under a busy GPU-lock queue (other teams' heavy runs) the step waits; with the default 120 s it then skips with the reason.

## 8. Evidence

Real runs 2026-11-10, dev Semechka (port 8792, fresh data dir, backend `spark-media` through an SSH tunnel) -> engine (`semechka-media-dev`, 127.0.0.1:8780 from worktree
`~/opensfield/wt-engine`) -> the live ComfyUI (8190), under `~/spark-reset/gpu-media`. Every output was fetched through Semechka's own asset API and looked at (stills) or
contact-sheeted (video); progress was polled every second through `GET /api/media/jobs/{id}` and was monotonic in every job (series saved with the proof).

| Job (through Semechka) | Wall time | Result checked |
|---|---|---|
| Z-Image 1024x1024 (cold) / 720x1280 (warm) | 25 s / 7 s | exact sizes, sharp, correct |
| FLUX.2 klein 720x1280 (graph size override) | 29 s | exact 720x1280, clean |
| Krea 2 Turbo 1368x768; request 1000x1000 snapped | 29 s / 19 s | 1368x768; second reported 1024x1024 |
| Qwen-Image 2512 Fast 1024 | 30 s | legible "OPEN" sign |
| Qwen-Image-Edit (chat flow, source picture) | 29 s | door yellow, rest unchanged; output 752x1392 from a 720x1280 source |
| FLUX.1 Fill inpaint via Imajutsu (mask white = repaint) | 39 s | cat inside the mask only, 720x1280 |
| SeedVR2 upscale 720x1280 -> 1440x2560 (narrow API on the engine) | 10 s | sharp, faithful |
| MiniMax H3 text-to-video 5 s | 145 s (cold load) | API told 124 frames at 24 fps = 5.1667 s; ffprobe 864x480, 124 frames, 5.166688 s, audio track; frames coherent, colours correct |
| Wan 2.2 14B image-to-video 5 s from the klein picture | 111 s | told 81 frames at 16 fps = 5.0625 s; ffprobe 81 frames, 5.0625 s; leaves and traffic move, colours correct |
| LTX-2.3 text-to-video 5 s, **engine restarted mid-job** | 75 s | told 121 frames at 24 fps = 5.0417 s; ffprobe 121 frames, 5.041667 s, audio; the engine log says "resumed 1 unfinished job(s)", the job kept its ComfyUI prompt id and finished; progress stayed monotonic across the restart |
| Reverse Engineer Video on an uploaded 5 s two-shot clip (made from the H3 and Wan outputs) | 1 s | 2 shots, one measured hard cut, contact sheet stored, analysis served at `/api/media/jobs/{id}/analysis` |
| Cancel an H3 job at 21 % (step 3 of 8) | ComfyUI queue empty 8.2 s after the request, GPU utilisation 0 % | engine job `cancelled`, no output |
| `POST /v1/release` after the runs | MemAvailable 71.0 -> 115.6 GiB (idle baseline 115.6) | 44.6 GB freed |

Not proven on the GPU: Wan 5B text-to-video, LTX/H3 image-to-video and H3 reference-to-video, first/last frame, Qwen-Image 2.1, the `opensfield-video-edit` / video upscale / smooth-motion
profiles (their tests use fakes; the workflows themselves are verified by OpenSfield's own `config/verified.json`), Imajutsu outpaint, the browser UI (nothing was clicked), a Roasted/Salted
deployment. Live sampler previews do not exist (ComfyUI runs without a preview method).

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 9. Phase 2: Imajutsu picture modes restored (2026-11-10)

New engine features (graphs written by `scripts/make_imajutsu_graphs.py`, registry `registry/imajutsu_features.py`, engine branch `consol2/imajutsu-engine`):

| Feature | What it is | Pictures | Notes |
|---|---|---|---|
| `image.edit-klein-1..4` | FLUX.2 klein 4B distilled (fp8, Apache-2.0), 4 steps, cfg 1: the official `image_flux2_klein_image_edit_4b_distilled` template with one reference-conditioning pair per picture | 1-4 (first is edited) | output = the first picture's size (`megapixels` set from the upload, 16-px steps, cap 2 MP), 10-20 s |
| `image.inpaint-klein-0..3` | the same with a mask: `SetLatentNoiseMask` on the source latent, source + 0-3 references condition it, the result is composited over the source with the mask | source + 0-3 references | outside the selection the result equals the source (measured mean abs diff 0.001-0.003, max 2); 4-16 s |
| `image.edit-qwen-2`, `-3` | Qwen-Image-Edit 2511 with `TextEncodeQwenImageEditPlus` image2 / image3 | 2, 3 | 41-49 s Fast; output size is Qwen's own (720x1280 -> 752x1392) |
| `image.img2img-zimage` | Z-Image Turbo with the picture VAE-encoded and a strength (0.05-1) | 1 | picture sides must be multiples of 16, <= 2048; 17 s at 2048x1152 |

Catalogue: `modes` of `flux2-klein-4b` = txt2img, edit, inpaint (`references` 4); `qwen-image-edit-2511` = edit (`references` 3); `z-image-turbo` = txt2img, img2img. `mode_states` keys: `edit` (1 picture), `edit+N` (N extra references);
a variant that was not rendered and reviewed is `unverified` and refused (409). `denoise` is accepted only by image-to-image (`422` otherwise); a picture whose sides are not multiples of 16 is refused for image-to-image.

### Evidence (real runs through the dev engine on the live ComfyUI, under the exclusive media lock; every output looked at; stills in `docs/evidence/imajutsu/`)

| Run | Time | What was seen |
|---|---|---|
| klein edit, 1 picture, "red jacket to dark green" | 11 s | jacket green, face, street and size (720x1280) unchanged |
| Qwen edit, same | 29-35 s | same result, size 752x1392 |
| klein edit, 2 pictures: replace the man with the woman of picture 2 | 13 s | curly-haired woman in a yellow raincoat, street and light of picture 1 kept |
| Qwen edit, 2 pictures, same | 41 s | same idea, background kept |
| klein edit, 3 pictures (kitchen + teapot + dog), seeds 5 and 9 | 10 s | kitchen kept, the blue teapot and the golden retriever of the references added |
| Qwen edit, 3 pictures, same | 49 s | same; Qwen re-drew the table |
| klein edit, 4 pictures (+ woman) | 20 s | kitchen with teapot, dog and the woman by the window |
| reference mode (no source): woman + teapot, klein / Qwen | 13 / 41 s | one café photo with the woman in her yellow coat holding the teapot |
| klein restyle "watercolour, keep the same objects" | 11 s | convincing watercolour, same composition and objects |
| Qwen (Fast) restyle, same prompt | 40 s | almost unchanged (Fast does not restyle); Quality is needed |
| klein edit with a painting as the style picture | 11 s | portrait re-painted in the painting's style, same man and street |
| Z-Image img2img "oil painting" 0.55 / "pencil sketch" 0.7 / "watercolour" 0.75 | 17 s | NOT a restyle: 0.55 barely changed the look and altered the fruit, 0.7 changed the man's face, 0.75 invented new objects. Kept for low-strength refine only |
| refine 0.3 on a 2048x1152 kitchen / 0.25 on a 1152x2048 portrait | 17 s | content and composition kept, fine texture added; on the face it changed the eye colour and added freckles (so the page's "it can change faces" label is real) |
| klein masked edit, 0 / 1 / 2 / 3 references | 4 / 13 / 16 / 14 s | words only: an old fisherman; one reference: the woman; two: woman with the teapot; three: woman, teapot and dog; outside the selection identical to the source in all four |
| cancel a Qwen Quality edit at step 2 of 40 | ComfyUI queue empty 4 s after the request | job `cancelled`, no output; `POST /v1/release` freed 27.6 GB |

Progress was monotonic in every job. Z-Image img2img is therefore recommended only as the refine pass; the routing file lists FLUX.2 klein first for edit, replace subject, reference and restyle.

### Browser verification of Imajutsu (real Chrome, desktop 1280 px and 390 px; dev Semechka -> dev engine -> live ComfyUI; stills in `docs/evidence/imajutsu/ui/`)

Signed in as a staging admin on a copy of the staging data, driven with Playwright (`web/` Chrome channel); every picture was fetched from the Library and looked at.

| Flow (Experimental -> Image, or the Images page) | Result |
|---|---|
| Images page, text only (the page stays: prompt, model, optional upload, Generate) | picture in 37 s |
| Images page, uploaded reference + words (FLUX.2 klein, edit mode) | the kitchen re-made at night with lamps on, same layout |
| Edit Image (recommended model FLUX.2 klein) | jacket green, 14 s, exact 720x1280 |
| Replace Subject: source + 1 reference + box selection | engine mode inpaint (FLUX.2 klein), 17 s; the "original kept outside the selection" copy was made automatically. Quality note: with a box covering the whole figure the face came out a blend (the man's beard under the woman's curls); a tighter selection and the API run gave the clean replacement shown in section 9 |
| Reference Image: two pictures, no source | woman holding the teapot in a café, 20 s |
| Restyle (klein edit) | watercolour kitchen, 11 s |
| Upscale + refine (2048x1152 from 1280x720, Z-Image image-to-image at 0.3) | 2048x1152 in 38 s; the plan said "scaled up" and labelled the refine as a model pass |
| Upscale with SeedVR2 to 1440x2560 | sharper face detail than Lanczos (crop in `ui_seedvr2_crop.jpg`) |
| iPhone Photo, Fast (klein) and Best realism (Qwen-Image 2512) | both exactly 720x1280 (9:16); 34 s and 166 s |
| Inpaint (FLUX.1 Fill, box) / Outpaint (padded canvas, FLUX.1 Fill) | bread on a board added inside the box; teapot photo extended to 16:9 |
| 390 px: Images, Experimental, Picture studio, Replace Subject | no horizontal overflow; layout readable |

Bug found and fixed on the way: in Upscale a custom width with no size chosen yet produced a SQUARE output (the shape lock started from 1:1); it now keeps the picture's own shape (`SizePanel.svelte`).

### Deploy / roll out (operator)

1. Engine: merge `consol2/imajutsu-engine` (Spark `~/opensfield/wt-imajutsu`) into `spark-local`; merge the 11 entries of `docs/evidence/imajutsu/verified_entries_to_merge.json` into the live `~/opensfield/config/verified.json`; restart `semechka-media-engine` when its queue is 0 (the live engine must restart to load the new registry; ComfyUI is not touched, no node or weight was added).
2. Semechka: deploy this branch; then per instance `scripts/spark/migrate_media_to_engine.py <data-dir> --apply` (dry run first). It refreshes the rows already on the engine backend, keeps enabled flags and grants, and with `--enable-models image:z-image-turbo --like image:flux2-klein-4b` enables Z-Image for the same people as FLUX.2 klein. Rollback: `--rollback <data-dir>/pre-update-media-engine-<ts>/rollback.json`. Rehearsed on copies of the pre-0.12.2 Salted and Roasted databases on the Spark (users/conversations/media hashes unchanged, rollback restores every hash).
3. Licences: FLUX.2 klein 4B and Qwen-Image-Edit 2511 and Z-Image Turbo are Apache-2.0; FLUX.1 Fill (inpaint/outpaint) stays non-commercial and keeps its `caution`.

---

## 10. Experimental tools: audio, picture tools, 3D (consolidation phase 2, 2026-11-10)

What the retired OpenSfield studio could do that Semechka did not offer yet, recovered where the weights and dependencies were already on the Spark and a real run worked.
The full before/after list of every studio tool is in [FEATURE_PARITY_OPENSFIELD.md](FEATURE_PARITY_OPENSFIELD.md).

**Engine** (`app/semechka_extras.py`, branch `consol2/imajutsu-parity` of `~/opensfield`): `tools` in `/v1/capabilities`, next to `models` and `profiles`, so an older Semechka sees
nothing new. Same rule as the models: a tool is `ready` only when its registry feature is runnable here AND is in `config/verified.json`; otherwise it is listed with `state`/`reason`
and refused (409). Jobs are ordinary engine jobs (`kind` = `audio` | `image` | `3d`; each generation has `type` and `mime`: WAV/MP3, PNG, GLB).

| Tool | Feature | Output | Licence | Runs on |
|---|---|---|---|---|
| `kokoro-tts` Text to speech (Kokoro) | `audio.tts-kokoro` | WAV 24 kHz mono | Apache-2.0 | CPU, a **child process** of the engine (below) |
| `ace-step-music` Music (ACE-Step 1.5) | `audio.music` | MP3, exactly the asked length (5-120 s) | Apache-2.0 | GPU (ComfyUI) |
| `bg-remove` Remove background (BiRefNet) | `image.bg-remove` | cut-out PNG with alpha + the mask | MIT | GPU (ComfyUI) |
| `image-layers` Split into layers (Qwen-Image Layered) | `image.decompose` | one transparent PNG per layer (2-8) | Apache-2.0 | GPU, about 50 GB |
| `style-reference` Style from a picture (Krea 2) | `soul.styles` | PNG | Krea-2 community (`caution`) | GPU (ComfyUI) |
| `moge-relief` Picture to 3D relief (MoGe-2) | `scene3d.moge-mesh` (**new**) | textured GLB + colour depth PNG | MIT | GPU (ComfyUI core nodes) |

*Text to speech without a standing service.* The old `opensfield-tts` sidecar on :8770 is retired and stays retired. With `OPENSFIELD_TTS_URL` **unset** the engine starts
`ops/tts/osf_tts_worker.py` in `~/opensfield/venv-tts` on the first speech request (JSON lines over a pipe, no port), CPU only (`CUDA_VISIBLE_DEVICES` empty, so no CUDA context
on the shared memory), offline (`HF_HUB_OFFLINE=1`), and stops it after `OPENSFIELD_TTS_IDLE` seconds without a request (default 300) or when its resident memory passes
`OPENSFIELD_TTS_MAX_MB` (default 4096; measured 1.8 GB with one English pipeline, 2.4 GB with both). Only voices already in the Hugging Face cache are offered (on this Spark:
`af_heart`, `bf_emma`); nothing is downloaded. Setting `OPENSFIELD_TTS_URL` restores the old HTTP-sidecar behaviour.

*3D, honestly.* No image-to-3D object model is installed (Hunyuan3D is licence-restricted and opt-in only; TRELLIS, TripoSG, SF3D, Depth Anything 3 weights are absent although ComfyUI
has their nodes). MoGe-2 (0.66 GB, already on disk) estimates the geometry visible in **one** picture; `scene3d.moge-mesh` triangulates that point map into a textured mesh. It is a
relief seen from the camera, not a closed object. The old studio's `app.3d-figure-open` is a Qwen-Image-Edit picture preset (a picture of a figurine), not 3D.

**Semechka** (`media_extras.py`, `routers/media_extras.py`): ready tools are discovered as models `tool:<id>` with the single capability `media_tool`, so no chat, Images or Videos
chooser ever lists them. A catalog sync adds them **disabled with no grants**; an administrator enables each and grants it (a grant limited to modes applies when it includes
`image`). A person sees a tool when they can use Images or Videos AND the tool is enabled and granted to them (owners with `models.use_all` also reach disabled ones, marked
restricted, as on Images). `GET /api/media/extras?group=audio|tools|3d`, `POST /api/media/extras/jobs` (multipart: `model_id`, `inputs` JSON, `file:<input>` or `asset:<input>` = a
Library id, `save`), `GET /api/media/extras/jobs`. Jobs are `MediaJob(kind="tool")` and share the Images/Videos daily limit, concurrency cap, retention and no-save rules; each stored
file is filed by what it is (`audio`, `image`, `3d`), the Library shows sounds and 3D meshes (player / 3D viewer) and filters by them. `GET /api/me` reports
`ui.operations.extras = {enabled, groups: {audio, tools, 3d}}`; the Experimental hub lists only the groups that have a tool for this person.

**Web**: `/experimental/audio`, `/experimental/tools`, `/experimental/3d` (one page, `ExtrasPage.svelte`, whose form is built from the engine's `inputs_schema`) and
`/experimental/video-tools` (the existing upscale and smooth-motion profiles through `/api/media/profile-jobs`). Results: pictures on a checkerboard (transparency is visible),
an audio player, a dependency-free WebGL GLB viewer (`GlbViewer.svelte`: drag, pinch or scroll, double-click to reset, keyboard; Picture/Shape views), always with a download.
A mesh in a picture's camera frame (all of it in front of the origin, looking down -Z, as MoGe writes it) opens from that camera, so it first looks like the picture, and turns
around a point at the median depth; any other GLB is framed by its bounding box. Tool and Video tools titles are the engine's own names unless an administrator renamed the row
(`display_name`, also in `/api/media/options`). The Images and Videos pages are unchanged.

---

## 11. Phase 2: OpenJutsu restored (H3 masked swap, multi-reference, object edit, mask preview)

Engine branch `consol2/openjutsu-swap` (merged with the other phase-2 engine branches in `consol2/openjutsu-integrated`), docs `docs/OPENJUTSU.md` in the engine repo, node-pack manifest
`~/opensfield/node-packs-oj/MANIFEST.json`. Nothing here is a new service: the validated Sarness builder (D3voz/openjutsu @ 7fe118b, MIT) was ported into the media engine
(`app/openjutsu/`), the three ComfyUI node packs it needs are installed ADDITIVELY in a new folder and reach ComfyUI through one added block in `extra_model_paths.yaml`, and the old H3 /
SAM 3.1 weights are read in place. Statuses are computed by the engine (`ready` only with a real run in `verified.json`), never copied from a file.

What a person gets in **Experimental > Video editor** (the Studio, unchanged UI) and what was proven on this Spark (dev Semechka -> dev engine -> ComfyUI with the new nodes; every output was
looked at; AI-generated footage and AI-generated reference pictures only):

| Operation / mode | Run | Result |
|---|---|---|
| `swap` (character replacement), 1 reference | 3 s 854x480 AI clip, 480p | 271 s wall; the woman is replaced by the reference person, street/bus/motion kept; 56 frames 2.333 s (72 -> 56: H3's 17k+5 frame grid), 864x480, soundtrack kept (aac, mean -16.7 dB); progress monotonic |
| `swap`, multi-clip | 10.1 s AI clip, 480p | 523 s; 2 clips; 226 frames 9.417 s (grid rounding), identity holds across the clip boundary and a bus passing; audio kept |
| `advanced_swap`, 3 references | 3-person AI clip, 480p, 3 s | 199 s; all three people replaced by the three pictures in the order of `roles`; 56 frames, audio present. The workflow takes up to 4 pictures; 3 were run (no 4-person clip) |
| `object_edit` change | coat -> yellow raincoat (words only), bike colour red -> blue (words only), 480p | 96 s / 124 s; only the object changes, face, background and soundtrack kept. `remove`, `add`, `background` and `paint` have NO real run: they stay experimental |
| `check_mask` text / box / points | 3-person AI clip, 3 s | 24-30 s each; text tracks the largest person, a box around the small man and a clicked point on the old man each select only that subject (point = the part clicked) |
| `reference_video`, 9 pictures | 9 AI pictures + prompt naming 6, 480p, 2.3 s | 82 s; 864x480, 56 frames, audio. **Limit:** 9 pictures are accepted and conditioned, but the model draws what the prompt names: about five identities are visible, the others are ignored (the engine warns for each picture the prompt never names) |
| Through Semechka's Studio (vehicle replacement, 1 picture, 640x480) | button press in the browser | 84 s; real engine progress in the UI ("Generating, step n of 6", cancel button), result saved to the Library (640x480, 2.333 s, audio); `accept_experimental` false because the operation is verified |
| Cancel at 12 % of a 10 s swap | API | ComfyUI queue empty 3.3 s later, engine `cancelled`, GPU 0 %, no output; `POST /v1/release` freed 61 GB |
| Regression on the extended ComfyUI | H3 text-to-video 2 s, Z-Image 1024 | both completed with correct output (58 s / 20 s) |

Limits that stay (published in the profile `limits`, shown by the Studio): at most 30 s of source, short edge <= 768 (only 480p was run in this phase; the old engine validated 736p), box/points only for
videos that run as a single 6 s clip, one reference per person in `advanced_swap`, `object_edit` needs words or one picture. **Validated footage:** AI-generated single-person clips (and the
three-person clip for masks and `advanced_swap`); camera footage, crowds, occlusion and hat/face fixes are not claimed. **Licence:** MiniMax H3's licence excludes the EU, UK, South Korea and the US;
the caution is in the profile and model descriptions. Not restored: the live node trace (VIEW PROCESS / VIEW COMFYUI) is still not published by the engine, so the Studio shows its fallback document;
the Studio's `motion_transfer` and `video_restyling` modes stay unavailable there and point at the editing profile `opensfield-video-edit` (Wan Animate 2, LTX restyle, Bernini-R), which runs from the Conversation studio.


---------------------------------------------------------------------------------------------------------------------------------------------------------------------

## 12. Phase 2: video tools run for real, advanced controls, Wan 14B text-to-video and HunyuanVideo 1.5

Engine branches `consol2/openjutsu-vid` (real runs + controls) and `consol2/openjutsu-wanhy` (converted weights, graphs), merged with `consol2/openjutsu-swap` in `consol2/openjutsu-integrated`
(engine tests 1300 passed, 2 skipped; baseline 1236). Evidence: `docs/evidence/video-tools/` (README + `runs.json`), `docs/evidence/wan-hunyuan/` (README, run JSONs, sha256 of every converted file).

**Video tools, run on the GPU for the first time** (dev engine -> live ComfyUI, AI-generated inputs, contact sheets looked at, progress monotonic): `opensfield-video-edit` replace_object (SCAIL-2, 261 s: person truly
replaced, motion kept), motion_transfer (Wan Animate 2, 341 s; the engine now delivers the clean result, not the side-by-side comparison it used to), restyle (LTX, 766 s incl. a memory wait; **the reference picture must be the
first frame already drawn in the new look**, otherwise the output stays photographic), edit_video (Bernini-R, 297 s; fixed: it forced a portrait shape on landscape clips, now follows the clip), `opensfield-video-upscale`
(SeedVR2, 333 s, 1708x960, real detail), `opensfield-smooth-motion` 2x/4x (17 s / 36 s; the 4x result is 96 fps), LTX-2.3 i2v (71 s) and first/last frame (38 s), H3 i2v (100 s) and r2v with two pictures (58 s), Wan 5B t2v (64 s, 960x544,
no audio track: the model has none). Cancel at 30 %: ComfyUI idle and GPU 0 % within 6 s. Also fixed: a busy ComfyUI was reported "not reachable".

**Advanced controls, proven by same-seed A/B runs** (everything else stays refused with a plain sentence): Wan 5B steps (8 vs 20 steps: 30 s vs 64 s), guidance, seed, negative prompt (wired, output changes; not a removal guarantee);
Wan 14B Quality steps / guidance / negative prompt (Fast uses a fixed 4-step recipe); LTX-2.3 guidance above 1 (negative prompt wired but weak). Proven through Semechka's Advanced video (Wan 5B, steps 8, guidance 4, negative prompt "people, text"):
the engine reported "step n of 8", 41 s, 960x544, 49 frames. fps is not configurable on any model (it would only change playback speed); no model takes LoRAs from the user.

**Restored models** (converted from the Diffusers files into NEW ComfyUI files under `~/opensfield/models`, originals untouched, `CONVERTED.md` there): Wan 2.2 14B text-to-video (Fast 44 s for 2 s; 8 s clip 182 s; Quality 214 s for 2 s),
HunyuanVideo 1.5 text-to-video and image-to-video (1 s 106 s, 2 s 221 s, 5 s about 13 minutes, so the catalogue caps it at 5 s; neither model makes sound). Each was also run through Semechka (Wan 14B 2 s: 52 s, 832x480, 33 frames at 16 fps;
HunyuanVideo 1 s: 105 s, 848x480, 25 frames) and the result was looked at. Licence: the HunyuanVideo licence text was read (Tencent Hunyuan Community: the Territory excludes the European Union, the United Kingdom and South Korea, use outside it is unlicensed); the caution is in the model description.
Gap: Fast mode of Wan 14B uses the Wan 2.1 LightX2V T2V LoRA (good output on the 2.2 experts); the official Wan 2.2 T2V 4-step LoRAs are not on disk (optional download, size not measured).

## 13. Deploying phase 2 on the production Spark (operator order)

1. **Engine code**: in `~/opensfield/app` (branch `spark-local`) merge `consol2/openjutsu-integrated` (it already contains `consol2/openjutsu-swap`, `-vid`, `-wanhy`; engine tests 1300 passed). `git -C ~/opensfield/app merge consol2/openjutsu-integrated`.
2. **Weights** (new files, already on this Spark under `~/opensfield/models`; on another machine copy them or re-run the converters in `scripts/convert_*_to_comfy.py`): see `~/opensfield/models/CONVERTED.md`.
3. **Node packs** (already installed, additive): `~/opensfield/node-packs-oj/` (pinned commits and licences in `MANIFEST.json`). Add this block to `~/opensfield/config/extra_model_paths.yaml`:
   ```
   openjutsu_nodes:
     base_path: /home/spark/opensfield/node-packs-oj
     custom_nodes: .
   ```
   then restart ONLY ComfyUI, under `~/spark-reset/gpu-media`, when `GET /v1/system` of the live engine shows queue 0: `systemctl --user restart opensfield-comfyui`. Afterwards re-run one `video.h3-t2v` and one image job (this was done on the dev ComfyUI with the same yaml: H3 t2v 58 s and Z-Image 20 s, both correct).
4. **verified.json**: merge `docs/evidence/openjutsu/verified-entries.json` (the five `openjutsu.*` features) and `docs/evidence/wan-hunyuan/verified_entries.json` (`video.wan22-t2v14`, `video.hunyuan15-t2v`, `video.hunyuan15-i2v`) into `~/opensfield/config/verified.json` (hand merge; never overwrite the file).
5. **Engine unit**: add `Environment=OPENSFIELD_COMFY_OUTPUT_DIR=%h/opensfield/state/comfy-output` (lets the hourly sweep remove old OpenJutsu clip checkpoints), then `systemctl --user restart semechka-media-engine`. `GET /v1/capabilities` must list `openjutsu-minimax-h3`, `openjutsu-reference-video` and the three restored models.
6. **Semechka release** from branch `consol2/openjutsu` (adapter passes the Studio contract, Guidance field, migration RESTORE), then per instance: `migrate_media_to_engine.py <data> --apply` (dry run first; it RESTOREs the parked OpenJutsu / Wan 14B / HunyuanVideo rows, keeps enabled flags and grants; rollback file written), then admin > backends > sync once.
   `openjutsu-reference-video` and `opensfield-video-edit` are new rows on Salted/Roasted (disabled, no grants): enable and grant in admin. The restored Wan 14B / HunyuanVideo rows keep their previous enabled flag (off on Salted).

### Acceptance plugins (phase 2)

`scripts/spark/prod_accept2.py <staging|salted|roasted> --plugin FILE` signs in with throwaway accounts (never a real user), runs the plugin and restores the exact prior data fingerprint. Plugins in `scripts/spark/acceptance/`:
`p2_core.py` (chat with context, image, H3 5 s with audio, Wan 14B, HunyuanVideo), `p2_openjutsu.py` (Studio swap, mask check, object edit, 4-picture reference-to-video, cancel), `p2_imajutsu.py` (picture modes, Images page, Experimental tools including 3D, REA).
Staging results on 0.12.3: core 26/26, Imajutsu 57/57, OpenJutsu 48/48 after the object-edit threshold fix (47/48 before it; the output was correct). The `scene3d.moge-mesh` verified entry is in `docs/evidence/imajutsu/moge-mesh-verified.json` (merge by hand into the live `verified.json`). REA scene descriptions need the router vision key file (`OPENSFIELD_VISION_KEY_FILE`); without it REA reports technical analysis only.
