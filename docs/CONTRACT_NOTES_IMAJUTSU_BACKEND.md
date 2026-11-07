# Contract notes from the Imajutsu backend worker

`docs/IMAJUTSU.md` was not edited. These are the places where the contract was silent, ambiguous or where the backend deliberately differs. The lead decides which go back into the contract.

1. **The image spec (version 1)** is defined in `IMAJUTSU_BACKEND.md`. Names chosen: `preset {id, scenes[], finish}` (scene chips are `scenes`), `aspect {id, w, h}`, `size {native, output}`,
   `selection {box (fractions 0-1), feather (px), invert, keep_outside}`, `references[] {asset, role, preserve, active, label}`, `advanced {steps, guidance, scheduler, denoise}`, `upscale {refine, strength}`,
   `outpaint {pad | target, anchor, overlap}`. Unknown `advanced` keys are refused (422); unknown top-level keys are ignored like the Studio's.
2. **Aspect `source` (and the default for modes that start from a picture)** is an addition: `aspects[]` lists `source` ("Same shape as the picture") next to 9:16, 16:9, 4:5, 3:4, 1:1 and `custom`. Without it, editing a
   3000x4000 photo would force 9:16. With another shape than the picture's, the plan says "cut in the centre (N% kept)".
3. **Generate returns the engine's NATIVE size.** `plan.output {width, height, scaling}` is the size the person wants; reaching it is a separate, labelled step on each result: `POST /api/imajutsu/assets/{aid}/upscale`
   (Lanczos + light unsharp; it also scales DOWN, `scaling: downscale`). `generate` in mode Upscale without refine answers 422 `use_upscale` (it is a scaling task, not a job); with `upscale.refine = true` it is an ordinary
   img2img job on the scaled picture (z-image-turbo today).
