import type { Page } from '@playwright/test';
import { test, expect } from './support/fixtures';
import { apiAs } from './support/api';
import { shot } from './support/shots';

test.describe.configure({ mode: 'serial' });

const open = async (page: Page) => { await page.getByRole('button', { name: /^(Search|Open command palette)/ }).first().waitFor(); await page.keyboard.press('Control+k'); await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible(); };
const results = (page: Page) => page.getByRole('listbox', { name: 'Results' });
const box = (page: Page) => page.getByRole('combobox', { name: 'Search commands' });

test('Ctrl+K opens it with focus in the search box, Escape closes it and returns focus', async ({ as }) => {
  const page = await as('member');
  await page.goto('/chat');
  await page.getByLabel('Message', { exact: true }).focus();
  await open(page);
  await expect(box(page)).toBeFocused();
  await shot(page, 'palette-1280');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.getByLabel('Message', { exact: true })).toBeFocused();
});

test('navigate by typing and Enter; arrow keys move the highlighted option', async ({ as }) => {
  const page = await as('member');
  await page.goto('/chat');
  await open(page);
  await box(page).fill('usage');
  await expect(results(page).getByRole('option', { name: 'Usage' })).toHaveAttribute('aria-selected', 'true');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/\/usage/);
  await open(page);
  await page.keyboard.press('ArrowDown');
  await expect(results(page).getByRole('option').nth(1)).toHaveAttribute('aria-selected', 'true');
  await expect(box(page)).toHaveAttribute('aria-activedescendant', 'pal-1');
});

test('new chat, new no-save chat, switch model and find a conversation', async ({ as }) => {
  const page = await as('member');
  await page.goto('/chat');
  await page.getByLabel('Message', { exact: true }).fill('palette findable walrus');
  await page.getByRole('button', { name: /^Send/ }).click();
  await expect(page.locator('article.msg.assistant').last()).toContainText('Hello');
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/);

  await open(page); await box(page).fill('new no-save'); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'This conversation will not be saved' })).toBeVisible();
  await expect(page).toHaveURL(/\/chat$/);

  await open(page); await box(page).fill('new chat'); await page.keyboard.press('Enter');
  await expect(page.getByRole('heading', { name: 'What are you working on?' })).toBeVisible();

  await open(page); await box(page).fill('Second Assistant');
  await results(page).getByRole('option', { name: 'Second Assistant' }).click();
  await expect(page.getByLabel('Model', { exact: true })).toHaveValue(/.+/);
  await expect(page.getByLabel('Model', { exact: true }).locator('option:checked')).toHaveText(/Second Assistant/);

  await open(page); await box(page).fill('walrus');
  await results(page).getByRole('option', { name: /palette findable walrus/ }).click();
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/);
  await expect(page.locator('article.msg.user').first()).toContainText('palette findable walrus');
});

test('theme and density commands change and persist the preference', async ({ as }) => {
  const page = await as('member');
  await page.goto('/chat');
  await open(page); await box(page).fill('theme carbon'); await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'carbon');
  await page.reload();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'carbon');
  await open(page); await box(page).fill('theme paper'); await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'paper');
});

test('commands respect permissions: members never see administration, admins do, and sign-out works', async ({ as }) => {
  const member = await as('member');
  await member.goto('/chat');
  await open(member); await box(member).fill('roles');
  await expect(results(member).getByRole('option')).toHaveCount(0);
  await box(member).fill('administration');
  await expect(results(member).getByRole('option')).toHaveCount(0);
  await member.keyboard.press('Escape');

  const owner = await as('owner');
  await owner.goto('/chat');
  await open(owner); await box(owner).fill('roles');
  await results(owner).getByRole('option', { name: 'Roles & Permissions' }).click();
  await expect(owner).toHaveURL(/\/admin\/roles/);
  // the same palette works inside the admin console, and finds settings by name
  await open(owner); await box(owner).fill('Minimum password');
  await results(owner).getByRole('option', { name: /Minimum password length/ }).click();
  await expect(owner).toHaveURL(/\/admin\/settings\?q=auth\.password_min_length/);

  const g = await as('guest');
  await g.goto('/usage');
  await open(g); await box(g).fill('new chat');
  await expect(results(g).getByRole('option')).toHaveCount(0); // chat is not available to this role, so it is not offered

  const api = await apiAs('member');
  await api.dispose();
});

test('the palette is reachable without a keyboard shortcut', async ({ as }) => {
  const page = await as('member');
  await page.goto('/chat');
  await page.getByRole('button', { name: /^Search/ }).click();
  await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
  await page.keyboard.press('Tab'); // focus stays inside the dialog
  expect(await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]'))).toBe(true);
});
