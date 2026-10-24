"""Model registry: provider/family recognition, curated metadata, ordering, groups and per-operation access.

Recognition only chooses a label and an icon. It never grants a capability or access: capabilities come from what the
backend reported (or what an administrator explicitly set), access comes from grants.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select

from . import perms
from .models import Backend, Model, ModelGrant, ModelGroup, ModelGroupMember

# ---- operations ----------------------------------------------------------------------------------------
# operation -> (mode gate, required capability). Vision is chat with an image attached.
OPERATIONS: dict[str, tuple[str, str]] = {
    "text": ("chat", "chat"), "code": ("code", "chat"), "vision": ("chat", "vision"),
    "image": ("image", "image"), "video": ("video", "video"),
}
GRANT_MODES = ["chat", "code", "vision", "image", "video"]  # what an administrator can restrict a grant to


def grant_mode_for(op: str) -> str:
    return {"text": "chat", "code": "code", "vision": "vision", "image": "image", "video": "video"}[op]


# ---- provider / family recognition ---------------------------------------------------------------------
@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    mark: str  # lettermark shown when no uploaded logo exists (never a third-party logo)
    tone: str  # restrained background tone for the lettermark
    patterns: tuple[str, ...]


# Order matters: the first match wins, so fine-tune families come before their base family.
PROVIDERS: list[Provider] = [
    Provider("dolphin", "Cognitive Computations (Dolphin)", "Dp", "#3E6A7A", (r"dolphin",)),
    Provider("nous", "Nous Research (Hermes)", "Nh", "#5B5F45", (r"hermes", r"\bnous")),
    Provider("qwen", "Qwen (Alibaba)", "Qw", "#3D5A80", (r"qwen", r"\bqwq\b")),
    Provider("deepseek", "DeepSeek", "DS", "#3F5E9E", (r"deepseek",)),
    Provider("moonshot", "Moonshot AI (Kimi)", "Ki", "#2F3437", (r"kimi", r"moonshot")),
    Provider("meta", "Meta (Llama)", "Ll", "#2D5DA8", (r"llama(?![-_.]?cpp)", r"meta-llama", r"\bmeta\b")),
    Provider("gemini", "Google (Gemini)", "Gi", "#3B6FB6", (r"gemini",)),
    Provider("google", "Google (Gemma)", "Gm", "#3D7F6E", (r"gemma", r"\bgoogle\b", r"paligemma")),
    Provider("openai", "OpenAI", "OA", "#2F3437", (r"gpt[-_]?oss", r"\bgpt[-_]?\d", r"^gpt", r"\bo[134]-", r"openai", r"whisper", r"\bdall[-_]?e")),
    Provider("anthropic", "Anthropic (Claude)", "Cl", "#8A5A3C", (r"claude", r"anthropic")),
    Provider("mistral", "Mistral AI", "Mi", "#A0561F", (r"mistral", r"mixtral", r"codestral", r"ministral", r"magistral", r"devstral", r"pixtral")),
    Provider("microsoft", "Microsoft (Phi)", "Ph", "#3A6EA5", (r"\bphi[-_]?\d", r"\bphi\b", r"microsoft")),
    Provider("minimax", "MiniMax", "MM", "#9E3F4E", (r"minimax",)),
    Provider("bfl", "Black Forest Labs (FLUX)", "Fx", "#2F3437", (r"flux",)),
    Provider("stability", "Stability AI (Stable Diffusion)", "SD", "#6B5B3E", (r"stable[-_]?diffusion", r"\bsdxl", r"\bsd[-_]?3", r"\bsd[-_]?turbo", r"stability")),
    Provider("wan", "Wan (Alibaba)", "Wn", "#4A6670", (r"\bwan[-_]?\d", r"\bwan2", r"\bwan[-_]")),
    Provider("ltx", "Lightricks (LTX)", "LT", "#7A4A3E", (r"\bltx",)),
    Provider("hunyuan", "Tencent (Hunyuan)", "Hy", "#2F6A9E", (r"hunyuan",)),
    Provider("zimage", "Z-Image (Alibaba Tongyi)", "Zi", "#4A6A8C", (r"z[-_]?image",)),
    Provider("glm", "Zhipu (GLM)", "GL", "#3A5A9A", (r"\bglm", r"chatglm", r"zhipu")),
    Provider("cohere", "Cohere (Command)", "Co", "#3E6B5A", (r"command[-_]?r", r"cohere", r"\baya\b")),
    Provider("ibm", "IBM (Granite)", "Gr", "#2F5E9E", (r"granite",)),
    Provider("nvidia", "NVIDIA (Nemotron)", "Nv", "#4E7A2A", (r"nemotron", r"nvidia")),
    Provider("allenai", "Ai2 (OLMo)", "Ol", "#9A5A2A", (r"\bolmo", r"allenai")),
]
_COMPILED = [(p, [re.compile(x, re.I) for x in p.patterns]) for p in PROVIDERS]
BY_PROVIDER = {p.id: p for p in PROVIDERS}


def detect_provider(*names: str | None) -> Provider | None:
    """Case-insensitive pattern match over the model's identifiers and names. Name-based only, so never authoritative."""
    hay = " ".join(n for n in names if n).replace("/", " ")
    for p, rxs in _COMPILED:
        if any(rx.search(hay) for rx in rxs):
            return p
    return None


