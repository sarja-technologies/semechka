# OpenJutsu Studio: design and contracts (0.9.0 work in progress)

Goal: turn OpenJutsu (a ComfyUI-backed set of MiniMax H3 video-editing operations on the Sarness Engine) into a workspace inside Semechka:
prepare a video (trim, crop, aspect, resolution), add any number of reference pictures, say what to replace (a person, a motorcycle, a car ...),
pick a model that really supports the operation, run it, and watch exactly what the Spark is doing.

Three layers, one source of truth each:

| Layer | Owns | Never does |
|---|---|---|
| **Engine** (Sarness, worktree `~/Documents/Sarness-oj-studio`, branch `feat/openjutsu-studio`) | what the installed models can do (`modes`, `operation_status`, limits), building the ComfyUI graph, running it, the **trace** of what ComfyUI did, completed segment files | storing user media beyond the job, knowing Semechka users |
| **Semechka backend** (`backend/semechka/studio*.py`, `routers/studio.py`) | projects (non-destructive edit specs), prepared/exported media assets, ownership/authorisation, translating the engine trace for one user | trusting the browser's claims about what a model supports |
| **Semechka web** (`web/src/routes/(app)/openjutsu`, `web/src/lib/studio`, `web/src/lib/components/studio`) | presentation, local interaction state | inventing progress, capability or file state |

Rules that apply everywhere: never fabricate progress (no timer-based percentages; indeterminate when the engine has none); never silently drop an input
(an unsupported or unused input is shown as such with the reason); an operation is *operational* only when the engine says its status is `ready`
(validated by a real run); `experimental` needs an explicit opt-in; `unavailable` is shown greyed with the reason.

## 1. Vocabulary

* **Mode** (what the person wants): `subject_replacement`, `character_replacement`, `object_replacement`, `vehicle_replacement`, `motion_transfer`,
  `reference_to_video`, `video_restyling`, `advanced_editing`. Modes share engine operations; a mode is a *preset over an operation*, not a pipeline.
* **Operation** (what the engine runs): OpenJutsu `swap`, `advanced_swap`, `object_edit` (+ `object_mode` change/remove/add/background), `check_mask`,
  and a plain `reference_video` (H3 reference-to-video, up to 9 pictures, no source video).
* **Subject**: what in the video is the target. `kind` is `person | vehicle | animal | object | other`; the engine tracks it with SAM 3.1 from a
  **text** description, optionally narrowed by a **box** or **points** on the first frame of the prepared clip (engine support is advertised, see 2.4).
* **Active reference**: a reference picture that really conditions the generation. Uploading is unlimited (library quotas aside); *active* is limited by the
  operation (`reference_policy.active_max`). Inactive pictures stay in the project and are labelled "not used in this run".
* **Prepared asset**: the video the engine receives (trim + crop + aspect treatment [+ scale]); a normal library asset with `origin: "prepared"`.
* **Export asset**: a generated result scaled to the requested output size (`origin: "export"`); the engine's native result is kept.

## 2. Engine contract (Sarness, `/v1`, Bearer token as today)

### 2.1 Profile additions (registry `profiles[]` entry and `<profiles>/<id>.json`)

```jsonc
{
  "modes": [{                      // authoritative list of what this profile can be asked to do
    "id": "vehicle_replacement", "label": "Vehicle replacement",
    "operation": "object_edit", "fixed_inputs": {"object_mode": "change"},   // merged into inputs by clients
    "subject_kinds": ["vehicle"],      // kinds the graph is built for / tested with
    "needs": {"video": true, "refs": {"min": 0, "active_max": 1}, "subject_text": true},
    "status": "experimental",          // ready | experimental | not_ready | unavailable (same vocabulary as operation_status)
    "reason": "wired in the OpenJutsu graph; no real-inference validation for vehicles yet",
    "validated": null | {"job": "...", "at": 1793..., "subject_kind": "vehicle"}
  }],
  "limits": {
    "resolutions": {"presets": ["480p","576p","736p","768p"], "max_short_edge": 768, "step": 32,
                    "note": "H3 works in steps of 32 px; 768 is the largest short edge tested on this 121 GB machine"},
    "max_source_seconds": 30,           // longer videos must be trimmed first (engine default; may be lower)
    "fps": 24,
    "memory": {"measured": [{"short_edge": 736, "peak_mb": 78848, "job": "...", "clip_seconds": 2.5}], "estimate_mb": 86016}
  },
  "reference_policy": {"swap": {"min": 1, "active_max": 1}, "advanced_swap": {"min": 1, "active_max": 4},
                       "object_edit": {"min": 0, "active_max": 1}, "check_mask": {"min": 0, "active_max": 0},
                       "reference_video": {"min": 1, "active_max": 9}},
  "subject_selection": {"text": true, "box": true|false, "points": true|false, "mask_paint": true|false,
                        "note": "box/points start SAM from the first frame of the prepared clip"}
}
```

