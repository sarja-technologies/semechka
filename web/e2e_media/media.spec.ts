import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { TMP } from '../playwright.media.config';
import { cards, composer, drop, dragOver, engine, fixtures, resetEngine, sha, sha256, shot, tray, waitReady } from './helpers';

const F = fixtures();
test.describe.configure({ mode: 'serial' });
test.use({ storageState: path.join(TMP, 'auth', 'owner.json') });

async function openOpenJutsu(page: Page, op?: string) {
  await page.goto('/chat?new=' + Date.now());
  await page.getByRole('radio', { name: 'Video' }).click();
  await page.getByRole('button', { name: /model: /i }).click();
  await page.getByRole('listbox').getByRole('option', { name: /OpenJutsu/ }).first().click();
  const g = page.getByRole('radiogroup', { name: /Operation/ });
  await expect(g).toBeVisible();
  if (op) await g.getByRole('radio', { name: op }).click();
}
const api = async (page: Page, p: string) => (await page.request.get(p)).json();
const csrf = async (page: Page) => (await api(page, '/api/me')).csrf as string;
function probe(buf: Buffer) {
  const f = path.join(TMP, `probe-${Date.now()}.bin`); writeFileSync(f, buf);
  const j = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', f]).toString());
  const v = j.streams.find((s: any) => s.codec_type === 'video');
  return { w: +v.width, h: +v.height, d: +j.format.duration, audio: j.streams.some((s: any) => s.codec_type === 'audio') };
}
const content = async (page: Page, id: string) => Buffer.from(await (await page.request.get(`/api/media/assets/${id}/content`)).body());
const viewer = (page: Page) => page.getByRole('dialog', { name: /harbour|portrait|phone|loop|edited/ });
const editedAssets = async (page: Page) => (await api(page, '/api/media/assets?origin=edit')) as any[];

test('1 drag and drop an image: the drop target lights up and the picture lands in the composer tray as a real thumbnail', async ({ page }) => {
  await openOpenJutsu(page);
  await dragOver(page, composer(page), [F.a]);
  await expect(page.getByText('Drop videos and reference pictures')).toBeVisible();
  await page.waitForTimeout(350);   // the veil fades in
  await shot(page, '01a-drop-target');
  await page.locator('.veil').dispatchEvent('dragenter');   // the pointer is over the target itself: it firms up
  await page.waitForTimeout(250);
  await shot(page, '01a2-drop-target-hot');
  await drop(page, composer(page), [F.a]);
  await waitReady(page, 1);
  const img = cards(page).first().locator('img');
  await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await expect(cards(page).first().getByRole('combobox')).toHaveValue('refs');
  await expect(page.getByText('Drop videos and reference pictures')).toHaveCount(0);
  await shot(page, '01b-image-in-tray');
});

test('2 drag and drop a video: thumbnail, duration, role Source video', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip]);
  await waitReady(page, 1);
  const c = cards(page).first();
  await expect(c.getByRole('combobox')).toHaveValue('video');
  await expect(c.locator('.dur')).toHaveText('0:06');
  await expect(c.getByRole('button', { name: /^Play / })).toBeVisible();
  await expect.poll(() => c.locator('img').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await shot(page, '02-video-in-tray');
});

