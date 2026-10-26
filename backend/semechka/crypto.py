"""Envelope encryption for content at rest.

A random 32-byte master key lives in <data_dir>/master.key (mode 0600). Purpose keys are derived with
HKDF so that content, session and backup material never share a key. Ciphertext is AES-256-GCM with a
random 96-bit nonce; the purpose label is bound as associated data.

Honest boundary: the key sits on the same host as the database. This protects database copies, backups
and disk images taken without the key file; it does not protect against a compromised running host.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

_PREFIX = "v1:"


def load_or_create_master_key(path: Path) -> bytes:
    if path.exists():
        data = path.read_bytes()
        if len(data) != 32:
            raise RuntimeError(f"{path} is not a valid master key (expected 32 bytes)")
        return data
    key = secrets.token_bytes(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


class Vault:
    def __init__(self, master: bytes):
        self._master = master
        self._cache: dict[str, bytes] = {}

    def _key(self, purpose: str) -> bytes:
        if purpose not in self._cache:
            self._cache[purpose] = HKDF(
                algorithm=hashes.SHA256(), length=32, salt=None, info=f"semechka/{purpose}".encode()
            ).derive(self._master)
        return self._cache[purpose]

    def encrypt(self, plaintext: str, purpose: str = "content") -> str:
        nonce = os.urandom(12)
        ct = AESGCM(self._key(purpose)).encrypt(nonce, plaintext.encode(), purpose.encode())
        return _PREFIX + base64.urlsafe_b64encode(nonce + ct).decode()

    def decrypt(self, token: str, purpose: str = "content") -> str:
        if not token.startswith(_PREFIX):
            raise ValueError("unrecognised ciphertext format")
        raw = base64.urlsafe_b64decode(token[len(_PREFIX):])
        return AESGCM(self._key(purpose)).decrypt(raw[:12], raw[12:], purpose.encode()).decode()

    # --- media block: binary payloads (generated images and video), bound to their asset id as associated data
    def encrypt_bytes(self, data: bytes, purpose: str, bind: str) -> bytes:
        nonce = os.urandom(12)
        return b"SMA1" + nonce + AESGCM(self._key(purpose)).encrypt(nonce, data, f"{purpose}:{bind}".encode())

    def decrypt_bytes(self, blob: bytes, purpose: str, bind: str) -> bytes:
        if blob[:4] != b"SMA1":
            raise ValueError("unrecognised ciphertext format")
        return AESGCM(self._key(purpose)).decrypt(blob[4:16], blob[16:], f"{purpose}:{bind}".encode())

    def mac(self, value: str, purpose: str) -> str:
        return hmac.new(self._key(purpose), value.encode(), hashlib.sha256).hexdigest()


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def seal_with_passphrase(data: bytes, passphrase: str) -> bytes:
    """Backup encryption: scrypt-derived key, AES-256-GCM. Format: b'SMCK1' | salt16 | nonce12 | ct."""
    salt, nonce = os.urandom(16), os.urandom(12)
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    return b"SMCK1" + salt + nonce + AESGCM(key).encrypt(nonce, data, b"semechka-backup")


def open_with_passphrase(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(b"SMCK1"):
        raise ValueError("not a Semechka backup")
    salt, nonce, ct = blob[5:21], blob[21:33], blob[33:]
    key = Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())
    return AESGCM(key).decrypt(nonce, ct, b"semechka-backup")


# ---- streaming backups (SMCK2) --------------------------------------------------------------------------------------
# Backups can be many gigabytes (workspace files), so they are sealed as a stream of independently authenticated chunks
# instead of one blob in memory. Each chunk's nonce is a random per-backup prefix plus its index; its associated data is
# the index and a "final" flag, so reordering, dropping, truncating or extending the stream fails authentication.
SMCK2 = b"SMCK2"
CHUNK = 1 << 20
_TAG = 16


def _stream_key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def _chunk_ad(i: int, final: bool) -> bytes:
    return b"semechka-backup2" + i.to_bytes(8, "big") + (b"\x01" if final else b"\x00")


class SealWriter:
    """File-like writer: everything written is encrypted into `raw` in CHUNK-sized authenticated pieces."""

    def __init__(self, raw, passphrase: str, chunk: int = CHUNK):
        self.raw, self.chunk, self.i, self.buf, self.closed = raw, chunk, 0, bytearray(), False
        salt, self.prefix = os.urandom(16), os.urandom(8)
        self.aes = AESGCM(_stream_key(passphrase, salt))
        raw.write(SMCK2 + salt + self.prefix + chunk.to_bytes(4, "big"))

    def _seal(self, data: bytes, final: bool) -> None:
        self.raw.write(self.aes.encrypt(self.prefix + self.i.to_bytes(4, "big"), data, _chunk_ad(self.i, final)))
        self.i += 1

    def write(self, b) -> int:
        self.buf += b
        while len(self.buf) > self.chunk:  # keep at least one byte back: the last chunk must be marked final
            self._seal(bytes(self.buf[:self.chunk]), False)
            del self.buf[:self.chunk]
        return len(b)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        if not self.closed:
            self._seal(bytes(self.buf), True)
            self.closed = True


class SealReader:
    """File-like reader for SMCK2. Raises ValueError for a wrong passphrase, damage, truncation or extension."""

    def __init__(self, raw, passphrase: str):
        head = raw.read(len(SMCK2) + 16 + 8 + 4)
        if len(head) < len(SMCK2) + 28 or not head.startswith(SMCK2):
            raise ValueError("not a Semechka backup")
        self.raw = raw
        self.chunk = int.from_bytes(head[-4:], "big")
        if not 1024 <= self.chunk <= 64 * CHUNK:
            raise ValueError("unreadable backup header")
        self.prefix = head[len(SMCK2) + 16:len(SMCK2) + 24]
        self.aes = AESGCM(_stream_key(passphrase, head[len(SMCK2):len(SMCK2) + 16]))
        self.i, self.out, self.done = 0, b"", False
        self.cur = self._read_cipher()
        self.nxt = self._read_cipher()

    def _read_cipher(self) -> bytes:
        want, parts = self.chunk + _TAG, []
        while want:
            b = self.raw.read(want)
            if not b:
                break
            parts.append(b)
            want -= len(b)
        return b"".join(parts)

    def _fill(self) -> bool:
        if self.done:
            return False
        final = not self.nxt
        if len(self.cur) < _TAG or (not final and len(self.cur) != self.chunk + _TAG):
            raise ValueError("backup is truncated or damaged")
        try:
            self.out = self.aes.decrypt(self.prefix + self.i.to_bytes(4, "big"), self.cur, _chunk_ad(self.i, final))
        except Exception:  # noqa: BLE001  InvalidTag: wrong passphrase or tampering; the reason is deliberately not distinguished
            raise ValueError("wrong passphrase, or the backup is damaged") from None
        self.i += 1
        self.done = final
        self.cur, self.nxt = self.nxt, (b"" if final else self._read_cipher())
        return True

    def read(self, n: int = -1) -> bytes:
        parts, need = [], n
        while n < 0 or need > 0:
            if not self.out and not self._fill():
                break
            take = self.out if n < 0 else self.out[:need]
            self.out = self.out[len(take):]
            parts.append(take)
            need -= len(take)
        return b"".join(parts)

    def verify_end(self) -> None:
        """Read to the end so the final chunk is authenticated (a tar reader may stop before it)."""
        while self.read(1 << 20):
            pass