`inputs_schema.refs.max_files` becomes **64** (a transport sanity cap, not a model limit). `requirements()` no longer rejects "too many pictures" for
an operation: it uses the first `active_max` pictures **in the given order** as conditioning and records every other one as `used:false` with the reason
(see 2.3). Fewer than `min` still fails with 422. New optional inputs: `short_edge` (int, multiple of 32; replaces `resolution` when set; refused above
`limits.resolutions.max_short_edge` with a 422 that says so), `subject_box` (`[x,y,w,h]` fractions of the frame), `subject_points`
(`[{"x":f,"y":f,"positive":bool}]`), `subject_kind`.

`GET /v1/profiles/{id}/estimate?operation=&width=&height=&short_edge=&seconds=&refs=` →
`{"processing": {"width": 1312, "height": 736}, "clip_seconds": 6, "frames": 60, "memory": {"peak_mb": 78848, "basis": "measured|extrapolated|none", "note": "..."}, "seconds": {"estimate": 197, "basis": "measured|none"}}`.
`basis:"none"` when nothing was measured at that size (never a made-up number).

### 2.2 Trace (the live inspector's source of truth)

Every profile job writes `<data>/traces/<job_id>.jsonl` (append-only events) and keeps a live summary; both survive an engine restart and are readable
for finished/failed jobs. Events come only from ComfyUI's own websocket and `/history` and from the engine's own phase changes; nothing is estimated.

`GET /v1/jobs/{id}/inspect?after=<seq>` →

```jsonc
{
  "job_id": "j_...", "backend": "comfyui", "profile": "openjutsu-minimax-h3", "operation": "swap",
  "status": "queued|running|completed|failed|cancelled",
  "stage": "queued|starting_backend|loading_models|tracking_subject|generating_scene|assembling|saving_output|done|failed",
  "stage_label": "Generating scene 3 of 7",
  "prompt_id": "uuid-or-null",                    // the ComfyUI prompt id, = the id in ComfyUI's own /history
  "started_at": 1793.., "updated_at": 1793.., "elapsed_s": 143.2,
  "progress": {"fraction": 0.42, "indeterminate": false, "basis": "scenes_saved/total_scenes"},   // fraction null + indeterminate true when unknown
  "sampling": {"node_id": "73", "title": "Sample Video + Audio", "step": 3, "max": 6, "scene": 3},   // null between samplers
  "scenes": {"total": 7, "completed": 2, "current": 3,
             "segments": [{"index": 1, "state": "saved", "frames": 141, "duration_s": 5.875, "width": 736, "height": 1312, "bytes": 1234567,
                           "seconds_to_make": 38.1}]},   // only segments whose file was written AND verified (ffprobe ok)
  "loop": {"iterations_ended": 2,
           "meaning": "Each 'Loop End' means one clip (scene) was saved as a checkpoint and the next clip is being set up. It is not the end of the job: the final video exists only after 'Final Video' has run and the output file was verified."},
  "nodes": {"total": 70, "done": 41, "running": ["73"], "pending": 26, "cached": 3, "failed": 0},
  "node_states": {"26": {"state": "done|running|pending|cached|failed", "class_type": "...", "title": "...", "runs": 1, "total_ms": 812, "last_ms": 812}},
  "conditioning": {"video": {"node_id": "5", "file": "<name as uploaded to ComfyUI>"},
                   "references": [{"position": 1, "node_id": "2", "file": "...", "used": true, "name": "yellow_coat.png"},
                                  {"position": 2, "file": "...", "used": false, "reason": "swap conditions on 1 picture"}],
                   "agree": true},                // every used reference has exactly one LoadImage node whose image == the uploaded file
  "memory": {"ram_available_mb": 21400, "comfyui_rss_mb": 58112, "vram_mb": null, "source": "/proc/meminfo + process RSS (unified memory: NVML reports no device memory)"},
  "output": {"state": "none|writing|ready", "file": {"width": 736, "height": 1312, "duration_s": 5.875, "bytes": 1229332, "has_audio": true}},
  "error": null | {"message": "...", "node_id": "63", "node_title": "Trim Overlap", "class_type": "..."},
  "events": [{"seq": 17, "t": 1793.., "type": "stage|node_start|node_done|node_cached|progress|scene_saved|loop_end|error|output|log",
              "node_id": "73", "title": "Sample Video + Audio", "scene": 3, "value": 3, "max": 6, "message": "..."}],
  "next_seq": 18
}
```

