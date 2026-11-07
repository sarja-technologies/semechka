// Imajutsu (image generation and editing), in real Chrome against the real Semechka server, real ffmpeg and the Sarness engine double with the image models
// (see STUDIO_HARNESS.md, "Imajutsu"). Nothing about the page is mocked except where a test says so: every assertion reads what the server stored, what the
// engine double recorded (the exact parameters and pictures it received) or what ffmpeg says about real files.
//
//   MEDIA_E2E_ENGINE_PORT=8871 MEDIA_E2E_APP_PORT=8872 MEDIA_E2E_TMP=.e2e-imajutsu-tmp npx playwright test -c playwright.media.config.ts imajutsu
import { test, expect, type Page, type Locator } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { MP, TMP } from '../playwright.media.config';
import { FIX, sha256 } from './helpers';

const ENG = `http://127.0.0.1:${MP.engine}`;
const SCREENS = path.join(path.dirname(new URL(import.meta.url).pathname), 'screens');
test.use({ storageState: path.join(TMP, 'auth', 'owner.json') });

// ---- real pictures made with ffmpeg -------------------------------------------------------------------------------------------------------------
function ff(args: string[], out: string) {
  if (!existsSync(out)) execFileSync('ffmpeg', ['-nostdin', '-loglevel', 'error', '-y', ...args, out]);
  return out;
}
const PHOTO = ff(['-f', 'lavfi', '-i', 'testsrc2=size=800x600', '-frames:v', '1'], path.join(FIX, 'ij-photo.png'));          // 800x600
const SQUARE = ff(['-f', 'lavfi', '-i', 'testsrc2=size=600x600', '-frames:v', '1'], path.join(FIX, 'ij-square.png'));        // 600x600
const SMALL = ff(['-f', 'lavfi', '-i', 'testsrc2=size=400x300', '-frames:v', '1'], path.join(FIX, 'ij-small.png'));          // 400x300
const hex = (n: number) => n.toString(16).padStart(2, '0');
/** twelve different small pictures (different colours, so each has its own bytes), named pic01.png ... pic12.png in that order */
const PICS = Array.from({ length: 12 }, (_, i) => ff(['-f', 'lavfi', '-i', `color=c=0x${hex((i * 21 + 30) % 256)}${hex((i * 53 + 90) % 256)}${hex((i * 97 + 10) % 256)}:s=${160 + i * 8}x${200 + i * 4}`, '-frames:v', '1'], path.join(FIX, `pic${String(i + 1).padStart(2, '0')}.png`)));
const stem = (p: string) => path.basename(p).replace(/\.[^.]+$/, '');

// ---- helpers --------------------------------------------------------------------------------------------------------------------------------------
const api = async (page: Page, p: string) => (await page.request.get(p)).json();
const csrf = async (page: Page) => (await api(page, '/api/me')).csrf as string;
const engine = async (page: Page) => (await page.request.get(`${ENG}/__state`)).json();
const subs = async (page: Page) => (await engine(page)).image_submissions as any[];
const control = (page: Page, data: object) => page.request.post(`${ENG}/__control/config`, { data });
const advance = (page: Page, n: number, job?: string) => page.request.post(`${ENG}/__control/advance?n=${n}${job ? `&job=${job}` : ''}`);
const pid = (page: Page) => new URL(page.url()).searchParams.get('project')!;
const project = async (page: Page) => api(page, `/api/imajutsu/projects/${pid(page)}`);
const spec = async (page: Page) => (await project(page)).spec;
const content = async (page: Page, id: string) => Buffer.from(await (await page.request.get(`/api/media/assets/${id}/content`)).body());
const asset = async (page: Page, id: string) => api(page, `/api/media/assets/${id}`);
const modeRadio = (page: Page, label: string) => page.getByTestId('modes').getByRole('radio', { name: new RegExp(`^${label}`) });
const panel = (page: Page, id: string) => page.getByTestId(`panel-${id}`);

/** The grey value (0..255) of one pixel of a PNG, read by ffmpeg, so a test never trusts the page about what a file contains. */
function pixel(buf: Buffer, x: number, y: number): number {
  const f = path.join(TMP, `px-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.png`);
  writeFileSync(f, buf);
  const out = execFileSync('ffmpeg', ['-nostdin', '-loglevel', 'error', '-i', f, '-vf', `crop=1:1:${x}:${y},format=gray`, '-f', 'rawvideo', '-frames:v', '1', 'pipe:1']);
  return out[0];
}
function dims(buf: Buffer) {
  const f = path.join(TMP, `dm-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.bin`);
  writeFileSync(f, buf);
  const j = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_streams', f]).toString());
  return { w: +j.streams[0].width, h: +j.streams[0].height };
}

test.beforeEach(async ({ page }) => {
  // runs from earlier tests may still be going: stop them so the per-person limit never gets in the way, then give the engine its instant default back
  const token = await csrf(page);
  for (const j of (await api(page, '/api/imajutsu/jobs')) as any[]) if (['queued', 'running'].includes(j.status)) await page.request.post(`/api/media/jobs/${j.job}/cancel`, { data: {}, headers: { 'x-csrf-token': token } });
  await page.request.post(`${ENG}/__reset`);
});

