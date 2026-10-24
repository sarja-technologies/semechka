"""One conversation engine for text, attached files, image analysis, image generation and video generation.

- A conversation is a tree of messages; the visible thread is the path from the root to `active_leaf`.
  Threads are served in slices (newest first) so long conversations open without decrypting every message.
- Attachments are referenced from a message's `meta.attachments` (ids only). Images reach a model as OpenAI-style
  `image_url` parts only when that model reports vision; otherwise the model is told an image was attached.
- Image and video results are assistant messages of kind image/video whose `meta.job_id` names the media job.
- Context fitting: when a request does not fit the context window the engine loaded the model with, the oldest turns
  are left out (never silently: the reply records how many) and the request is retried once. The window size is
  learned from the engine's own error message, or set by an administrator on the model.
"""
from __future__ import annotations

import base64
import re

from sqlalchemy import select

from . import attachments_svc, media_svc
from .media_models import MediaAsset, MediaJob
from .models import ChatAttachment, Conversation, Message, Model

VISION_IMAGES_MAX = 4  # most recent images resent to a vision model; earlier ones are described in words
IMAGE_TOKENS = 1100  # rough cost of one image; the true figure depends on the model's vision encoder
DEFAULT_RATIO = 0.3  # tokens per character before the engine has told us anything
CTX_ERR = re.compile(r"\((\d+) tokens\) exceeds the available context size \((\d+) tokens\)")
SOURCE_PREFIXES = ("at_", "ae_", "ma_")


# ---- the tree --------------------------------------------------------------------------------------
def load_rows(s, conv_id: str) -> dict[str, Message]:
    return {m.id: m for m in s.execute(select(Message).where(Message.conversation_id == conv_id)).scalars()}


def children(rows: dict[str, Message]) -> dict[str | None, list[Message]]:
    kids: dict[str | None, list[Message]] = {}
    for m in rows.values():
        kids.setdefault(m.parent_id, []).append(m)
    for v in kids.values():
        v.sort(key=lambda m: (m.created_at, m.id))
    return kids


def descend(kids: dict, mid: str) -> str:
    """The newest leaf under a message: where you land when you switch to that branch."""
    cur = mid
    while kids.get(cur):
        cur = kids[cur][-1].id
    return cur


def leaf_of(c: Conversation, rows: dict, kids: dict) -> str | None:
    if c.active_leaf in rows:
        return c.active_leaf
    roots = kids.get(None) or []
    return descend(kids, roots[-1].id) if roots else None


def path_to(rows: dict, leaf: str | None) -> list[Message]:
    out = []
    cur = rows.get(leaf) if leaf else None
    while cur is not None:
        out.append(cur)
        cur = rows.get(cur.parent_id) if cur.parent_id else None
    return out[::-1]


def siblings(m: Message, kids: dict) -> dict | None:
    sib = kids.get(m.parent_id) or [m]
    if len(sib) < 2:
        return None
    i = next(i for i, x in enumerate(sib) if x.id == m.id)
    return {"index": i, "count": len(sib), "prev": sib[i - 1].id if i else None,
            "next": sib[i + 1].id if i + 1 < len(sib) else None}


# ---- attachments and sources -------------------------------------------------------------------------
def describe_source(st, s, user_id: str, sid: str) -> dict | None:
    """An attachment (at_/ae_) or one of the person's own generated images (ma_), owner-checked."""
    if sid.startswith("ma_"):
        a = s.get(MediaAsset, sid)
        if a is None or a.user_id != user_id or a.kind != "image":
            return None
        return {"id": a.id, "name": "generated image", "kind": "image", "mime": a.mime, "size": a.size,
                "width": a.width, "height": a.height, "saved": True, "generated": True}
    return attachments_svc.describe(st, s, user_id, sid)


def load_source(st, s, user_id: str, sid: str) -> tuple[dict, bytes] | None:
    if sid.startswith("ma_"):
        d = describe_source(st, s, user_id, sid)
        if d is None:
            return None
        try:
            return d, media_svc.read_asset(st, s.get(MediaAsset, sid))
        except (OSError, ValueError):
            return None
    return attachments_svc.load(st, s, user_id, sid)


