// Which of the engine's own settings the Studio shows where. The names come from the schema the engine publishes (inputs_schema); the page keeps
// a small table of which belong with "what to replace" and which are plain output settings, and everything else the engine lists is shown too.
// Inputs the Studio sets itself (the video, the pictures, the operation, the subject, the size and length) are never offered as fields.
export const SELECTION_FIELDS = ['mask_margin', 'hat_fix', 'object_margin', 'biggest_only', 'keep_outline'];
export const BECOMES_FIELDS = ['change_to'];
/** inputs the Studio fills from its own controls */
export const OWNED_FIELDS = ['operation', 'video', 'refs', 'prompt', 'roles', 'target', 'object_target', 'resolution', 'short_edge', 'duration', 'start', 'accept_experimental',
  'subject_box', 'subject_points', 'subject_kind'];

type Profile = { inputs_schema?: Record<string, any>; ready?: boolean; reason?: string; [k: string]: any } | null;

/** Plainer wording for the engine's technical labels (the engine's own label is used for everything not listed here). */
const FRIENDLY: Record<string, string> = {
  mask_margin: 'How far the edit area grows around the subject (pixels)',
  motion_reference: 'Size of the motion reference (pixels)',
  clip_seconds: 'Length of each clip (seconds)',
  per_person_pass: 'One pass for each person (takes about twice as long)',
  no_lip_sync: 'Do not match lip movements (for songs the people do not sing along to)',
  biggest_only: 'Only the biggest match of each thing',
  keep_outline: 'Keep the old outline',
  object_margin: 'Margin of the edit area',
  placement: 'Where to add the object (advanced)',
  paint: 'Extra area to edit, painted (advanced)'
};

/** A copy of the profile whose schema holds only the wanted fields, so the existing settings form can draw them unchanged. */
export function pick(profile: Profile, opts: { only?: string[]; skip?: string[] }): Profile {
  if (!profile?.inputs_schema) return null;
  const skip = new Set(opts.skip ?? []);
  const entries = Object.entries(profile.inputs_schema).filter(([k, v]) => v.type !== 'file' && !skip.has(k) && (!opts.only || opts.only.includes(k)));
  return entries.length ? { ...profile, inputs_schema: Object.fromEntries(entries.map(([k, v]) => [k, FRIENDLY[k] ? { ...v, label: FRIENDLY[k], description: v.description ?? v.label } : v])) } : null;
}

/** fields this operation really uses (the engine says which operations use each input) */
export const usedBy = (profile: Profile, operation: string | undefined, names: string[]) =>
  names.filter((n) => { const sp = profile?.inputs_schema?.[n]; return sp && (!operation || !sp.operations || sp.operations.includes(operation)); });
