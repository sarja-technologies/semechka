// Imajutsu in real Chrome against the deployed STAGING instance with the REAL engine and the real GPU: nothing is a double.
// Every mode that makes a picture is run through the page the way a person does it (upload, brush, anchor, references), and the
// result is downloaded, its size read, and a screenshot kept. Never run against production.
//
//   STAGING_EMAIL=... STAGING_PW=... STAGING_FIX=~/oj-staging-out/fix STAGING_OUT=~/oj-staging-out/real \
//     npx playwright test -c playwright.staging.config.ts staging/imajutsu
import { test, expect, type Page } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { STG } from '../../playwright.staging.config';

const OUT = process.env.STAGING_OUT ?? '.';
const F = (n: string) => path.join(STG.fix, n);
test.use({ storageState: path.join(OUT, 'staging-owner.json') });
test.describe.configure({ mode: 'serial' });

const shot = (page: Page, n: string) => page.screenshot({ path: path.join(OUT, `ij-${n}.png`), fullPage: false });
const api = async (page: Page, p: string) => (await page.request.get(p)).json();
const pid = (page: Page) => new URL(page.url()).searchParams.get('project')!;
const modeRadio = (page: Page, label: string) => page.getByTestId('modes').getByRole('radio', { name: new RegExp(`^${label}`) });
async function settle(page: Page) {
  await expect(page.getByTestId('save-state')).toHaveText('All changes saved', { timeout: 20_000 });
  await expect(page.getByTestId('pipeline')).not.toHaveAttribute('aria-busy', 'true', { timeout: 20_000 });
}
async function open(page: Page, label: string) {
  await page.goto('/imajutsu?new=1');
  await expect(page.getByTestId('start')).toBeVisible();
  await modeRadio(page, label).click();
  await expect.poll(() => new URL(page.url()).searchParams.get('project')).toBeTruthy();
  await expect(page.getByTestId('panels')).toBeVisible();
}
async function addSource(page: Page, file: string) {
  await page.getByTestId('source-file').setInputFiles(file);
  await expect(page.getByTestId('source-card')).toBeVisible({ timeout: 60_000 });
}
async function addRefs(page: Page, files: string[]) {
  const before = await page.getByTestId('ref-gallery').locator('li[data-ref-index]').count();
  await page.getByTestId('ref-file').setInputFiles(files);
  await expect(page.getByTestId('ref-gallery').locator('li[data-ref-index]')).toHaveCount(before + files.length, { timeout: 60_000 });
}
async function chooseModel(page: Page, alias: RegExp) {
  await page.getByTestId('advanced-toggle').check();
  const det = page.getByTestId('panel-model').locator('details').first();
  if (!(await det.evaluate((e: HTMLDetailsElement) => e.open))) await page.getByTestId('panel-model').getByText(/^All models/).click();
  await page.getByTestId('panel-model').getByRole('radio', { name: alias }).check();
  await settle(page);
}
/** Press Generate; wait for the run to finish on the real GPU; save the result and return its size, the job and the seconds it took. */
async function run(page: Page, name: string, limitMs = 1_500_000) {
  const t0 = Date.now();
  const [r] = await Promise.all([page.waitForResponse((x) => /\/generate$/.test(x.url()) && x.request().method() === 'POST', { timeout: 90_000 }), page.getByTestId('generate').click()]);
  expect(r.status(), await r.text().catch(() => '')).toBe(200);
  const body = await r.json();
  const job = body.jobs?.[0]?.id ?? body.job ?? body.jobs?.[0];
  const res = page.locator(`[data-testid="result"][data-job="${job}"]`).first();
  await expect(res).toBeVisible({ timeout: limitMs });
  const secs = Math.round((Date.now() - t0) / 1000);
  const asset = (await res.getAttribute('data-asset'))!;
  const buf = Buffer.from(await (await page.request.get(`/api/media/assets/${asset}/content`)).body());
  writeFileSync(path.join(OUT, `ij-${name}.png`), buf);
  const meta = await api(page, `/api/media/assets/${asset}`);
  const jobs = (await api(page, '/api/imajutsu/jobs')) as any[];
  const row = jobs.find((j) => j.job === job);
  console.log(`RESULT ${name}: ${meta.width}x${meta.height} in ${secs}s, model ${row?.model ?? row?.model_id}, status ${row?.status}, ${Math.round(buf.length / 1024)} KiB`);
  await page.waitForTimeout(500);
  await shot(page, `${name}-page`);
  return { w: meta.width as number, h: meta.height as number, secs, asset, job, row, buf };
}

