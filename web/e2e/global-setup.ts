import { request, type APIRequestContext } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { DIRS, URLS, USERS } from './support/env';

/** A session-aware API client: keeps the cookie and adds the CSRF header like the web app does. */
export async function apiClient(base: string, headers: Record<string, string> = {}) {
  const ctx = await request.newContext({ baseURL: base, extraHTTPHeaders: headers });
  const c = { ctx, csrf: '' };
  const call = async (method: string, url: string, data?: unknown) => {
    const r = await ctx.fetch(url, { method, data, headers: method === 'GET' || !c.csrf ? {} : { 'x-csrf-token': c.csrf } });
    return r;
  };
  return {
    ctx,
    get: (u: string) => call('GET', u),
    post: (u: string, d: unknown = {}) => call('POST', u, d),
    put: (u: string, d: unknown) => call('PUT', u, d),
    patch: (u: string, d: unknown) => call('PATCH', u, d),
    refreshCsrf: async () => { c.csrf = (await (await call('GET', '/api/me')).json()).csrf; },
    login: async (email: string, password: string) => { const r = await call('POST', '/api/auth/login', { email, password }); if (r.ok()) c.csrf = (await (await call('GET', '/api/me')).json()).csrf; return r; }
  };
}
export type Api = Awaited<ReturnType<typeof apiClient>>;

async function must(r: Awaited<ReturnType<APIRequestContext['fetch']>>, what: string) {
  if (!r.ok()) throw new Error(`${what} failed: ${r.status()} ${await r.text()}`);
  return r;
}

export default async function globalSetup() {
  const token = readFileSync(path.join(DIRS.seeded, 'setup-token'), 'utf8').trim();
  const owner = await apiClient(URLS.seeded, { 'x-setup-token': token });
  await must(await owner.post('/api/setup/complete', {
    owner: { email: USERS.owner.email, password: USERS.owner.password, display_name: USERS.owner.name }, preset: 'business',
    settings: { 'brand.org_name': 'Banana Corporation', 'brand.app_name': 'Banana Corporation Private AI', 'brand.short_name': 'Banana AI',
      'brand.tab_title': 'Banana AI', 'signup.mode': 'admin', 'privacy.chat_retention_days': 30, 'privacy.ephemeral_allowed': true,
      'auth.mfa_policy': 'optional' }, // the Business preset requires it for administrators; MFA has its own tests
    backend: { adapter: 'openai_compat', base_url: URLS.fake, external: false }, enable_models: ['fake-small', 'fake-other'], smoke_model: 'fake-small'
  }), 'setup');
  await owner.refreshCsrf();
  await owner.ctx.storageState({ path: path.join(DIRS.auth, 'owner.json') });

  const roles = await (await owner.get('/api/admin/roles')).json();
  const member = roles.find((r: any) => r.id === 'member');
  await must(await owner.put('/api/admin/roles/member', { label: member.label, description: member.description, rank: member.rank, grants: [...new Set([...member.grants, 'chat.ephemeral'])], denies: member.denies }), 'member role');

  // friendly names, as an administrator would set them: the real ids must never reach ordinary members
  const models = await (await owner.get('/api/admin/models')).json();
  for (const m of models.items) await must(await owner.patch(`/api/admin/models/${m.id}`, { alias: m.real_id === 'fake-small' ? 'Small Assistant' : 'Second Assistant' }), 'alias');

  const mk = async (u: { email: string; name: string; password: string }, role: string) => {
    const r = await must(await owner.post('/api/admin/users', { email: u.email, display_name: u.name, password: u.password, role_id: role, team_ids: [] }), `create ${u.email}`);
    return (await r.json()).id as string;
  };
  const techId = await mk(USERS.tech, 'member');
  await mk(USERS.member, 'member');
  await mk(USERS.admin, 'platform_admin');
  await mk(USERS.guest, 'guest');
  const tp = await (await owner.get('/api/admin/tech-presets')).json();
  await must(await owner.put(`/api/admin/users/${techId}/override`, { grants: tp.presets.technical, denies: [] }), 'tech override');

  for (const [name, u] of Object.entries(USERS)) {
    if (name === 'owner') continue;
    const a = await apiClient(URLS.seeded);
    await must(await a.login(u.email, u.password), `login ${name}`);
    await a.ctx.storageState({ path: path.join(DIRS.auth, `${name}.json`) });
    await a.ctx.dispose();
  }
  await owner.ctx.dispose();
}