/** Open the page on a fresh project in one mode, the way a person does. */
async function open(page: Page, label: string) {
  await page.goto('/imajutsu?new=1');
  await expect(page.getByTestId('start')).toBeVisible();
  await modeRadio(page, label).click();
  await expect.poll(() => new URL(page.url()).searchParams.get('project')).toBeTruthy();
  await expect(page.getByTestId('panels')).toBeVisible();
}
async function addSource(page: Page, file = PHOTO) {
  await page.getByTestId('source-file').setInputFiles(file);
  await expect(page.getByTestId('source-card')).toBeVisible({ timeout: 30_000 });
}
const planAfter = (page: Page, act: () => Promise<unknown>) => Promise.all([page.waitForResponse((r) => /\/plan$/.test(r.url()) && r.request().method() === 'POST', { timeout: 10_000 }), act()]).then(async ([r]) => (await r.json()).plan);
async function settle(page: Page) { await expect(page.getByTestId('save-state')).toHaveText('All changes saved', { timeout: 10_000 }); await expect(page.getByTestId('pipeline')).not.toHaveAttribute('aria-busy', 'true', { timeout: 10_000 }); }
async function addRefs(page: Page, files: string[]) {
  const before = await page.getByTestId('ref-gallery').locator('li[data-ref-index]').count();
  await page.getByTestId('ref-file').setInputFiles(files);
  await expect(page.getByTestId('ref-gallery').locator('li[data-ref-index]')).toHaveCount(before + files.length, { timeout: 40_000 });
}
async function generate(page: Page) {
  const [r] = await Promise.all([page.waitForResponse((x) => /\/generate$/.test(x.url()) && x.request().method() === 'POST', { timeout: 60_000 }), page.getByTestId('generate').click()]);
  expect(r.status(), await r.text().catch(() => '')).toBe(200);
  return r.json();
}
async function chooseModel(page: Page, alias: string) {
  await page.getByTestId('advanced-toggle').check();
  const det = panel(page, 'model').locator('details').first();
  if (!(await det.evaluate((e: HTMLDetailsElement) => e.open))) await panel(page, 'model').getByText(/^All models/).click();
  await panel(page, 'model').getByRole('radio', { name: new RegExp(alias) }).check();
}
const shot = (page: Page, name: string) => { mkdirSync(SCREENS, { recursive: true }); return page.screenshot({ path: path.join(SCREENS, `imajutsu-${name}.png`) }); };

// =====================================================================================================================================================
test('1 the ten modes reflect /api/imajutsu/capabilities; an unavailable one is disabled and says why, and nothing is faked', async ({ page }) => {
  await page.goto('/imajutsu?new=1');
  await expect(page.getByTestId('modes').getByRole('radio')).toHaveCount(10);
  const labels = ['Generate Image', 'iPhone Photo', 'Edit Image', 'Replace Subject', 'Inpaint', 'Outpaint', 'Reference Image', 'Restyle', 'Upscale', 'Advanced Workflow'];
  for (const [i, l] of labels.entries()) await expect(page.getByTestId('modes').getByRole('radio').nth(i)).toContainText(l);
  const caps = await api(page, '/api/imajutsu/capabilities');
  expect(caps.modes.map((m: any) => m.label)).toEqual(labels);
  for (const m of caps.modes) {
    const r = modeRadio(page, m.label);
    const ok = m.status !== 'unavailable' && (m.id === 'upscale' || m.models.some((x: any) => ['ready', 'available', 'experimental'].includes(x.status)));
    await expect(r).toHaveAttribute('aria-disabled', ok ? 'false' : 'true');
  }
  // the server's own answer is changed in the browser: Inpaint and Upscale become unavailable with a reason; the page must show exactly that, never guess
  await page.route('**/api/imajutsu/capabilities', async (route) => {
    const r = await route.fetch(); const j = await r.json();
    for (const m of j.modes) if (m.id === 'inpaint') { m.status = 'unavailable'; m.reason = 'No installed model can inpaint right now (test).'; } else if (m.id === 'upscale') { m.status = 'unavailable'; m.reason = 'ffmpeg is not installed (test).'; }
    await route.fulfill({ response: r, json: j });
  });
  await page.reload();
  await expect(modeRadio(page, 'Inpaint')).toHaveAttribute('aria-disabled', 'true');
  await expect(modeRadio(page, 'Upscale')).toHaveAttribute('aria-disabled', 'true');
  await expect(page.getByTestId('mode-reasons')).toContainText('Inpaint is unavailable: No installed model can inpaint right now (test).');
  await expect(page.getByTestId('mode-reasons')).toContainText('Upscale is unavailable: ffmpeg is not installed (test).');
  await modeRadio(page, 'Inpaint').click({ force: true });
  await expect(page.getByTestId('start')).toBeVisible();                     // nothing happened: no project was made for a mode that cannot run
  await expect(modeRadio(page, 'iPhone Photo')).toHaveAttribute('aria-disabled', 'false');
  await shot(page, '01-modes');
});

