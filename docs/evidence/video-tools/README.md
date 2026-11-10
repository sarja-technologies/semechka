# Video tools and model controls: real-GPU evidence (2026-11-10/11)

Engine branch `consol2/openjutsu-vid` (Spark `~/opensfield/wt-oj-vid`), dev engines on 127.0.0.1:8783/8786 against the live ComfyUI, every run through the engine's `/v1` API under
`~/spark-reset/gpu-media`. Inputs are AI-generated only (the old validated fixtures `~/sarness-comfyui/openjutsu/inputs`: a 3 s 854x480 clip of a woman at a bus stop, AI reference pictures).
`runs.json` holds job ids, wall times, `applied`, ffprobe facts and whether progress was monotonic for every run listed below. Contact sheets (`sheet_*.png`) were looked at.

| Capability | Job | Wall | Output (ffprobe) | Verdict |
|---|---|---|---|---|
| Smooth motion 2x | f66ec317a0f0 | 17 s | 854x480, 143 fr @48, 2.979 s, audio -16.9 dB (= source) | smooth, audio kept |
| Smooth motion 4x | 6d4e7b4f4e11 | 36 s | 854x480, 285 fr @96 fps, 2.969 s | works; 96 fps is unusual for players |
| Video upscale (SeedVR2) | d6a859ddc79b | 333 s (cold) | 1708x960, 73 fr, 3.042 s, audio -17.4 dB | real added detail vs Lanczos (`cmp_upscale.png`) |
| Video edit: replace_object (SCAIL-2) | 88f6d675cb07 | 261 s | 896x512, 70 fr (source 72), audio -16.9 dB | woman replaced by the yellow-coat reference, motion and bus kept |
| Video edit: restyle (LTX) | ff95bdbaaa56 | 766 s (waited for memory) | 1280x704 @25, 65 fr = 2.6 s, audio -24 dB | whole clip in anime look, motion kept. Needs a restyled FIRST FRAME: with an unstyled picture (job 99239e3bbdd7) the output stays photographic |
| Video edit: motion_transfer (Wan Animate 2) | 6b7e3a318d2e | 341 s | 480x848, 81 fr (3.375 s, audio 3.0 s), audio -16.9 dB | man picture follows the driving clip (looks at phone, turns, reaches); first run delivered a 1988x848 side-by-side comparison, fixed to deliver the clean video |
| Video edit: edit_video (Bernini-R) | 5ccba79ce775 | 297 s | 848x480, 69 fr, audio -16.8 dB | background became a snowy street, woman unchanged. First run came out 480x832 portrait (template size), fixed |
| LTX-2.3 image-to-video | ed7c10463859 | 71 s | 768x448, 73 fr = 3.042 s (promised), audio -25.6 dB | starts on the picture, natural motion |
| LTX-2.3 first/last frame | 2ba19ebbe0e5 | 38 s | 768x448, 73 fr, audio -20.5 dB | opens on first frame, ends on last frame, bus arrives |
| MiniMax H3 image-to-video | 0fd5c85d01eb | 100 s | 864x480, 73 fr, audio -25.2 dB | good |
| MiniMax H3 reference-to-video (2 pictures) | 6b782de57d43 | 58 s | 864x480, 73 fr, audio -16.8 dB | both reference people present with their look |
| Wan 2.2 5B text-to-video | b67b18ece68a | 64 s | 960x544, 49 fr = 2.042 s (promised), no audio track (model has none) | good |
| Cancel at 30 % (Wan 5B 5 s) | a81e7568df4d | 44 s | status cancelled, ComfyUI queue empty 3 s later, GPU 96 % -> 0 % within 6 s | works |
| Release | n/a | n/a | MemAvailable 98.1 -> 114.1 GiB (16.0 GB freed) | works |

## Controls (A/B, same seed, one control changed)

| Model | Control | Evidence | Verdict |
|---|---|---|---|
| Wan 2.2 5B | steps | `steps=8` reports "step n of 8" (`sampler_steps_last` 8) vs 20, wall 30 s vs 64 s, very different render | proven |
| Wan 2.2 5B | guidance | cfg 1.0: 34 s (one pass), cfg 9: 56 s, renders differ strongly (`sheet_wan5b_ab.png`) | proven |
| Wan 2.2 5B | seed | seed 21 vs 22: different scene | proven |
| Wan 2.2 5B | negative prompt | output changes; "dog, animals" removed the animals in one of two seeds and a dog appeared in the other (`sheet_wan5b_negative.png`) | wired and effective, NOT a guarantee of removal |
| Wan 2.2 14B (Quality only) | steps | `steps=10` reports 5 per sampler vs 10, wall 52 s vs 112 s; last-frame mean pixel diff 4.2 | proven |
| Wan 2.2 14B (Quality only) | guidance | cfg 1: 54 s vs 112 s, pixel diff 10.3 | proven |
| Wan 2.2 14B (Quality only) | negative prompt | pixel diff 11.7, traffic composition changed; the requested bus did not appear within 1 s in either run | wired, effect small on a 1 s clip |
| Wan 2.2 14B Fast | steps/negative/guidance | Fast is the fixed 4-step cfg 1 recipe: refused with a plain message | not offered |
| LTX-2.3 | guidance (cfg above 1) | cfg 2 renders a different clip, runs both branches | proven |
| LTX-2.3 | negative prompt | only meaningful at cfg above 1; "dog" did not remove the dog the prompt asked for (`sheet_ltx_ab.png`) | wired, weak |
| LTX-2.3, H3, Z-Image, others | negative / steps / loras / fps | no graph node (fixed sigma list, BasicGuider), or no verified LoRA slot; fps only changes playback | still refused |
