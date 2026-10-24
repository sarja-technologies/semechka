import type { Page } from '@playwright/test';
import { test, expect } from './support/fixtures';
import { apiAs } from './support/api';
import { shot } from './support/shots';

test.describe.configure({ mode: 'serial' });

const send = async (page: Page, text: string) => {
  await page.getByLabel('Message', { exact: true }).fill(text);
  await page.getByRole('button', { name: /^Send/ }).click();
};
const lastReply = (page: Page) => page.locator('article.msg.assistant').last();

test.describe('chat', () => {
  test('a message streams back from the model and the conversation is saved and listed', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await expect(page.getByRole('heading', { name: 'What are you working on?' })).toBeVisible();
    await send(page, 'Say hello to the harbour');
    await expect(lastReply(page)).toContainText('Hello from the fake model');
    await expect(page).toHaveURL(/\/chat\/[\w-]+$/);
    await expect(page.getByRole('link', { name: 'Say hello to the harbour' })).toBeVisible();
    await shot(page, 'chat-reply-1280');
    // survives a reload (it came from the database, not from memory)
    await page.reload();
    await expect(page.locator('article.msg')).toHaveCount(2);
    await expect(lastReply(page)).toContainText('Hello from the fake model');
  });

  test('streaming is announced to assistive technology without reading every token', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await send(page, 'announce me please');
    await expect(page.locator('[data-chat-status]')).toHaveText(/Reply complete/);
    await expect(page.locator('[data-chat-status]')).toHaveAttribute('role', 'status');
    await expect(page.locator('[data-chat-status]')).toHaveAttribute('aria-live', 'polite');
  });

  test('stop keeps the partial reply and marks it stopped', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await send(page, 'a slow answer please');
    await expect(page.getByRole('button', { name: /^Stop/ })).toBeVisible();
    await expect(page.locator('article.msg.assistant').last()).toContainText('tick');
    await page.getByRole('button', { name: /^Stop/ }).click();
    await expect(page.getByRole('button', { name: /^Send/ })).toBeVisible();
    await expect(page.locator('.chip.warn', { hasText: 'Stopped' })).toBeVisible();
    const text = await lastReply(page).innerText();
    expect(text).toContain('tick');
    expect(text.split('tick').length).toBeLessThan(60); // it really stopped early
    // Escape stops too
    await send(page, 'another slow one');
    await expect(lastReply(page)).toContainText('tick');
    await page.keyboard.press('Escape');
    await expect(page.locator('.chip.warn', { hasText: 'Stopped' })).toHaveCount(2);
  });

  test('regenerate and edit create branches you can move between', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await send(page, 'branching test question');
    await expect(lastReply(page)).toContainText('Hello from the fake model');
    await lastReply(page).getByRole('button', { name: /Regenerate/ }).click();
    await expect(lastReply(page).getByText('2/2')).toBeVisible();
    await lastReply(page).getByRole('button', { name: 'Previous version' }).click();
    await expect(lastReply(page).getByText('1/2')).toBeVisible();
    // edit the user message: a new branch with a new reply
    await page.locator('article.msg.user').first().getByRole('button', { name: /Edit/ }).click();
    await page.getByLabel('Edit message').fill('branching test question, edited');
    await page.getByRole('button', { name: 'Save and regenerate' }).click();
    await expect(page.locator('article.msg.user').first()).toContainText('edited');
    await expect(page.locator('article.msg.user').first().getByText('2/2')).toBeVisible();
    await page.locator('article.msg.user').first().getByRole('button', { name: 'Previous version' }).click();
    await expect(page.locator('article.msg.user').first()).not.toContainText('edited');
  });

  test('search finds conversations by title; export, rename and delete work', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await send(page, 'zebra crossing research notes');
    await expect(lastReply(page)).toContainText('Hello');
    await page.getByRole('searchbox', { name: 'Search conversations' }).fill('zebra');
    await expect(page.getByRole('link', { name: 'zebra crossing research notes' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'branching test question' })).toHaveCount(0);
    await page.getByRole('searchbox', { name: 'Search conversations' }).fill('no such words anywhere');
    await expect(page.getByText('No conversation matches that search.')).toBeVisible();
    await page.getByRole('searchbox', { name: 'Search conversations' }).fill('');

    const dl = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Export as Markdown' }).click();
    expect((await dl).suggestedFilename()).toMatch(/\.md$/);

    await page.getByRole('button', { name: 'Rename', exact: true }).click();
    await page.getByLabel('Title').fill('Zebra notes renamed');
    await page.getByRole('button', { name: 'Rename', exact: true }).last().click();
    await expect(page.getByRole('heading', { name: 'Zebra notes renamed' })).toBeVisible();

    await page.getByRole('button', { name: 'Delete conversation' }).click();
    await page.getByRole('button', { name: 'Delete conversation' }).last().click();
    await expect(page.getByRole('link', { name: 'Zebra notes renamed' })).toHaveCount(0);
  });

  test('no-save: nothing is stored, nothing appears in history, and the panel says so', async ({ as }) => {
    const page = await as('member');
    const api = await apiAs('member');
    const before = (await (await api.get('/api/chat/conversations')).json()).length;
    await page.goto('/chat');
    await page.getByRole('checkbox', { name: 'No-save' }).check();
    await expect(page.getByRole('heading', { name: 'This conversation will not be saved' })).toBeVisible();
    await send(page, 'a secret that must not persist');
    await expect(lastReply(page)).toContainText('Hello from the fake model');
    await expect(page.getByRole('button', { name: /Not saved/ })).toBeVisible();
    await expect(page).toHaveURL(/\/chat$/);
    await page.getByRole('button', { name: /Not saved/ }).click();
    await expect(page.getByRole('dialog')).toContainText(/not (be )?(saved|stored)|nothing/i);
    await page.getByRole('button', { name: 'Close', exact: true }).last().click();
    expect((await (await api.get('/api/chat/conversations')).json()).length).toBe(before);
    await page.reload();
    await expect(page.getByText('a secret that must not persist')).toHaveCount(0);
    await api.dispose();
  });

  test('the model picker switches the model that answers', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await expect(page.getByLabel('Model', { exact: true }).locator('option')).toHaveCount(2);
    const opts = await page.getByLabel('Model', { exact: true }).locator('option').allInnerTexts();
    await page.getByLabel('Model', { exact: true }).selectOption({ index: 1 });
    await send(page, 'which model is this');
    await expect(lastReply(page)).toContainText('Hello');
    await expect(lastReply(page).locator('.who')).toContainText(opts[1].replace(/ \(.*/, ''));
  });
});

