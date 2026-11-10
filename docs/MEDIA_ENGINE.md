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
| `z-image-turbo` | image | txt2img -> `soul.t2i` | 6 s |
| `qwen-image-2512` | image | txt2img -> `image.qwen-2512` | 27 s |
| `flux2-klein-4b` | image | txt2img -> `image.t2i-open` (text only) | 33 s |
| `krea2-turbo` (new) | image | txt2img -> `soul.cinematic` | 30 s |
| `qwen-image-edit-2511` | image | edit -> `image.edit-open` (ONE picture) | 35 s (Fast) / 221 s (Quality) |
| `qwen-image-2.1` | image | edit -> `soul.cast` (exactly TWO pictures) | 27 s |
| `flux1-fill-dev` (new) | image | inpaint -> `image.inpaint-brush` (picture + mask) | 30 s |
| `ltx-2.3` | video | t2v -> `video.t2v-i2v-open`; i2v -> `cinema.camera-rig`; flf -> `cinema.first-last-frame` | 105 / 45 / 48 s |
| `wan2.2-ti2v-5b` ("Clip") | video | t2v -> `video.wan22-t2v` (text only) | 57 s |
| `wan2.2-i2v-a14b` | video | i2v -> `video.wan22-i2v` (needs a picture) | 57 s |
| `minimax-h3` | video | t2v -> `video.h3-t2v`; i2v -> `video.h3-i2v`; r2v -> `video.h3-r2v` (two pictures) | 51 / 57 / 36 s |

Profiles (video in, video out; same `inputs_schema` contract as before, **no** Studio `modes`):

| Profile id | operations -> feature | Notes |
|---|---|---|
| `opensfield-video-edit` | `replace_object` -> `genjutsu.replace-object` (SCAIL-2; video, reference picture, prompt, target); `motion_transfer` -> `genjutsu.motion-control` (Wan Animate 2; driving video, character picture, character prompt, motion prompt); `restyle` -> `genjutsu.styles` (LTX; video, picture, prompt); `edit_video` -> `video.edit` (Wan 2.2 Bernini-R; video, picture, prompt) | measured 439 / 364 / 177 / 664 s. Size and length follow the source; no mask, box or size control. Replace and motion produce two videos, only the last is delivered |
| `opensfield-video-upscale` | `upscale` -> `video.upscale` (SeedVR2) | 192 s |
| `opensfield-smooth-motion` | `interpolate` -> `video.interpolate` (FILM, 2x/4x) | 6 s |
| `reverse-video` | `analyse` -> `video.reverse` (REA, Reverse Engineer Video) | 3 s; CPU only; see section 7 |

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
| Imajutsu `edit` | works, **one picture** | `qwen-image-edit-2511` (no extra references: `references` = 1) |
| Imajutsu `inpaint`, `outpaint` (padded canvas + mask) | **works (new routing entry)** | `flux1-fill-dev`; the engine writes the mask into the picture's alpha channel (white = repaint) |
| Imajutsu `replace_subject`, `reference` | **unavailable** | needs source + >= 1 reference picture; the only two-picture workflow is `qwen-image-2.1` (blend), not the same operation. The routing file lists no model for them, so the page says so |
| Imajutsu `restyle`, `upscale` + refine | **unavailable** | image-to-image: no verified workflow |
| Imajutsu `upscale` (Lanczos / SeedVR2 / Real-ESRGAN) | **works** | Lanczos stays in Semechka; AI methods use the narrow `/api/internal/upscale*` routes the engine serves |
| Text-to-video | **works** | `minimax-h3` (t2v), `ltx-2.3`, `wan2.2-ti2v-5b` |
| Image-to-video | **works** | `minimax-h3` (i2v), `wan2.2-i2v-a14b`, `ltx-2.3` (i2v, and first/last frame at the engine level) |
| Reference-to-video (two pictures) | **works at the engine level** | `minimax-h3` r2v (`extra_references` = 2 ids). Semechka's composer sends it only if the web part exposes two references for video |
| Video editing profile (Composer "profile" tray) | **works, different models** | `opensfield-video-edit` operations above |
| OpenJutsu Studio (8 modes, SAM mask/box, H3 context loop, size presets, live trace) | **unavailable** | see section 5: not an OpenSfield workflow. The new profiles publish no Studio `modes`, so the Studio lists them as "does not say which operations it can do" instead of pretending |
| Reverse Engineer Video (REA) | **works** | profile `reverse-video`; analysis served at `GET /v1/reverse/{job}` |
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
| `hunyuanvideo-1.5-t2v`, `hunyuanvideo-1.5-i2v` | Diffusers pipeline only on the old engine; no OpenSfield workflow |
| `wan2.2-t2v-a14b` | OpenSfield has the 14B image-to-video and the 5B text-to-video workflows, not 14B text-to-video |
| `openjutsu-minimax-h3`, `openjutsu-reference-video` | SAM 3.1 masks + H3 reference-to-video with the Context-Loop nodes live in the old engine's own ComfyUI install (`~/sarness-comfyui`), not in OpenSfield's verified set. The Studio's modes (subject/character/object/vehicle replacement, motion transfer, reference-to-video, video restyling, advanced editing) with box/point selection, size presets and live trace are therefore unavailable; section 2 lists what runs instead (different models, no mask/box). Decision needed if the H3 swap must come back: port that workflow into the OpenSfield ComfyUI and verify it |
| Image-to-image (Imajutsu restyle, refine pass) | no verified workflow |
| Multi-reference editing with FLUX.2 klein, edit with extra references | klein is text-to-image only here; Qwen-Image-Edit takes one picture |
| Negative prompt, scheduler, guidance, LoRAs, batch, arbitrary steps | the verified workflows are fixed graphs; only Fast/Quality exists where a workflow has the switch |
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
* **Controls that do not exist**: negative prompt (`negative_prompt: false` on every model), scheduler, guidance, frame rate. **Steps** exist only on models with `speed_switch: true`
  (Qwen-Image 2512, Qwen-Image-Edit, Wan 14B, H3): label it "Fast / Quality" (<= 8 = Fast), not a step count. Imajutsu's draft/high step numbers therefore map to Fast/Quality and the
  plan text "N steps" is not what the workflow does.
