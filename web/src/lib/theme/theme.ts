// Design tokens -> CSS custom properties. The contract (token ids, presets, contrast pairs, fonts) is
// lib/theme/tokens.json, a copy of the server's backend/semechka/theme/tokens.json (design-lint keeps them equal).
import spec from './tokens.json';

export type ThemeName = 'light' | 'dark' | 'graphite' | 'custom';
export type TokenValues = Record<string, string>;
export type ThemeBundle = {
  default?: string; follow_system?: boolean; user_choice?: boolean; custom?: any; font?: string; font_size?: number;
  radius?: number; density?: string; user_align?: string; message_width?: string; sidebar_width?: number; css?: string;
  choices?: string[]; active?: string;
};

export const TOKENS = spec.tokens as { id: string; group: string; label: string; hint?: string; alpha: boolean }[];
export const GROUPS = spec.groups as { id: string; label: string }[];
export const CONTRAST = spec.contrast as { fg: string; bg: string; min: number; label: string }[];
export const PRESETS = spec.presets as Record<'light' | 'dark' | 'graphite', { label: string; scheme: 'light' | 'dark'; values: TokenValues }>;
export const FONTS = spec.typography.fonts as { id: string; label: string; stack: string }[];
export const THEME_LABELS: Record<string, string> = { light: 'Light', dark: 'Dark', graphite: 'Graphite', custom: 'Custom', system: 'Match device' };

const prefersDark = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches;

/** Which concrete token set to show: a choice, possibly 'system', resolved against the device and the bundle. */
export function resolve(choice: string | undefined, b: ThemeBundle): { name: ThemeName; variant: 'light' | 'dark'; values: TokenValues } {
  let name = (choice || b.default || 'light') as string;
  const follow = name === 'system' || (b.follow_system && name === (b.default || 'light') && name !== 'graphite');
  let variant: 'light' | 'dark' = 'light';
  if (name === 'system') name = prefersDark() ? 'dark' : 'light';
  if (name === 'custom' && !b.custom) name = 'light';
  if (name === 'custom') variant = follow ? (prefersDark() ? 'dark' : 'light') : 'light';
  else if (follow && (name === 'light' || name === 'dark')) name = prefersDark() ? 'dark' : 'light';
  if (name === 'dark' || name === 'graphite') variant = 'dark';
  const values = name === 'custom' ? { ...PRESETS[variant].values, ...(b.custom?.[variant] ?? {}) } : PRESETS[name as 'light'].values;
  return { name: name as ThemeName, variant, values };
}

/** Write a token set (and typography/layout values) as CSS variables on an element (document root or a preview box). */
export function writeTokens(el: HTMLElement, values: TokenValues, variant: 'light' | 'dark', b: ThemeBundle = {}) {
  const st = el.style;
  for (const t of TOKENS) st.setProperty(`--${t.id}`, values[t.id]);
  st.setProperty('color-scheme', variant);
  st.setProperty('--shadow', variant === 'dark' ? '0 8px 24px rgba(0, 0, 0, 0.45)' : '0 8px 24px rgba(0, 0, 0, 0.10)');
  const font = FONTS.find((f) => f.id === b.font) ?? FONTS[0];
  st.setProperty('--font', font.stack);
  if (b.font_size) st.setProperty('--font-size', `${b.font_size}px`);
  if (b.radius !== undefined && b.radius !== null) {
    st.setProperty('--radius', `${b.radius}px`);
    st.setProperty('--radius-sm', `${Math.max(2, Math.round(b.radius * 0.75))}px`);
  }
  if (b.sidebar_width) st.setProperty('--sidebar-w', `${b.sidebar_width}px`);
  st.setProperty('--msg-w', b.message_width === 'narrow' ? '680px' : b.message_width === 'wide' ? '1040px' : '820px');
}

export function applyTheme(choice: string | undefined, b: ThemeBundle, density?: string) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  const r = resolve(choice, b);
  writeTokens(root, r.values, r.variant, b);
  root.dataset.theme = r.name;
  root.dataset.variant = r.variant;
  root.dataset.density = density || b.density || 'comfortable';
  root.dataset.align = b.user_align === 'left' ? 'left' : 'right';
  const meta = document.querySelector<HTMLMetaElement>('meta[name="theme-color"]');
  if (meta) meta.content = r.values['header-bg'];
  return r;
}

/** Administrator CSS for the chat interface only. Removed on administration and sign-in pages so it can never hide
 *  the way back or a security control. The server validated it (no imports, no remote URLs, no scripting). */
export function setCustomCss(css: string | undefined | null) {
  if (typeof document === 'undefined') return;
  let el = document.getElementById('sem-custom-css') as HTMLStyleElement | null;
  if (!css) { el?.remove(); return; }
  if (!el) { el = document.createElement('style'); el.id = 'sem-custom-css'; document.head.appendChild(el); }
  if (el.textContent !== css) el.textContent = css;
}

