// The ordinary chat composer: a quiet "+" instead of a permanent reference field, pictures by picker / drag and drop / paste as compact removable thumbnails,
// what each model can do with them said in words, and the iPhone Photo option of the picture operation. Real Chrome, the real server, the engine double with the
// image models' REAL capabilities (flux2-klein-4b takes 4 pictures, qwen-image-edit-2511 takes 3, z-image-turbo restyles from 1, qwen-image-2.1 cannot edit).
//
//   MEDIA_E2E_ENGINE_PORT=8881 MEDIA_E2E_APP_PORT=8882 MEDIA_E2E_TMP=.e2e-chat-tmp npx playwright test -c playwright.media.config.ts chat-attachments
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { TMP } from '../playwright.media.config';
import { cards, composer, drop, dragOver, engine, fixtures, resetEngine, sha, tray } from './helpers';

const F = fixtures();
const SCREENS = path.join(path.dirname(new URL(import.meta.url).pathname), 'screens');
test.use({ storageState: path.join(TMP, 'auth', 'owner.json'), actionTimeout: 15_000 });
test.describe.configure({ mode: 'serial' });

const shot = async (page: Page, name: string) => { await page.waitForTimeout(350); mkdirSync(SCREENS, { recursive: true }); return page.screenshot({ path: path.join(SCREENS, `chat-${name}.png`) }); };
const subs = async (page: Page) => (await engine(page)).image_submissions as any[];

test.beforeEach(async ({ page }) => {
  await resetEngine(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize({ width: 1280, height: 800 });
});

async function fresh(page: Page) { await page.goto('/chat?new=' + Date.now()); await expect(page.getByLabel('Message', { exact: true })).toBeVisible(); }
async function pictureOp(page: Page) { await page.getByRole('radio', { name: 'Picture' }).click(); }
async function chooseModel(page: Page, name: RegExp) {
  await page.getByRole('button', { name: /^(Video |Picture )?[Mm]odel: / }).click();
  await page.getByRole('listbox').getByRole('option', { name }).first().click();
}
async function settled(page: Page, n: number) {
  await expect(cards(page)).toHaveCount(n);
  await expect(tray(page).getByRole('progressbar')).toHaveCount(0, { timeout: 30_000 });
}
/** The browser's own paste event with files (what pasting a screenshot does). */
async function paste(page: Page, files: string[], target = 'window') {
  const payload = files.map((f) => ({ name: path.basename(f) === 'portrait-a.png' ? 'image.png' : path.basename(f), type: f.endsWith('.png') ? 'image/png' : 'image/jpeg', b64: readFileSync(f).toString('base64') }));
  return page.evaluate(({ payload, target }) => {
    const dt = new DataTransfer();
    for (const p of payload) { const bin = atob(p.b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); dt.items.add(new File([u], p.name, { type: p.type })); }
    const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    (target === 'window' ? window : (document.querySelector(target) as Element)).dispatchEvent(ev);
    return ev.defaultPrevented;
  }, { payload, target });
}

test('1 no permanent reference field: a quiet + beside the composer is the only way in, and nothing extra shows until something is attached', async ({ page }) => {
  await fresh(page);
  await expect(page.getByRole('button', { name: 'Add pictures, videos or files' })).toBeVisible();
  await expect(tray(page)).toHaveCount(0);
  for (const op of ['Picture', 'Video']) {
    await page.getByRole('radio', { name: op }).click();
    await expect(page.getByRole('button', { name: /^Add (picture to change|first frame|reference)/i })).toHaveCount(0);   // no "visible add cards" in the ordinary chat
    await expect(tray(page)).toHaveCount(0);
  }
  await page.getByRole('radio', { name: 'Chat' }).click();
  await page.getByLabel('Message', { exact: true }).fill('hello');
  // the composer stays a small box: the + and the model chooser share one slim bar
  const box = await composer(page).locator('.box').boundingBox();
  expect(box!.height).toBeLessThan(150);
  await shot(page, '01-desktop-light-empty');
});

test('2 + opens the picker: a real file chooser, then a compact thumbnail with a count', async ({ page }) => {
  await fresh(page);
  await page.getByRole('button', { name: 'Add pictures, videos or files' }).click();
  const chooser = page.waitForEvent('filechooser');
  await page.getByRole('menuitem', { name: /Upload from this device/ }).click();
  await (await chooser).setFiles([F.a]);
  await settled(page, 1);
  await expect(page.getByTestId('tray-count')).toHaveText('1 picture');
  const b = await cards(page).first().locator('.pic').boundingBox();
  expect(b!.width).toBeLessThanOrEqual(80); expect(b!.height).toBeLessThanOrEqual(80);       // compact, not a big card
  await expect(cards(page).first().locator('img')).toBeVisible();
  await expect.poll(() => cards(page).first().locator('img').evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  // the test's chat model reports no vision: the picture still goes with the message, and the thumbnail and a calm note say that only its name is read
  await expect(cards(page).first()).not.toHaveClass(/unused/);
  await expect(cards(page).first()).toContainText('Names only');
  await expect(page.getByRole('status').filter({ hasText: /cannot read pictures/ })).toBeVisible();
});

test('3 drag and drop: the target lights up, files land as thumbnails; paste: pictures attach, text still pastes', async ({ page }) => {
  await fresh(page);
  await dragOver(page, composer(page), [F.a, F.b]);
  await expect(page.getByText('Drop pictures to attach them')).toBeVisible();
  await drop(page, composer(page), [F.a, F.b]);
  await settled(page, 2);
  await expect(page.getByTestId('tray-count')).toHaveText('2 pictures');
  await expect(page.getByText('Drop pictures to attach them')).toHaveCount(0);
  // paste of a picture (a screenshot is named image.png) attaches and is not left to the textarea
  const prevented = await paste(page, [F.a]);
  expect(prevented).toBe(true);
  await settled(page, 3);
  await expect(cards(page).last()).toHaveAttribute('aria-label', /Pasted picture/);
  // several pictures in one paste
  await paste(page, [F.a, F.b]);
  await settled(page, 5);
  // text pasted into the box is never intercepted
  const text = await page.evaluate(() => {
    const dt = new DataTransfer(); dt.setData('text/plain', 'plain words');
    const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true });
    document.querySelector('#msg')!.dispatchEvent(ev);
    return ev.defaultPrevented;
  });
  expect(text).toBe(false);
  await expect(cards(page)).toHaveCount(5);
});

