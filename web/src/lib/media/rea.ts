// REA, the video reverse engineer: upload a video, get an analysis of what is visible in it (docs/MEDIA_ENGINE.md section 7, branch consol/media).
// It runs as an ordinary profile job (`reverse-video`, operation `analyse`, one video input) through /api/media/profile-jobs. The finished job delivers a
// contact sheet; the full analysis (shots, camera, light, colour, transitions, an editable plan) is read from the job's analysis route. That route is
// not in the server code this branch was cut from, so the screen asks for it and says plainly when the server does not have it.
import { ApiError, get, postForm } from '$lib/api';

export type ReaProfile = { modelId: string; alias: string; fileInput: string; operation: string | null; ready: boolean; reason: string | null; maxMb?: number };

/** The engine's own profile for analysing a video, from what /api/media/options published (never assumed to exist). */
export function findReaProfile(options: any): ReaProfile | null {
  for (const m of options?.models ?? []) {
    const p = m.profile;
    if (!p) continue;
    const schema: Record<string, any> = p.inputs_schema ?? {};
    const ops: string[] = schema.operation?.values ?? p.operations ?? [];
    const isRea = /reverse/i.test(`${p.id ?? ''} ${p.family ?? ''} ${p.name ?? ''} ${m.alias ?? ''}`) || ops.includes('analyse');
    if (!isRea) continue;
    const fileInput = Object.entries(schema).find(([, s]: [string, any]) => s?.type === 'file')?.[0] ?? 'video';
    return { modelId: m.id, alias: m.alias, fileInput, operation: ops.includes('analyse') ? 'analyse' : ops[0] ?? null, ready: p.ready !== false, reason: p.reason ?? null };
  }
  return null;
}

/** Start an analysis of a video that is in the Library. Returns the job exactly as the server describes it. */
export function startAnalysis(p: ReaProfile, assetId: string): Promise<any> {
  const fd = new FormData();
  fd.append('model_id', p.modelId); fd.append('save', 'true');
  fd.append('inputs', JSON.stringify({ ...(p.operation ? { operation: p.operation } : {}) }));
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
