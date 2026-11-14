"""Yaob systems behind the OpenAI-compatible adapter (contract `yaob-origin/1`, see docs/YAOB_ORIGIN.md).

A Yaob server adds two things to the ordinary OpenAI surface, and clients that ignore them keep working:

* every `/v1/models` entry carries `name` (the runtime display name) and `yaob` (the identity record: the neural core,
  the node network and the capabilities, built by the server at start-up from what is really loaded);
* stream chunks may carry a top-level `yaob` key with `status` (what the system is doing before it writes),
  `sources` (the documents the answer cites) and `provenance` (the id of the server's own record of the reply).

Everything here comes from another process, and sources come from the open web, so all of it is validated and bounded
before it is stored or shown. Unknown keys are dropped; a malformed value is ignored, never an error.
"""
from __future__ import annotations

import re
from urllib.parse import urlsplit

WEB_MODES = ("auto", "on", "off")
STATUS_STATES = ("searching_web", "reading_sources", "verifying")
SOURCE_KINDS = ("web", "reference")
MAX_SOURCES = 20

_CTRL = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁦-⁩]")
_DATE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?([T ][0-9:.]+(Z|[+-]\d{2}:?\d{2})?)?$")
_HOST = re.compile(r"^[a-z0-9.-]{1,253}$|^\[[0-9a-f:.]+\]$", re.I)
_CAP = re.compile(r"^[a-z0-9_.-]{1,40}$")
_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def text(v, limit: int) -> str | None:
    """A single line of plain text, control and bidi characters removed, at most `limit` characters."""
    if not isinstance(v, str):
        return None
    s = " ".join(_CTRL.sub(" ", v).split())
    if len(s) > limit:
        s = s[: limit - 1].rstrip() + "…"
    return s or None


def safe_url(v) -> str | None:
    """An absolute http(s) URL with a host and no credentials, or None."""
    if not isinstance(v, str) or len(v) > 2048 or _CTRL.search(v) or any(c.isspace() for c in v):
        return None
    try:
        u = urlsplit(v)
        host = u.hostname
        _ = u.port  # raises on a malformed port
    except ValueError:
        return None
    if u.scheme.lower() not in ("http", "https") or not host or u.username or u.password or not _HOST.match(host):
        return None
    return v


def _date(v) -> str | None:
    s = text(v, 40)
    return s if s and _DATE.match(s) else None


def _int(v, lo: int, hi: int) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v:
        return None
    n = int(v)
    return n if lo <= n <= hi else None


# ---- discovery ------------------------------------------------------------------------------------------------
def identity(rec) -> dict | None:
    """The identity record, reduced to known fields. The node network's size and the core's parameter count are kept
    in separate places and never combined."""
    if not isinstance(rec, dict):
        return None
    core_in = rec.get("core") if isinstance(rec.get("core"), dict) else {}
    mpn_in = rec.get("mpn") if isinstance(rec.get("mpn"), dict) else {}
    core = {"name": text(core_in.get("name"), 80), "repo": text(core_in.get("repo"), 120),
            "revision": text(core_in.get("revision"), 64), "quantization": text(core_in.get("quantization"), 20),
            "parameters": _int(core_in.get("parameters"), 1, 10**13), "kind": text(core_in.get("kind"), 80)}
    mpn = {"name": text(mpn_in.get("name"), 80), "designation": text(mpn_in.get("designation"), 16),
           "nodes": _int(mpn_in.get("nodes"), 0, 10**13), "relationships": _int(mpn_in.get("relationships"), 0, 10**14),
           "counted_at": _date(mpn_in.get("counted_at")), "kind": text(mpn_in.get("kind"), 120)}
    caps = rec.get("capabilities") if isinstance(rec.get("capabilities"), list) else []
    out = {"system": text(rec.get("system"), 40), "display_name": text(rec.get("display_name"), 120),
           "core": {k: v for k, v in core.items() if v is not None},
           "mpn": {k: v for k, v in mpn.items() if v is not None},
           "capabilities": list(dict.fromkeys(c for c in caps if isinstance(c, str) and _CAP.match(c)))[:20],
           "runtime": text(rec.get("runtime"), 40),
           "legacy_ids": [x for x in (rec.get("legacy_ids") or []) if isinstance(x, str) and _ID.match(x)][:10]
           if isinstance(rec.get("legacy_ids"), list) else []}
    out = {k: v for k, v in out.items() if v not in (None, {}, [])}
    return out or None


