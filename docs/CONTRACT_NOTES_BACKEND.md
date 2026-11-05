# Contract notes from the backend worker (OpenJutsu Studio)

Nothing in `docs/OPENJUTSU_STUDIO.md` was edited. These are the places where the contract was silent, ambiguous or where the backend deliberately
differs. The lead decides which of them go back into the contract.

1. **The spec (version 1) is not defined in the contract.** The backend adopted the shape the UI worker already wrote in `web/src/lib/studio/types.ts`
   (`source_asset, trim{start,end}|null, crop{x,y,w,h}|null, frame{aspect,ratio,lock,treatment}, resolution{preset,width,height,lock_aspect},
   references[{asset,active,subject,label}], mode, subject{text,kind,box,points}, model, settings, accept_experimental`) and added `version: 1` and
   `audio: "keep" | "mute"` (default keep). `crop` is in pixels of the picture AS DISPLAYED (after rotation), before the aspect treatment. Unknown keys are
   ignored and not stored; wrong values are a 422 with a sentence.
2. **Event cursor (aligned with the engine, `CONTRACT_DEVIATIONS.md` item 1).** The engine's `next_seq` is last+1 and it wants `last_seq` as `after`. Semechka keeps the contract as
   written: its documents carry `next_seq` (= engine `last_seq` + 1) and also `last_seq`; Semechka's `?after=` and the SSE `Last-Event-ID` are `next_seq` values ("from this event on"),
   and the proxy hands the engine `after - 1`. The SSE `id:` is `next_seq`. A client that sends the last seq it saw as `after` receives that one event again (harmless).
3. **Structured engine inputs.** `subject_box` / `subject_points` are lists. `validate_profile_inputs` now understands an input schema type `json` (value sent as
   native JSON; a JSON string is parsed). The double declares them with `type: "json"`. If the engine declares them as `string`, the Studio would send a list
   to a string input and be refused: the engine worker should publish `json`.
4. **Processing size rule.** The contract example picks 576p "to stay near the prepared size"; the lead's instruction (implemented) is: the largest supported
   preset NOT ABOVE the requested output short edge, else the smallest. So a 1080p request on 608x1080 processes at 768p (the largest tested size), not 576p. The
   text in `processing.why` says exactly that. Presets come from `limits.resolutions.presets`; arbitrary multiples of 32 below `max_short_edge` are NOT chosen
   even though the engine would accept `short_edge` for them.
5. **Reference accounting.** The UI's `RefItem.active` means "the person wants this picture used". The backend sends to the engine only the active pictures,
   in order, up to `reference_policy[op].active_max`; every other picture (switched off, or active but beyond the limit) is listed in
   `accounting.not_used` with a reason. Fewer active pictures than `min` is a 422 (`references_missing`). The engine is never asked to drop anything.
6. **Response shapes the contract leaves open.** `prepare` -> `{task: "<edit id>", reused, asset|null, plan, task_status?, identity?}`; `identity: true` means the
   source already is what the engine wants (`asset` is the source, no new file). `generate` -> `{job: "<id>", job_detail: <media job>, prepared_asset: <asset>,
   accounting, plan, forced_unsaved}`; it answers **202** `{code: "preparing", task}` when the prepared copy takes longer than 90 s (call generate again).
   `plan` additionally carries `audio`, `prepare {mode: none|copy|reencode, ops, key}`, `frame.pad|stretch`, and `trim.mode` is `none|copy|reencode`.
7. **Added routes.** `GET /api/studio/jobs/{job}` (one row, same shape as the list; refreshes an active job). `GET /api/studio/jobs` rows also carry
   `exports[]` and the whole `studio` block (incl. `accounting`).
8. **`ui.operations.openjutsu`** is added next to `work`; no `pages[]` entry is added (the UI builds the nav entry from the flag, as it does for Work).
9. **Assets.** `GET /api/media/assets?origin=` accepts `prepared` and `export`; asset JSON gains `export {job, project_id, requested, native, scaling}` and
   `prepared {key, mode, source}`. Prepared assets are found by `MediaAsset.model_id == "prepared"` (exports: `"export"`).
10. **No-save organisations** (`privacy.chat_retention_days = 0`) store no job parameters, so their Studio jobs have no `studio` block and are not listed by
    `GET /api/studio/jobs`; they can still be inspected live.
11. **MOV is repackaged to MP4** (never "used as it is"), to keep one container for the engine.
12. **Real engine items the backend relies on:** `modes[].status/reason/operation/fixed_inputs/needs`, `reference_policy[op]`, `limits.resolutions.presets|max_short_edge|step`,
    `limits.max_source_seconds`, `limits.memory.measured[]`, `subject_selection.box|points`, inputs `short_edge` (optional), `resolution` enum, `subject_box`,
    `subject_points`, `subject_kind`, `accept_experimental`, `operation_status[op]`; inspect/graph/history/segments/preview/estimate as in 2.2.
    A profile without `modes` is shown as "does not say which operations it can do" and offers nothing.
13. **Engine deviations absorbed** (from `Sarness-oj-studio/docs/CONTRACT_DEVIATIONS.md`): a mode whose `profile` is another profile (`reference_to_video` -> `openjutsu-reference-video`) is
    not offered by this profile's model (reason names the other profile) and `generate` refuses it; `memory.basis: "extrapolated"` is shown with the engine's note; the unvalidated-profile 409
    (needs `accept_experimental`) is passed on as the engine's sentence; extra pictures never reach the engine in the first place (see 5), and the cancelled job is `status: cancelled, stage: failed`.
