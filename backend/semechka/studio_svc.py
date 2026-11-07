"""OpenJutsu Studio: projects, planning, preparing, generating and exporting. Routes live in routers/studio.py and only translate HTTP.

The rules this file keeps (docs/OPENJUTSU_STUDIO.md):
* what a model can do comes only from what the engine published for it (``Model.meta.media.profile``): unknown means unavailable, with the reason;
* nothing is dropped silently: every reference picture and every piece of subject information is either sent to the engine or listed as not used
  (or refused with a plain sentence);
* the person's media files are never changed: preparing and exporting make NEW assets (``origin`` prepared / export); a project never owns or
  deletes an asset;
* everything that arrives from the browser is validated here; ffmpeg only ever receives numbers that were checked.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import re
import time
from pathlib import Path

from sqlalchemy import select

from . import media_lib, media_svc, services, studio_plan
from .adapters import AdapterError
from .backends_svc import adapter_for
from .media_lib import MediaError
from .media_models import MediaAsset, MediaJob
from .models import Backend, Model
from .studio_models import StudioProject

SPEC_VERSION = 1
MAX_REFS = 200                     # sanity cap on pictures kept in one project (the engine uses only a few of them)
MAX_SPEC_BYTES = 256 * 1024
PREPARE_WAIT_S = 90.0              # generate waits this long for the prepared copy, then answers "preparing" and the caller asks again
ASPECT_IDS = ("original", "16:9", "9:16", "1:1", "4:3", "3:4", "custom")
RESOLUTION_IDS = ("original", "480p", "720p", "1080p", "1440p", "4k", "custom")
TREATMENTS = ("crop", "pad", "stretch")
KINDS = ("person", "vehicle", "animal", "object", "other")
STATUS_RANK = {"ready": 0, "experimental": 1, "not_ready": 2, "unavailable": 3}
MODE_LABELS = {
    "subject_replacement": "Subject replacement", "character_replacement": "Character replacement", "object_replacement": "Object replacement",
    "vehicle_replacement": "Vehicle replacement", "motion_transfer": "Motion transfer", "reference_to_video": "Reference to video",
    "video_restyling": "Video restyling", "advanced_editing": "Advanced editing"}
MODE_IDS = tuple(MODE_LABELS)


class StudioError(MediaError):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(status, code, message)
        self.extra = extra or {}


# ======================================================================================================================
# the spec (version 1)
# ======================================================================================================================
def default_spec() -> dict:
    return {"version": SPEC_VERSION, "source_asset": None, "trim": None, "crop": None,
            "frame": {"aspect": "original", "ratio": None, "lock": True, "treatment": "crop"},
            "resolution": {"preset": "original", "width": None, "height": None, "lock_aspect": True},
            "audio": "keep", "references": [], "mode": None,
            "subject": {"text": "", "kind": "person", "box": None, "points": []},
            "model": None, "settings": {}, "accept_experimental": False}


def _num(v, name: str, lo: float, hi: float, *, integer: bool = False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise StudioError(422, "invalid", f"{name} must be a number.")
    if not lo <= v <= hi:
        raise StudioError(422, "invalid", f"{name} must be between {lo:g} and {hi:g}.")
    return int(round(v)) if integer else float(v)


def _frac(v, name: str) -> float:
    return _num(v, name, 0.0, 1.0)


def _text(v, name: str, limit: int) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise StudioError(422, "invalid", f"{name} must be text.")
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
    if len(v) > limit:
        raise StudioError(422, "invalid", f"{name} is longer than {limit} characters.")
    return v


def asset_facts(st, s, user_id: str, aid, kind: str, what: str) -> MediaAsset:
    """The caller's saved library asset of ``kind``, else a plain 422. (A foreign id and a missing one read the same.)"""
    if not isinstance(aid, str) or not aid:
        raise StudioError(422, "invalid", f"{what} must be a library item.")
    if aid.startswith("me_"):
        raise StudioError(422, "ephemeral_asset", f"{what} is a temporary no-save file; add it to your library to use it in a project.")
    a = media_lib.owned(st, s, user_id, aid)
    if a is None:
        raise StudioError(422, "asset_missing", f"{what} is no longer in your library.")
    if a.kind != kind:
        raise StudioError(422, "invalid", f"{what} must be a {'video' if kind == 'video' else 'picture'}.")
    return a