* **Sizes**: free sizes (Z-Image, Qwen-Image, FLUX.2 klein) vs fixed shapes (Krea: `limits.sizes`, `size_policy: snap_to_shape`; the result states its real size) vs `size_policy: source_shape`
  (edit and inpaint: the workflow picks its own size from the picture; width/height are ignored).
* **Imajutsu**: modes `restyle`, `replace_subject`, `reference` and the refine pass of `upscale` have no model (the page already says "no model" when the routing finds none);
  `edit` takes one picture; `inpaint`/`outpaint` run on FLUX.1 Fill (non-commercial licence, `caution` on the model).
* **Studio**: the new profiles carry no Studio `modes`, so `/studio` lists them as not offering operations; the Composer's profile tray (video + one reference picture + prompt) is the
  way to run them. OpenJutsu's H3 swap is unavailable (section 5).
* **Reverse Engineer Video** has no page yet: `GET /api/media/jobs/{id}/analysis` returns the analysis and plan; the library item is the contact sheet.
* **Live previews** do not exist while a job runs (ComfyUI has no preview method enabled); progress is real: queue position, then "Loading the model", "Generating, step N of M".

## 7. Reverse Engineer Video (REA)

Profile `reverse-video`, operation `analyse`, input `video` (+ `vision` off/auto; **off by default**: the optional scene descriptions use a vision model that is already loaded on a
Sarness engine, which is being retired; re-pointing that hook at the llama.cpp router is not done). The upload is validated like OpenSfield's own (ffprobe, 500 MB, 10 minutes, 4K).
The job's delivered file is the contact sheet (JPEG); `GET /v1/reverse/{job}` returns the full analysis (shots, camera, light, colour, transitions) and the editable plan; frames at
`/v1/reverse/{job}/files/{name}`. It measures what is visible; it does not recover how a video was made.

---------------------------------------------------------------------------------------------------------------------------------------------------------------------

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

## 9. Experimental tools: audio, picture tools, 3D (consolidation phase 2, 2026-11-10)

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
The Images and Videos pages are unchanged.
