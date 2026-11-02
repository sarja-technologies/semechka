"""Trinodal AI: what Semechka decides for the same-origin proxy at /trinodal/ (see routers/trinodal.py).

Trinodal AI is a separate service with its own management UI. Semechka serves that UI at /trinodal/ on its own origin,
so the browser needs no second address, no CORS and no token. This module holds the pure decisions so they can be
tested without a network: which permission a request needs, which headers cross the proxy in each direction, and how
the service token is read.

The service token is a file on the server (SEMECHKA_TRINODAL_TOKEN_FILE, default <data dir>/trinodal-token). It is read
here, on use, and handed to the proxy for the Authorization header. It is never stored in the database, never logged and
never returned by any route.
"""
from __future__ import annotations

import logging
import re
import threading
from pathlib import Path

from . import perms

log = logging.getLogger("semechka.trinodal")

# scope name (X-Trinodal-Scopes) -> the permission that grants it
SCOPE_PERM = {"read": "trinodal.view", "train": "trinodal.train", "admin": "trinodal.admin"}
RESERVED = "_semechka"  # /trinodal/_semechka/... is answered by Semechka itself and never forwarded

# Non-GET requests under these are the training surface. Every other non-GET is treated the same way (train): a state
# change Semechka does not recognise must not be possible with read access.
TRAIN_PREFIXES = ("api/training", "api/datasets", "api/jobs", "api/checkpoints", "api/deploy")


class BadPath(ValueError):
    pass


def clean_path(path: str) -> str:
    """The path below /trinodal/, decoded, validated and without a leading slash. The permission decision and the
    forwarded request use this one string, so they cannot disagree about which resource is meant."""
    if len(path) > 2048 or re.search(r"[\x00-\x1f\x7f\\]", path) or "//" in path or path.startswith("/"):
        raise BadPath("The address is not valid.")
    if any(seg in (".", "..") for seg in path.split("/")):
        raise BadPath("The address is not valid.")
    return path


def required_scope(method: str, path: str) -> str:
    """read | train | admin for one request. `path` is clean_path() output."""
    segs = path.strip("/").lower().split("/")
    if len(segs) >= 2 and segs[0] == "api" and segs[1].startswith("admin"):
        return "admin"  # configuration, tokens and settings: every method, reading included
    if method in ("GET", "HEAD"):
        return "read"
    return "train"


def path_class(method: str, path: str) -> str:
    """A short, stable label for the audit log: which part of Trinodal a request touched."""
    segs = path.strip("/").lower().split("/")
    if len(segs) >= 2 and segs[0] == "api":
        head = f"api/{segs[1]}"
        if head in TRAIN_PREFIXES or segs[1].startswith("admin"):
            return head
        return "api/other"
    return "ui"


def scopes_held(p: perms.Principal) -> list[str]:
    return [k for k, perm in SCOPE_PERM.items() if p.can(perm)]


def holds_any(p: perms.Principal) -> bool:
    return any(p.can(perm) for perm in SCOPE_PERM.values())


def base_url(s, st) -> str:
    return st.settings.deployment(s, "trinodal.url") or ""


def instance_name(st) -> str:
    n = re.sub(r"[^A-Za-z0-9._-]", "", st.cfg.instance_name or "")[:48]
    return n or "smk-" + st.vault.mac("name", "trinodal-instance")[:8]


# ---- headers --------------------------------------------------------------------------------------------------
# Allow-lists, not block-lists: a header nobody thought about does not cross the proxy.
REQUEST_ALLOW = {"accept", "accept-language", "accept-encoding", "content-type", "range", "if-none-match", "if-match",
                 "if-modified-since", "if-unmodified-since", "if-range", "cache-control", "pragma", "last-event-id"}
RESPONSE_ALLOW = {"content-type", "content-length", "content-encoding", "content-disposition", "content-language",
                  "content-range", "accept-ranges", "etag", "last-modified", "expires", "vary", "retry-after",
                  "x-request-id", "x-total-count"}
