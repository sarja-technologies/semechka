"""Experimental tools from the media engine: speech, music, picture tools and 3D (``tool:<id>`` models, capability ``media_tool``).

Where they come from: the Spark media engine publishes them in ``tools`` of /v1/capabilities, separately from its image/video models and profiles, and
only offers the ones it has rendered and reviewed on that server (adapters/spark_media.py turns them into models). They are never offered by the chat,
Images or Videos choosers (none has an ``image``/``video``/``chat`` capability), only by the Experimental pages through /api/media/extras.

Who may use one (all enforced here, never by the page):
* the person can use Images or Videos (the media block is on for them: same mode switches, same daily and concurrency limits, same storage rules), AND
* the tool's model row is enabled by an administrator and granted to them (model grants; a grant limited to modes applies when it includes ``image``),
  exactly like any other model: new rows from a catalog sync start disabled with no grants, so nothing appears until an administrator turns one on.

A tool job is an ordinary MediaJob of kind ``tool``: polling, cancelling, the Library, retention and no-save all work unchanged.  Each stored file is filed by
what it is (``audio``, ``image`` or ``3d``; media_svc._asset_kind).
"""
from __future__ import annotations

import json
import time
import uuid
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select

from . import catalog_svc, media_svc, services
from .backends_svc import adapter_for
from .media_models import MediaJob
from .models import Backend, Model, User

TOOL_CAPABILITY = "media_tool"
JOB_KIND = "tool"
GROUPS = ("audio", "tools", "3d")
GRANT_MODE = "image"        # administrators restrict grants to chat/code/vision/image/video; a tool counts as image-like media work


def tool_info(m: Model) -> dict | None:
    return media_svc.media_info(m).get("tool")


def is_tool(m: Model | None) -> bool:
    return bool(m is not None and (m.real_id or "").startswith("tool:") and TOOL_CAPABILITY in catalog_svc.capabilities(m))


def media_on(s, p, st) -> str | None:
    """None when the person can use generated media at all (Images or Videos is on for them), else the kind used for limits. Returns the kind."""
    for k in ("image", "video"):
        if services.mode_state(s, p, st, k)["enabled"] and st.settings.value_for(s, f"ui.{k}_gen", p):
            return k
    return None


def tool_access(s, p, m: Model, b: Backend, st, grants=None) -> tuple[bool, str]:
    """services.model_access for an Experimental tool: same order of checks (available, enabled or models.use_all, capability, grants with deny winning,
    external egress)."""
    use_all = p.can("models.use_all")
    if not m.available or not b.enabled or (not m.enabled and not use_all):
        return False, "This tool is not available."
    if not is_tool(m):
        return False, "This is not an Experimental tool."
    if grants is None:
        from .models import ModelGrant
        grants = list(s.execute(select(ModelGrant).where(ModelGrant.model_id == m.id)).scalars())
    ok, why = catalog_svc.decide(grants, p, GRANT_MODE)
    if not ok and not (use_all and not catalog_svc.denied(grants, p, GRANT_MODE)):
        return False, why
    if services.is_external(m, b) and not st.settings.deployment(s, "privacy.external_egress"):
        return False, "This tool runs on an external service, which your organisation has turned off."
    return True, ""


def accessible_tools(s, p, st) -> list[tuple[Model, Backend]]:
    """Tools this person may use, in the administrator's order: offered first, then the ones reached only through models.use_all."""
    backends = {b.id: b for b in s.execute(select(Backend)).scalars()}
    by_model = catalog_svc.grants_by_model(s)
    offered, restricted = [], []
    for m in catalog_svc.ordered_models(s):
        b = backends.get(m.backend_id)
        if b is None or not is_tool(m):
            continue
        g = by_model.get(m.id, [])
        if tool_access(s, p, m, b, st, g)[0]:
            (restricted if services.is_restricted(s, p, m, GRANT_MODE, g) else offered).append((m, b))
    return offered + restricted