_PARAMS = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*([bm])(?![a-z0-9])", re.I)
_ACTIVE = re.compile(r"(?<![\w])a(\d+(?:\.\d+)?)b(?![a-z0-9])", re.I)
_QUANT = re.compile(r"(?:@|[-_.])((?:ud-)?(?:i?q\d(?:_[a-z0-9]+)*|mxfp4(?:_moe)?|fp8|fp16|bf16|f16|f32|int[48]|awq|gptq|nf4|q8_0))\b", re.I)


def from_name(real_id: str) -> dict:
    """Values readable from a model identifier such as 'qwen3.6-35b-a3b@MXFP4_MOE'. Labelled as coming from the name."""
    rid = real_id.split(":", 1)[-1]
    out: dict = {}
    base, _, after = rid.partition("@")
    m = _PARAMS.search(base.replace("_", "-"))
    if m:
        n, unit = m.group(1), m.group(2).upper()
        act = _ACTIVE.search(base)
        out["parameters"] = f"{n}{unit}" + (f" (A{act.group(1)}B active)" if act else "")
    q = _QUANT.search("@" + after) if after else _QUANT.search(rid)
    if q:
        out["quantization"] = q.group(1).upper()
    return out


# ---- curated profile ----------------------------------------------------------------------------------
PROFILE_FIELDS = ("provider", "family", "parameters", "quantization", "context_length", "description", "modality",
                  "icon_asset", "capabilities")


def profile_of(m: Model, provider_icons: dict | None = None) -> dict:
    """Effective metadata with the source of every value: 'admin', 'backend' or 'name'. Unknown values are absent."""
    meta, prof = m.meta or {}, m.profile or {}
    out: dict = {}
    src: dict = {}

    def put(key, val, source):
        if val not in (None, "", []):
            out[key], src[key] = val, source

    named = from_name(m.real_id)
    det = detect_provider(m.real_id, m.alias, meta.get("family"))
    put("provider", det.id if det else None, "name")
    put("provider", meta.get("provider") if meta.get("provider") in BY_PROVIDER else None, "backend")
    put("family", meta.get("family"), "backend")
    put("parameters", named.get("parameters"), "name")
    put("parameters", meta.get("params_label") or meta.get("params"), "backend")
    put("quantization", named.get("quantization"), "name")
    put("quantization", meta.get("quantization"), "backend")
    put("context_length", meta.get("context"), "backend")
    put("description", meta.get("description"), "backend")
    caps = list(m.capabilities or [])
    put("capabilities", caps, "backend")
    kind = (meta.get("media") or {}).get("kind")
    put("modality", "image" if kind == "image" else "video" if kind == "video" else
        ("text+image" if "vision" in caps else "text"), "backend")
    for k in PROFILE_FIELDS:  # administrator values win
        if k in prof and prof[k] not in (None, ""):
            put(k, prof[k], "admin")
    p = BY_PROVIDER.get(out.get("provider", ""))
    out["provider_label"] = p.label if p else (out.get("provider") or None)
    icon = prof.get("icon_asset") or (provider_icons or {}).get(out.get("provider", ""))
    out["icon"] = {"asset": icon} if icon else ({"mark": p.mark, "tone": p.tone} if p else {"mark": None, "tone": None})
    out["_source"] = src
    return out


