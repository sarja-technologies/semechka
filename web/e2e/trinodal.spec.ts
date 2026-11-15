import type { Page } from '@playwright/test';
import { test, expect, appReady } from './support/fixtures';
import { setSetting } from './support/api';
import { URLS } from './support/env';

test.describe.configure({ mode: 'serial' });

// The service is not running here: these specs cover what Semechka itself decides (who is offered the entry, who is refused
// by the proxy, how a run record is shown). The proxy against a Trinodal test double is covered by backend/tests/test_trinodal.py.
const send = async (page: Page, text: string) => {
  await page.getByLabel('Message', { exact: true }).fill(text);
  await page.getByRole('button', { name: /^Send/ }).click();
};

test.describe('Trinodal AI is not part of the interface', () => {
  let before: unknown;
  test.afterAll(async () => { await setSetting('trinodal.url', typeof before === 'string' ? before : ''); });

  test('configured or not, nobody is offered a Trinodal entry in the navigation, the account menu or the palette', async ({ as }) => {
    before = await setSetting('trinodal.url', 'http://127.0.0.1:8800');
    for (const who of ['owner', 'admin', 'member'] as const) {
      const page = await as(who);
      await page.goto('/chat');
      await appReady(page);
      await expect(page.getByRole('link', { name: /Trinodal/ })).toHaveCount(0);
      await expect(page.getByRole('button', { name: /Trinodal/ })).toHaveCount(0);
      await page.getByRole('button', { name: /^Account: / }).click();
      await expect(page.getByRole('group', { name: 'Trinodal AI' })).toHaveCount(0);
      await page.keyboard.press('Escape');
      await page.keyboard.press('Control+k');
      await page.keyboard.type('trinodal');
      await expect(page.getByRole('option', { name: /Trinodal/ })).toHaveCount(0);
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