test('4 thumbnails are removable one by one or all at once, by mouse and keyboard', async ({ page }) => {
  await fresh(page);
  await drop(page, composer(page), [F.a, F.b, F.c]);
  await settled(page, 3);
  await expect(page.getByTestId('tray-count')).toHaveText('3 pictures');
  await cards(page).nth(1).getByRole('button', { name: /^Remove / }).click();
  await expect(cards(page)).toHaveCount(2);
  await expect(page.getByTestId('tray-count')).toHaveText('2 pictures');
  await cards(page).first().focus();
  await cards(page).first().getByRole('button', { name: /^Remove / }).focus();
  await page.keyboard.press('Enter');
  await expect(cards(page)).toHaveCount(1);
  await page.getByRole('button', { name: 'Remove all' }).click();
  await expect(tray(page)).toHaveCount(0);
  await expect(page.getByTestId('tray-count')).toHaveCount(0);
});

test('5 a model that cannot edit pictures: attachments stay, marked "not used", with a calm note; the send button says what happens', async ({ page }) => {
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /Qwen-Image 2\.1/);
  await drop(page, composer(page), [F.a, F.b]);
  await settled(page, 2);
  await expect(page.getByRole('status').filter({ hasText: /makes pictures from words only/ })).toBeVisible();
  for (const i of [0, 1]) { await expect(cards(page).nth(i)).toHaveClass(/unused/); await expect(cards(page).nth(i)).toContainText('Not used'); }
  await expect(cards(page).first()).toHaveAttribute('aria-label', /Not used\. .* cannot edit pictures/);
  await page.getByLabel('Message', { exact: true }).fill('a lighthouse');
  const send = page.getByRole('button', { name: /^Make picture/ });
  await expect(send).toBeEnabled();
  await expect(send).toContainText('Make picture');
  await shot(page, '05-desktop-light-unsupported-note');
  // sending does not send the pictures the model cannot use
  await send.click();
  await expect(page.locator('figure.res img')).toBeVisible({ timeout: 30_000 });
  const s = (await subs(page)).at(-1);
  expect(s.model).toBe('qwen-image-2.1'); expect(s.pictures ?? []).toHaveLength(0);
  await expect(tray(page)).toHaveCount(0);
});