def availability(st, s, p, modes: dict | None = None) -> dict:
    """The Experimental navigation entry: {enabled, reason, groups: {audio|tools|3d: number of tools}}."""
    if media_on(s, p, st) is None:
        return {"enabled": False, "reason": "Images and videos are not available to you.", "groups": {}}
    groups: dict[str, int] = {}
    for m, _b in accessible_tools(s, p, st):
        g = (tool_info(m) or {}).get("group")
        if g in GROUPS:
            groups[g] = groups.get(g, 0) + 1
    return {"enabled": bool(groups), "reason": "" if groups else "No Experimental tool is enabled for you.", "groups": groups}


def tool_out(s, p, m: Model, b: Backend, grants=None) -> dict:
    t = tool_info(m) or {}
    meta = m.meta or {}
    from .backends_svc import friendly_alias
    name = meta.get("engine_name") if m.alias == friendly_alias(m.real_id) and meta.get("engine_name") else m.alias   # the engine's own name unless an administrator renamed it
    return {"id": m.id, "alias": name, "description": meta.get("description"), "license": meta.get("license"),
            "caution": media_svc.media_info(m).get("caution"), "tool": t.get("id"), "group": t.get("group"), "output": t.get("output"),
            "ready": bool(t.get("ready")), "reason": t.get("reason"), "inputs_schema": t.get("inputs_schema") or {}, "outputs": t.get("outputs") or [],
            "typical_seconds": t.get("typical_seconds"), "external": services.is_external(m, b),
            "restricted": services.is_restricted(s, p, m, GRANT_MODE, grants if grants is not None else [])}


def validate_inputs(m: Model, inputs: dict, files: dict[str, list[tuple[str, int]]], max_bytes: int) -> dict:
    """The engine's published input schema, checked with the same rules as editing profiles (media_svc.validate_profile_inputs)."""
    t = tool_info(m) or {}
    shim = SimpleNamespace(meta={"media": {"profile": {"ready": t.get("ready"), "reason": t.get("reason"), "inputs_schema": t.get("inputs_schema") or {}}}})
    return media_svc.validate_profile_inputs(shim, inputs, files, max_bytes)      # type: ignore[arg-type]


async def submit_tool(st, user: User, m: Model, b: Backend, inputs: dict, uploads: dict[str, list[tuple[Any, str, str]]], save: bool,
                      expires_at: float | None, *, sources: dict[str, list[str]] | None = None) -> str:
    """Hand the files and settings to the engine, which runs the tool as an ordinary durable job. Raises AdapterError and leaves nothing behind."""
    job_id = "mj_" + uuid.uuid4().hex[:24]
    ad = adapter_for(b, st)
    tid = m.real_id.split(":", 1)[1]
    sent = dict(inputs)
    for name, items in uploads.items():
        sent[name] = [await ad.media_upload(fo, mime, fname) for fo, mime, fname in items][0]
    remote = await ad.media_submit_tool(tid, sent, job_id, title=f"semechka experimental {tid}")
    schema = (tool_info(m) or {}).get("inputs_schema") or {}
    prompt = str(next((inputs[k] for k in ("text", "prompt", "tags") if isinstance(inputs.get(k), str) and inputs[k]), "")
                 or next((v for k, v in inputs.items() if isinstance(v, str) and v and (schema.get(k) or {}).get("type") == "string"), ""))
    params = {**{k: v for k, v in inputs.items() if k not in ("text", "prompt")}, "tool": tid, **({"sources": sources} if sources else {})}
    with st.db.session(write=True) as s:
        s.add(MediaJob(id=job_id, user_id=user.id, kind=JOB_KIND, model_id=m.id, backend_id=b.id, remote_id=remote, save=save,
                       prompt_enc=st.vault.encrypt(prompt, "media") if save else None,
                       params_enc=st.vault.encrypt(json.dumps(params), "media") if save else None,
                       status="queued", progress_text="Queued", started_at=None, expires_at=expires_at if save else None))
    return job_id


def expires_for(days) -> float | None:
    return time.time() + days * 86400 if days and days > 0 else None


def gate(ctx) -> str:
    """Refuse (HTTP) unless the person can use generated media; returns the kind whose limits apply (image or video)."""
    from fastapi import HTTPException
    k = media_on(ctx.s, ctx.principal, ctx.st)
    if k is None:
        raise HTTPException(403, {"code": "forbidden", "message": "Experimental tools need Images or Videos, which are not available to you."})
    return k
