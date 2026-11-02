import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { test, expect, signIn, appReady } from './support/fixtures';
import { apiAs, setSetting } from './support/api';
import { PW, URLS } from './support/env';

test.describe.configure({ mode: 'serial' });

// The service is not running here: these specs cover what Semechka itself decides (who is offered the entry, who is refused
// by the proxy, how a run record is shown). The proxy against a Trinodal test double is covered by backend/tests/test_trinodal.py.
const nav = (page: Page) => page.getByRole('navigation', { name: 'More' });
const send = async (page: Page, text: string) => {
  await page.getByLabel('Message', { exact: true }).fill(text);
  await page.getByRole('button', { name: /^Send/ }).click();
};

test.describe('Trinodal AI entry', () => {
  let before: unknown;
  test.afterAll(async () => { await setSetting('trinodal.url', typeof before === 'string' ? before : ''); });

  test('nobody is offered it while it is not configured', async ({ as }) => {
    for (const who of ['owner', 'admin', 'member'] as const) {
      const page = await as(who);
      await page.goto('/chat');
      await appReady(page);
      await expect(nav(page).getByRole('button', { name: /Trinodal/ })).toHaveCount(0);
      expect((await page.request.get('/trinodal/')).status()).toBe(404);
    }
  });

  test('once configured it is offered to people who hold trinodal.view, with training links only for trinodal.train', async ({ as, browser }) => {
    before = await setSetting('trinodal.url', 'http://127.0.0.1:8800');
    const all = ['Dashboard', 'Network Explorer', 'Training Studio', 'Training Jobs', 'Evaluations'];
    const links = async (page: Page) => {
      await page.goto('/chat');
      await appReady(page);
      const entry = nav(page).getByRole('button', { name: /Trinodal/ });
      if (!(await entry.count())) return null;
      await entry.click();
      const list = page.getByRole('list', { name: 'Trinodal AI' });
      return { names: await list.getByRole('link').allInnerTexts(), hrefs: await list.getByRole('link').evaluateAll((as) => as.map((a) => a.getAttribute('href'))) };
    };
    // Owner and Platform Admin templates hold view and train on a fresh install
    const owner = await links(await as('owner'));
    expect(owner?.names).toEqual(all);
    const op = await as('owner'); // the expanded entry and the account menu entry have no accessibility violations
    await op.goto('/chat');
    await appReady(op);
    await nav(op).getByRole('button', { name: /Trinodal/ }).click();
    expect(nav(op).getByRole('button', { name: /Trinodal/ })).toHaveAttribute('aria-expanded', 'true');
    await op.getByRole('button', { name: /^Account: / }).click();
    await expect(op.getByRole('group', { name: 'Trinodal AI' }).filter({ hasText: 'Training Studio' })).toBeVisible();
    const found = (await new AxeBuilder({ page: op }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()).violations.map((v) => `${v.id}: ${v.help}`);
    expect(found).toEqual([]);
    expect(owner?.hrefs).toEqual(['/trinodal/', '/trinodal/#/network', '/trinodal/#/training', '/trinodal/#/training/jobs', '/trinodal/#/evaluations']);
    expect((await links(await as('admin')))?.names).toEqual(all);
    expect(await links(await as('member'))).toBeNull();
    expect(await links(await as('guest'))).toBeNull();

    // a person who may only look
    const api = await apiAs('owner');
    const role = await (await api.post('/api/admin/roles', { label: 'Trinodal viewers', grants: ['mode.chat', 'trinodal.view'], denies: [] })).json();
    const made = await (await api.post('/api/admin/users', { email: 'viewer@e2e.test', display_name: 'Vera Viewer', password: PW, role_id: role.id, team_ids: [] })).json();
    try {
      const ctx = await browser.newContext({ baseURL: URLS.seeded });
      const page = await ctx.newPage();
      await signIn(page, 'viewer@e2e.test', PW);
      await appReady(page);
      // this role has no chat model, so there is no conversation sidebar: the account menu carries the entry too
      await page.getByRole('button', { name: /^Account: / }).click();
      const items = page.getByRole('group', { name: 'Trinodal AI' }).getByRole('menuitem');
      expect(await items.allInnerTexts()).toEqual(['Dashboard', 'Network Explorer', 'Evaluations']);
      expect(await items.evaluateAll((as) => as.map((a) => a.getAttribute('href')))).toEqual(['/trinodal/', '/trinodal/#/network', '/trinodal/#/evaluations']);
      // the server decides: the same person is refused every training request, whatever the page shows
      const me = await (await page.request.get('/api/me')).json();
      expect((await page.request.post('/trinodal/api/training/jobs', { data: {}, headers: { 'x-csrf-token': me.csrf } })).status()).toBe(403);
      expect((await page.request.get('/trinodal/api/admin/tokens')).status()).toBe(403);
      await ctx.close();
    } finally {
      await api.del(`/api/admin/users/${made.id}`);
      await api.del(`/api/admin/roles/${role.id}`);
      await api.dispose();
    }
  });

  test('the proxy refuses a signed-out visitor and a person without the permission; the service token is not set up here', async ({ as, playwright }) => {
    const anon = await playwright.request.newContext({ baseURL: URLS.seeded });
    expect((await anon.get('/trinodal/api/network')).status()).toBe(401);
    await anon.dispose();
    const member = await as('member');
    expect((await member.request.get('/trinodal/api/network')).status()).toBe(403);
    const owner = await as('owner');
    const r = await owner.request.get('/trinodal/api/network');
    expect(r.status()).toBe(503);
    expect(JSON.stringify(await r.json())).not.toMatch(/token-file|\.local|\/home\//);
    const av = await (await owner.request.get('/api/trinodal/availability')).json();
    expect(av).toEqual({ enabled: true, view: true, train: true, admin: true });
    expect(JSON.stringify(av)).not.toContain('8800');
  });
});

test.describe('Trinodal run record in the technical details', () => {
  const trinodal = { nodes: ['conversation', 'mathematics', 'conversation'], route: 'math.exact' };
  const mutate = (page: Page, tech: object | null) => page.route(/\/api\/chat\/conversations\/[^/]+\/thread/, async (route) => {
    const res = await route.fetch();
    const j = await res.json();
    for (const m of j.messages) if (m.role === 'assistant' && tech) m.tech = { ...m.tech, trinodal: tech };
    await route.fulfill({ response: res, json: j });
  });

  test('the path is shown as one line when the server sent it, and not at all when it did not', async ({ as }) => {
    const page = await as('tech');
    await page.goto('/chat');
    const open = page.getByRole('button', { name: 'Details', exact: true }); // "Hide details" when an earlier spec left the drawer open
    if (await open.count()) await open.click();
    await send(page, 'path please');
    await expect(page.locator('article.msg.assistant').last()).toContainText('Hello');
    const drawer = page.getByRole('complementary', { name: 'Technical details' });
    await expect(drawer).toContainText(/tokens|speed|Model ID/i);
    await expect(drawer).not.toContainText('Path:');
    await mutate(page, trinodal);
    await page.reload();
    await page.locator('article.msg.assistant').last().getByRole('button', { name: 'Technical details' }).click();
    await expect(drawer.getByText('Path: conversation → mathematics → conversation')).toBeVisible();
    await expect(drawer).toContainText('math.exact');
    await page.unroute(/\/api\/chat\/conversations\/[^/]+\/thread/);
    await mutate(page, { route: 'math.exact' }); // permitted to see the route but the reply has no nodes: no Path line
    await page.reload();
    await page.locator('article.msg.assistant').last().getByRole('button', { name: 'Technical details' }).click();
    await expect(drawer).toContainText('math.exact');
    await expect(drawer).not.toContainText('Path:');
  });
});
