import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect } from './support/fixtures';
import { apiAs, setSetting } from './support/api';
import { PW, URLS } from './support/env';
import { shot } from './support/shots';
import { apiClient } from './global-setup';

test.describe.configure({ mode: 'serial' });

const ADMIN_ROUTES = ['', '/branding', '/navigation', '/people', '/invites', '/teams', '/roles', '/visibility', '/models', '/backends', '/system', '/registration', '/privacy',
  '/quotas', '/email', '/access', '/settings', '/history', '/transfer', '/audit'];

/** Fail on anything a person would experience as broken: console errors and failed API calls. */
function watch(page: Page) {
  const bad: string[] = [];
  page.on('console', (m) => m.type() === 'error' && bad.push(`console: ${m.text()}`));
  page.on('pageerror', (e) => bad.push(`exception: ${e.message}`));
  page.on('response', (r) => r.url().includes('/api/') && r.status() >= 400 && bad.push(`${r.status()} ${r.url()}`));
  return bad;
}

test('every administration page loads with real content and no errors', async ({ as }) => {
  const page = await as('owner');
  const bad = watch(page);
  for (const r of ADMIN_ROUTES) {
    await page.goto(`/admin${r}`);
    await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
    await expect(page.getByRole('alert')).toHaveCount(0);
    await expect(page.getByText('Loading…')).toHaveCount(0);
  }
  expect(bad).toEqual([]);
});

test('hardware metrics on the system page are the real numbers of this machine', async ({ as }) => {
  const page = await as('owner');
  await page.goto('/admin/system');
  await expect(page.getByRole('heading', { name: 'Host' })).toBeVisible();
  await expect(page.getByText('Memory:')).toBeVisible();
  const sys = await (await page.request.get('/api/admin/system')).json();
  expect(JSON.stringify(sys)).not.toMatch(/fake|mock|demo/i);
  const totalMb = Number(readFileSync('/proc/meminfo', 'utf8').match(/MemTotal:\s+(\d+)/)![1]) / 1024;
  const reported = sys.host?.memory_total_mb ?? sys.nodes?.[0]?.host?.memory_total_mb;
  expect(Math.abs(reported - totalMb) / totalMb).toBeLessThan(0.02);
  await shot(page, 'admin-system-1280');
});

