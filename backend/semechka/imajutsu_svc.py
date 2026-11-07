"""Imajutsu: image generation and editing on the Sarness Engine's native image runtime. Routes live in routers/imajutsu.py and only translate HTTP.

The rules this file keeps (docs/IMAJUTSU.md, docs/IMAJUTSU_BACKEND.md):
* what a model can do comes only from what the engine published for it (``Model.meta.media``: modes, references, limits); which model serves a mode comes only
  from the routing DATA (imajutsu_routing.json): a mode is operational when a listed model really has the engine mode and the person may use it;
* nothing is dropped silently: every reference picture is either sent to the engine or listed as not used with the reason; scaling is called scaling;
* the person's pictures are never changed: adjusting, scaling and blending make NEW assets (origins ``adjusted``, ``scaled``, ``composited``);
* everything that arrives from the browser is validated here; ffmpeg only ever receives numbers that were checked.
Projects reuse the ``studio_projects`` table (spec.kind == "image"); no schema change.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import random
import re
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from . import imajutsu_adjust as adjust
from . import imajutsu_pix as pix
from . import imajutsu_plan as plan_mod
from . import imajutsu_presets as presets
from . import imajutsu_routing as routing
from . import media_lib, media_svc, services
from .adapters import AdapterError
from .backends_svc import adapter_for
from .media_lib import MediaError
from .media_models import MediaAsset, MediaJob
from .models import Backend, Model
from .studio_models import StudioProject

SPEC_VERSION = 1
KIND = "image"
MAX_REFS = 200                       # sanity cap on pictures kept in one project (the engine uses at most a few of them)
MAX_SPEC_BYTES = 256 * 1024
MAX_COUNT = 4
REF_MAX_EDGE = 2048                  # reference pictures are scaled down to this before they are sent
ADVANCED_KEYS = ("steps", "guidance", "scheduler", "denoise")
PREVIEW_CACHE_ENTRIES, PREVIEW_CACHE_BYTES = 48, 48 * 2**20


class ImajutsuError(MediaError):
    def __init__(self, status: int, code: str, message: str, extra: dict | None = None):
        super().__init__(status, code, message)
        self.extra = extra or {}


# ======================================================================================================================
# small validators
# ======================================================================================================================
def _num(v, name: str, lo: float, hi: float, *, integer: bool = False):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        raise ImajutsuError(422, "invalid", f"{name} must be a number.")
    if not lo <= v <= hi:
        raise ImajutsuError(422, "invalid", f"{name} must be between {lo:g} and {hi:g}.")
    return int(round(v)) if integer else float(v)


def _text(v, name: str, limit: int) -> str:
    if v is None:
        return ""
    if not isinstance(v, str):
        raise ImajutsuError(422, "invalid", f"{name} must be text.")
    v = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", v).strip()
    if len(v) > limit:
        raise ImajutsuError(422, "invalid", f"{name} is longer than {limit} characters.")
    return v


# ======================================================================================================================
# pictures (library assets, or the temporary no-save ones)
# ======================================================================================================================
@dataclass
class Pic:
    id: str
    obj: object                 # MediaAsset, or the id of an in-memory asset
    mem: bool
    mime: str
    width: int
    height: int
    name: str
    size: int

    def facts(self) -> dict:
        return {"id": self.id, "width": self.width, "height": self.height, "mime": self.mime, "name": self.name}


def load_pic(st, s, user_id: str, aid, what: str, *, missing: int = 422) -> Pic:
    """The caller's picture (library or temporary no-save), else a plain 422 (a picture named in a request body) or 404 (an id in the URL). A foreign id and a
    missing one read the same."""
    if not isinstance(aid, str) or not aid:
        raise ImajutsuError(422, "invalid", f"{what} must be a picture from your library.")
    got = media_lib.owned(st, s, user_id, aid)
    if got is None:
        if missing == 404:
            raise ImajutsuError(404, "not_found", "Not found.")
        raise ImajutsuError(422, "asset_missing", f"{what} is no longer in your library.")
    if isinstance(got, str):
        info = media_lib.mem_info(st, got)
        pic = Pic(got, got, True, info["mime"], int(info["width"]), int(info["height"]), media_lib.clean_name(info.get("name"), "image"), info["size"])
        kind = info["kind"]
    else:
        pic = Pic(got.id, got, False, got.mime, int(got.width), int(got.height), media_lib.display_name(media_lib.meta_of(st, got), got.kind, got.id), got.size)
        kind = got.kind
    if kind != "image":
        raise ImajutsuError(422, "invalid", f"{what} must be a picture.")
    if pic.mime == "image/gif":
        raise ImajutsuError(422, "unsupported_type", f"{what} is an animated GIF; save a frame as PNG first.")
    return pic


@contextlib.contextmanager
def open_pics(st, pics: list[Pic]):
    """Plaintext scratch files of the pictures -> [Path] (deleted on exit)."""
    with media_lib.materialise(st, [p.obj for p in pics]) as opened:
        paths = []
        for fh, *_ in opened:
            fh.close()
            paths.append(Path(fh.name))
        yield paths


async def pixel_work(st, fn, *args):
    """Run synchronous pixel code (ffmpeg + numpy) in a thread, one heavy job per ffmpeg slot; any PixError / RecipeError becomes a plain 422."""
    try:
        async with media_lib._sem(st):
            return await asyncio.to_thread(fn, *args)
    except (pix.PixError, adjust.RecipeError) as e:
        raise ImajutsuError(422, "image_failed", str(e)) from e


# ======================================================================================================================
# the spec (version 1)
# ======================================================================================================================
def default_aspect(mode: str) -> dict:
    if routing.MODE_DEFS.get(mode, {}).get("source"):
        return {"id": "source", "w": None, "h": None}
    w, h = plan_mod.ASPECTS["9:16"]
    return {"id": "9:16", "w": w, "h": h}


def default_spec(mode: str = "generate") -> dict:
    return {"version": SPEC_VERSION, "kind": KIND, "mode": mode, "prompt": "", "negative": "", "preset": {"id": presets.PRESET_ID if mode == "iphone_photo" else None, "scenes": [], "finish": False, "tier": None},
            "aspect": default_aspect(mode), "size": {"native": None, "output": None}, "quality": "standard", "seed": None, "count": 1,
            "source_asset": None, "mask_asset": None, "selection": {"box": None, "feather": 0, "invert": False, "keep_outside": None},
            "outpaint": {"pad": None, "target": None, "anchor": {"x": 0.5, "y": 0.5}, "overlap": 0}, "upscale": {"refine": False, "strength": 0.3},
            "references": [], "model": None, "advanced": {}, "adjustments": {}}


def _size(v, name: str):
    if v is None:
        return None
    if not isinstance(v, dict):
        raise ImajutsuError(422, "invalid", f"{name} needs a width and a height.")
    return {"width": _num(v.get("width"), f"{name}.width", plan_mod.MIN_OUT, plan_mod.MAX_OUT, integer=True),
            "height": _num(v.get("height"), f"{name}.height", plan_mod.MIN_OUT, plan_mod.MAX_OUT, integer=True)}


def normalize_spec(st, s, user_id: str, raw, *, check_model: bool = True) -> dict:
    """Validate what the browser sent and return the version-1 spec. Unknown keys are ignored (and not stored); wrong values are refused with a sentence."""
    if not isinstance(raw, dict):
        raise ImajutsuError(422, "invalid", "The project settings must be an object.")
    if len(json.dumps(raw, default=str)) > MAX_SPEC_BYTES:
        raise ImajutsuError(422, "invalid", "The project settings are too large.")
    mode = raw.get("mode", "generate")
    if mode not in routing.MODE_DEFS:
        raise ImajutsuError(422, "invalid", "Pick one of the modes: " + ", ".join(routing.MODE_IDS) + ".")
    d = default_spec(mode)
    d["prompt"] = _text(raw.get("prompt"), "The prompt", plan_mod.PROMPT_MAX)
    d["negative"] = _text(raw.get("negative"), "The negative prompt", plan_mod.PROMPT_MAX)
    pr = raw.get("preset")
    if pr is not None:
        if not isinstance(pr, dict):
            raise ImajutsuError(422, "invalid", "preset must be an object.")
        pid = pr.get("id")
        if pid not in (None, presets.PRESET_ID):
            raise ImajutsuError(422, "invalid", f"The only preset is '{presets.PRESET_ID}'.")
        scenes = pr.get("scenes", [])
        if not isinstance(scenes, list) or len(scenes) > len(presets.SCENES) or any(x not in presets.SCENES for x in scenes):
            raise ImajutsuError(422, "invalid", "Pick scenes from: " + ", ".join(presets.SCENES) + ".")
        tier = pr.get("tier")
        if tier is not None and tier not in presets.TIERS:
            raise ImajutsuError(422, "invalid", "The quality tier must be one of: " + ", ".join(presets.TIERS) + ".")
        d["preset"] = {"id": pid if mode != "iphone_photo" else presets.PRESET_ID, "scenes": list(dict.fromkeys(scenes)), "finish": bool(pr.get("finish", False)), "tier": tier}
    a = raw.get("aspect")
    if a is not None:
        if not isinstance(a, dict):
            raise ImajutsuError(422, "invalid", "aspect must be an object.")
        aid = a.get("id")
        if aid in plan_mod.ASPECTS:
            d["aspect"] = {"id": aid, "w": plan_mod.ASPECTS[aid][0], "h": plan_mod.ASPECTS[aid][1]}
        elif aid == "source":
            d["aspect"] = {"id": "source", "w": None, "h": None}
        elif aid == "custom":
            w, h = _num(a.get("w"), "aspect.w", 1, 100000), _num(a.get("h"), "aspect.h", 1, 100000)
            if not 0.2 <= w / h <= 5:
                raise ImajutsuError(422, "invalid", "A custom shape must be between 1:5 and 5:1.")
            d["aspect"] = {"id": "custom", "w": round(w, 4), "h": round(h, 4)}
        else:
            raise ImajutsuError(422, "invalid", "Pick one of the shapes: " + ", ".join([*plan_mod.ASPECTS, "custom", "source"]) + ".")
    sz = raw.get("size")
    if sz is not None:
        if not isinstance(sz, dict):
            raise ImajutsuError(422, "invalid", "size must be an object.")
        d["size"] = {"native": _size(sz.get("native"), "size.native"), "output": _size(sz.get("output"), "size.output")}
        for k in ("native", "output"):
            if d["size"][k] and d["size"][k]["width"] * d["size"][k]["height"] > plan_mod.MAX_OUT_PIXELS:
                raise ImajutsuError(422, "invalid", "That size is larger than 8K.")
    q = raw.get("quality", "standard")
    if q not in plan_mod.QUALITY:
        raise ImajutsuError(422, "invalid", "quality must be one of: " + ", ".join(plan_mod.QUALITY) + ".")
    d["quality"] = q
    seed = raw.get("seed")
    if seed is not None and seed != -1:
        d["seed"] = _num(seed, "seed", 0, 2**31 - 1 - MAX_COUNT, integer=True)
    d["count"] = _num(raw.get("count", 1), "count", 1, MAX_COUNT, integer=True)
    if raw.get("source_asset") is not None:
        d["source_asset"] = load_pic(st, s, user_id, raw["source_asset"], "The source picture").id
    if raw.get("mask_asset") is not None:
        d["mask_asset"] = load_pic(st, s, user_id, raw["mask_asset"], "The selection mask").id
    sel = raw.get("selection")
    if sel is not None:
        if not isinstance(sel, dict):
            raise ImajutsuError(422, "invalid", "selection must be an object.")
        box = sel.get("box")
        if box is not None:
            if not isinstance(box, dict):
                raise ImajutsuError(422, "invalid", "The selection box needs x, y, w and h (fractions of the picture, 0 to 1).")
            bx, by, bw, bh = (_num(box.get(k), f"selection.box.{k}", 0, 1) for k in ("x", "y", "w", "h"))
            if bw < 0.005 or bh < 0.005 or bx + bw > 1.0001 or by + bh > 1.0001:
                raise ImajutsuError(422, "invalid", "The selection box must lie inside the picture.")
            box = {"x": round(bx, 5), "y": round(by, 5), "w": round(bw, 5), "h": round(bh, 5)}
        ko = sel.get("keep_outside")
        d["selection"] = {"box": box, "feather": round(_num(sel.get("feather", 0), "selection.feather", 0, 200), 2), "invert": bool(sel.get("invert", False)),
                          "keep_outside": None if ko is None else bool(ko)}
    o = raw.get("outpaint")
    if o is not None:
        if not isinstance(o, dict):
            raise ImajutsuError(422, "invalid", "outpaint must be an object.")
        pad = o.get("pad")
        if pad is not None:
            if not isinstance(pad, dict):
                raise ImajutsuError(422, "invalid", "outpaint.pad needs left, top, right and bottom (pixels).")
            pad = {k: _num(pad.get(k, 0), f"outpaint.pad.{k}", 0, plan_mod.MAX_OUT, integer=True) for k in ("left", "top", "right", "bottom")}
        anc = o.get("anchor") or {}
        d["outpaint"] = {"pad": pad, "target": _size(o.get("target"), "outpaint.target"),
                         "anchor": {"x": _num(anc.get("x", 0.5), "outpaint.anchor.x", 0, 1), "y": _num(anc.get("y", 0.5), "outpaint.anchor.y", 0, 1)},
                         "overlap": _num(o.get("overlap", 0), "outpaint.overlap", 0, 128, integer=True)}
    u = raw.get("upscale")
    if u is not None:
        if not isinstance(u, dict):
            raise ImajutsuError(422, "invalid", "upscale must be an object.")
        d["upscale"] = {"refine": bool(u.get("refine", False)), "strength": round(_num(u.get("strength", 0.3), "upscale.strength", 0.05, 1), 3)}
    refs = raw.get("references", [])
    if not isinstance(refs, list):
        raise ImajutsuError(422, "invalid", "references must be a list.")
    if len(refs) > MAX_REFS:
        raise ImajutsuError(422, "too_many_references", f"A project keeps at most {MAX_REFS} reference pictures.")
    for i, ref in enumerate(refs):
        if not isinstance(ref, dict):
            raise ImajutsuError(422, "invalid", f"Reference {i + 1} is not valid.")
        pic = load_pic(st, s, user_id, ref.get("asset"), f"Reference picture {i + 1}")
        role = ref.get("role")
        if role is not None and role not in plan_mod.ROLES:
            raise ImajutsuError(422, "invalid", f"Reference {i + 1}: the role must be one of {', '.join(plan_mod.ROLES)} (or none).")
        d["references"].append({"asset": pic.id, "role": role, "preserve": _text(ref.get("preserve"), "A 'keep' note", 200), "active": bool(ref.get("active", True)),
                                "label": _text(ref.get("label"), "A label", 120)})
    m = raw.get("model")
    if m is not None:
        if not isinstance(m, str) or len(m) > 32:
            raise ImajutsuError(422, "invalid", "That model is not valid.")
        if check_model and s.get(Model, m) is None:
            raise ImajutsuError(422, "invalid", "That model does not exist.")
        d["model"] = m
    adv = raw.get("advanced", {})
    if adv is None:
        adv = {}
    if not isinstance(adv, dict):
        raise ImajutsuError(422, "invalid", "advanced must be an object.")
    unknown = sorted(set(adv) - set(ADVANCED_KEYS))
    if unknown:
        raise ImajutsuError(422, "invalid", "Unknown advanced setting: " + ", ".join(str(x)[:30] for x in unknown) + ". Allowed: " + ", ".join(ADVANCED_KEYS) + ".")
    if adv.get("steps") is not None:
        d["advanced"]["steps"] = _num(adv["steps"], "steps", 1, 200, integer=True)
    if adv.get("guidance") is not None:
        d["advanced"]["guidance"] = _num(adv["guidance"], "guidance", 0, 30)
    if adv.get("scheduler"):
        if not isinstance(adv["scheduler"], str) or not re.fullmatch(r"[a-z0-9_]{1,40}", adv["scheduler"]):
            raise ImajutsuError(422, "invalid", "That scheduler is not valid.")
        d["advanced"]["scheduler"] = adv["scheduler"]
    if adv.get("denoise") is not None:
        d["advanced"]["denoise"] = round(_num(adv["denoise"], "denoise", 0.05, 1), 3)
    try:
        d["adjustments"] = adjust.normalize_recipe(raw.get("adjustments"))
    except adjust.RecipeError as e:
        raise ImajutsuError(422, "invalid", str(e)) from e
    return d


# ======================================================================================================================
# projects (rows of studio_projects whose spec.kind == "image")
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


def project_kind(st, p: StudioProject) -> str:
    return _dec(st, p.spec_enc, {}).get("kind") or "video"


def spec_of(st, p: StudioProject) -> dict:
    stored = _dec(st, p.spec_enc, {})
    return {**default_spec(stored.get("mode") or "generate"), **stored}


def own_project(st, s, user_id: str, pid: str) -> StudioProject:
    p = s.get(StudioProject, pid) if isinstance(pid, str) and pid.startswith("sp_") else None
    if p is None or p.user_id != user_id or project_kind(st, p) != KIND:
        raise ImajutsuError(404, "not_found", "Not found.")
    return p


def create_project(st, s, user, mode: str | None, source_asset: str | None, title: str | None) -> StudioProject:
    raw = {"mode": mode or ("edit" if source_asset else "generate"), "source_asset": source_asset}
    spec = normalize_spec(st, s, user.id, raw)
    name = _text(title, "The title", 120)
    if not name:
        if spec["source_asset"]:
            pic = load_pic(st, s, user.id, spec["source_asset"], "The source picture")
            name = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", pic.name) or "Untitled image"
        else:
            name = routing.MODE_DEFS[spec["mode"]]["label"]
    p = StudioProject(user_id=user.id, title_enc=st.vault.encrypt(name, "media"), spec_enc=_enc(st, spec), state_enc=_enc(st, {"jobs": []}))
    s.add(p)
    s.flush()
    return p


def update_project(st, s, user, p: StudioProject, raw_spec, expected_updated_at, title: str | None) -> StudioProject:
    if expected_updated_at is not None:
        if isinstance(expected_updated_at, bool) or not isinstance(expected_updated_at, (int, float)) or abs(float(expected_updated_at) - p.updated_at) > 1e-4:
            raise ImajutsuError(409, "stale", "This project was changed somewhere else. Reload it before saving.", {"updated_at": p.updated_at})
    if raw_spec is not None:
        p.spec_enc = _enc(st, normalize_spec(st, s, user.id, raw_spec))
    if title is not None:
        name = _text(title, "The title", 120)
        if name:
            p.title_enc = st.vault.encrypt(name, "media")
    p.updated_at = max(time.time(), p.updated_at + 1e-3)
    return p


def _job_rows(st, s, user_id: str, ids: list[str]) -> list[dict]:
    out = []
    for jid in ids:
        j = s.get(MediaJob, jid)
        if j is not None and j.user_id == user_id:
            out.append({"id": j.id, "status": j.status, "created_at": j.created_at, "finished_at": j.finished_at})
    return out


def project_json(st, s, user, p: StudioProject, *, summary: bool = False) -> dict:
    spec = spec_of(st, p)
    state = _dec(st, p.state_enc, {})
    jobs = _job_rows(st, s, user.id, [j for j in state.get("jobs", []) if isinstance(j, str)][:100])
    thumb = None
    for jr in jobs:
        a = s.execute(select(MediaAsset).where(MediaAsset.job_id == jr["id"], MediaAsset.user_id == user.id).order_by(MediaAsset.created_at)).scalars().first()
        if a is not None:
            thumb = a.id
            break
    src = spec.get("source_asset")
    src_row = s.get(MediaAsset, src) if src and src.startswith("ma_") else None
    out = {"id": p.id, "kind": KIND, "title": st.vault.decrypt(p.title_enc, "media") if p.title_enc else "", "created_at": p.created_at, "updated_at": p.updated_at,
           "mode": spec.get("mode"), "source_asset": src, "jobs": jobs, "last_job": jobs[0] if jobs else None,
           "thumb_asset": thumb or (src_row.id if src_row is not None and src_row.user_id == user.id else None)}
    out["thumbnail"] = out["thumb_asset"]
    if summary:
        return out
    out["spec"] = spec
    out["source"] = _asset_json(st, s, user.id, spec.get("source_asset"))
    out["mask"] = _asset_json(st, s, user.id, spec.get("mask_asset"))
    return out


def _asset_json(st, s, user_id: str, aid) -> dict | None:
    if not aid:
        return None
    got = media_lib.owned(st, s, user_id, aid)
    if got is None:
        return None
    return media_lib.mem_out(st, got) if isinstance(got, str) else media_lib.asset_json(st, got)


# ======================================================================================================================
# models and the routing
# ======================================================================================================================
def model_facts(m: Model, b: Backend) -> dict:
    info = media_svc.media_info(m)
    return {"model_id": m.id, "alias": m.alias, "engine_id": m.real_id.split(":", 1)[1] if ":" in m.real_id else m.real_id,
            "modes": [x for x in info.get("modes") or [] if isinstance(x, str)], "references": int(info.get("references") or 0),
            "negative_prompt": bool(info.get("negative_prompt")), "schedulers": list(info.get("schedulers") or []), "limits": dict(info.get("limits") or {}),
            "defaults": dict(info.get("defaults") or {}), "license": (m.meta or {}).get("license"), "loaded": (m.meta or {}).get("loaded"),
            "description": (m.meta or {}).get("description")}


def image_rows(st, s, principal) -> list[tuple[Model, Backend, dict]]:
    """The image models the person may use (editing profiles are video and not included), in the administrator's order."""
    return [(m, b, model_facts(m, b)) for m, b in services.accessible_models(s, principal, st, "image") if not media_svc.profile_info(m)]


