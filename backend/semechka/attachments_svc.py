"""Files people attach to chat messages: images (for models that can see) and text files.

- Type is decided from the bytes (magic numbers), never from the name or the browser's claim. SVG is refused: it is
  markup, and vision models need pixels.
- Image dimensions are read from the file header; nothing is decoded or re-encoded, so no image library is needed.
- Saved conversations: bytes are AES-GCM encrypted under data_dir/attachments/<user_id>/<id>.bin, bound to the id.
- No-save conversations: bytes stay in process memory for EPHEMERAL_TTL and are never written to disk.
"""
from __future__ import annotations

import os
import struct
import time
import uuid
from pathlib import Path

from .models import ChatAttachment

IMAGE_MIME = ("image/png", "image/jpeg", "image/webp", "image/gif")
TEXT_MAX_CHARS = 200_000  # characters of one text file given to the model
EPHEMERAL_TTL = 60 * 60
EPHEMERAL_CAP_BYTES = 256 * 1024 * 1024
TEXT_EXT = {"txt", "md", "markdown", "csv", "tsv", "json", "yaml", "yml", "toml", "ini", "log", "xml", "html", "htm",
            "css", "js", "mjs", "ts", "tsx", "jsx", "py", "rb", "go", "rs", "c", "h", "cpp", "hpp", "java", "kt", "sh",
            "bash", "sql", "svelte", "vue", "php", "swift", "cs", "conf", "cfg", "env", "r", "lua", "pl", "scala", "tex"}


class AttachmentError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.status = code, status


