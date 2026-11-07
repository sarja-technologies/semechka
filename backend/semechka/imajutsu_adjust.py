"""Imajutsu: photographic adjustments. Non-destructive and model-free: the original picture is never touched, the recipe is the whole edit.

ONE code path serves the live preview (a small JPEG, long edge <= 1024) and the final file (a new asset): ``render()``. The preview only adds a last downscale;
the grain is a seeded field on a canonical 1024-pixel grid that is resampled to the picture, so the preview shows the same grain pattern the export has.

The recipe (version 1; every key optional, absent = neutral):

    exposure     -2..2    EV, applied to linear light (+1 EV doubles the linear brightness)
    contrast     -100..100  around mid grey, in the picture's own (sRGB) tones; 100 doubles the slope
    saturation   -100..100  -100 = black and white
    temperature  -100..100  white balance in slider units; positive = warmer (more red, less blue); brightness-neutral
    tint         -100..100  positive = more magenta, negative = more green; brightness-neutral
    highlights   -100..100  negative recovers bright areas, positive brightens them
    shadows      -100..100  positive lifts dark areas, negative deepens them
    sharpness    0..100     unsharp mask on brightness only; the radius follows the picture size
    grain        0..1       fine luminance grain, strongest in the mid-tones, seeded by ``grain_seed`` (default 1)
    vignette     0..1       darkens the corners
    rotate       0|90|180|270  turns the picture clockwise
    flip         true       mirrors left and right (after the turn)
    crop         {x, y, w, h}  pixels of the picture AFTER turning and flipping
    straighten   -10..10    degrees clockwise about the centre; the largest same-shape rectangle without empty corners is kept
    output       {width, height}  the final size (Lanczos); same shape as the result within 1.5%

Order: turn -> flip -> crop -> straighten -> output size -> exposure -> white balance -> highlights/shadows -> contrast -> saturation -> sharpness -> vignette -> grain.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import imajutsu_pix as pix

RECIPE_VERSION = 1
RANGES = {"exposure": (-2.0, 2.0), "contrast": (-100.0, 100.0), "saturation": (-100.0, 100.0), "temperature": (-100.0, 100.0), "tint": (-100.0, 100.0),
          "highlights": (-100.0, 100.0), "shadows": (-100.0, 100.0), "sharpness": (0.0, 100.0), "grain": (0.0, 1.0), "vignette": (0.0, 1.0),
          "straighten": (-10.0, 10.0)}
TONAL = ("exposure", "contrast", "saturation", "temperature", "tint", "highlights", "shadows", "sharpness", "grain", "vignette")
GEOMETRY = ("rotate", "flip", "crop", "straighten", "output")
KEYS = set(RANGES) | {"rotate", "flip", "crop", "output", "grain_seed"}
PREVIEW_EDGE = 1024
MAX_PIXELS = 36_000_000
GRAIN_GRID = 1024


class RecipeError(ValueError):
    """A plain sentence for the person."""


def _num(v, name: str, lo: float, hi: float) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise RecipeError(f"{name} must be a number.")
    if not lo <= v <= hi:
        raise RecipeError(f"{name} must be between {lo:g} and {hi:g}.")
    return float(v)


def normalize_recipe(raw) -> dict:
    """Validate a recipe from the browser and return the canonical one: only non-neutral keys, numbers rounded. Raises RecipeError."""
    if raw in (None, {}):
        return {}
    if not isinstance(raw, dict):
        raise RecipeError("The adjustments must be an object.")
    unknown = sorted(set(raw) - KEYS - {"version"})
    if unknown:
        raise RecipeError("Unknown adjustment: " + ", ".join(str(u)[:30] for u in unknown) + ".")
    out: dict = {}
    for k, (lo, hi) in RANGES.items():
        if raw.get(k) is None:
            continue
        v = _num(raw[k], k, lo, hi)
        if abs(v) > 1e-9:
            out[k] = round(v, 3)
    if raw.get("rotate") not in (None, 0):
        if raw["rotate"] not in (90, 180, 270) or isinstance(raw["rotate"], bool):
            raise RecipeError("rotate must be 0, 90, 180 or 270 degrees.")
        out["rotate"] = int(raw["rotate"])
    if raw.get("flip"):
        out["flip"] = True
    if raw.get("crop") is not None:
        c = raw["crop"]
        if not isinstance(c, dict):
            raise RecipeError("crop needs x, y, w and h.")
        vals = {k: int(round(_num(c.get(k), f"crop.{k}", 0, 100000))) for k in ("x", "y", "w", "h")}
        if vals["w"] < 16 or vals["h"] < 16:
            raise RecipeError("The crop is too small (at least 16 × 16 pixels).")
        out["crop"] = vals
    if raw.get("output") is not None:
        o = raw["output"]
        if not isinstance(o, dict):
            raise RecipeError("output needs a width and a height.")
        out["output"] = {k: int(round(_num(o.get(k), f"output.{k}", 16, 8192))) for k in ("width", "height")}
    if raw.get("grain_seed") is not None and "grain" in out:
        out["grain_seed"] = int(_num(raw["grain_seed"], "grain_seed", 0, 2**31 - 1))
    return out


def is_neutral(recipe: dict) -> bool:
    return not recipe


def recipe_key(recipe: dict) -> str:
    return hashlib.sha256(json.dumps({"v": RECIPE_VERSION, **recipe}, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:24]


# ---- geometry: one ffmpeg filter chain, with every size tracked in integers ----------------------------------------------------------------------------
@dataclass
class Geometry:
    vf: str
    width: int
    height: int


def geometry(recipe: dict, w: int, h: int, *, max_edge: int | None = None) -> Geometry:
    f: list[str] = []
    rot = recipe.get("rotate", 0)
    if rot:
        f += {90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}[rot]
        if rot in (90, 270):
            w, h = h, w
    if recipe.get("flip"):
        f.append("hflip")
    c = recipe.get("crop")
    if c:
        if c["x"] + c["w"] > w or c["y"] + c["h"] > h:
            raise RecipeError(f"The crop reaches outside the {w} × {h} picture.")
        f.append(f"crop={int(c['w'])}:{int(c['h'])}:{int(c['x'])}:{int(c['y'])}")
        w, h = c["w"], c["h"]
    ang = float(recipe.get("straighten", 0.0))
    if abs(ang) > 1e-9:
        t = abs(math.radians(ang))
        s = min(1.0, w / (w * math.cos(t) + h * math.sin(t)), h / (w * math.sin(t) + h * math.cos(t)))
        nw, nh = max(16, int(w * s)), max(16, int(h * s))
        f.append(f"rotate={math.radians(ang):.6f}:ow=iw:oh=ih:c=black:bilinear=1")
        f.append(f"crop={nw}:{nh}:{(w - nw) // 2}:{(h - nh) // 2}")
        w, h = nw, nh
    o = recipe.get("output")
    if o and max_edge is None:
        if abs((o["width"] / o["height"]) / (w / h) - 1) > 0.015:
            raise RecipeError(f"The output size {o['width']} × {o['height']} has a different shape from the picture ({w} × {h}).")
        f.append(f"scale={int(o['width'])}:{int(o['height'])}:flags=lanczos")
        w, h = o["width"], o["height"]
    if max_edge is not None and max(w, h) > max_edge:
        k = max_edge / max(w, h)
        nw, nh = max(2, int(round(w * k))), max(2, int(round(h * k)))
        f.append(f"scale={nw}:{nh}:flags=area")
        w, h = nw, nh
    return Geometry(",".join(f), w, h)


# ---- tonal operations on float32 ----------------------------------------------------------------------------------------------------------------------
_LIN_LUT = np.array([(i / 255.0) / 12.92 if i / 255.0 <= 0.04045 else ((i / 255.0 + 0.055) / 1.055) ** 2.4 for i in range(256)], np.float32)


def srgb_to_linear_u8(a: np.ndarray) -> np.ndarray:
    return _LIN_LUT[a]


def linear_to_srgb(x: np.ndarray) -> np.ndarray:
    x = np.clip(x, 0.0, 1.0)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055).astype(np.float32)


def _smooth(lo: float, hi: float, x: np.ndarray) -> np.ndarray:
    t = np.clip((x - lo) / (hi - lo), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _luma(v: np.ndarray) -> np.ndarray:
    return v[..., 0] * 0.2126 + v[..., 1] * 0.7152 + v[..., 2] * 0.0722


def wb_gains(temperature: float, tint: float) -> np.ndarray:
    t, ti = temperature / 100.0, tint / 100.0
    stops = np.array([0.30 * t + 0.25 * ti, -0.50 * ti, -0.30 * t + 0.25 * ti], np.float32)
    g = np.power(2.0, stops)
    return (g / (0.2126 * g[0] + 0.7152 * g[1] + 0.0722 * g[2])).astype(np.float32)


def _gauss_luma(y: np.ndarray, sigma: float) -> np.ndarray:
    """Separable Gaussian blur of a 2-D float32 array (edges repeated)."""
    r = max(1, int(math.ceil(sigma * 3)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / sigma) ** 2).astype(np.float32)
    k /= k.sum()
    out = np.zeros_like(y)
    p = np.pad(y, ((0, 0), (r, r)), mode="edge")
    for i, wgt in enumerate(k):
        out += wgt * p[:, i:i + y.shape[1]]
    out2 = np.zeros_like(y)
    p = np.pad(out, ((r, r), (0, 0)), mode="edge")
    for i, wgt in enumerate(k):
        out2 += wgt * p[i:i + y.shape[0], :]
    return out2


def _resize_bilinear(field: np.ndarray, h: int, w: int) -> np.ndarray:
    gh, gw = field.shape
    ys = np.clip((np.arange(h, dtype=np.float32) + 0.5) * gh / h - 0.5, 0, gh - 1)
    xs = np.clip((np.arange(w, dtype=np.float32) + 0.5) * gw / w - 0.5, 0, gw - 1)
    y0, x0 = np.floor(ys).astype(np.int64), np.floor(xs).astype(np.int64)
    y1, x1 = np.minimum(y0 + 1, gh - 1), np.minimum(x0 + 1, gw - 1)
    fy, fx = (ys - y0)[:, None], (xs - x0)[None, :]
    top = field[y0][:, x0] * (1 - fx) + field[y0][:, x1] * fx
    bot = field[y1][:, x0] * (1 - fx) + field[y1][:, x1] * fx
    return (top * (1 - fy) + bot * fy).astype(np.float32)


def grain_field(h: int, w: int, seed: int) -> np.ndarray:
    """Unit-variance noise, identical in pattern at every size: generated on a canonical grid (long edge 1024) and resampled."""
    k = GRAIN_GRID / max(h, w)
    gh, gw = max(2, int(round(h * k))), max(2, int(round(w * k)))
    f = np.random.default_rng(int(seed)).standard_normal((gh, gw)).astype(np.float32)
    f = _resize_bilinear(f, h, w) if (gh, gw) != (h, w) else f
    return f / max(float(f.std()), 1e-6) if f.size > 1 else f


def apply_tonal(rgb: np.ndarray, r: dict) -> np.ndarray:
    """The tonal part of a recipe on an H x W x 3 uint8 picture -> uint8. A recipe without tonal keys returns the picture itself."""
    if not any(k in r for k in TONAL):
        return rgb
    h, w = rgb.shape[:2]
    if "exposure" in r or "temperature" in r or "tint" in r:
        lin = srgb_to_linear_u8(rgb)
        if "exposure" in r:
            lin = lin * np.float32(2.0 ** r["exposure"])
        if "temperature" in r or "tint" in r:
            lin = lin * wb_gains(r.get("temperature", 0.0), r.get("tint", 0.0))
        v = linear_to_srgb(lin)
        del lin
    else:
        v = rgb.astype(np.float32) / 255.0
    if "shadows" in r or "highlights" in r:
        y = _luma(v)
        if "shadows" in r:
            s = r["shadows"] / 100.0
            wgt = (1.0 - _smooth(0.0, 0.55, y))[..., None]
            v = v + s * 0.55 * wgt * (1.0 - v) if s > 0 else v * (1.0 + s * 0.6 * wgt)
            y = _luma(v)
        if "highlights" in r:
            hh = r["highlights"] / 100.0
            wgt = _smooth(0.45, 1.0, y)[..., None]
            v = v + hh * 0.5 * wgt * (1.0 - v) if hh > 0 else v * (1.0 + hh * 0.45 * wgt)
    if "contrast" in r:
        v = (v - 0.5) * np.float32(2.0 ** (r["contrast"] / 100.0)) + 0.5
    if "saturation" in r:
        y = _luma(np.clip(v, 0, 1))[..., None]
        v = y + (v - y) * np.float32(1.0 + r["saturation"] / 100.0)
    if "sharpness" in r:
        y = _luma(np.clip(v, 0, 1))
        sigma = max(0.5, max(h, w) / 1600.0)
        v = v + ((y - _gauss_luma(y, sigma)) * np.float32(r["sharpness"] / 100.0 * 1.5))[..., None]
    if "vignette" in r:
        yy = (np.arange(h, dtype=np.float32) - (h - 1) / 2) / max((h - 1) / 2, 1)
        xx = (np.arange(w, dtype=np.float32) - (w - 1) / 2) / max((w - 1) / 2, 1)
        rad = np.sqrt(yy[:, None] ** 2 + xx[None, :] ** 2) / math.sqrt(2)
        v = v * (1.0 - r["vignette"] * 0.75 * np.power(_smooth(0.3, 1.0, rad), 1.2))[..., None]
    if "grain" in r:
        y = np.clip(_luma(np.clip(v, 0, 1)), 0, 1)
        wgt = 0.35 + 0.65 * 4 * y * (1 - y)
        v = v + (grain_field(h, w, r.get("grain_seed", 1)) * wgt * np.float32(r["grain"] * 0.08))[..., None]
    return np.clip(v * 255.0 + 0.5, 0, 255).astype(np.uint8)


# ---- the one entry point ---------------------------------------------------------------------------------------------------------------------------------
@dataclass
class Rendered:
    data: bytes
    mime: str
    width: int
    height: int


def render(path: Path, mime: str, recipe: dict, *, orientation: int = 1, max_edge: int | None = None, fmt: str | None = None) -> Rendered:
    """Apply a (normalised) recipe to the stored picture at ``path``. ``max_edge`` set = the preview (a JPEG at most that big); otherwise the final file in the
    source's own format (JPEG stays JPEG, everything else becomes PNG) at the recipe's size. The same function, the same tonal code."""
    demuxer = pix.demuxer_of(mime)
    stored = pix.probe_size(path, demuxer)
    w, h = pix.displayed(stored, orientation)
    if w * h > MAX_PIXELS:
        raise RecipeError(f"The picture is larger than {MAX_PIXELS // 1_000_000} megapixels, which is too large for adjustments.")
    g = geometry(recipe, w, h, max_edge=max_edge)
    rgb = pix.flatten(pix.decode_rgba(path, demuxer, orientation, g.vf, (g.width, g.height)))
    out = apply_tonal(rgb, recipe)
    fmt = fmt or ("jpg" if max_edge is not None or mime == "image/jpeg" else "png")
    return Rendered(pix.encode(out, "jpg" if fmt == "jpg" else "png", 3 if max_edge is not None else 2), "image/jpeg" if fmt == "jpg" else "image/png", g.width, g.height)
