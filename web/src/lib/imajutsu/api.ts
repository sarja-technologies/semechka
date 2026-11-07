// Calls to /api/imajutsu (docs/IMAJUTSU.md). Thin: no business rules live here. The job inspector uses the /api/studio/jobs/{id}/... routes, which
// the server makes work for image jobs too.
import { apiHeaders, del, get, post, put } from '$lib/api';
import type { Asset } from '$lib/media/assets';
import type { Caps, JobRow, Plan, Project, ProjectSummary, Recipe, Spec } from './types.ts';

export const loadCaps = (): Promise<Caps> => get('/api/imajutsu/capabilities').then((c) => ({ modes: [], presets: [], aspects: [], limits: {}, ...c }) as Caps);
export const createProject = (body: { mode?: string; source_asset?: string; title?: string }): Promise<Project> => post('/api/imajutsu/projects', body);
export const listProjects = (): Promise<ProjectSummary[]> => get('/api/imajutsu/projects');
export const getProject = (id: string): Promise<Project> => get(`/api/imajutsu/projects/${id}`);
export const saveProject = (id: string, spec: Spec, expected_updated_at?: number | string): Promise<Project> =>
  put(`/api/imajutsu/projects/${id}`, { spec, ...(expected_updated_at !== undefined ? { expected_updated_at } : {}) });
/** Best-effort save while the page is going away: the browser finishes the request after the page is gone. */
export function saveOnExit(id: string, spec: Spec, expected_updated_at?: number | string) {
  try {
    fetch(`/api/imajutsu/projects/${id}`, { method: 'PUT', credentials: 'same-origin', keepalive: true, headers: { ...apiHeaders('PUT'), 'content-type': 'application/json' },
      body: JSON.stringify({ spec, ...(expected_updated_at !== undefined ? { expected_updated_at } : {}) }) }).catch(() => {});
  } catch { /* nothing more can be done */ }
}
export const deleteProject = (id: string) => del(`/api/imajutsu/projects/${id}`);
export const planProject = (id: string, spec: Spec): Promise<Plan> => post(`/api/imajutsu/projects/${id}/plan`, { spec }).then((r) => (r?.plan ?? r) as Plan);
export type Generated = { job: string; jobs?: string[]; accounting?: any; plan?: any; batch?: { id: string; count: number; seeds: number[] } };
export const generate = (id: string, spec: Spec, count: number): Promise<Generated> => post(`/api/imajutsu/projects/${id}/generate`, { spec, count });
export const listJobs = (): Promise<JobRow[]> => get('/api/imajutsu/jobs');
export const reopenJob = (job: string): Promise<{ project: Project; dropped: string[] }> => post(`/api/imajutsu/jobs/${job}/reopen`);
export const keepOutside = (asset: string, feather?: number): Promise<{ task: string }> => post(`/api/imajutsu/assets/${asset}/keep-outside`, feather != null ? { feather } : {});
export const inspectJob = (job: string): Promise<any> => get(`/api/studio/jobs/${job}/inspect?after=2147483647`);
export const mediaJob = (job: string): Promise<any> => get(`/api/media/jobs/${job}`);
export const cancelJob = (job: string) => post(`/api/media/jobs/${job}/cancel`);
export const adjustApply = (asset: string, recipe: Recipe): Promise<{ task: string }> => post(`/api/imajutsu/assets/${asset}/adjust`, { recipe });
export const upscale = (asset: string, width: number, height: number): Promise<{ task: string; [k: string]: any }> => post(`/api/imajutsu/assets/${asset}/upscale`, { width, height });

/** Poll one media edit task (adjust and upscale are ordinary edits) until it ends. */
export async function followTask(task: string, onprogress?: (p: number) => void, isStopped: () => boolean = () => false): Promise<Asset> {
  for (;;) {
    if (isStopped()) throw new Error('Cancelled.');
    await new Promise((r) => setTimeout(r, 400));
    const s = await get(`/api/media/edits/${task}`);
    onprogress?.(s.progress ?? 0);
    if (s.status === 'completed') return s.asset as Asset;
    if (s.status === 'failed') throw new Error(s.error || 'The picture could not be made.');
    if (s.status === 'cancelled') throw new Error('Cancelled.');
  }
}

/** A job row's results as asset ids. */
export const outputIds = (j: { outputs?: any[] }): string[] => (j.outputs ?? []).map((o) => (typeof o === 'string' ? o : o?.id ?? o?.asset)).filter(Boolean);