test('6 a model that takes fewer pictures than attached: the extras are marked, never dropped silently or sent', async ({ page }) => {
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /Z-Image Turbo/);                    // restyles from exactly one picture
  await drop(page, composer(page), [F.a, F.b]);
  await settled(page, 2);
  await expect(cards(page).nth(0)).not.toHaveClass(/unused/);
  await expect(cards(page).nth(1)).toHaveClass(/unused/);
  await expect(cards(page).nth(1)).toHaveAttribute('aria-label', /takes one picture/);
  await expect(page.getByRole('status').filter({ hasText: /not used\. .* takes one picture/ })).toBeVisible();
  await page.getByLabel('Message', { exact: true }).fill('make it a watercolour');
  await expect(page.getByRole('button', { name: /^Edit picture/ })).toBeEnabled();
  await page.getByRole('button', { name: /^Edit picture/ }).click();
  await expect(page.locator('figure.res img')).toBeVisible({ timeout: 30_000 });
  const s = (await subs(page)).at(-1);
  expect(s.model).toBe('z-image-turbo'); expect(s.pictures).toHaveLength(1);
  expect(s.pictures[0].sha256).toBe(sha(F.a));
});

test('7 a model that edits with several pictures uses them all, in order: picture to change first, then references', async ({ page }) => {
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /FLUX\.2 klein/);
  await drop(page, composer(page), [F.a, F.b, F.c]);
  await settled(page, 3);
  for (const i of [0, 1, 2]) await expect(cards(page).nth(i)).not.toHaveClass(/unused/);
  await expect(cards(page).nth(0)).toContainText('To edit');
  await expect(cards(page).nth(1)).toContainText('Reference');
  await expect(cards(page).nth(2).locator('.ord')).toHaveText('3');
  await page.getByLabel('Message', { exact: true }).fill('Put the person of image 2 into the scene of image 1');
  await expect(page.getByRole('button', { name: /^Edit picture/ })).toBeEnabled();
  await shot(page, '07-desktop-light-three-attachments');
  await page.getByRole('button', { name: /^Edit picture/ }).click();
  await expect(page.locator('figure.res img')).toBeVisible({ timeout: 30_000 });
  const s = (await subs(page)).at(-1);
  expect(s.model).toBe('flux2-klein-4b'); expect(s.engine_mode).toBe('edit');
  expect(s.pictures.map((p: any) => p.sha256).length).toBe(3);
  expect(s.pictures[0].sha256).toBe(sha(F.a));
  // the user's message shows all three, and the extra pictures survive
  await expect(page.locator('article.msg.user').last().locator('.ac img')).toHaveCount(3);
});

test('8 reordering decides which picture is edited and which are references', async ({ page }) => {
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /FLUX\.2 klein/);
  await drop(page, composer(page), [F.a, F.b]);
  await settled(page, 2);
  await cards(page).nth(1).focus();
  await page.keyboard.press('Alt+ArrowLeft');
  await expect(cards(page).first()).toHaveAttribute('aria-label', /portrait-b/);
  await expect(cards(page).first()).toContainText('To edit');
  await expect(cards(page).nth(1)).toContainText('Reference');
  await page.getByLabel('Message', { exact: true }).fill('swap the faces');
  await page.getByRole('button', { name: /^Edit picture/ }).click();
  await expect(page.locator('figure.res img')).toBeVisible({ timeout: 30_000 });
  const s = (await subs(page)).at(-1);
  expect(s.pictures[0].sha256).toBe(sha(F.b)); expect(s.pictures[1].sha256).toBe(sha(F.a));
});

test('9 iPhone Photo in the chat: only with Picture selected, 9:16 720x1280, the shared prompt builder, the routed model, clearly labelled', async ({ page }) => {
  await fresh(page);
  await expect(page.getByRole('button', { name: /^iPhone Photo/ })).toHaveCount(0);       // not offered for chat
  await pictureOp(page);
  const chip = page.getByRole('button', { name: /^iPhone Photo/ });
  await expect(chip).toBeVisible();
  await chip.click();
  await expect(chip).toHaveAttribute('aria-pressed', 'true');
  const line = page.locator('.presetline');
  await expect(line).toContainText('iPhone Photo');
  await expect(line).toContainText('9:16');
  await expect(line).toContainText('720 × 1280');
  // the model is the one the routing table recommends for the preset (the server's default quality level), whatever the table says today
  const caps = (await (await page.request.get('/api/imajutsu/capabilities')).json()).presets[0];
  const def = caps.tiers.find((x: any) => x.default);
  await expect(line).toContainText(def.label);
  await page.getByRole('button', { name: 'Options' }).click();
  const quality = page.getByRole('radiogroup', { name: /Quality/ });
  await expect(quality.getByRole('radio', { name: def.label })).toHaveAttribute('aria-checked', 'true');
  await quality.getByRole('radio', { name: 'Fast' }).click();
  await expect(line).toContainText('Fast');
  // a scene hint and a prompt
  await page.getByRole('button', { name: 'Night' }).click();
  await page.getByLabel('Message', { exact: true }).fill('two friends at a bus stop');
  await shot(page, '09-desktop-light-iphone-photo');
  await page.getByRole('button', { name: /^Make iPhone Photo/ }).click();
  await expect(page.locator('figure.res img')).toBeVisible({ timeout: 30_000 });
  const s = (await subs(page)).at(-1);
  expect(s.model).toBe('flux2-klein-4b');
  expect([s.simple.width, s.simple.height]).toEqual([720, 1280]);
  expect(s.simple.prompt).toContain('two friends at a bus stop'); expect(s.simple.prompt).toContain('street lights'); expect(s.simple.prompt).toContain('unedited handheld phone photo');
  // the result message carries the label; the person's own words stay as they wrote them
  await expect(page.locator('article.msg.user').last()).toContainText('iPhone Photo');
  await expect(page.locator('article.msg.user').last()).toContainText('two friends at a bus stop');
});