def normalize_spec(st, s, user_id: str, raw, *, check_model: bool = True) -> dict:
    """Validate what the browser sent and return the version-1 spec. Unknown keys are ignored (and not stored); wrong values are refused."""
    if not isinstance(raw, dict):
        raise StudioError(422, "invalid", "The project settings must be an object.")
    if len(json.dumps(raw, default=str)) > MAX_SPEC_BYTES:
        raise StudioError(422, "invalid", "The project settings are too large.")
    d = default_spec()
    src = None
    sid = raw.get("source_asset")
    if sid is not None:
        src = asset_facts(st, s, user_id, sid, "video", "The source video")
        d["source_asset"] = src.id
    dur = float(src.duration or 0) if src else None
    t = raw.get("trim")
    if t is not None:
        if not isinstance(t, dict):
            raise StudioError(422, "invalid", "trim must have a start and an end.")
        a, b = _num(t.get("start"), "trim.start", 0, 6 * 3600), _num(t.get("end"), "trim.end", 0, 6 * 3600)
        if b - a < 0.1:
            raise StudioError(422, "invalid", "The selected part must be at least 0.1 s long.")
        if dur is not None:
            if a >= dur or b > dur + 0.05:
                raise StudioError(422, "invalid", f"The selected part must lie within the {dur:.1f} s video.")
            b = min(b, dur)
        d["trim"] = None if (dur is not None and a < 0.001 and b >= dur - 0.04) else {"start": round(a, 3), "end": round(b, 3)}
    c = raw.get("crop")
    if c is not None:
        if not isinstance(c, dict):
            raise StudioError(422, "invalid", "crop must have x, y, w and h.")
        x, y = _num(c.get("x"), "crop.x", 0, 100000, integer=True), _num(c.get("y"), "crop.y", 0, 100000, integer=True)
        w, h = _num(c.get("w"), "crop.w", 1, 100000, integer=True), _num(c.get("h"), "crop.h", 1, 100000, integer=True)
        if w < 16 or h < 16:
            raise StudioError(422, "invalid", "The crop is too small (at least 16 × 16 pixels).")
        if src is not None:
            if x + w > src.width or y + h > src.height:
                raise StudioError(422, "invalid", f"The crop reaches outside the {src.width} × {src.height} picture.")
            if x == 0 and y == 0 and w >= src.width and h >= src.height:
                c = None
        d["crop"] = {"x": x, "y": y, "w": w, "h": h} if c is not None else None
    f = raw.get("frame")
    if f is not None:
        if not isinstance(f, dict):
            raise StudioError(422, "invalid", "frame must be an object.")
        aspect = f.get("aspect", "original")
        if aspect not in ASPECT_IDS:
            raise StudioError(422, "invalid", "Pick one of the shapes: " + ", ".join(ASPECT_IDS) + ".")
        treatment = f.get("treatment", "crop")
        if treatment not in TREATMENTS:
            raise StudioError(422, "invalid", "The treatment must be crop, pad or stretch.")
        ratio = f.get("ratio")
        if aspect == "custom":
            ratio = _num(ratio, "frame.ratio", 0.2, 5.0)
        else:
            ratio = float(ratio) if isinstance(ratio, (int, float)) and not isinstance(ratio, bool) and 0.2 <= ratio <= 5 else None
        d["frame"] = {"aspect": aspect, "ratio": ratio, "lock": bool(f.get("lock", True)), "treatment": treatment}
    r = raw.get("resolution")
    if r is not None:
        if not isinstance(r, dict):
            raise StudioError(422, "invalid", "resolution must be an object.")
        preset = r.get("preset", "original")
        if preset not in RESOLUTION_IDS:
            raise StudioError(422, "invalid", "Pick one of the sizes: " + ", ".join(RESOLUTION_IDS) + ".")
        w = _num(r["width"], "resolution.width", studio_plan.MIN_SIDE, studio_plan.MAX_SIDE, integer=True) if r.get("width") is not None else None
        h = _num(r["height"], "resolution.height", studio_plan.MIN_SIDE, studio_plan.MAX_SIDE, integer=True) if r.get("height") is not None else None
        if preset == "custom" and w is None and h is None:
            raise StudioError(422, "invalid", "A custom size needs a width or a height.")
        d["resolution"] = {"preset": preset, "width": w, "height": h, "lock_aspect": bool(r.get("lock_aspect", True))}
    audio = raw.get("audio", "keep")
    if audio not in ("keep", "mute"):
        raise StudioError(422, "invalid", "audio must be keep or mute.")
    d["audio"] = audio
    refs = raw.get("references", [])
    if not isinstance(refs, list):
        raise StudioError(422, "invalid", "references must be a list.")
    if len(refs) > MAX_REFS:
        raise StudioError(422, "too_many_references", f"A project keeps at most {MAX_REFS} reference pictures.")
    for i, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise StudioError(422, "invalid", f"Reference {i + 1} is not valid.")
        asset_facts(st, s, user_id, ref.get("asset"), "image", f"Reference picture {i + 1}")
        subj = ref.get("subject")
        if subj is not None:
            subj = _num(subj, f"Reference {i + 1} target", 1, 64, integer=True)
        d["references"].append({"asset": ref["asset"], "active": bool(ref.get("active", True)), "subject": subj, "label": _text(ref.get("label"), "A label", 120)})
    mode = raw.get("mode")
    if mode is not None and (not isinstance(mode, str) or not re.fullmatch(r"[a-z][a-z0-9_]{1,39}", mode)):
        raise StudioError(422, "invalid", "That operation is not valid.")
    d["mode"] = mode
    sub = raw.get("subject")
    if sub is not None:
        if not isinstance(sub, dict):
            raise StudioError(422, "invalid", "subject must be an object.")
        kind = sub.get("kind", "person")
        if kind not in KINDS:
            raise StudioError(422, "invalid", "The kind of subject must be one of: " + ", ".join(KINDS) + ".")
        box = sub.get("box")
        if box is not None:
            if not (isinstance(box, list) and len(box) == 4):
                raise StudioError(422, "invalid", "The subject box needs x, y, width and height.")
            bx, by, bw, bh = (_frac(v, "The subject box") for v in box)
            if bw <= 0.005 or bh <= 0.005 or bx + bw > 1.0001 or by + bh > 1.0001:
                raise StudioError(422, "invalid", "The subject box must lie inside the first frame.")
            box = [round(bx, 5), round(by, 5), round(bw, 5), round(bh, 5)]
        pts = sub.get("points", [])
        if not isinstance(pts, list) or len(pts) > 32:
            raise StudioError(422, "invalid", "The subject points must be a list of at most 32.")
        pts = [{"x": round(_frac(p.get("x"), "A subject point"), 5), "y": round(_frac(p.get("y"), "A subject point"), 5), "positive": bool(p.get("positive", True))}
               for p in pts if isinstance(p, dict)]
        d["subject"] = {"text": _text(sub.get("text"), "The subject description", 500), "kind": kind, "box": box, "points": pts}
    model = raw.get("model")
    if model is not None:
        if not isinstance(model, str) or len(model) > 32:
            raise StudioError(422, "invalid", "That model is not valid.")
        if check_model and s.get(Model, model) is None:
            raise StudioError(422, "invalid", "That model does not exist.")
    d["model"] = model
    settings = raw.get("settings", {})
    if not isinstance(settings, dict) or len(settings) > 40:
        raise StudioError(422, "invalid", "settings must be an object with at most 40 entries.")
    for k, v in settings.items():
        if not isinstance(k, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,39}", k):
            raise StudioError(422, "invalid", f"'{str(k)[:40]}' is not a valid setting name.")
        if not (v is None or isinstance(v, (bool, int, float)) or (isinstance(v, str) and len(v) <= 20000)):
            raise StudioError(422, "invalid", f"The setting '{k}' must be a number, text or on/off.")
        d["settings"][k] = v
    d["accept_experimental"] = bool(raw.get("accept_experimental", False))
    return d


# ======================================================================================================================
# projects
# ======================================================================================================================
def _enc(st, obj) -> str:
    return st.vault.encrypt(json.dumps(obj), "media")


def _dec(st, blob: str | None, default):
    if not blob:
        return default
    try:
        v = json.loads(st.vault.decrypt(blob, "media"))
    except (ValueError, TypeError):
        return default
    return v if isinstance(v, type(default)) else default