# These never come back from Trinodal, whatever it sends: they would replace or loosen Semechka's own.
NEVER_RESPONSE = {"set-cookie", "set-cookie2", "content-security-policy", "content-security-policy-report-only",
                  "x-frame-options", "x-content-type-options", "strict-transport-security", "referrer-policy",
                  "permissions-policy", "cross-origin-opener-policy", "cross-origin-embedder-policy",
                  "cross-origin-resource-policy", "access-control-allow-origin", "access-control-allow-credentials",
                  "access-control-allow-headers", "access-control-allow-methods", "access-control-expose-headers",
                  "www-authenticate", "server"}
MAX_RESPONSE_HEADER_BYTES = 32 * 1024


def request_headers(incoming, *, token: str, actor: str, scopes: list[str], request_id: str, version: str) -> dict[str, str]:
    """Headers for the upstream request. Cookies, Authorization, Origin, Referer, CSRF and every X-Trinodal-* the browser
    may have sent are dropped; the three that carry identity are set here from server-side facts only."""
    out = {k.lower(): v for k, v in incoming.items() if k.lower() in REQUEST_ALLOW}
    out.setdefault("accept-encoding", "identity")  # never hand the browser a compressed body it did not ask for
    out["authorization"] = f"Bearer {token}"
    out["x-trinodal-actor"] = actor
    out["x-trinodal-scopes"] = ",".join(scopes)
    out["x-request-id"] = request_id
    out["user-agent"] = f"Semechka/{version} (trinodal-proxy)"
    return out


def response_headers(upstream, *, sse: bool, api: bool) -> dict[str, str]:
    """Headers for the browser. Semechka's own security headers (CSP, nosniff, frame and referrer policy) are added by the
    application for every response and are not replaced by anything Trinodal sends."""
    out: dict[str, str] = {}
    for k, v in upstream.items():
        lk = k.lower()
        if lk in NEVER_RESPONSE:
            continue
        if lk in RESPONSE_ALLOW or lk.startswith("x-trinodal-"):
            out[lk] = v
    # Everything here is behind a sign-in: never a shared cache's to keep. A page the person may have lost access to must
    # not be served from a cache either.
    cc = upstream.get("cache-control", "")
    if sse:
        out["cache-control"] = "no-cache, no-transform"
        out["x-accel-buffering"] = "no"
    elif api:
        out["cache-control"] = "no-store"
    elif "no-store" not in cc.lower() and "private" not in cc.lower():
        out["cache-control"] = ("private, " + cc) if cc else "private, no-cache"
    else:
        out["cache-control"] = cc
    return out


# ---- the service token ----------------------------------------------------------------------------------------
_TOKEN_OK = re.compile(r"[\x21-\x7e]{1,4096}")
_lock = threading.Lock()
_cache: dict[str, tuple[tuple[int, int], str | None]] = {}
_warned: set[str] = set()


def read_token(path: Path) -> str | None:
    """The service token from its file, or None when it is missing, unreadable or malformed. Cached by modification
    time, so replacing the file (token rotation) takes effect at once and without a restart. The value is returned to the
    proxy only; callers must not log it."""
    key = str(path)
    try:
        stt = path.stat()
        sig = (stt.st_mtime_ns, stt.st_size)
        with _lock:
            hit = _cache.get(key)
        if hit and hit[0] == sig:
            return hit[1]
        raw = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError):
        return None
    tok = raw if _TOKEN_OK.fullmatch(raw) else None
    with _lock:
        _cache[key] = (sig, tok)
        if key not in _warned:
            _warned.add(key)
            if tok is None:
                log.warning("The Trinodal token file %s is empty or not a single line of printable characters.", path)
            elif stt.st_mode & 0o077:
                log.warning("The Trinodal token file %s can be read by other users (mode %o). Restrict it to 0600.",
                            path, stt.st_mode & 0o777)
    return tok