def discovered_meta(entry: dict) -> dict:
    """Meta keys for one /v1/models entry: `display_name` from `name`, and `identity` + `capabilities` from `yaob`.
    Empty for servers that report neither, so their models are stored exactly as before."""
    meta: dict = {}
    name = text(entry.get("name"), 120) if isinstance(entry, dict) else None
    if name and name != entry.get("id"):
        meta["display_name"] = name
    ident = identity(entry.get("yaob")) if isinstance(entry, dict) else None
    if ident:
        meta["identity"] = ident
        if ident.get("capabilities"):
            meta["capabilities"] = list(ident["capabilities"])
        if not meta.get("display_name") and ident.get("display_name"):
            meta["display_name"] = ident["display_name"]
    return meta


def core_label(meta: dict | None) -> str | None:
    """The neural cognitive core, e.g. "Qwen3.5-4B"."""
    return ((meta or {}).get("identity") or {}).get("core", {}).get("name")


def network_label(meta: dict | None) -> str | None:
    """The node network with its designation, e.g. "Yaob Origin N119M". Never a parameter count."""
    mpn = ((meta or {}).get("identity") or {}).get("mpn") or {}
    if not mpn.get("name"):
        return None
    des = mpn.get("designation")
    return f"{mpn['name']} {des}" if des and des not in mpn["name"] else mpn["name"]


def has_web_search(meta: dict | None) -> bool:
    return "web_search" in ((meta or {}).get("capabilities") or [])


# ---- stream extension -----------------------------------------------------------------------------------------
def status(st) -> dict | None:
    if not isinstance(st, dict) or st.get("state") not in STATUS_STATES:
        return None
    ev = {"type": "status", "state": st["state"]}
    q = text(st.get("query"), 200)
    if q and st["state"] == "searching_web":
        ev["query"] = q
    n = _int(st.get("count"), 0, 100)
    if n is not None and st["state"] == "reading_sources":
        ev["count"] = n
    return ev


def sources(items) -> list[dict]:
    """User-facing citations: numbered, deduplicated by number, http(s) links only, every string bounded."""
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    seen: set[int] = set()
    for i, s in enumerate(items[:50]):
        if not isinstance(s, dict):
            continue
        url = safe_url(s.get("url"))
        if not url:
            continue
        n = _int(s.get("n"), 1, 100) or i + 1
        if n in seen:
            continue
        host = (urlsplit(url).hostname or "").lower()
        site = text(s.get("site"), 120) or (host[4:] if host.startswith("www.") else host)
        rec = {"n": n, "kind": s.get("kind") if s.get("kind") in SOURCE_KINDS else "web", "url": url,
               "title": text(s.get("title"), 300) or site, "site": site, "published": _date(s.get("published")),
               "retrieved_at": _date(s.get("retrieved_at")), "snippet": text(s.get("snippet"), 500)}
        out.append({k: v for k, v in rec.items() if v is not None})
        seen.add(n)
        if len(out) >= MAX_SOURCES:
            break
    return sorted(out, key=lambda r: r["n"])


def provenance_id(p) -> str | None:
    pid = p.get("id") if isinstance(p, dict) else None
    return pid if isinstance(pid, str) and _ID.match(pid) else None


def generation_speed(ext) -> float | None:
    """The server's own generation speed (`timing.tokens_per_s`, tokens over generation time). Semechka's measurement
    from first to last delivered token is wrong for Yaob replies that are released in one piece after a check or a
    tool decision (2026-11-14: "7099 tok/s" for 83 tokens)."""
    t = ext.get("timing") if isinstance(ext, dict) else None
    v = t.get("tokens_per_s") if isinstance(t, dict) else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and 0 < v < 100000 else None


def stream_events(ext) -> list[dict]:
    """Adapter events for one chunk's `yaob` value. `route`, `validation` and `timing` stay with the server."""
    if not isinstance(ext, dict):
        return []
    out = []
    st = status(ext.get("status"))
    if st:
        out.append(st)
    if "sources" in ext and isinstance(ext["sources"], list):
        out.append({"type": "sources", "sources": sources(ext["sources"])})
    pid = provenance_id(ext.get("provenance"))
    if pid:
        out.append({"type": "provenance", "id": pid})
    return out
