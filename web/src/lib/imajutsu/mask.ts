// The selection ("mask") of an image: a list of simple operations in pixels of the source picture. The same list draws the translucent overlay on
// screen and the PNG the server uses, so what is shown is what is sent. The PNG is white where something is selected and black elsewhere (white =
// change). "Invert" and the soft edge are settings the server applies (spec.selection), so they are not baked into the picture.
// The server keeps only the picture, so the strokes travel inside it as a PNG text chunk: reopening the project (on any device) brings them back.
import type { Box, MaskDoc, MaskOp } from './types.ts';

export const MAX_WORK = 2048;       // the on-screen canvas never needs more pixels than this on its long side
export const MAX_OPS = 400;          // the saved project stays small
export const MAX_PTS = 6000;         // numbers per stroke
export const CHUNK_KEY = 'semechka-selection';

export function emptyMask(w: number, h: number): MaskDoc { return { w, h, ops: [] }; }

/** A short fingerprint of what the PNG shows. Two documents with the same key make the same PNG. */
export function maskKey(d: Pick<MaskDoc, 'w' | 'h' | 'ops'>): string {
  const s = JSON.stringify([d.w, d.h, d.ops]);
  let a = 2166136261 >>> 0, b = 5381;
  for (let i = 0; i < s.length; i++) { const c = s.charCodeAt(i); a = Math.imul(a ^ c, 16777619) >>> 0; b = (Math.imul(b, 33) + c) >>> 0; }
  return `${a.toString(36)}${b.toString(36)}${s.length.toString(36)}`;
}

/** Drop points that are closer than `min` pixels to the previous one, so a long drag stays a short list. */
export function thin(pts: number[], min: number): number[] {
  if (pts.length <= 4) return pts.slice();
  const out = [pts[0], pts[1]];
  for (let i = 2; i < pts.length - 2; i += 2) {
    if (Math.hypot(pts[i] - out[out.length - 2], pts[i + 1] - out[out.length - 1]) >= min) out.push(pts[i], pts[i + 1]);
  }
  out.push(pts[pts.length - 2], pts[pts.length - 1]);
  return out;
}

export const round1 = (n: number) => Math.round(n * 10) / 10;

/** Is there anything selected at all? Erasing everything leaves nothing, which is "no selection". */
export function hasSelection(d: Pick<MaskDoc, 'ops'> | null | undefined): boolean {
  if (!d) return false;
  let on = false;
  for (const o of d.ops) {
    if (o.t === 'rect') on = o.sub ? on : true;
    else if (o.t === 'brush') on = true;
  }
  return on;
}

export function addOp(d: MaskDoc, op: MaskOp): MaskDoc { return { ...d, ops: [...d.ops, op].slice(-MAX_OPS) }; }

/** A selection that is one single box (nothing else) is sent as a box, with no picture to upload. */
export function asBox(d: MaskDoc | null): Box | null {
  if (!d || d.ops.length !== 1 || d.ops[0].t !== 'rect' || d.ops[0].sub || !d.w || !d.h) return null;
  const o = d.ops[0];
  return { x: round5(o.x / d.w), y: round5(o.y / d.h), w: round5(o.w / d.w), h: round5(o.h / d.h) };
}
const round5 = (n: number) => Math.round(n * 1e5) / 1e5;
export function fromBox(b: Box, w: number, h: number): MaskDoc {
  return { w, h, ops: [{ t: 'rect', x: Math.round(b.x * w), y: Math.round(b.y * h), w: Math.round(b.w * w), h: Math.round(b.h * h) }] };
}

/** Draw the operations on a canvas: opaque white where something is selected, transparent elsewhere. `k` = canvas pixels per source pixel. */
export function paintOps(ctx: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D, ops: MaskOp[], k: number) {
  const c = ctx as CanvasRenderingContext2D;
  c.save();
  c.lineCap = 'round'; c.lineJoin = 'round'; c.fillStyle = '#fff'; c.strokeStyle = '#fff';
  for (const o of ops) {
    c.globalCompositeOperation = (o.t === 'erase' || (o.t === 'rect' && o.sub)) ? 'destination-out' : 'source-over';
    if (o.t === 'rect') { c.fillRect(o.x * k, o.y * k, o.w * k, o.h * k); continue; }
    c.lineWidth = Math.max(1, o.size * k);
    const p = o.pts;
    if (p.length < 4) { c.beginPath(); c.arc(p[0] * k, p[1] * k, Math.max(0.5, o.size * k / 2), 0, Math.PI * 2); c.fill(); continue; }
    c.beginPath(); c.moveTo(p[0] * k, p[1] * k);
    for (let i = 2; i < p.length; i += 2) c.lineTo(p[i] * k, p[i + 1] * k);
    c.stroke();
  }
  c.restore();
}

