# Media composer: attach, preview, edit (0.8.0)

Pictures and videos are first-class in the message box. Drop them, paste them, pick them, or take them from the Library; see what they are;
play them; trim and crop them for real; and use the result as the source of an OpenJutsu operation, all without leaving Semechka.

## What a person sees

* **Drop target.** Dragging files over the window lights up the composer; dropping anywhere in the app attaches. Pasting a picture works too.
* **Tray at the top of the composer.** One compact card per file: thumbnail, duration badge for videos, the part it plays (a role), a play
  control that plays the video right in the card, replace, edit and remove. Cards reorder by drag or Alt+Arrow. The prompt stays directly under the tray.
* **Real uploads.** Progress, cancel and retry per file; up to three at a time, the rest wait. Cancelling, a lost connection or a refused file leaves
  nothing on the server.
* **Viewer.** Click a card (or a Library item, a result, a sent attachment): pictures zoom and pan; videos have a full player (scrubber, volume,
  speed, full screen, frame stepping, shortcuts: Space/K, arrows, J/L, comma and full stop, M, F, I/O in the editor).
* **Editor (only when opened).** Video: a frame timeline with two handles and exact start/end times, crop with Original / 1:1 / 16:9 / 9:16 / 4:3 /
  Free, a preview of the cropped result, optional removal of sound. Pictures: crop, rotate, mirror, zoom. Saving makes a **new** file; the original is
  never changed. "Use as source" swaps the edited file into the same place in the tray.
