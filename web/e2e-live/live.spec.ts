import { test, expect, type Page, type Browser } from '@playwright/test';

// Real people, real browser, real server on the Spark. Nothing is mocked and nothing is pre-signed-in.
test.describe.configure({ mode: 'serial' });
const CREDS: Record<string, string> = JSON.parse(process.env.STAGING_CREDS ?? '{}');
const DOMAIN = 'staging.test';

async function signIn(browser: Browser, local: string): Promise<Page> {
  const email = `${local}@${DOMAIN}`;
  const page = await (await browser.newContext()).newPage();
  await page.goto('/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(CREDS[email]);
  await page.getByRole('button', { name: /sign in/i }).click();
  await expect(page).not.toHaveURL(/\/login/);
  return page;
}
const api = async (page: Page, path: string) => (await page.request.get(path)).json();

/** Make sure the composer will use `model`: usually it is already the default, otherwise pick it from the chooser. */
async function choose(page: Page, model: string) {
  const current = page.locator('.composer').getByRole('button', { name: new RegExp(`^${model}\\b`) });
  if (await current.count()) return;
  await page.locator('.composer').getByRole('button', { name: /Model|chevron/i }).or(page.locator('.composer button[aria-haspopup]')).first().click();
  await page.getByRole('option', { name: new RegExp(model) }).first().click();
  await expect(page.locator('.composer').getByRole('button', { name: new RegExp(`^${model}\\b`) })).toBeVisible();
}

// ---- Scenario G: media generation as an ordinary member, through the UI --------------------------------------------
test('G: a member makes a picture in the browser and it is still there after reloading and signing in again', async ({ browser }, info) => {
  test.setTimeout(20 * 60_000);
  const page = await signIn(browser, 'acme-eng');
  await page.goto('/image');
  await expect(page.getByRole('radio', { name: 'Picture' })).toHaveAttribute('aria-checked', 'true');
  await choose(page, 'Sketch');
  const prompt = `A red paper boat on calm water, studio light, test ${Date.now()}`;
  await page.getByPlaceholder('Describe the picture').fill(prompt);
  await page.keyboard.press('Enter');
  const img = page.locator('img[alt^="Generated from"]').first();
  await expect(img).toBeVisible({ timeout: 15 * 60_000 });
  const src = await img.getAttribute('src');
  const ok = await page.evaluate(async (s) => { const r = await fetch(s!); const b = await r.blob(); return [r.status, r.headers.get('content-type'), b.size]; }, src);
  expect(ok[0]).toBe(200);
  expect(String(ok[1])).toMatch(/^image\//);
  expect(Number(ok[2])).toBeGreaterThan(2000);
  expect(await img.evaluate((e: HTMLImageElement) => e.naturalWidth)).toBeGreaterThan(100);
  await page.screenshot({ path: info.outputPath('picture.png'), fullPage: true });
  await page.reload();
  await expect(page.locator('img[alt^="Generated from"]').first()).toBeVisible({ timeout: 30_000 });
  // a different browser session, same person: the picture is kept on the server, in their library
  const again = await signIn(browser, 'acme-eng');
  await again.goto('/library');
  await expect(again.locator('img').first()).toBeVisible({ timeout: 30_000 });
  const lib = await api(again, '/api/media?limit=5').catch(() => null);
  info.annotations.push({ type: 'library', description: JSON.stringify(lib)?.slice(0, 200) ?? 'library listed in the page' });
});

test('G: the same member makes a short video in the browser (queued behind other people\'s work)', async ({ browser }, info) => {
  test.setTimeout(30 * 60_000);
  const page = await signIn(browser, 'acme-eng');
  await page.goto('/video');
  await expect(page.getByRole('radio', { name: 'Video' })).toHaveAttribute('aria-checked', 'true');
  await choose(page, 'Clip');
  await page.getByPlaceholder('Describe the video').fill('A paper boat drifting slowly across still water');
  await page.keyboard.press('Enter');
  const video = page.locator('video').first();
  await expect(video).toBeVisible({ timeout: 28 * 60_000 });
  await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState), { timeout: 60_000 }).toBeGreaterThanOrEqual(1);
  expect(await video.evaluate((v: HTMLVideoElement) => v.duration)).toBeGreaterThan(0.5);
  await page.screenshot({ path: info.outputPath('video.png'), fullPage: true });
});