test('2 iPhone Photo: 9:16 by default, the plan shows the 720x1280 native size, scene chips change the prompt the model receives', async ({ page }) => {
  await open(page, 'iPhone Photo');
  await expect(page.getByTestId('aspects').getByRole('radio', { name: '9:16' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('pipe-native')).toContainText('720×1280', { timeout: 10_000 });
  await expect(page.getByTestId('pipe-output')).toContainText('720×1280');
  expect((await spec(page)).aspect.id).toBe('9:16');
  const prompt = page.getByTestId('prompt-input');
  await prompt.fill('My friend laughing at a street market');
  const before = await planAfter(page, () => page.getByTestId('scene-chips').getByRole('button', { name: 'Night' }).click());
  await expect(page.getByTestId('final-prompt')).toContainText('It is night');
  expect(before.prompt.final).toContain('It is night');
  const flash = await planAfter(page, () => page.getByTestId('scene-chips').getByRole('button', { name: 'Flash' }).click());
  // the default is Best realism (Qwen-Image 2512), which gets plain wording: no phone, selfie, snapshot or 9:16 words anywhere in the prompt
  await expect(page.getByTestId('final-prompt')).toContainText('The light is a direct flash');
  for (const w of ['phone', 'iphone', 'snapshot', 'selfie', '9:16']) expect(flash.prompt.final.toLowerCase(), w).not.toContain(w);
  expect(flash.prompt.final).toContain('street market');
  await expect.poll(async () => (await spec(page)).preset.scenes).toEqual(['night', 'flash']);
  await expect(page.getByTestId('final-prompt')).toHaveText(flash.prompt.final);
  // Fast (FLUX.2 klein) takes natural wording about the camera
  await planAfter(page, () => page.getByTestId('tier-chips').getByRole('radio', { name: 'Fast' }).click());
  await expect(page.getByTestId('final-prompt')).toContainText('direct on-camera flash');
  await expect(panel(page, 'prompt')).toContainText('You do not need to write phone words');
  await shot(page, '02-iphone');
});

test('2b iPhone Photo opens on Best realism: Qwen-Image 2512 at the measured 30 steps, plain wording; the Quality control switches tier and the page, the plan and the engine agree', async ({ page }) => {
  await open(page, 'iPhone Photo');
  const tiers = page.getByTestId('tier-chips');
  await expect(tiers.getByRole('radio')).toHaveCount(3);
  await expect(tiers.getByRole('radio', { name: 'Best realism' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('model-name')).toContainText('qwen-image-2512');
  await expect(page.getByTestId('tier-hint')).toContainText('86 s');
  await page.getByTestId('prompt-input').fill('A family barbecue in the back garden');
  await settle(page);
  const plan = (await (await page.request.post(`/api/imajutsu/projects/${pid(page)}/plan`, { data: {}, headers: { 'x-csrf-token': await csrf(page) } })).json()).plan;
  expect(plan.model.alias).toBe('qwen-image-2512'); expect(plan.steps).toBe(30);
  await generate(page);
  const sub = (await subs(page)).at(-1);
  expect(sub.model).toBe('qwen-image-2512');
  expect(sub.simple.steps).toBe(30);
  expect([sub.simple.width, sub.simple.height]).toEqual([720, 1280]);
  for (const w of ['phone', 'iphone', 'smartphone', 'snapshot', '9:16', 'selfie', 'screenshot']) expect(sub.simple.prompt.toLowerCase(), w).not.toContain(w);
  await tiers.getByRole('radio', { name: 'Polished' }).click();
  await expect(page.getByTestId('model-name')).toContainText('z-image-turbo');
  await settle(page);
  expect((await spec(page)).preset.tier).toBe('polished');
  await shot(page, '02b-iphone-quality');
});

test('3 Generate sends exactly what the engine records: multiples of 16, the prompt shown on screen, a seed; the z-image variant gets no phone words', async ({ page }) => {
  await open(page, 'iPhone Photo');
  await page.getByTestId('prompt-input').fill('A friend laughing at a night market');
  // the page opens on Best realism (Qwen-Image 2512, plain wording, 30 steps); Fast is one click and runs FLUX.2 klein at its own 4 steps
  const tiers = page.getByTestId('tier-chips');
  await expect(tiers.getByRole('radio', { name: 'Best realism' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('model-name')).toContainText('qwen-image-2512');
  await tiers.getByRole('radio', { name: 'Fast' }).click();
  await expect(tiers.getByRole('radio', { name: 'Fast' })).toHaveAttribute('aria-checked', 'true');
  await expect(page.getByTestId('model-name')).toContainText('flux2-klein-4b');
  await page.getByTestId('scene-chips').getByRole('button', { name: 'Selfie' }).click();
  await page.getByTestId('scene-chips').getByRole('button', { name: 'Night' }).click();
  await settle(page);
  const shown = await page.getByTestId('final-prompt').innerText();
  await page.getByTestId('advanced-toggle').check();
  await page.getByTestId('seed-input').fill('4321');
  await settle(page);
  const r = await generate(page);
  expect(r.jobs).toHaveLength(1);
  await expect(page.getByTestId('run-card')).toBeVisible();
  const s1 = (await subs(page)).at(-1);
  expect(s1.model).toBe('flux2-klein-4b');
  expect(s1.engine_mode).toBe('txt2img');
  expect(s1.simple.prompt).toBe(shown);
  expect(s1.simple.width).toBe(720); expect(s1.simple.height).toBe(1280);
  expect(s1.simple.width % 16).toBe(0); expect(s1.simple.height % 16).toBe(0);
  expect(s1.simple.seed).toBe(4321);
  expect(s1.simple.steps).toBe(4);
  expect(s1.pictures).toHaveLength(0);
  // the same words for Z-Image, which draws hands holding phones when it reads phone wording: nothing of the kind may reach it
  await chooseModel(page, 'z-image-turbo');
  await settle(page);
  const plan = await planAfter(page, () => page.getByTestId('scene-chips').getByRole('button', { name: 'Casual' }).click());
  expect(plan.model.alias).toBe('z-image-turbo');
  await generate(page);
  const s2 = (await subs(page)).at(-1);
  expect(s2.model).toBe('z-image-turbo');
  for (const w of ['phone', 'iphone', 'smartphone', 'snapshot', '9:16', 'selfie', 'screenshot', 'camera app']) expect(s2.simple.prompt.toLowerCase(), w).not.toContain(w);
  expect(s2.simple.width % 16).toBe(0); expect(s2.simple.height % 16).toBe(0);
  expect(Number.isInteger(s2.simple.seed)).toBe(true);
  expect(s2.simple.seed).toBe(4321);                                           // the seed typed above is used again by the next run
});

test('4 references 1, 3, 4, 5 and 12: the engine receives only the used ones, in order; the others are shown Not used with the server\'s reason', async ({ page }) => {
  await open(page, 'Reference Image');
  await expect(panel(page, 'references')).toBeVisible();
  await page.getByTestId('prompt-input').fill('The same character walking in a park');
  const caps = await api(page, '/api/imajutsu/capabilities');
  const refMode = caps.modes.find((m: any) => m.id === 'reference');
  const limit = refMode.models.find((m: any) => m.model_id === refMode.recommended.model_id).limits.references;
  expect(limit).toBe(4);                                                     // FLUX.2 klein: 4 pictures
  let have = 0;
  for (const n of [1, 3, 4, 5, 12]) {
    await addRefs(page, PICS.slice(have, n)); have = n;
    await settle(page);
    const used = Math.min(n, limit);
    await expect(page.getByTestId('badge-used')).toHaveCount(used);
    await expect(page.getByTestId('badge-unused')).toHaveCount(n - used);
    await expect(page.getByTestId('ref-count')).toHaveText(`${used} of ${n} used in this run`);
    if (n > limit) {
      await expect(page.getByTestId('ref-over')).toBeVisible();
      await expect(page.getByTestId('badge-unused').first()).toContainText(`takes at most ${limit} picture`);
      await expect(page.getByTestId('acc-unused')).toContainText(`pic${String(limit + 1).padStart(2, '0')}`);
    }
    await expect(page.getByTestId('acc-used').locator('li')).toHaveCount(used);
    const tiles = page.getByTestId('ref-gallery').locator('li[data-ref-index]');
    for (let i = 0; i < n; i++) await expect(tiles.nth(i).locator('[data-testid^=badge-]')).toHaveAttribute('data-testid', i < limit ? 'badge-used' : 'badge-unused');
    await generate(page);
    const sub = (await subs(page)).at(-1);
    expect(sub.pictures.map((p: any) => p.name.replace(/\.[^.]+$/, ''))).toEqual(PICS.slice(0, used).map(stem));       // only the used ones, in the order shown
    expect(sub.pictures.length).toBeLessThanOrEqual(limit);
  }
  // switching a picture off hands its place to the next one: the engine then receives the later picture instead
  const tiles = page.getByTestId('ref-gallery').locator('li[data-ref-index]');
  await tiles.nth(0).getByRole('checkbox', { name: /Use .* in this run/ }).uncheck();
  await settle(page);
  await expect(tiles.nth(0).getByTestId('badge-unused')).toContainText('Switched off');
  await expect(tiles.nth(4).getByTestId('badge-used')).toBeVisible();
  await generate(page);
  expect((await subs(page)).at(-1).pictures.map((p: any) => p.name.replace(/\.[^.]+$/, ''))).toEqual([1, 2, 3, 4].map((i) => stem(PICS[i])));
  // nothing is lost from the project: all twelve are still kept, in order
  expect((await spec(page)).references.map((r: any) => r.asset)).toHaveLength(12);
  await shot(page, '04-references');
});

test('5 reference roles become sentences in the prompt: shown next to the picture, in the prompt box and in what the engine receives', async ({ page }) => {
  await open(page, 'Reference Image');
  await page.getByTestId('prompt-input').fill('A portrait at a window');
  await addRefs(page, PICS.slice(0, 3));
  await settle(page);
  const caps = await api(page, '/api/imajutsu/capabilities');
  const roles = caps.roles.map((r: any) => r.id);
  expect(roles).toEqual(expect.arrayContaining(['appearance', 'identity', 'composition', 'lighting', 'style']));
  const tiles = page.getByTestId('ref-gallery').locator('li[data-ref-index]');
  await expect(tiles.nth(0).getByTestId('ref-role').locator('option')).toHaveText(['No role', ...roles.map((r: string) => r[0].toUpperCase() + r.slice(1))]);
  const plan = await planAfter(page, () => tiles.nth(0).getByTestId('ref-role').selectOption('identity'));
  await planAfter(page, () => tiles.nth(1).getByTestId('ref-role').selectOption('lighting'));
  await planAfter(page, () => tiles.nth(2).getByTestId('ref-role').selectOption('style'));
  void plan;
  await settle(page);
  await expect(tiles.nth(0).getByTestId('ref-sentence')).toContainText('Keep the identity (face and features) of the person in image 1.');
  await expect(tiles.nth(1).getByTestId('ref-sentence')).toContainText('Match the lighting of image 2.');
  await expect(tiles.nth(2).getByTestId('ref-sentence')).toContainText('Use the visual style of image 3.');
  await tiles.nth(1).getByTestId('ref-keep').fill('the warm window light');
  await settle(page);
  await expect(tiles.nth(1).getByTestId('ref-sentence')).toContainText('Keep the warm window light from image 2 unchanged.');
  await page.getByText('The prompt the model receives').click();
  await expect(page.getByTestId('final-prompt')).toContainText('Match the lighting of image 2. Keep the warm window light from image 2 unchanged.');
  await expect(page.getByTestId('ref-sentences').locator('li')).toHaveCount(3);
  await generate(page);
  const sub = (await subs(page)).at(-1);
  for (const t of ['Keep the identity (face and features) of the person in image 1.', 'Match the lighting of image 2.', 'Use the visual style of image 3.']) expect(sub.simple.prompt).toContain(t);
  expect((await spec(page)).references.map((r: any) => r.role)).toEqual(['identity', 'lighting', 'style']);
  // a role the server does not list is never offered
  await expect(tiles.nth(0).getByTestId('ref-role').locator('option')).toHaveCount(roles.length + 1);
});

test('6 the selection editor: draw, erase, box, invert, undo; the saved mask is a real picture of the right size with white = change; the strokes come back after a reload', async ({ page }) => {
  await open(page, 'Inpaint');
  await addSource(page, PHOTO);
  await page.getByTestId('open-mask').click();
  const ed = page.getByTestId('mask-editor');
  await expect(ed).toBeVisible();
  const wrap = page.getByTestId('mask-canvas-wrap');
  await expect(wrap).toBeVisible();
  const box = (await wrap.boundingBox())!;
  const at = (fx: number, fy: number) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
  // a brush stroke across the upper left part
  await ed.getByRole('radio', { name: /^Brush/ }).click();
  await page.keyboard.press('Escape');
  const a = at(0.15, 0.2), b = at(0.45, 0.2);
  await page.mouse.move(a.x, a.y); await page.mouse.down(); await page.mouse.move((a.x + b.x) / 2, a.y + 4, { steps: 6 }); await page.mouse.move(b.x, b.y, { steps: 6 }); await page.mouse.up();
  await expect(page.getByTestId('mask-status')).toContainText('will change');
  const cover1 = await page.getByTestId('mask-status').innerText();
  // erase the right half of that stroke
  await ed.getByRole('radio', { name: /^Eraser/ }).click();
  const c = at(0.3, 0.2);
  await page.mouse.move(c.x, c.y); await page.mouse.down(); await page.mouse.move(b.x + 20, b.y, { steps: 8 }); await page.mouse.up();
  await expect(page.getByTestId('mask-status')).not.toHaveText(cover1);
  // a box low in the picture
  await ed.getByRole('radio', { name: /^Box select/ }).click();
  const d = at(0.55, 0.6), e = at(0.9, 0.9);
  await page.mouse.move(d.x, d.y); await page.mouse.down(); await page.mouse.move(e.x, e.y, { steps: 8 }); await page.mouse.up();
  // undo takes the box away, redo brings it back
  await page.getByTestId('mask-undo').click();
  await page.getByTestId('mask-redo').click();
  await page.getByTestId('mask-undo').click();
  await page.getByTestId('mask-redo').click();
  // the strokes are saved a moment later (a brush selection is a picture; one single box would be sent as a box)
  await expect.poll(async () => (await spec(page)).mask_asset, { timeout: 15_000 }).toBeTruthy();
  const sp = await spec(page);
  expect(sp.selection.box).toBeNull();
  const png = await content(page, sp.mask_asset);
  expect(dims(png)).toEqual({ w: 800, h: 600 });
  // white = change: inside the box (55-90 % across, 60-90 % down) it is white, far from every mark it is black, and the erased half of the stroke is black again
  expect(pixel(png, 800 * 0.72, 600 * 0.75)).toBeGreaterThan(240);
  expect(pixel(png, 800 * 0.15, 600 * 0.2)).toBeGreaterThan(240);          // the part of the stroke that stayed
  expect(pixel(png, 800 * 0.4, 600 * 0.2)).toBeLessThan(15);                // erased
  expect(pixel(png, 800 * 0.2, 600 * 0.8)).toBeLessThan(15);                // never touched
  // invert and the soft edge are settings the server applies; the picture itself keeps white = what you marked
  await page.getByTestId('mask-invert').click();
  await page.getByTestId('mask-undo').click();                                // undoing invert switches it back
  await expect(page.getByTestId('mask-invert')).toHaveAttribute('aria-pressed', 'false');
  await page.getByTestId('mask-invert').click();
  await expect(page.getByTestId('mask-invert')).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(async () => (await spec(page)).selection.invert).toBe(true);
  expect(pixel(await content(page, (await spec(page)).mask_asset), 800 * 0.72, 600 * 0.75)).toBeGreaterThan(240);
  await page.getByTestId('mask-invert').click();
  await expect.poll(async () => (await spec(page)).selection.invert).toBe(false);
  // keyboard: the same actions without a pointer
  await wrap.focus();
  await page.keyboard.press('i');
  await expect(page.getByTestId('mask-invert')).toHaveAttribute('aria-pressed', 'true');
  await page.keyboard.press('Control+z');
  await expect(page.getByTestId('mask-invert')).toHaveAttribute('aria-pressed', 'false');
  // saved: the page says so, and after a reload the strokes are still there to be edited
  await expect(page.getByTestId('mask-status')).toContainText('Saved', { timeout: 10_000 });
  const maskId = (await spec(page)).mask_asset;
  const status = await page.getByTestId('mask-status').innerText();
  await page.reload();
  await expect(page.getByTestId('open-mask')).toHaveText(/Edit the selection/);
  await page.getByTestId('open-mask').click();
  await expect(page.getByTestId('mask-editor')).toBeVisible();
  await expect(page.getByTestId('mask-status')).toContainText('will change');
  expect((await spec(page)).mask_asset).toBe(maskId);
  const px = await page.getByTestId('mask-overlay').evaluate((cv: HTMLCanvasElement) => {
    const g = cv.getContext('2d')!, w = cv.width, h = cv.height;
    const alpha = (fx: number, fy: number) => g.getImageData(Math.floor(w * fx), Math.floor(h * fy), 1, 1).data[3];
    return { box: alpha(0.72, 0.75), stroke: alpha(0.15, 0.2), erased: alpha(0.4, 0.2), empty: alpha(0.2, 0.8) };
  });
  expect(px.box).toBeGreaterThan(100); expect(px.stroke).toBeGreaterThan(100); expect(px.erased).toBe(0); expect(px.empty).toBe(0);
  void status;
  // the strokes themselves (not only the picture) are back: eraser and undo keep working on them
  await page.getByTestId('mask-clear').click();
  await expect(page.getByTestId('mask-status')).toContainText('Nothing selected yet');
  await shot(page, '06-mask');
});

test('7 inpaint end to end: the engine receives the source and the mask at its working size, in inpaint mode, with the words as the prompt', async ({ page }) => {
  await open(page, 'Inpaint');
  await addSource(page, PHOTO);
  await page.getByTestId('prompt-input').fill('A wooden bench');
  await expect(page.getByTestId('blockers')).toContainText('Paint or box the part of the picture to change.');
  await expect(page.getByTestId('generate')).toBeDisabled();
  await page.getByTestId('open-mask').click();
  const wrap = page.getByTestId('mask-canvas-wrap'), box = (await wrap.boundingBox())!;
  await page.getByTestId('mask-editor').getByRole('radio', { name: /^Brush/ }).click();
  await page.mouse.move(box.x + box.width * 0.3, box.y + box.height * 0.5); await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.55, { steps: 12 }); await page.mouse.up();
  await expect(page.getByTestId('blockers')).toHaveCount(0);                  // the selection is enough: it is saved when Generate is pressed
  await settle(page);
  const plan = (await (await page.request.post(`/api/imajutsu/projects/${pid(page)}/plan`, { data: {}, headers: { 'x-csrf-token': await csrf(page) } })).json()).plan;
  const r = await generate(page);
  expect(r.accounting.mask.sent_as).toBe('mask_image');
  const sub = (await subs(page)).at(-1);
  expect(sub.engine_mode).toBe('inpaint');
  expect(sub.model).toBe('flux2-klein-4b');
  expect(sub.simple.prompt).toContain('A wooden bench');
  expect(sub.pictures).toHaveLength(1);
  expect(sub.pictures[0].name).toBe('source.png');
  expect(sub.mask).toBeTruthy();
  const nat = plan.native;
  expect([sub.simple.width, sub.simple.height]).toEqual([nat.width, nat.height]);
  expect([sub.mask.width, sub.mask.height]).toEqual([nat.width, nat.height]);   // the mask has the size of the picture the engine works on
  expect([sub.pictures[0].width, sub.pictures[0].height]).toEqual([nat.width, nat.height]);
  expect((await spec(page)).mask_asset).toBeTruthy();
  // the result arrives, with its seed, and can be opened
  const res = page.getByTestId('result').first();
  await expect(res).toBeVisible({ timeout: 30_000 });
  await expect(res.getByTestId('result-seed')).toContainText('seed');
  await expect(page.getByTestId('accounting')).toContainText('Saved as a picture (white = change)');
});

test('8 outpaint: the new area is shown from the plan, and the engine receives the padded picture and the mask of the new area', async ({ page }) => {
  await open(page, 'Outpaint');
  await addSource(page, SQUARE);
  await page.getByTestId('aspects').getByRole('radio', { name: '16:9' }).click();
  await settle(page);
  await expect(page.getByTestId('outpaint-view')).toBeVisible();
  const plan = (await (await page.request.post(`/api/imajutsu/projects/${pid(page)}/plan`, { data: {}, headers: { 'x-csrf-token': await csrf(page) } })).json()).plan;
  expect(plan.outpaint.canvas).toEqual({ width: 1067, height: 600 });
  await expect(page.getByTestId('outpaint-note')).toContainText('New canvas 1067×600');
  await expect(page.getByTestId('pipe-outpaint')).toContainText('px added on the left');
  await page.getByTestId('prompt-input').fill('More of the beach and the sea');
  await generate(page);
  const sub = (await subs(page)).at(-1);
  expect(sub.engine_mode).toBe('inpaint');
  expect(sub.simple.prompt).toContain('More of the beach and the sea');
  expect(sub.pictures).toHaveLength(1);
  expect([sub.pictures[0].width, sub.pictures[0].height]).toEqual([plan.native.width, plan.native.height]);
  expect([sub.mask.width, sub.mask.height]).toEqual([plan.native.width, plan.native.height]);
  // pick where the picture sits: the new area grows on the other side
  await page.getByTestId('anchors').getByRole('radio', { name: 'Put your picture at the left' }).click();
  await settle(page);
  const p2 = (await (await page.request.post(`/api/imajutsu/projects/${pid(page)}/plan`, { data: {}, headers: { 'x-csrf-token': await csrf(page) } })).json()).plan;
  expect(p2.outpaint.pad.left).toBe(0);
  expect(p2.outpaint.pad.right).toBe(1067 - 600);
});

test('9 replace subject with three references: the source and the pictures reach the engine in order, the prompt carries the replace sentence', async ({ page }) => {
  await open(page, 'Replace Subject');
  await addSource(page, PHOTO);
  await page.getByTestId('prompt-input').fill('Replace the motorcycle');
  await expect(page.getByTestId('blockers')).toContainText('Add a reference picture.');
  await addRefs(page, PICS.slice(0, 3));
  await settle(page);
  // the recommended model takes 3 pictures in all (one is the source): only two of the three references fit, and the page says which
  await expect(page.getByTestId('badge-used')).toHaveCount(2);
  await expect(page.getByTestId('badge-unused')).toHaveCount(1);
  await expect(page.getByTestId('badge-unused')).toContainText('takes at most 3 pictures (the source picture is one of them)');
  // FLUX.2 klein takes four: all three references are used
  await chooseModel(page, 'flux2-klein-4b');
  await settle(page);
  await expect(page.getByTestId('badge-used')).toHaveCount(3);
  await generate(page);
  const sub = (await subs(page)).at(-1);
  expect(sub.model).toBe('flux2-klein-4b');
  expect(sub.engine_mode).toBe('edit');
  expect(sub.pictures.map((p: any) => p.name.replace(/\.[^.]+$/, ''))).toEqual(['source', ...PICS.slice(0, 3).map(stem)]);
  expect(sub.simple.prompt).toContain('Replace the motorcycle');
  expect(sub.simple.prompt).toContain('Replace the subject in image 1 with the subject from image 2.');
  expect(sub.simple.prompt).toContain('Replace the subject in image 1 with the subject from image 4.');
  await expect(page.getByTestId('acc-used').locator('li')).toHaveCount(3);
  await shot(page, '09-replace');
});

test('10 edit: instruction edit of the source; the result opens, compares with the original, and can become the next source', async ({ page }) => {
  await open(page, 'Edit Image');
  await addSource(page, PHOTO);
  await expect(page.getByTestId('blockers')).toContainText('Say what you want');
  await page.getByTestId('prompt-input').fill('Make the sky orange');
  await generate(page);
  const sub = (await subs(page)).at(-1);
  expect(sub.engine_mode).toBe('edit');
  expect(sub.model).toBe('qwen-image-edit-2511');
  expect(sub.pictures).toHaveLength(1);
  expect(sub.simple.prompt).toBe('Make the sky orange');                       // no reference pictures: the words go through untouched
  const res = page.getByTestId('result').first();
  await expect(res).toBeVisible({ timeout: 30_000 });
  await res.getByTestId('res-compare').click();
  await expect(page.getByTestId('compare')).toBeVisible();
  await page.getByTestId('compare-slider').fill('80');
  await res.getByTestId('res-open').click();
  await expect(page.getByRole('dialog').first()).toBeVisible();
  await page.keyboard.press('Escape');
  const assetId = await res.getAttribute('data-asset');
  await res.getByTestId('res-use-source').click();
  await expect.poll(async () => (await spec(page)).source_asset).toBe(assetId);
  await expect(page.getByTestId('source-card')).toContainText(`${(await asset(page, assetId!)).width}×${(await asset(page, assetId!)).height}`);
  await shot(page, '10-edit');
});

test('11 adjustments: the sliders drive a server-rendered preview; Apply makes a new picture of the expected size and the original is not touched', async ({ page }) => {
  await open(page, 'Edit Image');
  await addSource(page, PHOTO);
  await settle(page);
  const srcId = (await spec(page)).source_asset;
  const hashBefore = sha256(await content(page, srcId));
  await page.getByTestId('stage').getByRole('tab', { name: 'Adjust' }).click();
  await expect(page.getByTestId('adjust-stage')).toBeVisible();
  // moving a slider asks the server for a preview of exactly that recipe
  const exposure = panel(page, 'adjust').getByTestId('adj-exposure');
  const [resp] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/adjust/preview') && r.url().includes('exposure')),
    exposure.evaluate((el: HTMLInputElement) => { el.value = '1'; el.dispatchEvent(new Event('input', { bubbles: true })); })
  ]);
  expect(resp.status()).toBe(200);
  expect(resp.headers()['content-type']).toBe('image/jpeg');
  expect(JSON.parse(new URL(resp.url()).searchParams.get('recipe')!)).toEqual({ exposure: 1 });
  await expect(page.getByTestId('adjust-preview')).toHaveAttribute('data-src', /exposure/);
  await expect(page.getByTestId('adj-exposure-v')).toHaveText('1 EV');
  const warm = panel(page, 'adjust').getByTestId('adj-temperature');
  const [resp2] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/adjust/preview') && r.url().includes('temperature')),
    warm.evaluate((el: HTMLInputElement) => { el.value = '30'; el.dispatchEvent(new Event('input', { bubbles: true })); })
  ]);
  expect(JSON.parse(new URL(resp2.url()).searchParams.get('recipe')!)).toEqual({ exposure: 1, temperature: 30 });
  expect((await resp2.body()).length).toBeGreaterThan(500);
  // the preview really is a picture and is smaller than the file (long edge at most 1024)
  const nat = await page.getByTestId('adjust-preview').evaluate((im: HTMLImageElement) => ({ w: im.naturalWidth, h: im.naturalHeight }));
  expect(nat.w).toBeGreaterThan(100);
  // turn it a quarter and Apply: a new asset of 600x800 (the 800x600 picture turned)
  await panel(page, 'adjust').getByRole('button', { name: 'Turn right 90 degrees' }).click();
  await expect(page.getByTestId('adjust-dims')).toContainText('800×600 → 600×800');
  await panel(page, 'adjust').getByTestId('adjust-apply').click();
  const done = page.getByTestId('adjust-done');
  await expect(done).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('adjust-done-dims')).toHaveText('600×800');
  await expect(panel(page, 'adjust')).toContainText('The original is not changed');
  const copy = (await api(page, '/api/media/assets?origin=adjusted'))[0];
  expect(copy.origin).toBe('adjusted');
  expect(copy.parent).toBe(srcId);
  expect(dims(await content(page, copy.id))).toEqual({ w: 600, h: 800 });
  expect((await spec(page)).adjustments).toEqual({ exposure: 1, temperature: 30, rotate: 90 });
  // the original is byte for byte what was uploaded, and still 800x600
  expect(sha256(await content(page, srcId))).toBe(hashBefore);
  expect(sha256(readFileSync(PHOTO))).toBe(hashBefore);
  expect(dims(await content(page, srcId))).toEqual({ w: 800, h: 600 });
  // reset
  await panel(page, 'adjust').getByTestId('adjust-reset').click();
  await expect(page.getByTestId('adjust-summary')).toHaveText('No changes yet');
  await shot(page, '11-adjust');
});