def claim_attachments(st, s, user_id: str, ids: list[str], conv: Conversation | None) -> list[dict]:
    """Validate the ids a message refers to and bind saved uploads to the conversation (they then share its fate)."""
    out = []
    for sid in dict.fromkeys(ids):
        if not sid.startswith(SOURCE_PREFIXES):
            raise attachments_svc.AttachmentError("not_found", "Attachment not found.", 404)
        if conv is not None and sid.startswith("ae_"):
            raise attachments_svc.AttachmentError("invalid", "That file was uploaded for a no-save conversation.", 422)
        if conv is None and sid.startswith("at_"):
            raise attachments_svc.AttachmentError("invalid", "That file belongs to a saved conversation.", 422)
        d = describe_source(st, s, user_id, sid)
        if d is None:
            raise attachments_svc.AttachmentError("not_found", "Attachment not found.", 404)
        if sid.startswith("at_"):
            row = s.get(ChatAttachment, sid)
            if row.conversation_id not in (None, conv.id):
                raise attachments_svc.AttachmentError("not_found", "Attachment not found.", 404)
            row.conversation_id, row.expires_at = conv.id, conv.expires_at
        out.append(d)
    return out


# ---- what the model reads ----------------------------------------------------------------------------
def _file_block(name: str, data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    cut = len(text) > attachments_svc.TEXT_MAX_CHARS
    text = text[:attachments_svc.TEXT_MAX_CHARS]
    safe = name.replace('"', "'")
    return f'<file name="{safe}">\n{text}\n</file>' + ("\n(The file was cut short for length.)" if cut else "")


def media_note(st, m: Message) -> str:
    prompt = st.vault.decrypt(m.content_enc, "content")
    what = "an image" if m.kind == "image" else "a video"
    if m.status == "complete":
        return f"[Generated {what} for the prompt: {prompt}]"
    if m.status in ("pending",):
        return f"[{what.capitalize()} is being generated for the prompt: {prompt}]"
    return f"[Generating {what} for the prompt \"{prompt}\" did not succeed]"


def build_messages(st, s, user_id: str, path: list[Message], vision: bool,
                   extra_last: list[str] | None = None) -> list[dict]:
    """Model input for a thread. `extra_last` are attachment ids for a message not stored (no-save chats)."""
    out: list[dict] = []
    image_slots: list[tuple[int, str, dict, bytes]] = []
    for idx, m in enumerate(path):
        if m.role == "assistant" and m.kind in ("image", "video"):
            out.append({"role": "assistant", "content": media_note(st, m)})
            continue
        text = st.vault.decrypt(m.content_enc, "content")
        ids = list((m.meta or {}).get("attachments") or [])
        files = []
        for sid in ids:
            got = load_source(st, s, user_id, sid)
            if got is None:
                files.append("[An attachment that is no longer available]")
                continue
            d, data = got
            if d["kind"] == "text":
                files.append(_file_block(d["name"], data))
            else:
                image_slots.append((idx, sid, d, data))
        if files:
            text = "\n\n".join([*files, text]) if text else "\n\n".join(files)
        out.append({"role": m.role, "content": text})
    return _place_images(out, image_slots, vision)


def _place_images(out: list[dict], image_slots: list, vision: bool) -> list[dict]:
    resend = {sid for _, sid, _, _ in image_slots[-VISION_IMAGES_MAX:]} if vision else set()
    by_msg: dict[int, list] = {}
    for idx, sid, d, data in image_slots:
        by_msg.setdefault(idx, []).append((sid, d, data))
    for idx, imgs in by_msg.items():
        msg = out[idx]
        parts: list[dict] = []
        notes = []
        for sid, d, data in imgs:
            if sid in resend:
                url = f"data:{d['mime']};base64,{base64.b64encode(data).decode()}"
                parts.append({"type": "image_url", "image_url": {"url": url}})
            elif vision:
                notes.append(f"[An earlier image ({d['name']}) is not resent to save space]")
            else:
                notes.append(f"[The person attached an image ({d['name']}); the current model cannot see images]")
        text = "\n".join([*notes, msg["content"]]) if notes else msg["content"]
        msg["content"] = [{"type": "text", "text": text or "(image)"}, *parts] if parts else text
    return out


def ephemeral_messages(st, s, user_id: str, raw: list[dict], vision: bool) -> list[dict]:
    """A no-save transcript held by the browser. Each item: role, content, optional attachments (ae_/ma_ ids)."""
    out: list[dict] = []
    image_slots = []
    for idx, x in enumerate(raw):
        text = x["content"]
        files = []
        for sid in x.get("attachments") or []:
            got = load_source(st, s, user_id, sid) if str(sid).startswith(("ae_", "ma_")) else None
            if got is None:
                files.append("[An attachment that is no longer available]")
                continue
            d, data = got
            if d["kind"] == "text":
                files.append(_file_block(d["name"], data))
            else:
                image_slots.append((idx, sid, d, data))
        if files:
            text = "\n\n".join([*files, text]) if text else "\n\n".join(files)
        out.append({"role": x["role"], "content": text})
    return _place_images(out, image_slots, vision)


# ---- context fitting -----------------------------------------------------------------------------------
def _chars(msg: dict) -> tuple[int, int]:
    c = msg["content"]
    if isinstance(c, str):
        return len(c), 0
    return sum(len(p.get("text") or "") for p in c), sum(1 for p in c if p.get("type") == "image_url")


def estimate(messages: list[dict], ratio: float) -> int:
    total = 0
    for m in messages:
        ch, imgs = _chars(m)
        total += int(ch * ratio) + imgs * IMAGE_TOKENS + 8
    return total


def learned(st) -> dict:
    return st.__dict__.setdefault("ctx_learned", {})


def learn(st, model_id: str, err: str, messages: list[dict]) -> int | None:
    """Record the engine's context size and our token/character ratio from its refusal. Returns the size."""
    m = CTX_ERR.search(err or "")
    if not m:
        return None
    req, n_ctx = int(m.group(1)), int(m.group(2))
    ch = sum(_chars(x)[0] for x in messages) or 1
    imgs = sum(_chars(x)[1] for x in messages)
    ratio = max(0.05, min(1.5, (req - imgs * IMAGE_TOKENS) / ch))
    learned(st)[model_id] = {"n_ctx": n_ctx, "ratio": ratio}
    return n_ctx


def window_for(st, m: Model) -> tuple[int | None, float]:
    """(context tokens the model runs with, tokens per character) as far as Semechka knows."""
    admin = (m.defaults or {}).get("ctx")
    got = learned(st).get(m.id) or {}
    return (int(admin) if admin else got.get("n_ctx")), got.get("ratio", DEFAULT_RATIO)


def fit(messages: list[dict], n_ctx: int | None, max_tokens: int, ratio: float) -> tuple[list[dict], int]:
    """Leave out the oldest turns until the prompt fits. Keeps the system message and the newest message.
    -> (messages, number of messages left out)."""
    if not n_ctx:
        return messages, 0
    budget = n_ctx - min(max_tokens, n_ctx // 4) - 64
    head = messages[:1] if messages and messages[0]["role"] == "system" else []
    rest = messages[len(head):]
    dropped = 0
    while len(rest) > 1 and estimate(head + rest, ratio) > budget:
        rest = rest[1:]
        dropped += 1
    while len(rest) > 1 and rest[0]["role"] != "user":  # chat templates expect the first turn to be the person's
        rest = rest[1:]
        dropped += 1
    return head + rest, dropped


# ---- serialisation -------------------------------------------------------------------------------------
def jobs_for(s, msgs: list[Message]) -> dict[str, MediaJob]:
    ids = [(m.meta or {}).get("job_id") for m in msgs if m.kind in ("image", "video")]
    ids = [i for i in ids if i]
    if not ids:
        return {}
    return {j.id: j for j in s.execute(select(MediaJob).where(MediaJob.id.in_(ids))).scalars()}


def attachments_for(st, s, user_id: str, msgs: list[Message]) -> dict[str, dict]:
    ids = {sid for m in msgs for sid in ((m.meta or {}).get("attachments") or [])}
    out = {}
    if not ids:
        return out
    for row in s.execute(select(ChatAttachment).where(ChatAttachment.id.in_([i for i in ids if i.startswith("at_")]),
                                                      ChatAttachment.user_id == user_id)).scalars():
        out[row.id] = attachments_svc.out(st, row)
    for sid in ids - set(out):
        d = describe_source(st, s, user_id, sid)
        if d is not None:
            out[sid] = d
    return out


def public_meta(meta: dict | None) -> dict:
    m = meta or {}
    return {k: m[k] for k in ("trimmed", "operation", "source", "error") if m.get(k) is not None}
