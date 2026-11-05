"""The media library beyond generated results: files people upload, edited copies, previews and the ffmpeg work behind them.

Everything here is a ``MediaAsset`` (the Library's own table); no schema change. Uploaded and edited items are told apart from generated ones by
the metadata kept (encrypted) in ``params_enc``: ``origin`` (upload | edit), ``name``, ``parent``, ``edit`` and ``media`` (codec, frame rate ...).

* Uploads are streamed to a private scratch file, identified from their bytes (never the name or the browser's claim), probed, given a preview
  picture, then sealed in the chunked format of ``media_store`` so large videos are never held in memory, in the database or in one ciphertext.
* Editing (trim, crop, rotate) is real ffmpeg work on a decrypted scratch copy; the original is never touched and the result is a new asset.
* A scratch copy is plaintext on disk for the seconds ffmpeg needs it (in RAM-backed /dev/shm for no-save items), mode 0600 in a 0700 directory,
  removed in a ``finally``; leftovers are swept.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from . import media_store
from .media_models import MediaAsset

log = logging.getLogger("semechka.media_lib")

IMAGE_EXT = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
VIDEO_EXT = {"video/mp4": "mp4", "video/quicktime": "mov", "video/webm": "webm", "video/x-matroska": "mkv", "video/x-msvideo": "avi"}
SERVE_MIME = {**IMAGE_EXT, **VIDEO_EXT}
EPHEMERAL_UPLOAD_MAX = 256 * 1024 * 1024
ORIGINS = ("upload", "edit")
META_KEYS = ("origin", "name", "parent", "edit", "media")
BROWSER_VIDEO = {"h264", "vp8", "vp9", "av1"}
BROWSER_AUDIO = {None, "aac", "mp3", "opus", "vorbis", "flac"}
MAX_STRIP = 32


class MediaError(Exception):
    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


# ---- external programs ---------------------------------------------------------------------------------
def ffbin(name: str) -> str | None:
    return os.environ.get(f"SEMECHKA_{name.upper()}") or shutil.which(name)


def need_ffmpeg() -> tuple[str, str]:
    ff, fp = ffbin("ffmpeg"), ffbin("ffprobe")
    if not ff or not fp:
        raise MediaError(503, "no_ffmpeg", "Video and picture processing (ffmpeg) is not installed on this server, so this file cannot be prepared.")
    return ff, fp


def _sem(st) -> asyncio.Semaphore:
    s = st.__dict__.get("media_ffmpeg_sem")
    if s is None:
        s = st.__dict__["media_ffmpeg_sem"] = asyncio.Semaphore(2)
    return s


async def run(cmd: list[str], *, timeout: float, on_out=None, cancel: asyncio.Event | None = None, holder: list | None = None) -> tuple[int, str]:
    """Run a program without a shell. Returns (code, last stderr). ``on_out`` receives each stdout line (ffmpeg's -progress). Killed on timeout or cancel."""
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    if holder is not None:
        holder.append(proc)
    err = bytearray()

    async def pump_err():
        while True:
            b = await proc.stderr.read(4096)
            if not b:
                return
            err.extend(b)
            del err[:-4000]

    async def pump_out():
        while True:
            line = await proc.stdout.readline()
            if not line:
                return
            if on_out:
                on_out(line.decode("utf-8", "replace").strip())

    tasks = [asyncio.create_task(pump_err()), asyncio.create_task(pump_out())]
    waiter = asyncio.create_task(proc.wait())
    watchers = [waiter] + ([asyncio.create_task(cancel.wait())] if cancel else [])
    try:
        done, _ = await asyncio.wait(watchers, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            if cancel and cancel.is_set():
                return -9, "cancelled"
            return -9, "timed out"
        await asyncio.gather(*tasks, return_exceptions=True)
        return proc.returncode or 0, err.decode("utf-8", "replace").strip()
    finally:
        for t in tasks + watchers:
            t.cancel()
        with contextlib.suppress(ProcessLookupError):
            if proc.returncode is None:
                proc.kill()


# ---- scratch space -------------------------------------------------------------------------------------
def scratch_root(st, ephemeral: bool) -> Path:
    shm = Path("/dev/shm")
    root = (shm / "semechka-scratch") if (ephemeral and shm.is_dir() and os.access(shm, os.W_OK)) else st.cfg.data_dir / "tmp" / "media"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


@contextlib.contextmanager
def scratch(st, ephemeral: bool = False) -> Iterator[Path]:
    d = Path(tempfile.mkdtemp(prefix="w-", dir=scratch_root(st, ephemeral)))
    os.chmod(d, 0o700)
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def sweep_scratch(st) -> int:
    n = 0
    for ephemeral in (False, True):
        root = scratch_root(st, ephemeral)
        for d in root.iterdir():
            with contextlib.suppress(OSError):
                if time.time() - d.stat().st_mtime > 2 * 3600:
                    shutil.rmtree(d, ignore_errors=True) if d.is_dir() else d.unlink(missing_ok=True)
                    n += 1
    return n


# ---- identifying files ---------------------------------------------------------------------------------
@dataclass
class Sniffed:
    kind: str          # image | video
    mime: str
    demuxer: str       # forced on ffmpeg so a crafted file cannot make it guess a playlist or script format


def sniff(head: bytes) -> Sniffed:
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return Sniffed("image", "image/png", "png_pipe")
    if head.startswith(b"\xff\xd8\xff"):
        return Sniffed("image", "image/jpeg", "jpeg_pipe")
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return Sniffed("image", "image/webp", "webp_pipe")
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return Sniffed("image", "image/gif", "gif")
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"avif", b"avis"):
            raise MediaError(415, "unsupported_type", "HEIC and AVIF pictures are not supported. Export the picture as JPEG or PNG.")
        return Sniffed("video", "video/quicktime" if brand == b"qt  " else "video/mp4", "mov,mp4,m4a,3gp,3g2,mj2")
    if head[4:8] in (b"moov", b"mdat", b"free", b"wide", b"skip"):
        return Sniffed("video", "video/quicktime", "mov,mp4,m4a,3gp,3g2,mj2")
    if head[:4] == b"\x1aE\xdf\xa3":
        return Sniffed("video", "video/webm" if b"webm" in head[:64] else "video/x-matroska", "matroska,webm")
    if head[:4] == b"RIFF" and head[8:12] == b"AVI ":
        return Sniffed("video", "video/x-msvideo", "avi")
    raise MediaError(415, "unsupported_type", "This file type is not supported. Use PNG, JPEG, WebP or GIF pictures, or MP4, MOV, WebM, MKV or AVI videos.")


def default_demuxer(mime: str) -> str:
    return {"image/png": "png_pipe", "image/jpeg": "jpeg_pipe", "image/webp": "webp_pipe", "image/gif": "gif", "video/webm": "matroska,webm",
            "video/x-matroska": "matroska,webm", "video/x-msvideo": "avi"}.get(mime, "mov,mp4,m4a,3gp,3g2,mj2")


def exif_orientation(head: bytes) -> int:
    """Orientation (1..8) from a JPEG's EXIF block, 1 when absent. Read from the header; nothing is decoded."""
    try:
        i = 2
        while i + 4 < len(head):
            if head[i] != 0xFF:
                return 1
            marker, ln = head[i + 1], int.from_bytes(head[i + 2:i + 4], "big")
            if marker == 0xE1 and head[i + 4:i + 10] == b"Exif\x00\x00":
                t = i + 10
                le = head[t:t + 2] == b"II"
                rd = lambda o, n, t=t, le=le: int.from_bytes(head[t + o:t + o + n], "little" if le else "big")
                ifd = rd(4, 4)
                for k in range(rd(ifd, 2)):
                    e = ifd + 2 + k * 12
                    if rd(e, 2) == 0x0112:
                        v = rd(e + 8, 2)
                        return v if 1 <= v <= 8 else 1
                return 1
            if marker == 0xDA:
                return 1
            i += 2 + ln
    except (IndexError, ValueError):
        pass
    return 1


# transpose chain that undoes each EXIF orientation (after -noautorotate)
_EXIF_FILTER = {1: "", 2: "hflip", 3: "hflip,vflip", 4: "vflip", 5: "transpose=0", 6: "transpose=1", 7: "transpose=3", 8: "transpose=2"}


@dataclass
class Probe:
    kind: str
    mime: str
    demuxer: str
    width: int
    height: int
    duration: float | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    orientation: int = 1
    playable: bool = True

    def media(self) -> dict:
        return {"codec": self.vcodec, "audio_codec": self.acodec, "audio": bool(self.acodec), "fps": self.fps, "demuxer": self.demuxer,
                "orientation": self.orientation, "playable": self.playable}


def _fps(s: str | None) -> float | None:
    try:
        a, _, b = (s or "").partition("/")
        v = float(a) / float(b or 1)
        return round(v, 3) if v > 0 else None
    except (ValueError, ZeroDivisionError):
        return None


async def probe(path: Path, sn: Sniffed, head: bytes) -> Probe:
    _, fp = need_ffmpeg()
    code, out = await _capture([fp, "-v", "error", "-protocol_whitelist", "file", "-f", sn.demuxer, "-print_format", "json", "-show_format", "-show_streams",
                                "-i", f"file:{path}"], 40)
    if code != 0:
        raise MediaError(422, "unreadable", "This file could not be read. It may be damaged or use a format that cannot be opened.")
    try:
        d = json.loads(out)
    except ValueError:
        raise MediaError(422, "unreadable", "This file could not be read.") from None
    streams = d.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None) \
        or next((s for s in streams if s.get("codec_type") == "video"), None)
    if v is None:
        raise MediaError(422, "no_video", "This file has no picture in it." if sn.kind == "image" else "This file has no video track (it may be audio only).")
    w, h = int(v.get("width") or 0), int(v.get("height") or 0)
    if w < 2 or h < 2:
        raise MediaError(422, "unreadable", "The picture size could not be determined.")
    if w * h > 120_000_000:
        raise MediaError(422, "too_big", "The picture is larger than 120 megapixels.")
    rot = 0
    for sd in v.get("side_data_list") or []:
        if "rotation" in sd:
            rot = round(float(sd["rotation"]))
    if not rot and (v.get("tags") or {}).get("rotate"):
        with contextlib.suppress(ValueError):
            rot = -int(v["tags"]["rotate"])
    orient = exif_orientation(head) if sn.mime == "image/jpeg" else 1
    if rot % 180 != 0 or orient in (5, 6, 7, 8):
        w, h = h, w  # the size people see
    acodec = next((s.get("codec_name") for s in streams if s.get("codec_type") == "audio"), None)
    dur = None
    for src in (v.get("duration"), (d.get("format") or {}).get("duration")):
        with contextlib.suppress(TypeError, ValueError):
            dur = float(src)
            break
    if sn.kind == "video":
        if not dur or dur <= 0:
            raise MediaError(422, "unreadable", "The length of this video could not be determined.")
        if dur > 6 * 3600:
            raise MediaError(422, "too_long", "Videos longer than six hours are not supported.")
    else:
        dur = None
    vc = v.get("codec_name")
    playable = sn.kind == "image" or (sn.mime in ("video/mp4", "video/quicktime", "video/webm") and vc in BROWSER_VIDEO and acodec in BROWSER_AUDIO)
    if sn.kind == "video" and vc is None:
        raise MediaError(422, "unsupported_codec", "The video uses a codec this server cannot read.")
    return Probe(sn.kind, sn.mime, sn.demuxer, w, h, round(dur, 3) if dur else None, _fps(v.get("avg_frame_rate")) or _fps(v.get("r_frame_rate")),
                 vc, acodec, orient, playable)


