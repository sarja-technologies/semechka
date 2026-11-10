import { test, expect } from '@playwright/test';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { STG } from '../../playwright.staging.config';

// REA against a REAL instance (staging or a dev copy): a real upload, the real engine's analysis with the real vision model on the router, the keyframes
// through Semechka's proxy. Needs STAGING_URL, STAGING_EMAIL/STAGING_PW (synthetic account), STAGING_FIX with REA_CLIP (default multishot.mp4).
const OUT = process.env.STAGING_OUT ?? '.';
test.use({ storageState: path.join(OUT, 'staging-owner.json') });
const shot = (page: any, n: string) => page.screenshot({ path: path.join(OUT, `${n}.png`), fullPage: true });

test('REA: upload a video, analyse it with scene descriptions, read per-shot descriptions, keyframes and the reconstructed prompt', async ({ page }) => {
  test.setTimeout(3_600_000);
  const clip = path.join(STG.fix, process.env.REA_CLIP ?? 'multishot.mp4');
  await page.goto('/experimental/rea');
  await expect(page.getByRole('heading', { name: /REA, video reverse engineer/ })).toBeVisible();
  await page.getByLabel('Choose a video').setInputFiles(clip);
  const go = page.getByTestId('rea-go');
  await expect(go).toBeEnabled({ timeout: 300_000 });
  await expect(page.getByLabel(/Describe the scenes with a vision model/)).toBeChecked();
  await shot(page, 'rea-1-ready');
  const t0 = Date.now();
  await go.click();
  const vis = page.getByTestId('rea-vision');
  await expect(vis).toBeVisible({ timeout: 3_000_000 });
  console.log('analysis visible after', Math.round((Date.now() - t0) / 1000), 's:', (await vis.innerText()).trim());
  const shots = page.getByTestId('rea-shot');
  const n = await shots.count();
  console.log('shots shown:', n);
  expect(n).toBeGreaterThan(0);
  // every keyframe really loads through /api/media/jobs/{id}/analysis/files/...
  const imgs = page.locator('[data-testid="rea-shot"] img');
  await expect(imgs.first()).toBeVisible();
  for (const img of await imgs.all()) {
    await img.scrollIntoViewIfNeeded();
    await expect.poll(() => img.evaluate((e: HTMLImageElement) => e.complete && e.naturalWidth > 0), { timeout: 30_000 }).toBe(true);
  }
  console.log('keyframes loaded:', await imgs.count());
  for (let i = 0; i < n; i++) console.log(`shot ${i + 1}:`, (await shots.nth(i).locator('.desc, p.muted').first().innerText()).slice(0, 300));
  const clipCard = page.getByTestId('rea-clip');
  if (await clipCard.count()) {
    const prompt = await page.getByLabel('Reconstructed prompt for the whole video').inputValue();
    console.log('reconstructed prompt:', prompt);
    expect(prompt.length).toBeGreaterThan(20);
    await clipCard.getByRole('button', { name: 'Copy prompt' }).click();
    await expect(page.getByText('Prompt copied')).toBeVisible();
  }
  const src = await imgs.first().getAttribute('src');
  const jid = decodeURIComponent((src ?? '').split('/')[4] ?? '');
  const doc = await page.request.get(`/api/media/jobs/${encodeURIComponent(jid)}/analysis`);
  expect(doc.ok()).toBe(true);
  writeFileSync(path.join(OUT, 'rea-analysis.json'), JSON.stringify(await doc.json(), null, 1));
  await page.setViewportSize({ width: 1280, height: 900 });
  await shot(page, 'rea-2-desktop');
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(400);
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  console.log('horizontal overflow at 390 px:', overflow);
  expect(overflow).toBeLessThanOrEqual(0);
  await shot(page, 'rea-3-mobile');
});
