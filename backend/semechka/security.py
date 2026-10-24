from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_ph = PasswordHasher()  # argon2id, library defaults (RFC 9106 low-memory profile or stronger)
_DUMMY = _ph.hash("semechka-dummy-password-for-timing")

_COMMON = {"password", "123456789012", "password1234", "qwertyuiopas", "letmein12345", "administrator",
           "changeme1234", "welcome12345", "iloveyou1234", "semechka12345"}


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(hashed: str | None, pw: str) -> bool:
    """Constant-effort verify: a missing user still costs one argon2 verification."""
    try:
        return _ph.verify(hashed or _DUMMY, pw) and hashed is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _ph.check_needs_rehash(hashed)


def password_problem(pw: str, email: str, min_len: int) -> str | None:
    if len(pw) < min_len:
        return f"Use at least {min_len} characters."
    if len(pw) > 256:
        return "That password is too long."
    if pw.lower() in _COMMON or pw.lower() == email.lower() or len(set(pw)) < 5:
        return "That password is too easy to guess."
    return None


class RateLimiter:
    """In-process sliding window. Adequate for one control-plane process; documented limitation."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, window_s: float) -> bool:
        """Record an attempt; returns False when the caller is over the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_s:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            if len(self._hits) > 50_000:
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] > window_s][:10_000]:
                    self._hits.pop(k, None)
            return True

    def reset(self):
        with self._lock:
            self._hits.clear()


# ---- TOTP (RFC 6238, SHA-1, 6 digits, 30 s) ---------------------------------------------------------
def new_totp_secret() -> str:
    return base64.b32encode(hashlib.sha256(str(time.time_ns()).encode() + __import__("os").urandom(32)).digest()[:20]
                            ).decode().rstrip("=")


def totp_at(secret: str, t: float, digits=6, step=30) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    msg = struct.pack(">Q", int(t // step))
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10**digits
    return str(code).zfill(digits)


def totp_verify(secret: str, code: str, now: float | None = None, window=1) -> bool:
    now = now or time.time()
    code = (code or "").strip().replace(" ", "")
    return any(hmac.compare_digest(totp_at(secret, now + d * 30), code) for d in range(-window, window + 1))


def totp_uri(secret: str, account: str, issuer: str) -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&digits=6&period=30"
