// The decisions of an image project (the "spec"): defaults and tolerant reading of what the server stored. The numbers that decide what is really
// sent to the model (native size, output size, final prompt) always come from the server's plan; this file never works them out for the request.
import type { AspectEntry, MaskDoc, RefItem, Spec } from './types.ts';

/** The shapes people pick. The words are short; the server's own aspect list is used for anything it adds. */
export const ASPECTS: AspectEntry[] = [
  { id: '9:16', label: '9:16', w: 9, h: 16 },
  { id: '16:9', label: '16:9', w: 16, h: 9 },
  { id: '4:5', label: '4:5', w: 4, h: 5 },
  { id: '3:4', label: '3:4', w: 3, h: 4 },
  { id: '1:1', label: '1:1', w: 1, h: 1 }
];

export const QUALITY: { id: string; label: string; note: string }[] = [
  { id: 'draft', label: 'Draft', note: 'Fewest steps. Quick tries.' },
  { id: 'standard', label: 'Standard', note: 'The model’s own recommended steps.' },
  { id: 'high', label: 'High', note: 'More steps. Slower; the gain is small on models made for few steps.' }
];

export const SCENES: { id: string; label: string }[] = [
  { id: 'outdoor_day', label: 'Outdoor day' }, { id: 'indoor', label: 'Indoor' }, { id: 'night', label: 'Night' }, { id: 'flash', label: 'Flash' },
  { id: 'selfie', label: 'Selfie' }, { id: 'mirror', label: 'Mirror' }, { id: 'casual', label: 'Casual' }
];

export function defaultSpec(mode = 'generate'): Spec {
  return {
    version: 1, kind: 'image', mode, prompt: '', negative: '', preset: { id: null, scenes: [], finish: false },
    aspect: { id: '9:16', w: 9, h: 16 }, size: { native: null, output: null }, quality: 'standard', seed: null, count: 1,
    source_asset: null, mask_asset: null, selection: { box: null, feather: 0, invert: false, keep_outside: null },
    outpaint: { pad: null, target: null, anchor: { x: 0.5, y: 0.5 }, overlap: 0 }, upscale: { refine: false, strength: 0.3 },
    references: [], model: null, advanced: {}, adjustments: {}
  };
}

const sz = (v: any) => (v && typeof v.width === 'number' && typeof v.height === 'number' ? { width: v.width, height: v.height } : null);

/** What the server stored, filled in with defaults for anything it does not (yet) keep. */
export function normalizeSpec(raw: Partial<Spec> | null | undefined): Spec {
  const r: any = raw ?? {}, d = defaultSpec(typeof r.mode === 'string' ? r.mode : 'generate');
  return {
    ...d, ...r, kind: 'image',
    preset: { ...d.preset, ...(r.preset ?? {}), scenes: Array.isArray(r.preset?.scenes) ? r.preset.scenes.map(String) : [] },
    aspect: { id: r.aspect?.id ?? d.aspect.id, w: r.aspect?.w ?? null, h: r.aspect?.h ?? null },
    size: { native: sz(r.size?.native), output: sz(r.size?.output) },
    selection: { ...d.selection, ...(r.selection ?? {}) },
    outpaint: { ...d.outpaint, ...(r.outpaint ?? {}), anchor: { ...d.outpaint.anchor, ...(r.outpaint?.anchor ?? {}) } },
    upscale: { ...d.upscale, ...(r.upscale ?? {}) },
    references: Array.isArray(r.references) ? r.references.filter((x: any) => x?.asset).map((x: any): RefItem => ({ asset: String(x.asset), active: x.active !== false, role: x.role ?? null, preserve: String(x.preserve ?? ''), label: String(x.label ?? '') })) : [],
    advanced: r.advanced && typeof r.advanced === 'object' ? r.advanced : {},
    adjustments: r.adjustments && typeof r.adjustments === 'object' ? r.adjustments : {},
    prompt: String(r.prompt ?? ''), negative: String(r.negative ?? ''), count: Math.min(4, Math.max(1, Math.round(Number(r.count) || 1)))
  };
}

/** Pixel size to the nearest multiple the model works in. */
export const snapTo = (v: number, step: number) => Math.max(step, Math.round(v / step) * step);

export const ratioOfAspect = (a: { id: string; w: number | null; h: number | null }): number | null => (a.w && a.h ? a.w / a.h : null);

export const fmtDims = (d?: { width?: number | null; height?: number | null } | null) => (d && d.width && d.height ? `${d.width}×${d.height}` : '–');
export const fmtSecs = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
export const fmtMb = (mb: number) => (mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`);

export type { MaskDoc };