def routing_doc(st) -> dict:
    try:
        return routing.load(st.cfg.data_dir)
    except routing.RoutingError as e:
        raise ImajutsuError(500, "routing", f"The routing table could not be read: {e}") from e


def base_engine_mode(mode: str, entry: dict | None) -> str | None:
    return (entry or {}).get("engine_mode") or routing.MODE_DEFS[mode]["engine_mode"]


def operational(mode: str, f: dict, entry: dict | None) -> tuple[bool, str]:
    """May this model serve this mode: (ok, reason). The engine mode must be one of the model's real modes, and a mode that needs pictures needs room for them."""
    md = routing.MODE_DEFS[mode]
    if mode == "advanced":
        return (True, "") if f["modes"] else (False, f"{f['alias']} reports no engine modes.")
    em = base_engine_mode(mode, entry)
    if em is None:
        return False, "This mode runs without a model."
    if em not in f["modes"]:
        return False, f"{f['alias']} does not offer {'instruction editing' if em == 'edit' else em} (it can do: {', '.join(f['modes']) or 'nothing it reports'})."
    if md["refs_min"]:
        slots = plan_mod.picture_cap(em, f["references"]) - (1 if md["source"] else 0)
        if slots < md["refs_min"]:
            return False, f"{f['alias']} takes {plan_mod.picture_cap(em, f['references'])} picture{'s' if plan_mod.picture_cap(em, f['references']) != 1 else ''} and {md['label']} needs {md['refs_min'] + (1 if md['source'] else 0)}."
    return True, ""


