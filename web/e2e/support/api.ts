import { request } from '@playwright/test';
import path from 'node:path';
import { DIRS, URLS, USERS } from './env';

/** Direct API client for set-up and assertions, signed in with a saved session (no extra login attempts). */
export async function apiAs(role: keyof typeof USERS, base = URLS.seeded) {
  const ctx = await request.newContext({ baseURL: base, storageState: path.join(DIRS.auth, `${role}.json`) });
  const csrf = (await (await ctx.get('/api/me')).json()).csrf as string;
  const send = (method: string) => (url: string, data?: unknown) => ctx.fetch(url, { method, data: data ?? {}, headers: { 'x-csrf-token': csrf } });
  return { ctx, csrf, get: (u: string) => ctx.get(u), post: send('POST'), put: send('PUT'), patch: send('PATCH'), del: send('DELETE'), dispose: () => ctx.dispose() };
}

/** Set a deployment setting, returning its previous value so a test can put it back. */
export async function setSetting(key: string, value: unknown) {
  const a = await apiAs('owner');
  const list = await (await a.get(`/api/admin/settings?q=${encodeURIComponent(key)}`)).json();
  const before = list.settings.find((s: any) => s.id === key)?.value;
  const r = await a.put(`/api/admin/settings/${key}`, { value, confirm_weaker: true });
  if (!r.ok()) throw new Error(`setting ${key}: ${r.status()} ${await r.text()}`);
  await a.dispose();
  return before;
}
