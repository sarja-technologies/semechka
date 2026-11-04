"""The chunked encrypted file format used for uploaded videos (semechka/media_store.py)."""
import os

import pytest
from semechka import media_store as ms

KEY = bytes(range(32))
C = ms.CHUNK


def seal(tmp_path, data: bytes, bind="ma_x", name="f.bin"):
    p = tmp_path / name
    assert ms.seal_bytes(KEY, "asset", bind, data, p) == len(data)
    return p


@pytest.mark.parametrize("n", [0, 1, 100, C - 1, C, C + 1, 3 * C, 3 * C + 17])
def test_round_trip_at_every_chunk_boundary(tmp_path, n):
    data = os.urandom(n)
    p = seal(tmp_path, data)
    assert ms.read_all(KEY, "asset", "ma_x", p, n) == data
    assert p.stat().st_size == ms.sealed_size(n)
    assert ms.kind_of(p) == "chunked"
    assert n < 16 or data[:16] not in p.read_bytes()       # not stored in the clear


def test_ranges_inside_across_and_at_the_end(tmp_path):
    data = os.urandom(3 * C + 500)
    p = seal(tmp_path, data)
    for a, b in [(0, 0), (5, 99), (C - 3, C + 3), (C, 2 * C - 1), (2 * C + 10, len(data) - 1), (len(data) - 1, len(data) - 1), (0, len(data) - 1)]:
        assert b"".join(ms.iter_range(KEY, "asset", "ma_x", p, len(data), a, b)) == data[a:b + 1], (a, b)


def test_a_flipped_bit_a_wrong_binding_or_wrong_key_is_refused(tmp_path):
    data = os.urandom(C + 10)
    p = seal(tmp_path, data)
    with pytest.raises(ValueError):
        ms.read_all(KEY, "asset", "ma_other", p, len(data))                  # moved to another asset id
    with pytest.raises(ValueError):
        ms.read_all(bytes(32), "asset", "ma_x", p, len(data))
    raw = bytearray(p.read_bytes())
    raw[ms.HEAD + 5] ^= 1
    p.write_bytes(raw)
    with pytest.raises(ValueError):
        ms.read_all(KEY, "asset", "ma_x", p, len(data))


def test_truncation_and_reordering_are_detected(tmp_path):
    data = os.urandom(2 * C + 50)
    p = seal(tmp_path, data)
    raw = p.read_bytes()
    cut = tmp_path / "cut.bin"
    cut.write_bytes(raw[:ms.HEAD + 2 * (C + ms.TAG)])                           # the final chunk is gone
    with pytest.raises(ValueError):
        ms.read_all(KEY, "asset", "ma_x", cut, len(data))
    swapped = tmp_path / "swap.bin"
    a, b = ms.HEAD, ms.HEAD + C + ms.TAG
    swapped.write_bytes(raw[:a] + raw[b:b + C + ms.TAG] + raw[a:b] + raw[b + C + ms.TAG:])
    with pytest.raises(ValueError):
        ms.read_all(KEY, "asset", "ma_x", swapped, len(data))


def test_a_failed_write_leaves_no_file(tmp_path):
    w = ms.Writer(KEY, "asset", "ma_x", tmp_path / "w.bin")
    w.write(b"x" * (C + 5))
    w.abort()
    assert list(tmp_path.iterdir()) == []


def test_the_old_whole_file_format_is_still_recognised(tmp_path):
    from semechka.crypto import Vault
    v = Vault(os.urandom(32))
    p = tmp_path / "old.bin"
    p.write_bytes(v.encrypt_bytes(b"png bytes", "asset", "ma_old"))
    assert ms.kind_of(p) == "legacy"
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"nope")
    with pytest.raises(ValueError):
        ms.kind_of(junk)
