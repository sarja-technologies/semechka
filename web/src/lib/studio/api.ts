// Calls to /api/studio (docs/OPENJUTSU_STUDIO.md section 3). Thin: no business rules live here.
import { ApiError, apiHeaders, del, get, post, put } from '$lib/api';
import type { Asset } from '$lib/media/assets';
import type { Caps, Inspect, JobRow, Plan, Project, ProjectSummary, Spec } from './types';

/** A mode nobody can run is shown unavailable, whatever it claims: "ready" needs a model that supports it. */
export const loadCaps = (): Promise<Caps> => get('/api/studio/capabilities').then((c) => {
  const caps: Caps = { modes: [], models: [], limits: {}, ...c };
  caps.modes = caps.modes.map((m) => {
    const models = m.models ?? [];
    const can = models.some((x) => x.status === 'ready' || x.status === 'experimental');
    return can || m.status === 'unavailable' ? { ...m, models } : { ...m, models, status: 'unavailable' as const, reason: m.reason || 'No installed model can do this yet.' };
  });
  return caps;
});
export const createProject = (source_asset: string, title?: string): Promise<Project> => post('/api/studio/projects', { source_asset, ...(title ? { title } : {}) });
export const listProjects = (): Promise<ProjectSummary[]> => get('/api/studio/projects');
export const getProject = (id: string): Promise<Project> => get(`/api/studio/projects/${id}`);
export const saveProject = (id: string, spec: Spec, expected_updated_at?: number | string): Promise<Project> =>
  put(`/api/studio/projects/${id}`, { spec, ...(expected_updated_at !== undefined ? { expected_updated_at } : {}) });
/** Best-effort save while the page is going away (closing the tab, navigating off): the browser finishes the request after the page is gone. */
export function saveOnExit(id: string, spec: Spec, expected_updated_at?: number | string) {
  try {
    fetch(`/api/studio/projects/${id}`, { method: 'PUT', credentials: 'same-origin', keepalive: true, headers: { ...apiHeaders('PUT'), 'content-type': 'application/json' },
      body: JSON.stringify({ spec, ...(expected_updated_at !== undefined ? { expected_updated_at } : {}) }) }).catch(() => {});
  } catch { /* nothing more can be done */ }
}
export const deleteProject = (id: string) => del(`/api/studio/projects/${id}`);
export const planProject = (id: string, spec: Spec, signal?: AbortSignal): Promise<Plan> =>
  post(`/api/studio/projects/${id}/plan`, { spec }).then((r) => (r?.plan ?? r) as Plan);
export const listJobs = (): Promise<JobRow[]> => get('/api/studio/jobs');
export const inspectJob = (job: string): Promise<Inspect> => get(`/api/studio/jobs/${job}/inspect?after=2147483647`);
export const mediaJob = (job: string): Promise<any> => get(`/api/media/jobs/${job}`);
export const cancelJob = (job: string) => post(`/api/media/jobs/${job}/cancel`);
export const mediaOptions = (): Promise<any> => get('/api/media/options?kind=video');

export type Generated = { job: string; prepared_asset?: any; accounting?: any };
/** Starts the job. While the server is still preparing the video it answers 202 {code:'preparing'}; ask again until it has a job. */
export async function generate(id: string, spec: Spec, extra: Record<string, unknown> = {}): Promise<Generated> {
  for (let i = 0; i < 80; i++) {
    const r = await post(`/api/studio/projects/${id}/generate`, { spec, accept_experimental: spec.accept_experimental, ...extra });
    if (r?.job) return r as Generated;
    if (r?.code !== 'preparing') throw new ApiError(0, { code: 'generate_failed', message: r?.message || 'The server did not start the job.' });
    await new Promise((res) => setTimeout(res, 1500));
  }
  throw new ApiError(0, { code: 'preparing', message: 'The video is taking a long time to prepare. Try Generate again in a moment.' });
}
export const exportJob = (job: string, width: number, height: number, fit: 'scale' | 'pad' = 'scale'): Promise<{ task: string }> =>
  post(`/api/studio/jobs/${job}/export`, { width, height, fit });

export type PrepHandle = { done: Promise<{ asset: Asset; reused: boolean }>; cancel: () => void };

/** Poll one media edit task (prepare and export are both ordinary edits) until it ends. */
export function followTask(task: string, onprogress?: (p: number) => void, isStopped: () => boolean = () => false): Promise<Asset> {
  return (async () => {
    for (;;) {
      if (isStopped()) throw new ApiError(0, { code: 'cancelled', message: 'Cancelled.' });
      await new Promise((r) => setTimeout(r, 450));
      const s = await get(`/api/media/edits/${task}`);
      onprogress?.(s.progress ?? 0);
      if (s.status === 'completed') return s.asset as Asset;
      if (s.status === 'failed') throw new ApiError(422, { code: 'edit_failed', message: s.error || 'The video could not be prepared.' });
      if (s.status === 'cancelled') throw new ApiError(0, { code: 'cancelled', message: 'Cancelled.' });
    }
  })();
}

/** Start preparing the video (or reuse a copy that already matches) and follow the work. Progress is the server's own. */
export function prepare(id: string, spec: Spec, onprogress?: (p: number) => void): PrepHandle {
  let stop = false, task = '';
  const done = (async () => {
    const r = await post(`/api/studio/projects/${id}/prepare`, { spec });
    task = r.task ?? '';
    if (r.reused && r.asset) return { asset: r.asset as Asset, reused: true };
    if (!task) throw new ApiError(0, { code: 'prepare_failed', message: 'The server did not start preparing the video.' });
    const asset = await followTask(task, onprogress, () => stop);
    return { asset, reused: !!r.reused };
  })();
  return { done, cancel: () => { stop = true; if (task) post(`/api/media/edits/${task}/cancel`).catch(() => {}); } };
}

/** A job row's results as asset ids, whatever shape the server lists them in. */
export const outputIds = (j: { outputs?: any[] }): string[] => (j.outputs ?? []).map((o) => (typeof o === 'string' ? o : o?.id ?? o?.asset)).filter(Boolean);
