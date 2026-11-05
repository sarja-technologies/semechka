// What every part of the interface knows about a picture or video, wherever it came from: generated, uploaded, or an edited copy.
// The server describes them all the same way (routers/media_assets.py); these helpers only read that shape.
import { get, qs } from '$lib/api';

export type Asset = {
  id: string;
  kind: 'image' | 'video';
  mime: string;
  size: number;
  width?: number | null;
  height?: number | null;
  duration?: number | null;
  name?: string;
  origin?: 'generated' | 'upload' | 'edit' | 'prepared' | 'export';
  parent?: string | null;
  edit?: { trim?: [number, number | null] | null; crop?: { x: number; y: number; w: number; h: number } | null; rotate?: number; flip?: boolean; mute?: boolean } | null;
  media?: { codec?: string | null; audio_codec?: string | null; audio?: boolean; fps?: number | null; playable?: boolean };
  has_thumb?: boolean;
  /** original = the browser plays the file itself; proxy = a converted copy is ready; pending = being converted; none = cannot be played here */
  preview?: 'original' | 'proxy' | 'pending' | 'none';
  saved?: boolean;
  created_at?: number;
  expires_at?: number | null;
  prompt?: string;
  job?: { id: string; prompt: string; conversation_id: string | null } | null;
  /** where the bytes are when they are not in the media library (an older chat attachment) */
  url?: string;
};

export const isMem = (id: string) => id.startsWith('me_');
export const isLibraryId = (id: string) => id.startsWith('ma_') || id.startsWith('me_');
export const contentUrl = (a: Pick<Asset, 'id' | 'preview'> & { url?: string }, opts: { download?: boolean; preview?: boolean } = {}) =>
  a.url ? a.url : `/api/media/assets/${a.id}/content${qs({ download: opts.download || undefined, preview: opts.preview ?? (a.preview === 'proxy' ? true : undefined) })}`;
export const thumbUrl = (a: Pick<Asset, 'id'>) => `/api/media/assets/${a.id}/thumb`;
export const stripUrl = (a: Pick<Asset, 'id'>, n = 12, h = 64) => `/api/media/assets/${a.id}/strip${qs({ n, h })}`;

export function fmtDuration(s: number | null | undefined, precise = false): string {
  if (s == null || !isFinite(s)) return '';
  const sign = s < 0 ? '-' : '';
  s = Math.abs(s);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  const body = precise ? sec.toFixed(2).padStart(5, '0') : String(Math.floor(sec)).padStart(2, '0');
  return sign + (h ? `${h}:${String(m).padStart(2, '0')}:${body}` : `${m}:${body}`);
}

/** "1:23.45", "83.45", "1:02:03" -> seconds, or null when it is not a time. */
export function parseTime(t: string): number | null {
  const parts = t.trim().split(':');
  if (!parts.length || parts.length > 3 || parts.some((p) => p === '' || !/^\d+(\.\d+)?$/.test(p))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

export function fmtSize(n: number | null | undefined): string {
  if (n == null) return '';
  if (n >= 1073741824) return `${(n / 1073741824).toFixed(1)} GB`;
  if (n >= 1048576) return `${(n / 1048576).toFixed(n >= 10485760 ? 0 : 1)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

export const fmtDims = (a: Pick<Asset, 'width' | 'height'>) => (a.width && a.height ? `${a.width}×${a.height}` : '');

/** The facts shown under a card: resolution, length, size. */
export function facts(a: Asset): string[] {
  return [fmtDims(a), a.kind === 'video' && a.duration ? fmtDuration(a.duration) : '', fmtSize(a.size)].filter(Boolean);
}

export const gcd = (a: number, b: number): number => (b ? gcd(b, a % b) : a);

/** Why a video cannot be shown by the browser and what the person can still do with it. */
export function playbackNote(a: Asset): string {
  if (a.kind !== 'video') return '';
  if (a.preview === 'pending') return 'Preparing a playable copy. The original is unchanged and can still be used.';
  if (a.preview === 'none' && a.media?.playable === false) return `This browser cannot play ${a.media?.codec?.toUpperCase() ?? 'this'} video. The file is still fine to use; trimming it makes a playable MP4.`;
  return '';
}

export async function fetchAsset(id: string): Promise<Asset> {
  return get(`/api/media/assets/${id}`);
}

export async function listAssets(q: { kind?: string; origin?: string; before?: number; limit?: number }): Promise<Asset[]> {
  return get(`/api/media/assets${qs(q)}`);
}
