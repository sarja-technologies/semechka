import { request } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { MP, TMP } from '../playwright.media.config';

export const OWNER = { email: 'owner@media.test', password: 'correct horse battery staple', name: 'Olga Owner' };

export default async function setup() {
  const token = readFileSync(path.join(TMP, 'data', 'setup-token'), 'utf8').trim();
  const ctx = await request.newContext({ baseURL: `http://127.0.0.1:${MP.app}`, extraHTTPHeaders: { 'x-setup-token': token } });
  const r = await ctx.post('/api/setup/complete', { data: {
    owner: { email: OWNER.email, password: OWNER.password, display_name: OWNER.name }, preset: 'business',
    settings: { 'modes.image.enabled': true, 'modes.video.enabled': true, 'privacy.chat_retention_days': 30, 'privacy.ephemeral_allowed': true, 'auth.mfa_policy': 'optional' },
    backend: { adapter: 'sarness', base_url: `http://127.0.0.1:${MP.engine}`, api_key: 'fake-sarness-token' },
    enable_models: ['local:fake-chat', 'image:fake-turbo', 'image:flux2-klein-4b', 'image:qwen-image-edit-2511', 'image:qwen-image-2512', 'image:z-image-turbo', 'image:qwen-image-2.1', 'image:sdxl-turbo', 'video:fake-clip', 'video:openjutsu-minimax-h3'], smoke_model: 'local:fake-chat' } });
  if (!r.ok()) throw new Error(`setup failed: ${r.status()} ${await r.text()}`);
  const me = await (await ctx.get('/api/me')).json();
  // One owner makes every picture of every spec in a combined run: the product's own per-person gallery limit (200 by default) is reached near the end and then
  // refuses new generations ('gallery_full'), which showed up as random failures in the LAST specs only. The suite does not test that limit, so lift it here.
  const q = await ctx.put('/api/admin/settings/media.max_assets', { data: { value: 0 }, headers: { 'x-csrf-token': me.csrf } });
  if (!q.ok()) throw new Error(`could not lift the gallery limit for the test instance: ${q.status()} ${await q.text()}`);
  await ctx.storageState({ path: path.join(TMP, 'auth', 'owner.json') });
  await ctx.dispose();
}
