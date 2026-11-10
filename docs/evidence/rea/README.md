# REA scene descriptions: evidence (2026-11-10)

All media is generated (MiniMax H3 text-to-video and Z-Image stills made through the dev engine for this test). No real people, no secrets.
Setup: dev engine `osf-dev-rea` (engine branch `consol2/imajutsu-rea`, 127.0.0.1:8785 on the Spark, live ComfyUI :8190), router `spark-llm` :8080,
router key for the tests = `work.key` read inside the engine process (`OPENSFIELD_VISION_KEY_FILE`); dev Semechka on 127.0.0.1:8797 against a copy of the
staging data, synthetic account `globex-owner@staging.test`.

## Clips

| Clip | How it was made | ffprobe |
|---|---|---|
| H3 bicycle | `minimax-h3` text-to-video through the dev engine, 5 s, seed 4242 ("a red vintage bicycle leaning against a brick wall on a rainy city street at night, neon reflections, slow push in"); generation + 3 stills took 173 s under `gpu-media` | 864x480, 124 frames @24 = 5.167 s, audio track |
| multishot v1 | H3 clip + 3 Z-Image stills joined with ffmpeg; **my zoompan filter was wrong** (96 frames per looped input frame), so the dog shot is 400 s | 864x480, 421.17 s, 4 shots |
| multishot (final) | same 4 sources, fixed: H3 5.17 s, dog slow zoom 4 s, cabin slow pan 13 s, oranges static 3 s, hard cuts, silent | 864x480, 604 frames = 25.17 s |

## Runs

| Run | Path | Result | Time | MemAvailable |
|---|---|---|---|---|
| H3 bicycle, `vision=auto` (`h3_auto.json`) | engine API, inside my exclusive `gpu-media` hold (engine lock off there) | 1 shot, 3 keyframes, qwen3.6-35b-a3b loaded by REA (23.1 s) and unloaded | 32.9 s job, 5.7 s describe | 111.7 -> min 87.8 -> 112.2 GB; router: nothing loaded after |
| multishot v1, `auto` (`multi_auto.json`) | same | 4 shots, 16 keyframes (5 + 5 for the long shots), clip prompt, unloaded | 40.8 s job | 112.1 -> min 86.9 -> 111.9 GB |
| multishot v1, cancel at "Describing shot 2" (`multi_cancel.json`) | same | job `cancelled` 1.5 s after the request, model unloaded, no analysis document | 25.7 s | 111.9 -> min 87.4 -> 111.6 GB; router: nothing loaded |
| multishot final, through the Semechka REA page (`ui_multishot.json`, `ui_playwright.log`, screenshots) | browser -> Semechka -> engine; **the engine took the shared GPU lock itself** (`flock -s -w 2400` child queued behind other workstreams' exclusive runs for ~15 min) | 4 shots, 14 keyframes, 4/4 described, clip prompt, loaded by REA (23.3 s) and unloaded; keyframes load through `/api/media/jobs/{id}/analysis/files/...`; Copy works; no horizontal overflow at 390 px | 973 s, of which ~15 min lock wait | 93.5 GB at job start (15 min before the lock came free); memory at lock time was not recorded in this run (`at_lock` was added afterwards, a04be45); 21.7 GB right after loading implies about 46 GB when admission passed (needs 29.3 + 6 GB reserve), other runs held ComfyUI memory; 45.3 GB after unload; router: nothing loaded after |
| owner H3 street clip, wrong key | engine API | `"The model server refused the engine's key (HTTP 401) ..."`, all measurements kept | 1.2 s | unchanged |
| same, router URL with nothing listening | engine API | `"The model server (llama.cpp router) is not reachable ..."` | 1.6 s | unchanged |
| same, no key file | engine API | `"The engine has no key for the model server yet ..."` | 1.2 s | unchanged |
| same, through the page while heavy runs held the lock (default 120 s wait) | browser -> Semechka -> engine | after 123 s: `"Heavy image/video work held the shared GPU lock for longer than 120 s ..."`, 1 shot with 3 keyframes and measurements shown | 123 s | unchanged |

## What the model said vs what the frames show (frames looked at: `h3_keyframes.jpg`, `multishot_v1_keyframes.jpg`, `ui_desktop.png`)

* **H3 bicycle.** Said: red bicycle parked against a brick wall, wet street at night, shop with a blue sign, neon reflections, medium wide, eye level, slow push-in.
  Frames: all correct; the bicycle grows from first to last frame (measured: slow push-in, net scale 1.20). **Mismatches:** the first H3 prompt said
  "A static shot ... the camera performs a slow push-in" (self-contradictory) and the multishot v1 prompt called it "static"; a second bicycle by the shop door
  and the street lamp were never mentioned; v1 transcribed the garbled shop sign as "VIQR BIC" (the frame shows something like "VIQK BIC"), although the
  instruction says not to write text it cannot read. After commit c5c6a62 (the measured camera movement is given as a fact for the prompt) all four shot
  prompts of the final run use the measured movement (push-in, push-in, pan right, static).
* **Dog.** Said: golden retriever running on wet sand toward the camera at sunset, waves behind, warm backlight. Frames: correct (the dog trots toward camera).
* **Cabin.** Said: small wooden cabin among snow-covered pines, mountain behind, smoke from the chimney, slow pan right moving the cabin from the right of the
  frame toward the centre. Frames: correct, including the direction of the pan.
* **Oranges.** Said: light blue ceramic bowl of several oranges on a wooden counter, blurred kitchen, window light from the left, medium close-up. Frames:
  correct (4 oranges, window on the left).
* **Clip prompt** (final run): the four shots in order joined by "Cut to"; nothing in it that is not in the frames.
