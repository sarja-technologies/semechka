# Wan 2.2 14B text-to-video and HunyuanVideo 1.5 (t2v, i2v): real runs, 2026-11-10/11

Engine branch `consol2/openjutsu-wanhy` (worktree `~/opensfield/wt-oj-wanhy`, dev engine on 127.0.0.1:8784) -> the live ComfyUI 0.44.2 on the Spark, under the exclusive GPU lock.
Weights: converted from the Diffusers files into NEW ComfyUI-format files (`~/opensfield/models/CONVERTED.md`; `converted.sha256`, `sources.sha256`, `conversion_checks.txt` here).
Every run went through `POST /v1/jobs` of the engine; `<name>.json` is the job's own summary (applied settings, ffprobe, progress series); `*_sheet.png` are contact sheets (6 frames) I looked at.
Footage is AI-generated; the i2v start frame is a frame of an AI-generated clip.

| Run | Model / mode | Request | Wall | Result (ffprobe) | Verdict |
|---|---|---|---|---|---|
| `wan14_t2v_fast_2s` | Wan 2.2 14B t2v, Fast (4 steps, LightX2V LoRA) | 2 s, 832x480 | 44 s | 33 frames, 16 fps, 2.0625 s | coherent fox in snow, sharp (`wan14_fast_f20.png`) |
| `wan14_t2v_quality_2s` | same, Quality (20 steps, cfg 3.5) | 2 s, 832x480 | 214 s | 33 frames, 2.0625 s | coherent, darker dawn look, fox in the distance |
| `wan14_t2v_fast_5s_916` | same, Fast | 5 s, 480x832 | 102 s | 81 frames, 16 fps, 5.0625 s, 480x832 | street musician with guitar, stable identity and hands |
| `wan14_t2v_fast_8s` | same, Fast | 8 s, 832x480 | 182 s | 129 frames, 8.062 s | sailboat, gulls, stable |
| `hy_t2v_1s_progress` | HunyuanVideo 1.5 t2v | 1 s, 848x480 | 106 s | 25 frames, 24 fps, 1.0417 s | progress 0.94 at step 20/20, then 1.0 (after the runner fix) |
| `hy_t2v_2s` | HunyuanVideo 1.5 t2v | 2 s, 848x480 | 221 s | 49 frames, 2.0417 s | coherent fox (`hy_t2v_f24.png`) |
| `hy_t2v_5s` | HunyuanVideo 1.5 t2v | 5 s, 848x480 | 797 s | 121 frames, 24 fps, 5.0417 s | coherent wet street, guitar, hand (`hy_t2v_5s_f60.png`); prompt framing drifted to the legs |
| `hy_i2v_2s` | HunyuanVideo 1.5 i2v | 2 s from a start frame | 226 s | 49 frames, 2.0417 s | starts on the given frame, the woman looks up, identity kept |
| `hy_i2v_5s` | HunyuanVideo 1.5 i2v | 5 s | 819 s | 121 frames, 5.0417 s | the prompted red bus drives past, identity kept (`hy_i2v_5s_f100.png`) |
| `hy_t2v_cancel` | HunyuanVideo t2v, cancel at 20 % (step 4/20) | | 40 s | job `cancelled`; ComfyUI queue empty 1.6 s after the request; GPU 0 % shortly after | cancel works |

All progress series are monotonic. `POST /v1/release` after the runs: MemAvailable 77.1 -> 102.3 GB and 86.2 -> 112.9 GB (25-27 GB of ComfyUI models freed).
Neither Wan 14B t2v nor HunyuanVideo makes sound (no audio stream), by design of the models.