def spec_of(st, p: StudioProject) -> dict:
    return {**default_spec(), **_dec(st, p.spec_enc, {})}


def project_kind(st, p: StudioProject) -> str:
    """"video" (OpenJutsu Studio; older specs carry no kind) or "image" (Imajutsu keeps its projects in the same table)."""
    return _dec(st, p.spec_enc, {}).get("kind") or "video"


def own_project(st, s, user_id: str, pid: str) -> StudioProject:
    p = s.get(StudioProject, pid) if isinstance(pid, str) and pid.startswith("sp_") else None
    if p is None or p.user_id != user_id or project_kind(st, p) != "video":
        raise StudioError(404, "not_found", "Not found.")
    return p


def source_json(st, s, user_id: str, spec: dict) -> dict | None:
    sid = spec.get("source_asset")
    a = s.get(MediaAsset, sid) if sid else None
    return media_lib.asset_json(st, a) if a is not None and a.user_id == user_id else None


def find_prepared(st, s, user_id: str, key: str) -> MediaAsset | None:
    """The prepared asset made for this cache key, if it is still in the library."""
    for a in s.execute(select(MediaAsset).where(MediaAsset.user_id == user_id, MediaAsset.model_id == "prepared")
                       .order_by(MediaAsset.created_at.desc())).scalars():
        if (media_lib.meta_of(st, a).get("prep") or {}).get("key") == key:
            return a
    return None


async def project_json(st, s, user, p: StudioProject, *, summary: bool = False) -> dict:
    spec = spec_of(st, p)
    state = _dec(st, p.state_enc, {})
    job_ids = [j for j in state.get("jobs", []) if isinstance(j, str)][:100]
    jobs = []
    for jid in job_ids:
        j = s.get(MediaJob, jid)
        if j is not None and j.user_id == user.id:
            jobs.append({"id": j.id, "status": j.status, "created_at": j.created_at, "finished_at": j.finished_at})
    out = {"id": p.id, "title": st.vault.decrypt(p.title_enc, "media") if p.title_enc else "", "created_at": p.created_at, "updated_at": p.updated_at,
           "source_asset": spec.get("source_asset"), "mode": spec.get("mode"), "jobs": jobs, "last_job": jobs[0] if jobs else None}
    src = s.get(MediaAsset, spec["source_asset"]) if spec.get("source_asset") else None
    out["thumb_asset"] = out["thumbnail"] = src.id if src is not None and src.user_id == user.id else None
    if summary:
        return out
    out["spec"] = spec
    out["source"] = source_json(st, s, user.id, spec)
    out["prepared_asset"] = None
    out["prepared_current"] = False
    if src is not None and src.user_id == user.id:
        try:
            pln = (await build_plan(st, s, user, spec, estimate=False))["plan"]
            key = pln["prepare"]["key"]
            if pln["prepare"]["mode"] == "none":
                out["prepared_asset"], out["prepared_current"] = out["source"], True
            else:
                a = find_prepared(st, s, user.id, key) if key else None
                out["prepared_asset"] = media_lib.asset_json(st, a) if a is not None else None
                out["prepared_current"] = a is not None
        except (StudioError, MediaError):
            pass
    return out


def create_project(st, s, user, source_asset: str, title: str | None) -> StudioProject:
    spec = normalize_spec(st, s, user.id, {"source_asset": source_asset})
    if not spec["source_asset"]:
        raise StudioError(422, "invalid", "Choose a source video.")
    name = _text(title, "The title", 120)
    if not name:
        a = s.get(MediaAsset, spec["source_asset"])
        name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", media_lib.display_name(media_lib.meta_of(st, a), "video", a.id)) or "Untitled project"
    p = StudioProject(user_id=user.id, title_enc=st.vault.encrypt(name, "media"), spec_enc=_enc(st, spec), state_enc=_enc(st, {"jobs": []}))
    s.add(p)
    s.flush()
    return p


def update_project(st, s, user, p: StudioProject, raw_spec, expected_updated_at, title: str | None) -> StudioProject:
    if expected_updated_at is not None:
        if isinstance(expected_updated_at, bool) or not isinstance(expected_updated_at, (int, float)) or abs(float(expected_updated_at) - p.updated_at) > 1e-4:
            raise StudioError(409, "stale", "This project was changed somewhere else. Reload it before saving.", {"updated_at": p.updated_at})
    if raw_spec is not None:
        p.spec_enc = _enc(st, normalize_spec(st, s, user.id, raw_spec))
    if title is not None:
        name = _text(title, "The title", 120)
        if name:
            p.title_enc = st.vault.encrypt(name, "media")
    p.updated_at = max(time.time(), p.updated_at + 1e-3)
    return p


# ======================================================================================================================
# the plan
# ======================================================================================================================
async def source_facts(st, a: MediaAsset) -> dict:
    """{"rotation": degrees, "keyframes": [seconds]} of a stored video, read once with ffprobe and kept sealed next to the asset."""
    got = media_lib.read_sidecar(st, a, "facts")
    if got:
        with contextlib.suppress(ValueError):
            d = json.loads(got)
            if isinstance(d, dict) and "keyframes" in d:
                return d
    _, fp = media_lib.need_ffmpeg()
    meta = media_lib.meta_of(st, a)
    media = meta.get("media") or {}
    demux = media.get("demuxer") or media_lib.default_demuxer(a.mime)
    with media_lib.materialise(st, [a]) as [(fh, *_)]:
        fh.close()
        src = Path(fh.name)
        base = [fp, "-v", "error", "-protocol_whitelist", "file", "-f", demux, "-print_format", "json"]
        code, out = await media_lib._capture([*base, "-select_streams", "v:0", "-show_entries", "stream_side_data=rotation:stream_tags=rotate:format=start_time",
                                              "-i", f"file:{src}"], 60)
        rot, start = 0, 0.0
        if code == 0:
            with contextlib.suppress(ValueError, TypeError, KeyError):
                d = json.loads(out)
                v = (d.get("streams") or [{}])[0]
                for sd in v.get("side_data_list") or []:
                    if "rotation" in sd:
                        rot = round(float(sd["rotation"]))
                if not rot and (v.get("tags") or {}).get("rotate"):
                    rot = -int(v["tags"]["rotate"])
                start = float((d.get("format") or {}).get("start_time") or 0)
        code, out = await media_lib._capture([*base, "-select_streams", "v:0", "-skip_frame", "nokey", "-show_entries", "frame=pts_time,pkt_pts_time",
                                              "-i", f"file:{src}"], 240)
        kfs: list[float] = []
        if code == 0:
            with contextlib.suppress(ValueError, TypeError):
                for fr in json.loads(out).get("frames") or []:
                    v = fr.get("pts_time", fr.get("pkt_pts_time"))
                    if v is not None:
                        kfs.append(round(float(v) - start, 4))
        facts = {"rotation": rot % 360, "keyframes": sorted(set(kfs))[:50000], "start_time": start}
    with contextlib.suppress(OSError, ValueError):
        media_lib.write_sidecar(st, a.user_id, a.id, "facts", json.dumps(facts).encode())
    return facts


