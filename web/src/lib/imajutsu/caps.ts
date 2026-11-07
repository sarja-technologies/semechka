// Reading /api/imajutsu/capabilities. The server decides what can be done; these helpers only look things up and fall back to the most cautious
// reading when a field is missing (never to a more generous one), so the page can never promise more than was advertised.
import type { Caps, ModeEntry, ModelEntry, Status } from './types.ts';

/** The ten modes, in the order and with the exact words the person sees. `key` is the server's mode id. */
export const MODES: { key: string; label: string; blurb: string }[] = [
  { key: 'generate', label: 'Generate Image', blurb: 'Describe a picture and the model makes it.' },
  { key: 'iphone_photo', label: 'iPhone Photo', blurb: 'A natural-looking phone photo. The app writes the prompt for the model.' },
  { key: 'edit', label: 'Edit Image', blurb: 'Change your picture by telling the model what to do.' },
  { key: 'replace_subject', label: 'Replace Subject', blurb: 'Put a person, animal or thing from your pictures into your picture.' },
  { key: 'inpaint', label: 'Inpaint', blurb: 'Paint over a part of your picture and say what should be there instead.' },
  { key: 'outpaint', label: 'Outpaint', blurb: 'Make the picture bigger and let the model fill the new area.' },
  { key: 'reference', label: 'Reference Image', blurb: 'Make a new picture guided by your reference pictures.' },
  { key: 'restyle', label: 'Restyle', blurb: 'Keep the picture, change its look.' },
  { key: 'upscale', label: 'Upscale', blurb: 'Make the picture bigger. This is scaling, not a model: no new detail.' },
  { key: 'advanced', label: 'Advanced Workflow', blurb: 'Every engine setting, and the full view of what the engine does.' }
];

const norm = (s: string) => s.toLowerCase().replace(/[^a-z]/g, '');
const BY_LABEL = new Map(MODES.map((m) => [norm(m.label), m.key]));
/** Which of the ten modes a capability entry is (by the server's id, which is the key; falling back to its label). */
export function keyOf(m: Pick<ModeEntry, 'id' | 'label'> | null | undefined): string {
  if (!m) return '';
  return MODES.some((x) => x.key === m.id) ? m.id : BY_LABEL.get(norm(m.label ?? '')) ?? m.id;
}

export type Needs = {
  prompt: boolean; promptRequired: boolean; chips: boolean; source: boolean; selection: 'none' | 'optional' | 'required' | 'area'; refs: 'none' | 'optional' | 'required'; size: boolean;
  model: boolean; negative: boolean; refine: boolean; /** the words are about WHAT to replace in the picture */ what: boolean;
};
const N = (o: Partial<Needs>): Needs => ({ prompt: true, promptRequired: true, chips: false, source: false, selection: 'none', refs: 'none', size: true, model: true, negative: false, refine: false, what: false, ...o });
/** What each mode needs from the person. The server's own `needs` (source, mask, prompt, references) override these where it states them. */
export const NEEDS: Record<string, Needs> = {
  generate: N({}),
  iphone_photo: N({ chips: true }),
  edit: N({ source: true, refs: 'optional' }),
  replace_subject: N({ source: true, selection: 'optional', refs: 'required', what: true }),
  inpaint: N({ source: true, selection: 'required', refs: 'optional', what: true }),
  outpaint: N({ source: true, selection: 'area', promptRequired: false }),
  reference: N({ refs: 'required', promptRequired: false }),
  restyle: N({ source: true, refs: 'none' }),
  upscale: N({ prompt: false, promptRequired: false, source: true, model: false, refine: true }),
  advanced: N({ refs: 'optional', selection: 'optional', negative: true, promptRequired: true })
};
export function needsOf(key: string, entry?: ModeEntry | null): Needs {
  const base = { ...(NEEDS[key] ?? NEEDS.generate) }, n = entry?.needs;
  if (!n) return base;
  if (typeof n.source === 'boolean' && key !== 'advanced') base.source = n.source;
  if (typeof n.prompt === 'boolean' && key !== 'upscale') { base.prompt = true; base.promptRequired = base.promptRequired && n.prompt; }
  if (n.references_ok === false) base.refs = 'none';
  else if ((n.references_min ?? 0) > 0) base.refs = 'required';
  return base;
}

/** Ready, untested-but-works and experimental models can run a mode. */
export const runnable = (s: Status | string | undefined) => s === 'ready' || s === 'available' || s === 'experimental';
export const STATUS_LABEL: Record<string, string> = { ready: 'Ready', available: 'Works, not benchmarked', experimental: 'Experimental', not_ready: 'Not ready', unavailable: 'Unavailable' };

/** The capability entry for one of the ten modes, or null when the server does not list it. */
export const entryOfKey = (c: Caps | null, key: string): ModeEntry | null => c?.modes.find((m) => keyOf(m) === key) ?? null;

/** A mode is operational only when the server says so AND (except Upscale, which is scaling) at least one model can run it. */
export function modeState(c: Caps | null, key: string): { ok: boolean; status: Status; reason: string; entry: ModeEntry | null } {
  const entry = entryOfKey(c, key);
  if (!entry) return { ok: false, status: 'missing', reason: c ? 'This server does not offer it.' : 'Waiting for the server.', entry: null };
  if (!runnable(entry.status)) return { ok: false, status: entry.status, reason: entry.reason || 'Not available on this server.', entry };
  if (key !== 'upscale' && !(entry.models ?? []).some((m) => runnable(m.status))) return { ok: false, status: 'unavailable', reason: entry.reason || 'No installed model can do this yet.', entry };
  return { ok: true, status: entry.status, reason: entry.reason ?? '', entry };
}

export const modelEntry = (mode: ModeEntry | null, id: string | null): ModelEntry | null => (mode && id && (mode.models ?? []).find((m) => m.model_id === id)) || null;
export const modelName = (m: ModelEntry | null | undefined) => m?.alias || m?.engine_id || m?.model_id || '';

export type RoleInfo = { id: string; label: string; hint: string };
const ROLE_INFO: Record<string, RoleInfo> = {
  appearance: { id: 'appearance', label: 'Appearance', hint: 'How it looks: shape, colours, details.' },
  identity: { id: 'identity', label: 'Identity', hint: 'Who it is: keep the same face and features.' },
  composition: { id: 'composition', label: 'Composition', hint: 'Where things are and how the frame is laid out.' },
  lighting: { id: 'lighting', label: 'Lighting', hint: 'The light: direction, softness, colour.' },
  style: { id: 'style', label: 'Style', hint: 'The look: colour, brush, film, mood.' }
};
/** Only the roles the server says it supports (none listed = no role control). */
export function rolesFor(caps: Caps | null): RoleInfo[] {
  const raw = caps?.roles;
  if (!Array.isArray(raw)) return [];
  return raw.map((r: any) => (typeof r === 'string' ? r : r?.id)).filter(Boolean).map((id: string) => ROLE_INFO[id] ?? { id, label: id[0].toUpperCase() + id.slice(1), hint: '' });
}

/** Smallest and largest side, and the step, a model works in. */
export function sideLimits(entry: ModelEntry | null) {
  const l = entry?.limits ?? {};
  return { min: l.min_side ?? 256, max: l.max_side ?? 2048, step: l.multiple ?? 16 };
}