test('3 several reference images at once, in order, with numbers; 4 they are previewed inside the composer', async ({ page }) => {
  await openOpenJutsu(page, 'Advanced swap');
  await drop(page, composer(page), [F.clip, F.a, F.b, F.c]);
  await waitReady(page, 4);
  for (const c of await cards(page).all()) await expect.poll(() => c.locator('img').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await expect(cards(page).nth(1).getByRole('combobox')).toHaveValue('refs');
  await expect(cards(page).locator('.ord')).toHaveText(['1', '2', '3']);
  // the prompt box stays right under the tray
  const t = await tray(page).boundingBox(), box = await page.getByLabel('Message', { exact: true }).boundingBox();
  expect(box!.y - (t!.y + t!.height)).toBeLessThan(60);
  await shot(page, '03-four-attachments');
  // order can be changed
  await cards(page).nth(3).focus();
  await page.keyboard.press('Alt+ArrowLeft');
  await expect(cards(page).nth(2)).toHaveAttribute('aria-label', /portrait-c/);
});

test('5 a video plays right in the attachment card', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip]);
  await waitReady(page, 1);
  await cards(page).first().getByRole('button', { name: /^Play / }).click();
  const v = cards(page).first().locator('video');
  await expect(v).toBeVisible();
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.currentTime)).toBeGreaterThan(0.3);
  await shot(page, '05-inline-play');
  await v.evaluate((x: HTMLVideoElement) => x.pause());
});

test('6 clicking the video opens the full player; keyboard and controls work', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip]);
  await waitReady(page, 1);
  await cards(page).first().getByRole('button', { name: /^Open / }).click({ position: { x: 12, y: 62 } });
  const dlg = viewer(page);
  await expect(dlg).toBeVisible();
  const v = dlg.locator('video');
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.readyState)).toBeGreaterThanOrEqual(2);
  await expect(dlg.getByLabel('Current time')).toHaveText('0:00');
  await page.keyboard.press(' ');
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.currentTime)).toBeGreaterThan(0.3);
  await page.keyboard.press(' ');
  const t0 = await v.evaluate((x: HTMLVideoElement) => x.currentTime);
  await page.keyboard.press('.');   // one frame forward
  expect(await v.evaluate((x: HTMLVideoElement) => x.currentTime)).toBeGreaterThan(t0);
  await dlg.getByRole('slider', { name: 'Seek' }).fill('3');
  await expect(dlg.getByLabel('Current time')).toHaveText('0:03');
  await dlg.getByLabel('Volume').fill('0.3');
  expect(await v.evaluate((x: HTMLVideoElement) => Math.round(x.volume * 10))).toBe(3);
  await shot(page, '06-viewer');
  await page.keyboard.press('Escape');
  await expect(dlg).toHaveCount(0);
});

test('7 trim a real video: exact times, new file, original untouched', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip]);
  await waitReady(page, 1);
  await cards(page).first().getByRole('button', { name: /^Edit / }).click();
  const dlg = viewer(page);
  await expect(dlg.getByRole('slider', { name: 'Trim start' })).toBeVisible();
  await expect.poll(() => dlg.locator('img.strip').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 100)).toBe(true);
  await dlg.getByLabel('Start', { exact: true }).fill('1.5'); await page.keyboard.press('Enter');
  await dlg.getByLabel('End', { exact: true }).fill('4.5'); await page.keyboard.press('Enter');
  await expect(dlg.getByText(/0:03\.00 of 0:06\.00/)).toBeVisible();
  await shot(page, '07a-trim');
  const before = (await editedAssets(page)).length;
  await dlg.getByRole('button', { name: 'Save copy' }).click();
  await expect.poll(async () => (await editedAssets(page)).length, { timeout: 60_000 }).toBe(before + 1);
  const made = (await editedAssets(page))[0];
  expect(made.edit.trim).toEqual([1.5, 4.5]);
  expect(Math.abs(made.duration - 3)).toBeLessThan(0.15);
  const p = probe(await content(page, made.id));
  expect(p.w).toBe(640); expect(Math.abs(p.d - 3)).toBeLessThan(0.15); expect(p.audio).toBe(true);
  // the original is still the file that was dropped
  const orig = (await api(page, '/api/media/assets?origin=upload')).find((a: any) => a.id === made.parent);
  expect(sha256(await content(page, orig.id))).toBe(sha(F.clip));
});

