// Reading /api/studio/capabilities. The server (and behind it the engine) decides what can be done; these helpers only look things up and fall back
// to the most cautious reading when a field is missing (never to a more generous one), so the page can never promise more than was advertised.
import type { CapModel, Caps, ModeEntry, ModelEntry, Policy, Status } from './types';

export const modeById = (c: Caps | null, id: string | null) => (id && c?.modes.find((m) => m.id === id)) || null;
export const modelById = (c: Caps | null, id: string | null) => (id && c?.models.find((m) => m.model_id === id)) || null;

/** The words a person sees for a status. */
export const STATUS_LABEL: Record<string, string> = { ready: 'Ready', experimental: 'Experimental', not_ready: 'Not ready', unavailable: 'Unavailable' };
export const runnable = (s: Status | string | undefined) => s === 'ready' || s === 'experimental';

/** What the engine says about one model for one mode. */
export const entryFor = (mode: ModeEntry | null, modelId: string | null): ModelEntry | null => (mode && modelId && mode.models?.find((m) => m.model_id === modelId)) || null;

export type Choice = { model: CapModel; entry: ModelEntry | null; supported: boolean; status: Status | 'unsupported'; reason: string };

/** Every model, for the chosen mode: those that support it (with the engine's status for them) and those that cannot, with the reason why not. */
export function choicesFor(caps: Caps | null, mode: ModeEntry | null): Choice[] {
  if (!caps || !mode) return [];
  return caps.models.map((model) => {
    const entry = entryFor(mode, model.model_id);
    const listed = model.modes?.includes(mode.id) || !!entry;
    if (!listed) {
      const why = model.unsupported?.find((u) => u.mode === mode.id)?.reason;
      return { model, entry: null, supported: false, status: 'unsupported' as const, reason: why || `${model.alias ?? 'This model'} cannot do this.` };
    }
    const status = (entry?.status ?? mode.status) as Status;
    return { model, entry, supported: runnable(status), status, reason: entry?.reason || (runnable(status) ? '' : mode.reason || '') };
  });
}

/** How many pictures this operation needs at least, and how many it really uses at most. Per-operation maps (as the engine publishes them) and flat ones are both read. */
export function refPolicy(mode: ModeEntry | null, entry: ModelEntry | null): Policy {
  const pick = (p: any): Policy | null => {
    if (!p || typeof p !== 'object') return null;
    const q = typeof p.active_max === 'number' ? p : mode ? p[mode.operation] : null;
    return q && typeof q.active_max === 'number' ? { min: Number(q.min ?? 0), active_max: Number(q.active_max) } : null;
  };
  const n = mode?.needs?.refs;
  return pick(entry?.reference_policy) ?? pick(entry?.needs?.refs) ?? (n && typeof n.active_max === 'number' ? { min: Number(n.min ?? 0), active_max: Number(n.active_max) } : null) ?? { min: 0, active_max: 1 };
}

export type SubjectSupport = { text: boolean; box: boolean; points: boolean; scrub: boolean; note: string };
export function subjectSupport(mode: ModeEntry | null, entry: ModelEntry | null): SubjectSupport {
  const s = entry?.subject_selection ?? mode?.subject_selection ?? {};
  return { text: s.text !== false, box: s.box === true, points: s.points === true, scrub: s.scrub === true || s.any_frame === true, note: typeof s.note === 'string' ? s.note : '' };
}

export const needsVideo = (mode: ModeEntry | null) => mode?.needs?.video !== false;
export const needsSubject = (mode: ModeEntry | null) => !!mode && (mode.needs?.subject_text === true || mode.needs?.subject === true);

/** The longest source clip the model takes, in seconds (null = nothing advertised). */
export function maxSourceSeconds(caps: Caps | null, entry: ModelEntry | null): number | null {
  const v = entry?.limits?.max_source_seconds ?? caps?.limits?.max_source_seconds ?? caps?.limits?.video_max_seconds;
  return typeof v === 'number' && v > 0 ? v : null;
}

/** Can the server run a selection check as a studio job before the full run? Only when it says so, explicitly: an engine that merely lists a
 *  check operation is not enough, because the request must be one the server knows how to turn into that operation (never into a full run). */
export function canCheck(mode: ModeEntry | null, entry: ModelEntry | null, caps: Caps | null): boolean {
  const flag = entry?.check_selection ?? mode?.check_selection ?? caps?.check_selection ?? caps?.limits?.check_selection;
  return !!mode && flag === true && mode.operation !== 'reference_video';
}

/** Plain-language one-liners for the eight modes. Unknown modes show the engine's own label and reason only. */
export const MODE_BLURB: Record<string, string> = {
  subject_replacement: 'Replace a subject in your video with the one in your pictures.',
  character_replacement: 'Swap a character in your video for a different one.',
  object_replacement: 'Replace a thing in your video with another thing.',
  vehicle_replacement: 'Replace a car or motorcycle in your video with another vehicle.',
  motion_transfer: 'Give the movement of your video to the person in a picture.',
  reference_to_video: 'Make a new video from your pictures. The source video is not used.',
  video_restyling: 'Keep the video as it is and change its look.',
  advanced_editing: 'Several replacements at once, one picture for each person.'
};
