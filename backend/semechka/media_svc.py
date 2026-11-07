"""Image and video generation: job lifecycle, encrypted asset storage, limits.

Design notes (all of this is enforced here, not in the interface):
- Progress is poll-driven: every status read advances the job, and an optional background pump (cfg.background)
  advances jobs nobody is watching. No state is held only in a task, so a service restart loses nothing but
  the in-memory results of no-save requests.
- Results are copied into data_dir/assets/<user_id>/<asset_id>.bin, AES-256-GCM encrypted with an id-bound
  associated-data tag, then discarded from the inference engine's own library.
- A no-save request stores no prompt, no parameters and no file. Its result is held in process memory for
  EPHEMERAL_TTL seconds so the browser can fetch it, then forgotten.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import func, select

from . import adapters, media_store, services
from .adapters import AdapterError
from .backends_svc import adapter_for
from .media_models import MediaAsset, MediaJob
from .models import Backend, Message, Model, UsageEvent, User

log = logging.getLogger("semechka.media")

MEDIA_KINDS = ("image", "video")
MAX_BYTES = {"image": 40 * 1024 * 1024, "video": 1024 * 1024 * 1024}  # saved results stream to disk, so the cap is about disk, not memory
EPHEMERAL_MAX = 200 * 1024 * 1024  # no-save results are held in memory
EPHEMERAL_TTL = 15 * 60
EPHEMERAL_CAP_BYTES = 512 * 1024 * 1024
UNREACHABLE_GIVE_UP_S = 10 * 60
MAX_FETCH_FAILURES = 5
PROMPT_MAX = 4000
SAFE_MIME = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "video/mp4": "mp4", "video/webm": "webm"}
ACTIVE = ("queued", "running")


# ---- availability ----------------------------------------------------------------------------------
def backend_supports_media(s) -> bool:
    for b in s.execute(select(Backend).where(Backend.enabled == True)).scalars():  # noqa: E712
        cls = adapters.REGISTRY.get(b.adapter)
        if cls is not None and cls.manifest.media:
            return True
    return False


def unavailable_reason(s, mode: str) -> str | None:
    """Why image/video cannot work at all in this deployment, or None when a media-capable backend exists."""
    if backend_supports_media(s):
        return None
    what = "images" if mode == "image" else "video"
    return (f"No connected inference backend can generate {what}. Connect a Sarness Engine that has {mode} models "
            "installed (Administration, Backends).")


# ---- parameters ------------------------------------------------------------------------------------
def clean_text(v) -> str:
    """An engine-supplied sentence, safe to show: server file paths removed, bounded length."""
    from .adapters.sarness_media import clean_error
    return clean_error(v) if v else ""


def media_info(m: Model) -> dict:
    return (m.meta or {}).get("media") or {}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _round_to(v: int, mult: int) -> int:
    return max(mult, int(round(v / mult)) * mult)


def option_defaults(kind: str, m: Model, default_size: int, video_max_s: int) -> dict:
    info = media_info(m)
    d, lim = dict(info.get("defaults") or {}), info.get("limits") or {}
    if kind == "image" and default_size:
        mult, lo, hi = int(lim.get("multiple") or 8), int(lim.get("min_side") or 256), int(lim.get("max_side") or 1024)
        side = _clamp(_round_to(default_size, mult), lo, hi)
        d["width"] = d["height"] = side
    if kind == "video":
        fps = d.get("fps") or 16
        step = int(lim.get("frame_step") or 4)
        fr = d.get("frames") or 33
        while fr > 1 and fr / fps > video_max_s and fr - step >= int((lim.get("frames") or [5, 5])[0]):
            fr -= step
        d["frames"] = fr
    return d


class ParamError(ValueError):
    pass


def validate_params(kind: str, m: Model, body: dict, video_max_s: int) -> dict:
    info = media_info(m)
    lim, dfl = info.get("limits") or {}, info.get("defaults") or {}

    def pick(name, default=None):
        v = body.get(name)
        return default if v is None else v

    def num(name, lo, hi, integer=True):
        v = pick(name, dfl.get(name))
        if v is None:
            return None
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ParamError(f"{name} must be a number.")
        if integer and int(v) != v:
            raise ParamError(f"{name} must be a whole number.")
        if not lo <= v <= hi:
            raise ParamError(f"{name} must be between {lo} and {hi} for this model.")
        return int(v) if integer else float(v)

    out: dict[str, Any] = {}
    prompt = str(body.get("prompt") or "").strip()
    if not prompt:
        raise ParamError("Write a prompt first.")
    if len(prompt) > PROMPT_MAX:
        raise ParamError(f"The prompt is longer than {PROMPT_MAX} characters.")
    out["prompt"] = prompt
    neg = str(body.get("negative_prompt") or "").strip()
    if neg:
        if not info.get("negative_prompt"):
            raise ParamError("This model does not use a negative prompt.")
        if len(neg) > PROMPT_MAX:
            raise ParamError(f"The negative prompt is longer than {PROMPT_MAX} characters.")
        out["negative_prompt"] = neg
    seed = pick("seed", -1)
    if isinstance(seed, bool) or not isinstance(seed, int) or not -1 <= seed <= 2**31 - 1:
        raise ParamError("seed must be -1 (random) or a whole number up to 2147483647.")
    out["seed"] = seed
    s_lo, s_hi = (lim.get("steps") or [1, 100])[:2]
    g_lo, g_hi = (lim.get("guidance") or [0, 20])[:2]
    out["steps"] = num("steps", s_lo, s_hi)
    out["guidance"] = num("guidance", g_lo, g_hi, integer=False)
    mult = int(lim.get("multiple") or 8)
    if kind == "image":
        lo, hi = int(lim.get("min_side") or 256), int(lim.get("max_side") or 1024)
        for k in ("width", "height"):
            out[k] = num(k, lo, hi)
            if out[k] is None or out[k] % mult:
                raise ParamError(f"{k} must be a multiple of {mult} for this model.")
        sched = body.get("scheduler")
        if sched:
            if sched not in (info.get("schedulers") or []):
                raise ParamError("This model does not offer that scheduler.")
            out["scheduler"] = sched
    else:
        res = [tuple(r) for r in (lim.get("resolutions") or [])]
        w, h = pick("width", dfl.get("width")), pick("height", dfl.get("height"))
        if res and (w, h) not in res:
            raise ParamError("Pick one of the resolutions this model supports.")
        out["width"], out["height"] = int(w), int(h)
        f_lo, f_hi = (lim.get("frames") or [5, 121])[:2]
        step = int(lim.get("frame_step") or 4)
        frames = num("frames", f_lo, f_hi)
        if frames is None or (frames - 1) % step:
            raise ParamError(f"frames must be 1 more than a multiple of {step} for this model.")
        fps_lo, fps_hi = (lim.get("fps") or [8, 30])[:2]
        fps = num("fps", fps_lo, fps_hi)
        out["frames"], out["fps"] = frames, fps
        if frames / fps > video_max_s + 1e-9:
            raise ParamError(f"Your organisation limits clips to {video_max_s} seconds ({frames} frames at {fps} fps is "
                             f"{frames / fps:.1f}).")
    return {k: v for k, v in out.items() if v is not None}


# ---- composite profiles (e.g. OpenJutsu) --------------------------------------------------------------------
FILE_MIMES = ("video/", "image/")


def profile_info(m: Model) -> dict | None:
    return media_info(m).get("profile")


def validate_profile_inputs(m: Model, inputs: dict, files: dict[str, list[tuple[str, int]]], max_bytes: int) -> dict:
    """Check a profile request against the schema THE ENGINE published for it (the engine validates again): returns the plain inputs.
    ``files`` maps an input name to [(mime, size)]. A profile that is not ready is refused with the engine's own reason."""
    prof = profile_info(m)
    if not prof:
        raise ParamError("This model is not an editing profile.")
    if not prof.get("ready"):
        raise ParamError(f"This is not ready on the server yet: {prof.get('reason') or 'unknown reason'}")
    schema = prof.get("inputs_schema") or {}
    out: dict[str, Any] = {}
    # Some profiles (OpenJutsu) have several operations and each input says which ones use it. An input that the chosen operation does not
    # use is neither required nor sent; a file given for one is refused with a plain reason (the person decides what to remove).
    op = inputs.get("operation", (schema.get("operation") or {}).get("default")) if "operation" in schema else None

    def used(spec: dict) -> bool:
        return not op or not spec.get("operations") or op in spec["operations"]

    for name, spec in schema.items():
        t = spec.get("type", "string")
        if not used(spec) and name != "operation":
            if t == "file" and files.get(name):
                raise ParamError(f"{spec.get('label') or name} are not used by the '{op}' operation. Remove them or choose another operation.")
            continue
        if t == "file":
            got = files.get(name) or []
            if spec.get("required") and not got:
                raise ParamError(f"'{name}' is required: add the file.")
            if len(got) > (int(spec.get("max_files", 9)) if spec.get("multi") else 1):
                raise ParamError(f"'{name}' takes " + (f"at most {spec.get('max_files', 9)} files." if spec.get("multi") else "one file."))
            for mime, size in got:
                if not mime.startswith(FILE_MIMES):
                    raise ParamError(f"'{name}' must be a video or a picture (got {mime or 'unknown type'}).")
                if spec.get("kind") and not mime.startswith(spec["kind"] + "/"):
                    raise ParamError(f"'{name}' must be a {spec['kind']}.")
                if size > max_bytes:
                    raise ParamError(f"'{name}' is larger than the {max_bytes // 2**20} MB this server accepts.")
                if size == 0:
                    raise ParamError(f"'{name}' is empty.")
            continue
        v = inputs.get(name, spec.get("default"))
        if v in (None, ""):
            if spec.get("required"):
                raise ParamError(f"'{name}' is required.")
            continue
        if t == "string":
            v = str(v).strip()
            if len(v) > min(int(spec.get("max_length", PROMPT_MAX)), PROMPT_MAX):
                raise ParamError(f"'{name}' is too long.")
        elif t in ("int", "float"):
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ParamError(f"'{name}' must be a number.")
            if t == "int" and int(v) != v:
                raise ParamError(f"'{name}' must be a whole number.")
            if ("min" in spec and v < spec["min"]) or ("max" in spec and v > spec["max"]):
                raise ParamError(f"'{name}' must be between {spec.get('min')} and {spec.get('max')}.")
            v = int(v) if t == "int" else float(v)
        elif t == "bool":
            if not isinstance(v, bool):
                raise ParamError(f"'{name}' must be on or off.")
        elif t == "json":  # structured values (a box, a list of points): sent as native JSON
            if isinstance(v, str):
                try:
                    v = json.loads(v)
                except ValueError:
                    raise ParamError(f"'{name}' must be valid JSON.") from None
            if not isinstance(v, (list, dict)) or len(json.dumps(v)) > int(spec.get("max_length", 20000)):
                raise ParamError(f"'{name}' must be a JSON list or object of reasonable size.")
        elif t == "enum" and v not in spec.get("values", []):
            raise ParamError(f"'{name}' must be one of: {', '.join(map(str, spec.get('values', [])))}.")
        out[name] = v
    unknown = set(inputs) - set(schema)
    if unknown:
        raise ParamError(f"Unknown setting: {', '.join(sorted(unknown))}.")
    return out


