"""The Semechka side of the Schemra proxy interface: pure helpers, no web framework and no Schemra code.

Semechka and Schemra meet only over HTTP. Semechka authenticates the browser, decides which Schemra role the person
may have, and forwards the request to a Schemra on this machine with signed identity headers (docs/SCHEMRA_INTEGRATION.md):

    X-Forwarded-Prefix: /schemra
    X-Schemra-User / -Role / -Ts / -Nonce / -Sig
    Sig = hex HMAC-SHA256(secret, "v1\\n" + METHOD + "\\n" + PATH_WITH_QUERY + "\\n" + TS + "\\n" + NONCE + "\\n" + USER + "\\n" + ROLE)

PATH_WITH_QUERY is the request target Schemra receives once the prefix is stripped, byte for byte as it is sent
(`/api/lab/boards?x=1`). The key is the secret file's content with surrounding whitespace removed, as UTF-8 bytes.

Everything in this module is deterministic and unit-testable; the route that uses it is routers/schemra.py.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import httpx

PREFIX = "/schemra"
SIGNATURE_VERSION = "v1"
CONNECT_TIMEOUT_S = 5.0
WRITE_TIMEOUT_S = 60.0
SECRET_MIN_LEN = 32
SAFE_METHODS = frozenset({"GET", "HEAD"})
PROXY_METHODS = ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")

# Roles Semechka may assert, strongest first, and the Semechka permission behind each (perms.py).
ROLE_PERMISSIONS = (("admin", "schemra.admin"), ("operator", "schemra.operate"), ("viewer", "schemra.view"))


class PathRefused(ValueError):
    """The request path cannot be forwarded (traversal, ambiguity, control characters). Safe to show."""


class SecretError(RuntimeError):
    """The shared secret cannot be used. The message never contains the secret."""


# ---- settings ------------------------------------------------------------------------------------------------
def validate_upstream(value) -> str:
    """Only a Schemra on this machine: http, a loopback address literal or `localhost`, an optional port, no path.

    Returns the normalised origin. The check is repeated for every request (see `upstream_origin`), so a value that
    reached the database some other way is still refused."""
    from .settings_reg import SettingError
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 0x21 or ord(c) > 0x7e for c in value.strip()):
        raise SettingError("Enter an address like http://127.0.0.1:8820.")
    value = value.strip()
    try:
        u = urlsplit(value)
        port = u.port
        host = u.hostname
    except ValueError:
        raise SettingError("That is not a valid address.") from None
    if u.scheme != "http":
        raise SettingError("Use http://. Schemra runs on this machine, so the connection never leaves it.")
    if u.username is not None or u.password is not None or "@" in u.netloc:
        raise SettingError("The address cannot carry a user name or password.")
    if u.path not in ("", "/") or u.query or u.fragment:
        raise SettingError("Give the address only (host and port), without a path.")
    if not host or not _is_loopback(host):
        raise SettingError("Schemra must be on this machine: use 127.0.0.1, ::1 or localhost.")
    if port is not None and not 1 <= port <= 65535:
        raise SettingError("The port must be between 1 and 65535.")
    return _origin(host, port)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _origin(host: str, port: int | None) -> str:
    h = f"[{host}]" if ":" in host else host
    return f"http://{h}" + (f":{port}" if port else "")


def upstream_origin(value) -> tuple[str, str]:
    """(connection origin, Host header value). `localhost` is connected to as 127.0.0.1 so a resolver is never consulted."""
    origin = validate_upstream(value)
    u = urlsplit(origin)
    netloc = u.netloc
    conn = origin.replace("//localhost", "//127.0.0.1", 1) if u.hostname == "localhost" else origin
    return conn, netloc


def make_timeout(read_s: float) -> httpx.Timeout:
    """Connect fails fast (the lab is on this machine); the read timeout is the silence between two bytes, which
    stays above Schemra's 15 second keep-alive ping."""
    return httpx.Timeout(connect=CONNECT_TIMEOUT_S, read=float(read_s), write=WRITE_TIMEOUT_S, pool=CONNECT_TIMEOUT_S)