`events` holds only events with `seq > after` (cap 500; `next_seq` is the cursor), the rest of the document is always the current summary. Per-sampler
`progress` events are coalesced (at most one per second per node). `completed` is reported only after the output file exists, was copied and probed
(`output.state == "ready"`); a "Loop End" event never changes `status` or `output`.

`GET /v1/jobs/{id}/graph` → `{"prompt_id": "...", "format": "comfyui-api", "nodes": {"26": {"class_type": "...", "title": "...", "inputs": {"seed": 123, "width": ["11", 1]}}}, "order": ["2","5",...]}`
the exact graph that was queued (links are `[node_id, output_index]`; long strings truncated to 2000 chars, file paths reduced to base names).
`GET /v1/jobs/{id}/segments/{n}` → the n-th completed segment as `video/mp4` with Range support; 404 until it is saved and verified.
`GET /v1/jobs/{id}/preview` → the newest preview picture ComfyUI sent for this job (JPEG) or a frame taken from the newest completed segment; 404 when none exists.
`GET /v1/jobs/{id}/history` → ComfyUI's `/history/{prompt_id}` entry for the job reduced to `{status, messages, outputs, node_errors}` (read-only; 404 if ComfyUI no longer has it).

Profiles that do not run on ComfyUI answer `inspect` with the same shape but `backend` = their backend, `nodes: null`, and a stage/progress built from their own events;
`graph`/`history` answer 404 `{"code": "no_graph"}` and clients hide "View ComfyUI".

### 2.3 Reference accounting

`job.workflow.inputs.refs` is exactly what the caller sent. The runner builds one `LoadImage` per **used** reference, verifies afterwards that
`len(used) == len(picture_nodes)` and that each node's `image` equals the file uploaded for that position (otherwise the job fails before queueing), and writes
the result to `conditioning` in the trace and `lineage.openjutsu.references` in the output.

### 2.4 Subject selection

Text target (`target` / `object_target`) is always available. `subject_box` / `subject_points` are advertised only if validated on the Spark with `check_mask`
(`subject_selection.box|points`). The engine uses them to seed SAM 3.1 on frame 0 of the source video it receives (`SAM3_Detect` -> `initial_mask` of the tracker).

## 3. Semechka backend contract (`/api/studio`, session cookie + CSRF as every route)

All routes: owner only, 404 for anyone else's id; gated like the Video mode (`_gate(ctx, "video")`) plus access to an editing-profile model.