async def _capture(cmd: list[str], timeout: float) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*cmd, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return -9, ""
    return proc.returncode or 0, out.decode("utf-8", "replace")


_IN = ["-nostdin", "-hide_banner", "-loglevel", "error", "-protocol_whitelist", "file"]


def _inp(p: Probe, path: Path, *pre: str) -> list[str]:
    extra = ["-noautorotate"] if p.kind == "image" else []
    return [*_IN, *extra, *pre, "-f", p.demuxer, "-i", f"file:{path}"]


async def make_thumb(st, p: Probe, src: Path, dest: Path, size: int = 480) -> bool:
    """A small JPEG of an image or of a frame near the start of a video. False when it could not be made (the item is still usable)."""
    ff = ffbin("ffmpeg")
    if not ff:
        return False
    fit = f"scale='if(gt(iw,ih),min({size},iw),-2)':'if(gt(iw,ih),-2,min({size},ih))':flags=lanczos"
    if p.kind == "image":
        e = _EXIF_FILTER.get(p.orientation, "")
        vf = (e + "," if e else "") + fit
        seek: list[str] = []
    else:
        vf = fit
        seek = ["-ss", f"{min(1.0, (p.duration or 1) * 0.1):.3f}"]
    async with _sem(st):
        code, _ = await run([ff, *_inp(p, src, *seek), "-frames:v", "1", "-vf", vf, "-q:v", "4", "-an", "-map_metadata", "-1", "-y", str(dest)], timeout=60)
    if (code != 0 or not dest.exists() or dest.stat().st_size == 0) and p.kind == "video" and seek:
        async with _sem(st):  # a very short clip may have nothing at that offset
            code, _ = await run([ff, *_inp(p, src), "-frames:v", "1", "-vf", vf, "-q:v", "4", "-an", "-y", str(dest)], timeout=60)
    return code == 0 and dest.exists() and dest.stat().st_size > 0