// ---- PNG text chunk ---------------------------------------------------------------------------------------------------------------------------
const CRC = (() => { const t = new Uint32Array(256); for (let n = 0; n < 256; n++) { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; t[n] = c >>> 0; } return t; })();
function crc32(b: Uint8Array): number { let c = 0xffffffff; for (let i = 0; i < b.length; i++) c = CRC[(c ^ b[i]) & 255] ^ (c >>> 8); return (c ^ 0xffffffff) >>> 0; }
const u32 = (v: number) => new Uint8Array([(v >>> 24) & 255, (v >>> 16) & 255, (v >>> 8) & 255, v & 255]);
const ascii = (s: string) => Uint8Array.from(s, (ch) => ch.charCodeAt(0) & 127);

/** The same PNG with the strokes in a tEXt chunk after the header. The picture itself is unchanged. */
export function embedStrokes(png: Uint8Array, d: MaskDoc): Uint8Array {
  const json = JSON.stringify({ v: 1, w: d.w, h: d.h, ops: d.ops });
  const body = new Uint8Array([...ascii(CHUNK_KEY), 0, ...ascii(json.replace(/[^\x20-\x7e]/g, ''))]);
  const type = ascii('tEXt');
  const typed = new Uint8Array(type.length + body.length); typed.set(type); typed.set(body, type.length);
  const chunk = new Uint8Array([...u32(body.length), ...typed, ...u32(crc32(typed))]);
  const at = 8 + 4 + 4 + 13 + 4;      // signature + IHDR (length, type, data, crc)
  const out = new Uint8Array(png.length + chunk.length);
  out.set(png.subarray(0, at)); out.set(chunk, at); out.set(png.subarray(at), at + chunk.length);
  return out;
}

/** The strokes a PNG carries (made by embedStrokes), or null for a picture that came from elsewhere. */
export function readStrokes(png: Uint8Array): MaskDoc | null {
  if (png.length < 33 || png[0] !== 0x89 || png[1] !== 0x50) return null;
  let p = 8;
  const dv = new DataView(png.buffer, png.byteOffset, png.byteLength);
  while (p + 12 <= png.length) {
    const len = dv.getUint32(p), type = String.fromCharCode(png[p + 4], png[p + 5], png[p + 6], png[p + 7]);
    if (type === 'tEXt') {
      const data = png.subarray(p + 8, p + 8 + len), z = data.indexOf(0);
      if (z > 0 && String.fromCharCode(...data.subarray(0, z)) === CHUNK_KEY) {
        try {
          const j = JSON.parse(new TextDecoder('latin1').decode(data.subarray(z + 1)));
          if (j && Array.isArray(j.ops) && j.w > 0 && j.h > 0) return { w: Number(j.w), h: Number(j.h), ops: j.ops as MaskOp[] };
        } catch { return null; }
      }
    }
    if (type === 'IDAT' || type === 'IEND') return null;
    p += 12 + len;
  }
  return null;
}

/** The PNG the server receives: size of the source picture, white = selected, black = not, with the strokes inside. */
export async function renderPng(d: MaskDoc): Promise<Blob> {
  const sel = document.createElement('canvas'); sel.width = d.w; sel.height = d.h;
  paintOps(sel.getContext('2d')!, d.ops, 1);
  const out = document.createElement('canvas'); out.width = d.w; out.height = d.h;
  const c = out.getContext('2d')!;
  c.fillStyle = '#000'; c.fillRect(0, 0, d.w, d.h);
  c.drawImage(sel, 0, 0);
  const blob: Blob = await new Promise((res, rej) => out.toBlob((b) => (b ? res(b) : rej(new Error('The selection could not be turned into a picture.'))), 'image/png'));
  const bytes = embedStrokes(new Uint8Array(await blob.arrayBuffer()), d);
  return new Blob([bytes as BlobPart], { type: 'image/png' });
}

/** Share of the picture that is selected, from the operations drawn on a small canvas (for the plain-words readout). */
export function coverage(d: MaskDoc, invert = false): number {
  const k = Math.min(1, 160 / Math.max(d.w, d.h, 1)), w = Math.max(1, Math.round(d.w * k)), h = Math.max(1, Math.round(d.h * k));
  const c = document.createElement('canvas'); c.width = w; c.height = h;
  const x = c.getContext('2d', { willReadFrequently: true })!;
  paintOps(x, d.ops, k);
  const px = x.getImageData(0, 0, w, h).data;
  let on = 0;
  for (let i = 3; i < px.length; i += 4) if (px[i] > 127) on++;
  const f = on / (w * h);
  return invert ? 1 - f : f;
}