test.describe('technical visibility', () => {
  test('an ordinary member sees no technical affordance, in the UI or in raw API responses', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await expect(page.getByRole('button', { name: 'Technical details' })).toHaveCount(0);
    await send(page, 'tech check');
    await expect(lastReply(page)).toContainText('Hello');
    const api = await apiAs('member');
    const convs = await (await api.get('/api/chat/conversations')).json();
    const conv = await (await api.get(`/api/chat/conversations/${convs[0].id}`)).json();
    const blob = JSON.stringify(conv) + JSON.stringify(await (await api.get('/api/chat/models')).json());
    for (const leak of ['fake-small', 'openai_compat', 'tokens_per_s', 'ttft', '127.0.0.1']) expect(blob).not.toContain(leak);
    expect((await api.get('/api/system/resources')).status()).toBe(403);
    expect((await api.get('/api/admin/models')).status()).toBe(403);
    await api.dispose();
  });

  test('a person granted technical fields sees exactly those, read-only', async ({ as }) => {
    const page = await as('tech');
    await page.goto('/chat');
    await page.getByRole('button', { name: 'Technical details' }).click();
    await send(page, 'tech details please');
    await expect(lastReply(page)).toContainText('Hello');
    const drawer = page.getByRole('complementary', { name: 'Technical details' });
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText(/tokens|speed|Model ID/i);
    await expect(drawer.getByRole('button')).toHaveCount(0); // reading is not operating
    await shot(page, 'chat-technical-1280');
    const api = await apiAs('tech');
    expect((await api.post('/api/admin/models/x/unload')).status()).toBe(403);
    expect((await api.get('/api/admin/backends')).status()).toBe(403);
    await api.dispose();
  });
});

test('another person cannot open or change my conversation, even knowing its id', async ({ as }) => {
  const owner = await as('member');
  await owner.goto('/chat');
  await send(owner, 'my private question');
  await expect(lastReply(owner)).toContainText('Hello');
  const id = owner.url().split('/').pop()!;
  const other = await as('tech');
  await other.goto(`/chat/${id}`);
  await expect(other.getByText(/does not exist or is not yours/)).toBeVisible();
  const api = await apiAs('tech');
  for (const r of [await api.get(`/api/chat/conversations/${id}`), await api.patch(`/api/chat/conversations/${id}`, { title: 'x' }), await api.get(`/api/chat/conversations/${id}/export`)])
    expect(r.status()).toBe(404);
  await api.dispose();
});