# ---- role mapping --------------------------------------------------------------------------------------------
def schemra_role(principal) -> str | None:
    """The strongest Schemra role the person's Semechka permissions grant, or None. Deny rules win exactly as they do
    everywhere else in Semechka, so a person denied `schemra.*` gets nothing."""
    for role, perm in ROLE_PERMISSIONS:
        if principal.can(perm):
            return role
    return None


_SAFE_IDENT = re.compile(r"^[A-Za-z0-9._%+@-]{1,200}$")


def identity_name(user) -> str:
    """The value of X-Schemra-User: the account's e-mail address (lower case), or the account id when the address has
    characters that do not belong in a header."""
    email = (user.email or "").strip().lower()
    return email if _SAFE_IDENT.match(email) else user.id


# ---- the shared secret -----------------------------------------------------------------------------------------
_secret_lock = threading.Lock()
_secret_cache: dict[str, tuple[tuple, bytes]] = {}


def load_secret(path: Path) -> bytes:
    """Read the secret file. It must be a regular file owned by the account running Semechka and readable by nobody else.
    The content is cached until the file changes, so a rotation needs no restart."""
    p = str(path)
    try:
        st = os.stat(p)
    except FileNotFoundError:
        raise SecretError(f"The Schemra secret file {p} does not exist.") from None
    except OSError as e:
        raise SecretError(f"The Schemra secret file {p} cannot be read ({type(e).__name__}).") from None
    if not stat.S_ISREG(st.st_mode):
        raise SecretError(f"The Schemra secret file {p} is not a regular file.")
    if st.st_mode & 0o077:
        raise SecretError(f"The Schemra secret file {p} is readable by other accounts. Run: chmod 600 {p}")
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise SecretError(f"The Schemra secret file {p} is not owned by the account running Semechka.")
    key = (st.st_mtime_ns, st.st_size, st.st_ino)
    with _secret_lock:
        hit = _secret_cache.get(p)
        if hit and hit[0] == key:
            return hit[1]
    try:
        data = Path(p).read_bytes().strip()
    except OSError as e:
        raise SecretError(f"The Schemra secret file {p} cannot be read ({type(e).__name__}).") from None
    if len(data) < SECRET_MIN_LEN:
        raise SecretError(f"The Schemra secret in {p} is shorter than {SECRET_MIN_LEN} characters.")
    with _secret_lock:
        _secret_cache[p] = (key, data)
    return data


# ---- signing -------------------------------------------------------------------------------------------------
def signing_string(method: str, path_with_query: str, ts: str, nonce: str, user: str, role: str) -> bytes:
    return f"{SIGNATURE_VERSION}\n{method}\n{path_with_query}\n{ts}\n{nonce}\n{user}\n{role}".encode()


def sign(secret: bytes, method: str, path_with_query: str, ts: str, nonce: str, user: str, role: str) -> str:
    return hmac.new(secret, signing_string(method, path_with_query, ts, nonce, user, role), hashlib.sha256).hexdigest()


def identity_headers(secret: bytes, method: str, path_with_query: str, user: str, role: str, *,
                     now: float | None = None, nonce: str | None = None) -> list[tuple[str, str]]:
    ts = str(int(time.time() if now is None else now))
    nonce = nonce or secrets.token_hex(16)
    return [("X-Forwarded-Prefix", PREFIX), ("X-Schemra-User", user), ("X-Schemra-Role", role), ("X-Schemra-Ts", ts),
            ("X-Schemra-Nonce", nonce), ("X-Schemra-Sig", sign(secret, method, path_with_query, ts, nonce, user, role))]


# ---- paths ---------------------------------------------------------------------------------------------------
_PCT = re.compile(r"%(.{0,2})")
_HEX2 = re.compile(r"^[0-9A-Fa-f]{2}$")


