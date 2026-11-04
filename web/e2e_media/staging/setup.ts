import { request } from '@playwright/test';
import path from 'node:path';
import { STG } from '../../playwright.staging.config';

export default async function setup() {
  const ctx = await request.newContext({ baseURL: STG.url });
  const r = await ctx.post('/api/auth/login', { data: { email: process.env.STAGING_EMAIL, password: process.env.STAGING_PW } });
  if (!r.ok()) throw new Error(`staging login failed: ${r.status()}`);
  await ctx.storageState({ path: path.join(process.env.STAGING_OUT ?? '.', 'staging-owner.json') });
  await ctx.dispose();
}
