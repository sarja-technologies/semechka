// Which reference pictures really condition the result. The person marks pictures Active; the operation takes only so many, in order. A picture is
// "used" only when it is active AND within that limit; everything else is kept in the project and said, plainly, not to influence the result.
import type { RefItem } from './types';

export type RefState = { used: boolean; /** 1-based place among the used pictures (the order they are sent), 0 when not used */ order: number; note: string };

const pics = (n: number) => `${n} picture${n === 1 ? '' : 's'}`;

export function refStates(refs: RefItem[], max: number): RefState[] {
  let used = 0;
  const out: RefState[] = refs.map((r) => {
    if (r.active && used < max) { used++; return { used: true, order: used, note: '' }; }
    return { used: false, order: 0, note: '' };
  });
  return refs.map((r, i) => out[i].used ? out[i] : { ...out[i], note: r.active
    ? `Not used: this operation takes ${pics(max)}`
    : used >= max ? `Not used: this operation takes ${pics(max)}` : 'Not used' });
}

export const usedCount = (refs: RefItem[], max: number) => refStates(refs, max).filter((s) => s.used).length;

/** Keep the person's choices, but never leave more pictures marked active than the operation takes (the later ones are switched off, in plain view). */
export function fitActive(refs: RefItem[], max: number): RefItem[] {
  let n = 0;
  return refs.map((r) => (r.active && n++ >= max ? { ...r, active: false } : r));
}

/** The first `max` pictures become active, all others not. */
export function autoFill(refs: RefItem[], max: number): RefItem[] {
  return refs.map((r, i) => ({ ...r, active: i < max }));
}

export function move<T>(list: T[], from: number, to: number): T[] {
  if (from === to || from < 0 || to < 0 || from >= list.length || to >= list.length) return list;
  const a = list.slice(); const [x] = a.splice(from, 1); a.splice(to, 0, x); return a;
}