def mode_table(doc: dict, rows: list[tuple[Model, Backend, dict]], mode: str) -> dict:
    """For one mode: every model with its status, and the recommended one (the first listed model that is operational)."""
    by_engine = {f["engine_id"]: f for _m, _b, f in rows}
    out: list[dict] = []
    listed: set[str] = set()
    rec = None
    for e in routing.entries(doc, mode):
        eid = e["engine_id"]
        listed.add(eid)
        f = by_engine.get(eid)
        base = {"engine_id": eid, "routed": True, "why": e.get("why"), "evidence": e.get("evidence") or [], "slow": bool(e.get("slow")), "seconds_hint": e.get("seconds_hint"),
                "avoid_phone_words": bool(e.get("avoid_phone_words")), "tier": e.get("tier") if mode == "iphone_photo" else None,
                "wording": e.get("wording") or ("plain" if e.get("avoid_phone_words") else "natural")}
        if f is None:
            out.append({**base, "model_id": None, "alias": eid, "status": "unavailable", "reason": "Not installed on the engine, or not available to you.", "limits": None, "needs": None})
            continue
        ok, why = operational(mode, f, e)
        row = {**base, "model_id": f["model_id"], "alias": f["alias"], "status": "ready" if ok else "unavailable", "reason": "" if ok else why, "limits": model_limits(f, e),
               "needs": needs_of(mode)}
        if ok and rec is None:
            rec = row
        out.append(row)
    for _m, _b, f in rows:
        if f["engine_id"] in listed:
            continue
        ok, why = operational(mode, f, None)
        out.append({"engine_id": f["engine_id"], "routed": False, "why": None, "evidence": [], "slow": False, "seconds_hint": None, "avoid_phone_words": False, "tier": None, "wording": "natural",
                    "model_id": f["model_id"], "alias": f["alias"], "status": "available" if ok else "unavailable",
                    "reason": "Works for this, but it is not on the recommended list (untested)." if ok else why, "limits": model_limits(f, None), "needs": needs_of(mode)})
    return {"models": out, "recommended": rec}