test('8 crop a real video with a preset: real pixels change', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip]);
  await waitReady(page, 1);
  await cards(page).first().getByRole('button', { name: /^Edit / }).click();
  const dlg = viewer(page);
  await dlg.getByRole('button', { name: '1:1' }).click();
  await expect(dlg.locator('.out')).toContainText('360 × 360');
  await page.waitForTimeout(400);
  await shot(page, '08a-crop-1x1');
  await dlg.getByRole('button', { name: 'Preview' }).click();
  await shot(page, '08b-crop-preview');
  await dlg.getByRole('button', { name: 'Preview' }).click();
  const before = (await editedAssets(page)).length;
  await dlg.getByRole('button', { name: 'Save copy' }).click();
  await expect.poll(async () => (await editedAssets(page)).length, { timeout: 60_000 }).toBe(before + 1);
  const made = (await editedAssets(page))[0];
  expect([made.width, made.height]).toEqual([360, 360]);
  const p = probe(await content(page, made.id));
  expect([p.w, p.h]).toEqual([360, 360]);
  expect(Math.abs(p.d - 6)).toBeLessThan(0.3);
});

let editedId = '';
test('9+10 trim and crop together, use it as the source, and OpenJutsu receives exactly that file', async ({ page }) => {
  await resetEngine(page);
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip, F.a]);
  await waitReady(page, 2);
  await cards(page).first().getByRole('button', { name: /^Edit / }).click();
  const dlg = viewer(page);
  await dlg.getByLabel('Start', { exact: true }).fill('1'); await page.keyboard.press('Enter');
  await dlg.getByLabel('End', { exact: true }).fill('4'); await page.keyboard.press('Enter');
  await dlg.getByRole('button', { name: '9:16' }).click();
  await expect(dlg.locator('.out')).toContainText(/202 × 360 · 0:03\.00/);
  await dlg.getByRole('button', { name: 'Use as source' }).click();
  await expect(dlg).toHaveCount(0, { timeout: 60_000 });
  // the attachment now IS the edited file, in the same place, with the same role
  const c = cards(page).first();
  await expect(c).toHaveAttribute('aria-label', /\(edited\)/);
  await expect(c.getByRole('combobox')).toHaveValue('video');
  await expect(c.locator('.dur')).toHaveText('0:03');
  await shot(page, '09-edited-in-tray');
  const made = (await editedAssets(page))[0];
  editedId = made.id;
  expect([made.width, made.height]).toEqual([202, 360]);
  // send it
  await page.getByLabel('Message', { exact: true }).fill('the singer');
  await page.getByRole('button', { name: /^Make video/ }).click();
  await expect.poll(async () => (await engine(page)).submissions.length, { timeout: 60_000 }).toBe(1);
  const st = await engine(page);
  const sub = st.submissions[0];
  expect(sub.profile).toBe('openjutsu-minimax-h3');
  expect(sub.inputs.operation).toBe('swap'); expect(sub.inputs.prompt).toBe('the singer');
  const got = st.uploads[sub.inputs.video];
  expect(got.sha256).toBe(sha256(await content(page, made.id)));          // the engine received the edited file, byte for byte
  const refs = Array.isArray(sub.inputs.refs) ? sub.inputs.refs : [sub.inputs.refs];
  expect(refs).toHaveLength(1);
  expect(st.uploads[refs[0]].sha256).toBe(sha(F.a));
  // the message shows its attachments as cards and the tray is empty again
  await expect(cards(page)).toHaveCount(0);
  await expect(page.locator('article.msg.user .ac')).toHaveCount(2);
  await expect(page.locator('article.msg.assistant video')).toBeVisible({ timeout: 60_000 });
  await shot(page, '10-sent');
});

test('11 reopen an existing asset from the Library, play it and edit it', async ({ page }) => {
  await page.goto('/library');
  await expect(page.getByRole('button', { name: /^Open .*edited/ }).first()).toBeVisible();
  await shot(page, '11a-library');
  await page.getByRole('button', { name: /^Open .*harbour/ }).first().click();
  const dlg = viewer(page);
  const v = dlg.locator('video');
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.readyState)).toBeGreaterThanOrEqual(2);
  await page.keyboard.press('k');
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.currentTime)).toBeGreaterThan(0.2);
  await page.keyboard.press('Escape');
  // a generated result is previewable too
  await page.getByRole('radio', { name: 'Generated' }).click();
  await expect(page.getByText('Generated').first()).toBeVisible();
  await shot(page, '11b-library-generated');
});

