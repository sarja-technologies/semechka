// Thin typed wrappers over /api/work. Every call goes through the shared api() helper, so cookies, CSRF and the
// ApiError shape are the same as everywhere else.
import { api, get, post, put, patch, del, qs, sendForm, ApiError } from '../api';
import type { WorkOptions, WorkSession, WorkState, EventsPage, FileListing, WorkArtifact, WorkSkill, SkillInput } from './types';

const B = '/api/work';
const S = (id: string) => `${B}/sessions/${encodeURIComponent(id)}`;

/** Accept either a bare array or {key: [...]}: the list routes are specified as arrays but this keeps the UI working if wrapped. */
export const asList = <T>(x: any, key: string): T[] => (Array.isArray(x) ? x : Array.isArray(x?.[key]) ? x[key] : []);

export const workOptions = () => get<WorkOptions>(`${B}/options`);
export const listSessions = async () => asList<WorkSession>(await get(`${B}/sessions`), 'sessions');
export const createSession = (b: { title?: string; objective?: string; model_id?: string; skill_ids?: string[]; network?: boolean | 'none' | 'internet'; autonomy?: string }) => post<WorkSession>(`${B}/sessions`, b);
export const getSession = (id: string) => get<WorkSession>(S(id));
export const patchSession = (id: string, b: Partial<Pick<WorkSession, 'title' | 'objective'>>) => patch<WorkSession>(S(id), b);
export const deleteSession = (id: string) => del(S(id));
export const sendMessage = (id: string, content: string) => post(`${S(id)}/messages`, { content });
export const control = (id: string, action: 'pause' | 'resume' | 'cancel') => post(`${S(id)}/${action}`);
export const getState = (id: string) => get<WorkState>(`${S(id)}/state`);
export const pollEvents = (id: string, since: number, wait: number, signal?: AbortSignal) =>
  api<EventsPage | WorkEventsArray>('GET', `${S(id)}/events${qs({ since, wait })}`, undefined, signal);
type WorkEventsArray = EventsPage['events'];
export const answerApproval = (id: string, step: string | number, approve: boolean) => post(`${S(id)}/approvals/${encodeURIComponent(String(step))}`, { approve });

export const listFiles = (id: string, path = '') => get<FileListing>(`${S(id)}/files${qs({ path })}`);
export const fileUrl = (id: string, path: string, download = false) => `${S(id)}/files/content${qs({ path, download: download ? 1 : undefined })}`;
export const uploadFile = (id: string, dir: string, file: File) => { const fd = new FormData(); fd.append('file', file, file.name); return sendForm('PUT', `${S(id)}/files${qs({ path: dir })}`, fd); };
export const deleteFile = (id: string, path: string) => del(`${S(id)}/files${qs({ path })}`);
export const readTextFile = async (id: string, path: string, maxBytes: number): Promise<{ text: string; cut: boolean }> => {
  let r: Response;
  try { r = await fetch(fileUrl(id, path), { credentials: 'same-origin' }); }
  catch { throw new ApiError(0, { code: 'offline', message: 'Cannot reach the server. Check your connection.' }); }
  if (!r.ok) { let m = r.statusText; try { const j = await r.json(); m = (typeof j.detail === 'string' ? j.detail : j.detail?.message) || m; } catch { /* not JSON */ } throw new ApiError(r.status, { message: m }); }
  const buf = await r.arrayBuffer();
  const cut = buf.byteLength > maxBytes;
  return { text: new TextDecoder().decode(cut ? buf.slice(0, maxBytes) : buf), cut };
};

export const listArtifacts = async (id: string) => asList<WorkArtifact>(await get(`${S(id)}/artifacts`), 'artifacts');
export const artifactUrl = (aid: string, download = false) => `${B}/artifacts/${encodeURIComponent(aid)}/content${download ? '?download=1' : ''}`;
export const saveToLibrary = (aid: string) => post(`${B}/artifacts/${encodeURIComponent(aid)}/save-to-library`);

export const listSkills = async () => asList<WorkSkill>(await get(`${B}/skills`), 'skills');
export const getSkill = (id: string) => get<WorkSkill>(`${B}/skills/${encodeURIComponent(id)}`);
export const createSkill = (b: SkillInput) => post<WorkSkill>(`${B}/skills`, b);
export const updateSkill = (id: string, b: SkillInput) => put<WorkSkill>(`${B}/skills/${encodeURIComponent(id)}`, b);
export const deleteSkill = (id: string) => del(`${B}/skills/${encodeURIComponent(id)}`);
export const setSkillEnabled = (id: string, on: boolean) => post<WorkSkill>(`${B}/skills/${encodeURIComponent(id)}/${on ? 'enable' : 'disable'}`);
export const importSkill = (file: File, scope: 'org' | 'user') => { const fd = new FormData(); fd.append('file', file, file.name); fd.append('scope', scope); return sendForm<WorkSkill>('POST', `${B}/skills/import`, fd); };

export type Problem = { kind: 'offline' | 'denied' | 'notfound' | 'limit' | 'conflict' | 'error'; message: string };
/** Sort an error into what the screen should do about it. The server's own wording is always shown as written. */
export function problem(e: unknown): Problem {
  if (e instanceof ApiError) {
    const m = e.message;
    if (e.status === 0) return { kind: 'offline', message: m };
    if (e.status === 401) return { kind: 'denied', message: 'Your sign-in has ended. Sign in again to continue.' };
    if (e.status === 403) return { kind: 'denied', message: m && !/^Request failed/.test(m) ? m : 'You do not have permission to do this.' };
    if (e.status === 404) return { kind: 'notfound', message: m && !/^Request failed|Not Found/.test(m) ? m : 'That was not found. It may have been deleted.' };
    if (e.status === 409) return { kind: 'conflict', message: m };
    if (e.status === 413 || e.status === 422 || e.status === 429) return { kind: 'limit', message: m };
    return { kind: 'error', message: m };
  }
  return { kind: 'error', message: 'Something unexpected happened. Try again.' };
}