* **OpenJutsu roles.** The roles come from the input schema the engine publishes for the chosen operation (Source video; Reference picture, in order),
  with OpenJutsu's documented per-operation counts. Changing the operation never drops a file: files the operation does not use stay in the tray,
  marked "Not used by check mask", and come back when the operation does. Impossible combinations are named ("Only one reference picture can be used for
  swap: 2 are attached") and Send stays off. There is no mask or target-picture input in the engine's schema, so none is offered.
* **Drafts.** The tray, roles, operation, model and text are kept per conversation across page changes and reloads (browser tab storage; references only).

## Server

Everything is a `MediaAsset` (no schema change). Uploaded and edited items are marked in their encrypted metadata (`origin` upload | edit, `name`,
`parent`, `edit`, `media`).

| Route | Purpose |
|---|---|
| `POST /api/media/uploads?name=&ephemeral=` | One file as the raw body, streamed to a private scratch file (never held in memory), identified from its bytes, probed, given a preview picture, sealed. |
| `GET /api/media/assets?kind=&origin=&before=` | The Library: generated, uploaded and edited items together. |
| `GET /api/media/assets/{id}` `/content` `/thumb` `/strip?n=&h=` | Details; the file by byte range (Range, 206/416); a ~480 px preview; a filmstrip for the trim timeline. Owner only (404 otherwise). |
| `POST /api/media/assets/{id}/edits` `GET /api/media/edits/{eid}` `POST .../cancel` | Trim, crop, rotate, mute with ffmpeg into a new asset; poll progress; cancel. |
| `POST /api/media/profile-jobs` | Now also accepts `asset:<input>` = library asset id (repeat for several, in order), next to `file:<input>` parts. The stored bytes go to the engine unchanged. |

* **Large files.** Files are sealed in 1 MiB AES-GCM chunks (`SMC1`, `media_store.py`; chunk-bound, reorder- and truncation-proof) so a Range request reads
  only the chunks it needs. Older results keep the whole-file format and are read as before. Memory per request is a few chunks.
* **Safety.** The type comes from the bytes (PNG, JPEG, WebP, GIF; MP4, MOV, WebM, MKV, AVI). ffmpeg is told the container (`-f`) and may read only `file:`
  paths. HEIC/AVIF, SVG, audio-only and damaged files are refused with a reason. Decoded size limits apply.
* **Scratch copies.** ffmpeg needs a plaintext file for a few seconds: `data/tmp/media/w-*` (0700), or RAM-backed `/dev/shm` for no-save items;
  removed in a `finally`, stale ones swept after 2 h.
* **Limits.** `media.upload_max_mb` (default 512; no-save uploads are held in memory and limited to 256 MB), `media.max_assets` (uploads and edits
  count), three uploads in flight per person, one edit at a time per person, four in the server. Attachments must be on for the person
  (`chat.attachments`, `ui.file_upload`, `chat.attach`) or picture/video mode enabled.
* **No-save.** Uploads made with no-save on stay in server memory for 15 minutes and are never written to the Library; their edits are no-save too.
* **Browser can't play it?** The file is still stored and usable; a converted preview copy (H.264, 720p) is made in the background and used for
  playback only. The original is what an operation receives.
* **Needs** `ffmpeg` and `ffprobe` on the server (`SEMECHKA_FFMPEG` / `SEMECHKA_FFPROBE` override). Without them pictures still upload (size from the
  header, no preview) and videos are refused with a clear message.

## Tests

`backend/tests/test_media_library.py` (real ffmpeg, real files) and `web/e2e_media/media.spec.ts` (real Chrome, the real server, an engine double that
speaks the Sarness job protocol with OpenJutsu's real input schema). Run: `cd web && npx vite build && npx playwright test -c playwright.media.config.ts`.

## The ordinary chat: attachments and iPhone Photo

* **One entry point, nothing permanent.** The composer shows a small "+" (upload or Library). Nothing else about attachments is on screen until something is attached: then a compact tray appears
  (square thumbnails, a count, "Remove all", an always-visible remove button, drag or Alt+Arrow to reorder). Drag and drop anywhere over the window and paste of pictures (one or several; text paste is never intercepted)
  work as before. The OpenJutsu editing profile keeps its labelled add cards; only plain chat/picture/video lose them.
* **What a model does with pictures comes from the engine**: `GET /api/media/options` now lists `references` (how many pictures a model takes in all; `modes` says whether it edits). The first picture is the
  one to change, the others are reference pictures (the engine numbers them image 1, 2, 3 in that order; reordering changes the roles the program chose). More pictures than the model takes are kept, marked "Not used"
  with the reason, and said in one calm note, never dropped silently; a model that cannot edit marks them all. The server refuses a request that exceeds the model (`too_many_pictures`, 422). A chat model without vision
  still gets the message and the attachments but only reads their names: the thumbnails say "Names only". The send button says what will happen ("Make picture", "Edit picture", "Animate picture", "Make iPhone Photo").
* **iPhone Photo** is a chip next to the operation selector, shown only with Picture selected. It sets 9:16 (720 x 1280, a larger 9:16 where the model allows), picks the model the routing table recommends for the
  preset (the Quality choice in the options picks another tier), and sends `preset: "iphone_photo"` (+ `scenes`) with the picture request. The server runs Imajutsu's own plan for it (`routers/media.preset_params` ->
  `imajutsu_svc.build_plan`), so the prompt builder, the size, the steps and the wording rules per model are the same as on /imajutsu. The message keeps the person's own words; the preset is remembered on it (label, another take and
  editing keep it). A preset request takes no pictures. Deep link: `/chat?op=image&preset=iphone_photo`.
* Tests: `backend/tests/test_chat_attachments.py`, `web/src/lib/media/roles.test.ts` (`npm run test`), `web/e2e_media/chat-attachments.spec.ts` (screenshots in `web/e2e_media/screens/chat-*.png`).

## Not done / known limits

* Chat models do not read videos: a video in a plain chat message stays in the tray, marked unused.
* Saved generation results now stream engine -> scratch file -> chunked sealed storage (cap 1 GiB, ranged playback); no-save results are held in memory and keep a 200 MB cap. Work Mode artifacts saved to the Library are still sealed from bytes.
* Animated GIFs can be viewed but not edited; EXIF orientation is applied for JPEG; video rotation metadata is honoured.
* Editing is on demand (not queued across restarts): a restart during an edit loses that edit; nothing partial is stored.

## Rolling back

No schema change: install 0.7.0 over 0.8.0. Uploaded and edited items remain in the Library table; 0.7.0 lists them with the generated ones' fields (no
preview, no name) and cannot read the chunked files (`SMC1`): delete them first (Library) if you need a clean 0.7.0.