test('12 attachments survive navigation and a reload', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip, F.b]);
  await waitReady(page, 2);
  await page.getByRole('link', { name: 'Library' }).click();
  await expect(page).toHaveURL(/\/library/);
  await page.goBack();
  await expect(cards(page)).toHaveCount(2);
  await page.getByLabel('Message', { exact: true }).fill('the one in the red coat');
  await page.waitForTimeout(400);
  await page.reload();
  // the files, their roles, the operation, the model and the words typed are all still here
  await expect(page.getByRole('radiogroup', { name: /Operation/ })).toBeVisible();
  await expect(cards(page)).toHaveCount(2);
  await expect(page.getByLabel('Message', { exact: true })).toHaveValue('the one in the red coat');
  await expect(cards(page).first().getByRole('combobox')).toHaveValue('video');
  await expect(cards(page).first().locator('img')).toBeVisible();
});

test('16 changing the operation keeps every attachment; incompatible combinations are explained', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip, F.a, F.b]);
  await waitReady(page, 3);
  const send = page.getByRole('button', { name: /^Make video/ });
  // swap takes one reference picture: two are too many
  await expect(page.getByText(/Only one reference picture can be used for swap: 2 are attached/)).toBeVisible();
  await expect(send).toBeDisabled();
  await shot(page, '16a-incompatible');
  await composer(page).getByRole('radio', { name: 'Check mask' }).click();
  await expect(cards(page)).toHaveCount(3);
  await expect(cards(page).nth(1).locator('.sub')).toHaveText('Not used by check mask');
  await expect(cards(page).nth(1)).toHaveClass(/unused/);
  await expect(send).toBeEnabled();
  await shot(page, '16b-check-mask');
  await composer(page).getByRole('radio', { name: 'Advanced swap' }).click();
  await expect(cards(page)).toHaveCount(3);
  await expect(cards(page).nth(1)).not.toHaveClass(/unused/);
  await expect(send).toBeEnabled();
  await composer(page).getByRole('radio', { name: 'Object edit' }).click();
  await expect(page.getByText(/Only one reference picture can be used for object edit/)).toBeVisible();
  await composer(page).getByRole('radio', { name: 'Swap', exact: true }).click();
  await cards(page).nth(2).getByRole('button', { name: /^Remove / }).click();
  await expect(page.getByText(/Only one reference picture/)).toHaveCount(0);
  await expect(send).toBeEnabled();
});

test('13 invalid files, over-size files and a clear message', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.junk]);
  const c = cards(page).first();
  await expect(c.getByRole('alert')).toContainText(/could not be read/i, { timeout: 30_000 });
  await expect(c.getByRole('button', { name: /^Retry / })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Make video/ })).toBeDisabled();
  await shot(page, '13a-invalid');
  await c.getByRole('button', { name: /^Remove / }).click();
  await expect(cards(page)).toHaveCount(0);
  // the server refuses a file over its limit before storing it
  const token = await csrf(page);
  const big = await page.request.put('/api/admin/settings/media.upload_max_mb', { data: { value: 1 }, headers: { 'x-csrf-token': token } });
  expect(big.ok()).toBeTruthy();
  try {
    const r = await page.request.post('/api/media/uploads?name=big.mp4', { data: readFileSync(F.big).subarray(0, 3 * 1048576), headers: { 'x-csrf-token': token, 'content-type': 'application/octet-stream' } });
    expect(r.status()).toBe(413);
    expect((await r.json()).detail.message).toMatch(/larger than the 1 MB/);
    await page.reload();
    await page.getByRole('radio', { name: 'Video' }).click();
    await drop(page, composer(page), [F.big]);
    await expect(page.getByText(/larger than the 1 MB/)).toBeVisible();
    await expect(cards(page)).toHaveCount(0);
    await shot(page, '13b-too-large');
  } finally {
    await page.request.put('/api/admin/settings/media.upload_max_mb', { data: { value: 512 }, headers: { 'x-csrf-token': token } });
  }
});