def needs_of(mode: str) -> dict:
    md = routing.MODE_DEFS[mode]
    return {"source": md["source"], "mask": md["mask"], "prompt": md["prompt"], "references_min": md["refs_min"], "references_ok": md["refs_ok"]}


def model_limits(f: dict, entry: dict | None) -> dict:
    lim = f["limits"]
    return {"references": f["references"], "min_side": lim.get("min_side"), "max_side": lim.get("max_side"), "multiple": lim.get("multiple") or 16,
            "native_pixels": (entry or {}).get("pixels") or routing.DEFAULT_PIXELS, "steps": lim.get("steps"), "guidance": lim.get("guidance"),
            "negative_prompt": f["negative_prompt"], "schedulers": f["schedulers"], "modes": f["modes"]}


def choose_model(spec: dict, rows: list[tuple[Model, Backend, dict]], doc: dict) -> dict:
    """{"row": (m, b, facts) | None, "entry", "recommended": bool, "why_none"}. A model named in the spec is used as it is (the plan reports what it cannot do)."""
    mode = spec["mode"]
    key = "refine" if mode == "upscale" else "models"
    entries = routing.entries(doc, "upscale" if mode == "upscale" else mode, key)
    by_engine = {f["engine_id"]: r for r in rows for f in [r[2]]}
    first = None
    for e in entries:
        r = by_engine.get(e["engine_id"])
        if r is None:
            continue
        ee = e if mode != "upscale" else {**e, "engine_mode": e.get("engine_mode") or "img2img"}
        ok = operational("restyle" if mode == "upscale" else mode, r[2], ee)[0]
        if ok:
            first = (r, e)
            break
    if mode == "advanced" and first is None:
        for r in rows:
            if r[2]["modes"]:
                first = (r, routing.entry_for(doc, mode, r[2]["engine_id"]))
                break
    if spec.get("model"):
        r = next((x for x in rows if x[0].id == spec["model"]), None)
        if r is None:
            return {"row": None, "entry": None, "recommended": False, "why_none": "That model is not available to you."}
        e = next((x for x in entries if x["engine_id"] == r[2]["engine_id"]), None)
        # a model chosen by hand that is not on the mode's list still gets the shared facts of the file (native sizes, wording rules), marked as not routed
        return {"row": r, "entry": e or {**routing.shared_facts(doc, r[2]["engine_id"]), "routed": False}, "recommended": bool(first and first[0][0].id == r[0].id)}
    if mode == "iphone_photo" and (spec.get("preset") or {}).get("tier") and first is not None:
        # a quality tier (fast | best | polished) picks the model of that tier; when that model is not usable the recommended one runs and the plan says so
        want = spec["preset"]["tier"]
        for e in entries:
            r = by_engine.get(e["engine_id"])
            if e.get("tier") == want and r is not None and operational(mode, r[2], e)[0]:
                return {"row": r, "entry": e, "recommended": first[0][0].id == r[0].id}
        return {"row": first[0], "entry": first[1], "recommended": True,
                "warnings": [f"{presets.TIER_LABELS[want]} needs a model that is not available to you; {first[0][2]['alias']} was used instead."]}
    if first is not None:
        return {"row": first[0], "entry": first[1], "recommended": True}
    if not rows:
        return {"row": None, "entry": None, "recommended": False, "why_none": "No image model is available to you."}
    names = ", ".join(e["engine_id"] for e in entries) or "none"
    return {"row": None, "entry": None, "recommended": False, "why_none": f"None of the recommended models ({names}) is installed, allowed for you and able to do {routing.MODE_DEFS[mode]['label']}."}


# ======================================================================================================================
# capabilities
# ======================================================================================================================
def aspects_list() -> list[dict]:
    return [{"id": k, "label": plan_mod.ASPECT_LABELS[k], "w": w, "h": h} for k, (w, h) in plan_mod.ASPECTS.items()] + \
           [{"id": "custom", "label": plan_mod.ASPECT_LABELS["custom"], "w": None, "h": None}, {"id": "source", "label": plan_mod.ASPECT_LABELS["source"], "w": None, "h": None}]


def capabilities(st, s, principal) -> dict:
    doc_info = routing_doc(st)
    doc = doc_info["doc"]
    rows = image_rows(st, s, principal)
    modes = []
    ffmpeg_ok = media_lib.ffbin("ffmpeg") is not None
    for mid, md in routing.MODE_DEFS.items():
        if mid == "upscale":
            ref_entries = routing.entries(doc, "upscale", "refine")
            by_engine = {r[2]["engine_id"]: r[2] for r in rows}
            ref_models = []
            for e in ref_entries:
                f = by_engine.get(e["engine_id"])
                ok = bool(f) and (e.get("engine_mode") or "img2img") in f["modes"]
                ref_models.append({"engine_id": e["engine_id"], "model_id": f["model_id"] if f else None, "alias": f["alias"] if f else e["engine_id"], "why": e.get("why"),
                                   "status": "ready" if ok else "unavailable", "reason": "" if ok else "Not installed, not allowed for you, or without image-to-image.",
                                   "limits": model_limits(f, e) if f else None})
            scal = (doc.get("modes", {}).get("upscale") or {}).get("scaling") or {}
            modes.append({"id": mid, "label": md["label"], "engine_mode": None, "status": "ready" if ffmpeg_ok else "unavailable",
                          "reason": "" if ffmpeg_ok else "ffmpeg is not installed on this server.", "recommended": None, "models": [], "needs": needs_of(mid),
                          "scaling": {"method": scal.get("method", "lanczos"), "label": scal.get("label", "Scaled, no new detail"), "ai": False},
                          "refine": {"available": any(x["status"] == "ready" for x in ref_models), "models": ref_models,
                                     "recommended": next(({"model_id": x["model_id"], "why": x["why"]} for x in ref_models if x["status"] == "ready"), None)}})
            continue
        t = mode_table(doc, rows, mid)
        rec = t["recommended"]
        if rec is not None:
            status, reason = "ready", ""
        else:
            status = "unavailable"
            routed = [x for x in t["models"] if x["routed"]]
            installed = [x for x in routed if x["model_id"]]
            if not rows:
                reason = "No image model is available to you."
            elif installed:
                reason = installed[0]["reason"] or "No installed model can do this."
            else:
                reason = "None of the recommended models is installed or allowed for you" + (f" ({', '.join(x['engine_id'] for x in routed)})." if routed else ".")
            if mid == "advanced":
                reason = "No image model reports an engine mode."
        modes.append({"id": mid, "label": md["label"], "engine_mode": md["engine_mode"], "status": status, "reason": reason,
                      "recommended": {"model_id": rec["model_id"], "alias": rec["alias"], "why": rec["why"], "evidence": rec["evidence"]} if rec else None,
                      "models": t["models"], "needs": needs_of(mid)})
    iphone = next((m for m in modes if m["id"] == "iphone_photo"), {})
    tiers = [{"id": r["tier"], "label": presets.TIER_LABELS.get(r["tier"], r["tier"]), "model_id": r["model_id"], "alias": r["alias"], "status": r["status"], "reason": r["reason"],
              "why": r["why"], "seconds_hint": r["seconds_hint"], "default": r["tier"] == presets.DEFAULT_TIER}
             for r in iphone.get("models", []) if r.get("routed") and r.get("tier")]
    iphone["tiers"], iphone["default_tier"] = tiers, presets.DEFAULT_TIER
    return {"modes": modes, "presets": [presets.info((iphone.get("recommended") or {}).get("model_id"), tiers)], "aspects": aspects_list(),
            "roles": [{"id": r} for r in plan_mod.ROLES], "qualities": list(plan_mod.QUALITY),
            "limits": {"max_pictures": MAX_REFS, "max_count": MAX_COUNT, "reference_max_edge": REF_MAX_EDGE, "preview_edge": adjust.PREVIEW_EDGE,
                       "upload_max_mb": st.settings.value_for(s, "media.upload_max_mb", principal), "max_assets": st.settings.value_for(s, "media.max_assets", principal),
                       "concurrent": st.settings.value_for(s, "media.max_concurrent_jobs", principal),
                       "adjust": {**{k: list(v) for k, v in adjust.RANGES.items()}, "rotate": [0, 90, 180, 270]}},
            "adjust": {"ranges": {k: list(v) for k, v in adjust.RANGES.items()}, "finish": dict(presets.FINISH_RECIPE)},
            "routing": {"source": doc_info["source"], "version": doc.get("version"), "updated": doc.get("updated"), "problem": doc_info.get("problem")}}


