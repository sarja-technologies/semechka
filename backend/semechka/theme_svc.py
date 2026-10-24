"""Themes: the canonical token list, colour parsing, contrast checks, theme bundles and bounded custom CSS.

The token file (theme/tokens.json) is shared with the web client, so the editor and the server agree on every token.
A Custom theme is stored fully resolved (every token, light and dark) together with the editor's inputs, so the client
never has to re-derive colours to display it and the server can check every contrast pair it promises.
"""
from __future__ import annotations

import colorsys
import json
import re
from functools import lru_cache
from pathlib import Path

from .settings_reg import SettingError

TOKENS_PATH = Path(__file__).parent / "theme" / "tokens.json"
THEMES = ["light", "dark", "graphite", "custom"]


@lru_cache(maxsize=1)
def spec() -> dict:
    return json.loads(TOKENS_PATH.read_text())


def token_ids() -> list[str]:
    return [t["id"] for t in spec()["tokens"]]


def preset(name: str) -> dict[str, str]:
    return dict(spec()["presets"][name]["values"])


# ---- colours -------------------------------------------------------------------------------------
_NUM = r"\s*([+-]?\d*\.?\d+)(%?)\s*"
_RGB = re.compile(rf"^rgba?\({_NUM},{_NUM},{_NUM}(?:,{_NUM})?\)$", re.I)
_RGB_SP = re.compile(rf"^rgba?\({_NUM}\s{_NUM}\s{_NUM}(?:/{_NUM})?\)$", re.I)
_HSL = re.compile(r"^hsla?\(\s*([+-]?\d*\.?\d+)(?:deg)?\s*[, ]\s*([+-]?\d*\.?\d+)%\s*[, ]\s*([+-]?\d*\.?\d+)%\s*(?:[,/]\s*([+-]?\d*\.?\d+)(%?)\s*)?\)$", re.I)


def parse_color(value: str) -> tuple[int, int, int, float]:
    """HEX (#rgb, #rgba, #rrggbb, #rrggbbaa), rgb()/rgba() and hsl()/hsla(). Returns (r, g, b, alpha 0..1)."""
    if not isinstance(value, str):
        raise SettingError("Colours must be text.")
    v = value.strip()
    if v.startswith("#"):
        h = v[1:]
        if not re.fullmatch(r"[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8}", h):
            raise SettingError(f"'{value}' is not a valid HEX colour.")
        if len(h) in (3, 4):
            h = "".join(c * 2 for c in h)
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        a = int(h[6:8], 16) / 255 if len(h) == 8 else 1.0
        return r, g, b, a
    m = _RGB.match(v) or _RGB_SP.match(v)
    if m:
        parts = m.groups()

        def chan(num, pct):
            x = float(num)
            return round(min(255.0, max(0.0, x * 2.55 if pct else x)))
        r, g, b = chan(*parts[0:2]), chan(*parts[2:4]), chan(*parts[4:6])
        a = 1.0
        if parts[6] is not None:
            a = float(parts[6]) / (100 if parts[7] else 1)
        return r, g, b, min(1.0, max(0.0, a))
    m = _HSL.match(v)
    if m:
        hue, sat, lig = float(m.group(1)) % 360, float(m.group(2)) / 100, float(m.group(3)) / 100
        rr, gg, bb = colorsys.hls_to_rgb(hue / 360, min(1, max(0, lig)), min(1, max(0, sat)))
        a = 1.0 if m.group(4) is None else float(m.group(4)) / (100 if m.group(5) else 1)
        return round(rr * 255), round(gg * 255), round(bb * 255), min(1.0, max(0.0, a))
    raise SettingError(f"'{value}' is not a colour. Use HEX (#1D2021), rgb(29 32 33) or hsl(195 6% 12%).")


def to_hex(rgba: tuple[int, int, int, float]) -> str:
    r, g, b, a = rgba
    base = f"#{r:02X}{g:02X}{b:02X}"
    return base if a >= 0.999 else base + f"{round(a * 255):02X}"


def normalise(value: str) -> str:
    return to_hex(parse_color(value))


def _over(top: tuple[int, int, int, float], under: tuple[int, int, int, float]) -> tuple[int, int, int, float]:
    a = top[3]
    return (round(top[0] * a + under[0] * (1 - a)), round(top[1] * a + under[1] * (1 - a)),
            round(top[2] * a + under[2] * (1 - a)), 1.0)


def _lum(c) -> float:
    def ch(x):
        x = x / 255
        return x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c[0]) + 0.7152 * ch(c[1]) + 0.0722 * ch(c[2])


def contrast(fg: str, bg: str, base: str | None = None) -> float:
    """WCAG contrast. Translucent colours are composited: bg over `base`, then fg over the result."""
    under = parse_color(base) if base else (255, 255, 255, 1.0)
    b = _over(parse_color(bg), under)
    f = _over(parse_color(fg), b)
    hi, lo = max(_lum(f), _lum(b)), min(_lum(f), _lum(b))
    return (hi + 0.05) / (lo + 0.05)


def contrast_report(values: dict[str, str]) -> list[dict]:
    out = []
    for pair in spec()["contrast"]:
        ratio = contrast(values[pair["fg"]], values[pair["bg"]], values["bg"])
        out.append({**pair, "ratio": round(ratio, 2), "ok": ratio + 1e-9 >= pair["min"]})
    return out


