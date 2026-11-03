// Shared Work Mode state: the options the person may choose from, and the list of their sessions.
import { workOptions, listSessions, problem, type Problem } from './client';
import type { WorkOptions, WorkSession } from './types';

export const work = $state({
  options: null as WorkOptions | null,
  optionsError: null as Problem | null,
  sessions: [] as WorkSession[],
  sessionsLoaded: false,
  sessionsError: null as Problem | null
});

export async function loadOptions(force = false) {
  if (work.options && !force) return;
  try { work.options = await workOptions(); work.optionsError = null; }
  catch (e) { work.optionsError = problem(e); }
}

export async function loadSessions() {
  try {
    const rows = await listSessions();
    work.sessions = rows.sort((a, b) => Date.parse(b.last_active_at) - Date.parse(a.last_active_at));
    work.sessionsError = null;
  } catch (e) { work.sessionsError = problem(e); }
  finally { work.sessionsLoaded = true; }
}

export function upsertSession(s: WorkSession) {
  const i = work.sessions.findIndex((x) => x.id === s.id);
  if (i >= 0) work.sessions[i] = { ...work.sessions[i], ...s }; else work.sessions.unshift(s);
}
export const removeSession = (id: string) => { work.sessions = work.sessions.filter((s) => s.id !== id); };
export function patchRow(id: string, p: Partial<WorkSession>) {
  const i = work.sessions.findIndex((x) => x.id === id);
  if (i >= 0) work.sessions[i] = { ...work.sessions[i], ...p };
}
