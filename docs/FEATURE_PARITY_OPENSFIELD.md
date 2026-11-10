# OpenSfield studio -> Semechka: feature parity (consolidation phase 2)

The OpenSfield browser studio on the Spark was retired on 2026-11-09; Semechka (through the Spark media engine, [MEDIA_ENGINE.md](MEDIA_ENGINE.md)) is now the only place its
tools can be used. This file lists **every** tool the studio had, what it could do on this Spark before it was retired, and what Semechka offers now.

Directive (2026-11-10): *audit the other features lost from OpenSfield, including TTS and 3D; recover those that previously worked and have usable model weights/dependencies;
put them in Experimental without cluttering normal image/video generation.*

## Sources and method

* **Before** = a read-only audit of the studio's own registry and readiness check (`app/readiness.py`) against the live ComfyUI, plus `config/verified.json` (the tools this Spark has
  actually rendered and a person looked at). Nothing in a manifest's own "status" claim is trusted. 301 registry features: 154 partner (cloud API, need Comfy Cloud credits; never
  local), 147 open (local). Of the 147 open: 140 runnable here, 37 reviewed (verified.json).
* **Now** = what a Semechka user can reach on a page. The engine publishes models, profiles and (new) Experimental tools; Semechka offers a tool only when the engine says it is runnable
  AND reviewed, and an administrator has enabled it.
* **Recovered** in this phase (Experimental only; the Images and Videos pages are unchanged): text to speech (Kokoro), music (ACE-Step 1.5), remove background (BiRefNet), split into
  layers (Qwen-Image Layered), style from a picture (Krea 2), picture to 3D relief (MoGe-2, new), and a page for the existing video upscale / smooth motion profiles.
  Evidence: section "Evidence" below and [MEDIA_ENGINE.md](MEDIA_ENGINE.md) section 9.

## Not recovered, and why

| What | Why | What it would take |
|---|---|---|
| `video.reframe` (LTX video outpainting) | **STOP.** ComfyUI here lacks the node `Float32ColorCorrect` (it is in none of the node packs on disk: ComfyMath, KJNodes, LTXVideo, VideoHelperSuite, opensfield_ops) and the installed VideoHelperSuite's `VHS_VideoCombine` has no `pix_fmt`/`crf` fields | a new node pack and a ComfyUI restart (both outside this workstream); then a real run |
| `audio.tts`, `audio.voice-clone` (Chatterbox) | the `FL_ChatterboxTTS` node pack is not installed and pins an older torch than ComfyUI's; no Chatterbox weights on disk. Voice cloning also needs a consent policy | a separate venv (like Kokoro's) + weights download + a policy decision |
| `scene3d.image-to-3d` (Hunyuan3D) | territory-restricted licence; installed only through the explicit opt-in `optional-restricted`. Not enabled, not downloaded (decision of the owner) | the owner's explicit opt-in |
| A real closed 3D object from a picture (TRELLIS 2, TripoSG/TripoSplat, SF3D, Depth Anything 3) | ComfyUI has the nodes, the weights are not on this Spark | a download and a licence check per model |
| LTX-2.5, FLUX.3 Klein, Gemma 4 encoder | gated on Hugging Face (licence acceptance + token) | the owner accepts the licences |
| 154 partner tools (Kling, Seedance, Nano Banana, ElevenLabs, Bria, ...), `marketing.faceless-video` | cloud APIs that need Comfy Cloud credits; this deployment is local only | a decision to allow paid external egress |
| Local CPU effects (`matchcut.*`, `video.clipify`, `video.deflicker`, `audio.captions`, `platform.*`, montage) | small OpenCV/ffmpeg helpers inside the old studio's web app, not part of the media engine contract; never reviewed on this Spark | port into the engine one by one if wanted |
| Per-preset pages (`app.*`, 72 open presets) | each is a fixed prompt around a model Semechka already offers (45 on Qwen-Image Edit, 27 on LTX-2.3 image to video); only 2 were ever reviewed | the prompts could become Picture studio presets later |

Note: `app.3d-figure-open` ("3D figure") is a Qwen-Image-Edit picture preset (a photo of a figurine), not 3D.

## Every open (local) tool