async def submit_profile(st, user: User, m: Model, b: Backend, inputs: dict, uploads: dict[str, list[tuple[Any, str, str]]], save: bool,
                         expires_at: float | None, *, job_id: str | None = None, origin: str | None = None,
                         sources: dict[str, list[str]] | None = None, extra_params: dict | None = None) -> str:
    """Hand source files and settings to the engine, which runs the profile as an ordinary durable job. Raises AdapterError and leaves
    nothing behind. ``uploads`` maps an input name to [(file object or bytes, mime, filename)]; ``sources`` records which library assets they came from."""
    job_id = job_id or "mj_" + uuid.uuid4().hex[:24]
    ad = adapter_for(b, st)
    spec = (profile_info(m) or {}).get("inputs_schema") or {}
    sent = dict(inputs)
    for name, items in uploads.items():
        ids = [await ad.media_upload(fo, mime, fname) for fo, mime, fname in items]
        sent[name] = ids if (spec.get(name) or {}).get("multi") else ids[0]
    remote = await ad.media_submit_profile(m.real_id.split(":", 1)[1], sent, job_id, **({"title": origin + " " + m.real_id.split(":", 1)[1]} if origin else {}))
    # what is shown for the job: the prompt, or the main text the operation takes (first string input of the schema)
    prompt = str(inputs.get("prompt") or next((v for k, v in inputs.items() if isinstance(v, str) and v and (spec.get(k) or {}).get("type") == "string"), ""))
    with st.db.session(write=True) as s:
        s.add(MediaJob(id=job_id, user_id=user.id, kind="video", model_id=m.id, backend_id=b.id, remote_id=remote, save=save,
                       prompt_enc=st.vault.encrypt(prompt, "media") if save else None,
                       params_enc=st.vault.encrypt(json.dumps({**{k: v for k, v in inputs.items() if k != "prompt"}, **({"sources": sources} if sources else {}),
                                                        **(extra_params or {})}), "media") if save else None,
                       status="queued", progress_text="Queued", started_at=None, expires_at=expires_at if save else None))
    return job_id