def upstream_target(raw_path: bytes, raw_query: bytes) -> str:
    """The request target to send to Schemra (path below the prefix, plus the query), or PathRefused.

    Works on the path as it arrived, before any decoding: `..`, an empty segment, a backslash, a control character and
    the percent-encoded forms of dot, slash, backslash and controls are all refused rather than normalised, so what
    Semechka checked is exactly what Schemra receives."""
    try:
        path = raw_path.decode("ascii")
        query = raw_query.decode("ascii")
    except UnicodeDecodeError:
        raise PathRefused("The address contains characters that must be percent-encoded.") from None
    if path != PREFIX and not path.startswith(PREFIX + "/"):
        raise PathRefused("Not a Schemra address.")
    rest = path[len(PREFIX):] or "/"
    for ch in rest + query:
        if ord(ch) < 0x21 or ord(ch) > 0x7e:
            raise PathRefused("The address contains a space or control character.")
    if "\\" in rest or "//" in rest:
        raise PathRefused("The address contains an empty path segment or a backslash.")
    for m in _PCT.finditer(rest):
        if not _HEX2.match(m.group(1)):
            raise PathRefused("The address contains a malformed percent-escape.")
        b = int(m.group(1), 16)
        if b < 0x20 or b == 0x7f or b in (0x2e, 0x2f, 0x5c):
            raise PathRefused("The address contains an encoded dot, slash, backslash or control character.")
    if any(seg in (".", "..") for seg in rest.split("/")):
        raise PathRefused("The address contains a '.' or '..' segment.")
    for m in _PCT.finditer(query):
        if not _HEX2.match(m.group(1)):
            raise PathRefused("The query contains a malformed percent-escape.")
        b = int(m.group(1), 16)
        if b < 0x20 or b == 0x7f:
            raise PathRefused("The query contains an encoded control character.")
    return rest + ("?" + query if query else "")


# ---- request headers -----------------------------------------------------------------------------------------
_DROP_REQUEST = frozenset({
    # hop-by-hop (RFC 9110 7.6.1) and framing, which are recomputed
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade", "host", "expect", "content-length", "accept-encoding",
    # credentials and identity: Schemra gets only what Semechka signs
    "cookie", "authorization", "x-real-ip", "forwarded", "via", "x-csrf-token", "true-client-ip", "x-client-ip",
    "x-http-method-override", "x-method-override", "x-http-method", "x-request-id", "x-correlation-id",
    # the page's origin is Semechka's business, not Schemra's
    "origin", "referer",
})
_DROP_PREFIXES = ("x-forwarded-", "x-schemra-", "x-semechka-", "x-original-", "x-rewrite-", "x-real-", "x-envoy-",
                  "x-amzn-", "cf-", "sec-", "forwarded-", "proxy-", "x-arr-", "x-azure-")


def request_headers(inbound, *, upstream_netloc: str) -> list[tuple[str, str]]:
    """What is left of the browser's headers after everything that carries identity or routing is removed.
    `inbound` is an iterable of (name, value). Names named by `Connection:` are hop-by-hop too."""
    pairs = [(k.lower(), v) for k, v in inbound]
    named = {t.strip() for k, v in pairs if k == "connection" for t in v.lower().split(",") if t.strip()}
    out = []
    for k, v in pairs:
        if k in _DROP_REQUEST or k in named or k.startswith(_DROP_PREFIXES):
            continue
        if "\r" in v or "\n" in v or "\x00" in v:
            continue
        out.append((k, v))
    out.append(("host", upstream_netloc))
    out.append(("accept-encoding", "identity"))  # live streams must not be compressed; the lab is on this machine anyway
    return out


# ---- response headers ----------------------------------------------------------------------------------------
_ALLOW_RESPONSE = frozenset({
    "content-type", "content-length", "content-disposition", "content-language", "content-encoding", "content-range",
    "accept-ranges", "cache-control", "etag", "last-modified", "expires", "vary", "location", "retry-after", "allow",
    "x-frame-options", "content-security-policy", "x-content-type-options", "referrer-policy",
})
_ALLOW_RESPONSE_PREFIXES = ("x-schemra-",)
DEFAULT_CSP = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; font-src 'self' data:; "
               "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
               "base-uri 'self'; form-action 'self'; object-src 'none'")
FLOOR_CSP = "object-src 'none'; base-uri 'self'; form-action 'self'"  # added next to whatever Schemra sends: browsers enforce both


