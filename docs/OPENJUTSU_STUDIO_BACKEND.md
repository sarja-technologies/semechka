# OpenJutsu Studio: Backend

What the Semechka server adds for the Studio (contract: `OPENJUTSU_STUDIO.md` section 3; deviations and open points: `CONTRACT_NOTES_BACKEND.md`).
Engine side is Sarness (section 2). The browser harness and the engine double are described in `web/e2e_media/STUDIO_HARNESS.md`.

## Files

| File | Role |
|---|---|
| `backend/semechka/studio_models.py` | the one new table `studio_projects` |
| `backend/semechka/studio_plan.py` | `plan()`: pure function (source facts + spec + engine limits -> trim mode, crop rectangle, prepared size, processing size, output scaling, warnings, errors) and the prepared-asset cache key |
| `backend/semechka/studio_svc.py` | spec validation, projects, key-frame facts, prepare, capabilities, generate (accounting), export, job list |
| `backend/semechka/studio_inspect.py` | the proxy of the engine's trace: ownership, path scrubbing, fallback document, stale handling, SSE |
| `backend/semechka/routers/studio.py` | `/api/studio/...` (HTTP only) |
| `backend/semechka/media_lib.py` | extended, not forked: `EditSpec` gained `stretch`, `pad`, `scale`, `stream_copy`, `force`, `audio_copy`, `scale_first`; `start_edit` takes `origin`, `extra_meta`, `key`; `Probe.rotation`; origins `prepared`, `export` |
| `backend/semechka/adapters/sarness_media.py` | `media_inspect`, `media_graph`, `media_history`, `media_preview`, `media_segment_open` (streamed, Range passed through), `media_estimate` |
| `backend/semechka/adapters/sarness.py` | profile discovery and the registry whitelist carry `modes, limits, reference_policy, subject_selection, operation_status` into `Model.meta.media.profile` |
| `backend/semechka/routers/me.py` | `ui.operations.openjutsu = {enabled, reason}` |

## Routes (all session cookie + CSRF; Video mode gate; owner only, a stranger's id answers exactly like a missing one)

```
GET    /api/studio/capabilities
POST   /api/studio/projects                 {source_asset, title?}
GET    /api/studio/projects                 summaries, newest first
GET    /api/studio/projects/{id}            spec, source, prepared_asset (+ prepared_current), jobs[]
PUT    /api/studio/projects/{id}            {spec, expected_updated_at?, title?}   409 {code:"stale", project} when stale
DELETE /api/studio/projects/{id}            never touches an asset
POST   /api/studio/projects/{id}/plan      {spec?}  pure
POST   /api/studio/projects/{id}/prepare   {spec?}  -> {task, reused, asset, plan, identity?}   poll GET /api/media/edits/{task}
POST   /api/studio/projects/{id}/generate  {spec?, accept_experimental?} -> {job, job_detail, prepared_asset, accounting, plan}   (202 {code:"preparing", task} if slow)
GET    /api/studio/jobs                     jobs that have a `studio` block, newest first, with outputs and exports
GET    /api/studio/jobs/{job}               one row
POST   /api/studio/jobs/{job}/export       {width, height, fit: scale|pad} -> {task, scaling, note}
GET    /api/studio/jobs/{job}/inspect?after=N     N = a next_seq value (events with seq >= N); engine gets N-1 (its last_seq)
GET    /api/studio/jobs/{job}/inspect/stream      text/event-stream: `inspect` (id = cursor), `stale`, comment heartbeat every 15 s; Last-Event-ID or ?after=
GET    /api/studio/jobs/{job}/graph | /history    engine documents, read-only, paths scrubbed
GET    /api/studio/jobs/{job}/segments/{n}        video/mp4 streamed from the engine, Range passed through, never buffered
GET    /api/studio/jobs/{job}/preview             image/jpeg
```

Inspect documents are the engine's (contract 2.2) with `job_id` = Semechka's id, `user_status` (MediaJob status), `comfy.available` (the engine says it is a ComfyUI job
with a prompt id and nodes), `trace_available` and `stale`. When the engine has no trace (restart, older engine, not a Studio job) or is down for a finished job,
the answer is a minimal document built from Semechka's own record with `trace_available: false`: final status, output size, references and nothing invented.
While a job is running and the engine does not answer: `stale: true` plus `stale_reason`; the stream sends an `event: stale` and keeps trying.