# ---- storage ---------------------------------------------------------------------------------------
def assets_dir(cfg) -> Path:
    return cfg.data_dir / "assets"


def asset_path(cfg, user_id: str, asset_id: str) -> Path:
    return assets_dir(cfg) / user_id / f"{asset_id}.bin"


def _mem(st) -> dict:
    return st.__dict__.setdefault("media_mem", {})


def _locks(st) -> dict:
    return st.__dict__.setdefault("media_locks", {})


def purge_mem(st) -> None:
    now = time.time()
    mem = _mem(st)
    for k in [k for k, v in mem.items() if v["expires"] < now]:
        mem.pop(k, None)


def mem_bytes(st) -> int:
    return sum(len(v["data"]) for v in _mem(st).values())


def write_asset(st, user_id: str, asset_id: str, data: bytes) -> None:
    p = asset_path(st.cfg, user_id, asset_id)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(st.vault.encrypt_bytes(data, "asset", asset_id))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def read_asset(st, a: MediaAsset) -> bytes:
    """The whole file. Meant for pictures; large videos are streamed by range instead (media_lib.iter_asset)."""
    p = asset_path(st.cfg, a.user_id, a.id)
    if media_store.kind_of(p) == "chunked":
        return media_store.read_all(st.vault.key_for("asset"), "asset", a.id, p, a.size)
    return st.vault.decrypt_bytes(p.read_bytes(), "asset", a.id)


