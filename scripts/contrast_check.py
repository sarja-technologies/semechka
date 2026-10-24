#!/usr/bin/env python3
"""Computes WCAG contrast ratios for every foreground/background token pair in web/src/app.css, per theme.
Exit status 1 if any text pair is below 4.5:1 (or a UI-boundary pair below 3:1). Used by CI and by docs/DESIGN_REVIEW.md."""
import re
import sys
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "web/src/app.css"


def lum(hexcolor: str) -> float:
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = lum(a), lum(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def themes():
    css = CSS.read_text()
    out = {}
    for name, sel in (("paper", r":root, :root\[data-theme='paper'\]"), ("carbon", r":root\[data-theme='carbon'\]"), ("graphite", r":root\[data-theme='graphite'\]")):
        block = re.search(sel + r"\s*\{(.*?)\}", css, re.S).group(1)
        out[name] = dict(re.findall(r"--([\w-]+):\s*(#[0-9a-fA-F]{6})", block))
    return out


TEXT_PAIRS = [("fg", "bg"), ("fg", "surface"), ("fg", "surface-2"), ("fg-muted", "bg"), ("fg-muted", "surface"), ("fg-muted", "surface-2"),
              ("accent", "bg"), ("accent", "surface"), ("accent", "surface-2"), ("accent", "accent-soft"), ("accent-fg", "accent"),
              ("danger", "surface"), ("danger", "bg"), ("danger", "surface-2"), ("success", "surface-2"), ("warn", "surface-2"), ("info", "surface-2"), ("danger", "danger-soft"), ("success", "surface"), ("success", "success-soft"),
              ("warn", "surface"), ("warn", "warn-soft"), ("info", "surface"), ("info", "info-soft"), ("fg", "accent-soft")]
UI_PAIRS = [("border-strong", "surface"), ("border-strong", "bg"), ("accent", "surface")]


def main() -> int:
    bad = 0
    for theme, t in themes().items():
        print(f"\n{theme}")
        for fg, bg in TEXT_PAIRS:
            r = ratio(t[fg], t[bg])
            flag = "" if r >= 4.5 else "  <-- below 4.5:1"
            bad += r < 4.5
            print(f"  {fg:14} on {bg:12} {r:5.2f}{flag}")
        for fg, bg in UI_PAIRS:
            r = ratio(t[fg], t[bg])
            flag = "" if r >= 3 else "  <-- below 3:1 (UI boundary)"
            bad += r < 3
            print(f"  [ui] {fg:9} on {bg:12} {r:5.2f}{flag}")
    print(f"\n{bad} failing pair(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
