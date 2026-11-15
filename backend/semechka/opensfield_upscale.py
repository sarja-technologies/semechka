"""AI upscaling for Imajutsu through OpenSfield's narrow upscale API on the same Spark.

Semechka has no GPU code of its own: the upscalers (SeedVR2, Real-ESRGAN) run in OpenSfield's ComfyUI. This module is the whole contract:

* ``SEMECHKA_OPENSFIELD_URL``                  default http://127.0.0.1:8765
* ``SEMECHKA_OPENSFIELD_UPSCALE_TOKEN_FILE``   a file (mode 0600) holding the upscale-only token. Never stored in the database, never returned by any API,
  never logged. Unset or unreadable = AI upscaling reports "not configured" and Lanczos scaling keeps working.

The token only opens OpenSfield's /api/internal/upscale* routes (create/read/fetch/cancel/delete upscale jobs); it cannot load or unload models.
Everything here fails soft: a down OpenSfield never breaks the rest of Imajutsu, it just turns the AI methods off with a plain reason.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Callable
from pathlib import Path

import httpx

ENGINES = ("seedvr2", "esrgan")
_CAPS: dict = {"at": 0.0, "value": None}
CAPS_TTL = 20.0
POLL_S = 2.0
MAX_WAIT_S = 1800.0                 # a 90 MP, two-pass upscale takes about five minutes; the memory gate may add a wait


class UpscaleUnavailable(Exception):
    """A plain sentence for the person."""


def base_url() -> str:
    return os.environ.get("SEMECHKA_OPENSFIELD_URL", "http://127.0.0.1:8765").rstrip("/")


def _token() -> str | None:
    p = os.environ.get("SEMECHKA_OPENSFIELD_UPSCALE_TOKEN_FILE")
    if not p:
        return None
    try:
        t = Path(p).expanduser().read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return t or None


# tests replace this to run against an in-process fake (httpx.MockTransport)
def make_client(timeout: float = 30.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url(), timeout=timeout)


def _headers() -> dict:
    t = _token()
    if not t:
        raise UpscaleUnavailable("AI upscaling is not set up on this server (no OpenSfield upscale token).")
    return {"X-OSF-Upscale": t}


def _plain(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
        if isinstance(d, str):
            return d[:300]
    except Exception:  # noqa: BLE001
        pass
    return f"OpenSfield answered {r.status_code}."


async def capabilities(force: bool = False) -> dict:
    """{"configured": bool, "reachable": bool, "reason": str|None, "engines": [...], "max_output_pixels", "max_edge", ...}. Never raises."""
    now = time.monotonic()
    if not force and _CAPS["value"] is not None and now - _CAPS["at"] < CAPS_TTL:
        return _CAPS["value"]
    out: dict = {"configured": True, "reachable": False, "reason": None, "engines": []}
    try:
        h = _headers()
    except UpscaleUnavailable as e:
        out.update(configured=False, reason=str(e))
        return out                                    # not cached: setting the token file takes effect immediately
    try:
        async with make_client(8.0) as c:
            r = await c.get("/api/internal/upscale/capabilities", headers=h)
        if r.status_code == 200:
            out.update(r.json(), reachable=True)
        elif r.status_code in (401, 503):
            out.update(reason="OpenSfield did not accept this server's upscale token.")
        else:
            out.update(reason=_plain(r))
    except httpx.HTTPError:
        out.update(reason="OpenSfield (the image tools on the Spark) is not reachable right now.")
    _CAPS.update(at=now, value=out)
    return out


def engine_info(caps: dict, engine: str) -> dict | None:
    return next((e for e in caps.get("engines", []) if e.get("id") == engine), None)


def wire_name(data: bytes, name: str) -> str:
    """The file name sent to OpenSfield: its upload check goes by the extension, and a library name (a generated picture's title) may have none."""
    ext = ".png" if data[:4] == b"\x89PNG" else ".jpg" if data[:3] == b"\xff\xd8\xff" else ".webp" if data[8:12] == b"WEBP" else ""
    if not ext:
        return name
    return (name.rsplit(".", 1)[0] if name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")) else name)[:80] + ext


async def upscale(data: bytes, name: str, engine: str, width: int, height: int, fmt: str, *, on_progress: Callable[[float, str], None] | None = None,
                  cancelled: Callable[[], bool] | None = None) -> tuple[bytes, dict]:
    """Send the picture, wait for the result, fetch it, free OpenSfield's copy. -> (file bytes, the job's result description). Raises UpscaleUnavailable."""
    if engine not in ENGINES:
        raise UpscaleUnavailable("Unknown upscale method.")
    h = _headers()
    job = None
    name = wire_name(data, name)
    try:
        async with make_client(60.0) as c:
            r = await c.post("/api/internal/upscale", headers=h, files={"file": (name, data, "application/octet-stream")},
                             data={"engine": engine, "width": str(width), "height": str(height), "fmt": fmt})
            if r.status_code == 422:
                raise UpscaleUnavailable(_plain(r))
            if r.status_code >= 300:
                raise UpscaleUnavailable("OpenSfield could not start the upscale: " + _plain(r))
            job = r.json()["job"]
            t0 = time.monotonic()
            while True:
                if cancelled and cancelled():
                    await c.post(f"/api/internal/upscale/{job}/cancel", headers=h)
                    raise asyncio.CancelledError
                s = await c.get(f"/api/internal/upscale/{job}", headers=h)
                if s.status_code != 200:
                    raise UpscaleUnavailable("OpenSfield lost track of the upscale: " + _plain(s))
                st = s.json()
                if on_progress:
                    on_progress(float(st.get("progress") or 0), str(st.get("phase") or ""))
                if st["status"] == "done":
                    res = st.get("result") or {}
                    break
                if st["status"] in ("error", "cancelled"):
                    raise UpscaleUnavailable("The upscale failed: " + str(st.get("error") or st["status"])[:300])
                if time.monotonic() - t0 > MAX_WAIT_S:
                    await c.post(f"/api/internal/upscale/{job}/cancel", headers=h)
                    raise UpscaleUnavailable("The upscale took too long and was stopped. Try a smaller size.")
                await asyncio.sleep(POLL_S)
            f = await c.get(f"/api/internal/upscale/{job}/result", headers=h, timeout=300.0)
            if f.status_code != 200:
                raise UpscaleUnavailable("The upscaled picture could not be fetched: " + _plain(f))
            body = f.content
            await c.delete(f"/api/internal/upscale/{job}", headers=h)       # free the copy on the Spark
            job = None
            return body, res
    except httpx.HTTPError as e:
        raise UpscaleUnavailable("OpenSfield (the image tools on the Spark) stopped answering during the upscale.") from e
    finally:
        if job:                                                              # failed after the job existed: best-effort cleanup
            try:
                async with make_client(8.0) as c:
                    await c.delete(f"/api/internal/upscale/{job}", headers=_headers())
            except Exception:  # noqa: BLE001
                pass