test('14 cancelling an upload leaves nothing behind', async ({ page }) => {
  await openOpenJutsu(page);
  const before = (await api(page, '/api/media/assets')).length;
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: 400 * 1024 });
  await drop(page, composer(page), [F.big]);
  const bar = tray(page).getByRole('progressbar');
  await expect(bar).toBeVisible();
  await expect.poll(async () => +(await bar.getAttribute('aria-valuenow')!)).toBeGreaterThan(0);
  await shot(page, '14a-uploading');
  await page.getByRole('button', { name: /^Cancel upload of big/ }).click();
  await expect(cards(page)).toHaveCount(0);
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 0, downloadThroughput: -1, uploadThroughput: -1 });
  await page.waitForTimeout(1500);
  expect((await api(page, '/api/media/assets')).length).toBe(before);
  const tmp = path.join(TMP, 'data', 'tmp', 'media');
  expect(existsSync(tmp) ? readdirSync(tmp).filter((n) => n.startsWith('w-')) : []).toEqual([]);
});

test('15 desktop and mobile layouts', async ({ page }) => {
  for (const [name, w, h] of [['desktop', 1280, 800], ['tablet', 820, 1100], ['phone', 390, 844]] as const) {
    await page.setViewportSize({ width: w, height: h });
    await page.goto('/library'); await page.evaluate(() => sessionStorage.clear());
    await openOpenJutsu(page);
    await drop(page, composer(page), [F.clip, F.a, F.b]);
    await waitReady(page, 3);
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over, `${name} scrolls sideways`).toBeLessThanOrEqual(1);
    await shot(page, `15-${name}-composer`);
    await cards(page).first().getByRole('button', { name: /^Edit / }).click({ force: true });
    await expect(viewer(page)).toBeVisible();
    await expect(viewer(page).getByRole('slider', { name: 'Trim start' })).toBeVisible();
    const vo = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(vo).toBeLessThanOrEqual(1);
    await page.waitForTimeout(400);
    await shot(page, `15-${name}-editor`);
    await page.keyboard.press('Escape');
    await page.goto('/library');
    await expect(page.getByRole('button', { name: /^Open / }).first()).toBeVisible();
    await shot(page, `15-${name}-library`);
  }
});

test('17 pictures attached to an ordinary chat message use the same library path and show as real cards', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now());
  await drop(page, composer(page), [F.b]);
  await waitReady(page, 1);
  await expect(cards(page).first().getByRole('combobox')).toHaveValue('attach');
  await page.getByLabel('Message', { exact: true }).fill('what is in this picture?');
  await page.getByRole('button', { name: /^Send/ }).click();
  const user = page.locator('article.msg.user').last();
  await expect(user.locator('.ac img')).toBeVisible();
  await expect.poll(() => user.locator('.ac img').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await expect(page.locator('article.msg.assistant').last()).toBeVisible({ timeout: 30_000 });
  await expect(page).toHaveURL(/\/chat\/[\w-]+$/);
  await user.locator('.ac button').click();
  await expect(page.getByRole('dialog', { name: /portrait-b/ })).toBeVisible();
  await page.keyboard.press('Escape');
  // it survives a reload as part of the saved conversation
  await page.reload();
  await expect(page.locator('article.msg.user .ac img').first()).toBeVisible();
});