def availability(st, s, principal, image_state: dict) -> dict:
    """ui.operations.imajutsu: may this person open Imajutsu (the image mode is on and an image model they may use has txt2img or edit)."""
    if not image_state.get("enabled"):
        return {"enabled": False, "reason": image_state.get("reason") or "Images are not available to you."}
    rows = image_rows(st, s, principal)
    if not rows:
        return {"enabled": False, "reason": "No image model is available to you."}
    if any({"txt2img", "edit"} & set(f["modes"]) for _m, _b, f in rows):
        return {"enabled": True, "reason": ""}
    return {"enabled": False, "reason": "None of your image models can generate from text or edit a picture."}


# ======================================================================================================================
# the plan
# ======================================================================================================================
async def build_plan(st, s, user, spec: dict, *, estimate: bool = True) -> dict:
    """The plan for a spec plus what the caller needs next. No side effects."""
    principal = services.load_principal(s, user)
    info = routing_doc(st)
    doc = info["doc"]
    rows = image_rows(st, s, principal)
    pics: dict[str, Pic] = {}

    def pic(aid, what):
        if aid and aid not in pics:
            pics[aid] = load_pic(st, s, user.id, aid, what)
        return pics.get(aid)
    source = pic(spec.get("source_asset"), "The source picture")
    maskp = pic(spec.get("mask_asset"), "The selection mask")
    choice = choose_model(spec, rows, doc)
    row = choice["row"]
    facts = row[2] if row else None
    if spec["mode"] == "upscale" and not (spec.get("upscale") or {}).get("refine"):
        row, facts, choice = None, None, {"row": None, "entry": None, "recommended": False}
    ctx = {"mode": spec["mode"], "model": facts, "entry": choice.get("entry"), "recommended": choice.get("recommended"), "why_none": choice.get("why_none"),
           "source": source.facts() if source else None, "mask": ({"asset": maskp.id, "width": maskp.width, "height": maskp.height} if maskp else None),
           "avoid_words": routing.avoid_words(doc), "pixels": doc.get("pixels"), "warnings": choice.get("warnings")}
    pln = plan_mod.plan(spec, ctx)
    if maskp and source and abs(maskp.width / maskp.height / (source.width / source.height) - 1) > plan_mod.SHAPE_TOLERANCE:
        pln["errors"].append(f"The selection was drawn on a picture of another shape ({maskp.width}×{maskp.height}); the source is {source.width}×{source.height}.")
    if estimate and row and facts and pln.get("native") and not pln["errors"]:
        await _estimate(st, row[1], facts, pln)
    return {"plan": pln, "source": source, "mask": maskp, "row": row, "facts": facts, "entry": choice.get("entry"), "pics": pics}


async def _estimate(st, b: Backend, f: dict, pln: dict) -> None:
    """Ask the engine what it recorded for this model and size. The answer is shown only when the engine says it measured or extrapolated something."""
    nat = pln["native"]
    try:
        ad = adapter_for(b, st)
        code, d = await asyncio.wait_for(ad.media_model_estimate(f["engine_id"], {"width": nat["width"], "height": nat["height"], "steps": pln["steps"], "mode": pln.get("engine_mode"),
                                                                                    "pictures": pln["references"]["pictures"] or None}), 6)
    except (AdapterError, TimeoutError, AttributeError):
        return
    if code != 200 or not isinstance(d, dict):
        return
    sec, mem = d.get("seconds") or {}, d.get("memory") or {}
    est: dict = {}
    if sec.get("basis") in ("measured", "extrapolated") and sec.get("estimate"):
        est["seconds"] = {"estimate": sec["estimate"], "basis": sec["basis"]}
    if mem.get("basis") in ("measured", "extrapolated") and mem.get("peak_mb"):
        est["memory"] = {"peak_mb": mem["peak_mb"], "basis": mem["basis"], "note": media_svc.clean_text(mem.get("note"))}
    if est:
        pln["estimate"] = est


# ======================================================================================================================
# preparing the pictures for the engine
# ======================================================================================================================
def _read_pic(path: Path, mime: str):
    demux = pix.demuxer_of(mime)
    with open(path, "rb") as fh:
        head = fh.read(65536)
    return pix.decode_rgba(path, demux, pix.orientation_of(head, mime))


def prepare_inputs(job: dict) -> dict:
    """Synchronous pixel work for one generation (runs in a thread). ``job`` carries the plan, local file paths and the selection settings.
    -> {"pictures": [(bytes, mime, name)] in the order the engine receives them, "mask": (bytes, mime, name) | None, "canvas": {...} | None}"""
    pln, spec = job["plan"], job["spec"]
    nw, nh = pln["work"]["width"], pln["work"]["height"]
    em = pln["engine_mode"]
    pictures: list[tuple[bytes, str, str]] = []
    mask_out = None
    canvas_info = None
    src_rgb = None
    if job.get("source"):
        path, mime, _name = job["source"]
        src_rgb = pix.flatten(_read_pic(path, mime))
    full_mask = None
    if job["mode"] == "outpaint" and src_rgb is not None:
        pad = pln["outpaint"]["pad"]
        src_rgb, full_mask = pix.pad_canvas(src_rgb, pad["left"], pad["top"], pad["right"], pad["bottom"])
        ov = pln["outpaint"].get("overlap") or 0
        if ov:
            h0, w0 = src_rgb.shape[0] - pad["top"] - pad["bottom"], src_rgb.shape[1] - pad["left"] - pad["right"]
            if pad["left"]:
                full_mask[pad["top"]:pad["top"] + h0, pad["left"]:pad["left"] + min(ov, w0)] = 255
            if pad["right"]:
                full_mask[pad["top"]:pad["top"] + h0, pad["left"] + w0 - min(ov, w0):pad["left"] + w0] = 255
            if pad["top"]:
                full_mask[pad["top"]:pad["top"] + min(ov, h0), pad["left"]:pad["left"] + w0] = 255
            if pad["bottom"]:
                full_mask[pad["top"] + h0 - min(ov, h0):pad["top"] + h0, pad["left"]:pad["left"] + w0] = 255
        canvas_info = {"width": src_rgb.shape[1], "height": src_rgb.shape[0], "pad": pad}
    if job["mode"] == "upscale" and src_rgb is not None:           # refine: the scaled picture goes to the engine at the size it will keep
        pictures.append((pix.encode(pix.scale(src_rgb, nw, nh), "png"), "image/png", "scaled.png"))
    elif src_rgb is not None and em in ("edit", "inpaint", "img2img"):
        pictures.append((pix.encode(pix.cover(src_rgb, nw, nh), "png"), "image/png", "source.png"))
    if em == "inpaint" and src_rgb is not None:
        sh, sw = src_rgb.shape[:2]
        if full_mask is not None:
            m = full_mask
        elif job.get("mask"):
            mp, mmime, _n = job["mask"]
            m = pix.luminance_mask(_read_pic(mp, mmime))
            if m.shape != (sh, sw):
                m = pix.scale(m, sw, sh, "bilinear")
        else:
            m = pix.box_mask(sw, sh, spec["selection"]["box"])
        sel = spec["selection"]
        if sel.get("invert"):
            m = 255 - m
        m = pix.cover(m, nw, nh, "neighbor" if full_mask is not None or not job.get("mask") else "bilinear")
        if sel.get("feather") and full_mask is None:
            m = pix.blur_mask(m, float(sel["feather"]) * nw / max(sw, 1) / 2)
        mask_out = (pix.encode(m, "png"), "image/png", "mask.png")
    for path, mime, name in job.get("refs", []):
        rgb = pix.flatten(_read_pic(path, mime))
        h, w = rgb.shape[:2]
        if max(w, h) > REF_MAX_EDGE:
            k = REF_MAX_EDGE / max(w, h)
            rgb = pix.scale(rgb, max(2, int(round(w * k))), max(2, int(round(h * k))))
        png = mime == "image/png"
        pictures.append((pix.encode(rgb, "png" if png else "jpg", 2), "image/png" if png else "image/jpeg", re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name)[:80] + (".png" if png else ".jpg")))
    return {"pictures": pictures, "mask": mask_out, "canvas": canvas_info}