def remove_asset_file(st, user_id: str, asset_id: str) -> None:
    """The asset's file and everything derived from it (preview picture, filmstrips, converted copy)."""
    d = asset_path(st.cfg, user_id, asset_id).parent
    if d.is_dir():
        for f in d.glob(f"{asset_id}.*"):
            f.unlink(missing_ok=True)


# ---- serialisation ---------------------------------------------------------------------------------
def asset_out(st, a: MediaAsset) -> dict:
    return {"id": a.id, "kind": a.kind, "mime": a.mime, "size": a.size, "width": a.width, "height": a.height,
            "duration": a.duration, "seed": a.seed, "created_at": a.created_at, "expires_at": a.expires_at,
            "prompt": st.vault.decrypt(a.prompt_enc, "media") if a.prompt_enc else "",
            "params": json.loads(st.vault.decrypt(a.params_enc, "media")) if a.params_enc else {},
            "saved": True, "model_id": a.model_id}


def _queue_tech(j: MediaJob) -> dict:
    if j.status == "queued":
        return {"state": "queued", "position": j.queue_position, "note": "Waiting for the inference engine's GPU queue."}
    return {"state": j.status, "position": None, "note": "Jobs run one at a time on the inference engine's GPU."}


def job_out(st, s, p, j: MediaJob, models: dict | None = None) -> dict:
    from .models import Model as M
    m = (models or {}).get(j.model_id) or s.get(M, j.model_id)
    b = s.get(Backend, j.backend_id)
    params = json.loads(st.vault.decrypt(j.params_enc, "media")) if j.params_enc else {}
    end = j.finished_at or time.time()
    elapsed = max(0.0, end - (j.started_at or j.created_at))
    assets = [asset_out(st, a) for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == j.id)
                                                  .order_by(MediaAsset.created_at)).scalars()]
    for mid, v in _mem(st).items():
        if v["job_id"] == j.id and v["user_id"] == j.user_id:
            assets.append({**v["meta"], "id": mid, "mime": v["mime"], "size": len(v["data"]), "saved": False,
                           "expires_at": v["expires"], "prompt": "", "params": {}, "model_id": j.model_id})
    return {"id": j.id, "kind": j.kind, "status": j.status, "model_id": j.model_id, "model": m.alias if m else None,
            "prompt": st.vault.decrypt(j.prompt_enc, "media") if j.prompt_enc else "", "params": params,
            "progress": j.progress, "progress_text": j.progress_text, "queue_position": j.queue_position,
            "elapsed_s": round(elapsed, 1), "error": j.error, "save": j.save, "created_at": j.created_at,
            "finished_at": j.finished_at, "assets": assets, "conversation_id": j.conversation_id,
            "tech": services.filter_tech(p, {
                "model_real_id": services.display_id(m.real_id) if m else None, "gen_params": params or None,
                "elapsed_s": round(elapsed, 1), "queue": _queue_tech(j), "node": b.name if b else None,
                "trace_id": j.remote_id})}


