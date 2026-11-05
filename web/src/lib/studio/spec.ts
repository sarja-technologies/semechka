// The editing decisions of a project (the "spec"): defaults, tolerant reading of what the server sends, and the picture geometry used to draw
// the live framing. The numbers that decide what is really sent to the model (processing size, output size) always come from the server's plan,
// never from here: this file only draws a faithful preview of the framing while a handle is being dragged.
import type { Rect, Spec, Treatment } from './types';

export const ASPECTS: { id: string; label: string; ratio: number | null }[] = [
  { id: 'original', label: 'Original', ratio: null },
  { id: '16:9', label: '16:9', ratio: 16 / 9 },
  { id: '9:16', label: '9:16', ratio: 9 / 16 },
  { id: '1:1', label: '1:1', ratio: 1 },
  { id: '4:3', label: '4:3', ratio: 4 / 3 },
  { id: '3:4', label: '3:4', ratio: 3 / 4 },
  { id: 'custom', label: 'Custom', ratio: null }
];

/** Resolution presets are named by the short edge of the picture, like video sizes are everywhere (720p = 720 on the short side). */
export const RESOLUTIONS: { id: string; label: string; edge: number | null }[] = [
  { id: 'original', label: 'Original', edge: null },
  { id: '480p', label: '480p', edge: 480 },
  { id: '720p', label: '720p', edge: 720 },
  { id: '1080p', label: '1080p', edge: 1080 },
  { id: '1440p', label: '1440p', edge: 1440 },
  { id: '4k', label: '4K', edge: 2160 },
  { id: 'custom', label: 'Custom', edge: null }
];

export const TREATMENTS: { id: Treatment; label: string; note: string }[] = [
  { id: 'crop', label: 'Crop', note: 'Cuts the picture to the new shape. Nothing is stretched.' },
  { id: 'pad', label: 'Pad', note: 'Keeps the whole picture and adds black bars to fill the new shape.' },
  { id: 'stretch', label: 'Stretch', note: 'Squeezes or stretches the whole picture to the new shape. This distorts people and objects.' }
];

export const KINDS: { id: 'person' | 'vehicle' | 'animal' | 'object' | 'other'; label: string; example: string }[] = [
  { id: 'person', label: 'Person', example: 'the singer on the left' },
  { id: 'vehicle', label: 'Vehicle', example: 'the dirt bike' },
  { id: 'animal', label: 'Animal', example: 'the brown dog' },
  { id: 'object', label: 'Object', example: 'the red cup on the table' },
  { id: 'other', label: 'Other', example: 'the sky' }
];

export function defaultSpec(): Spec {
  return {
    source_asset: null, trim: null, crop: null,
    frame: { aspect: 'original', ratio: null, lock: true, treatment: 'crop' },
    resolution: { preset: 'original', width: null, height: null, lock_aspect: true }, audio: 'keep',
    references: [], mode: null,
    subject: { text: '', kind: 'person', box: null, points: [] },
    model: null, settings: {}, accept_experimental: false
  };
}

/** What the server stored, filled in with defaults for anything it does not (yet) keep. */
export function normalizeSpec(raw: Partial<Spec> | null | undefined): Spec {
  const d = defaultSpec(), r: any = raw ?? {};
  return {
    ...d, ...r,
    trim: r.trim && typeof r.trim.start === 'number' && typeof r.trim.end === 'number' ? { start: r.trim.start, end: r.trim.end } : null,
    crop: r.crop && r.crop.w > 0 && r.crop.h > 0 ? { x: r.crop.x, y: r.crop.y, w: r.crop.w, h: r.crop.h } : null,
    frame: { ...d.frame, ...(r.frame ?? {}) },
    resolution: { ...d.resolution, ...(r.resolution ?? {}) },
    references: Array.isArray(r.references) ? r.references.map((x: any) => ({ asset: String(x.asset), active: x.active !== false, subject: x.subject ?? null, label: x.label ?? '' })) : [],
    subject: { ...d.subject, ...(r.subject ?? {}), points: Array.isArray(r.subject?.points) ? r.subject.points : [] },
    settings: { ...(r.settings ?? {}) },
    audio: r.audio === 'mute' ? 'mute' : 'keep',
    accept_experimental: !!r.accept_experimental
  };
}

/** The part of the spec that decides what the prepared video looks like. Changing it makes an earlier prepared copy out of date. */
export const prepKey = (s: Spec) => JSON.stringify([s.source_asset, s.trim, s.crop, s.frame, s.resolution, s.audio]);

const even = (v: number) => Math.max(2, Math.round(v / 2) * 2);
export const fullRect = (w: number, h: number): Rect => ({ x: 0, y: 0, w, h });
export const isFull = (r: Rect | null, w: number, h: number) => !r || (r.x <= 0 && r.y <= 0 && r.w >= w && r.h >= h);

/** The largest centred rectangle of a given shape inside `within` (whole pixels, even sizes so video encoders accept it). */
export function fitInside(within: Rect, ratio: number): Rect {
  let w = within.w, h = within.w / ratio;
  if (h > within.h) { h = within.h; w = within.h * ratio; }
  w = Math.min(within.w, even(w)); h = Math.min(within.h, even(h));
  return { x: Math.round(within.x + (within.w - w) / 2), y: Math.round(within.y + (within.h - h) / 2), w, h };
}

/** The shape asked for by the aspect setting: width / height, or null = "as it is". */
export function targetRatio(frame: Spec['frame']): number | null {
  if (frame.aspect === 'custom') return frame.ratio && frame.ratio > 0 ? frame.ratio : null;
  return ASPECTS.find((a) => a.id === frame.aspect)?.ratio ?? null;
}

export type Framing = {
  /** shape of the finished frame, width / height */
  ratio: number;
  /** the part of the source that is shown */
  content: Rect;
  treatment: Treatment;
  /** with padding: how much of the frame the picture covers (0..1 each way) */
  fill: { w: number; h: number };
};

/** How the finished frame is made from the source: the same rules the server applies, used only to draw the preview. */
export function framing(spec: Spec, sw: number, sh: number): Framing {
  const content = spec.crop && !isFull(spec.crop, sw, sh) ? spec.crop : fullRect(sw, sh);
  const R = targetRatio(spec.frame);
  const treatment = spec.frame.treatment;
  const a = content.w / content.h;
  if (!R || Math.abs(R - a) < 0.002) return { ratio: a, content, treatment, fill: { w: 1, h: 1 } };
  if (treatment === 'crop') { const c = fitInside(content, R); return { ratio: c.w / c.h, content: c, treatment, fill: { w: 1, h: 1 } }; }
  if (treatment === 'stretch') return { ratio: R, content, treatment, fill: { w: 1, h: 1 } };
  return { ratio: R, content, treatment, fill: a > R ? { w: 1, h: R / a } : { w: a / R, h: 1 } };
}

/** "9:16" for 0.5625, "1.37:1" when it is not a familiar shape. */
export function ratioWords(r: number): string {
  for (const a of ASPECTS) if (a.ratio && Math.abs(a.ratio - r) < 0.006) return a.label;
  return r >= 1 ? `${r.toFixed(2)}:1` : `1:${(1 / r).toFixed(2)}`;
}

export const fmtSecs = (s: number | null | undefined) => (s == null || !isFinite(s) ? '' : s >= 60 ? `${Math.floor(s / 60)}:${(s % 60).toFixed(1).padStart(4, '0')}` : `${s.toFixed(s % 1 ? 1 : 0)} s`);
export const fmtMb = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(mb >= 10240 ? 0 : 1)} GB` : `${Math.round(mb)} MB`);
