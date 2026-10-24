from __future__ import annotations

import hashlib
import json
import threading
import time

from sqlalchemy import select

from .models import AuditEvent

_lock = threading.Lock()
_FORBIDDEN = {"password", "token", "secret", "content", "prompt", "api_key", "text"}


def _clean(detail: dict) -> dict:
    out = {}
    for k, v in (detail or {}).items():
        if k.lower() in _FORBIDDEN:
            continue
        out[k] = v
    return out


def record(session, actor: str, action: str, target: str = "", detail: dict | None = None, ip: str = "") -> AuditEvent:
    """Append to the hash chain. Never pass message content: sensitive keys are dropped defensively."""
    with _lock:
        prev = session.execute(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(1)).scalar_one_or_none()
        ev = AuditEvent(ts=time.time(), actor=actor or "", action=action, target=target or "",
                        detail=_clean(detail or {}), ip=ip or "", prev_hash=prev.hash if prev else "")
        ev.hash = _digest(ev)
        session.add(ev)
        session.flush()
        return ev


def _digest(ev: AuditEvent) -> str:
    body = json.dumps([round(ev.ts, 6), ev.actor, ev.action, ev.target, ev.detail, ev.ip, ev.prev_hash],
                      sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode()).hexdigest()


def verify_chain(session) -> dict:
    prev = ""
    n = 0
    for ev in session.execute(select(AuditEvent).order_by(AuditEvent.id)).scalars():
        if ev.prev_hash != prev or _digest(ev) != ev.hash:
            return {"ok": False, "broken_at": ev.id, "checked": n}
        prev = ev.hash
        n += 1
    return {"ok": True, "checked": n}