# ---- lifecycle -------------------------------------------------------------------------------------
def _finish(st, job_id: str, status: str, error: str | None = None, text: str | None = None) -> None:
    with st.db.session(write=True) as s:
        j = s.get(MediaJob, job_id)
        if j is None or j.status not in ACTIVE:
            return
        j.status, j.finished_at = status, time.time()
        j.error = error[:400] if error else None
        j.progress_text = text or {"completed": "Done", "failed": "Failed", "cancelled": "Cancelled"}[status]
        if status == "completed":
            j.progress = 1.0
        if j.message_id:  # a generation that lives in a conversation: the message shows the job's outcome
            msg = s.get(Message, j.message_id)
            if msg is not None:
                msg.status = {"completed": "complete", "failed": "error", "cancelled": "stopped"}[status]
        s.add(UsageEvent(user_id=j.user_id, model_id=j.model_id, kind=j.kind, tokens_source="unavailable",
                         duration_s=j.finished_at - (j.started_at or j.created_at), ephemeral=not j.save,
                         status={"completed": "complete"}.get(status, status)))


async def upload_inputs(st, b: Backend, uploads: dict[str, list[tuple[Any, str, str]]]) -> dict[str, Any]:
    """Hand source pictures to the engine once. ``uploads`` maps an engine input name to [(file object or bytes, mime, filename)]; the result maps the name
    to the engine's file id (a list for ``extra_references``). Used for image jobs that start from a picture, a mask or more pictures (Imajutsu)."""
    ad = adapter_for(b, st)
    out: dict[str, Any] = {}
    for name, items in uploads.items():
        ids = [await ad.media_upload(fo, mime, fname) for fo, mime, fname in items]
        out[name] = ids if name == "extra_references" else ids[0]
    return out


async def submit(st, user: User, kind: str, m: Model, b: Backend, params: dict, save: bool,
                 expires_at: float | None, *, job_id: str | None = None, conversation_id: str | None = None,
                 message_id: str | None = None, origin: str | None = None, extra_params: dict | None = None) -> str:
    """Create the job row and hand the job to the engine. Raises AdapterError (nothing is left behind).
    `params` may carry engine-side source images (reference_image / extra_references / mask_image / start_image, as engine file ids); they are not stored
    here. ``extra_params`` is kept with the job's (encrypted) parameters and never sent to the engine (Imajutsu keeps its project snapshot there)."""
    job_id = job_id or "mj_" + uuid.uuid4().hex[:24]
    ad = adapter_for(b, st)
    real = m.real_id.split(":", 1)[1]
    remote = await ad.media_submit(kind, {**params, "model": real}, job_id, **({"title": f"{origin} {kind}"} if origin else {}))
    params = {k: v for k, v in params.items() if k not in ("reference_image", "start_image", "extra_references", "mask_image")}
    with st.db.session(write=True) as s:
        j = MediaJob(id=job_id, user_id=user.id, kind=kind, model_id=m.id, backend_id=b.id, remote_id=remote, save=save,
                     prompt_enc=st.vault.encrypt(params["prompt"], "media") if save else None,
                     params_enc=st.vault.encrypt(json.dumps({**{k: v for k, v in params.items() if k != "prompt"}, **(extra_params or {})}), "media")
                     if save else None, status="queued", progress_text="Queued", started_at=None,
                     expires_at=expires_at if save else None, conversation_id=conversation_id,
                     message_id=message_id)
        s.add(j)
    return job_id


def new_job_id() -> str:
    return "mj_" + uuid.uuid4().hex[:24]


