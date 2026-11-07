"""Imajutsu: the iPhone Photo preset. A prompt BUILDER (not a suffix) that turns the person's words and a few scene chips into natural sentences about light and
framing, with per-model wording rules that come from the routing data (``avoid_phone_words``: Z-Image draws hands holding phones or poster graphics when the
prompt says "phone snapshot" or "9:16"). It never claims EXIF or a particular camera: the result is a picture that LOOKS like an unedited phone photo.

Data-driven: add a scene or change a sentence here; nothing else in the code mentions a scene.
"""
from __future__ import annotations

import re

PRESET_ID = "iphone_photo"
PRESET_LABEL = "iPhone Photo"
PRESET_DESCRIPTION = ("A picture that looks like an unedited photo from a modern phone camera: natural light and colour, casual framing, mild noise in the dark. "
                      "Made by a text-to-image model; there is no real camera data (no EXIF) in the file.")
DEFAULT_ASPECT = "9:16"
DEFAULT_COUNT = 1

# id -> label, the sentence for models that take natural wording, and the sentence for models flagged avoid_phone_words (no phone, selfie or screen wording)
SCENES: dict[str, dict] = {
    "outdoor_day": {"label": "Outdoor day",
                    "sentence": "It is daytime outdoors with soft natural sunlight and slightly uneven exposure, like a quick handheld photo.",
                    "plain": "It is daytime outdoors with soft natural sunlight and slightly uneven exposure, framed by hand."},
    "indoor": {"label": "Indoor",
               "sentence": "It is indoors in ordinary room light with some window light, a little warm, with natural soft shadows.",
               "plain": "It is indoors in ordinary room light with some window light, a little warm, with natural soft shadows."},
    "night": {"label": "Night",
              "sentence": "It is night, lit by street lights and shop windows, with real dark areas and some grain.",
              "plain": "It is night, lit by street lights and shop windows, with real dark areas and some grain."},
    "flash": {"label": "Flash",
              "sentence": "The light is a direct on-camera flash: a bright subject, slightly harsh shadows behind it and a darker background.",
              "plain": "The light is a direct flash: a bright subject, slightly harsh shadows behind it and a darker background."},
    "selfie": {"label": "Selfie",
               "sentence": "It is taken at arm's length from the person's own point of view, slightly wide-angle and framed a little off-centre.",
               "plain": "It is a close portrait taken at arm's length, slightly wide-angle, the person looking into the lens and framed a little off-centre."},
    "mirror": {"label": "Mirror",
               "sentence": "The person is seen in a mirror in an ordinary room, framed from chest height, a little off-centre.",
               "plain": "The person is seen in a mirror in an ordinary room, framed from chest height, a little off-centre."},
    "casual": {"label": "Casual",
               "sentence": "The framing is casual and unposed, like a quick picture of a normal moment, not a studio shot.",
               "plain": "The framing is casual and unposed, like a quick picture of a normal moment, not a studio shot."},
}
LOOK = {"sentence": "It looks like an unedited handheld phone photo: natural colour, mild noise in the dark areas, no filters, no text, no graphics.",
        "plain": "It looks like an unedited ordinary photograph: natural colour, mild noise in the dark areas, no filters, no text, no graphics."}
TIERS = ("fast", "best", "polished")
TIER_LABELS = {"fast": "Fast", "best": "Best realism", "polished": "Polished"}
DEFAULT_TIER = "best"

# "Phone finish": a subtle photographic recipe (gentle S-curve, lifted shadows, restrained highlights, fine grain, light sharpening). Optional and switchable;
# applied through the same adjust pipeline as any other adjustment, never mixed into the generation.
FINISH_RECIPE = {"contrast": 8, "highlights": -8, "shadows": 10, "saturation": 5, "sharpness": 15, "grain": 0.12}


def scene_chips() -> list[dict]:
    return [{"id": k, "label": v["label"]} for k, v in SCENES.items()]


def info(model_id: str | None = None, tiers: list[dict] | None = None) -> dict:
    """The preset as the capabilities route lists it."""
    return {"tiers": tiers or [], "default_tier": DEFAULT_TIER, "id": PRESET_ID, "label": PRESET_LABEL, "description": PRESET_DESCRIPTION, "aspect": DEFAULT_ASPECT, "count": DEFAULT_COUNT,
            "scene_chips": scene_chips(), "model_id": model_id, "finish": {"label": "Phone finish", "recipe": dict(FINISH_RECIPE),
                                                                         "note": "A subtle photographic finish (curve, grain, light sharpening). Optional; it is applied to the result, not to the generation."}}


def forbidden_in(text: str, words: list[str]) -> list[str]:
    """The words of ``words`` that occur in ``text`` (whole words or the literal token), in the order of ``words``."""
    low = (text or "").lower()
    hits = []
    for w in words:
        wl = w.lower()
        pat = rf"(?<![a-z0-9]){re.escape(wl)}(?![a-z0-9])" if wl.isalnum() else re.escape(wl)
        if re.search(pat, low) or (wl.isalpha() and re.search(rf"(?<![a-z0-9]){re.escape(wl)}s?(?![a-z0-9])", low)):
            hits.append(w)
    return hits


def build_prompt(text: str, scenes: list[str], *, avoid_words: list[str] | None = None, model_alias: str = "this model") -> dict:
    """-> {"base", "final", "sentences": [{"scene", "label", "sentence"}], "look": str, "plain": bool, "warnings": [..]}.

    ``avoid_words`` is set (the routing flag ``avoid_phone_words`` of the model that will run) => the wording is plain: no scene or look sentence contains any of
    those words, and none is ever injected. The person's own text is left exactly as written; if it contains such a word the plan says so (a warning)."""
    plain = avoid_words is not None
    base = (text or "").strip()
    sents = []
    seen = set()
    for sid in scenes or []:
        if sid in SCENES and sid not in seen:
            seen.add(sid)
            sents.append({"scene": sid, "label": SCENES[sid]["label"], "sentence": SCENES[sid]["plain" if plain else "sentence"]})
    look = LOOK["plain" if plain else "sentence"]
    parts = []
    if base and plain:           # plain wording: 'A photograph of ...', and never a word that makes the model draw a selfie or a phone
        low = base[:1].lower() + base[1:] if base.split(" ", 1)[0].lower() in ("a", "an", "the") else base
        if not re.match(r"(?i)(a |an )?(photo|photograph|picture)\b", base):
            base_p = "A photograph of " + low
        else:
            base_p = base
        parts.append(base_p if base_p[-1] in ".!?" else base_p + ".")
    elif base:
        parts.append(base if base[-1] in ".!?" else base + ".")
    parts += [x["sentence"] for x in sents] + [look]
    final = " ".join(parts)
    warnings = []
    if plain:
        bad = forbidden_in(base, avoid_words or [])
        if bad:
            warnings.append(f"{model_alias} draws hands holding phones or poster graphics when the prompt says " + ", ".join(f"'{w}'" for w in bad)
                            + ". Describe the scene in plain words instead.")
    return {"base": base, "final": final, "sentences": sents, "look": look, "plain": plain, "warnings": warnings}
