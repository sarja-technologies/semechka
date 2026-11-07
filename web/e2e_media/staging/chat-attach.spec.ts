// The ordinary chat composer on a REAL instance with the real GPU: a quiet "+" and nothing else permanent, pictures by drag and drop and by paste as
// compact removable thumbnails, then a real edit with two pictures (the first is edited, the second is a reference) and the real result.
//   STAGING_URL=http://192.168.20.193:8788 STAGING_EMAIL=... STAGING_PW=... STAGING_FIX=~/oj-staging-out/fix STAGING_OUT=~/oj-staging-out/real-salted \
//     npx playwright test -c playwright.staging.config.ts staging/chat-attach
import { test, expect, type Page } from '@playwright/test';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { cards, composer, drop, tray } from '../helpers';
import { STG } from '../../playwright.staging.config';

const OUT = process.env.STAGING_OUT ?? '.';
const F = (n: string) => path.join(STG.fix, n);
test.use({ storageState: path.join(OUT, 'staging-owner.json') });
test.describe.configure({ mode: 'serial' });
const shot = async (page: Page, n: string) => { await page.waitForTimeout(400); return page.screenshot({ path: path.join(OUT, `chat-${n}.png`) }); };

async function settled(page: Page, n: number) {
  await expect(cards(page)).toHaveCount(n, { timeout: 60_000 });
  await expect(tray(page).getByRole('progressbar')).toHaveCount(0, { timeout: 60_000 });
}
async function paste(page: Page, file: string) {
  const b64 = readFileSync(file).toString('base64');
  return page.evaluate((b64) => {
    const bin = atob(b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    const dt = new DataTransfer(); dt.items.add(new File([u], 'image.png', { type: 'image/png' }));
    const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    window.dispatchEvent(ev); return ev.defaultPrevented;
  }, b64);
}

test('a quiet + is the only way in: no permanent reference field in Picture or Video', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now());
  await expect(page.getByLabel('Message', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Add pictures, videos or files' })).toBeVisible();
  await expect(tray(page)).toHaveCount(0);
  for (const op of ['Picture', 'Video']) {
    await page.getByRole('radio', { name: op }).click();
    await expect(page.getByRole('button', { name: /^Add (picture to change|first frame|reference)/i })).toHaveCount(0);
    await expect(tray(page)).toHaveCount(0);
  }
  await page.getByRole('radio', { name: 'Picture' }).click();
  await shot(page, '1-picture-quiet');
});

test('drag and drop two pictures, paste a third, remove it; the first is edited, the second is a reference', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now());
  await page.getByRole('radio', { name: 'Picture' }).click();
  await page.getByRole('button', { name: /^(Video |Picture )?[Mm]odel: / }).click();
  await page.getByRole('listbox').getByRole('option', { name: /klein/i }).first().click();
  await drop(page, composer(page), [F('klein.png'), F('qwen.png')]);
  await settled(page, 2);
  await expect(page.getByTestId('tray-count')).toHaveText('2 pictures');
  await expect(cards(page).nth(0)).toContainText('To edit');
  await expect(cards(page).nth(1)).toContainText('Reference');
  expect(await paste(page, F('edit.png'))).toBe(true);
  await settled(page, 3);
  await shot(page, '2-three-thumbnails');
  await cards(page).nth(2).getByRole('button', { name: /^Remove/ }).click();
  await settled(page, 2);
  await page.getByLabel('Message', { exact: true }).fill('Put the person from image 2 into the street scene of image 1, same lighting');
  await expect(page.getByRole('button', { name: /^Edit picture/ })).toBeEnabled();
  const t0 = Date.now();
  await page.getByRole('button', { name: /^Edit picture/ }).click();
  await expect(page.locator('figure.res img').last()).toBeVisible({ timeout: 900_000 });
  console.log(`CHAT-EDIT real result in ${Math.round((Date.now() - t0) / 1000)}s`);
  await expect(page.locator('article.msg.user').last().locator('.ac img')).toHaveCount(2);
  const src = await page.locator('figure.res img').last().getAttribute('src');
  if (src) { const r = await page.request.get(src.startsWith('http') ? src : new URL(src, STG.url).toString()); if (r.ok()) writeFileSync(path.join(OUT, 'chat-edit-result.png'), Buffer.from(await r.body())); }
  await shot(page, '3-edit-result');
});