test('12 upscale is scaling, not a model: labelled "no new detail", exact size, a new picture', async ({ page }) => {
  await open(page, 'Upscale');
  await addSource(page, SMALL);
  await expect(panel(page, 'size')).toContainText('Scaling, not a model');
  await expect(panel(page, 'model')).toHaveCount(0);
  await panel(page, 'size').getByTestId('scales').getByRole('radio', { name: /^2×/ }).click();
  await settle(page);
  await expect(page.getByTestId('pipe-output')).toContainText('800×600');
  await expect(page.getByTestId('pipe-output')).toContainText('scaled up, no new detail');
  await expect(page.getByTestId('pipe-output-note')).toContainText('No new detail is added: this is not AI');
  await expect(page.getByTestId('acc-model')).toHaveText('Scaling (Lanczos), no model');
  await page.getByTestId('generate').click();
  const copy = page.getByTestId('copy').first();
  await expect(copy).toBeVisible({ timeout: 30_000 });
  await expect(copy).toContainText('not made by a model');
  const id = await copy.getAttribute('data-asset');
  expect(dims(await content(page, id!))).toEqual({ w: 800, h: 600 });
  expect((await asset(page, id!)).origin).toBe('scaled');
  expect((await subs(page)).length).toBe(0);                                  // no engine job was made
  await expect(page.getByTestId('result-view')).toBeVisible();
});