def delete_for_conversation(st, s, conversation_id: str) -> list[str]:
    """Remove the jobs and result files a conversation produced (conversation deletion and retention).
    Returns the ids of jobs still running, which the caller cancels; their rows and any late result expire at once."""
    running: list[str] = []
    for j in s.execute(select(MediaJob).where(MediaJob.conversation_id == conversation_id)).scalars().all():
        for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == j.id)).scalars().all():
            remove_asset_file(st, a.user_id, a.id)
            s.delete(a)
        for k in [k for k, v in _mem(st).items() if v["job_id"] == j.id]:
            _mem(st).pop(k, None)
        if j.status in ACTIVE:
            j.conversation_id = j.message_id = None
            j.prompt_enc, j.params_enc, j.expires_at = None, None, time.time()  # the sweep removes what remains
            running.append(j.id)
        else:
            s.delete(j)
    return running


async def refresh(st, job_id: str) -> None:
    """Advance one job from the engine's state. Safe to call concurrently and repeatedly."""
    lock = _locks(st).setdefault(job_id, asyncio.Lock())
    async with lock:
        with st.db.session() as s:
            j = s.get(MediaJob, job_id)
            if j is None or j.status not in ACTIVE:
                return
            b = s.get(Backend, j.backend_id)
            if b is None:
                _finish(st, job_id, "failed", "The inference backend was removed.")
                return
            ad = adapter_for(b, st)
            remote, kind, last_ok, user_id = j.remote_id, j.kind, j.last_ok, j.user_id
            save, model_id = j.save, j.model_id
            prompt = st.vault.decrypt(j.prompt_enc, "media") if j.prompt_enc else ""
            params_enc = j.params_enc
        try:
            r = await ad.media_status(remote)
        except AdapterError as e:
            if time.time() - last_ok > UNREACHABLE_GIVE_UP_S:
                _finish(st, job_id, "failed", f"The inference backend has not answered for {UNREACHABLE_GIVE_UP_S // 60} minutes.")
            else:
                with st.db.session(write=True) as s:
                    j = s.get(MediaJob, job_id)
                    if j:
                        j.progress_text = "Waiting for the inference backend" if e.kind == "unreachable" else str(e)[:300]
            return
        status = r["status"]
        with st.db.session(write=True) as s:
            j = s.get(MediaJob, job_id)
            j.last_ok = time.time()
            j.queue_position = r.get("queue_position")
            if status in ("running", "completed") and j.started_at is None:
                j.started_at = time.time()
            if status in ACTIVE:
                j.status = status
                j.progress = r["progress"] if (status == "running" and r["progress"]) else None
                j.progress_text = r["text"] or ("Queued" if status == "queued" else "Working")
        if status == "failed":
            _finish(st, job_id, "failed", r.get("error") or "The engine reported a failure.")
        elif status == "cancelled":
            _finish(st, job_id, "cancelled")
        elif status == "completed":
            await _collect(st, job_id, ad, r["outputs"], kind, user_id, model_id, save, prompt, params_enc)