test('18 a generated picture is previewable in the viewer and reusable as a source', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now());
  await page.getByRole('radio', { name: 'Picture' }).click();
  await page.getByLabel('Message', { exact: true }).fill('a red bicycle');
  await page.getByRole('button', { name: /^Make picture/ }).click();
  const fig = page.locator('figure.res img');
  await expect(fig).toBeVisible({ timeout: 30_000 });
  await fig.click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /Use as source/ }).click();
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first().locator('img')).toBeVisible();
});

test('19 the Library hands a file to a new message, and the files can be dropped on the Library page', async ({ page }) => {
  await page.goto('/library');
  await page.getByRole('radio', { name: 'Uploaded' }).click();
  const before = (await api(page, '/api/media/assets?origin=upload')).length;
  await drop(page, page.locator('.lib'), [F.webm]);
  await expect(page.getByRole('button', { name: /^Open loop/ })).toBeVisible({ timeout: 30_000 });
  expect((await api(page, '/api/media/assets?origin=upload')).length).toBe(before + 1);
  await page.getByRole('button', { name: /^Open loop/ }).locator('xpath=ancestor::li').getByRole('button', { name: 'Use' }).click();
  await expect(page).toHaveURL(/\/chat/);
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first()).toHaveAttribute('aria-label', /loop\.webm/);
  await expect(page.getByRole('radiogroup', { name: 'What to make' }).getByRole('radio', { name: 'Video' })).toHaveAttribute('aria-checked', 'true');
});

test('20 other formats: MOV and WebM play, no-save uploads stay in memory', async ({ page }) => {
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.mov, F.webm]);
  await waitReady(page, 2);
  for (const c of await cards(page).all()) {
    await c.getByRole('button', { name: /^Play / }).click();
    const v = c.locator('video');
    await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.currentTime)).toBeGreaterThan(0.2);
    await v.evaluate((x: HTMLVideoElement) => x.pause());
  }
  // a no-save conversation keeps its uploads in server memory only
  await page.evaluate(() => sessionStorage.clear());
  await page.goto('/chat?new=' + Date.now() + '&nosave=1');
  await page.getByRole('radio', { name: 'Video' }).click();
  await drop(page, composer(page), [F.mov]);
  await waitReady(page, 1);
  const list = await api(page, '/api/media/assets?origin=upload');
  expect(list.some((a: any) => a.id.startsWith('me_') && a.saved === false)).toBe(true);
});

test('21 accessibility: the composer with attachments and the open viewer have no serious violations', async ({ page }) => {
  const { default: AxeBuilder } = await import('@axe-core/playwright');
  await page.goto('/library'); await page.evaluate(() => sessionStorage.clear());
  await openOpenJutsu(page);
  await drop(page, composer(page), [F.clip, F.a]);
  await waitReady(page, 2);
  const serious = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  expect((await serious()).map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  await cards(page).first().getByRole('button', { name: /^Edit / }).click();
  await expect(viewer(page).getByRole('slider', { name: 'Trim start' })).toBeVisible();
  await page.waitForTimeout(400);
  expect((await serious()).map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
});

test('22 add from the Library through the + menu', async ({ page }) => {
  await openOpenJutsu(page);
  await page.getByRole('button', { name: 'Add pictures, videos or files' }).click();
  await expect(page.getByRole('menuitem')).toHaveCount(2);
  await shot(page, '22a-add-menu');
  await page.keyboard.press('ArrowDown');
  await page.getByRole('menuitem', { name: /Library/ }).click();
  const dlg = page.getByRole('dialog', { name: 'Add from your Library' });
  await expect(dlg).toBeVisible();
  await dlg.getByRole('radio', { name: 'Edited' }).click();
  const first = dlg.locator('.it').first();
  await expect(first).toBeVisible();
  await expect.poll(() => first.locator('img').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await shot(page, '22b-picker');
  await first.click();
  await dlg.getByRole('button', { name: /^Attach/ }).click();
  await expect(cards(page)).toHaveCount(1);
  await expect(cards(page).first().getByRole('combobox')).toHaveValue('video');
  await expect(cards(page).first()).toHaveAttribute('aria-label', /edited/);
});
