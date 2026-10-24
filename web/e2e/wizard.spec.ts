import { execSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import type { Page } from '@playwright/test';
import { test, expect, VIEWPORTS, noHorizontalScroll } from './support/fixtures';
import { DIRS, PW, URLS } from './support/env';
import { shot } from './support/shots';

test.use({ baseURL: URLS.fresh });

/** The same screen at the four target widths (real viewport resize): no sideways scroll, and a screenshot to look at. */
async function matrix(page: Page, name: string) {
  for (const v of VIEWPORTS) {
    await page.setViewportSize({ width: v.width, height: v.height });
    await noHorizontalScroll(page);
    await shot(page, `${name}-${v.name}`);
  }
  await page.setViewportSize({ width: 1280, height: 800 });
}

test('first-run wizard: every step works, branding and policies apply, nothing is pre-created', async ({ page }) => {
  const token = readFileSync(path.join(DIRS.fresh, 'setup-token'), 'utf8').trim();

  // a fresh server has no accounts: every path leads to the wizard, and the API is closed
  await page.goto('/login');
  await expect(page).toHaveURL(/\/setup/);
  expect((await page.request.get('/api/me')).status()).toBe(401);

  // step 0: the one-time token
  await expect(page.getByRole('heading', { name: 'Set up this server', exact: true })).toBeVisible();
  await page.getByLabel('Setup token').fill('not-the-token');
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('alert')).toContainText(/token/i);
  await page.getByLabel('Setup token').fill(token);
  await matrix(page, 'wizard-0-token');
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 1: identity and owner (client-side validation blocks a short password)
  await expect(page.getByRole('heading', { name: 'Identity', exact: true })).toBeVisible();
  await page.getByLabel('Organisation name').fill('Acme Research');
  await page.getByLabel('Application title').fill('Acme Research Private AI');
  await page.getByLabel('Short name').fill('Acme AI');
  await page.getByLabel('Browser tab label').fill('Acme AI');
  await page.getByLabel('Default theme').selectOption('graphite');
  await page.getByLabel('Your name').fill('Ola Owner');
  await page.getByLabel('Email').fill('ola@acme.test');
  await page.getByLabel('Password').fill('short');
  await expect(page.getByRole('button', { name: 'Continue' })).toBeDisabled();
  await page.getByLabel('Password').fill(PW);
  await matrix(page, 'wizard-1-identity');
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 2: preset
  await expect(page.getByRole('heading', { name: 'Deployment preset', exact: true })).toBeVisible();
  await page.getByRole('radio').nth(1).check();
  await matrix(page, 'wizard-2-preset');
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 3: real hardware inspection of this machine
  await expect(page.getByRole('heading', { name: 'This machine', exact: true })).toBeVisible();
  // the wizard reports this machine's real architecture, not a guess
  await expect(page.getByText(execSync('uname -m').toString().trim(), { exact: true })).toBeVisible();
  await expect(page.getByText('Listening on')).toBeVisible();
  await matrix(page, 'wizard-3-hardware');
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 4: backend detection by address, model discovery, selection and smoke test choice
  await expect(page.getByRole('heading', { name: 'Models', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Continue' })).toBeEnabled();
  await page.getByLabel('Address').fill('http://127.0.0.1:9/v1');
  await page.getByRole('button', { name: /Test connection/ }).click();
  await expect(page.getByText(/Could not connect/)).toBeVisible();
  await page.getByLabel('Address').fill(URLS.fake);
  await page.getByRole('button', { name: /Test connection/ }).click();
  await expect(page.getByText(/2 models found/)).toBeVisible();
  await page.getByRole('checkbox', { name: 'fake-other' }).uncheck();
  await expect(page.getByLabel('Smoke test with')).toHaveValue('fake-small');
  await matrix(page, 'wizard-4-models');
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 5: technical visibility for ordinary Members
  await expect(page.getByRole('heading', { name: 'People and technical visibility', exact: true })).toBeVisible();
  await page.getByRole('radio', { name: /Informed/ }).check();
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 6: privacy and limits
  await expect(page.getByRole('heading', { name: /Privacy, retention/ })).toBeVisible();
  await page.getByLabel('Keep chat history for').selectOption('7');
  await page.getByLabel('Messages per person per day').fill('25');
  await page.getByLabel('Notice shown in the privacy panel (optional)').fill('Acme policy: no customer data.');
  await matrix(page, 'wizard-6-privacy');
  await page.getByRole('button', { name: 'Continue' }).click();

  // step 7: access
  await expect(page.getByRole('heading', { name: 'Access and sign-up', exact: true })).toBeVisible();
  await page.getByLabel('How people get an account').selectOption('admin');
  await matrix(page, 'wizard-7-access');
  await page.getByRole('button', { name: 'Review' }).click();

  // step 8: review, then launch while resizing the real viewport to check the layout of every step's final state
  await expect(page.getByRole('heading', { name: 'Review and launch', exact: true })).toBeVisible();
  await expect(page.getByText('ola@acme.test')).toBeVisible();
  await matrix(page, 'wizard-8-review');
  await page.getByRole('button', { name: 'Launch', exact: true }).click();

  // launched: smoke test succeeded, branding is live, owner is signed in, only chosen models exist
  await expect(page.getByRole('heading', { name: 'Ready', exact: true })).toBeVisible();
  await expect(page.getByText('passed')).toBeVisible();
  await matrix(page, 'wizard-9-done');
  const me = await (await page.request.get('/api/me')).json();
  expect(me.user.email).toBe('ola@acme.test');
  expect(me.ui.brand.app_name).toBe('Acme Research Private AI');
  expect(me.ui.retention_days).toBe(7);
  const models = await (await page.request.get('/api/admin/models')).json();
  expect(models.items.map((m: any) => [m.real_id, m.enabled])).toEqual(expect.arrayContaining([['fake-small', true], ['fake-other', false]]));
  expect((await (await page.request.get('/api/admin/users')).json()).items).toHaveLength(1);
  // the token is spent
  expect((await page.request.get('/api/setup/hardware', { headers: { 'x-setup-token': token } })).status()).toBeGreaterThanOrEqual(400);
  await page.getByRole('button', { name: 'Open Acme Research Private AI' }).click();
  await expect(page).toHaveTitle('Acme AI');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'graphite');
});