// ---- colour maths for the editor (mirrors theme_svc.py; the server re-checks on publish) ------------------------
export function parseColor(v: string): [number, number, number, number] | null {
  const s = v.trim().toLowerCase();
  let m = s.match(/^#([0-9a-f]{3,8})$/);
  if (m) {
    let h = m[1];
    if (h.length === 3 || h.length === 4) h = [...h].map((c) => c + c).join('');
    if (h.length !== 6 && h.length !== 8) return null;
    const n = (i: number) => parseInt(h.slice(i, i + 2), 16);
    return [n(0), n(2), n(4), h.length === 8 ? n(6) / 255 : 1];
  }
  m = s.match(/^rgba?\(\s*([\d.]+%?)[\s,]+([\d.]+%?)[\s,]+([\d.]+%?)(?:[\s,/]+([\d.]+%?))?\s*\)$/);
  if (m) {
    const c = (x: string) => Math.round(x.endsWith('%') ? (parseFloat(x) * 255) / 100 : parseFloat(x));
    const a = m[4] ? (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4])) : 1;
    const out: [number, number, number, number] = [c(m[1]), c(m[2]), c(m[3]), a];
    return out.slice(0, 3).every((x) => x >= 0 && x <= 255) && a >= 0 && a <= 1 ? out : null;
  }
  m = s.match(/^hsla?\(\s*([\d.]+)(?:deg)?[\s,]+([\d.]+)%[\s,]+([\d.]+)%(?:[\s,/]+([\d.]+%?))?\s*\)$/);
  if (m) {
    const [r, g, b] = hslToRgb(parseFloat(m[1]), parseFloat(m[2]) / 100, parseFloat(m[3]) / 100);
    const a = m[4] ? (m[4].endsWith('%') ? parseFloat(m[4]) / 100 : parseFloat(m[4])) : 1;
    return [r, g, b, a];
  }
  return null;
}

export function hslToRgb(h: number, s: number, l: number): [number, number, number] {
  const k = (n: number) => (n + h / 30) % 12, a = s * Math.min(l, 1 - l);
  const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
  return [Math.round(f(0) * 255), Math.round(f(8) * 255), Math.round(f(4) * 255)];
}

export function rgbToHsl(r: number, g: number, b: number): [number, number, number] {
  r /= 255; g /= 255; b /= 255;
  const max = Math.max(r, g, b), min = Math.min(r, g, b), l = (max + min) / 2;
  if (max === min) return [0, 0, Math.round(l * 100)];
  const d = max - min, s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  const h = max === r ? (g - b) / d + (g < b ? 6 : 0) : max === g ? (b - r) / d + 2 : (r - g) / d + 4;
  return [Math.round(h * 60), Math.round(s * 100), Math.round(l * 100)];
}

export function toHex([r, g, b, a]: [number, number, number, number]): string {
  const h = (n: number) => Math.round(n).toString(16).padStart(2, '0').toUpperCase();
  return `#${h(r)}${h(g)}${h(b)}${a < 0.999 ? h(a * 255) : ''}`;
}

const over = (t: number[], u: number[]) => [0, 1, 2].map((i) => t[i] * t[3] + u[i] * (1 - t[3])).concat(1);
const lum = (c: number[]) => {
  const ch = c.slice(0, 3).map((x) => { x /= 255; return x <= 0.03928 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2];
};

export function contrast(fg: string, bg: string, base = '#FFFFFF'): number {
  const F = parseColor(fg), B = parseColor(bg), U = parseColor(base);
  if (!F || !B || !U) return 0;
  const b = over(B, over(U, [255, 255, 255, 1])), f = over(F, b);
  const hi = Math.max(lum(f), lum(b)), lo = Math.min(lum(f), lum(b));
  return (hi + 0.05) / (lo + 0.05);
}

export function contrastReport(values: TokenValues) {
  return CONTRAST.map((p) => {
    const ratio = contrast(values[p.fg], values[p.bg], values.bg);
    return { ...p, ratio: Math.round(ratio * 100) / 100, ok: ratio + 1e-9 >= p.min };
  });
}

/** Mix two colours (0..1 of b). Used to derive surfaces from the base colours. */
export function mix(a: string, b: string, t: number): string {
  const A = parseColor(a), B = parseColor(b);
  if (!A || !B) return a;
  return toHex([0, 1, 2].map((i) => A[i] + (B[i] - A[i]) * t).concat(1) as [number, number, number, number]);
}

/** Which maintained theme a Custom version was started from: Graphite shares the dark scheme, and its background
 *  (never derived) tells them apart. */
export function sourceOf(variant: 'light' | 'dark', current: TokenValues): 'light' | 'dark' | 'graphite' {
  return variant === 'dark' && current.bg?.toUpperCase() === PRESETS.graphite.values.bg.toUpperCase() ? 'graphite' : variant;
}

/** Re-derive every token that the administrator has not overridden from Colour 1, Colour 2 and Accent, keeping the
 *  preset's light/dark structure. Text colours are not derived (they would put contrast at risk); they keep the preset. */
export function derive(variant: 'light' | 'dark', base: { brand: string; 'brand-2': string; accent: string }, overrides: string[], current: TokenValues,
  from: 'light' | 'dark' | 'graphite' = sourceOf(variant, current)): TokenValues {
  const p = PRESETS[from].values;
  const bg = current.bg ?? p.bg;
  const d: TokenValues = {
    brand: base.brand, 'brand-2': base['brand-2'], accent: base.accent, link: base.accent,
    'btn-primary-bg': base.brand,
    'btn-primary-fg': contrast('#FFFFFF', base.brand) >= contrast(p.fg, base.brand) ? '#FFFFFF' : p.fg,
    'user-msg-bg': mix(bg, base['brand-2'], variant === 'light' ? 0.1 : 0.16),
    selected: mix(bg, base['brand-2'], variant === 'light' ? 0.13 : 0.2),
    'sidebar-bg': mix(p['sidebar-bg'], base.brand, 0.04),
    'header-bg': mix(p['header-bg'], base.brand, 0.02)
  };
  const out: TokenValues = { ...current };
  for (const [k, v] of Object.entries(d)) if (!overrides.includes(k)) out[k] = v;
  return out;
}