test('iPhone Photo, Best realism (Qwen-Image 2512): vertical 9:16 720x1280 from the page', async ({ page }) => {
  await open(page, 'iPhone Photo');
  await expect(page.getByTestId('tier-chips').getByRole('radio', { name: 'Best realism' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('model-name')).toContainText(/qwen-image/i);
  await page.getByTestId('prompt-input').fill('My sister and her dog on the sofa on a rainy Sunday afternoon');
  await page.getByTestId('scene-chips').getByRole('button', { name: 'Indoor' }).click().catch(() => {});
  await settle(page);
  await shot(page, 'iphone-best-before');
  const r = await run(page, 'iphone-best');
  expect([r.w, r.h]).toEqual([720, 1280]);
});

test('iPhone Photo, Fast (FLUX.2 klein): vertical 9:16 720x1280 from the page', async ({ page }) => {
  await open(page, 'iPhone Photo');
  await page.getByTestId('tier-chips').getByRole('radio', { name: 'Fast' }).click();
  await expect(page.getByTestId('model-name')).toContainText(/klein/i);
  await page.getByTestId('prompt-input').fill('Two colleagues having lunch outside an office building, a sunny day');
  await settle(page);
  const r = await run(page, 'iphone-fast');
  expect([r.w, r.h]).toEqual([720, 1280]);
  expect(r.secs).toBeLessThan(300);
});

test('Edit Image (Qwen-Image-Edit 2511): an instruction edit of one picture', async ({ page }) => {
  await open(page, 'Edit Image');
  await addSource(page, F('klein.png'));
  await page.getByTestId('prompt-input').fill('Change the colour of the top the person is wearing to bright red. Keep everything else exactly as it is.');
  await settle(page);
  await expect(page.getByTestId('model-name')).toContainText(/qwen-image[ -]?edit/i);
  const r = await run(page, 'edit');
  expect(r.w).toBeGreaterThan(300);
});

test('Inpaint with a brushed selection (FLUX.2 klein)', async ({ page }) => {
  await open(page, 'Inpaint');
  await addSource(page, F('qwen.png'));
  await page.getByTestId('prompt-input').fill('A potted green plant');
  await page.getByTestId('open-mask').click();
  const wrap = page.getByTestId('mask-canvas-wrap');
  await expect(wrap).toBeVisible();
  const box = (await wrap.boundingBox())!;
  await page.getByTestId('mask-editor').getByRole('radio', { name: /^Brush/ }).click();
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.78);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.85, box.y + box.height * 0.8, { steps: 10 });
  await page.mouse.move(box.x + box.width * 0.85, box.y + box.height * 0.9, { steps: 10 });
  await page.mouse.move(box.x + box.width * 0.62, box.y + box.height * 0.9, { steps: 10 });
  await page.mouse.up();
  await expect(page.getByTestId('mask-status')).toContainText('will change');
  await shot(page, 'inpaint-mask');
  await page.keyboard.press('Escape');
  await settle(page);
  const r = await run(page, 'inpaint');
  expect(r.w).toBeGreaterThan(300);
});

test('Outpaint to a wider picture (FLUX.2 klein)', async ({ page }) => {
  await open(page, 'Outpaint');
  await addSource(page, F('klein.png'));
  await page.getByTestId('aspects').getByRole('radio', { name: '1:1' }).click();
  await settle(page);
  await expect(page.getByTestId('outpaint-view')).toBeVisible();
  await shot(page, 'outpaint-plan');
  const r = await run(page, 'outpaint');
  expect(r.w).toBeGreaterThan(r.h * 0.9);
});

test('Replace Subject with two reference pictures (FLUX.2 klein)', async ({ page }) => {
  await open(page, 'Replace Subject');
  await addSource(page, F('qwen.png'));
  await page.getByTestId('prompt-input').fill('Replace the main person with the person from the reference pictures');
  await addRefs(page, [F('klein.png'), F('edit.png')]);
  await chooseModel(page, /klein/i);
  await expect(page.getByTestId('badge-used')).toHaveCount(2);
  const r = await run(page, 'replace');
  expect(r.w).toBeGreaterThan(300);
});

test('Reference Image with three references (FLUX.2 klein)', async ({ page }) => {
  await open(page, 'Reference Image');
  await addRefs(page, [F('klein.png'), F('qwen.png'), F('edit.png')]);
  await page.getByTestId('prompt-input').fill('The same people together in a park on an autumn afternoon');
  await settle(page);
  const r = await run(page, 'reference');
  expect(r.w).toBeGreaterThan(300);
});

test('the gallery and the Jobs list show every run', async ({ page }) => {
  await page.goto('/imajutsu');
  const jobs = (await api(page, '/api/imajutsu/jobs')) as any[];
  const done = jobs.filter((j) => j.status === 'completed');
  console.log(`JOBS completed: ${done.length}; modes: ${[...new Set(done.map((j) => j.mode))].join(', ')}`);
  expect(done.length).toBeGreaterThanOrEqual(7);
});