## Table

`studio_projects(id, user_id -> users ON DELETE CASCADE, title_enc, spec_enc, state_enc, created_at, updated_at)`. Title, spec and the list of job ids are encrypted with
the vault's `media` purpose like every other media parameter. Created by `create_all` on start. A project owns nothing: deleting it removes the row only.

## Preparing and the cache key

`prepare` plans, then (a) uses the source itself when the plan says `none` (already H.264/AAC MP4, upright, nothing trimmed or cropped), (b) returns the existing prepared
asset when one has the same key, (c) joins a running task with the same key, or (d) starts an edit with `origin: "prepared"`.

* **Key** = `sha256(source asset id + "\n" + canonical JSON of the normalised operations)`, operations = `{v, trim[start,end]|null, crop|null, pad|null, stretch|null, scale|null, mute}`.
  Settings that do not change the file (resolution preset, lock flags, an aspect that equals the picture) are not in it. Stored in the prepared asset's encrypted metadata
  (`prep.key`) and found through `MediaAsset.model_id == "prepared"`.
* **Mode.** `copy` (stream copy, no quality loss) for a trim-only / mute-only / container change of H.264+AAC when the start is on a key frame (ffprobe `-skip_frame nokey`,
  cached in a sealed sidecar `<asset>.facts.bin`, with the rotation of older uploads); otherwise `reencode` (libx264 crf 18, aac 192k, `aresample=async=1:first_pts=0`).
  Rotated sources always re-encode: the rotation is burned in, so crops, sizes and the output are in the DISPLAYED picture and the file carries no rotation flag.
* The source is never changed. Audio is kept unless the spec says `mute`. Even sizes everywhere. A prepared file is never larger than twice the size the model works at.

## Generate

Order of checks (each a plain 422): model is an editing profile the person may use; the mode exists on that profile and its status (mode tightened by the operation's
and readiness) is not `unavailable`/`not_ready`; `experimental` needs `accept_experimental`; enough active pictures; plan has no errors (source seconds vs
`limits.max_source_seconds`); the subject text exists; box/points only if `subject_selection` advertises them AND the input exists in the schema; settings against the
engine's `inputs_schema`; quotas (`media_svc.admit`). Then the prepared video is made or reused (waits up to 90 s, else 202), files are uploaded from the library,
and `media_svc.submit_profile` creates the ordinary job. `MediaJob.params.studio` keeps `{project_id, mode, operation, subject, references (ALL, with active flags), prepared_asset,
requested_output, plan, accounting}`.

Engine inputs: `operation` + the mode's `fixed_inputs`; the subject text in the fields the operation uses (`target`+`prompt` for swap, `object_target` for object_edit,
`roles` from the labels for advanced_swap); `subject_box|points|kind`; `short_edge` and `resolution` = the plan's processing size; `accept_experimental`; the spec's `settings`.

## Export

`fit: "scale"` needs the same shape as the result (1.2% tolerance), `"pad"` fits inside the size and adds black bars. Lanczos, H.264 crf 18, audio stream-copied, even sizes,
`origin: "export"`, `parent` = the engine's result asset, metadata `export {job, requested, native, scaling}` (`scaling` is `upscale | downscale | none`, the same words as the
plan). The engine's own result is never replaced.

## Tests

`cd backend && ../../semechka/.venv/bin/python -m pytest tests/test_studio.py tests/test_studio_prepare.py tests/test_studio_generate.py tests/test_studio_inspect.py` (needs ffmpeg and
ffprobe; real files made on the fly; the only double is the engine, `tests/fake_sarness.py`, whose scripted OpenJutsu run is stepped with `fs.advance(n)` and configured with
`fs.configure(scenes=3, variant_next="fail", inspect_down=True, forget_traces=True, ...)`). Shared helpers: `tests/studio_helpers.py`, fixture `sm` in `tests/conftest.py`.

## Rollback

Additive only: one new table, new routes, new optional fields. Installing the previous version over this one leaves `studio_projects` unused (drop it by hand if wanted); prepared and
export assets stay in the Library as ordinary items (the previous version lists them like uploads). Nothing existing was migrated or altered.