def is_event_stream(content_type: str) -> bool:
    return content_type.split(";", 1)[0].strip().lower() == "text/event-stream"


def rewrite_location(value: str, upstream_netloc: str, conn_netloc: str | None = None) -> str | None:
    """Keep a redirect below /schemra/. Returns the new Location, or None if it points anywhere else."""
    v = value.strip()
    if not v or any(ord(c) < 0x20 or ord(c) == 0x7f or c == "\\" for c in v):
        return None
    try:
        u: SplitResult = urlsplit(v)
    except ValueError:
        return None
    own = {upstream_netloc.lower(), (conn_netloc or upstream_netloc).lower()}
    if u.scheme or u.netloc:
        if u.scheme not in ("http", "") or u.netloc.lower() not in own or u.username or u.password:
            return None
        path = u.path or "/"
        tail = ("?" + u.query if u.query else "") + ("#" + u.fragment if u.fragment else "")
        return PREFIX + path + tail
    if u.path.startswith("/"):
        if u.path == PREFIX or u.path.startswith(PREFIX + "/"):  # Schemra already honoured X-Forwarded-Prefix
            return v
        return PREFIX + v
    return v  # relative to the current address, which is already below /schemra/


def response_headers(upstream: list[tuple[str, str]], *, upstream_netloc: str, conn_netloc: str | None = None,
                     stream: bool) -> list[tuple[str, str]] | None:
    """Headers for the browser. None means the redirect cannot be kept below /schemra/ (answer 502)."""
    out: list[tuple[str, str]] = []
    csp_seen = False
    for k, v in upstream:
        k = k.lower()
        if k not in _ALLOW_RESPONSE and not k.startswith(_ALLOW_RESPONSE_PREFIXES):
            continue
        if "\r" in v or "\n" in v:
            continue
        if k == "location":
            v = rewrite_location(v, upstream_netloc, conn_netloc)
            if v is None:
                return None
        elif k == "x-frame-options":
            if v.strip().upper() not in ("DENY", "SAMEORIGIN"):
                continue
        elif k == "content-security-policy":
            csp_seen = True
        elif k == "cache-control":
            # Pages are per person: never let a shared cache keep one for somebody else.
            v = re.sub(r"\bpublic\b", "private", v, flags=re.IGNORECASE)
        elif k == "content-length" and stream:
            continue
        out.append((k, v))
    names = {k for k, _ in out}
    if not csp_seen:
        out.append(("content-security-policy", DEFAULT_CSP))
    out.append(("content-security-policy", FLOOR_CSP))
    if "cache-control" not in names:
        out.append(("cache-control", "private, no-cache"))
    if "x-content-type-options" not in names:
        out.append(("x-content-type-options", "nosniff"))
    if stream:
        out = [(k, v) for k, v in out if k != "cache-control"] + [("cache-control", "no-cache, no-transform"),
                                                                    ("x-accel-buffering", "no")]
    return out


# ---- CSRF ------------------------------------------------------------------------------------------------------
def csrf_check(method: str, headers, session_csrf: str, mode: str) -> str | None:
    """None when the request may change state, else the error code ('bad_origin' or 'csrf').

    Semechka's own mechanism is the per-session token in `X-CSRF-Token` plus a same-origin check on `Origin`. Schemra's
    pages are another application served from the same origin and do not know the token, so with mode
    `token_or_origin` a browser request that proves it comes from a page on this very address (Origin equal to Host and
    Sec-Fetch-Site, when sent, `same-origin`) is accepted instead. Cross-site pages cannot produce either proof."""
    if method in SAFE_METHODS:
        return None
    origin = headers.get("origin")
    site = (headers.get("sec-fetch-site") or "").lower()
    if origin and urlsplit(origin).netloc != headers.get("host", ""):
        return "bad_origin"
    if site and site != "same-origin":
        return "bad_origin"
    sent = headers.get("x-csrf-token", "")
    if sent and secrets.compare_digest(sent.encode(), session_csrf.encode()):
        return None
    if mode == "token_or_origin" and origin:
        return None
    return "csrf"