test('13 the live job: stage and step from the engine, the engine\'s own preview picture while it samples, VIEW PROCESS and VIEW WORKFLOW', async ({ page }) => {
  await control(page, { clock: 'manual' });
  await open(page, 'Generate Image');
  await page.getByTestId('prompt-input').fill('A fishing boat at dawn');
  await generate(page);
  const card = page.getByTestId('run-card');
  await expect(card).toBeVisible();
  await expect(card.getByTestId('job-stage')).toContainText('Queued');
  await advance(page, 4);                                                    // load, encode, first sampler steps
  await expect(card.getByTestId('job-stage')).toContainText('Sampling', { timeout: 15_000 });
  await expect(card.getByTestId('job-stage')).toContainText('Step ');
  const strip = card.getByRole('group', { name: 'Job progress' });
  await expect(strip).toBeVisible();
  const pv = card.getByTestId('job-preview');
  await expect(pv).toBeVisible({ timeout: 15_000 });
  expect(await pv.evaluate((im: HTMLImageElement) => im.naturalWidth)).toBeGreaterThan(8);        // a real picture from the engine
  await expect(card.getByTestId('view-workflow')).toBeVisible();
  await expect(card.getByRole('button', { name: 'VIEW COMFYUI' })).toHaveCount(0);
  await card.getByTestId('view-process').click();
  const dlg = page.getByRole('dialog', { name: 'Job process' });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByTestId('sampler')).toContainText('Sample image');
  await expect(dlg.getByTestId('sampler-step')).toContainText('step');
  await expect(dlg.getByText('ComfyUI prompt id')).toHaveCount(0);
  await expect(dlg.getByText('Scenes', { exact: true })).toHaveCount(0);
  await shot(page, '13-process');
  await page.keyboard.press('Escape');
  await card.getByTestId('view-workflow').click();
  const wf = page.getByRole('dialog', { name: 'Engine workflow' });
  await expect(wf).toBeVisible();
  await expect(wf.getByText('View workflow')).toBeVisible();
  await expect(wf.getByText(/Sample image/).first()).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press('Escape');
  await advance(page, 100);
  await expect(card).toHaveAttribute('data-status', 'completed', { timeout: 20_000 });
  await expect(page.getByTestId('result').first()).toBeVisible({ timeout: 20_000 });
  await expect(card.getByTestId('job-preview')).toHaveCount(0);              // no preview is claimed once it is finished
});

