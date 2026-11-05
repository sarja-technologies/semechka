"""Encrypted storage for large media files, readable by byte range without loading the file into memory.

The older format (``SMA1``, :meth:`Vault.encrypt_bytes`) seals a whole file in one AES-GCM message, so reading any part needs all of it in memory.
That is fine for a generated picture; it is not for a 500 MB video. ``SMC1`` seals the file in 1 MiB chunks:

    SMC1 | 8-byte random nonce prefix | chunk 0 | chunk 1 | ...        (each chunk = ciphertext + 16-byte tag)

Chunk ``i`` uses nonce ``prefix | i`` and associated data ``purpose:bind:i:final``, so chunks cannot be reordered, moved to another file or
cut off (the last chunk is marked final; a file missing its final chunk fails to read). Every chunk but the last holds exactly ``CHUNK``
plaintext bytes, so a range maps straight to chunk numbers and a seek reads only what it needs.
"""
from __future__ import annotations

import os
import struct
from collections.abc import Iterator
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"SMC1"
LEGACY = b"SMA1"
CHUNK = 1 << 20
TAG = 16
HEAD = len(MAGIC) + 8


def kind_of(path: Path) -> str:
    """'chunked', 'legacy' or raises ValueError."""
    with open(path, "rb") as f:
        m = f.read(4)
    if m == MAGIC:
        return "chunked"
    if m == LEGACY:
        return "legacy"
    raise ValueError("unrecognised ciphertext format")


def _aad(purpose: str, bind: str, i: int, final: bool) -> bytes:
    return f"{purpose}:{bind}:{i}:{int(final)}".encode()


def _nonce(prefix: bytes, i: int) -> bytes:
    return prefix + struct.pack(">I", i)


def sealed_size(plain: int) -> int:
    n = max(1, -(-plain // CHUNK))
    return HEAD + plain + n * TAG


class Writer:
    """Write plaintext in any block size; the file appears at ``path`` only when :meth:`close` succeeds."""

    def __init__(self, key: bytes, purpose: str, bind: str, path: Path):
        self.aes, self.purpose, self.bind, self.path = AESGCM(key), purpose, bind, path
        self.tmp = path.with_name(path.name + ".tmp")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        self.f = os.fdopen(fd, "wb")
        self.prefix = os.urandom(8)
        self.f.write(MAGIC + self.prefix)
        self.buf = bytearray()
        self.i = 0
        self.size = 0
        self.done = False

    def write(self, data: bytes) -> None:
        self.size += len(data)
        self.buf += data
        while len(self.buf) > CHUNK:  # keep at least one byte back: the last chunk must be marked final
            self._emit(bytes(self.buf[:CHUNK]), False)
            del self.buf[:CHUNK]

    def _emit(self, block: bytes, final: bool) -> None:
        self.f.write(self.aes.encrypt(_nonce(self.prefix, self.i), block, _aad(self.purpose, self.bind, self.i, final)))
        self.i += 1

    def close(self) -> int:
        self._emit(bytes(self.buf), True)
        self.buf.clear()
        self.f.flush()
        os.fsync(self.f.fileno())
        self.f.close()
        os.replace(self.tmp, self.path)
        self.done = True
        return self.size

    def abort(self) -> None:
        try:
            self.f.close()
        except OSError:
            pass
        self.tmp.unlink(missing_ok=True)


def seal_file(key: bytes, purpose: str, bind: str, src: Path, dest: Path) -> int:
    """Encrypt an existing plaintext file into ``dest`` in constant memory. Returns the plaintext size."""
    w = Writer(key, purpose, bind, dest)
    try:
        with open(src, "rb") as f:
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                w.write(b)
        return w.close()
    except BaseException:
        w.abort()
        raise


def seal_bytes(key: bytes, purpose: str, bind: str, data: bytes, dest: Path) -> int:
    w = Writer(key, purpose, bind, dest)
    try:
        w.write(data)
        return w.close()
    except BaseException:
        w.abort()
        raise


def iter_range(key: bytes, purpose: str, bind: str, path: Path, size: int, start: int, end: int) -> Iterator[bytes]:
    """Yield plaintext bytes ``start..end`` (inclusive) of a chunked file whose plaintext is ``size`` bytes. Raises ValueError if the file
    was tampered with or truncated."""
    if size == 0 or start > end:
        return
    aes = AESGCM(key)
    total = max(1, -(-size // CHUNK))
    with open(path, "rb") as f:
        if f.read(4) != MAGIC:
            raise ValueError("unrecognised ciphertext format")
        prefix = f.read(8)
        first, last = start // CHUNK, end // CHUNK
        f.seek(HEAD + first * (CHUNK + TAG))
        for i in range(first, last + 1):
            final = i == total - 1
            want = (size - i * CHUNK) if final else CHUNK
            blob = f.read(want + TAG)
            if len(blob) != want + TAG:
                raise ValueError("file is shorter than recorded")
            try:
                plain = aes.decrypt(_nonce(prefix, i), blob, _aad(purpose, bind, i, final))
            except InvalidTag as e:
                raise ValueError("file failed authentication") from e
            lo = start - i * CHUNK if i == first else 0
            hi = end - i * CHUNK + 1 if i == last else len(plain)
            yield plain[lo:hi]


def read_all(key: bytes, purpose: str, bind: str, path: Path, size: int) -> bytes:
    return b"".join(iter_range(key, purpose, bind, path, size, 0, size - 1)) if size else b""


def open_to_file(key: bytes, purpose: str, bind: str, path: Path, size: int, dest: Path) -> None:
    """Decrypt into a plaintext file (for ffmpeg, or for handing a source on to the engine). The caller deletes it."""
    fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as out:
        for block in iter_range(key, purpose, bind, path, size, 0, size - 1) if size else ():
            out.write(block)