async def make_strip(st, p: Probe, src: Path, dest: Path, n: int, height: int) -> bool:
    """``n`` frames across the clip, side by side in one JPEG (the trim timeline's filmstrip). Each frame is found by its own seek, so the cost does
    not grow with the length of the video (a plain 'fps' filter would decode the whole file)."""
    ff = ffbin("ffmpeg")
    if not ff or not p.duration:
        return False
    fw = max(2, round(height * p.width / p.height / 2) * 2)
    step = p.duration / n
    cmd = [ff, *_IN]
    for i in range(n):
        cmd += ["-ss", f"{max(0.0, min(step * (i + 0.5), p.duration - 0.1)):.3f}", "-f", p.demuxer, "-i", f"file:{src}"]
    chain = ";".join(f"[{i}:v]scale={fw}:{height}:flags=fast_bilinear,setsar=1[v{i}]" for i in range(n))
    fc = f"{chain};{''.join(f'[v{i}]' for i in range(n))}hstack=inputs={n}"
    async with _sem(st):
        code, _ = await run([*cmd, "-filter_complex", fc, "-frames:v", "1", "-q:v", "5", "-an", "-y", str(dest)], timeout=180)
    return code == 0 and dest.exists() and dest.stat().st_size > 0


# ---- storage ---------------------------------------------------------------------------------------------
def asset_file(st, user_id: str, aid: str, suffix: str = "") -> Path:
    from . import media_svc
    base = media_svc.asset_path(st.cfg, user_id, aid)
    return base if not suffix else base.with_name(f"{aid}.{suffix}.bin")


def _akey(st) -> bytes:
    return st.vault.key_for("asset")


def read_sidecar(st, a: MediaAsset, suffix: str) -> bytes | None:
    p = asset_file(st, a.user_id, a.id, suffix)
    try:
        if not p.exists():
            return None
        return media_store.read_all(_akey(st), "asset", f"{a.id}.{suffix}", p, _sealed_len(p))
    except (OSError, ValueError):
        return None


