// Pure logic of the administrator explorer (conversations and media library): filter building, labels, paging and the in-memory reason
// register. No DOM and no Svelte here so it can be tested with `node --test`.

export type Filters = {
  user_id?: string; model_id?: string; kind?: string; workflow?: string; status?: string; mode?: string; media?: string; q?: string;
  since?: string; until?: string; conversation_id?: string;
};
export type FileRef = { type: 'asset' | 'attachment'; id: string; kind: string; mime?: string; name?: string; size?: number; label?: string };
export const MIN_REASON = 10;
export const MAX_REASON = 500;

/** The body filters the server takes. Dates come from <input type=date> as local YYYY-MM-DD; `until` is inclusive of that whole day. */
export function toApiFilters(f: Filters): Record<string, string | number> {
  const out: Record<string, string | number> = {};
  for (const k of ['user_id', 'model_id', 'kind', 'workflow', 'status', 'mode', 'media', 'conversation_id'] as const) if (f[k]) out[k] = f[k]!;
  if (f.q && f.q.trim()) out.q = f.q.trim().slice(0, 120);
  const s = dayStart(f.since), u = dayStart(f.until);
  if (s !== null) out.since = s;
  if (u !== null) out.until = u + 86400;
  return out;
}
/** Epoch seconds of local midnight for YYYY-MM-DD, or null when the text is not a real date. */
export function dayStart(v?: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(v ?? '');
  if (!m) return null;
  const d = new Date(+m[1], +m[2] - 1, +m[3]);
  if (d.getFullYear() !== +m[1] || d.getMonth() !== +m[2] - 1 || d.getDate() !== +m[3]) return null;
  return Math.floor(d.getTime() / 1000);
}
export function activeFilterCount(f: Filters): number {
  return Object.values(f).filter((v) => v !== undefined && v !== null && String(v).trim() !== '').length;
}
export const reasonOk = (r: string) => r.trim().length >= MIN_REASON && r.length <= MAX_REASON;

export const STATUS_LABEL: Record<string, string> = {
  queued: 'Queued', running: 'Running', completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', expired: 'Expired', missing: 'File missing',
  active: 'Active', archived: 'Archived'
};
/** Tone of a status chip. Colour never carries the meaning alone: the label is always shown. */
export function statusTone(s: string): 'ok' | 'warn' | 'bad' | 'info' | '' {
  return ({ completed: 'ok', active: 'ok', running: 'info', queued: 'info', failed: 'bad', missing: 'bad', cancelled: 'warn', expired: 'warn', archived: '' } as Record<string, 'ok' | 'warn' | 'bad' | 'info' | ''>)[s] ?? '';
}
export const WORKFLOW_LABEL: Record<string, string> = {
  chat_image: 'Chat image', chat_video: 'Chat video', imajutsu: 'Imajutsu', openjutsu: 'OpenJutsu', image_generation: 'Image generation',
  video_generation: 'Video generation', upload: 'Upload', edit: 'Edited copy', prepared: 'Prepared for OpenJutsu', export: 'Exported size',
  adjusted: 'Adjusted picture', scaled: 'Scaled picture', composited: 'Composited picture'
};
export const workflowLabel = (w: string) => WORKFLOW_LABEL[w] ?? w;
export const ROLE_LABEL: Record<string, string> = {
  source: 'Source', mask: 'Mask', reference: 'Reference', prepared: 'Prepared video', attachment: 'Attachment', video: 'Source video', refs: 'Reference'
};
export const roleLabel = (r: string) => ROLE_LABEL[r] ?? r;

export function fmtSize(n?: number | null): string {
  if (n == null) return '—';
  if (n >= 2 ** 30) return `${(n / 2 ** 30).toFixed(1)} GiB`;
  if (n >= 2 ** 20) return `${(n / 2 ** 20).toFixed(1)} MiB`;
  if (n >= 2 ** 10) return `${Math.round(n / 2 ** 10)} KiB`;
  return `${n} B`;
}
export function fmtDur(s?: number | null): string {
  if (s == null) return '—';
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)} s`;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return m >= 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min ${r} s`;
}
/** "512 × 512 · 4 steps · seed 7": the few parameters worth showing on a card. */
export function paramsLine(p: Record<string, any> | null | undefined): string {
  if (!p) return '';
  const out: string[] = [];
  if (p.width && p.height) out.push(`${p.width} × ${p.height}`);
  if (p.frames && p.fps) out.push(`${p.frames} frames at ${p.fps} fps`);
  if (p.steps != null) out.push(`${p.steps} steps`);
  if (p.seed != null && p.seed >= 0) out.push(`seed ${p.seed}`);
  return out.join(' · ');
}
/** Parameter rows for the detail drawer in a stable, readable order; anything unexpected follows alphabetically. */
const ORDER = ['operation', 'mode', 'width', 'height', 'frames', 'fps', 'steps', 'guidance', 'seed', 'scheduler', 'denoise', 'negative_prompt'];
export function paramRows(p: Record<string, any> | null | undefined): [string, string][] {
  const keys = Object.keys(p ?? {});
  keys.sort((a, b) => (ORDER.indexOf(a) < 0 ? 99 : ORDER.indexOf(a)) - (ORDER.indexOf(b) < 0 ? 99 : ORDER.indexOf(b)) || a.localeCompare(b));
  return keys.map((k) => [k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, ' '), typeof p![k] === 'object' ? JSON.stringify(p![k]) : String(p![k])]);
}

/** Cursor paging: each page's items are appended; a repeated id (an item that moved between calls) is not shown twice. */
export function appendPage<T extends { id: string }>(have: T[], page: T[]): T[] {
  const seen = new Set(have.map((x) => x.id));
  return [...have, ...page.filter((x) => !seen.has(x.id))];
}

/**
 * The reason register. A reason lives in memory for this browsing session only (never in storage). Lists reuse the one given the first time;
 * opening a new item asks again, with the last reason filled in, so every item opened is a conscious, recorded act.
 */
export class ReasonRegister {
  last = '';
  private seen = new Set<string>();
  /** Does this target need the dialog? Lists after the first do not; an item not yet confirmed does. */
  needs(kind: 'browse' | 'item', key: string): boolean {
    if (!reasonOk(this.last)) return true;
    return kind === 'item' && !this.seen.has(key);
  }
  confirm(key: string, reason: string) { this.last = reason.trim(); this.seen.add(key); }
  clear() { this.last = ''; this.seen.clear(); }
}
