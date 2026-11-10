// REA, the video reverse engineer: upload a video, get an analysis of what is visible in it (docs/MEDIA_ENGINE.md section 7, branch consol/media).
// It runs as an ordinary profile job (`reverse-video`, operation `analyse`, one video input) through /api/media/profile-jobs. The finished job delivers a
// contact sheet; the full analysis (shots, camera, light, colour, transitions, an editable plan) is read from the job's analysis route
// (GET /api/media/jobs/{id}/analysis) and its keyframes from /api/media/jobs/{id}/analysis/files/{name}. When the engine's vision step ran, every shot
// also carries a scene description (`shot.vision`) and the analysis a reconstructed prompt (`analysis.vision.clip`); `reaView` reads both defensively.
import { ApiError, get, postForm } from '$lib/api';

export type ReaProfile = { modelId: string; alias: string; fileInput: string; operation: string | null; ready: boolean; reason: string | null; maxMb?: number; vision: boolean };

/** The engine's own profile for analysing a video, from what /api/media/options published (never assumed to exist). */
export function findReaProfile(options: any): ReaProfile | null {
  for (const m of options?.models ?? []) {
    const p = m.profile;
    if (!p) continue;
    const schema: Record<string, any> = p.inputs_schema ?? {};
    const ops: string[] = schema.operation?.values ?? p.operations ?? [];
    const isRea = /reverse/i.test(`${p.id ?? ''} ${p.family ?? ''} ${p.name ?? ''} ${m.alias ?? ''}`) || ops.includes('analyse');
    if (!isRea) continue;
    // only a schema with an `operation` input takes one (reverse-video has none: sending it made Semechka refuse every analysis)
    const fileInput = Object.entries(schema).find(([, s]: [string, any]) => s?.type === 'file')?.[0] ?? 'video';
    return { modelId: m.id, alias: m.alias, fileInput, operation: schema.operation ? (ops.includes('analyse') ? 'analyse' : ops[0] ?? null) : null, ready: p.ready !== false, reason: p.reason ?? null,
      vision: Array.isArray(schema.vision?.values) && schema.vision.values.includes('off') };
  }
  return null;
}

/** Start an analysis of a video that is in the Library. Returns the job exactly as the server describes it. */
export function startAnalysis(p: ReaProfile, assetId: string, describe = true): Promise<any> {
  const fd = new FormData();
  fd.append('model_id', p.modelId); fd.append('save', 'true');
  fd.append('inputs', JSON.stringify({ ...(p.operation ? { operation: p.operation } : {}), ...(p.vision ? { vision: describe ? 'auto' : 'off' } : {}) }));
  fd.append(`asset:${p.fileInput}`, assetId);
  return postForm('/api/media/profile-jobs', fd);
}

export type Analysis = Record<string, unknown>;
/** The full analysis of a finished job, or null when this server does not serve one (404/501). */
export async function loadAnalysis(jobId: string): Promise<Analysis | null> {
  try { return await get(`/api/media/jobs/${encodeURIComponent(jobId)}/analysis`); }
  catch (e) { if (e instanceof ApiError && (e.status === 404 || e.status === 405 || e.status === 501)) return null; throw e; }
}

/** Turn any value of the analysis into rows to show, without knowing its field names: scalars as text, lists of objects as tables. */
export type Block = { title: string; kind: 'text' | 'pairs' | 'table' | 'list'; text?: string; pairs?: [string, string][]; columns?: string[]; rows?: string[][]; items?: string[] };
const label = (k: string) => k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
const show = (v: unknown): string => (v == null ? '' : typeof v === 'number' ? String(Math.round(v * 1000) / 1000) : typeof v === 'string' ? v : Array.isArray(v) ? v.map(show).join(', ') : typeof v === 'object' ? Object.entries(v as object).map(([k, x]) => `${label(k)}: ${show(x)}`).join('; ') : String(v));
export function blocksOf(a: Analysis): Block[] {
  const out: Block[] = [];
  for (const [k, v] of Object.entries(a)) {
    if (v == null || v === '') continue;
    const title = label(k);
    if (typeof v !== 'object') out.push({ title, kind: 'text', text: show(v) });
    else if (Array.isArray(v)) {
      if (v.length && v.every((x) => x && typeof x === 'object' && !Array.isArray(x))) {
        const cols = [...new Set(v.flatMap((x) => Object.keys(x as object)))].filter((c) => v.some((x: any) => typeof x[c] !== 'object' || x[c] === null));
        out.push({ title, kind: 'table', columns: cols.map(label), rows: v.map((x: any) => cols.map((c) => show(x[c]))) });
      } else out.push({ title, kind: 'list', items: v.map(show) });
    } else out.push({ title, kind: 'pairs', pairs: Object.entries(v as object).filter(([, x]) => x != null && x !== '').map(([kk, x]) => [label(kk), show(x)] as [string, string]) });
  }
  return out;
}

/** URL of a keyframe / contact sheet of the person's own finished analysis job (served by Semechka, proxied from the engine). */
export const reaFileUrl = (jobId: string, name: string) => `/api/media/jobs/${encodeURIComponent(jobId)}/analysis/files/${encodeURIComponent(name)}`;

