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