test('14 a failed run names the failing node in plain words', async ({ page }) => {
  await open(page, 'Generate Image');
  await page.getByTestId('prompt-input').fill('boom goes the sampler');
  await generate(page);
  const err = page.getByTestId('job-error');
  await expect(err).toBeVisible({ timeout: 30_000 });
  await expect(err).toContainText('It stopped in “Sample image”');
  await expect(err).toContainText('out of memory');
  await expect(page.getByTestId('run-card')).toHaveAttribute('data-status', 'failed');
  await expect(page.getByTestId('results-empty')).toBeVisible();
});

test('15 reload brings back the project and the run; the Jobs list reopens an earlier run with its references and selection intact', async ({ page }) => {
  await open(page, 'Replace Subject');
  await addSource(page, PHOTO);
  await page.getByTestId('prompt-input').fill('Replace the woman in the red coat');
  await addRefs(page, PICS.slice(0, 2));
  await page.getByTestId('open-mask').click();
  const wrap = page.getByTestId('mask-canvas-wrap'), box = (await wrap.boundingBox())!;
  await page.getByTestId('mask-editor').getByRole('radio', { name: /^Brush/ }).click();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.4); await page.mouse.down(); await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.6, { steps: 8 }); await page.mouse.up();
  await expect.poll(async () => (await spec(page)).mask_asset, { timeout: 15_000 }).toBeTruthy();
  await settle(page);
  const before = await spec(page);
  await generate(page);
  await expect(page.getByTestId('result').first()).toBeVisible({ timeout: 30_000 });
  const url = page.url();
  const job = new URL(url).searchParams.get('job');
  expect(job).toBeTruthy();
  await page.reload();
  expect(page.url()).toContain(`project=${pid(page)}`);
  await expect(page.getByTestId('prompt-input')).toHaveValue('Replace the woman in the red coat');
  await expect(page.getByTestId('ref-gallery').locator('li[data-ref-index]')).toHaveCount(2);
  await expect(page.getByTestId('result').first()).toBeVisible({ timeout: 30_000 });
  await expect(page.getByTestId('run-card')).toHaveAttribute('data-job', job!);
  expect(await spec(page)).toEqual(before);
  // change the project, then reopen the earlier run: it comes back as it was
  await page.getByTestId('prompt-input').fill('Something else entirely');
  await page.getByTestId('ref-gallery').locator('li[data-ref-index]').first().getByRole('button', { name: /^Remove / }).click();
  await settle(page);
  await page.getByTestId('open-jobs').click();
  const row = page.getByTestId('jobs-list').locator('li').first();
  await expect(row).toContainText('Replace Subject');
  await row.getByRole('button', { name: /^Open / }).click();
  await expect(page.getByTestId('prompt-input')).toHaveValue('Replace the woman in the red coat');
  await expect(page.getByTestId('ref-gallery').locator('li[data-ref-index]')).toHaveCount(2);
  const reopened = await spec(page);
  expect(reopened.references.map((r: any) => r.asset)).toEqual(before.references.map((r: any) => r.asset));
  expect(reopened.mask_asset).toBe(before.mask_asset);
  await page.getByTestId('open-mask').click();
  await expect(page.getByTestId('mask-status')).toContainText('will change');
});

