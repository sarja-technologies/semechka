// Pure helpers for Yaob web search (contract yaob-origin/1): the three modes, the status line, and how a reply's
// [n] citation markers are split around the sources that exist. No DOM and no network, so node --test runs them.

export type WebMode = 'auto' | 'on' | 'off';
export const WEB_MODES: { id: WebMode; label: string; note: string }[] = [
  { id: 'auto', label: 'Auto', note: 'Searches when a question needs current or outside information' },
  { id: 'on', label: 'On', note: 'Always ready to search, and searches when you ask' },
  { id: 'off', label: 'Off', note: 'Never searches the web' }
];

export const asMode = (v: unknown): WebMode => (v === 'on' || v === 'off' ? v : 'auto');

/** A status line for what the system reports it is doing before it writes. Unknown states say nothing. */
export function activityText(a: { state: string; query?: string; count?: number } | null | undefined): string {
  if (!a) return '';
  if (a.state === 'searching_web') return 'Searching the web…';
  if (a.state === 'reading_sources') return a.count ? `Reading ${a.count} source${a.count === 1 ? '' : 's'}…` : 'Reading sources…';
  if (a.state === 'verifying') return 'Checking the answer…';
  return '';
}

const CITE = /\[(\d{1,3})\]/g;
export const HAS_CITE = /\[\d{1,3}\]/;

/** Splits text around [n] markers whose source n exists: strings stay as they are, numbers become links.
 *  Returns null when nothing would change. */
export function citeParts(text: string, have: Set<number>): (string | number)[] | null {
  if (!have.size || !HAS_CITE.test(text)) return null;
  const out: (string | number)[] = [];
  let last = 0;
  for (const m of text.matchAll(CITE)) {
    const n = Number(m[1]);
    if (!have.has(n)) continue;
    const at = m.index ?? 0;
    if (at > last) out.push(text.slice(last, at));
    out.push(n);
    last = at + m[0].length;
  }
  if (last === 0) return null;
  if (last < text.length) out.push(text.slice(last));
  return out;
}
