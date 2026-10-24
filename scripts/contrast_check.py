#!/usr/bin/env python3
"""Computes WCAG contrast ratios for every pair listed in web/src/lib/theme/tokens.json, for each maintained theme
(Light, Dark, Graphite). These are the same pairs the theme editor checks before a Custom theme can be published.
Exit status 1 if any pair is below its minimum (4.5:1 for text, 3:1 for UI boundaries). Used by CI and by
docs/DESIGN_REVIEW.md."""
import json
import sys
from pathlib import Path

TOKENS = Path(__file__).resolve().parents[1] / "web/src/lib/theme/tokens.json"


def rgba(hexcolor: str) -> tuple[float, float, float, float]:
    h = hexcolor.lstrip("#")
    r, g, b = (int(h[i:i + 2], 16) for i in (0, 2, 4))
    return r, g, b, (int(h[6:8], 16) / 255 if len(h) == 8 else 1.0)


def over(top, under):
    a = top[3]
    return tuple(top[i] * a + under[i] * (1 - a) for i in range(3)) + (1.0,)


def lum(c) -> float:
    f = lambda v: v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4  # noqa: E731
    r, g, b = (v / 255 for v in c[:3])
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(fg: str, bg: str, base: str) -> float:
    # translucent colours are composited the way the browser paints them: background over the page, text over that
    b = over(rgba(bg), over(rgba(base), (255, 255, 255, 1.0)))
    f = over(rgba(fg), b)
    hi, lo = max(lum(f), lum(b)), min(lum(f), lum(b))
    return (hi + 0.05) / (lo + 0.05)


def main() -> int:
    spec = json.loads(TOKENS.read_text())
    bad = 0
    for name, preset in spec["presets"].items():
        t = preset["values"]
        print(f"\n{preset['label']} ({name})")
        for p in spec["contrast"]:
            r = ratio(t[p["fg"]], t[p["bg"]], t["bg"])
            low = r + 1e-9 < p["min"]
            bad += low
            print(f"  {p['fg']:16} on {p['bg']:16} {r:5.2f}  (min {p['min']}){'  <-- too low' if low else ''}  {p['label']}")
    print(f"\n{bad} failing pair(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