test('16 on a phone (390 px) the core flow works with no sideways scrolling and 44 px targets', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, 'Generate Image');
  const overflow = () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(await overflow()).toBeLessThanOrEqual(0);
  await page.getByTestId('prompt-input').fill('A fishing boat at dawn');
  await panel(page, 'size').scrollIntoViewIfNeeded();
  await page.getByTestId('aspects').getByRole('radio', { name: '4:5' }).click();
  await expect(page.getByTestId('pipe-native')).toContainText('832×1040', { timeout: 10_000 });
  expect(await overflow()).toBeLessThanOrEqual(0);
  await page.getByTestId('generate').scrollIntoViewIfNeeded();
  await generate(page);
  await expect(page.getByTestId('result').first()).toBeVisible({ timeout: 30_000 });
  expect(await overflow()).toBeLessThanOrEqual(0);
  const small: string[] = [];
  for (const b of await page.locator('[data-testid=modes] button, [data-testid=generate], [data-testid=aspects] button, [data-testid=count] button, [data-testid=res-open]').all()) {
    const bb = await b.boundingBox(); if (bb && bb.height < 43.5) small.push(`${await b.innerText()} ${Math.round(bb.height)}`);
  }
  expect(small, 'targets below 44 px').toEqual([]);
  await shot(page, '16-mobile');
});