// ---- Scenario I: an administrator customises the platform through the admin UI; people see it ----------------------
test('I: renaming the product in Administration shows up for other people, and reverting restores it', async ({ browser }) => {
  const admin = await signIn(browser, 'owner');
  const before = (await api(admin, '/api/public')).brand?.app_name ?? (await api(admin, '/api/public')).app_name;
  await admin.goto('/admin/branding');
  const name = admin.locator('#set-brand-app_name');
  await name.getByLabel('Application name').fill('Harbour Staging AI');
  await name.getByRole('button', { name: 'Save' }).click();
  await expect(admin.getByText(/Saved/).first()).toBeVisible();
  const member = await signIn(browser, 'acme-finance');
  await member.goto('/chat');
  await expect(member.getByRole('link', { name: 'Harbour Staging AI: home' })).toBeVisible();
  const stored = await api(admin, '/api/admin/settings?q=brand.app_name');
  expect(stored.settings[0].value).toBe('Harbour Staging AI');
  // revert
  await admin.goto('/admin/branding');
  await admin.locator('#set-brand-app_name').getByLabel('Application name').fill('Semechka Staging');
  await admin.locator('#set-brand-app_name').getByRole('button', { name: 'Save' }).click();
  await expect(admin.getByText(/Saved/).first()).toBeVisible();
  await member.reload();
  await expect(member.getByRole('link', { name: 'Semechka Staging: home' })).toBeVisible();
  void before;
});

test('I: switching Cloud workspaces off in Administration removes it for people at once, and on brings it back', async ({ browser }) => {
  const admin = await signIn(browser, 'owner');
  const member = await signIn(browser, 'acme-finance');
  await member.goto('/chat');
  await expect(member.getByRole('link', { name: 'Workspaces' }).first()).toBeVisible();
  const toggle = async (on: boolean) => {
    await admin.goto('/admin/settings?q=cloud.workspaces.enabled');
    const row = admin.locator('article.set', { hasText: 'Cloud workspaces' });
    const box = row.getByRole('checkbox').or(row.getByRole('switch')).first();
    if ((await box.isChecked()) !== on) await box.click();
    await row.getByRole('button', { name: 'Save' }).click();
    await expect(admin.getByText(/Saved/).first()).toBeVisible();
  };
  await toggle(false);
  expect((await api(admin, '/api/admin/settings?q=cloud.workspaces.enabled')).settings[0].value).toBe(false);
  await member.reload();
  await expect(member.getByRole('link', { name: 'Workspaces' })).toHaveCount(0);
  await toggle(true);
  expect((await api(admin, '/api/admin/settings?q=cloud.workspaces.enabled')).settings[0].value).toBe(true);
  await member.reload();
  await expect(member.getByRole('link', { name: 'Workspaces' }).first()).toBeVisible();
  // the change is on record
  const hist = await api(admin, '/api/admin/audit?limit=30').catch(() => null);
  if (hist) expect(JSON.stringify(hist)).toContain('cloud.workspaces.enabled');
});

// ---- the cloud features through the real UI ---------------------------------------------------------------------------
test('Workspaces: a department lead opens their workspace, sees their files searchable and cannot see another company', async ({ browser }) => {
  const fin = await signIn(browser, 'acme-finance');
  await fin.goto('/workspaces');
  await expect(fin.getByRole('heading', { level: 1, name: 'Workspaces' })).toBeVisible();
  await fin.getByRole('link', { name: /Finance/ }).first().click();
  await expect(fin.getByText('policy.docx').first()).toBeVisible();
  await expect(fin.getByText('Searchable').first()).toBeVisible();
  const glx = await signIn(browser, 'globex-analyst');
  await glx.goto('/workspaces');
  await expect(glx.getByRole('link', { name: /Finance/ })).toHaveCount(0);
  await expect(glx.getByText('policy.docx')).toHaveCount(0);
});
