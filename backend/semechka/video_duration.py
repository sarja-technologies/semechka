"""Video length in SECONDS, for every workflow that generates a clip from a prompt (chat media panel, media API, agent ``generate_video``).

People think in seconds; engines want frames and a frame rate, and video models only accept some frame counts (e.g. 1 more than a multiple of 4 or 8). This module is the
single source of truth that turns one into the other, as pure functions (no I/O), so the page, the API and the agent tool cannot disagree:

* ``spec``     what to show for a model: the frame rate that will be used (automatic), the shortest and longest clip, the increment, and the exact durations on offer
               when the model only supports specific ones.
* ``to_frames`` seconds -> (frames, fps, actual seconds): the nearest frame count the model accepts, clamped to the model's range and to the organisation's
               longest-clip limit. The ACTUAL duration is always returned and shown; nothing is silently different from what the page displays.

The model facts are the engine's own (``media.limits``: ``frames`` [min, max], ``frame_step``, ``fps`` [min, max], optional ``durations`` [seconds] for models that
only support specific lengths; ``media.defaults``: ``fps``, ``frames``). FPS is "automatic" (the model's default) unless the model really offers a range; the main
controls never show it.
"""
from __future__ import annotations

import math

DEFAULT_FPS = 16
DEFAULT_STEP = 4
MAX_LISTED = 12          # up to this many valid lengths are offered as a list instead of a slider


class DurationError(ValueError):
    """A plain sentence for the person."""


def _facts(limits: dict | None, defaults: dict | None) -> tuple[int, int, int, int, int, int]:
    lim, dfl = limits or {}, defaults or {}
    f_lo, f_hi = (list(lim.get("frames") or [5, 121]) + [121])[:2]
    step = max(1, int(lim.get("frame_step") or DEFAULT_STEP))
    fps_lo, fps_hi = (list(lim.get("fps") or []) + [None, None])[:2] if lim.get("fps") else (None, None)
    fps = int(dfl.get("fps") or fps_lo or DEFAULT_FPS)
    fps_lo, fps_hi = int(fps_lo or fps), int(fps_hi or fps)
    return int(f_lo), int(f_hi), step, fps, fps_lo, fps_hi


def valid_frames(limits: dict | None, defaults: dict | None, fps: int | None = None, max_seconds: float | None = None) -> list[int]:
    """Every frame count the model accepts (1 + k*step inside its range), cut to the organisation's longest clip at ``fps``."""
    f_lo, f_hi, step, dfps, _a, _b = _facts(limits, defaults)
    fps = fps or dfps
    first = f_lo if (f_lo - 1) % step == 0 else f_lo + (step - (f_lo - 1) % step)
    out = list(range(first, f_hi + 1, step))
    if max_seconds:
        out = [f for f in out if f / fps <= max_seconds + 1e-9]
    return out


def spec(limits: dict | None, defaults: dict | None, max_seconds: float | None = None) -> dict:
    """What the page shows for one model. ``options`` is a list of exact seconds when the model supports only specific lengths (declared, or few enough to list);
    otherwise null and the page uses a slider from ``min`` to ``max`` in steps of ``step``."""
    f_lo, f_hi, step, fps, fps_lo, fps_hi = _facts(limits, defaults)
    declared = [float(x) for x in ((limits or {}).get("durations") or []) if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
    frames = valid_frames(limits, defaults, fps, max_seconds)
    if not frames:
        return {"fps": fps, "fps_configurable": fps_lo != fps_hi, "fps_range": [fps_lo, fps_hi], "min": 0, "max": 0, "step": step / fps, "default": 0, "options": None, "declared": False,
                "frames_min": 0, "frames_max": 0, "frame_step": step, "available": False,
                "why": f"This model's shortest clip is longer than your organisation's {max_seconds:g} s limit." if max_seconds else "No valid length."}
    secs = [f / fps for f in frames]
    if declared:
        opts = sorted({s for s in declared if (max_seconds is None or s <= max_seconds + 1e-9)})
    elif len(frames) <= MAX_LISTED:
        opts = [round(s, 3) for s in secs]
    else:
        opts = None
    d_frames = int((defaults or {}).get("frames") or frames[0])
    d_frames = min(frames, key=lambda f: abs(f - d_frames))
    default = d_frames / fps
    if opts:
        default = min(opts, key=lambda s: abs(s - default))
    return {"fps": fps, "fps_configurable": fps_lo != fps_hi, "fps_range": [fps_lo, fps_hi], "min": round(opts[0] if opts else secs[0], 3), "max": round(opts[-1] if opts else secs[-1], 3),
            "step": round(step / fps, 4), "default": round(default, 3), "options": opts, "declared": bool(declared), "frames_min": frames[0], "frames_max": frames[-1], "frame_step": step, "available": True, "why": None}


def to_frames(seconds: float, limits: dict | None, defaults: dict | None, max_seconds: float | None = None, fps: int | None = None) -> dict:
    """The clip the model will really make for ``seconds``: {"frames", "fps", "seconds" (actual), "requested", "adjusted" (bool)}. Raises DurationError for nonsense."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        raise DurationError("Duration must be a number of seconds greater than zero.")
    f_lo, f_hi, step, dfps, fps_lo, fps_hi = _facts(limits, defaults)
    if fps is not None:
        if isinstance(fps, bool) or int(fps) != fps or not fps_lo <= fps <= fps_hi:
            raise DurationError(f"fps must be between {fps_lo} and {fps_hi} for this model.")
        use = int(fps)
    else:
        use = dfps
    declared = [float(x) for x in ((limits or {}).get("durations") or []) if isinstance(x, (int, float)) and not isinstance(x, bool) and x > 0]
    allowed = valid_frames(limits, defaults, use, max_seconds)
    if not allowed:
        raise DurationError("This model's shortest clip is longer than your organisation allows.")
    if declared:                                       # only specific lengths exist: snap to the nearest one that also has a valid frame count
        cands = [(abs(s - seconds), s, min(allowed, key=lambda f: abs(f / use - s))) for s in declared if max_seconds is None or s <= max_seconds + 1e-9]
        if not cands:
            raise DurationError("None of this model's lengths fit your organisation's limit.")
        _d, _s, frames = min(cands)
    else:
        frames = min(allowed, key=lambda f: (abs(f / use - seconds), f))
    actual = frames / use
    return {"frames": frames, "fps": use, "seconds": round(actual, 4), "requested": seconds, "adjusted": abs(actual - seconds) > 0.1}


def check_frames(frames: int, fps: int, limits: dict | None, defaults: dict | None, max_seconds: float | None = None) -> float:
    """Validate a frame-based request (the older API) with the same rules; returns the clip's seconds. Raises DurationError with the allowed values in the sentence."""
    f_lo, f_hi, step, _d, fps_lo, fps_hi = _facts(limits, defaults)
    if not fps_lo <= fps <= fps_hi:
        raise DurationError(f"fps must be between {fps_lo} and {fps_hi} for this model.")
    if not f_lo <= frames <= f_hi:
        raise DurationError(f"frames must be between {f_lo} and {f_hi} for this model.")
    if (frames - 1) % step:
        raise DurationError(f"frames must be 1 more than a multiple of {step} for this model.")
    if max_seconds and frames / fps > max_seconds + 1e-9:
        raise DurationError(f"Your organisation limits clips to {max_seconds:g} seconds ({frames} frames at {fps} fps is {frames / fps:.1f}).")
    return frames / fps
