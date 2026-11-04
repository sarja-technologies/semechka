// Which attachment plays which part. A "slot" is one place a file can go: an input of an editing profile (OpenJutsu's source video, its
// reference pictures), or the single source of a picture/video request. Slots come from what the engine published for the chosen
// operation, never from a fixed list here; only the per-operation counts that the engine documents in prose are kept in a small table.

export type Slot = { input: string; label: string; kind: 'video' | 'image'; multi: boolean; max: number; min: number; hint?: string };
export type Item = { key: string; kind: 'image' | 'video' | 'file'; name: string; role: string | null };

type Schema = Record<string, any>;
export type Profile = { id?: string; family?: string; name?: string; inputs_schema?: Schema; operations?: string[] } | null | undefined;

const OP_LABEL: Record<string, string> = { swap: 'Swap', advanced_swap: 'Advanced swap', object_edit: 'Object edit', check_mask: 'Check mask' };
export const opLabel = (op: string) => OP_LABEL[op] ?? op.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());

/** How many reference pictures each OpenJutsu operation takes (the engine's own wording for the "refs" input). */
const OPENJUTSU_REFS: Record<string, [number, number]> = { swap: [1, 1], advanced_swap: [1, 4], object_edit: [0, 1], check_mask: [0, 0] };

/** The engine names the family; older servers do not pass it on, so the profile's own id or name is read too. */
export const isOpenJutsu = (p: Profile) => !!p && (p.family === 'openjutsu' || /openjutsu/i.test(`${p.id ?? ''} ${p.name ?? ''}`));

export function currentOperation(profile: Profile, values: Record<string, any>): string | null {
  const s = profile?.inputs_schema?.operation;
  return s ? (values.operation ?? s.default ?? s.values?.[0] ?? null) : null;
}

const singular = (l: string) => l.replace(/ies$/, 'y').replace(/(picture|image|video|file)s$/i, '$1');

/** The file inputs the chosen operation uses, as slots. */
export function profileSlots(profile: Profile, operation: string | null): Slot[] {
  const out: Slot[] = [];
  for (const [input, spec] of Object.entries(profile?.inputs_schema ?? {}) as [string, any][]) {
    if (spec.type !== 'file') continue;
    if (operation && spec.operations && !spec.operations.includes(operation)) continue;
    const multi = !!spec.multi;
    let min = spec.required ? 1 : 0, max = multi ? Number(spec.max_files ?? 9) : 1;
    if (isOpenJutsu(profile) && input === 'refs' && operation && OPENJUTSU_REFS[operation]) [min, max] = OPENJUTSU_REFS[operation];
    if (max === 0) continue;
    const kind = spec.kind === 'image' ? 'image' : 'video';
    out.push({ input, label: singular(spec.label ?? input.replace(/_/g, ' ')).replace(/^./, (c) => c.toUpperCase()), kind, multi: max > 1, max, min,
      hint: spec.description });
  }
  return out;
}

/** Slots for an ordinary picture or video request: one source picture at most. */
export function plainSlots(op: 'text' | 'image' | 'video', opts: { needSource: boolean; canUseSource: boolean; vision: boolean }): Slot[] {
  if (op === 'text') return opts.vision ? [{ input: 'attach', label: 'Attached picture', kind: 'image', multi: true, max: 10, min: 0 }] : [];
  if (!opts.canUseSource && !opts.needSource) return [];
  return [{ input: 'source', label: op === 'image' ? 'Picture to change' : 'First frame', kind: 'image', multi: false, max: 1, min: opts.needSource ? 1 : 0 }];
}

export const slotFor = (slots: Slot[], item: Pick<Item, 'role'>) => slots.find((s) => s.input === item.role);

/** Give each item without a role the first fitting slot that still has room. Roles the person chose are left alone. */
export function autoAssign<T extends Item & { roleAuto?: boolean }>(items: T[], slots: Slot[]): void {
  const count = (input: string) => items.filter((i) => i.role === input).length;
  for (const it of items) {
    if (it.kind === 'file') continue;
    const valid = it.role && slots.some((s) => s.input === it.role && s.kind === it.kind);
    if (valid) continue;
    if (it.role && !it.roleAuto) continue; // a chosen role that this operation does not use: keep it, it is shown as unused
    if (it.role && !slots.some((s) => s.kind === it.kind)) continue; // nothing here takes this kind of file (check mask takes no pictures): keep the role for when the operation changes back
    it.role = null;
    const hit = slots.find((s) => s.kind === it.kind && count(s.input) < s.max);
    if (hit) { it.role = hit.input; it.roleAuto = true; }
  }
}

export type Verdict = {
  /** per item: the slot it fills, 'unused' when the chosen operation does not use it, or 'extra' when its slot is already full */
  state: Record<string, 'used' | 'unused' | 'extra' | 'unassigned'>;
  problems: string[];
  notes: string[];
};

/** Which items count, what is missing and what cannot go together, in plain words. */
export function evaluate(items: Item[], slots: Slot[], operation: string | null): Verdict {
  const state: Verdict['state'] = {}, problems: string[] = [], notes: string[] = [];
  const where = operation ? ` for ${opLabel(operation).toLowerCase()}` : '';
  for (const it of items) {
    if (it.kind === 'file') { state[it.key] = 'used'; continue; }
    const s = slotFor(slots, it);
    state[it.key] = s && s.kind === it.kind ? 'used' : !slots.some((x) => x.kind === it.kind) ? 'unused' : 'unassigned';
  }
  const covered = new Set<string>();
  for (const s of slots) {
    // files marked for this slot, plus pictures or videos the program could not place because it is full: they are all "attached for it"
    const first = slots.find((x) => x.kind === s.kind) === s;
    const mine = items.filter((i) => i.kind === s.kind && (i.role === s.input && state[i.key] === 'used' || (first && state[i.key] === 'unassigned')));
    mine.forEach((i, n) => { if (n >= s.max && i.role === s.input) state[i.key] = 'extra'; });
    const n = mine.length;
    const lc = s.label.toLowerCase();
    if (n < s.min) problems.push(s.min === s.max ? `Add ${s.min === 1 ? `a ${lc}` : `${s.min} ${lc}s`}${where}.` : `Add at least ${s.min} ${lc}${s.min > 1 ? 's' : ''}${where}.`);
    else if (n > s.max) {
      problems.push(`Only ${s.max === 1 ? 'one' : s.max} ${lc}${s.max === 1 ? '' : 's'} can be used${where}: ${n} are attached. Remove ${n - s.max === 1 ? 'one' : n - s.max}${operation ? ' or choose another operation' : ''}.`);
      mine.forEach((i) => covered.add(i.key));
    }
  }
  const unused = items.filter((i) => state[i.key] === 'unused');
  if (unused.length) notes.push(`${unused.length === 1 ? `“${unused[0].name}” is` : `${unused.length} files are`} kept here but not used${operation ? ` by ${opLabel(operation).toLowerCase()}` : ''}.`);
  const un = items.filter((i) => state[i.key] === 'unassigned' && !covered.has(i.key));
  if (un.length) {
    const wantVideo = un.some((i) => i.kind === 'video') && !slots.some((s) => s.kind === 'video');
    notes.push(wantVideo ? 'This request cannot use a video.' : `Choose what ${un.length === 1 ? `“${un[0].name}”` : `the ${un.length} unassigned files`} should be used for.`);
  }
  return { state, problems, notes };
}