def _sealed_len(p: Path) -> int:
    """Plaintext length of a chunked file from its size on disk."""
    body = p.stat().st_size - media_store.HEAD
    return body - -(-body // (media_store.CHUNK + media_store.TAG)) * media_store.TAG


def write_sidecar(st, user_id: str, aid: str, suffix: str, data: bytes) -> None:
    media_store.seal_bytes(_akey(st), "asset", f"{aid}.{suffix}", data, asset_file(st, user_id, aid, suffix))


def meta_of(st, a: MediaAsset) -> dict:
    try:
        d = json.loads(st.vault.decrypt(a.params_enc, "media")) if a.params_enc else {}
    except (ValueError, TypeError):
        return {}
    return d if isinstance(d, dict) else {}


def origin_of(st, a: MediaAsset) -> str:
    return meta_of(st, a).get("origin") or "generated"


def is_chunked(st, a: MediaAsset) -> bool:
    try:
        return media_store.kind_of(asset_file(st, a.user_id, a.id)) == "chunked"
    except (OSError, ValueError):
        return False


def display_name(meta: dict, kind: str, aid: str) -> str:
    return meta.get("name") or f"{'Picture' if kind == 'image' else 'Video'} {aid[-6:]}"


def clean_name(name: str | None, kind: str) -> str:
    n = os.path.basename((name or "").replace("\\", "/")).strip()
    n = re.sub(r"[\x00-\x1f\x7f]", "", n)[:120]
    return n or ("picture" if kind == "image" else "video")


def new_asset_id() -> str:
    return "ma_" + uuid.uuid4().hex[:24]


async def seal_to_store(st, user_id: str, aid: str, src: Path) -> int:
    return await asyncio.to_thread(media_store.seal_file, _akey(st), "asset", aid, src, asset_file(st, user_id, aid))


def remove_files(st, user_id: str, aid: str) -> None:
    d = asset_file(st, user_id, aid).parent
    if d.is_dir():
        for f in d.glob(f"{aid}.*"):
            f.unlink(missing_ok=True)
        # (the main file is "<id>.bin" and sidecars "<id>.<name>.bin": the glob covers both)


# ---- ingest --------------------------------------------------------------------------------------------
def _mem(st) -> dict:
    from . import media_svc
    return media_svc._mem(st)


def active_uploads(st) -> dict:
    return st.__dict__.setdefault("media_uploads_active", {})


async def ingest(st, *, user_id: str, name: str, body: AsyncIterator[bytes], declared: int | None, cap: int, ephemeral: bool,
                 expires_at: float | None, origin: str = "upload", extra_meta: dict | None = None, min_free: int = 64 * 1024 * 1024) -> dict:
    """Receive one file as a stream, check it, store it. Raises MediaError; leaves nothing behind on any failure."""
    have_ffmpeg = ffbin("ffmpeg") is not None
    if ephemeral:
        cap = min(cap, EPHEMERAL_UPLOAD_MAX)
    if declared is not None and declared > cap:
        raise MediaError(413, "too_large", f"This file is larger than the {cap // 2**20} MB this server accepts.")
    if declared == 0:
        raise MediaError(422, "empty", "The file is empty.")
    if active_uploads(st).get(user_id, 0) >= 3:
        raise MediaError(429, "busy", "Three uploads are already in progress. Wait for one to finish.")
    active_uploads(st)[user_id] = active_uploads(st).get(user_id, 0) + 1
    try:
        with scratch(st, ephemeral) as tmp:
            raw = tmp / "upload"
            try:
                free = shutil.disk_usage(tmp).free
            except OSError:
                free = cap + min_free
            if free < (declared or 0) + min_free:
                raise MediaError(507, "no_space", "The server does not have room for this file right now.")
            size = 0
            head = b""
            fd = os.open(raw, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                async for chunk in body:
                    if not chunk:
                        continue
                    if len(head) < 65536:
                        head += chunk[:65536 - len(head)]
                    size += len(chunk)
                    if size > cap:
                        raise MediaError(413, "too_large", f"This file is larger than the {cap // 2**20} MB this server accepts.")
                    await asyncio.to_thread(f.write, chunk)
            if size == 0:
                raise MediaError(422, "empty", "The file is empty.")
            sn = sniff(head)
            if sn.kind == "video" or have_ffmpeg:
                pr = await probe(raw, sn, head)
            else:  # pictures stay usable on a server without ffmpeg: size from the header
                from .attachments_svc import image_size
                w, h = image_size(head, sn.mime)
                if not w:
                    raise MediaError(422, "unreadable", "The picture size could not be determined.")
                pr = Probe("image", sn.mime, sn.demuxer, int(w), int(h), orientation=exif_orientation(head) if sn.mime == "image/jpeg" else 1)
            thumb = tmp / "thumb.jpg"
            has_thumb = await make_thumb(st, pr, raw, thumb)
            return await _store(st, user_id=user_id, name=clean_name(name, pr.kind), pr=pr, src=raw, size=size, thumb=thumb if has_thumb else None,
                                ephemeral=ephemeral, expires_at=expires_at, origin=origin, extra=extra_meta or {})
    except MediaError:
        raise
    except OSError as e:
        log.warning("upload failed: %s", e)
        raise MediaError(500, "io", "The file could not be stored.") from e
    finally:
        active_uploads(st)[user_id] = max(0, active_uploads(st).get(user_id, 1) - 1)


async def _store(st, *, user_id: str, name: str, pr: Probe, src: Path, size: int, thumb: Path | None, ephemeral: bool, expires_at: float | None,
                 origin: str, extra: dict) -> dict:
    from . import media_svc
    meta = {"origin": origin, "name": name, "media": pr.media(), **extra}
    now = time.time()
    if ephemeral:
        media_svc.purge_mem(st)
        if media_svc.mem_bytes(st) + size > media_svc.EPHEMERAL_CAP_BYTES:
            raise MediaError(503, "busy", "Temporary storage for no-save files is full. Try again shortly.")
        aid = "me_" + uuid.uuid4().hex[:24]
        data = await asyncio.to_thread(src.read_bytes)
        _mem(st)[aid] = {"user_id": user_id, "job_id": None, "data": data, "mime": pr.mime, "expires": now + media_svc.EPHEMERAL_TTL,
                         "thumb": thumb.read_bytes() if thumb else None, "name": name,
                         "meta": {"kind": pr.kind, "width": pr.width, "height": pr.height, "duration": pr.duration, "seed": None, "created_at": now,
                                  "origin": origin, "name": name, "media": pr.media(), "parent": extra.get("parent"), "edit": extra.get("edit")}}
        return mem_out(st, aid)
    aid = new_asset_id()
    try:
        await seal_to_store(st, user_id, aid, src)
        if thumb:
            await asyncio.to_thread(write_sidecar, st, user_id, aid, "thumb", thumb.read_bytes())
        with st.db.session(write=True) as s:
            s.add(MediaAsset(id=aid, user_id=user_id, job_id=None, kind=pr.kind, model_id="edit" if origin == "edit" else "upload", mime=pr.mime,
                             size=size, width=pr.width, height=pr.height, duration=pr.duration, seed=None, prompt_enc="",
                             params_enc=st.vault.encrypt(json.dumps(meta), "media"), expires_at=expires_at))
            s.flush()
            if not pr.playable and pr.kind == "video":
                start_proxy(st, user_id, aid, pr)  # begins after this block commits (nothing here awaits); the result says "pending"
            a = s.get(MediaAsset, aid)
            out = asset_json(st, a)
    except BaseException:
        remove_files(st, user_id, aid)
        raise
    return out


def mem_out(st, aid: str) -> dict:
    v = _mem(st)[aid]
    m = v["meta"]
    return {"id": aid, "kind": m["kind"], "mime": v["mime"], "size": len(v["data"]), "width": m["width"], "height": m["height"], "duration": m["duration"],
            "seed": None, "created_at": m["created_at"], "expires_at": v["expires"], "prompt": "", "params": {}, "saved": False,
            "model_id": "edit" if m.get("origin") == "edit" else "upload", "origin": m.get("origin") or "upload", "name": m.get("name"),
            "parent": m.get("parent"), "edit": m.get("edit"), "media": m.get("media"), "has_thumb": bool(v.get("thumb")),
            "preview": preview_state(st, aid, m.get("media"), True)}


def preview_state(st, aid: str, media: dict | None, mem: bool = False) -> str:
    """original (the browser can play the file), proxy (a converted copy is ready), pending (being converted) or none."""
    if not media or media.get("playable", True):
        return "original"
    if mem:
        return "none"
    if aid in st.__dict__.get("media_proxy_pending", {}):
        return "pending"
    return "proxy" if any(True for _ in _proxy_paths(st, aid)) else "none"


def _proxy_paths(st, aid: str):
    for udir in (st.cfg.data_dir / "assets").glob("*"):
        p = udir / f"{aid}.proxy.bin"
        if p.exists():
            yield p


def asset_json(st, a: MediaAsset) -> dict:
    """The shape every client sees for any asset (generated, uploaded or edited)."""
    from . import media_svc
    base = media_svc.asset_out(st, a)
    meta = meta_of(st, a)
    if meta.get("origin") in ORIGINS:
        base["params"] = {}
        base["origin"] = meta["origin"]
        base["name"] = display_name(meta, a.kind, a.id)
        base["parent"] = meta.get("parent")
        base["edit"] = meta.get("edit")
        base["media"] = meta.get("media")
        base["has_thumb"] = asset_file(st, a.user_id, a.id, "thumb").exists()
        base["preview"] = preview_state(st, a.id, meta.get("media"))
    else:
        base["origin"] = "generated"
        base["has_thumb"] = False
        base["preview"] = "original"
    return base


# ---- the preview copy of a video the browser cannot play ----------------------------------------------
def start_proxy(st, user_id: str, aid: str, pr: Probe) -> None:
    pend = st.__dict__.setdefault("media_proxy_pending", {})
    if aid in pend:
        return
    pend[aid] = time.time()

    async def go():
        try:
            ff, _ = need_ffmpeg()
            with scratch(st) as tmp:
                src, out = tmp / "src", tmp / "proxy.mp4"
                a = _row(st, aid)
                if a is None:
                    return
                await asyncio.to_thread(media_store.open_to_file, _akey(st), "asset", aid, asset_file(st, user_id, aid), a.size, src)
                vf = "scale='min(1280,iw)':-2:flags=bicubic,format=yuv420p"
                async with _sem(st):
                    code, err = await run([ff, *_inp(pr, src), "-map", "0:v:0", "-map", "0:a:0?", "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                                           "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", "-map_metadata", "-1", "-y", str(out)],
                                          timeout=max(600, (pr.duration or 60) * 8))
                if code == 0 and out.exists():
                    await asyncio.to_thread(media_store.seal_file, _akey(st), "asset", f"{aid}.proxy", out, asset_file(st, user_id, aid, "proxy"))
                else:
                    log.warning("preview conversion failed for %s: %s", aid, err[-200:])
        except Exception:
            log.exception("preview conversion failed")
        finally:
            pend.pop(aid, None)

    asyncio.get_running_loop().create_task(go())


def _row(st, aid: str) -> MediaAsset | None:
    with st.db.session() as s:
        a = s.get(MediaAsset, aid)
        if a is not None:
            s.expunge(a)
        return a


# ---- reading ---------------------------------------------------------------------------------------------
def iter_asset(st, a: MediaAsset, start: int, end: int, suffix: str = "") -> Iterator[bytes]:
    bind = a.id if not suffix else f"{a.id}.{suffix}"
    p = asset_file(st, a.user_id, a.id, suffix)
    size = a.size if not suffix else _sealed_len(p)
    return media_store.iter_range(_akey(st), "asset", bind, p, size, start, end)


@contextlib.contextmanager
def materialise(st, assets: list, ephemeral_ok: bool = True) -> Iterator[list[tuple[object, str, str, str]]]:
    """Plaintext scratch files for stored or in-memory assets: yields [(open file, mime, filename, kind)] and deletes them on exit."""
    from . import media_svc
    opened = []
    mem = any(isinstance(a, str) for a in assets)
    with scratch(st, mem) as tmp:
        try:
            for i, a in enumerate(assets):
                if isinstance(a, str):  # an in-memory (no-save) asset id
                    v = _mem(st)[a]
                    p = tmp / f"s{i}"
                    p.write_bytes(v["data"])
                    nm, mime, kind = v.get("name") or a, v["mime"], v["meta"]["kind"]
                else:
                    p = tmp / f"s{i}"
                    if media_store.kind_of(asset_file(st, a.user_id, a.id)) == "chunked":
                        media_store.open_to_file(_akey(st), "asset", a.id, asset_file(st, a.user_id, a.id), a.size, p)
                    else:
                        p.write_bytes(media_svc.read_asset(st, a))
                    nm, mime, kind = display_name(meta_of(st, a), a.kind, a.id), a.mime, a.kind
                opened.append((open(p, "rb"), mime, nm, kind))  # noqa: SIM115
            yield opened
        finally:
            for f, *_ in opened:
                f.close()


def owned(st, s, user_id: str, aid: str):
    """The caller's asset (row, or the id of an in-memory one), else None. Someone else's id is indistinguishable from a missing one."""
    if aid.startswith("me_"):
        from . import media_svc
        media_svc.purge_mem(st)
        v = _mem(st).get(aid)
        return aid if v and v["user_id"] == user_id else None
    a = s.get(MediaAsset, aid) if aid.startswith("ma_") else None
    return a if a is not None and a.user_id == user_id else None


def mem_info(st, aid: str) -> dict:
    v = _mem(st)[aid]
    m = v["meta"]
    return {"id": aid, "kind": m["kind"], "mime": v["mime"], "size": len(v["data"]), "width": m["width"], "height": m["height"], "duration": m["duration"],
            "media": m.get("media") or {}, "name": m.get("name")}


# ---- listing -----------------------------------------------------------------------------------------------
def list_assets(st, s, user_id: str, *, kind: str | None, origin: str | None, limit: int, before: float | None) -> list[dict]:
    from .media_models import MediaJob
    q = select(MediaAsset).where(MediaAsset.user_id == user_id)
    if kind:
        q = q.where(MediaAsset.kind == kind)
    if before:
        q = q.where(MediaAsset.created_at < before)
    out = []
    jobs: dict[str, MediaJob | None] = {}
    for a in s.execute(q.order_by(MediaAsset.created_at.desc()).limit(limit * 3 if origin else limit)).scalars():
        o = asset_json(st, a)
        if origin and o["origin"] != origin:
            continue
        if a.job_id:
            if a.job_id not in jobs:
                jobs[a.job_id] = s.get(MediaJob, a.job_id)
            j = jobs[a.job_id]
            o["job"] = {"id": j.id, "prompt": st.vault.decrypt(j.prompt_enc, "media") if j and j.prompt_enc else "", "conversation_id": j.conversation_id if j else None} if j else None
        out.append(o)
        if len(out) >= limit:
            break
    return out


# ---- editing ---------------------------------------------------------------------------------------------
@dataclass
class EditSpec:
    start: float | None = None
    end: float | None = None
    crop: dict | None = None
    rotate: int = 0
    flip: bool = False
    mute: bool = False


@dataclass
class EditTask:
    id: str
    user_id: str
    source: str
    kind: str
    status: str = "running"
    progress: float = 0.0
    error: str | None = None
    asset: dict | None = None
    created: float = field(default_factory=time.time)
    cancel: asyncio.Event = field(default_factory=asyncio.Event)


def tasks(st) -> dict:
    return st.__dict__.setdefault("media_edits", {})


def task_out(t: EditTask) -> dict:
    return {"id": t.id, "status": t.status, "progress": round(t.progress, 3), "error": t.error, "asset": t.asset, "source": t.source, "kind": t.kind}


def check_spec(kind: str, spec: EditSpec, w: int, h: int, dur: float | None) -> EditSpec:
    """Validate against the real source and normalise (video sizes even). Raises MediaError(422)."""
    if spec.rotate not in (0, 90, 180, 270):
        raise MediaError(422, "invalid", "Rotation must be 0, 90, 180 or 270 degrees.")
    rw, rh = (h, w) if spec.rotate in (90, 270) else (w, h)
    if spec.crop is not None:
        try:
            x, y, cw, ch = (round(float(spec.crop[k])) for k in ("x", "y", "w", "h"))
        except (KeyError, TypeError, ValueError):
            raise MediaError(422, "invalid", "The crop needs x, y, w and h.") from None
        if kind == "video":
            x, y, cw, ch = x - x % 2, y - y % 2, cw - cw % 2, ch - ch % 2
        if cw < 16 or ch < 16:
            raise MediaError(422, "invalid", "The crop is too small (at least 16 × 16).")
        if x < 0 or y < 0 or x + cw > rw or y + ch > rh:
            raise MediaError(422, "invalid", f"The crop reaches outside the {'rotated ' if spec.rotate else ''}{rw} × {rh} frame.")
        full = x == 0 and y == 0 and cw >= rw and ch >= rh
        spec.crop = None if full else {"x": x, "y": y, "w": cw, "h": ch}
    if kind == "video":
        d = dur or 0
        s0 = 0.0 if spec.start is None else float(spec.start)
        e0 = d if spec.end is None else float(spec.end)
        if s0 < 0 or e0 > d + 0.05 or e0 - s0 < 0.1:
            raise MediaError(422, "invalid", f"The selected part must be at least 0.1 s and lie within the {d:.1f} s video.")
        spec.start = None if s0 < 0.001 else round(s0, 3)
        spec.end = None if e0 >= d - 0.04 else round(e0, 3)
    if (spec.crop is None and spec.rotate == 0 and not spec.flip and (kind == "image" or (spec.start is None and spec.end is None and not spec.mute))):
        raise MediaError(422, "nothing_to_do", "Nothing was changed. Trim, crop or rotate first.")
    return spec


def build_filters(kind: str, spec: EditSpec, p: Probe) -> str:
    f = []
    if kind == "image":
        e = _EXIF_FILTER.get(p.orientation, "")
        if e:
            f.append(e)
    f += {0: [], 90: ["transpose=1"], 180: ["hflip", "vflip"], 270: ["transpose=2"]}[spec.rotate]
    if spec.flip:
        f.append("hflip")
    if spec.crop:
        c = spec.crop
        f.append(f"crop={c['w']}:{c['h']}:{c['x']}:{c['y']}")
    if kind == "video":
        f.append("scale=trunc(iw/2)*2:trunc(ih/2)*2")
        f.append("format=yuv420p")
    return ",".join(f)


def out_dims(spec: EditSpec, w: int, h: int) -> tuple[int, int]:
    if spec.crop:
        return spec.crop["w"], spec.crop["h"]
    return (h, w) if spec.rotate in (90, 270) else (w, h)


async def start_edit(st, *, user_id: str, aid: str, spec: EditSpec, name: str | None, expires_at: float | None, ephemeral: bool, cap: int) -> EditTask:
    with st.db.session() as s:
        src_obj = owned(st, s, user_id, aid)
        if src_obj is None:
            raise MediaError(404, "not_found", "Not found.")
        if isinstance(src_obj, str):
            info = mem_info(st, src_obj)
        else:
            m = meta_of(st, src_obj)
            info = {"id": src_obj.id, "kind": src_obj.kind, "mime": src_obj.mime, "size": src_obj.size, "width": src_obj.width, "height": src_obj.height,
                    "duration": src_obj.duration, "media": m.get("media") or {}, "name": display_name(m, src_obj.kind, src_obj.id)}
            s.expunge(src_obj)
    if info["mime"] == "image/gif":
        raise MediaError(422, "unsupported_edit", "Animated GIFs cannot be edited here. Save a frame as PNG first.")
    ff, _ = need_ffmpeg()
    mine = [t for t in tasks(st).values() if t.user_id == user_id and t.status == "running"]
    if len(mine) >= 1:
        raise MediaError(429, "busy", "An edit is already running. Wait for it to finish or cancel it.")
    if len([t for t in tasks(st).values() if t.status == "running"]) >= 4:
        raise MediaError(429, "busy", "The server is busy editing other files. Try again in a minute.")
    spec = check_spec(info["kind"], spec, info["width"], info["height"], info["duration"])
    t = EditTask(id="ed_" + uuid.uuid4().hex[:20], user_id=user_id, source=aid, kind=info["kind"])
    tasks(st)[t.id] = t
    _gc_tasks(st)
    asyncio.get_running_loop().create_task(_run_edit(st, t, src_obj, info, spec, name, expires_at, ephemeral or isinstance(src_obj, str), ff, cap))
    return t


def _gc_tasks(st) -> None:
    now = time.time()
    for k in [k for k, v in tasks(st).items() if v.status != "running" and now - v.created > 3600]:
        tasks(st).pop(k, None)


async def _run_edit(st, t: EditTask, src, info: dict, spec: EditSpec, name: str | None, expires_at: float | None, ephemeral: bool, ff: str, cap: int) -> None:
    kind = info["kind"]
    try:
        with scratch(st, ephemeral) as tmp, materialise(st, [src]) as [(fh, _m, _n, _k)]:
            fh.close()
            path = Path(fh.name)
            media = info.get("media") or {}
            sn = Sniffed(kind, info["mime"], media.get("demuxer") or default_demuxer(info["mime"]))
            pr = Probe(kind, info["mime"], sn.demuxer, info["width"], info["height"], info["duration"], media.get("fps"), media.get("codec"),
                       media.get("audio_codec"), int(media.get("orientation") or 1))
            vf = build_filters(kind, spec, pr)
            ext = "mp4" if kind == "video" else ("jpg" if info["mime"] == "image/jpeg" else "png")
            out = tmp / f"out.{ext}"
            if kind == "video":
                seek = ["-ss", f"{spec.start:.3f}"] if spec.start is not None else []
                length = (spec.end if spec.end is not None else info["duration"]) - (spec.start or 0)
                cmd = [ff, *_inp(pr, path, *seek), "-t", f"{length:.3f}", "-map", "0:v:0", *([] if spec.mute else ["-map", "0:a:0?"]), *(["-an"] if spec.mute else []),
                       "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
                       "-map_metadata", "-1", "-progress", "pipe:1", "-nostats", "-y", str(out)]
            else:
                q = ["-q:v", "2"] if ext == "jpg" else []
                cmd = [ff, *_inp(pr, path), *(["-vf", vf] if vf else []), "-frames:v", "1", *q, "-map_metadata", "-1", "-y", str(out)]
                length = 1

            def on_out(line: str) -> None:
                if line.startswith(("out_time_us=", "out_time_ms=")):
                    with contextlib.suppress(ValueError):
                        t.progress = max(t.progress, min(0.99, int(line.split("=", 1)[1]) / 1e6 / max(length, 0.1)))

            async with _sem(st):
                if t.cancel.is_set():
                    t.status = "cancelled"
                    return
                code, err = await run(cmd, timeout=max(300, length * 10), on_out=on_out, cancel=t.cancel)
            if t.cancel.is_set():
                t.status = "cancelled"
                return
            if code != 0 or not out.exists() or out.stat().st_size == 0:
                log.warning("edit failed: %s", err[-300:])
                raise MediaError(422, "edit_failed", "The edit could not be made. The file may use a codec this server cannot process.")
            osn = Sniffed(kind, "video/mp4" if kind == "video" else ("image/jpeg" if ext == "jpg" else "image/png"),
                          "mov,mp4,m4a,3gp,3g2,mj2" if kind == "video" else ("jpeg_pipe" if ext == "jpg" else "png_pipe"))
            head = out.read_bytes()[:65536] if kind == "image" else b""
            npr = await probe(out, osn, head)
            npr.orientation = 1
            thumb = tmp / "thumb.jpg"
            has_thumb = await make_thumb(st, npr, out, thumb)
            if out.stat().st_size > cap:
                raise MediaError(413, "too_large", "The edited file is larger than this server allows.")
            new_name = clean_name(name, kind) if name else _edited_name(info.get("name") or "", kind, ext)
            edit_meta = {"trim": [spec.start or 0, spec.end] if (spec.start is not None or spec.end is not None) else None, "crop": spec.crop,
                         "rotate": spec.rotate, "flip": spec.flip, "mute": spec.mute}
            asset = await _store(st, user_id=t.user_id, name=new_name, pr=npr, src=out, size=out.stat().st_size, thumb=thumb if has_thumb else None,
                                 ephemeral=ephemeral, expires_at=expires_at, origin="edit", extra={"parent": info["id"], "edit": edit_meta})
            t.asset, t.progress, t.status = asset, 1.0, "completed"
    except MediaError as e:
        t.status, t.error = "failed", e.message
    except asyncio.CancelledError:
        t.status = "cancelled"
        raise
    except Exception:
        log.exception("edit crashed")
        t.status, t.error = "failed", "The edit failed unexpectedly."


def _edited_name(orig: str, kind: str, ext: str) -> str:
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", orig or "") or ("picture" if kind == "image" else "video")
    stem = re.sub(r" \(edited(?: \d+)?\)$", "", stem)
    return f"{stem[:100]} (edited).{ext}"


def cancel_edit(st, user_id: str, eid: str) -> EditTask:
    t = tasks(st).get(eid)
    if t is None or t.user_id != user_id:
        raise MediaError(404, "not_found", "Not found.")
    if t.status == "running":
        t.cancel.set()
    return t