export type ReaFrame = { name: string; t: number; key: string };
export type ReaShot = {
  index: number; start: number; end: number; duration: number; transition: string; frames: ReaFrame[];
  description: string; prompt: string; subjects: string[]; facts: [string, string][]; measured: [string, string][];
};
export type ReaVision = { used: boolean; model: string | null; described: number; total: number; framesSent: number; reason: string; note: string;
  loadedByUs: boolean; unloaded: boolean | null; elapsed: number | null };
export type ReaView = { duration: number; shots: ReaShot[]; vision: ReaVision; clip: { summary: string; style: string; prompt: string } | null; honesty: string };

const str = (v: unknown) => (typeof v === 'string' ? v.trim() : '');
const num = (v: unknown) => (typeof v === 'number' && isFinite(v) ? v : 0);
const val = (o: any, ...path: string[]) => { let x = o; for (const k of path) x = x?.[k]; return x && typeof x === 'object' && 'value' in x ? x.value : undefined; };

/** The parts of the engine's analysis document a person reads first: shots with keyframes, scene descriptions and prompts, and the vision note. */
export function reaView(doc: any): ReaView | null {
  const a = doc?.analysis ?? doc;
  if (!a || !Array.isArray(a.shots)) return null;
  const v = a.vision ?? {};
  const shots: ReaShot[] = a.shots.map((s: any, i: number) => {
    const sv = s.vision ?? {};
    const own: ReaFrame[] = Array.isArray(sv.frames) ? sv.frames.filter((f: any) => str(f?.file)).map((f: any) => ({ name: f.file, t: num(f.t), key: str(f.key) })) : [];
    const saved: ReaFrame[] = Object.entries(s.frames ?? {}).filter(([, f]: [string, any]) => str(f?.file)).map(([k, f]: [string, any]) => ({ name: f.file, t: num(f.t), key: k }));
    const frames = (own.length ? own : saved).slice().sort((x, y) => x.t - y.t).slice(0, 5);
    const cam = (val(s, 'camera', 'movements') as any[] | undefined)?.map((m) => str(m?.label) || str(m?.kind)).filter(Boolean).join(', ');
    const pal = (val(s, 'grade', 'palette') as any[] | undefined)?.slice(0, 5).map((p) => str(p?.hex)).filter(Boolean);
    const facts: [string, string][] = ([['Shot size', str(sv.shot_size)], ['Angle', str(sv.angle)], ['Light', str(sv.lighting)], ['Mood', str(sv.mood)],
      ['Setting', str(sv.setting)], ['Action', str(sv.action)], ['Changes', str(sv.changes)]] as [string, string][]).filter(([, x]) => x && x !== 'unclear');
    const measured: [string, string][] = ([['Camera (measured)', cam ?? ''], ['Light (measured)', str(val(s, 'lighting', 'key'))],
      ['Movement in the scene', str(val(s, 'subject', 'activity'))], ['Colours', pal?.join(' ') ?? '']] as [string, string][]).filter(([, x]) => x);
    return { index: num(s.index ?? i), start: num(s.start), end: num(s.end), duration: num(s.duration), transition: str(s.transition_in?.label), frames,
      description: str(sv.text), prompt: str(sv.prompt), subjects: Array.isArray(sv.subjects) ? sv.subjects.map(str).filter(Boolean) : [], facts, measured };
  });
  const clip = v.clip && str(v.clip.prompt) ? { summary: str(v.clip.summary), style: str(v.clip.style), prompt: str(v.clip.prompt) } : null;
  return { duration: num(a.source?.duration_s), shots, clip, honesty: str(a.honesty),
    vision: { used: !!(v.used ?? v.available) && num(v.described) > 0, model: str(v.model) || null, described: num(v.described), total: num(v.total) || shots.length,
      framesSent: num(v.frames_sent), reason: str(v.reason), note: str(v.note), loadedByUs: !!v.loaded_by_us, unloaded: typeof v.unloaded === 'boolean' ? v.unloaded : null,
      elapsed: typeof v.elapsed_s === 'number' ? v.elapsed_s : null } };
}

/** One plain sentence about the vision step: which model, how many keyframes, or why it did not run. */
export function visionLine(v: ReaVision): string {
  if (!v.used) return v.reason && v.reason !== 'No vision model was used.' ? v.reason : 'Scene descriptions were not made for this analysis (measurements only).';
  const parts = [`Vision used: ${v.model}, ${v.framesSent} keyframe${v.framesSent === 1 ? '' : 's'} from ${v.described} of ${v.total} shot${v.total === 1 ? '' : 's'}`];
  if (v.elapsed != null) parts.push(`${Math.round(v.elapsed)} s`);
  let s = parts.join(', ') + '.';
  if (v.loadedByUs) s += v.unloaded ? ' The model was loaded for this analysis and unloaded afterwards.' : ' The model was loaded for this analysis and is still loaded (someone else was using it).';
  if (v.reason && v.reason !== 'ok') s += ` ${v.reason}`;
  return s;
}