# ---- custom theme --------------------------------------------------------------------------------
def complete(values: dict | None, fallback: str) -> dict[str, str]:
    """Every token present and normalised; missing ones come from the named preset."""
    base = preset(fallback)
    out = {}
    alpha_ok = {t["id"]: t.get("alpha", False) for t in spec()["tokens"]}
    for tid in token_ids():
        raw = (values or {}).get(tid, base[tid])
        rgba = parse_color(raw)
        if rgba[3] < 0.999 and not alpha_ok[tid]:
            raise SettingError(f"'{tid}' cannot be transparent.")
        out[tid] = to_hex(rgba)
    return out


def validate_custom(value, *, enforce_contrast: bool = True) -> dict:
    if not isinstance(value, dict):
        raise SettingError("A custom theme is an object with 'light' and 'dark' token sets.")
    out = {"schema": "semechka.theme/1", "name": str(value.get("name") or "Custom")[:40]}
    for variant in ("light", "dark"):
        vals = complete(value.get(variant), variant)
        if enforce_contrast:
            bad = [p for p in contrast_report(vals) if not p["ok"]]
            if bad:
                b = bad[0]
                raise SettingError(f"{variant.title()} variant: {b['label']} is {b['ratio']}:1, needs {b['min']}:1"
                                   + (f" (and {len(bad) - 1} more)" if len(bad) > 1 else "") + ".")
        out[variant] = vals
    base = value.get("base") or {}
    out["base"] = {v: {k: normalise(c) for k, c in (base.get(v) or {}).items() if k in ("brand", "brand-2", "accent")}
                   for v in ("light", "dark")}
    ov = value.get("overrides") or {}
    out["overrides"] = {v: [t for t in (ov.get(v) or []) if t in token_ids()] for v in ("light", "dark")}
    return out


def custom_from_legacy_accent(accent_light: str | None, accent_dark: str | None, base_theme: str) -> dict:
    """0.2.0 applied an administrator accent on top of every preset. Recreate that as a Custom theme."""
    light, dark = preset("light"), preset("dark" if base_theme != "graphite" else "graphite")
    for vals, acc in ((light, accent_light), (dark, accent_dark)):
        if acc:
            for t in ("accent", "brand-2", "link"):
                vals[t] = normalise(acc)
    return {"schema": "semechka.theme/1", "name": "Custom (from 0.2.0 accent)", "light": light, "dark": dark,
            "base": {"light": {k: light[k] for k in ("brand", "brand-2", "accent")},
                     "dark": {k: dark[k] for k in ("brand", "brand-2", "accent")}},
            "overrides": {"light": [], "dark": []}, "accent": {"light": accent_light, "dark": accent_dark}}


# ---- bundles: the editor's draft, export and import ----------------------------------------------
BUNDLE_KEYS = {  # bundle field -> setting id
    "default": "brand.theme_default", "follow_system": "theme.follow_system", "user_choice": "theme.user_choice",
    "custom": "theme.custom", "font": "theme.font", "font_size": "theme.font_size", "radius": "brand.radius",
    "density": "brand.density", "user_align": "layout.user_align", "message_width": "layout.message_width",
    "sidebar_width": "layout.sidebar_width", "css": "theme.custom_css",
}


def bundle_from_settings(get) -> dict:
    return {"schema": "semechka.theme-bundle/1", **{k: get(sid) for k, sid in BUNDLE_KEYS.items()}}


# ---- custom CSS within boundaries ------------------------------------------------------------------
CSS_MAX = 20_000
_CSS_BANNED = [
    (re.compile(r"@import", re.I), "@import is not allowed: custom CSS cannot load other files."),
    (re.compile(r"@font-face", re.I), "@font-face is not allowed: fonts are chosen under Typography."),
    (re.compile(r"@namespace|@charset", re.I), "@namespace and @charset are not allowed."),
    (re.compile(r"expression\s*\(|javascript:|vbscript:|-moz-binding|behavior\s*:", re.I), "Scripting constructs are not allowed."),
    (re.compile(r"<|>\s*/|\\"), "Angle brackets that could close the style element and backslash escapes are not allowed."),
]
_URL = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.I)
_ASSET_URL = re.compile(r"^/api/assets/[0-9a-f]{24}\.(png|webp|jpg|svg)$")


def validate_css(value) -> str:
    """Custom CSS for the chat interface. Bounded: no imports, no remote URLs (which would reveal viewers to other
    servers), no scripting constructs. Only uploaded branding assets may be referenced. Admin pages never load it."""
    if not isinstance(value, str):
        raise SettingError("Custom CSS must be text.")
    css = value.strip()
    if len(css) > CSS_MAX:
        raise SettingError(f"Custom CSS is limited to {CSS_MAX:,} characters.")
    for rx, why in _CSS_BANNED:
        if rx.search(css):
            raise SettingError(why)
    for m in _URL.finditer(css):
        if not _ASSET_URL.match(m.group(2).strip()):
            raise SettingError("url() may only point at an image uploaded under Branding (/api/assets/…).")
    if css.count("{") != css.count("}"):
        raise SettingError("Unbalanced braces.")
    return css
