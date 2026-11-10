// Pure logic and the few server calls behind the Images and Videos screens (and the advanced versions of both under Experimental).
// Everything shown comes from what the server published for the chosen model (/api/media/options); nothing is guessed from a model name.
import { get, post, patch } from '$lib/api';
import type { DurationSpec } from './duration';

export type Kind = 'image' | 'video';

/** One model as /api/media/options describes it. Only the fields these screens read. */
export type MediaModel = {
  id: string; alias: string; modes?: string[]; references?: number; negative_prompt?: boolean; audio?: boolean;
  limits?: { resolutions?: number[][]; min_side?: number; max_side?: number; multiple?: number; steps?: number[]; [k: string]: any };
  defaults?: { width?: number; height?: number; steps?: number; seconds?: number; [k: string]: any };
  duration?: DurationSpec & { default: number };
  profile?: any; description?: string | null; loaded?: boolean | null;
};

export type Choice = { v: string; label: string };

/** Shapes offered for a picture model (a side range and a multiple), or the listed resolutions of a video model. */
export function sizeChoices(kind: Kind, m: MediaModel | undefined): Choice[] {
  if (!m) return [];
  const lim = m.limits ?? {};
  if (kind === 'video') return (lim.resolutions ?? []).map(([w, h]) => ({ v: `${w}x${h}`, label: `${w} × ${h}` }));
  const mult = lim.multiple || 8, lo = lim.min_side || 256, hi = lim.max_side || 1024, s0 = m.defaults?.width || 512;
  const fit = (n: number) => Math.min(hi, Math.max(lo, Math.round(n / mult) * mult));
  const shapes: [string, number, number][] = [['Square', 1, 1], ['Landscape', 4, 3], ['Portrait', 3, 4], ['Wide', 16, 9], ['Tall', 9, 16]];
  const out: Choice[] = [];
  for (const [name, a, b] of shapes) {
    const long = fit(s0 * Math.sqrt(Math.max(a, b) / Math.min(a, b))), short = fit((long * Math.min(a, b)) / Math.max(a, b));
    const [w, h] = a >= b ? [long, short] : [short, long];
    if (!out.some((o) => o.v === `${w}x${h}`)) out.push({ v: `${w}x${h}`, label: `${name} (${w} × ${h})` });
  }
  return out;
}

/** What the chosen model needs and accepts as a starting picture. */
export function referenceRules(kind: Kind, m: MediaModel | undefined) {
  const modes = m?.modes ?? [];
  const known = modes.length > 0;
  const textOnly = kind === 'image' ? 'txt2img' : ['t2v', 'ti2v'];
  const canText = !known || (kind === 'image' ? modes.includes('txt2img') : modes.some((x) => x === 't2v' || x === 'ti2v'));
  const canRef = !known || (kind === 'image' ? modes.some((x) => x === 'edit' || x === 'img2img') : modes.some((x) => x === 'i2v' || x === 'ti2v'));
  void textOnly;
  return { needs: known && !canText, accepts: canRef, known };
}

/** Why Generate is not available, in a sentence, or '' when it is. */
export function blockReason(a: { kind: Kind; model?: MediaModel; prompt: string; hasRef: boolean; refBusy: boolean; secondsOver: boolean; noDuration: boolean; tooLong: boolean }): string {
  const name = a.model?.alias ?? 'This model';
  const r = referenceRules(a.kind, a.model);
  if (!a.model) return 'Choose a model.';
  if (!a.prompt.trim()) return a.kind === 'image' ? 'Describe the picture to make.' : 'Describe the video to make.';
  if (a.tooLong) return 'The description is longer than the server accepts.';
  if (a.refBusy) return 'Wait for the reference to finish uploading.';
  if (r.needs && !a.hasRef) return `${name} starts from a picture. Add a reference image.`;
  if (a.hasRef && !r.accepts) return a.kind === 'image' ? `${name} makes pictures from words only; it cannot change a picture. Remove the reference or choose another model.`
    : `${name} cannot animate a picture. Remove the reference or choose another model.`;
  if (a.kind === 'video' && a.noDuration) return `${name} cannot make a clip within your limits.`;
  if (a.secondsOver) return 'That length is longer than your organisation allows.';
  return '';
}

/** The media settings of a request (the same keys the chat composer sends). Frame counts and frame rates are never asked of the person here. */
export function mediaParams(a: { kind: Kind; size: string; seed: string; negative: string; steps: string; seconds: number | null; fps: number | null; model?: MediaModel; editParams: boolean }): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (a.size) { const [w, h] = a.size.split('x').map(Number); if (w > 0 && h > 0) { out.width = w; out.height = h; } }
  if (a.seed.trim() !== '' && Number.isInteger(Number(a.seed)) && Number(a.seed) >= 0) out.seed = Number(a.seed);
  if (a.negative.trim() && a.model?.negative_prompt) out.negative_prompt = a.negative.trim();
  if (a.steps && a.editParams && Number(a.steps) > 0) out.steps = Number(a.steps);
  if (a.kind === 'video' && a.seconds) { out.seconds = a.seconds; if (a.fps && a.model?.duration?.fps_configurable) out.fps = a.fps; }
  return out;
}

// ---- generation ---------------------------------------------------------------------------------------------------------
/** Start a picture or video made from words alone: a standalone job saved to the Library. Returns the job exactly as the server describes it. */
export function startFromWords(kind: Kind, modelId: string, prompt: string, media: Record<string, unknown>): Promise<any> {
  return post('/api/media/jobs', { kind, model_id: modelId, prompt: prompt.trim(), save: true, ...media });
}

const key = (uid: string, kind: Kind) => `sem.studio.conv.${uid}.${kind}`;

/** A request that starts from a picture goes through a conversation (the only place the server takes a source picture). The conversation is the
 *  screen's own: created once per person and kind, titled for what it holds, and archived at once so Chat history stays about chats. */
async function studioConversation(uid: string, kind: Kind, modelId: string): Promise<string> {
  let id = '';
  try { id = localStorage.getItem(key(uid, kind)) ?? ''; } catch { /* storage unavailable */ }
  if (id) {
    try { await get(`/api/chat/conversations/${id}`); return id; } catch { id = ''; }
  }
  const c = await post('/api/chat/conversations', { model_id: modelId || null, mode: 'chat' });
  await patch(`/api/chat/conversations/${c.id}`, { title: kind === 'image' ? 'Images (made from a reference)' : 'Videos (made from a reference)', archived: true });
  try { localStorage.setItem(key(uid, kind), c.id); } catch { /* storage unavailable */ }
  return c.id as string;
}

/** Start a picture or video from a reference picture in the Library. Returns the job. */
export async function startFromReference(uid: string, kind: Kind, modelId: string, prompt: string, assetId: string, media: Record<string, unknown>): Promise<any> {
  const cid = await studioConversation(uid, kind, modelId);
  const r = await post(`/api/chat/conversations/${cid}/generate`, { mode: 'send', operation: kind, content: prompt.trim(), model_id: modelId, attachments: [assetId], source_id: assetId, media, parent_id: null });
  const job = r?.message?.media;
  if (!job?.id) throw new Error('The server accepted the request but did not return a job to follow.');
  return job;
}

export const recentJobs = (kind: Kind): Promise<any[]> => get(`/api/media/jobs?kind=${kind}`);