def pick_profile_model(st, s, principal, spec: dict):
    """(model, backend, profile) the spec names, or else the first editing profile the person may use; (None, None, None) when there is none."""
    rows = services.accessible_models(s, principal, st, "video")
    if spec.get("model"):
        rows = [(m, b) for m, b in rows if m.id == spec["model"]]
    for m, b in rows:
        pr = media_svc.profile_info(m)
        if pr:
            return m, b, pr
    return None, None, None


async def build_plan(st, s, user, spec: dict, *, estimate: bool = True) -> dict:
    """The plan for a spec plus the things the caller needs next ({"plan", "source", "model", "backend", "profile"}). No side effects (key frames are cached)."""
    if not spec.get("source_asset"):
        raise StudioError(422, "invalid", "Choose a source video first.")
    a = asset_facts(st, s, user.id, spec["source_asset"], "video", "The source video")
    meta = media_lib.meta_of(st, a)
    media = meta.get("media") or {}
    principal = services.load_principal(s, user)
    m, b, prof = pick_profile_model(st, s, principal, spec)
    limits = (prof or {}).get("limits")
    src = {"id": a.id, "width": a.width, "height": a.height, "duration": a.duration, "rotation": media.get("rotation"), "fps": media.get("fps"),
           "audio": bool(media.get("audio")), "vcodec": media.get("codec"), "acodec": media.get("audio_codec"), "mime": a.mime}
    facts = None
    if src["rotation"] is None:  # an upload from before rotation was recorded
        facts = await source_facts(st, a)
        src["rotation"] = facts["rotation"]
    pln = studio_plan.plan(src, spec, limits)
    if pln["trim"].get("needs_keyframes"):
        facts = facts or await source_facts(st, a)
        pln = studio_plan.plan(src, spec, limits, keyframes=facts["keyframes"])
    if estimate and pln.get("processing") and m is not None and b is not None and not pln["errors"]:
        await _estimate(st, b, m, pln, spec)
    return {"plan": pln, "source": a, "model": m, "backend": b, "profile": prof}


async def _estimate(st, b: Backend, m: Model, pln: dict, spec: dict) -> None:
    """Ask the engine what it measured for this size; the answer only ever adds to the plan (memory, time), never replaces its numbers."""
    proc = pln["processing"]
    op = next((x.get("operation") for x in (media_svc.profile_info(m) or {}).get("modes") or [] if x.get("id") == spec.get("mode")), None)
    try:
        ad = adapter_for(b, st)
        code, d = await asyncio.wait_for(ad.media_estimate(m.real_id.split(":", 1)[1], {
            "operation": op, "width": pln["prepared"]["width"], "height": pln["prepared"]["height"], "short_edge": proc["short_edge"],
            "seconds": pln["trim"]["seconds"], "refs": sum(1 for r in spec.get("references") or [] if r.get("active"))}), 6)
    except (AdapterError, TimeoutError, AttributeError):
        return
    if code != 200 or not isinstance(d, dict):
        return
    mem, sec = d.get("memory") or {}, d.get("seconds") or {}
    if mem.get("basis") in ("measured", "extrapolated"):
        proc["memory"] = {"peak_mb": mem.get("peak_mb"), "basis": mem["basis"], "note": media_svc.clean_text(mem.get("note"))}
    if sec.get("basis") in ("measured", "extrapolated") and sec.get("estimate"):
        pln["seconds"] = {"estimate": sec["estimate"], "basis": sec["basis"]}


# ======================================================================================================================
# preparing
# ======================================================================================================================
def _check_quota(ctx_st, s, principal, user_id: str) -> None:
    quota = ctx_st.settings.value_for(s, "media.max_assets", principal)
    if quota and media_svc.count_assets(s, user_id) >= quota:
        raise StudioError(409, "gallery_full", f"Your library holds {quota} items, the maximum. Delete some to make room.")


async def prepare(st, s, user, spec: dict) -> dict:
    """Make (or find) the prepared copy of the source for this spec. -> {"task": id|None, "reused": bool, "asset": json|None, "plan"}"""
    r = await build_plan(st, s, user, spec, estimate=False)
    pln, a = r["plan"], r["source"]
    if pln["errors"]:
        raise StudioError(422, "plan_errors", " ".join(pln["errors"]), {"plan": pln})
    if pln["prepare"]["mode"] == "none":
        return {"task": None, "reused": True, "asset": media_lib.asset_json(st, a), "identity": True, "plan": pln}
    key = pln["prepare"]["key"]
    existing = find_prepared(st, s, user.id, key)
    if existing is not None:
        return {"task": None, "reused": True, "asset": media_lib.asset_json(st, existing), "plan": pln}
    principal = services.load_principal(s, user)
    _check_quota(st, s, principal, user.id)
    days = st.settings.value_for(s, "privacy.chat_retention_days", principal)
    cap = int(st.settings.value_for(s, "media.upload_max_mb", principal)) * 2**20
    expires = time.time() + days * 86400 if days and days > 0 else None
    es = studio_plan.edit_spec_from(pln)
    stem = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", media_lib.display_name(media_lib.meta_of(st, a), "video", a.id))[:100] or "video"
    s.commit()
    t = await media_lib.start_edit(st, user_id=user.id, aid=a.id, spec=es, name=f"{stem} (prepared).mp4", expires_at=expires, ephemeral=False, cap=cap,
                                   origin="prepared", key=key,
                                   extra_meta={"prep": {"key": key, "source": a.id, "ops": pln["prepare"]["ops"], "mode": pln["prepare"]["mode"], "version": 1}})
    return {"task": t.id, "reused": False, "asset": None, "task_status": media_lib.task_out(t), "plan": pln}