```
GET    /api/studio/capabilities            -> {modes:[{id,label,operation,fixed_inputs,subject_kinds,status,reason,models:[{model_id,alias,family,status,reason,needs,reference_policy,limits}]}],
                                               models:[{model_id,alias,family,kind:"editing_profile|generator",modes:[mode ids],unsupported:[{mode,reason}]}],
                                               limits:{video_max_seconds,upload_max_mb,max_assets,resolutions:{...}}}
POST   /api/studio/projects                {source_asset, title?} -> project
GET    /api/studio/projects                -> [project summary]  (newest first, with last job status + thumbnail asset)
GET    /api/studio/projects/{id}           -> project (spec, prepared asset, jobs[])
PUT    /api/studio/projects/{id}           {spec, expected_updated_at?} -> project   (spec validated; 409 when stale)
DELETE /api/studio/projects/{id}           (never deletes the source asset or any result)
POST   /api/studio/projects/{id}/plan      {spec?} -> {plan}                 (pure; no side effects)
POST   /api/studio/projects/{id}/prepare   {spec?} -> {task, reused:boolean, asset?}   (starts or reuses; poll GET /api/media/edits/{task} like any edit)
POST   /api/studio/projects/{id}/generate  {spec?, accept_experimental?} -> {job, prepared_asset, accounting}
POST   /api/studio/jobs/{job}/export       {width,height,fit:"scale"|"pad"} -> {task}   (derived asset origin "export"; engine result untouched)
GET    /api/studio/jobs                    -> [{job, project_id, status, mode, model, created_at, outputs}]   (reopen/inspect)
GET    /api/studio/jobs/{job}/inspect?after=N          -> engine inspect document + {"comfy": {"available": bool}, "user_status": MediaJob status}
GET    /api/studio/jobs/{job}/inspect/stream           -> text/event-stream, event "inspect" (same JSON), Last-Event-ID = next_seq; heartbeat comment every 15 s
GET    /api/studio/jobs/{job}/graph                    -> engine graph (read-only)
GET    /api/studio/jobs/{job}/history                  -> engine history (read-only)
GET    /api/studio/jobs/{job}/segments/{n}             -> video/mp4, ranged
GET    /api/studio/jobs/{job}/preview                  -> image/jpeg
```

`plan` returned by `/plan` and embedded in `/generate`:
```jsonc
{"input":    {"width":1920,"height":1080,"duration":42.0,"rotation":0,"fps":30,"audio":true},
 "trim":     {"start":3.0,"end":8.0,"seconds":5.0,"mode":"copy|reencode","why":"start is on a keyframe"},
 "frame":    {"aspect":"9:16","treatment":"crop","crop":{"x":656,"y":0,"w":608,"h":1080}},
 "prepared": {"width":608,"height":1080,"scale":false},
 "processing": {"short_edge":576,"width":576,"height":1024,"preset":"576p","exact":false,"why":"largest H3 size not above the requested 1080p is 768p; 576p was chosen to stay near the prepared size",
                "memory":{"peak_mb":null,"basis":"none"}},
 "output":   {"requested":{"preset":"1080p","width":1080,"height":1920},"native":{"width":576,"height":1024},"scaling":"upscale|downscale|none",
              "note":"The model produces 576×1024; 1080×1920 is made by scaling it up (no new detail)."},
 "warnings": [], "errors": []}                       // errors block /generate with a plain sentence
```

`MediaJob.params` for a studio job additionally holds `studio: {project_id, mode, operation, subject, references:[{asset,active,subject,label}], prepared_asset, requested_output, plan}` so a job reopens with every
reference it had (active or not).

Media assets gain `origin` values `prepared` and `export` (listed in the Library with their parent, never mixed up with `upload`/`edit`/`generated`).

## 4. Web contract

* Route `/openjutsu` (nav id `openjutsu`, offered when `capabilities.models` contains an editing profile the person may use).
* Steps (one page, a stepper that is also a summary): **1 Video -> 2 Prepare -> 3 References -> 4 Operation & subject -> 5 Model & output -> 6 Generate**; the job panel opens beside/below on generate and stays reachable from `Jobs` and from the Library.
* Editing state lives in the project (server), autosaved (debounced 600 ms); the browser keeps only per-viewer conveniences.
* `JobInspector` (View Process) and `ComfyGraphView` (View ComfyUI) are standalone components that take a `jobId` and use only the `/api/studio/jobs/{id}/...` routes above; they work for running, finished and failed jobs.