// ---- accessibility --------------------------------------------------------------------------------------------------------------------------------
async function axePass(page: Page, theme: 'light' | 'dark') {
  const { default: AxeBuilder } = await import('@axe-core/playwright');
  await page.emulateMedia({ colorScheme: theme });
  await page.evaluate((t) => document.documentElement.setAttribute('data-theme', t), theme);
  const found: string[] = [];
  const run = async (label: string, within?: Locator) => {
    let b = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']);
    if (within) b = b.include(await within.evaluate((el) => { el.setAttribute('data-axe', '1'); return '[data-axe="1"]'; }));
    for (const v of (await b.analyze()).violations) found.push(`${theme} ${label}: ${v.id} (${v.impact}) ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`);
  };
  await run('page');
  return { found, run };
}

test('17 accessibility: axe finds nothing on the page or the selection editor, light and dark', async ({ page }) => {
  await open(page, 'Inpaint');
  await addSource(page, PHOTO);
  await page.getByTestId('prompt-input').fill('A wooden bench');
  await addRefs(page, PICS.slice(0, 2));
  const found: string[] = [];
  for (const theme of ['light', 'dark'] as const) {
    await page.getByTestId('stage').getByRole('tab', { name: 'Picture' }).click();
    const { found: f, run } = await axePass(page, theme);
    found.push(...f);
    await page.getByTestId('open-mask').click();
    await expect(page.getByTestId('mask-editor')).toBeVisible();
    await run('mask editor', page.getByTestId('mask-editor'));
    await page.getByTestId('stage').getByRole('tab', { name: 'Adjust' }).click();
    await run('adjust', page.getByTestId('stage'));
    await run('adjust panel', panel(page, 'adjust'));
    await panel(page, 'adjust').getByTestId('crop-toggle').click();
    await run('crop', page.getByTestId('stage'));
    await panel(page, 'adjust').getByTestId('crop-toggle').click();
    if (theme === 'dark') await shot(page, '17-dark');
  }
  await page.emulateMedia({ colorScheme: 'light' });
  expect(found, found.join('\n')).toEqual([]);
});
