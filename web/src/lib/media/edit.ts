// Asking the server to trim, crop and rotate for real, and following the work until the new file exists.
import { ApiError, get, post } from '$lib/api';
import type { Asset } from './assets';

export type EditBody = {
  trim?: { start: number; end: number } | null;
  crop?: { x: number; y: number; w: number; h: number } | null;
  rotate?: number; flip?: boolean; mute?: boolean; name?: string;
};

export type EditHandle = { done: Promise<Asset>; cancel: () => void };

/** Start an edit of `assetId`. `onprogress` gets 0..1. Resolves with the new asset; rejects with a message fit to show. */
export function runEdit(assetId: string, body: EditBody, onprogress?: (p: number) => void): EditHandle {
  let stop = false, taskId = '';
  const done = (async () => {
    const t = await post(`/api/media/assets/${assetId}/edits`, body);
    taskId = t.id;
    for (;;) {
      if (stop) throw new ApiError(0, { code: 'cancelled', message: 'Cancelled.' });
      await new Promise((r) => setTimeout(r, 450));
      const s = await get(`/api/media/edits/${taskId}`);
      onprogress?.(s.progress ?? 0);
      if (s.status === 'completed') return s.asset as Asset;
      if (s.status === 'failed') throw new ApiError(422, { code: 'edit_failed', message: s.error || 'The edit could not be made.' });
      if (s.status === 'cancelled') throw new ApiError(0, { code: 'cancelled', message: 'Cancelled.' });
    }
  })();
  return { done, cancel: () => { stop = true; if (taskId) post(`/api/media/edits/${taskId}/cancel`).catch(() => {}); } };
}

/** The named aspect ratios offered for cropping. `null` ratio = free. */
export const PRESETS: { id: string; label: string; ratio: number | null }[] = [
  { id: 'original', label: 'Original', ratio: -1 },
  { id: '1:1', label: '1:1', ratio: 1 },
  { id: '16:9', label: '16:9', ratio: 16 / 9 },
  { id: '9:16', label: '9:16', ratio: 9 / 16 },
  { id: '4:3', label: '4:3', ratio: 4 / 3 },
  { id: 'free', label: 'Free', ratio: null }
];

/** The largest centred rectangle of a given shape inside w × h. */
export function fitRect(w: number, h: number, ratio: number, even = false) {
  let cw = w, ch = w / ratio;
  if (ch > h) { ch = h; cw = h * ratio; }
  const snap = (v: number) => (even ? Math.floor(v / 2) * 2 : Math.floor(v));
  cw = snap(cw); ch = snap(ch);
  return { x: snap((w - cw) / 2), y: snap((h - ch) / 2), w: cw, h: ch };
}
