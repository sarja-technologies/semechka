#!/usr/bin/env bash
# Fails on anti-slop patterns in first-party UI source (see DESIGN.md hard bans).
set -u
cd "$(dirname "$0")/../web/src"
FAIL=0
chk() { # description, pattern
  if grep -rniE "$2" --include=*.svelte --include=*.css --include=*.ts . | grep -v "design-lint:allow" ; then echo "FAIL: $1"; FAIL=1; fi
}
chk "gradients are banned" 'gradient\('
chk "glass / blur panels are banned" 'backdrop-filter'
chk "purple-family keywords are banned" '\b(purple|violet|lavender|lilac|indigo|fuchsia|magenta)\b'
chk "sparkle/orb iconography" '\b(sparkle|sparkles|orb|glow)\b'
# hex colours in the violet hue band (hue 250–330°) anywhere in first-party CSS/Svelte
python3 - <<'PY' || FAIL=1
import colorsys, pathlib, re, sys
bad = []
for p in pathlib.Path(".").rglob("*"):
    if p.suffix not in (".svelte", ".css", ".ts"): continue
    for m in re.finditer(r"#([0-9a-fA-F]{6})\b", p.read_text(errors="ignore")):
        h = m.group(1); r, g, b = (int(h[i:i+2], 16) / 255 for i in (0, 2, 4))
        hue, s, v = colorsys.rgb_to_hsv(r, g, b)
        if 250 <= hue * 360 <= 335 and s > 0.12 and v > 0.2: bad.append(f"{p}: #{h} hue {hue*360:.0f}")
emo = re.compile("[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B50\u2728]")
for p in pathlib.Path(".").rglob("*"):
    if p.suffix in (".svelte", ".css", ".ts"):
        for i, line in enumerate(p.read_text(errors="ignore").splitlines(), 1):
            if emo.search(line): bad.append(f"{p}:{i}: emoji used as UI")
if bad: print("FAIL: violet/purple hex colours:\n  " + "\n  ".join(bad)); sys.exit(1)
PY
[ "$FAIL" -eq 0 ] && echo "design-lint: pass" || exit 1