def clean_profile(raw: dict) -> dict:
    """Validate an administrator's profile edit. Empty strings clear a field."""
    out: dict = {}
    for k in PROFILE_FIELDS:
        if k not in raw:
            continue
        v = raw[k]
        if v in ("", None):
            continue
        if k == "capabilities":
            if not isinstance(v, list) or any(c not in ("chat", "vision", "tools", "image", "video", "embeddings") for c in v):
                raise ValueError("Capabilities must be chosen from chat, vision, tools, image, video, embeddings.")
            out[k] = sorted(set(v))
        elif k == "context_length":
            if not isinstance(v, int) or not 0 < v <= 10_000_000:
                raise ValueError("Context length is a whole number of tokens.")
            out[k] = v
        elif k == "provider":
            out[k] = str(v)[:40]
        elif k == "icon_asset":
            if not re.fullmatch(r"[0-9a-f]{24}\.(png|webp|jpg|svg)", str(v)):
                raise ValueError("Upload the icon first.")
            out[k] = v
        else:
            out[k] = str(v).strip()[:500 if k == "description" else 80]
    return out


def capabilities(m: Model) -> list[str]:
    """Capabilities used for access decisions: an administrator's explicit list, else what the backend reported."""
    prof = m.profile or {}
    return list(prof["capabilities"]) if prof.get("capabilities") else list(m.capabilities or [])


# ---- access -----------------------------------------------------------------------------------------------
def subject_keys(p: perms.Principal) -> set[tuple[str, str]]:
    return {("user", p.user_id), ("everyone", "*")} | {("role", r) for r in p.role_ids} | {("team", t) for t in p.team_ids}


def grant_applies(g: ModelGrant, mode: str) -> bool:
    return g.modes is None or mode in (g.modes or [])


def decide(grants: list[ModelGrant], p: perms.Principal, mode: str) -> tuple[bool, str]:
    """Deny wins over allow; no matching allow means no access. A grant limited to other modes does not apply."""
    keys = subject_keys(p)
    mine = [g for g in grants if (g.subject_type, g.subject_id) in keys and grant_applies(g, mode)]
    if any(g.effect == "deny" for g in mine):
        return False, "Your group does not have access to this model."
    if not any(g.effect == "allow" for g in mine):
        if any((g.subject_type, g.subject_id) in keys and g.effect == "allow" for g in grants):
            return False, f"You may use this model, but not for {mode}."
        return False, "Your group does not have access to this model."
    return True, ""


def grants_by_model(s) -> dict[str, list[ModelGrant]]:
    out: dict[str, list[ModelGrant]] = {}
    for g in s.execute(select(ModelGrant)).scalars():
        out.setdefault(g.model_id, []).append(g)
    return out


def ordered_models(s) -> list[Model]:
    return list(s.execute(select(Model).order_by(Model.sort, Model.alias)).scalars())


def groups(s) -> list[tuple[ModelGroup, list[str]]]:
    gs = list(s.execute(select(ModelGroup).order_by(ModelGroup.sort, ModelGroup.name)).scalars())
    members: dict[str, list[tuple[int, str]]] = {}
    for mm in s.execute(select(ModelGroupMember)).scalars():
        members.setdefault(mm.group_id, []).append((mm.sort, mm.model_id))
    return [(g, [mid for _, mid in sorted(members.get(g.id, []))]) for g in gs]


def audience_ok(g: ModelGroup, p: perms.Principal) -> bool:
    aud = g.audience or []
    if not aud:
        return True
    keys = subject_keys(p)
    return any((a.get("type"), a.get("id")) in keys for a in aud)


def _backends(s) -> dict[str, Backend]:
    return {b.id: b for b in s.execute(select(Backend)).scalars()}
