"""Imajutsu: the routing table (which engine model serves which mode) and the mode definitions.

The table is DATA (``imajutsu_routing.json`` next to this file, or ``<data_dir>/imajutsu_routing.json`` when an operator put one there). Code never
names a model: it reads the ordered list for a mode, and the first model on it that is installed, allowed for the person and really offers the engine
mode (the engine registry's ``capabilities.modes``) is the recommended one. Reordering the file is all it takes to change the recommendation.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger("semechka.imajutsu")

PACKAGED = Path(__file__).with_name("imajutsu_routing.json")
OVERRIDE_NAME = "imajutsu_routing.json"

# The ten modes, with the labels shown in the interface. ``engine_mode`` is the engine's mode that normally serves it (a routing entry may name another, for
# example restyle on an image-to-image model); None = no model (scaling) or decided from the inputs (advanced).
MODE_DEFS: dict[str, dict] = {
    "generate": {"label": "Generate Image", "engine_mode": "txt2img", "source": False, "mask": False, "prompt": True, "refs_min": 0, "refs_ok": False},
    "iphone_photo": {"label": "iPhone Photo", "engine_mode": "txt2img", "source": False, "mask": False, "prompt": True, "refs_min": 0, "refs_ok": False},
    "edit": {"label": "Edit Image", "engine_mode": "edit", "source": True, "mask": False, "prompt": True, "refs_min": 0, "refs_ok": True},
    "replace_subject": {"label": "Replace Subject", "engine_mode": "edit", "source": True, "mask": False, "prompt": True, "refs_min": 1, "refs_ok": True},
    "inpaint": {"label": "Inpaint", "engine_mode": "inpaint", "source": True, "mask": True, "prompt": True, "refs_min": 0, "refs_ok": True},
    "outpaint": {"label": "Outpaint", "engine_mode": "inpaint", "source": True, "mask": False, "prompt": False, "refs_min": 0, "refs_ok": False},
    "reference": {"label": "Reference Image", "engine_mode": "edit", "source": False, "mask": False, "prompt": False, "refs_min": 1, "refs_ok": True},
    "restyle": {"label": "Restyle", "engine_mode": "img2img", "source": True, "mask": False, "prompt": True, "refs_min": 0, "refs_ok": True},
    "upscale": {"label": "Upscale", "engine_mode": None, "source": True, "mask": False, "prompt": False, "refs_min": 0, "refs_ok": False},
    "advanced": {"label": "Advanced Workflow", "engine_mode": None, "source": False, "mask": False, "prompt": True, "refs_min": 0, "refs_ok": True},
}
MODE_IDS = tuple(MODE_DEFS)
ENGINE_MODES = ("txt2img", "img2img", "edit", "inpaint")
DEFAULT_PIXELS = 921600       # ~0.92 MP: 720x1280


class RoutingError(ValueError):
    pass


def _check(d) -> dict:
    """Validate the shape of a routing document; raises RoutingError with a plain sentence."""
    if not isinstance(d, dict) or not isinstance(d.get("modes"), dict):
        raise RoutingError("the routing file needs a 'modes' object")
    shared = d.get("models") or {}
    if not isinstance(shared, dict):
        raise RoutingError("'models' must be an object")
    for mid, lst in d["modes"].items():
        if mid not in MODE_DEFS:
            raise RoutingError(f"unknown mode '{mid}'")
        if not isinstance(lst, dict) or not isinstance(lst.get("models", []), list):
            raise RoutingError(f"mode '{mid}' needs a 'models' list")
        for key in ("models", "refine"):
            for i, e in enumerate(lst.get(key) or []):
                if not isinstance(e, dict) or not isinstance(e.get("engine_id"), str) or not e["engine_id"]:
                    raise RoutingError(f"mode '{mid}' entry {i + 1} needs an engine_id")
                if e.get("engine_mode") is not None and e["engine_mode"] not in ENGINE_MODES:
                    raise RoutingError(f"mode '{mid}' entry {i + 1}: unknown engine_mode '{e['engine_mode']}'")
                nat = {**(shared.get(e["engine_id"]) or {}).get("native", {}), **(e.get("native") or {})}
                for aid, wh in nat.items():
                    if not (isinstance(wh, list) and len(wh) == 2 and all(isinstance(v, int) and v > 0 for v in wh)):
                        raise RoutingError(f"native size for {aid} must be [width, height]")
    return d


_cache: dict = {}


def load(data_dir: Path | None = None) -> dict:
    """The routing document: the operator's file in the data directory when there is a valid one, else the packaged file. Re-read when the file changes.
    -> {"doc": {...}, "source": "packaged" | "data_dir", "path": str, "problem": str | None}"""
    cands = []
    if data_dir is not None:
        cands.append((Path(data_dir) / OVERRIDE_NAME, "data_dir"))
    cands.append((PACKAGED, "packaged"))
    problem = None
    for path, source in cands:
        try:
            st = path.stat()
        except OSError:
            continue
        key = (str(path), st.st_mtime_ns, st.st_size)
        hit = _cache.get(str(path))
        if hit and hit[0] == key:
            doc = hit[1]
        else:
            try:
                doc = _check(json.loads(path.read_text()))
            except (OSError, ValueError) as e:
                problem = f"{path.name} ({source}) is not usable: {e}"
                log.warning("imajutsu routing: %s", problem)
                continue
            _cache[str(path)] = (key, doc)
        return {"doc": doc, "source": source, "path": str(path), "problem": problem}
    raise RoutingError(problem or "no routing file found")


def avoid_words(doc: dict) -> list[str]:
    return [w for w in doc.get("avoid_words") or [] if isinstance(w, str) and w]


def entries(doc: dict, mode: str, key: str = "models") -> list[dict]:
    """The ordered entries for a mode, each merged over the shared facts of its model (native sizes, quality presets, flags). A copy: callers may edit it."""
    shared = doc.get("models") or {}
    out = []
    for e in (doc.get("modes", {}).get(mode) or {}).get(key) or []:
        merged = {**json.loads(json.dumps(shared.get(e["engine_id"]) or {})), **json.loads(json.dumps(e))}
        merged.setdefault("pixels", doc.get("pixels") or DEFAULT_PIXELS)
        out.append(merged)
    return out


def entry_for(doc: dict, mode: str, engine_id: str) -> dict | None:
    for e in entries(doc, mode):
        if e["engine_id"] == engine_id:
            return e
    return None


def shared_facts(doc: dict, engine_id: str) -> dict:
    """What the file knows about a model regardless of mode (for a model chosen by hand that is not on the mode's list)."""
    d = json.loads(json.dumps((doc.get("models") or {}).get(engine_id) or {}))
    d.setdefault("pixels", doc.get("pixels") or DEFAULT_PIXELS)
    return d