# ======================================================================================================================
# capabilities
# ======================================================================================================================
def mode_table(prof: dict | None) -> dict[str, dict]:
    return {m["id"]: m for m in (prof or {}).get("modes") or [] if isinstance(m, dict) and isinstance(m.get("id"), str)}


def worst(*statuses: str) -> str:
    return max(statuses, key=lambda x: STATUS_RANK.get(x, 3))


def mode_status(prof: dict, mode: dict) -> tuple[str, str]:
    """(status, reason) of one mode on one profile: the mode's own verdict, tightened by the operation's and by the profile's readiness."""
    if not prof.get("ready"):
        return "not_ready", prof.get("reason") or "The engine says this is not ready."
    st_ = mode.get("status") if mode.get("status") in STATUS_RANK else "unavailable"
    reason = mode.get("reason") or ""
    opst = (prof.get("operation_status") or {}).get(mode.get("operation")) or {}
    if opst.get("status") in STATUS_RANK and STATUS_RANK[opst["status"]] > STATUS_RANK[st_]:
        st_, reason = opst["status"], opst.get("reason") or reason
    return st_, media_svc.clean_text(reason) or ("" if st_ == "ready" else "The engine gives no reason.")


def slim_validated(v) -> dict | None:
    """The evidence that an operation was really run, reduced to what a person needs (when, which job, what kind of subject)."""
    return {k: v[k] for k in ("at", "job", "subject_kind") if isinstance(v, dict) and k in v} or None


def editing_rows(st, s, principal):
    return [(m, b, pr) for m, b in services.accessible_models(s, principal, st, "video") if (pr := media_svc.profile_info(m))]


def capabilities(st, s, principal) -> dict:
    vmax = st.settings.value_for(s, "media.video_max_seconds", principal)
    rows = services.accessible_models(s, principal, st, "video")
    by_mode: dict[str, dict] = {k: {"id": k, "label": v, "operation": None, "fixed_inputs": {}, "subject_kinds": [], "needs": None, "models": []} for k, v in MODE_LABELS.items()}
    models = []
    limits_all: dict = {}
    for m, b in rows:
        info = media_svc.media_info(m)
        prof = media_svc.profile_info(m)
        entry = {"model_id": m.id, "alias": m.alias, "family": (prof or {}).get("family") or (m.meta or {}).get("architecture"),
                 "kind": "editing_profile" if prof else "generator", "modes": [], "unsupported": []}
        table = mode_table(prof)
        if prof and not table:
            why = "This engine does not say which editing operations it can do (it predates OpenJutsu Studio), so none is offered."
        elif not prof:
            gen = ", ".join(info.get("modes") or []) or "video"
            why = f"{m.alias} makes video from a prompt ({gen}); it cannot edit a video you give it."
        else:
            why = ""
        for mid in list(by_mode):
            mode = table.get(mid)
            if mode is not None and mode.get("profile") and prof and prof.get("ready") and mode["profile"] != prof.get("id"):
                entry["unsupported"].append({"mode": mid, "reason": f"{MODE_LABELS.get(mid, mid)} runs on the engine's '{mode['profile']}' profile, not on {m.alias}."})
                continue
            if mode is None:
                reason = why or f"{m.alias} does not offer {MODE_LABELS.get(mid, mid).lower()}."
                entry["unsupported"].append({"mode": mid, "reason": reason})
                continue
            status, reason = mode_status(prof, mode)
            mrow = by_mode[mid]
            mrow["operation"] = mrow["operation"] or mode.get("operation")
            if not mrow["fixed_inputs"]:
                mrow["fixed_inputs"] = mode.get("fixed_inputs") or {}
            mrow["subject_kinds"] = sorted(set(mrow["subject_kinds"]) | set(mode.get("subject_kinds") or []))
            mrow["needs"] = mrow["needs"] or mode.get("needs")
            mrow["models"].append({"model_id": m.id, "alias": m.alias, "family": entry["family"], "status": status, "reason": reason,
                                   "needs": mode.get("needs"), "reference_policy": ((prof.get("reference_policy") or {}).get(mode.get("operation"))),
                                   "limits": prof.get("limits"), "subject_selection": prof.get("subject_selection"), "operations": prof.get("operations") or [],
                                   "validated": slim_validated(mode.get("validated"))})
            if status in ("ready", "experimental"):
                entry["modes"].append(mid)
            else:
                entry["unsupported"].append({"mode": mid, "reason": reason or "Not available."})
        if prof and prof.get("limits") and not limits_all.get("resolutions"):
            limits_all = {"resolutions": (prof["limits"] or {}).get("resolutions"), "max_source_seconds": (prof["limits"] or {}).get("max_source_seconds")}
        models.append(entry)
    modes = []
    for mid, mrow in by_mode.items():
        sts = [x["status"] for x in mrow["models"]]
        if sts:
            best = min(sts, key=lambda x: STATUS_RANK.get(x, 3))
            mrow["status"] = best
            reasons = [x["reason"] for x in mrow["models"] if x["status"] == best and x["reason"]]
            mrow["reason"] = reasons[0] if reasons else ""
        else:
            mrow["status"] = "unavailable"
            mrow["reason"] = "No model available to you offers this." if rows else "No video model is available to you."
        modes.append(mrow)
    return {"modes": modes, "models": models,
            "limits": {"video_max_seconds": vmax, "upload_max_mb": st.settings.value_for(s, "media.upload_max_mb", principal),
                       "max_assets": st.settings.value_for(s, "media.max_assets", principal), "max_references": MAX_REFS, **limits_all}}


def availability(st, s, principal, video_state: dict) -> dict:
    """ui.operations.openjutsu: may this person open the Studio at all (an editing profile they may use has a ready or experimental operation)."""
    if not video_state.get("enabled"):
        return {"enabled": False, "reason": video_state.get("reason") or "Video is not available to you."}
    rows = editing_rows(st, s, principal)
    if not rows:
        return {"enabled": False, "reason": "No video-editing model (OpenJutsu) is available to you."}
    why = ""
    for _m, _b, pr in rows:
        if not pr.get("ready"):
            why = why or media_svc.clean_text(pr.get("reason")) or "The editing model is not ready on the server."
            continue
        table = mode_table(pr)
        if not table:
            why = why or "The editing model does not say which operations it can do."
            continue
        if any(mode_status(pr, md)[0] in ("ready", "experimental") for md in table.values()):
            return {"enabled": True, "reason": ""}
        why = why or "None of the editing operations is available on this engine yet."
    return {"enabled": False, "reason": why}