# ======================================================================================================================
# generating
# ======================================================================================================================
def _reference_ids(spec: dict, used: list[dict]) -> list[dict]:
    return [{"asset": u["asset"], "position": u["position"], "role": u.get("role"), "label": u.get("label") or ""} for u in used]


async def generate(st, s, user, project: StudioProject, body: dict) -> dict:
    principal = services.load_principal(s, user)
    spec = spec_of(st, project)
    if isinstance(body.get("spec"), dict):  # the page sends what is on screen; it becomes the project's saved state too
        spec = normalize_spec(st, s, user.id, body["spec"])
        with st.db.session(write=True) as ws:
            pr = ws.get(StudioProject, project.id)
            pr.spec_enc = _enc(st, spec)
            pr.updated_at = max(time.time(), pr.updated_at + 1e-3)
    if body.get("count") is not None:
        spec["count"] = _num(body["count"], "count", 1, MAX_COUNT, integer=True)
    mode = spec["mode"]
    if mode == "upscale" and not spec["upscale"]["refine"]:
        raise ImajutsuError(422, "use_upscale", "Upscale without refining is a scaling step: use POST /api/imajutsu/assets/{id}/upscale.")
    r = await build_plan(st, s, user, spec, estimate=False)
    pln, facts, row, source, maskp = r["plan"], r["facts"], r["row"], r["source"], r["mask"]
    if pln["errors"]:
        raise ImajutsuError(422, "plan_errors", " ".join(pln["errors"]), {"plan": pln})
    m, b, _f = row
    ok, why = services.model_access(s, principal, m, b, st, "image")
    if not ok:
        raise ImajutsuError(403, "model_forbidden", why)
    count = spec["count"] if mode != "upscale" else 1
    seed0 = spec["seed"] if spec["seed"] is not None else random.randrange(0, 2**31 - 1 - MAX_COUNT)
    seeds = [seed0 + i for i in range(count)]
    used = pln["references"]["used"]
    ref_pics = [load_pic(st, s, user.id, u["asset"], f"Reference picture {u['position']}") for u in used]
    em = pln["engine_mode"]
    vmax = int(st.settings.value_for(s, "media.video_max_seconds", principal))
    try:
        base = {"prompt": pln["prompt"]["final"], "width": pln["work"]["width"], "height": pln["work"]["height"], "steps": pln["steps"], "guidance": pln["guidance"],
                "seed": seeds[0], **({"negative_prompt": pln["prompt"]["negative"]} if pln["prompt"]["negative_sent"] else {}),
                **({"scheduler": pln["scheduler"]} if pln["scheduler"] else {})}
        clean = media_svc.validate_params("image", m, base, vmax)
    except media_svc.ParamError as e:
        raise ImajutsuError(422, "invalid", str(e)) from e
    try:
        save = media_svc.admit(st, s, principal, user.id, True, "image", n=count)
    except media_svc.AdmitError as e:
        raise ImajutsuError(e.status, e.code, e.message) from e
    days = st.settings.value_for(s, "privacy.chat_retention_days", principal)
    # ---- prepare the pictures ----------------------------------------------------------------------------------------------------------------------
    need_src = source is not None and (mode in ("edit", "replace_subject", "inpaint", "outpaint", "restyle", "upscale") or (mode == "advanced" and em in ("edit", "inpaint", "img2img")))
    use_mask = em == "inpaint" and mode != "outpaint" and maskp is not None
    objs = ([source] if need_src else []) + ([maskp] if use_mask else []) + ref_pics
    s.commit()
    with open_pics(st, objs) as paths:
        it = iter(paths)
        job = {"plan": pln, "spec": spec, "mode": mode, "source": None, "mask": None, "refs": []}
        if need_src:
            job["source"] = (next(it), source.mime, source.name)
        if use_mask:
            job["mask"] = (next(it), maskp.mime, maskp.name)
        job["refs"] = [(next(it), p.mime, p.name) for p in ref_pics]
        prepared = await pixel_work(st, prepare_inputs, job)
    pictures = prepared["pictures"]
    uploads: dict[str, list] = {}
    if pictures:
        uploads["reference_image"] = [pictures[0]]
        if len(pictures) > 1:
            uploads["extra_references"] = pictures[1:]
    if prepared["mask"]:
        uploads["mask_image"] = [prepared["mask"]]
    accounting = {"mode": mode, "engine_mode": em, "model": facts["engine_id"], "sent_to_engine": _reference_ids(spec, used), "not_used": pln["references"]["not_used"],
                  "source": {"asset": source.id, "sent_as": "reference_image"} if need_src and source is not None else None,
                  "mask": ({"asset": maskp.id, "sent_as": "mask_image"} if use_mask else {"box": spec["selection"]["box"], "sent_as": "mask_image"} if prepared["mask"] and mode != "outpaint"
                           else {"outpaint": pln["outpaint"], "sent_as": "mask_image"} if prepared["mask"] else None),
                  "pictures_sent": len(pictures), "negative_sent": pln["prompt"]["negative_sent"], "seeds": seeds,
                  "prompt_sentences": pln["prompt"]["sentences"], "native": pln["native"], "output": pln["output"]}
    expires = time.time() + days * 86400 if days and days > 0 else None
    batch = "ib_" + uuid.uuid4().hex[:16]
    jobs: list[str] = []
    try:
        ids = await media_svc.upload_inputs(st, b, uploads) if uploads else {}
        for i in range(count):
            params = {**clean, "seed": seeds[i], **ids}
            if pln["denoise"] is not None and em in ("img2img", "inpaint"):
                params["denoise"] = pln["denoise"]
            block = {"version": 1, "project_id": project.id, "mode": mode, "operation": em, "engine_mode": em, "model": facts["engine_id"], "model_id": m.id, "spec": spec,
                     "references": spec["references"], "source_asset": spec["source_asset"], "mask_asset": spec["mask_asset"], "selection": spec["selection"],
                     "adjustments": spec["adjustments"], "plan": pln, "accounting": accounting, "batch": {"id": batch, "index": i, "count": count, "seed": seeds[i]}}
            jobs.append(await media_svc.submit(st, user, "image", m, b, params, save, expires, origin="imajutsu", extra_params={"imajutsu": block}))
    except AdapterError as e:
        for jid in jobs:           # all or none: a variation that could not start cancels the ones that did
            with contextlib.suppress(AdapterError):
                await media_svc.cancel(st, jid)
        raise ImajutsuError(422 if e.status == 422 else 502, e.kind, str(e)) from e
    with st.db.session(write=True) as ws:
        pr = ws.get(StudioProject, project.id)
        if pr is not None:
            state = _dec(st, pr.state_enc, {})
            state["jobs"] = [*reversed(jobs), *[x for x in state.get("jobs", []) if x not in jobs]][:100]
            pr.state_enc = _enc(st, state)
            pr.updated_at = max(time.time(), pr.updated_at + 1e-3)
    for jid in jobs:
        await media_svc.refresh(st, jid)
    s.commit()          # end the read snapshot taken while the pictures were opened, so the new job rows are visible
    details = [media_svc.job_out(st, s, principal, s.get(MediaJob, jid)) for jid in jobs]
    return {"job": jobs[0], "jobs": jobs, "job_detail": details[0], "job_details": details, "batch": {"id": batch, "count": count, "seeds": seeds},
            "accounting": accounting, "plan": pln, "forced_unsaved": not save}


# ======================================================================================================================
# jobs
# ======================================================================================================================
def block_of(st, j: MediaJob) -> dict | None:
    if not j.params_enc:
        return None
    try:
        d = json.loads(st.vault.decrypt(j.params_enc, "media"))
    except (ValueError, TypeError):
        return None
    b = d.get("imajutsu") if isinstance(d, dict) else None
    return b if isinstance(b, dict) else None


DERIVED_MODELS = ("adjusted", "scaled", "composited")


def _derived_by_parent(st, s, user_id: str) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for a in s.execute(select(MediaAsset).where(MediaAsset.user_id == user_id, MediaAsset.model_id.in_(DERIVED_MODELS)).order_by(MediaAsset.created_at)).scalars():
        meta = media_lib.meta_of(st, a)
        out.setdefault(meta.get("parent") or "", []).append({"id": a.id, "origin": meta.get("origin"), "width": a.width, "height": a.height, "created_at": a.created_at,
                                                             "imajutsu": meta.get("imajutsu")})
    return out