test('10 iPhone Photo: Polished quality switches to its model with plain wording; attached pictures are marked not used; off restores the earlier model', async ({ page }) => {
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /Qwen-Image 2512/);
  await page.getByRole('button', { name: /^iPhone Photo/ }).click();
  await page.getByRole('button', { name: 'Options' }).click();
  await page.getByRole('radiogroup', { name: /Quality/ }).getByRole('radio', { name: 'Polished' }).click();
  await expect(page.locator('.presetline')).toContainText('Polished');
  await drop(page, composer(page), [F.a]);
  await settled(page, 1);
  await expect(cards(page).first()).toHaveClass(/unused/);
  await expect(page.getByRole('status').filter({ hasText: /iPhone Photo makes a new picture from words/ }).first()).toBeVisible();
  await page.getByLabel('Message', { exact: true }).fill('a red car parked on a street');
  await page.getByRole('button', { name: /^Make iPhone Photo/ }).click();
  await expect(page.locator('figure.res img')).toBeVisible({ timeout: 30_000 });
  const s = (await subs(page)).at(-1);
  expect(s.model).toBe('z-image-turbo'); expect(s.pictures ?? []).toHaveLength(0);                 // the preset does not send pictures
  const p = s.simple.prompt.toLowerCase();
  expect(p).toContain('a photograph of a red car'); for (const w of ['phone', '9:16', 'selfie']) expect(p).not.toContain(w);
  // switching the preset off brings the earlier model back
  await page.getByRole('button', { name: /^iPhone Photo/ }).click();
  await expect(page.locator('.presetline')).toHaveCount(0);
  await expect(page.getByRole('button', { name: /^Picture model: / })).toContainText(/Qwen-Image 2512/);
});

test('11 /chat?op=image&preset=iphone_photo opens ready for it, and Imajutsu still offers the same preset', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now() + '&op=image&preset=iphone_photo');
  await expect(page.locator('.presetline')).toContainText('720 × 1280');
  await page.goto('/imajutsu?new=1');
  await expect(page.getByTestId('start')).toBeVisible();
  await expect(page.getByTestId('modes').getByRole('radio', { name: /^iPhone Photo/ })).toBeVisible();
});

test('12 phone width and dark theme: the composer, the tray and the notes fit and read well', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /Z-Image Turbo/);
  await drop(page, composer(page), [F.a, F.b, F.c]);
  await settled(page, 3);
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  await shot(page, '12-mobile-light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('body')).toBeVisible();
  await shot(page, '13-mobile-dark');
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.reload();
  await expect(page.getByRole('list', { name: 'Attached files' })).toBeVisible();
  await page.getByLabel('Message', { exact: true }).fill('make it a watercolour');
  await shot(page, '14-desktop-dark');
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: /^iPhone Photo/ }).click();
  await page.getByRole('button', { name: 'Options' }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(1);
  await shot(page, '15-mobile-iphone-photo');
});

test('13 accessibility: the compact tray, the not-used notes and the iPhone Photo options have no serious violations, light and dark', async ({ page }) => {
  const { default: AxeBuilder } = await import('@axe-core/playwright');
  const serious = async () => (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
  await fresh(page);
  await pictureOp(page);
  await chooseModel(page, /Z-Image Turbo/);
  await drop(page, composer(page), [F.a, F.b]);
  await settled(page, 2);
  for (const theme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.waitForTimeout(300);
    expect(await serious(), `${theme}: tray and note`).toEqual([]);
  }
  await page.emulateMedia({ colorScheme: 'light' });
  await page.getByRole('button', { name: /^iPhone Photo/ }).click();
  await page.getByRole('button', { name: 'Options' }).click();
  for (const theme of ['light', 'dark'] as const) {
    await page.emulateMedia({ colorScheme: theme });
    await page.waitForTimeout(300);
    expect(await serious(), `${theme}: iPhone Photo options`).toEqual([]);
  }
});