# ======================================================================================================================
# generating
# ======================================================================================================================
def _plain(msg) -> str:
    return media_svc.clean_text(msg)


def reference_accounting(spec: dict, policy: dict | None, op: str) -> tuple[list[dict], list[dict]]:
    """(sent, not_used). Only the ACTIVE pictures, in the project's order, up to what the operation can use, go to the engine; everything else is listed with why."""
    pol = policy or {"min": 0, "active_max": 64}
    amax = int(pol.get("active_max", 64))
    sent: list[dict] = []
    not_used: list[dict] = []
    for i, r in enumerate(spec.get("references") or []):
        if not r.get("active"):
            not_used.append({"asset": r["asset"], "position": i + 1, "reason": "Switched off in the project."})
        elif len(sent) >= amax:
            not_used.append({"asset": r["asset"], "position": i + 1,
                             "reason": f"{op} uses {amax} picture{'s' if amax != 1 else ''}; this one comes after them." if amax else f"{op} does not use reference pictures."})
        else:
            sent.append({"asset": r["asset"], "position": i + 1, "label": r.get("label") or "", "subject": r.get("subject")})
    return sent, not_used


TEXT_KEYS = {"swap": ("target", "prompt"), "check_mask": ("target",), "object_edit": ("object_target",), "advanced_swap": ("roles",)}


def build_inputs(spec: dict, mode: dict, prof: dict, plan: dict, sent: list[dict], accepted: bool, mode_status_: str) -> tuple[dict, dict]:
    """The plain (non-file) inputs for the engine and the accounting of the subject. Raises StudioError(422) for anything the engine cannot take."""
    schema = prof.get("inputs_schema") or {}
    op = mode["operation"]
    sel = prof.get("subject_selection") or {}
    needs = mode.get("needs") or {}
    inputs: dict = {}
    if "operation" in schema:
        inputs["operation"] = op
    inputs.update(mode.get("fixed_inputs") or {})
    sub = spec.get("subject") or {}
    text = (sub.get("text") or "").strip()
    sent_fields, not_sent = [], []
    if needs.get("subject_text") or text:
        if needs.get("subject_text") and not text:
            raise StudioError(422, "subject_missing", "Say what should be replaced in the video (for example 'the man in the red jacket').")
        keys = [k for k in TEXT_KEYS.get(op, ()) if k in schema and (not (schema[k].get("operations")) or op in schema[k]["operations"])]
        if op == "advanced_swap":
            lines = [(r["label"] or text) for r in sent] if sent else [text]
            if keys:
                inputs["roles"] = "\n".join(lines)[:int(schema["roles"].get("max_length", 800))]
                sent_fields.append("text")
        elif keys:
            for k in keys:
                inputs[k] = text
            sent_fields.append("text")
        if text and not keys:
            raise StudioError(422, "subject_unsupported", f"This engine has no field for describing the subject of {mode.get('label') or op}.")
    for field, key, label in (("box", "subject_box", "A box around the subject"), ("points", "subject_points", "Clicked points on the subject")):
        val = sub.get(field)
        if not val:
            continue
        if not sel.get(field) or key not in schema:
            raise StudioError(422, "subject_unsupported", f"{label} is not supported by this engine ({_plain(sel.get('note') or 'it takes the description only')}). "
                                                              "Remove it, or describe the subject in words.")
        inputs[key] = val
        sent_fields.append(field)
    if sub.get("kind"):
        if "subject_kind" in schema:
            inputs["subject_kind"] = sub["kind"]
            sent_fields.append("kind")
        else:
            not_sent.append({"field": "kind", "reason": "This engine has no field for the kind of subject; the description is used on its own."})
    # processing size
    proc = plan.get("processing")
    if not proc:
        raise StudioError(422, "no_size", "The size the model works at is not known; choose an editing model first.")
    edge = proc["short_edge"]
    named = "resolution" in schema and f"{edge}p" in [str(v) for v in schema["resolution"].get("values", [])]
    if "short_edge" in schema:
        inputs["short_edge"] = edge
    if named:
        inputs["resolution"] = f"{edge}p"      # said both ways so the engine's default size can never disagree with the plan
    if "short_edge" not in schema and not named:
        raise StudioError(422, "no_size", f"This engine cannot be told to work at {edge}p.")
    if "accept_experimental" in schema and (mode_status_ == "experimental" or accepted):
        inputs["accept_experimental"] = True
    for k, v in (spec.get("settings") or {}).items():
        if k in inputs:
            continue  # what the Studio decided wins over a stray setting of the same name
        if v is not None and v != "":
            inputs[k] = v
    return inputs, {"text": text, "kind": sub.get("kind"), "box": sub.get("box"), "points": sub.get("points") or [], "sent": sent_fields, "not_sent": not_sent}