def sniff(data: bytes, name: str) -> tuple[str, str]:
    """-> (kind, mime). kind is 'image' or 'text'."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image", "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "image/gif"
    head = data[:4096]
    if b"\x00" in head:
        raise AttachmentError("unsupported_type", "Only images (PNG, JPEG, WebP, GIF) and text files can be attached.", 415)
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        raise AttachmentError("unsupported_type", "Text files must be UTF-8.", 415) from None
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if head.lstrip()[:5].lower() in (b"<svg ", b"<?xml") and ext == "svg":
        raise AttachmentError("unsupported_type", "SVG drawings cannot be read by models. Attach a PNG or JPEG.", 415)
    if ext and ext not in TEXT_EXT:
        raise AttachmentError("unsupported_type", f"'.{ext}' files are not supported. Attach images or text files.", 415)
    return "text", "text/plain"


def image_size(data: bytes, mime: str) -> tuple[int | None, int | None]:
    try:
        if mime == "image/png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if mime == "image/gif":
            return struct.unpack("<HH", data[6:10])
        if mime == "image/webp":
            chunk = data[12:16]
            if chunk == b"VP8X":
                return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
            if chunk == b"VP8 ":
                w, h = struct.unpack("<HH", data[26:30])
                return w & 0x3FFF, h & 0x3FFF
            if chunk == b"VP8L":
                b = data[21:25]
                return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        if mime == "image/jpeg":
            i = 2
            while i < len(data) - 9:
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except (struct.error, IndexError):
        pass
    return None, None


# ---- storage ---------------------------------------------------------------------------------------
def path_for(cfg, user_id: str, att_id: str) -> Path:
    return cfg.data_dir / "attachments" / user_id / f"{att_id}.bin"


def write(st, user_id: str, att_id: str, data: bytes) -> None:
    p = path_for(st.cfg, user_id, att_id)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = p.with_suffix(".tmp")
    tmp.write_bytes(st.vault.encrypt_bytes(data, "attachment", att_id))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def remove(st, user_id: str, att_id: str) -> None:
    path_for(st.cfg, user_id, att_id).unlink(missing_ok=True)


def _mem(st) -> dict:
    return st.__dict__.setdefault("attachment_mem", {})


def purge_mem(st) -> None:
    now = time.time()
    mem = _mem(st)
    for k in [k for k, v in mem.items() if v["expires"] < now]:
        mem.pop(k, None)


def store(st, s, user_id: str, name: str, data: bytes, *, ephemeral: bool) -> dict:
    kind, mime = sniff(data, name)
    w, h = image_size(data, mime) if kind == "image" else (None, None)
    name = os.path.basename(name or "attachment")[:120] or "attachment"
    if ephemeral:
        purge_mem(st)
        if sum(len(v["data"]) for v in _mem(st).values()) + len(data) > EPHEMERAL_CAP_BYTES:
            raise AttachmentError("busy", "Temporary storage for no-save attachments is full. Try again shortly.", 503)
        aid = "ae_" + uuid.uuid4().hex[:24]
        _mem(st)[aid] = {"user_id": user_id, "name": name, "kind": kind, "mime": mime, "data": data,
                         "width": w, "height": h, "expires": time.time() + EPHEMERAL_TTL}
        return {"id": aid, "name": name, "kind": kind, "mime": mime, "size": len(data), "width": w, "height": h,
                "saved": False}
    row = ChatAttachment(user_id=user_id, name_enc=st.vault.encrypt(name, "attachment-name"), kind=kind, mime=mime,
                         size=len(data), width=w, height=h)
    s.add(row)
    s.flush()
    write(st, user_id, row.id, data)
    return out(st, row)


def out(st, row: ChatAttachment) -> dict:
    return {"id": row.id, "name": st.vault.decrypt(row.name_enc, "attachment-name"), "kind": row.kind, "mime": row.mime,
            "size": row.size, "width": row.width, "height": row.height, "saved": True}


def load(st, s, user_id: str, att_id: str) -> tuple[dict, bytes] | None:
    """Owner-checked read. Returns (description, bytes) or None when it does not exist or is not theirs."""
    if att_id.startswith("ae_"):
        purge_mem(st)
        v = _mem(st).get(att_id)
        if v is None or v["user_id"] != user_id:
            return None
        return ({"id": att_id, "name": v["name"], "kind": v["kind"], "mime": v["mime"], "size": len(v["data"]),
                 "width": v["width"], "height": v["height"], "saved": False}, v["data"])
    row = s.get(ChatAttachment, att_id)
    if row is None or row.user_id != user_id:
        return None
    try:
        data = st.vault.decrypt_bytes(path_for(st.cfg, user_id, row.id).read_bytes(), "attachment", row.id)
    except (OSError, ValueError):
        return None
    return out(st, row), data


def describe(st, s, user_id: str, att_id: str) -> dict | None:
    if att_id.startswith("ae_"):
        r = load(st, s, user_id, att_id)
        return r[0] if r else None
    row = s.get(ChatAttachment, att_id)
    return out(st, row) if row is not None and row.user_id == user_id else None


def delete_for_conversation(st, s, user_id: str, conversation_id: str) -> int:
    from sqlalchemy import select
    n = 0
    for row in s.execute(select(ChatAttachment).where(ChatAttachment.conversation_id == conversation_id,
                                                      ChatAttachment.user_id == user_id)).scalars().all():
        remove(st, user_id, row.id)
        s.delete(row)
        n += 1
    return n


UNSENT_TTL = 24 * 3600


def sweep(st, s) -> int:
    """Uploads never sent within a day, attachments past their conversation's expiry, and files with no row."""
    from sqlalchemy import or_, select
    now = time.time()
    purge_mem(st)
    n = 0
    for row in s.execute(select(ChatAttachment).where(or_(
            (ChatAttachment.conversation_id == None) & (ChatAttachment.created_at < now - UNSENT_TTL),  # noqa: E711
            (ChatAttachment.expires_at != None) & (ChatAttachment.expires_at < now)))).scalars().all():  # noqa: E711
        remove(st, row.user_id, row.id)
        s.delete(row)
        n += 1
    root = st.cfg.data_dir / "attachments"
    if root.exists():
        known = set(s.execute(select(ChatAttachment.id)).scalars())
        for udir in root.iterdir():
            if not udir.is_dir():
                continue
            for f in udir.iterdir():
                if f.stat().st_mtime < now - 3600 and (f.suffix == ".tmp" or f.stem not in known):
                    f.unlink(missing_ok=True)
                    n += 1
    return n
