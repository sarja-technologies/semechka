// Pan/zoom maths for the graph canvas. screen = world * k + (x, y). Pure, so the centring and zoom-at-cursor rules are unit tested.
export interface View { x: number; y: number; k: number }
export interface Size { w: number; h: number }
export interface Rect { x: number; y: number; w: number; h: number }

export const MIN_K = 0.04;
export const MAX_K = 2.5;
export const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));

/** Scale and offset that show all of `bounds` inside `size` with `pad` pixels of margin (never magnified beyond `maxK`). */
export function fitView(bounds: Size, size: Size, pad = 24, maxK = 1): View {
  if (bounds.w <= 0 || bounds.h <= 0 || size.w <= 0 || size.h <= 0) return { x: 0, y: 0, k: 1 };
  const k = clampK(Math.min((size.w - 2 * pad) / bounds.w, (size.h - 2 * pad) / bounds.h, maxK));
  return { k, x: (size.w - bounds.w * k) / 2, y: (size.h - bounds.h * k) / 2 };
}

/** Zoom by `factor` keeping the world point under the screen position (sx, sy) where it is. */
export function zoomAt(v: View, sx: number, sy: number, factor: number): View {
  const k = clampK(v.k * factor);
  const r = k / v.k;
  return { k, x: sx - (sx - v.x) * r, y: sy - (sy - v.y) * r };
}

/** Put the world point (wx, wy) in the middle of the viewport, optionally at a new scale. */
export function centerOn(v: View, wx: number, wy: number, size: Size, k = v.k): View {
  const kk = clampK(k);
  return { k: kk, x: size.w / 2 - wx * kk, y: size.h / 2 - wy * kk };
}

export const panBy = (v: View, dx: number, dy: number): View => ({ ...v, x: v.x + dx, y: v.y + dy });

/** Where a world point lands on screen. */
export const toScreen = (v: View, wx: number, wy: number) => ({ x: wx * v.k + v.x, y: wy * v.k + v.y });

/** The part of the world that is visible, widened by `margin` screen pixels. */
export function visibleWorld(v: View, size: Size, margin = 0): Rect {
  return { x: (-v.x - margin) / v.k, y: (-v.y - margin) / v.k, w: (size.w + 2 * margin) / v.k, h: (size.h + 2 * margin) / v.k };
}
export const intersects = (a: Rect, b: Rect) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;

/** Two-finger gesture: new view from the previous pair of touch points to the current pair. */
export function pinch(v: View, a0: { x: number; y: number }, b0: { x: number; y: number }, a1: { x: number; y: number }, b1: { x: number; y: number }): View {
  const d0 = Math.hypot(b0.x - a0.x, b0.y - a0.y) || 1, d1 = Math.hypot(b1.x - a1.x, b1.y - a1.y);
  const m0 = { x: (a0.x + b0.x) / 2, y: (a0.y + b0.y) / 2 }, m1 = { x: (a1.x + b1.x) / 2, y: (a1.y + b1.y) / 2 };
  const z = zoomAt(v, m0.x, m0.y, d1 / d0);
  return { ...z, x: z.x + (m1.x - m0.x), y: z.y + (m1.y - m0.y) };
}