def job_row(st, s, user, j: MediaJob, models: dict, derived: dict) -> dict | None:
    block = block_of(st, j)
    if block is None:
        return None
    m = models.get(j.model_id)
    outs = []
    for a in s.execute(select(MediaAsset).where(MediaAsset.job_id == j.id).order_by(MediaAsset.created_at)).scalars():
        outs.append({"id": a.id, "kind": a.kind, "mime": a.mime, "width": a.width, "height": a.height, "size": a.size, "seed": a.seed, "created_at": a.created_at,
                     "derived": derived.get(a.id, [])})
    return {"job": j.id, "project_id": block.get("project_id"), "status": j.status, "mode": block.get("mode"), "engine_mode": block.get("engine_mode"),
            "model": m.alias if m else None, "model_id": j.model_id, "created_at": j.created_at, "finished_at": j.finished_at, "progress": j.progress,
            "progress_text": j.progress_text, "error": j.error, "batch": block.get("batch"), "outputs": outs, "imajutsu": block}


def list_jobs(st, s, user, limit: int = 100) -> list[dict]:
    models = {m.id: m for m in s.execute(select(Model)).scalars()}
    derived = _derived_by_parent(st, s, user.id)
    out = []
    for j in s.execute(select(MediaJob).where(MediaJob.user_id == user.id, MediaJob.kind == "image").order_by(MediaJob.created_at.desc()).limit(400)).scalars():
        row = job_row(st, s, user, j, models, derived)
        if row is not None:
            out.append(row)
            if len(out) >= limit:
                break
    return out


def one_job(st, s, user, jid: str) -> dict:
    j = s.get(MediaJob, jid)
    if j is None or j.user_id != user.id or j.kind != "image":
        raise ImajutsuError(404, "not_found", "Not found.")
    row = job_row(st, s, user, j, {m.id: m for m in s.execute(select(Model)).scalars()}, _derived_by_parent(st, s, user.id))
    if row is None:
        raise ImajutsuError(404, "not_found", "Not found.")
    return row


async def reopen(st, s, user, jid: str) -> dict:
    """The project of a job, set back to exactly what the job was made from (spec snapshot incl. every reference with its role, the mask, the selection, the
    adjustments). A picture that has left the library is dropped from the list and named in ``dropped``. Calling it again changes nothing."""
    j = s.get(MediaJob, jid)
    if j is None or j.user_id != user.id or j.kind != "image":
        raise ImajutsuError(404, "not_found", "Not found.")
    block = block_of(st, j)
    if block is None:
        raise ImajutsuError(404, "not_found", "This generation kept no settings (no-save).")
    snap = json.loads(json.dumps(block.get("spec") or {}))
    dropped: list[str] = []

    def alive(aid):
        got = media_lib.owned(st, s, user.id, aid) if isinstance(aid, str) and aid else None
        return got is not None
    for k in ("source_asset", "mask_asset"):
        if snap.get(k) and not alive(snap[k]):
            dropped.append(snap[k])
            snap[k] = None
    keep = []
    for r in snap.get("references") or []:
        if alive(r.get("asset")):
            keep.append(r)
        else:
            dropped.append(r.get("asset"))
    snap["references"] = keep
    if snap.get("model") and s.get(Model, snap["model"]) is None:
        snap["model"] = None
    spec = normalize_spec(st, s, user.id, snap)
    p = s.get(StudioProject, block.get("project_id")) if block.get("project_id") else None
    if p is None or p.user_id != user.id or project_kind(st, p) != KIND:
        title = f"{routing.MODE_DEFS.get(spec['mode'], {}).get('label', 'Image')} (reopened)"
        p = StudioProject(user_id=user.id, title_enc=st.vault.encrypt(title, "media"), spec_enc=_enc(st, spec), state_enc=_enc(st, {"jobs": [jid]}))
        s.add(p)
        s.flush()
        pid = p.id
        s.commit()
        with st.db.session(write=True) as ws:   # remember it, so a second call finds this project instead of making another
            jj = ws.get(MediaJob, jid)
            try:
                d = json.loads(st.vault.decrypt(jj.params_enc, "media"))
                d["imajutsu"]["project_id"] = pid
                jj.params_enc = st.vault.encrypt(json.dumps(d), "media")
            except (ValueError, TypeError, KeyError):
                pass
        s.expire_all()
        p = s.get(StudioProject, pid)
    else:
        p.spec_enc = _enc(st, spec)
        p.updated_at = max(time.time(), p.updated_at + 1e-3)
        s.commit()
    return {"project": project_json(st, s, user, p), "dropped": [d for d in dropped if d]}


# ======================================================================================================================
# photographic work on a single picture: adjust, scale, keep-outside blend (all produce NEW assets; the original is never touched)
# ======================================================================================================================
def _cache(st) -> OrderedDict:
    return st.__dict__.setdefault("imajutsu_preview_cache", OrderedDict())


def _cache_put(st, key, data: bytes) -> None:
    c = _cache(st)
    c[key] = data
    c.move_to_end(key)
    while len(c) > PREVIEW_CACHE_ENTRIES or sum(len(v) for v in c.values()) > PREVIEW_CACHE_BYTES:
        c.popitem(last=False)


async def adjust_preview(st, s, user, aid: str, raw_recipe) -> tuple[bytes, str]:
    """-> (JPEG, recipe key). Rendered by the same function as the final file, cached by picture + recipe."""
    pic = load_pic(st, s, user.id, aid, "The picture", missing=404)
    try:
        recipe = adjust.normalize_recipe(raw_recipe)
    except adjust.RecipeError as e:
        raise ImajutsuError(422, "invalid", str(e)) from e
    key = (user.id, pic.id, adjust.recipe_key(recipe), adjust.PREVIEW_EDGE)
    hit = _cache(st).get(key)
    if hit is not None:
        _cache(st).move_to_end(key)
        return hit, key[2]
    s.commit()
    with open_pics(st, [pic]) as [path]:
        head = path.read_bytes()[:65536] if pic.mime == "image/jpeg" else b""
        rendered = await pixel_work(st, lambda: adjust.render(path, pic.mime, recipe, orientation=pix.orientation_of(head, pic.mime), max_edge=adjust.PREVIEW_EDGE))
    _cache_put(st, key, rendered.data)
    return rendered.data, key[2]


@dataclass
class TaskCtx:
    expires: float | None
    cap: int
    ephemeral: bool


def _task_ctx(st, s, principal, pic: Pic) -> TaskCtx:
    days = st.settings.value_for(s, "privacy.chat_retention_days", principal)
    cap = int(st.settings.value_for(s, "media.upload_max_mb", principal)) * 2**20
    quota = st.settings.value_for(s, "media.max_assets", principal)
    ephemeral = pic.mem or days == 0
    if not ephemeral and quota and media_svc.count_assets(s, principal.user_id) >= quota:
        raise ImajutsuError(409, "gallery_full", f"Your library holds {quota} items, the maximum. Delete some to make room.")
    return TaskCtx(time.time() + days * 86400 if days and days > 0 else None, cap, ephemeral)


async def _bytes_iter(data: bytes):
    yield data


def _spawn(st, user_id: str, source: str, work) -> media_lib.EditTask:
    """Run ``work(task)`` -> the new asset's json in the background as an edit task (polled at GET /api/media/edits/{id} like any edit)."""
    mine = [t for t in media_lib.tasks(st).values() if t.user_id == user_id and t.status == "running"]
    if mine:
        raise ImajutsuError(429, "busy", "An edit is already running. Wait for it to finish or cancel it.")
    if len([t for t in media_lib.tasks(st).values() if t.status == "running"]) >= 4:
        raise ImajutsuError(429, "busy", "The server is busy editing other files. Try again in a minute.")
    t = media_lib.EditTask(id="ed_" + uuid.uuid4().hex[:20], user_id=user_id, source=source, kind="image")
    media_lib.tasks(st)[t.id] = t
    media_lib._gc_tasks(st)

    async def go():
        try:
            t.asset = await work(t)
            t.progress, t.status = 1.0, "completed"
        except (MediaError, pix.PixError, adjust.RecipeError) as e:
            t.status, t.error = "failed", getattr(e, "message", None) or str(e)
        except asyncio.CancelledError:
            t.status = "cancelled"
            raise
        except Exception:
            media_lib.log.exception("imajutsu task crashed")
            t.status, t.error = "failed", "The edit failed unexpectedly."
    task = asyncio.get_running_loop().create_task(go())
    st.__dict__.setdefault("media_edit_tasks", set()).add(task)
    task.add_done_callback(st.__dict__["media_edit_tasks"].discard)
    return t


def _stem(name: str) -> str:
    return re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name or "") or "picture"