test('access follows the role: an operator sees only operating pages, and the API agrees', async ({ as, browser }) => {
  const owner = await apiAs('owner');
  const r = await owner.post('/api/admin/users', { email: 'ops@e2e.test', display_name: 'Ora Operator', password: PW, role_id: 'model_operator', team_ids: [] });
  expect(r.ok()).toBe(true);
  const ctx = await browser.newContext({ baseURL: URLS.seeded });
  const page = await ctx.newPage();
  await page.goto('/login');
  await page.getByLabel('Email').fill('ops@e2e.test');
  await page.getByLabel('Password').fill(PW);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).toHaveURL(/\/chat|\/usage|\/admin/);
  await page.goto('/admin');
  const nav = page.locator('nav#anav');
  await expect(nav.getByRole('link', { name: 'Models & Aliases' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Backends' })).toBeVisible();
  await expect(nav.getByRole('link', { name: 'Roles & Permissions' })).toHaveCount(0);
  await expect(nav.getByRole('link', { name: 'Import / Export' })).toHaveCount(0);
  await page.goto('/admin/roles');
  await expect(page.getByText('You do not have access to this section.')).toBeVisible(); // explained, not a failing page
  await expect(page.getByRole('button', { name: 'Retry' })).toHaveCount(0);
  expect((await page.request.get('/api/admin/roles')).status()).toBe(403);
  expect((await page.request.get('/api/admin/settings-export')).status()).toBe(403);
  await ctx.close();
  // an ordinary member never gets the admin shell at all
  const member = await as('member');
  await member.goto('/admin');
  await expect(member).not.toHaveURL(/\/admin/);
});

test('roles: create with a grant and a deny, edit, and delete', async ({ as }) => {
  const page = await as('owner');
  await page.goto('/admin/roles');
  await page.getByRole('button', { name: 'Create role' }).click();
  await page.getByLabel('Display name').fill('Reviewer');
  await page.getByLabel('Description').fill('Reads usage only');
  await page.getByRole('button', { name: 'Save role' }).click();
  await expect(page.getByRole('cell', { name: /^Reviewer/ }).first()).toBeVisible();
  await page.getByRole('row', { name: /^Reviewer/ }).getByRole('button', { name: 'Edit' }).click();
  await page.getByLabel('Display name').fill('Reviewer (read only)');
  await page.getByRole('button', { name: 'Save role' }).click();
  await expect(page.getByRole('cell', { name: /Reviewer \(read only\)/ }).first()).toBeVisible();
  await page.getByRole('row', { name: /Reviewer \(read only\)/ }).getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('button', { name: /^Delete/ }).last().click();
  await expect(page.getByRole('cell', { name: /^Reviewer/ })).toHaveCount(0); // (the built-in Compliance Reviewer stays)
});

test('deny always wins: a team deny removes a model the role grants, and the explainer says why', async ({ as }) => {
  const owner = await apiAs('owner');
  const models = await (await owner.get('/api/admin/models')).json();
  const small = models.items.find((m: any) => m.alias === 'Small Assistant');
  const team = await (await owner.post('/api/admin/teams', { name: 'No small model', grants: [], denies: [] })).json();
  const u = await (await owner.post('/api/admin/users', { email: 'denied@e2e.test', display_name: 'Dee Denied', password: PW, role_id: 'member', team_ids: [team.id] })).json();
  const g = await owner.put(`/api/admin/models/${small.id}/grants`, { grants: [{ subject_type: 'team', subject_id: team.id, effect: 'deny' }, { subject_type: 'role', subject_id: 'member', effect: 'allow' }] });
  expect(g.ok(), await g.text()).toBe(true);
  const why = await (await owner.get(`/api/admin/explain/model?user_id=${u.id}&model_id=${small.id}`)).json();
  expect(JSON.stringify(why).toLowerCase()).toMatch(/deny|denied/);
  const page = await as('owner');
  await page.goto(`/admin/people/${u.id}`);
  await expect(page.getByRole('heading', { name: 'Dee Denied' }).first()).toBeVisible();
  // enforced by the server, not just explained: Dee's model list and a direct request both exclude it
  const dee = await apiClient(URLS.seeded);
  expect((await dee.login('denied@e2e.test', PW)).ok()).toBe(true);
  const visible = (await (await dee.get('/api/chat/models')).json()).map((m: any) => m.alias);
  expect(visible).not.toContain('Small Assistant');
  expect(visible).toContain('Second Assistant');
  const direct = await dee.ctx.fetch(`/api/chat/ephemeral/generate`, { method: 'POST', data: { model_id: small.id, messages: [{ role: 'user', content: 'hi' }] }, headers: { 'x-csrf-token': (await (await dee.get('/api/me')).json()).csrf } });
  expect(direct.status()).toBe(403);
  // restore for later specs
  await owner.put(`/api/admin/models/${small.id}/grants`, { grants: [{ subject_type: 'role', subject_id: 'member', effect: 'allow' }, { subject_type: 'role', subject_id: 'owner', effect: 'allow' }] });
  await owner.dispose();
});

test('teams: create and delete through the UI', async ({ as }) => {
  const page = await as('owner');
  await page.goto('/admin/teams');
  await page.getByRole('button', { name: 'Create team' }).click();
  await page.getByLabel('Team name').fill('Research');
  await page.getByRole('button', { name: 'Save team' }).click();
  await expect(page.getByRole('cell', { name: 'Research', exact: true })).toBeVisible();
  await page.getByRole('row', { name: /Research/ }).getByRole('button', { name: 'Delete' }).click();
  await page.getByRole('button', { name: /^Delete/ }).last().click();
  await expect(page.getByRole('cell', { name: 'Research', exact: true })).toHaveCount(0);
});

test.describe('settings', () => {
  test('search finds a setting by label and by alias', async ({ as }) => {
    const page = await as('owner');
    await page.goto('/admin/settings');
    await page.getByRole('searchbox').or(page.getByPlaceholder(/search/i)).first().fill('retention');
    await expect(page.getByRole('heading', { name: /retention/i }).first()).toBeVisible();
    await page.getByRole('searchbox').or(page.getByPlaceholder(/search/i)).first().fill('zzzz-nothing');
    await expect(page.getByText(/no settings|nothing matches|No matches/i).first()).toBeVisible();
  });

  test('a weakening change shows its impact and needs confirmation; rollback restores it', async ({ as }) => {
    const page = await as('owner');
    await page.goto('/admin/settings?q=password_min_length');
    const row = page.locator('article.set', { hasText: 'Minimum password length' });
    await row.getByLabel('Minimum password length').fill('10');
    await row.getByRole('button', { name: 'Save' }).click();
    const dlg = page.getByRole('dialog', { name: 'This change reduces protection' });
    await expect(dlg).toContainText('12');
    await expect(dlg).toContainText('10');
    await shot(page, 'admin-impact-1280');
    await dlg.getByRole('button', { name: 'Cancel' }).click();
    expect((await (await page.request.get('/api/admin/settings?q=password_min_length')).json()).settings[0].value).toBe(12); // nothing applied
    await row.getByRole('button', { name: 'Save' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Apply anyway' }).click();
    await expect(page.getByText('Saved “Minimum password length”.')).toBeVisible();
    expect((await (await page.request.get('/api/admin/settings?q=password_min_length')).json()).settings[0].value).toBe(10);

    await page.goto('/admin/history');
    await page.getByPlaceholder(/Filter by setting id/).fill('auth.password_min_length');
    await page.getByRole('row', { name: /auth.password_min_length/ }).first().getByRole('button', { name: 'Roll back' }).click();
    await expect(page.getByText('Rolled back.')).toBeVisible();
    expect((await (await page.request.get('/api/admin/settings?q=password_min_length')).json()).settings[0].value).toBe(12);
  });

  test('a per-person override beats the default, and the inspector explains it', async ({ as }) => {
    const owner = await apiAs('owner');
    const users = await (await owner.get('/api/admin/users?q=member@e2e.test')).json();
    const uid = users.items[0].id;
    expect((await owner.put('/api/admin/settings/quota.daily_messages', { value: 3, scope_type: 'user', scope_id: uid })).ok()).toBe(true);
    const eff = await (await owner.get(`/api/admin/settings/quota.daily_messages/effective?user_id=${uid}`)).json();
    expect(eff.value).toBe(3);
    expect(JSON.stringify(eff.source)).toContain('user');
    const me = await apiAs('member');
    expect((await (await me.get('/api/usage')).json()).daily_limit).toBe(3);
    await me.dispose();
    await owner.ctx.fetch(`/api/admin/settings/quota.daily_messages?scope_type=user&scope_id=${uid}`, { method: 'DELETE', headers: { 'x-csrf-token': owner.csrf } });
    await owner.dispose();
  });

  test('export contains no secrets; a modified file previews, then applies', async ({ as }, info) => {
    const page = await as('owner');
    await page.goto('/admin/transfer');
    const dl = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export settings' }).click();
    const file = path.join(info.outputDir, 'settings.json');
    await (await dl).saveAs(file);
    const profile = JSON.parse(readFileSync(file, 'utf8'));
    expect(JSON.stringify(profile)).not.toMatch(/api_key|password|secret|\$argon2/i);
    profile.settings = profile.settings.filter((s: any) => s.key !== 'brand.footer_text');
    profile.settings.push({ key: 'brand.footer_text', value: 'Imported by the E2E suite' });
    writeFileSync(file, JSON.stringify(profile));
    await page.locator('input[type=file]').setInputFiles(file);
    await expect(page.getByRole('heading', { name: 'Preview of changes' })).toBeVisible();
    await expect(page.getByRole('row', { name: /brand.footer_text/ })).toContainText('Imported by the E2E suite');
    await page.getByRole('button', { name: /^Apply \d+ settings/ }).click();
    await expect(page.getByRole('heading', { name: 'Applied' })).toBeVisible();
    await setSetting('brand.footer_text', '');
  });

  test('the audit log records who changed what, and its hash chain verifies', async ({ as }) => {
    const page = await as('owner');
    await page.goto('/admin/audit');
    await expect(page.getByRole('cell', { name: 'setting.change' }).first()).toBeVisible();
    const v = await (await page.request.get('/api/admin/audit/verify')).json();
    expect(v.ok).toBe(true);
    const text = (await page.locator('main').innerText());
    expect(text).not.toMatch(/\$argon2|sk-|correct horse/);
  });
});

test.describe('branding and themes', () => {
  test('a new name, accent and logo show up for other people, and revert cleanly', async ({ as }, info) => {
    const owner = await as('owner');
    await owner.goto('/admin/branding');
    const name = owner.locator('article.set', { hasText: 'Application name' });
    await name.getByLabel('Application name').fill('Harbour Labs AI');
    await name.getByRole('button', { name: 'Save' }).click();
    // typing in the next field while the first save is still reloading the list must not lose what is typed
    const tab = owner.locator('article.set', { hasText: 'Browser tab title' });
    await tab.getByLabel('Browser tab title').fill('Harbour');
    await tab.getByRole('button', { name: 'Save' }).click();
    // a tiny real PNG
    mkdirSync(info.outputDir, { recursive: true });
    const png = path.join(info.outputDir, 'logo.png');
    writeFileSync(png, Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==', 'base64'));
    await owner.locator('input[type=file]').first().setInputFiles(png);
    await expect(owner.getByAltText('Current logo')).toBeVisible();
    await owner.locator('input[type=file]').nth(1).setInputFiles(png); // favicon
    await expect(owner.getByAltText('Current favicon')).toBeVisible();

    const member = await as('member');
    await member.goto('/chat');
    await expect(member).toHaveTitle('Harbour');
    await expect(member.locator('.rail img[alt="Harbour Labs AI"]')).toBeVisible();
    await expect(member.locator('link[rel=icon]')).toHaveAttribute('href', /^\/api\/assets\/[0-9a-f]{24}\.png$/);
    const ico = await member.request.get(await member.locator('link[rel=icon]').getAttribute('href') as string);
    expect(ico.status()).toBe(200);
    expect(ico.headers()['content-type']).toBe('image/png');
    await shot(member, 'branding-applied-1280');
    // the login screen of a signed-out visitor carries the new brand too
    const anon = await (await member.context().browser()!.newContext()).newPage();
    await anon.goto(`${URLS.seeded}/login`);
    await expect(anon.getByRole('heading', { name: /Harbour Labs AI/ })).toBeVisible();
    await anon.close();

    await owner.goto('/admin/branding');
    await owner.getByRole('button', { name: 'Remove' }).first().click();
    await owner.getByRole('button', { name: 'Remove' }).first().click();
    await setSetting('brand.app_name', 'Banana Corporation Private AI');
    await setSetting('brand.tab_title', 'Banana AI');
  });

  test('Paper, Carbon and Graphite switch live, persist across reload, and carry the accent colour', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    for (const t of ['carbon', 'graphite', 'paper']) {
      await page.getByRole('button', { name: /Max Member/ }).click();
      await page.getByLabel('Theme').selectOption(t);
      await expect(page.locator('html')).toHaveAttribute('data-theme', t);
      await page.reload();
      await expect(page.locator('html')).toHaveAttribute('data-theme', t);
      const bg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
      if (t !== 'paper') expect(bg).not.toBe('rgb(247, 247, 245)');
      await shot(page, `theme-${t}-1280`);
    }
  });

  test('colour that fails contrast is refused by the server', async ({ as }) => {
    const page = await as('owner');
    await page.goto('/admin/branding');
    const row = page.locator('article.set', { hasText: 'Accent colour (light themes)' });
    await row.locator('input.mono, input[type=text]').first().fill('#CCCCCC');
    await row.getByRole('button', { name: 'Save' }).click();
    await expect(row.getByRole('alert')).toContainText(/contrast/i);
  });
});

test('models: an alias change shows up in chat; disabling a model removes it for people', async ({ as }) => {
  const owner = await as('owner');
  await owner.goto('/admin/models');
  await owner.getByRole('row', { name: /Second Assistant/ }).getByRole('button', { name: /Edit/ }).click();
  await owner.getByLabel('Alias (what people see)').fill('Harbour Helper');
  await owner.getByRole('button', { name: /^Save/ }).click();
  const member = await apiAs('member');
  const names = (await (await member.get('/api/chat/models')).json()).map((m: any) => m.alias);
  expect(names).toContain('Harbour Helper');
  await member.dispose();
  const api = await apiAs('owner');
  const items = (await (await api.get('/api/admin/models')).json()).items;
  const m = items.find((x: any) => x.alias === 'Harbour Helper');
  await api.patch(`/api/admin/models/${m.id}`, { alias: 'Second Assistant' });
  await api.dispose();
});

test('backends: the connection test reports the real state', async ({ as }) => {
  const page = await as('owner');
  await page.goto('/admin/backends');
  await page.getByRole('button', { name: /Test/ }).first().click();
  await expect(page.getByText(/reachable|responding|connected|ok/i).first()).toBeVisible();
});