4. **Added routes:** `GET /api/imajutsu/jobs/{job}`, `POST /api/imajutsu/jobs/{job}/reopen` (-> `{project, dropped[]}`, idempotent), `POST /api/imajutsu/assets/{aid}/keep-outside` (the optional "keep everything outside the
   selection" blend, ffmpeg `maskedmerge`, labelled, needs a result of Imajutsu: it reads the source/mask/selection from the job). `generate` additionally accepts `count`.
5. **Response of `generate`:** `{job (first), jobs[], job_detail, job_details[], batch {id, count, seeds}, accounting, plan, forced_unsaved}`. `accounting = {sent_to_engine[], not_used[], source, mask, pictures_sent, negative_sent,
   seeds, prompt_sentences, native, output, ...}`. Variations are admitted all-or-none (`media_svc.admit(n=count)`): with the default quota of 2 concurrent jobs, `count: 3` is a 429 naming how many are free; nothing is started.
6. **Recipe units.** `temperature` and `tint` are slider units (-100..100; positive = warmer / more magenta, brightness-neutral), not kelvin. `rotate` and `straighten` are clockwise. `crop` is in pixels of the picture AFTER turning
   and flipping. `output` must have the shape of the result (1.5%), it is never stretched. Optional `grain_seed`. An all-neutral recipe is a 422 `nothing_to_do` for `adjust` (the preview accepts it). The preview takes the recipe as a JSON
   string in `?recipe=`; response headers `ETag` and `X-Recipe-Key`.
7. **Capabilities shape.** Per mode: `models[]` lists every routed model (status `ready | unavailable`, with `reason`, `why`, `evidence`, `limits`) plus the installed models that are not routed (status `available`, `routed: false`;
   chosen by hand only, never recommended). `limits.references` 0 means "takes the source only". Upscale carries `scaling {method, label, ai:false}` and `refine {available, models[], recommended}`. Added: `roles[]`, `qualities[]`,
   `routing {source, version, updated, problem}`, `adjust {ranges, finish}`. `presets[0].finish` carries the phone-finish recipe.
8. **Routing file override:** `<data_dir>/imajutsu_routing.json` wins over the packaged file when valid (an operator can reorder without a release). Top-level `avoid_words` is the list of words the plain-wording models must never be given.
9. **Inspector.** The Studio routes accept image jobs and are gated by the job's own kind (an Imajutsu-only person is not blocked by the Video gate). Inspect documents carry `graph_kind` (`comfyui | sarness-workflow | null`) and
   `workflow {available}` next to `comfy {available}`. `graph` answers the engine's document with `graph_kind` set. `history` of an image job is 404 `no_graph`. Fallback documents of an image job list the source as picture 1.
10. **Assets.** New origins `adjusted`, `scaled`, `composited` (accepted by `GET /api/media/assets?origin=`); asset JSON gains `imajutsu {kind, recipe | method/label/ai/from/to | source/mask/feather, parent}`.
    Result assets also carry the job's `params.imajutsu`.
11. **No-save installs** keep no `params.imajutsu`; their jobs are not listed by `/api/imajutsu/jobs` but run and can be inspected live.
12. **Engine facts the backend relies on (please confirm with the engine worker):**
    * `capabilities.references` in `/v1/models` (or `/v1/registry` models[].capabilities) = the number of pictures an edit/inpaint takes, **the source included**; absent = the model takes the source only.
    * `reference_image` = picture 1, `extra_references` = the others in order; the engine refuses more than the cap with a 422 (the double does). The currently audited `image_workflow()` still slices `extra_references[:2]`.
    * Semechka pre-scales the source (cover-fit) and the mask to exactly the requested `width x height`, because the engine resizes `reference_image` with mode "fill" (centre crop).
    * Masks: PNG, **white = repaint**, black = keep (`mask.load channel luminance`). If the engine's polarity is the opposite the fix is one line in `prepare_inputs`.
    * `GET /v1/models/{id}/estimate?width=&height=&steps=` -> `{seconds:{estimate,basis}, memory:{peak_mb,basis,note}}`, `basis` in `measured | extrapolated | none`.
    * Inspect documents of native image jobs as in `IMAJUTSU.md` section 3, plus `conditioning.mask`; `graph` -> `{graph_kind, nodes:{id:{class_type,title,inputs}}}` with links `[node_id, output_index]`.
13. **Web harness:** `web/e2e_media/setup.ts` (web worker) must enable the image models of the double, e.g. `image:flux2-klein-4b`, `image:qwen-image-edit-2511`, `image:qwen-image-2512`, `image:z-image-turbo`,
    `image:qwen-image-2.1`, `image:sdxl-turbo`, in its `enable_models`; the double (`fake_engine.py`) publishes them from the start.
14. **Dependencies:** none added. Pillow is not a Semechka dependency (it is only used by tests that were already there), so all pixel work is ffmpeg + numpy.
15. **Updates of 2026-11-07 (corrected benchmark, each model at its OWN default steps/guidance).**
    * **Standard steps/guidance are the engine's own** (`Model.meta.media.defaults`: flux2-klein 4 / 1.0, z-image-turbo 9 / 0, qwen-image-2512 50 / 4); the routing file's `quality` only holds `draft` and `high`
      (`standard` is not allowed there). An engine that states no default: `plan.steps` is `null` and nothing is sent (the engine fills it). The plan's estimate query passes `mode` and `pictures` too.
    * **iPhone Photo has three quality tiers** in the routing data (`tier` on the model facts, `tiers` and `default_tier` on the mode and on `presets[0]`): `fast` = flux2-klein-4b (default, ~4 s), `best` = qwen-image-2512
      ("Best realism", ~144 s), `polished` = z-image-turbo (~12 s). The spec carries `preset.tier` (null = default `fast`); the tier picks the model; when its model is missing the recommended one runs and `plan.warnings` says so.
      `plan.model.tier` reports it. Generate Image order: klein, z-image-turbo, qwen-image-2512.
    * **Wording rules are routing data:** `wording: natural | plain` (+ optional `avoid_words`) per model. natural (klein): natural sentences, "handheld phone photo" is fine. plain (qwen-image-2512, z-image-turbo): "A photograph of ..." + plain
      light/framing sentences; the words snapshot, phone, 9:16, candid (and screenshot, selfie for z-image) are never written, and the selfie/mirror sentences only appear when that chip is chosen.
    * The double's estimates: klein 4 steps 720x1280 4 s, z-image 9 steps 12 s, qwen-image-2512 50 steps 144 s (measured at those points, extrapolated elsewhere; omitted `steps` = 30 like the real endpoint).