async def _collect(st, job_id, ad, outputs, kind, user_id, model_id, save, prompt, params_enc) -> None:
    """Copy the engine's result files into Semechka. Saved results are streamed engine -> scratch file -> sealed chunked file (never held in
    memory, so large videos work); no-save results are held in memory for a few minutes, so they keep the smaller cap."""
    import contextlib

    from . import media_lib
    if not outputs:
        _finish(st, job_id, "failed", "The engine finished without producing a file.")
        return
    cap = MAX_BYTES[kind] if save else min(MAX_BYTES[kind], EPHEMERAL_MAX)
    with contextlib.ExitStack() as stack:
        tmp = stack.enter_context(media_lib.scratch(st, not save)) if save else None
        fetched = []
        try:
            for i, o in enumerate(outputs):
                if save:
                    dest = tmp / f"out{i}"
                    mime = await ad.media_fetch_to(o["ref"], dest, cap)
                    payload = dest
                else:
                    payload, mime = await ad.media_fetch(o["ref"], cap)
                if mime not in SAFE_MIME:
                    raise AdapterError("The engine returned a file type Semechka does not serve.", kind="bad_type")
                fetched.append((o, payload, mime))
        except AdapterError as e:
            permanent = e.kind in ("too_large", "bad_type")
            key = f"fetch_fail:{job_id}"
            cnt = st.__dict__.setdefault("media_counters", {})
            cnt[key] = cnt.get(key, 0) + 1
            if permanent or cnt[key] >= MAX_FETCH_FAILURES:
                cnt.pop(key, None)
                _finish(st, job_id, "failed", str(e))
                for o in outputs:
                    await _discard(ad, o)
            else:
                with st.db.session(write=True) as s:
                    j = s.get(MediaJob, job_id)
                    j.progress_text = "Retrieving the result"
            return
        now = time.time()
        if save:
            with st.db.session() as s:
                u = s.get(User, user_id)
                days = st.settings.value_for(s, "privacy.chat_retention_days", services.load_principal(s, u)) if u else -1
            expires = now + days * 86400 if days and days > 0 else None
            rows = []
            try:
                for o, path, mime in fetched:  # files first (slow), the write lock only for the rows (fast)
                    aid = "ma_" + uuid.uuid4().hex[:24]
                    size = await media_lib.seal_to_store(st, user_id, aid, path)
                    rows.append(MediaAsset(id=aid, user_id=user_id, job_id=job_id, kind=kind, model_id=model_id, mime=mime,
                                           size=size, width=o.get("width"), height=o.get("height"),
                                           duration=o.get("duration"), seed=o.get("seed"),
                                           prompt_enc=st.vault.encrypt(prompt, "media"), params_enc=params_enc or "",
                                           expires_at=expires))
                with st.db.session(write=True) as s:
                    s.add_all(rows)
            except BaseException:
                for r in rows:
                    remove_asset_file(st, user_id, r.id)
                raise
        else:
            purge_mem(st)
            if mem_bytes(st) + sum(len(d) for _, d, _ in fetched) > EPHEMERAL_CAP_BYTES:
                _finish(st, job_id, "failed", "Temporary storage for no-save results is full. Try again in a few minutes.")
                for o in outputs:
                    await _discard(ad, o)
                return
            for o, data, mime in fetched:
                _mem(st)["me_" + uuid.uuid4().hex[:24]] = {
                    "user_id": user_id, "job_id": job_id, "data": data, "mime": mime, "expires": now + EPHEMERAL_TTL,
                    "meta": {"kind": kind, "width": o.get("width"), "height": o.get("height"),
                             "duration": o.get("duration"), "seed": o.get("seed"), "created_at": now}}
    st.__dict__.setdefault("media_counters", {}).pop(f"fetch_fail:{job_id}", None)
    for o in outputs:  # the engine keeps no copy of the result file
        await _discard(ad, o)
    _finish(st, job_id, "completed")


async def _discard(ad, o) -> None:
    try:
        if o.get("discard_id"):
            await ad.media_discard(o["discard_id"])
    except AdapterError:
        log.warning("could not discard an engine-side result")


async def cancel(st, job_id: str) -> None:
    with st.db.session(write=True) as s:
        j = s.get(MediaJob, job_id)
        if j is None or j.status not in ACTIVE:
            return
        b = s.get(Backend, j.backend_id)
        ad, remote = adapter_for(b, st), j.remote_id
        j.cancel_requested = True
    await ad.media_cancel(remote)
    await refresh(st, job_id)


async def pump(st) -> None:
    """Background: advance every active job so results are collected even when nobody is polling."""
    while True:
        try:
            with st.db.session() as s:
                ids = [r for r in s.execute(select(MediaJob.id).where(MediaJob.status.in_(ACTIVE))).scalars()]
            for i in ids:
                await refresh(st, i)
        except Exception:  # noqa: BLE001
            log.exception("media pump failed")
        await asyncio.sleep(3)