async def generate(st, s, user, project: StudioProject, body: dict) -> dict:
    principal = services.load_principal(s, user)
    spec = spec_of(st, project)
    if isinstance(body.get("spec"), dict):  # the page sends what is on screen; it becomes the project's saved state too
        spec = normalize_spec(st, s, user.id, body["spec"])
        with st.db.session(write=True) as ws:
            pr = ws.get(StudioProject, project.id)
            pr.spec_enc = _enc(st, spec)
            pr.updated_at = max(time.time(), pr.updated_at + 1e-3)
    accepted = bool(body.get("accept_experimental", spec.get("accept_experimental")))
    if not spec.get("source_asset"):
        raise StudioError(422, "invalid", "Choose a source video first.")
    if not spec.get("model"):
        raise StudioError(422, "no_model", "Choose a model first.")
    if not spec.get("mode"):
        raise StudioError(422, "no_mode", "Choose what you want to do (for example subject replacement) first.")
    m = s.get(Model, spec["model"])
    b = s.get(Backend, m.backend_id) if m else None
    prof = media_svc.profile_info(m) if m else None
    if m is None or b is None or not prof:
        raise StudioError(422, "no_model", "That model is not an editing model.")
    ok, why = services.model_access(s, principal, m, b, st, "video")
    if not ok:
        raise StudioError(403, "model_forbidden", why)
    table = mode_table(prof)
    mode = table.get(spec["mode"])
    if mode is None:
        why = "this engine does not say which operations it can do" if not table else f"{m.alias} does not offer {MODE_LABELS.get(spec['mode'], spec['mode']).lower()}"
        raise StudioError(422, "mode_unsupported", f"{MODE_LABELS.get(spec['mode'], spec['mode'])} cannot be run: {why}.")
    if mode.get("profile") and mode["profile"] != prof.get("id"):
        raise StudioError(422, "mode_unavailable", f"{MODE_LABELS.get(spec['mode'], spec['mode'])} runs on the engine's '{mode['profile']}' profile, not on {m.alias}. "
                                                   "Choose the model that offers it.")
    status, reason = mode_status(prof, mode)
    label = mode.get("label") or MODE_LABELS.get(spec["mode"], spec["mode"])
    if status in ("unavailable", "not_ready"):
        raise StudioError(422, "mode_unavailable", f"{label} is not available on {m.alias}: {reason}")
    if status == "experimental" and not accepted:
        raise StudioError(422, "experimental_not_accepted", f"{label} is experimental on {m.alias} ({reason}). Turn on 'run an experimental operation' to go ahead.")
    op = mode["operation"]
    # references
    sent, not_used = reference_accounting(spec, (prof.get("reference_policy") or {}).get(op), label)
    pol = (prof.get("reference_policy") or {}).get(op) or {"min": 0}
    if len(sent) < int(pol.get("min", 0)):
        n = int(pol["min"])
        raise StudioError(422, "references_missing", f"{label} needs at least {n} reference picture{'s' if n != 1 else ''} switched on; you have {len(sent)}.")
    r = await build_plan(st, s, user, spec, estimate=False)
    pln = r["plan"]
    if pln["errors"]:
        raise StudioError(422, "plan_errors", " ".join(pln["errors"]), {"plan": pln})
    needs_video = (mode.get("needs") or {}).get("video", True)
    inputs, subject_acc = build_inputs(spec, mode, prof, pln, sent, accepted, status)
    pol_sizes = ((prof.get("limits") or {}).get("resolutions") or {})
    if pol_sizes.get("max_short_edge") and pln["processing"]["short_edge"] > pol_sizes["max_short_edge"]:
        raise StudioError(422, "no_size", "The chosen size is above what the model can process.")
    # the prepared video
    prepared = None
    if needs_video:
        prep = await prepare(st, s, user, spec)
        if prep["task"]:
            t = media_lib.tasks(st).get(prep["task"])
            deadline = time.monotonic() + PREPARE_WAIT_S
            while t is not None and t.status == "running" and time.monotonic() < deadline:
                await asyncio.sleep(0.15)
            if t is None or t.status == "running":
                raise StudioError(202, "preparing", "The video is still being prepared. Try again in a moment.", {"task": prep["task"]})
            if t.status != "completed":
                raise StudioError(422, "prepare_failed", t.error or "The video could not be prepared.")
            prep["asset"] = t.asset
        s.expire_all()
        prepared = s.get(MediaAsset, prep["asset"]["id"])
        if prepared is None:
            raise StudioError(422, "asset_missing", "The prepared video is no longer in your library.")
    refs_rows = [asset_facts(st, s, user.id, x["asset"], "image", f"Reference picture {x['position']}") for x in sent]
    files: dict[str, list[tuple[str, int]]] = {}
    if prepared is not None:
        files["video"] = [(prepared.mime, prepared.size)]
    if refs_rows:
        files["refs"] = [(a.mime, a.size) for a in refs_rows]
    try:
        clean = media_svc.validate_profile_inputs(m, inputs, files, int(st.settings.value_for(s, "media.profile_upload_max_mb", principal)) * 2**20)
    except media_svc.ParamError as e:
        raise StudioError(422, "invalid", str(e)) from e
    try:
        save = media_svc.admit(st, s, principal, user.id, True, "video")
    except media_svc.AdmitError as e:
        raise StudioError(e.status, e.code, e.message) from e
    days = st.settings.value_for(s, "privacy.chat_retention_days", principal)
    accounting = {"sent_to_engine": sent, "not_used": not_used, "subject": subject_acc, "operation": op, "mode": spec["mode"],
                  "inputs_sent": {k: v for k, v in clean.items()}}
    studio = {"version": 1, "project_id": project.id, "mode": spec["mode"], "operation": op, "subject": spec["subject"],
              "references": spec["references"], "prepared_asset": prepared.id if prepared is not None else None, "source_asset": spec["source_asset"],
              "requested_output": pln["output"]["requested"], "plan": pln, "accounting": accounting, "model": m.id}
    s.commit()
    uploads: dict[str, list] = {}
    pick = ([prepared] if prepared is not None else []) + refs_rows
    try:
        with media_lib.materialise(st, pick) as opened:
            it = iter(opened)
            if prepared is not None:
                fh, mime, nm, _k = next(it)
                uploads["video"] = [(fh, mime, nm)]
            for _x in refs_rows:
                fh, mime, nm, _k = next(it)
                uploads.setdefault("refs", []).append((fh, mime, nm))
            jid = await media_svc.submit_profile(st, user, m, b, clean, uploads, save, time.time() + days * 86400 if days and days > 0 else None,
                                                 origin="studio", sources={"video": [prepared.id] if prepared is not None else [], "refs": [x["asset"] for x in sent]},
                                                 extra_params={"studio": studio})
    except AdapterError as e:
        raise StudioError(422 if e.status == 422 else 502, e.kind, str(e)) from e
    with st.db.session(write=True) as ws:
        pr = ws.get(StudioProject, project.id)
        if pr is not None:
            state = _dec(st, pr.state_enc, {})
            state["jobs"] = [jid, *[x for x in state.get("jobs", []) if x != jid]][:100]
            pr.state_enc = _enc(st, state)
            pr.updated_at = max(time.time(), pr.updated_at + 1e-3)
    await media_svc.refresh(st, jid)
    s.commit()          # end the read snapshot taken while the files were opened, so the new job row is visible
    job = media_svc.job_out(st, s, principal, s.get(MediaJob, jid))
    return {"job": jid, "job_detail": job, "prepared_asset": media_lib.asset_json(st, prepared) if prepared is not None else None, "accounting": accounting,
            "plan": pln, "forced_unsaved": not save}


