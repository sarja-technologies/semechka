from __future__ import annotations

import re
import time

from sqlalchemy import select

from . import adapters
from .adapters import AdapterError
from .models import Backend, Model


def adapter_for(b: Backend, st) -> adapters.Adapter:
    key = st.vault.decrypt(b.api_key_enc, "backend") if b.api_key_enc else None
    return adapters.make(b.adapter, b.base_url, key, {**(b.options or {}), "external": b.external})


def friendly_alias(real_id: str) -> str:
    base = real_id.split("/")[-1]
    base = re.sub(r"\.(gguf|bin)$", "", base, flags=re.I)
    base = base.split(":", 1)[-1] if base.startswith(("local:", "endpoint:", "image:", "video:")) else base
    return base[:100]


async def sync_models(session_factory, st, backend_id: str) -> dict:
    """Discover from the backend and upsert the catalogue. New models start disabled and with no access grants."""
    with session_factory() as s:
        b = s.get(Backend, backend_id)
        if b is None:
            raise AdapterError("Unknown backend.", status=404)
        ad = adapter_for(b, st)
    found = await ad.discover()
    health = await ad.health()
    with session_factory() as s:
        b = s.get(Backend, backend_id)
        b.options = {**(b.options or {}), "version": health.get("version"), "last_seen": time.time()}
        existing = {m.real_id: m for m in s.execute(select(Model).where(Model.backend_id == backend_id)).scalars()}
        added = updated = 0
        seen = set()
        for d in found:
            seen.add(d.real_id)
            meta = dict(d.meta)
            if d.external:
                meta["external"] = True
            if d.loaded is not None:
                meta["loaded"] = d.loaded
            m = existing.get(d.real_id)
            if m is None:
                s.add(Model(backend_id=backend_id, real_id=d.real_id, alias=friendly_alias(d.real_id),
                            capabilities=d.capabilities, meta=meta, enabled=False))
                added += 1
            else:
                # Replaced, not merged: a value the backend stopped reporting must not linger. A hand-registered
                # model the backend now lists becomes an ordinary discovered one.
                m.capabilities, m.meta, m.available = d.capabilities, meta, True
                updated += 1
        for rid, m in existing.items():
            if rid not in seen and not (m.meta or {}).get("manual"):
                m.available = False
        s.commit()
    return {"found": len(found), "added": added, "updated": updated, "version": health.get("version")}