| Tool | Studio | Before: runnable / reviewed | Semechka now | Why / note |
|---|---|---|---|---|
| `image.edit-open` Edit Image (open) | Image models | yes / yes (35.2 s) | Images / Picture studio: Qwen-Image Edit (edit) |  |
| `image.qwen-2512` Qwen-Image 2512 | Image models | yes / yes (27.1 s) | Images: Qwen-Image 2512 |  |
| `image.t2i-open` Create Image (open) | Image models | yes / yes (33.1 s) | Images: FLUX.2 klein |  |
| `image.bg-remove` Background Remover | Image tools | yes / yes (3.2 s) | **Experimental > Tools** (BiRefNet) |  |
| `image.decompose` Layers | Image tools | yes / yes (126.2 s) | **Experimental > Tools** (Qwen-Image Layered) |  |
| `image.inpaint-brush` Inpaint | Image tools | yes / yes (30.1 s) | Picture studio: FLUX.1 Fill (inpaint) |  |
| `image.iphone-photo` iPhone Photo | Image tools | yes / yes (3.2 s) | Experimental > iPhone Photo (Semechka's own preset; OpenSfield's photo finisher is not used) |  |
| `image.outpaint` Expand Image | Image tools | yes / yes (21.2 s) | Picture studio: FLUX.1 Fill (outpaint, padded canvas) |  |
| `image.reframe` Reframe Image | Image tools | yes / no | not offered | same graph as image.outpaint (Picture studio outpaint) |
| `image.upscale` Upscale | Image tools | yes / yes (6.1 s) | not offered | SeedVR2 picture upscale: covered by Experimental > Upscale (Imajutsu) |
| `image.upscale-esrgan` Upscale · Real-ESRGAN | Image tools | yes / yes (12.3 s) | Experimental > Upscale (Imajutsu, Real-ESRGAN) |  |
| `image.upscale-esrgan-core` Real-ESRGAN 4× (graph) | Image tools | yes / no | not offered | covered by Experimental > Upscale (Real-ESRGAN) |
| `image.upscale-seedvr` Upscale · SeedVR2 | Image tools | yes / yes (42.3 s) | Experimental > Upscale (Imajutsu, SeedVR2) |  |
| `soul.cast` Soul Cast | Soul | yes / yes (27.2 s) | Picture studio: Qwen-Image 2.1 (two-picture edit) |  |
| `soul.character-sheet` Character Sheet | Soul | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `soul.cinematic` Soul Cinema | Soul | yes / yes (30.1 s) | Images: Krea 2 Turbo |  |
| `soul.id` Soul ID | Soul | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `soul.location` Soul Location | Soul | yes / no | not offered | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `soul.styles` Soul Styles | Soul | yes / yes (48.1 s) | **Experimental > Tools** (Krea 2 style reference) |  |
| `soul.t2i` Soul 2.0 | Soul | yes / yes (6.0 s) | Images: Z-Image Turbo |  |
| `video.h3-i2v` MiniMax H3 · Image to Video | Video models | yes / yes (57.2 s) | Videos: MiniMax H3 (image to video) |  |
| `video.h3-r2v` MiniMax H3 · Reference to Video | Video models | yes / yes (36.3 s) | Videos: MiniMax H3 (two references, engine level) |  |
| `video.h3-t2v` MiniMax H3 · Text to Video | Video models | yes / yes (51.2 s) | Videos: MiniMax H3 (text to video) |  |
| `video.t2v-i2v-open` Create Video (open) | Video models | yes / yes (105.2 s) | Videos: LTX-2.3 (text to video) |  |
| `video.wan22-i2v` Wan 2.2 14B | Video models | yes / yes (57.2 s) | Videos: Wan 2.2 14B (image to video) |  |
| `video.wan22-t2v` Wan 2.2 5B | Video models | yes / yes (57.1 s) | Videos: Wan 2.2 5B |  |
| `video.bg-remove` Video Background Remover | Video tools | yes / no | not offered | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `video.clipify` Clipify | Video tools | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `video.deflicker` Deflicker | Video tools | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `video.edit` Edit Video | Video tools | yes / yes (663.9 s) | Video editing profile (opensfield-video-edit) |  |
| `video.interpolate` Smooth Motion (frame interpolation) | Video tools | yes / yes (6.1 s) | **Experimental > Video tools** (smooth motion) |  |
| `video.reframe` Reframe Video | Video tools | no / no | not offered | STOP: ComfyUI lacks `Float32ColorCorrect` (in no node pack on disk) and the installed VideoHelperSuite has no `pix_fmt`/`crf` fields; needs a new node pack + ComfyUI restart |
| `video.reverse` Reverse Engineer Video | Video tools | yes / yes (3.2 s) | Experimental > REA |  |
| `video.upscale` Video Upscale | Video tools | yes / yes (192.4 s) | **Experimental > Video tools** (opensfield-video-upscale) |  |
| `cinema.camera-rig` Camera Controls | Cinema Studio | yes / yes (45.1 s) | Videos: LTX-2.3 (image to video) |  |
| `cinema.draw-to-video` Draw to Video | Cinema Studio | yes / no | not offered | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `cinema.first-last-frame` First & Last Frame | Cinema Studio | yes / yes (48.3 s) | Videos: LTX-2.3 (first/last frame, engine level) |  |
| `cinema.multishot` Multi-shot | Cinema Studio | yes / no | same model, no preset page (LTX-2.3 image to video) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `genjutsu.motion-control` Motion Control | Genjutsu | yes / yes (363.7 s) | Video editing profile: motion transfer |  |
| `genjutsu.motion-gallery` Motion Gallery | Genjutsu | yes / no | same model, no preset page (Wan Animate 2) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `genjutsu.replace-object` Replace | Genjutsu | yes / yes (438.6 s) | Video editing profile: replace object |  |
| `genjutsu.styles` Restyle Video | Genjutsu | yes / yes (177.3 s) | Video editing profile: restyle |  |
| `viral.effects` Viral Effects | Viral Hub | yes / yes (54.2 s) | same model, no preset page (LTX-2.3 image to video) |  |
| `audio.captions` Captions | Audio | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `audio.music` Music | Audio | yes / yes (18.1 s) | **Experimental > Audio** (ACE-Step 1.5) |  |
| `audio.narration` Narration | Audio | no / yes (6.2 s) | not offered | same Kokoro voice as audio.tts-kokoro (now in Experimental > Audio); the separate narration wrapper is not exposed |
| `audio.tts` Text to Speech | Audio | no / no | not offered | FL_ChatterboxTTS node pack not installed (it pins an older torch than ComfyUI's); no weights |
| `audio.tts-kokoro` Kokoro Text to Speech | Audio | no / yes (3.1 s) | **Experimental > Audio** (Kokoro, child process) |  |
| `audio.voice-clone` Voice Clone | Audio | no / no | not offered | as audio.tts (Chatterbox); voice cloning also needs a consent policy |
| `reshoot.novel-view` Reshoot | Reshoot | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `scene3d.angles` Angles | Reshoot | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `scene3d.autosprite` Sprite Sheets | Reshoot | yes / no | same model, no preset page (Z-Image) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `scene3d.image-to-3d` Image to 3D | Reshoot | no / no | not offered | Hunyuan3D: territory-restricted licence, opt-in `optional-restricted` only; not enabled or downloaded (user decision) |
| `marketing.ad-multiplier` Ad Multiplier | Marketing | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.ads-studio` Ads Studio | Marketing | yes / yes (33.2 s) | same model, no preset page (FLUX.2 klein) |  |
| `marketing.ai-influencer` AI Influencer | Marketing | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.brand-assets` Brand Kit | Marketing | yes / no | same model, no preset page (FLUX.2 klein) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.click-to-ad` Click to Ad | Marketing | yes / no | same model, no preset page (LTX-2.3 image to video) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.faceless-video` Faceless Video | Marketing | no / no | not offered | pipeline needs partner (cloud) tools |
| `marketing.product-photoshoot` Product Photoshoot | Marketing | yes / no | same model, no preset page (Qwen-Image Edit) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.shorts-studio` Shorts Studio | Marketing | yes / no | same model, no preset page (LTX-2.3 image to video) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.studio-presets` Product Motion | Marketing | yes / no | same model, no preset page (LTX-2.3 image to video) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.thumbnails` Thumbnails | Marketing | yes / no | same model, no preset page (Krea 2) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.ugc-video` UGC Video | Marketing | yes / no | same model, no preset page (LTX-2.3 image to video) | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `marketing.video-edit-montage` Montage | Marketing | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `matchcut.render` Match Cut Reel | Match Cut + Tracelab | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `matchcut.retro-fx` Retro FX | Match Cut + Tracelab | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `matchcut.subject-cuts` Subject Cuts | Match Cut + Tracelab | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `matchcut.text-motion` Text Motion | Match Cut + Tracelab | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `matchcut.thermal` Thermal | Match Cut + Tracelab | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `matchcut.tracelab` Tracelab | Match Cut + Tracelab | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `platform.billing-community` Credits & Usage | Platform | yes / no | not offered | runnable but never rendered and reviewed on this Spark; a preset of a model Semechka already offers |
| `platform.publish` TikTok Export | Platform | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `platform.virality` Virality Predictor | Platform | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `platform.website-builder` Website Builder | Platform | yes / no | not offered | local CPU effect (OpenCV/ffmpeg) in the old studio; not part of the media engine contract, not exposed |
| `scene3d.moge-mesh` Picture to 3D relief (MoGe-2) | Reshoot | new (did not exist) | **Experimental > 3D** (MoGe-2, new) | built from ComfyUI core nodes + the MoGe-2 weights already on disk (MIT) |

| App presets (`app.*`) | Count | Runnable / reviewed before | Semechka now |
|---|---|---|---|
| open: `image_qwen_image_edit_2511_int8` | 45 | 45 / 1 | the model is offered (Picture studio edit); the per-preset prompt pages are not |
| partner: `api_nano_banana_2_1_image_edit` | 41 | 0 / 0 | partner (cloud) API: not offered |
| partner: `api_seedance2_5_i2v_1080p` | 27 | 0 / 0 | partner (cloud) API: not offered |
| open: `video_ltx2_3_i2v` | 27 | 27 / 1 | the model is offered (Videos, image to video); the per-preset prompt pages are not |
| partner: `api_kling_vto` | 2 | 0 / 0 | partner (cloud) API: not offered |


## Evidence

All runs were real, on the Spark's GPU (through the shared `gpu-media` lock) or CPU, on 2026-11-10, against a dev engine (worktree `consol2/imajutsu-parity`, port 8794)
and a dev Semechka with a copy of staging data and synthetic accounts. Inputs were generated pictures (a teal mug, a harbour painting) and an ffmpeg test pattern; no real people.
Every output below was looked at or listened to; numbers are measured, not taken from the engine's claims. Files are in [evidence/parity/](evidence/parity/).

| Tool | Path | Wall time | Result (measured) |
|---|---|---|---|
| Text to speech (Kokoro, CPU child) | engine | 8.1 s cold, 3.1 s warm | 7.325 s WAV, transcript exact (job 1); bf_emma at 1.4x fine except one ASR mishearing ([transcripts](evidence/parity/tts-transcripts.txt)). Child RSS 1.76-2.41 GB, stops itself when idle (checked after 120 s) |
| Text to speech | Semechka, org member | 5.6 s | `audio/wav` 346,844 B, 7.225 s, 24 kHz mono, mean -24.3 / max -6.0 dB; transcript exact |
| Music (ACE-Step 1.5) | engine | 20 s asked | 20.0 s MP3, 48 kHz stereo, mean -14.3 / max 0.0 dB; **music to 16.5 s, then 3.5 s of silence** ([spectrogram](evidence/parity/outputs/music-20s-spectrogram.jpg): steady beat) |
| Music | Semechka | 12 s asked | 12.02 s MP3, music to 8.6 s then 3.4 s silence, mean -17.3 / max 0.0 dB. The length hint now says the music can end early |
| Remove background (BiRefNet) | engine / Semechka | 3.4 s / 2.4 s | 1024x1024 RGBA cut-out + mask; clean edge, the handle's hole is transparent ([on magenta](evidence/parity/outputs/bg-remove-cutout-on-magenta.jpg)) |
| Style from a picture (Krea 2) | engine | 47.2 s | a bicycle in the painting's orange/cobalt impasto; the reference's content is not copied ([pair](evidence/parity/outputs/style-reference-pair.jpg)) |
| Split into layers (Qwen-Image Layered) | engine | 127.5 s (20 steps) | 3 RGBA 640x640 layers: background with the mug and spoon removed and filled (a faint blurred ghost of the spoon is left), the mug, the spoon ([on magenta](evidence/parity/outputs/image-layers-3-on-magenta.jpg)) |
| Picture to 3D relief (MoGe-2, new) | engine / Semechka | 2.1 s / 72.4 s (queued behind layers) | GLB 12,074,448 B, 257,221 vertices, 507,176 triangles, 1024x1024 texture, plus a depth PNG ([depth](evidence/parity/outputs/moge-depth.jpg)). Opens in the photo's own view; turned, the back is absent, as the page says ([viewer](evidence/parity/screens/3d-viewer-photo-view-and-turned.jpg)) |
| Smooth motion (FILM, existing profile) | Semechka, Video tools page | 4.4 s | 36 frames at 12 fps -> 71 frames at 24 fps (2.958 s, 640x360); in-between frames are clean ([frames 20-22](evidence/parity/outputs/smooth-motion-frames-20-22.jpg)) |

Progress was monotonic in every run (the engine's phases are in the JSON reports: [engine batch](evidence/parity/engine-runs-batch1.json),
[layers](evidence/parity/engine-runs-layers.json), [through Semechka](evidence/parity/semechka-runs.json)). Semechka's own progress line for Kokoro stays at "Preparing" until the
file is done (the CPU child reports no steps).

Pages ([screens](evidence/parity/screens/)), desktop 1366 px and phone 390 px, no horizontal overflow, no console errors: the Experimental hub with the new Audio, Tools and 3D groups
and a Video tools entry; Audio, Picture tools, 3D and Video tools with earlier results. The Images and Videos pages were checked unchanged.

Gaps found while testing and fixed in this phase: the 3D viewer framed the bounding box (the far wall made the mug tiny), so it now opens from the photo's camera; the Video tools
page showed raw profile ids, so `/api/media/options` now also returns `display_name` (the engine's name unless an administrator renamed the row); the music length hint claimed an
exact length.

Not tested here: `style-reference` and `image-layers` through Semechka's own form (both ran on the engine API; the Semechka path is the same tool endpoint as remove background),
video upscale (SeedVR2) on the Video tools page (it is the existing, already reviewed profile).