async def start_adjust(st, s, user, aid: str, raw_recipe, name: str | None) -> media_lib.EditTask:
    principal = services.load_principal(s, user)
    pic = load_pic(st, s, user.id, aid, "The picture", missing=404)
    try:
        recipe = adjust.normalize_recipe(raw_recipe)
    except adjust.RecipeError as e:
        raise ImajutsuError(422, "invalid", str(e)) from e
    if adjust.is_neutral(recipe):
        raise ImajutsuError(422, "nothing_to_do", "Nothing was adjusted. Move a slider first.")
    tc = _task_ctx(st, s, principal, pic)
    s.commit()

    async def work(t):
        with open_pics(st, [pic]) as [path]:
            head = path.read_bytes()[:65536] if pic.mime == "image/jpeg" else b""
            rendered = await pixel_work(st, lambda: adjust.render(path, pic.mime, recipe, orientation=pix.orientation_of(head, pic.mime)))
        ext = "jpg" if rendered.mime == "image/jpeg" else "png"
        nm = media_lib.clean_name(name, "image") if name else f"{_stem(pic.name)} (adjusted).{ext}"
        meta = {"parent": pic.id, "imajutsu": {"kind": "adjusted", "recipe": recipe, "recipe_version": adjust.RECIPE_VERSION, "parent": pic.id, "model": None}}
        return await media_lib.ingest(st, user_id=user.id, name=nm, body=_bytes_iter(rendered.data), declared=len(rendered.data), cap=tc.cap, ephemeral=tc.ephemeral,
                                      expires_at=None if tc.ephemeral else tc.expires, origin="adjusted", extra_meta=meta)
    return _spawn(st, user.id, pic.id, work)


async def start_upscale(st, s, user, aid: str, width: int, height: int, name: str | None = None) -> tuple[media_lib.EditTask, dict]:
    principal = services.load_principal(s, user)
    pic = load_pic(st, s, user.id, aid, "The picture", missing=404)
    w = _num(width, "width", plan_mod.MIN_OUT, plan_mod.MAX_OUT, integer=True)
    h = _num(height, "height", plan_mod.MIN_OUT, plan_mod.MAX_OUT, integer=True)
    if w * h > plan_mod.MAX_OUT_PIXELS:
        raise ImajutsuError(422, "invalid", "That size is larger than 8K.")
    if (w, h) == (pic.width, pic.height):
        raise ImajutsuError(422, "nothing_to_do", f"The picture already is {w}×{h}.")
    if abs((w / h) / (pic.width / pic.height) - 1) > plan_mod.SHAPE_TOLERANCE:
        raise ImajutsuError(422, "shape_differs", f"{w}×{h} has a different shape from the picture ({pic.width}×{pic.height}). Choose a size with the same shape.")
    kind = plan_mod.scaling_kind((pic.width, pic.height), (w, h))
    tc = _task_ctx(st, s, principal, pic)
    s.commit()
    info = {"scaling": kind, "method": "lanczos", "from": {"width": pic.width, "height": pic.height}, "to": {"width": w, "height": h},
            "label": "Scaled, no new detail" if kind == "upscale" else "Scaled down", "ai": False}

    async def work(t):
        fmt = "jpg" if pic.mime == "image/jpeg" else "png"
        with open_pics(st, [pic]) as [path]:
            head = path.read_bytes()[:65536] if pic.mime == "image/jpeg" else b""
            data = await pixel_work(st, pix.lanczos_unsharp, path, pix.demuxer_of(pic.mime), pix.orientation_of(head, pic.mime), w, h, fmt)
        nm = media_lib.clean_name(name, "image") if name else f"{_stem(pic.name)} ({w}x{h}).{fmt}"
        meta = {"parent": pic.id, "imajutsu": {"kind": "scaled", **info, "parent": pic.id}}
        return await media_lib.ingest(st, user_id=user.id, name=nm, body=_bytes_iter(data), declared=len(data), cap=tc.cap, ephemeral=tc.ephemeral,
                                      expires_at=None if tc.ephemeral else tc.expires, origin="scaled", extra_meta=meta)
    return _spawn(st, user.id, pic.id, work), info


def blend_inputs(st, s, user, aid: str) -> tuple[Pic, dict, Pic, Pic | None]:
    """(the result, the job's imajutsu block, the source picture, the mask picture or None) for the keep-outside blend of a generated result."""
    res = load_pic(st, s, user.id, aid, "The result", missing=404)
    row = res.obj if not res.mem else None
    if row is None or not row.job_id:
        raise ImajutsuError(422, "not_a_result", "Only a picture that Imajutsu generated can be blended with its source.")
    j = s.get(MediaJob, row.job_id)
    block = block_of(st, j) if j is not None and j.user_id == user.id else None
    if block is None:
        raise ImajutsuError(422, "not_a_result", "This picture was not made by Imajutsu, or its settings were not kept.")
    if not block.get("source_asset"):
        raise ImajutsuError(422, "no_source", "This result has no source picture to keep.")
    src = load_pic(st, s, user.id, block["source_asset"], "The source picture")
    maskp = load_pic(st, s, user.id, block["mask_asset"], "The selection mask") if block.get("mask_asset") else None
    return res, block, src, maskp


def blend_work(res_path: Path, res_mime: str, src_path: Path, src_mime: str, mask_path: Path | None, mask_mime: str | None, block: dict, feather: float) -> bytes:
    """Sync: the result where the selection is, the source everywhere else (ffmpeg maskedmerge), at the result's size."""
    res = pix.flatten(_read_pic(res_path, res_mime))
    h, w = res.shape[:2]
    src = pix.flatten(_read_pic(src_path, src_mime))
    sel = block.get("selection") or {}
    pln = block.get("plan") or {}
    if block.get("mode") == "outpaint":
        pad = (pln.get("outpaint") or {}).get("pad")
        if not pad:
            raise pix.PixError("The padding of this outpaint was not kept.")
        src, m = pix.pad_canvas(src, pad["left"], pad["top"], pad["right"], pad["bottom"])
        m_cmp = "neighbor"
    elif mask_path is not None:
        m = pix.luminance_mask(_read_pic(mask_path, mask_mime))
        m_cmp = "bilinear"
    elif sel.get("box"):
        m = pix.box_mask(src.shape[1], src.shape[0], sel["box"])
        m_cmp = "neighbor"
    else:
        raise pix.PixError("This result was made without a selection; there is nothing to keep outside of.")
    if m.shape != src.shape[:2]:
        m = pix.scale(m, src.shape[1], src.shape[0], "bilinear")
    if sel.get("invert"):
        m = 255 - m
    if abs(src.shape[1] / src.shape[0] / (w / h) - 1) > plan_mod.SHAPE_TOLERANCE:
        raise pix.PixError("The result has a different shape from the source, so the two cannot be laid over each other. Generate with the shape 'Same shape as the picture'.")
    src_n, m_n = pix.cover(src, w, h), pix.cover(m, w, h, m_cmp)
    out = pix.masked_merge(src_n, res, m_n, feather)
    return pix.encode(out, "png")


async def start_keep_outside(st, s, user, aid: str, feather: float | None, name: str | None = None) -> media_lib.EditTask:
    principal = services.load_principal(s, user)
    res, block, src, maskp = blend_inputs(st, s, user, aid)
    sel = block.get("selection") or {}
    fe = float(feather) if feather is not None else float(sel.get("feather") or 0)
    fe = _num(fe, "feather", 0, 200)
    tc = _task_ctx(st, s, principal, res)
    s.commit()

    async def work(t):
        with open_pics(st, [res, src, *([maskp] if maskp else [])]) as paths:
            sig = fe * res.width / max(src.width, 1) / 2
            data = await pixel_work(st, blend_work, paths[0], res.mime, paths[1], src.mime, paths[2] if maskp else None, maskp.mime if maskp else None, block, sig)
        nm = media_lib.clean_name(name, "image") if name else f"{_stem(res.name)} (outside kept).png"
        meta = {"parent": res.id, "imajutsu": {"kind": "composited", "parent": res.id, "source": src.id, "mask": maskp.id if maskp else None, "feather": fe,
                                              "label": "The source picture outside the selection, the generated picture inside it", "model": None}}
        return await media_lib.ingest(st, user_id=user.id, name=nm, body=_bytes_iter(data), declared=len(data), cap=tc.cap, ephemeral=tc.ephemeral,
                                      expires_at=None if tc.ephemeral else tc.expires, origin="composited", extra_meta=meta)
    return _spawn(st, user.id, res.id, work)