def sweep(st) -> dict:
    """Retention for assets, plus housekeeping for orphans, no-save leftovers and stuck jobs."""
    now = time.time()
    purge_mem(st)
    removed = orphans = 0
    with st.db.session(write=True) as s:
        for a in s.execute(select(MediaAsset).where(MediaAsset.expires_at != None, MediaAsset.expires_at < now)).scalars().all():  # noqa: E711
            remove_asset_file(st, a.user_id, a.id)
            s.delete(a)
            removed += 1
        # a saved job's prompt and parameters expire with the retention window, even if it produced nothing
        for j in s.execute(select(MediaJob).where(MediaJob.expires_at != None, MediaJob.expires_at < now,  # noqa: E711
                                                  MediaJob.status.notin_(ACTIVE))).scalars().all():
            for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == j.id)).scalars().all():
                remove_asset_file(st, a.user_id, a.id)
                s.delete(a)
            s.delete(j)
            removed += 1
        # no-save job rows carry no content; drop them once their in-memory results are gone
        live_jobs = {v["job_id"] for v in _mem(st).values()}
        for j in s.execute(select(MediaJob).where(MediaJob.save == False, MediaJob.status.notin_(ACTIVE),  # noqa: E712
                                                  MediaJob.finished_at < now - EPHEMERAL_TTL)).scalars().all():
            if j.id not in live_jobs:
                s.delete(j)
        for j in s.execute(select(MediaJob).where(MediaJob.status.in_(ACTIVE), MediaJob.created_at < now - 12 * 3600)).scalars():
            j.status, j.error, j.finished_at = "failed", "The job never finished.", now
        known = {a for a in s.execute(select(MediaAsset.id)).scalars()}
        users = {u for u in s.execute(select(User.id)).scalars()}
    root = assets_dir(st.cfg)
    if root.exists():
        for udir in root.iterdir():
            if not udir.is_dir():
                continue
            for f in udir.iterdir():
                stale = f.suffix == ".tmp" and f.stat().st_mtime < now - 3600
                if stale or (f.suffix == ".bin" and f.name.split(".", 1)[0] not in known and f.stat().st_mtime < now - 3600):
                    f.unlink(missing_ok=True)
                    orphans += 1
            if udir.name not in users:
                for f in udir.iterdir():
                    f.unlink(missing_ok=True)
                    orphans += 1
                udir.rmdir()
    from . import media_lib
    orphans += media_lib.sweep_scratch(st)
    return {"expired_assets": removed, "orphan_files": orphans}


class AdmitError(Exception):
    """A generation was refused before it started: a limit, a setting or a permission. `code` and `status` are the HTTP form."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def admit(st, s, principal, user_id: str, save: bool, kind: str, n: int = 1) -> bool:
    """Server-side limits for ``n`` new generations at once (the Video/Image routes and Work Mode's engine tools share this one gate; Imajutsu starts up to
    four variations together and wants all of them admitted or none). Returns whether the result will be saved; raises AdmitError."""
    def val(key):
        return st.settings.value_for(s, key, principal)
    if not val(f"ui.{kind}_gen"):
        raise AdmitError(403, "disabled", f"{kind.capitalize()} generation is turned off for you by your administrator.")
    days = val("privacy.chat_retention_days")
    if not save and not (val("privacy.ephemeral_allowed") and principal.can("chat.ephemeral")):
        raise AdmitError(403, "ephemeral_forbidden", "No-save generation is not available to you.")
    if days == 0:
        save = False  # the organisation stores nothing; the result is only held briefly for download
    limit = val("media.max_concurrent_jobs")
    if count_active(s, user_id) + n > limit:
        free = max(0, limit - count_active(s, user_id))
        raise AdmitError(429, "busy", "You already have the maximum number of generations running. Wait for one to finish or cancel it." if n == 1 else
                         f"You can have {limit} generations queued or running at once and {free} {'is' if free == 1 else 'are'} free. Ask for {free} variation{'s' if free != 1 else ''} "
                         "or wait for a running one to finish.")
    daily = val("media.daily_generations")
    if daily and generations_today(s, user_id) + count_active(s, user_id) + n > daily:
        raise AdmitError(429, "daily_limit", f"Daily limit of {daily} generations reached.")
    cap = val("media.max_assets")
    if save and cap and count_assets(s, user_id) + n > cap:
        raise AdmitError(409, "gallery_full", f"Your gallery holds {cap} items, the maximum. Delete some to continue.")
    return save


def count_active(s, user_id: str) -> int:
    return s.execute(select(func.count()).select_from(MediaJob).where(MediaJob.user_id == user_id,
                                                                     MediaJob.status.in_(ACTIVE))).scalar() or 0


def count_assets(s, user_id: str) -> int:
    return s.execute(select(func.count()).select_from(MediaAsset).where(MediaAsset.user_id == user_id)).scalar() or 0


def generations_today(s, user_id: str) -> int:
    return s.execute(select(func.count()).select_from(UsageEvent).where(
        UsageEvent.user_id == user_id, UsageEvent.ts >= time.time() - 86400,
        UsageEvent.kind.in_(MEDIA_KINDS))).scalar() or 0