# ======================================================================================================================
# jobs and exports
# ======================================================================================================================
def _job_assets(st, s, jid: str) -> list[dict]:
    return [{"id": a.id, "kind": a.kind, "mime": a.mime, "width": a.width, "height": a.height, "duration": a.duration, "size": a.size,
             "origin": media_lib.origin_of(st, a)} for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == jid).order_by(MediaAsset.created_at)).scalars()]


def _exports_by_job(st, s, user_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for a in s.execute(select(MediaAsset).where(MediaAsset.user_id == user_id, MediaAsset.model_id == "export").order_by(MediaAsset.created_at)).scalars():
        ex = media_lib.meta_of(st, a).get("export") or {}
        if ex.get("job"):
            out.setdefault(ex["job"], []).append({"id": a.id, "width": a.width, "height": a.height, "scaling": ex.get("scaling"), "fit": (ex.get("requested") or {}).get("fit"),
                                                  "created_at": a.created_at})
    return out


def list_jobs(st, s, user, limit: int = 100) -> list[dict]:
    models = {m.id: m for m in s.execute(select(Model)).scalars()}
    exports = _exports_by_job(st, s, user.id)
    out = []
    for j in s.execute(select(MediaJob).where(MediaJob.user_id == user.id, MediaJob.kind == "video").order_by(MediaJob.created_at.desc()).limit(400)).scalars():
        studio = studio_block(st, j)
        if studio is None:
            continue
        m = models.get(j.model_id)
        out.append({"job": j.id, "project_id": studio.get("project_id"), "status": j.status, "mode": studio.get("mode"), "operation": studio.get("operation"),
                    "model": m.alias if m else None, "model_id": j.model_id, "created_at": j.created_at, "finished_at": j.finished_at,
                    "progress": j.progress, "progress_text": j.progress_text, "error": j.error, "outputs": _job_assets(st, s, j.id), "exports": exports.get(j.id, []),
                    "studio": studio})
        if len(out) >= limit:
            break
    return out


def studio_block(st, j: MediaJob) -> dict | None:
    if not j.params_enc:
        return None
    try:
        d = json.loads(st.vault.decrypt(j.params_enc, "media"))
    except (ValueError, TypeError):
        return None
    sb = d.get("studio") if isinstance(d, dict) else None
    return sb if isinstance(sb, dict) else None


async def export(st, s, user, jid: str, body: dict) -> dict:
    j = s.get(MediaJob, jid)
    if j is None or j.user_id != user.id:
        raise StudioError(404, "not_found", "Not found.")
    if j.status != "completed":
        raise StudioError(409, "not_finished", "The result can be exported once the generation has finished.")
    src = next((a for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == jid, MediaAsset.user_id == user.id, MediaAsset.kind == "video")
                                     .order_by(MediaAsset.created_at)).scalars()), None)
    if src is None:
        raise StudioError(404, "no_result", "The result of this generation is no longer in your library.")
    w = _num(body.get("width"), "width", studio_plan.MIN_SIDE, studio_plan.MAX_SIDE, integer=True)
    h = _num(body.get("height"), "height", studio_plan.MIN_SIDE, studio_plan.MAX_SIDE, integer=True)
    fit = body.get("fit", "scale")
    if fit not in ("scale", "pad"):
        raise StudioError(422, "invalid", "fit must be scale or pad.")
    if w % 2 or h % 2:
        raise StudioError(422, "invalid", "Width and height must be even numbers (video needs them).")
    if w * h > studio_plan.MAX_PIXELS:
        raise StudioError(422, "invalid", "That size is larger than 8K.")
    nw, nh = src.width, src.height
    if (w, h) == (nw, nh):
        raise StudioError(422, "nothing_to_do", f"The result is already {nw}×{nh}.")
    if fit == "scale" and abs((w / h) / (nw / nh) - 1) > 0.012:
        raise StudioError(422, "shape_differs", f"{w}×{h} has a different shape from the {nw}×{nh} result. Choose a size with the same shape, or use 'pad' to add black bars.")
    scaling = "upscale" if min(w, h) > min(nw, nh) else "downscale" if min(w, h) < min(nw, nh) else "none"
    if fit == "pad":  # the picture is scaled to fit inside w×h and centred on black
        f = min(w / nw, h / nh)
        iw, ih = max(2, int(nw * f) // 2 * 2), max(2, int(nh * f) // 2 * 2)
        es = media_lib.EditSpec(scale=(iw, ih), pad={"w": w, "h": h, "x": ((w - iw) // 2) // 2 * 2, "y": ((h - ih) // 2) // 2 * 2}, force=True, audio_copy=True, scale_first=True)
        scaling = "upscale" if f > 1 else "downscale" if f < 1 else "none"
    else:
        es = media_lib.EditSpec(scale=(w, h), force=True, audio_copy=True)
    principal = services.load_principal(s, user)
    _check_quota(st, s, principal, user.id)
    days = st.settings.value_for(s, "privacy.chat_retention_days", principal)
    cap = int(st.settings.value_for(s, "media.upload_max_mb", principal)) * 2**20
    studio = studio_block(st, j) or {}
    s.commit()
    t = await media_lib.start_edit(st, user_id=user.id, aid=src.id, spec=es, name=f"result {w}x{h}.mp4", expires_at=time.time() + days * 86400 if days and days > 0 else None,
                                   ephemeral=False, cap=cap, origin="export",
                                   extra_meta={"export": {"job": jid, "project_id": studio.get("project_id"), "requested": {"width": w, "height": h, "fit": fit},
                                                          "native": {"width": nw, "height": nh}, "scaling": scaling}})
    return {"task": t.id, "task_status": media_lib.task_out(t), "scaling": scaling, "native": {"width": nw, "height": nh}, "requested": {"width": w, "height": h, "fit": fit},
            "note": {"upscale": f"The model made {nw}×{nh}; {w}×{h} is that picture scaled up (no new detail).",
                     "downscale": f"The model made {nw}×{nh}; {w}×{h} is that picture scaled down.",
                     "none": f"The picture is not scaled; bars are added to reach {w}×{h}."}[scaling]}
