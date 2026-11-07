// Photographic adjustments (recipe v1, backend/semechka/imajutsu_adjust.py). No inference: the server renders the recipe with one pipeline for the preview and
// the final, so this file only describes the controls and writes the recipe in its compact form (neutral values are left out).
import type { Caps, Crop, Recipe } from './types.ts';

export type Slider = { key: keyof Recipe & string; label: string; min: number; max: number; step: number; group: 'light' | 'colour' | 'detail'; hint: string; /** shown number = value * show */ show: number; unit: string };

const S = (key: Slider['key'], label: string, min: number, max: number, step: number, group: Slider['group'], hint: string, show = 1, unit = ''): Slider => ({ key, label, min, max, step, group, hint, show, unit });
/** The ranges here are the server's (imajutsu_adjust.RANGES); the capabilities' `adjust.ranges` replace them when the server states them. */
export const SLIDERS: Slider[] = [
  S('exposure', 'Exposure', -2, 2, 0.05, 'light', 'Brighter or darker overall, in stops.', 1, ' EV'),
  S('contrast', 'Contrast', -100, 100, 1, 'light', 'Difference between light and dark areas.'),
  S('highlights', 'Highlights', -100, 100, 1, 'light', 'Only the brightest areas.'),
  S('shadows', 'Shadows', -100, 100, 1, 'light', 'Only the darkest areas.'),
  S('temperature', 'Warmth', -100, 100, 1, 'colour', 'White balance: cooler (blue) to warmer (orange).'),
  S('tint', 'Tint', -100, 100, 1, 'colour', 'White balance: greener to more magenta.'),
  S('saturation', 'Saturation', -100, 100, 1, 'colour', 'How strong the colours are.'),
  S('sharpness', 'Sharpness', 0, 100, 1, 'detail', 'Edge crispness. It cannot add detail that is not there.'),
  S('grain', 'Grain', 0, 1, 0.01, 'detail', 'Fine film grain. The same grain appears in the preview and the file.', 100, '%'),
  S('vignette', 'Vignette', 0, 1, 0.01, 'detail', 'Darker corners.', 100, '%')
];
export const slidersFor = (caps: Caps | null): Slider[] => {
  const r = caps?.adjust?.ranges;
  return SLIDERS.map((s) => (r?.[s.key] ? { ...s, min: r[s.key][0], max: r[s.key][1] } : s));
};

export const GROUPS: { id: Slider['group']; label: string }[] = [{ id: 'light', label: 'Light' }, { id: 'colour', label: 'Colour' }, { id: 'detail', label: 'Detail' }];

const eq = (a: number, b: number) => Math.abs(a - b) < 1e-9;

/** The recipe without neutral values: what is stored and sent. */
export function cleanRecipe(r: Recipe): Recipe {
  const o: Recipe = {};
  for (const s of SLIDERS) { const v = r[s.key]; if (typeof v === 'number' && !eq(v, 0)) (o as any)[s.key] = Math.round(v * 1000) / 1000; }
  if (r.straighten && !eq(r.straighten, 0)) o.straighten = Math.round(r.straighten * 10) / 10;
  if (r.rotate && r.rotate % 360) o.rotate = ((r.rotate % 360) + 360) % 360;
  if (r.flip) o.flip = true;
  if (r.crop && r.crop.w > 0 && r.crop.h > 0) o.crop = { x: Math.round(r.crop.x), y: Math.round(r.crop.y), w: Math.round(r.crop.w), h: Math.round(r.crop.h) };
  if (r.output && r.output.width > 0 && r.output.height > 0) o.output = { width: Math.round(r.output.width), height: Math.round(r.output.height) };
  return o;
}
export const isNeutral = (r: Recipe) => Object.keys(cleanRecipe(r)).length === 0;

/** The URL the server renders the preview from. The recipe is JSON in the query, exactly as POST adjust receives it. */
export const previewUrl = (asset: string, r: Recipe) => `/api/imajutsu/assets/${asset}/adjust/preview?recipe=${encodeURIComponent(JSON.stringify(cleanRecipe(r)))}`;

/** Size of the picture after turning (and the crop on the turned picture); the straighten angle trims a little more, the server says exactly how much. */
export function turned(w: number, h: number, r: Recipe): { width: number; height: number } {
  return ((r.rotate ?? 0) / 90) % 2 ? { width: h, height: w } : { width: w, height: h };
}
export function sizeAfter(w: number, h: number, r: Recipe): { width: number; height: number } {
  if (r.output?.width && r.output?.height) return { width: r.output.width, height: r.output.height };
  const t = turned(w, h, r);
  return r.crop ? { width: r.crop.w, height: r.crop.h } : t;
}
export const withCrop = (r: Recipe, crop: Crop | null): Recipe => { const o = { ...r }; if (crop) o.crop = crop; else delete o.crop; return o; };

/** Plain-words list of what the recipe changes, for the summary line. */
export function describe(r: Recipe): string[] {
  const c = cleanRecipe(r), out: string[] = [];
  for (const s of SLIDERS) { const v = c[s.key]; if (typeof v === 'number') out.push(`${s.label} ${v > 0 && s.min < 0 ? '+' : ''}${Math.round(v * s.show * 10) / 10}${s.unit}`); }
  if (c.crop) out.push('Cropped');
  if (c.rotate) out.push(`Turned ${c.rotate}°`);
  if (c.straighten) out.push(`Straightened ${c.straighten}°`);
  if (c.flip) out.push('Mirrored');
  if (c.output) out.push(`Output ${c.output.width}×${c.output.height}`);
  return out;
}
