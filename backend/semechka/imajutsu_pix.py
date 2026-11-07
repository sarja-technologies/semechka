"""Imajutsu: pixel work with ffmpeg and numpy only (no new dependency; Pillow is not a Semechka dependency).

Everything here is synchronous and pure: bytes/arrays in, bytes/arrays out. Callers run it in a thread. ffmpeg receives numbers that were checked by the
caller and forced through int()/float() here; file names are scratch paths made by Semechka, never the person's text.

Conventions: an image array is H x W x 3 uint8 (RGB); a mask is H x W uint8 where 255 = the area to change (white = selected, black = keep).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np

from . import media_lib

MAX_PIXELS = 48_000_000          # a picture larger than this is refused for pixel work
_NOAUTO = ["-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file,pipe", "-noautorotate"]


class PixError(Exception):
    """A plain sentence for the person."""


def _ffmpeg() -> tuple[str, str]:
    try:
        return media_lib.need_ffmpeg()
    except media_lib.MediaError as e:
        raise PixError(e.message) from e


def run_ff(args: list[str], data: bytes | None = None, timeout: float = 180) -> bytes:
    ff, _ = _ffmpeg()
    try:
        p = subprocess.run([ff, *args], input=data, capture_output=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as e:
        raise PixError("The picture took too long to process.") from e
    if p.returncode != 0:
        raise PixError("The picture could not be processed (the file may be damaged or use an unsupported format).")
    return p.stdout


def orientation_of(data: bytes, mime: str) -> int:
    return media_lib.exif_orientation(data[:65536]) if mime == "image/jpeg" else 1


def demuxer_of(mime: str) -> str:
    return media_lib.default_demuxer(mime)


def probe_size(path: Path, demuxer: str) -> tuple[int, int]:
    """Stored width and height (before any EXIF turn)."""
    _, fp = _ffmpeg()
    try:
        p = subprocess.run([fp, "-v", "error", "-protocol_whitelist", "file", "-f", demuxer, "-print_format", "json", "-show_streams", "-i", f"file:{path}"],
                           capture_output=True, timeout=40, check=False)
        d = json.loads(p.stdout or b"{}")
        v = next(s for s in d.get("streams", []) if s.get("codec_type") == "video")
        return int(v["width"]), int(v["height"])
    except (StopIteration, KeyError, ValueError, subprocess.SubprocessError) as e:
        raise PixError("The picture size could not be read.") from e


def displayed(size: tuple[int, int], orientation: int) -> tuple[int, int]:
    w, h = size
    return (h, w) if orientation in (5, 6, 7, 8) else (w, h)


def decode_rgba(path: Path, demuxer: str, orientation: int = 1, vf: str = "", dims: tuple[int, int] | None = None) -> np.ndarray:
    """The first picture of a file as H x W x 4 uint8, EXIF orientation applied, then ``vf`` (filters whose output size ``dims`` states)."""
    stored = probe_size(path, demuxer)
    w, h = dims or displayed(stored, orientation)
    if w * h > MAX_PIXELS:
        raise PixError(f"The picture is larger than {MAX_PIXELS // 1_000_000} megapixels.")
    chain = [x for x in (media_lib._EXIF_FILTER.get(orientation, ""), vf, "format=rgba") if x]
    raw = run_ff([*_NOAUTO, "-f", demuxer, "-i", f"file:{path}", "-vf", ",".join(chain), "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"])
    if len(raw) != w * h * 4:
        raise PixError("The picture could not be decoded at the expected size.")
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4)


def flatten(rgba: np.ndarray, bg: int = 255) -> np.ndarray:
    """RGB from RGBA, transparent parts laid over ``bg`` (white by default). Opaque pictures come back untouched."""
    a = rgba[..., 3]
    if a.min() == 255:
        return np.ascontiguousarray(rgba[..., :3])
    af = a.astype(np.float32)[..., None] / 255.0
    return np.clip(rgba[..., :3].astype(np.float32) * af + bg * (1 - af) + 0.5, 0, 255).astype(np.uint8)


def luminance_mask(rgba: np.ndarray) -> np.ndarray:
    """A mask picture as H x W uint8: brightness weighted by opacity (so both 'white strokes on black' and 'white strokes on transparent' work)."""
    rgb = rgba[..., :3].astype(np.float32)
    lum = rgb[..., 0] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.114
    return np.clip(lum * (rgba[..., 3].astype(np.float32) / 255.0) + 0.5, 0, 255).astype(np.uint8)


def encode(arr: np.ndarray, fmt: str = "png", quality: int = 2) -> bytes:
    """PNG, or JPEG (``quality`` is ffmpeg's -q:v: 2 = about 95%)."""
    h, w = arr.shape[:2]
    codec = ["-c:v", "png", "-compression_level", "6"] if fmt == "png" else ["-c:v", "mjpeg", "-q:v", str(int(quality)), "-pix_fmt", "yuvj420p"]
    pix = "rgb24" if arr.ndim == 3 else "gray"
    return run_ff(["-nostdin", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{int(w)}x{int(h)}", "-i", "pipe:0", "-frames:v", "1",
                   "-map_metadata", "-1", *codec, "-f", "image2pipe", "pipe:1"], np.ascontiguousarray(arr).tobytes())


def filter_raw(arr: np.ndarray, vf: str, out_w: int, out_h: int) -> np.ndarray:
    """Run an ffmpeg filter chain over a raw array (RGB or single-channel) and read it back at the stated output size."""
    h, w = arr.shape[:2]
    pix = "rgb24" if arr.ndim == 3 else "gray"
    raw = run_ff(["-nostdin", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", pix, "-s", f"{int(w)}x{int(h)}", "-i", "pipe:0", "-vf", vf,
                  "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", pix, "pipe:1"], np.ascontiguousarray(arr).tobytes())
    shape = (int(out_h), int(out_w), 3) if arr.ndim == 3 else (int(out_h), int(out_w))
    if len(raw) != int(np.prod(shape)):
        raise PixError("The picture could not be resized.")
    return np.frombuffer(raw, np.uint8).reshape(shape)


def scale(arr: np.ndarray, w: int, h: int, flags: str = "lanczos") -> np.ndarray:
    if arr.shape[1] == w and arr.shape[0] == h:
        return arr
    return filter_raw(arr, f"scale={int(w)}:{int(h)}:flags={flags}", w, h)


def cover(arr: np.ndarray, w: int, h: int, flags: str = "lanczos") -> np.ndarray:
    """Fill w x h exactly: scale until both sides are covered, then cut the centre. No distortion."""
    if arr.shape[1] == w and arr.shape[0] == h:
        return arr
    return filter_raw(arr, f"scale={int(w)}:{int(h)}:force_original_aspect_ratio=increase:flags={flags},crop={int(w)}:{int(h)}", w, h)


def blur_mask(mask: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return mask
    return filter_raw(mask, f"gblur=sigma={float(sigma):.3f}:steps=2", mask.shape[1], mask.shape[0])


def box_mask(w: int, h: int, box: dict) -> np.ndarray:
    """A white rectangle on black. ``box`` has x, y, w, h as fractions (0..1) of the picture."""
    m = np.zeros((h, w), np.uint8)
    x0, y0 = int(round(box["x"] * w)), int(round(box["y"] * h))
    x1, y1 = int(round((box["x"] + box["w"]) * w)), int(round((box["y"] + box["h"]) * h))
    m[max(0, y0):min(h, max(y1, y0 + 1)), max(0, x0):min(w, max(x1, x0 + 1))] = 255
    return m


def pad_canvas(rgb: np.ndarray, left: int, top: int, right: int, bottom: int, fill: int = 127) -> tuple[np.ndarray, np.ndarray]:
    """The source on a larger canvas (neutral grey) and the mask of the NEW area (255 outside the source, 0 on it)."""
    h, w = rgb.shape[:2]
    H, W = h + top + bottom, w + left + right
    canvas = np.full((H, W, 3), fill, np.uint8)
    canvas[top:top + h, left:left + w] = rgb
    mask = np.full((H, W), 255, np.uint8)
    mask[top:top + h, left:left + w] = 0
    return canvas, mask


def lanczos_unsharp(src: Path, demuxer: str, orientation: int, w: int, h: int, fmt: str) -> bytes:
    """The Upscale mode: Lanczos resize and a light unsharp mask, straight from the stored file. Not a model: no detail is invented."""
    chain = ",".join(x for x in (media_lib._EXIF_FILTER.get(orientation, ""), f"scale={int(w)}:{int(h)}:flags=lanczos",
                                 "unsharp=luma_msize_x=5:luma_msize_y=5:luma_amount=0.35:chroma_msize_x=3:chroma_msize_y=3:chroma_amount=0.0") if x)
    codec = ["-c:v", "png", "-compression_level", "6"] if fmt == "png" else ["-c:v", "mjpeg", "-q:v", "2", "-pix_fmt", "yuvj420p"]
    return run_ff([*_NOAUTO, "-f", demuxer, "-i", f"file:{src}", "-vf", chain, "-frames:v", "1", "-map_metadata", "-1", *codec, "-f", "image2pipe", "pipe:1"])


def masked_merge(base: np.ndarray, over: np.ndarray, mask: np.ndarray, feather: float = 0.0) -> np.ndarray:
    """ffmpeg's maskedmerge: ``over`` where the mask is white, ``base`` where it is black, a smooth blend across a feathered edge. All three the same size."""
    h, w = base.shape[:2]
    if over.shape[:2] != (h, w) or mask.shape[:2] != (h, w):
        raise PixError("The pictures to blend differ in size.")
    blur = f",gblur=sigma={float(feather):.3f}:steps=2" if feather > 0 else ""
    fc = f"[2:v]format=gray{blur}[m];[0:v]format=gbrp[b];[1:v]format=gbrp[o];[b][o][m]maskedmerge,format=rgb24[out]"
    args = ["-nostdin", "-hide_banner", "-loglevel", "error"]
    # three raw inputs via temporary files would need a scratch dir; named pipes are not portable, so the inputs are PNG files made next to each other
    import tempfile
    with tempfile.TemporaryDirectory(prefix="imajutsu-") as d:
        paths = []
        for i, a in enumerate((base, over, mask)):
            p = Path(d) / f"in{i}.png"
            p.write_bytes(encode(a, "png"))
            paths.append(p)
        raw = run_ff([*args, "-i", str(paths[0]), "-i", str(paths[1]), "-i", str(paths[2]), "-filter_complex", fc, "-map", "[out]", "-frames:v", "1",
                      "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"])
    if len(raw) != h * w * 3:
        raise PixError("The blend could not be made.")
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)
