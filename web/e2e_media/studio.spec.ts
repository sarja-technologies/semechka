// OpenJutsu Studio, in real Chrome against the real Semechka server, real ffmpeg and the Sarness engine double (see STUDIO_HARNESS.md).
// Nothing about the Studio is mocked: every assertion reads what the server stored, what ffprobe says about real files, or what the engine received.
import { test, expect, type Page, type Locator } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { MP, TMP } from '../playwright.media.config';
import { drop, FIX, fixtures, sha, sha256, shot } from './helpers';

const F = fixtures();
const ENG = `http://127.0.0.1:${MP.engine}`;
test.use({ storageState: path.join(TMP, 'auth', 'owner.json') });

// ---- media made for these tests -----------------------------------------------------------------------------------------------
function ff(args: string[], out: string) {
  if (!existsSync(out)) execFileSync('ffmpeg', ['-nostdin', '-loglevel', 'error', '-y', ...args, out]);
  return out;
}
/** twelve different small pictures (different colours, so every one has its own bytes and its own hash) */
const PICS = Array.from({ length: 12 }, (_, i) => ff(['-f', 'lavfi', '-i', `color=c=0x${((i * 21 + 30) % 256).toString(16).padStart(2, '0')}${((i * 53 + 90) % 256).toString(16).padStart(2, '0')}${((i * 97 + 10) % 256).toString(16).padStart(2, '0')}:s=96x96`, '-frames:v', '1'], path.join(FIX, `studio-ref-${String(i + 1).padStart(2, '0')}.png`)));
const PIC_SHA = PICS.map(sha);

// ---- helpers --------------------------------------------------------------------------------------------------------------------
const api = async (page: Page, p: string) => (await page.request.get(p)).json();
const csrf = async (page: Page) => (await api(page, '/api/me')).csrf as string;
const engine = async (page: Page) => (await page.request.get(`${ENG}/__state`)).json();
const advance = (page: Page, n: number, job?: string) => page.request.post(`${ENG}/__control/advance?n=${n}${job ? `&job=${job}` : ''}`);
const control = (page: Page, data: object) => page.request.post(`${ENG}/__control/config`, { data });
const resetEngine = (page: Page) => page.request.post(`${ENG}/__reset`);
const pid = (page: Page) => new URL(page.url()).searchParams.get('project')!;
const project = async (page: Page) => api(page, `/api/studio/projects/${pid(page)}`);
const spec = async (page: Page) => (await project(page)).spec;
function probe(buf: Buffer) {
  const f = path.join(TMP, `probe-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.bin`); writeFileSync(f, buf);
  const j = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', f]).toString());
  const v = j.streams.find((s: any) => s.codec_type === 'video');
  return { w: +v.width, h: +v.height, d: +j.format.duration };
}
const content = async (page: Page, id: string) => Buffer.from(await (await page.request.get(`/api/media/assets/${id}/content`)).body());
const panel = (page: Page) => page.getByTestId('panel');
const stepTo = (page: Page, id: string) => page.locator(`button[data-step="${id}"]`).click();
const tool = (page: Page, name: string) => panel(page).getByRole('toolbar', { name: 'Prepare tools' }).getByRole('button', { name, exact: true }).click();
const tiles = (page: Page) => page.getByTestId('ref-gallery').locator('li[data-ref-index]');
const seconds = (t: string) => { const m = t.trim().match(/^(?:(\d+):)?(\d+(?:\.\d+)?)$/); return m ? (m[1] ? +m[1] * 60 : 0) + +m[2] : NaN; };

async function openStudio(page: Page) {
  await page.goto('/openjutsu?new=1');
  await expect(page.getByTestId('start')).toBeVisible();
}
/** Start a project the way a person does: drop a video on the first screen. */
async function startWith(page: Page, file = F.clip) {
  await openStudio(page);
  await drop(page, page.getByTestId('start'), [file]);
  await expect(page.getByTestId('panel').getByRole('heading', { name: 'Prepare the video' })).toBeVisible({ timeout: 40_000 });
  await expect(page.getByTestId('stage-frame')).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get('project')).toBeTruthy();
}
const planAfter = (page: Page, act: () => Promise<unknown>) => Promise.all([page.waitForResponse((r) => /\/plan$/.test(r.url()) && r.request().method() === 'POST', { timeout: 10_000 }), act()]).then(async ([r]) => (await r.json()).plan);
async function addPictures(page: Page, files: string[]) {
  const before = await tiles(page).count();
  await page.locator('input[type=file][accept="image/*"]').setInputFiles(files);
  await expect(tiles(page)).toHaveCount(before + files.length, { timeout: 40_000 });
}
async function chooseMode(page: Page, label: string | RegExp) {
  await stepTo(page, 'operation');
  await page.getByTestId('modes').getByRole('radio', { name: label }).click();
}
/** Drag one gallery tile onto another with a real pointer (HTML5 drag and drop needs a few moves before the browser starts it). */
async function dragTile(page: Page, from: number, to: number) {
  const src = tiles(page).nth(from).locator('.pic'); await src.scrollIntoViewIfNeeded();
  const a = (await src.boundingBox())!;
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2); await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 12, a.y + a.height / 2 - 12, { steps: 4 });
  const dst = tiles(page).nth(to).locator('.pic'); await dst.scrollIntoViewIfNeeded();
  const b = (await dst.boundingBox())!;
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 10 });
  await page.mouse.up();
}
const refOrder = async (page: Page) => (await spec(page)).references.map((r: any) => r.asset) as string[];

test.beforeEach(async ({ page }) => {
  // runs from earlier tests are still "running" on the manual clock: stop them so the per-person limit never gets in the way
  const csrfToken = await csrf(page);
  for (const j of (await api(page, '/api/studio/jobs')) as any[]) if (['queued', 'running'].includes(j.status)) await page.request.post(`/api/media/jobs/${j.job}/cancel`, { data: {}, headers: { 'x-csrf-token': csrfToken } });
  await resetEngine(page); await control(page, { clock: 'manual' });
});

// =====================================================================================================================================
test('1 a video dropped on the first screen starts a project: facts shown, saved on the server, and it comes back after a reload', async ({ page }) => {
  await startWith(page);
  await stepTo(page, 'video');
  const facts = page.getByTestId('video-facts');
  await expect(facts).toContainText('harbour.mp4');
  await expect(facts).toContainText('640×360');
  await expect(facts).toContainText('Landscape');
  await expect(facts).toContainText('0:06.00');
  const p = await project(page);
  expect(p.spec.source_asset).toBeTruthy();
  expect(p.source.width).toBe(640);
  const url = page.url();
  await page.reload();
  await expect(page.getByTestId('video-facts')).toContainText('harbour.mp4');
  expect(pid(page)).toBe(p.id);
  // and from the bare address the newest project is reopened
  await page.goto('/openjutsu');
  await expect(page.getByTestId('video-facts').or(page.getByTestId('panel'))).toBeVisible();
  await expect.poll(() => page.url()).toContain(p.id);
  void url;
  await shot(page, 'studio-01-video');
});

test('2 trim by dragging a handle and by typing exact times; the selected length is shown and stored; playback stays inside the selection', async ({ page }) => {
  await startWith(page);
  await tool(page, 'Trim');
  const dock = page.getByTestId('trim-dock');
  await expect(dock).toBeVisible();
  await expect(page.getByTestId('trim-original')).toHaveText('0:06.00');
  await expect(page.getByTestId('trim-selected')).toHaveText('0:06.00');
  await expect(dock).toContainText('This model takes up to 30 s');
  // drag the end handle to the middle of the timeline
  const track = dock.locator('.track'), tb = (await track.boundingBox())!;
  const h = page.getByRole('slider', { name: 'Trim end' }), hb = (await h.boundingBox())!;
  await page.mouse.move(hb.x + hb.width / 2, hb.y + hb.height / 2);
  await page.mouse.down();
  await page.mouse.move(tb.x + tb.width * 0.75, hb.y + hb.height / 2, { steps: 6 });
  await page.mouse.move(tb.x + tb.width * 0.5, hb.y + hb.height / 2, { steps: 6 });
  await page.mouse.up();
  const dragged = seconds(await page.getByTestId('trim-selected').innerText());
  expect(dragged).toBeGreaterThan(2.6); expect(dragged).toBeLessThan(3.4);
  await expect.poll(async () => (await spec(page)).trim?.end ?? 0).toBeGreaterThan(2.6);
  // exact times
  await dock.getByLabel('Start', { exact: true }).fill('1.5'); await dock.getByLabel('Start', { exact: true }).press('Enter');
  await dock.getByLabel('End', { exact: true }).fill('4.5'); await dock.getByLabel('End', { exact: true }).press('Enter');
  await expect(page.getByTestId('trim-selected')).toHaveText('0:03.00');
  await expect(page.getByTestId('trim-original')).toHaveText('0:06.00');
  await expect.poll(async () => (await spec(page)).trim).toEqual({ start: 1.5, end: 4.5 });
  // preview of the selection: it starts at the selection and never leaves it
  const v = page.locator('[data-testid="stage-frame"] video');
  await page.getByRole('button', { name: 'Play', exact: true }).click();
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.currentTime)).toBeGreaterThan(1.6);
  await page.waitForTimeout(3600);   // longer than the 3 s selection: it must have looped
  const t = await v.evaluate((x: HTMLVideoElement) => x.currentTime);
  expect(t).toBeGreaterThanOrEqual(1.4); expect(t).toBeLessThanOrEqual(4.6);
  await page.getByRole('button', { name: 'Pause', exact: true }).click();
  await shot(page, 'studio-02-trim');
  // typing a nonsense time is refused in plain words and changes nothing
  await dock.getByLabel('End', { exact: true }).fill('99'); await dock.getByLabel('End', { exact: true }).press('Enter');
  await expect(dock).toContainText('Use a time like 1:05.25');
  expect((await spec(page)).trim).toEqual({ start: 1.5, end: 4.5 });
  // Reset in the toolbar brings the whole video back (after a second press that confirms it)
  await panel(page).getByRole('button', { name: /^Reset$/ }).click();
  await panel(page).getByRole('button', { name: /Press again to reset/ }).click();
  await expect.poll(async () => (await spec(page)).trim).toBeNull();
  await page.reload();
  await tool(page, 'Trim');
  await expect(page.getByTestId('trim-selected')).toHaveText('0:06.00');
});

test('3 a clip longer than the model takes can be trimmed down; the limit is shown and Generate says why it is off until it fits', async ({ page }) => {
  await startWith(page, F.big);
  await tool(page, 'Trim');
  const dock = page.getByTestId('trim-dock');
  await expect(page.getByTestId('trim-original')).toHaveText('0:40.00');
  await expect(dock).toContainText('takes up to 30 s');
  await expect(dock).toContainText('Your selection is 40.0 s');
  await dock.getByLabel('End', { exact: true }).fill('35'); await dock.getByLabel('End', { exact: true }).press('Enter');
  await expect(dock).toContainText('Your selection is 35.0 s but this model takes up to 30 s');
  await expect(dock).not.toContainText('Your selection is 40.0 s');
  await stepTo(page, 'generate');
  await expect(page.getByTestId('generate')).toBeDisabled();
  await expect(page.getByTestId('blockers')).toContainText('Trim it to 30 s or less');
  await stepTo(page, 'prepare');
  await page.getByRole('button', { name: /use 30 s from the start/ }).click();
  await expect(page.getByTestId('trim-selected')).toHaveText('0:30.00');
  await expect.poll(async () => (await spec(page)).trim).toEqual({ start: 0, end: 30 });
});

test('4 crop by dragging the frame; 9:16 sets and locks the shape; Crop, Pad and Stretch give different plans; the live framing preview follows', async ({ page }) => {
  await startWith(page);
  await tool(page, 'Crop');
  const frame = page.getByRole('group', { name: /^Crop frame/ });
  await expect(frame).toBeVisible();
  const se = page.locator('.hd.se'), sb = (await se.boundingBox())!;
  await page.mouse.move(sb.x + sb.width / 2 - 2, sb.y + sb.height / 2 - 2);
  await page.mouse.down();
  await page.mouse.move(sb.x - 80, sb.y - 40, { steps: 6 });
  await page.mouse.up();
  await expect.poll(async () => (await spec(page)).crop?.w ?? 640).toBeLessThan(600);
  let c = (await spec(page)).crop;
  expect(c.x).toBe(0); expect(c.y).toBe(0); expect(c.h).toBeLessThan(360);
  expect(c.w % 2).toBe(0);
  // moving it with the arrow keys (a pointer is not required)
  await frame.focus();
  await page.keyboard.press('Shift+ArrowRight');
  await expect.poll(async () => (await spec(page)).crop.x).toBe(10);
  // 9:16: the frame becomes that shape, in the display coordinates of the picture
  await tool(page, 'Aspect ratio');
  await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: '9:16' }).click();
  await expect.poll(async () => { const k = (await spec(page)).crop; return k ? k.w / k.h : 0; }).toBeCloseTo(9 / 16, 1);
  await expect(page.getByTestId('framing-preview')).toHaveAttribute('data-treatment', 'crop');
  const ratio = +(await page.getByTestId('framing-preview').getAttribute('data-ratio'))!;
  expect(ratio).toBeCloseTo(0.5625, 2);
  // the shape stays locked while the frame is resized
  await tool(page, 'Crop');
  const lock = page.getByRole('checkbox', { name: /Keep the 9:16 shape/ });
  await expect(lock).toBeChecked();
  const se2 = page.locator('.hd.se'), b2 = (await se2.boundingBox())!;
  await page.mouse.move(b2.x + b2.width / 2 - 2, b2.y + b2.height / 2 - 2); await page.mouse.down(); await page.mouse.move(b2.x - 30, b2.y - 90, { steps: 6 }); await page.mouse.up();
  await expect.poll(async () => { const k = (await spec(page)).crop; return k.w / k.h; }).toBeCloseTo(9 / 16, 1);
  // treatments
  await tool(page, 'Aspect ratio');
  const treatment = page.getByRole('radiogroup', { name: 'Treatment' });
  await page.getByRole('radio', { name: '9:16' }).click();
  let plan = await planAfter(page, () => treatment.getByRole('radio', { name: 'Pad' }).click());
  expect(plan.frame.treatment).toBe('pad'); expect(plan.frame.pad).toBeTruthy(); expect(plan.frame.stretch).toBeUndefined();
  await expect(page.getByTestId('pipe-prepared')).toContainText('640×1138');
  await expect(page.getByTestId('framing-preview')).toHaveAttribute('data-treatment', 'pad');
  plan = await planAfter(page, () => treatment.getByRole('radio', { name: /Stretch/ }).click());
  expect(plan.frame.treatment).toBe('stretch'); expect(plan.frame.stretch).toBeTruthy(); expect(plan.frame.pad).toBeUndefined();
  await expect(page.getByTestId('treatment-note')).toContainText('distorts');
  await expect(treatment.getByText('distorts')).toBeVisible();   // Stretch is marked on its own button
  plan = await planAfter(page, () => treatment.getByRole('radio', { name: 'Crop' }).click());
  expect(plan.frame.treatment).toBe('crop'); expect(plan.frame.pad).toBeUndefined();
  expect(plan.prepared.width / plan.prepared.height).toBeCloseTo(9 / 16, 2);
  expect((await spec(page)).frame).toMatchObject({ aspect: '9:16', treatment: 'crop' });
  // custom shape and back to the original
  await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: 'Custom' }).click();
  await page.getByLabel('Ratio width').fill('5'); await page.getByLabel('Ratio width').press('Tab');
  await page.getByLabel('Ratio height').fill('4'); await page.getByLabel('Ratio height').press('Tab');
  await expect.poll(async () => (await spec(page)).frame.ratio).toBeCloseTo(1.25, 2);
  await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: 'Original' }).click();
  await shot(page, 'studio-04-aspect');
});

test('5 resolution presets and a custom size with the shape locked or free; the pipeline shows four different numbers and says when it only scales up', async ({ page }) => {
  await startWith(page);
  await tool(page, 'Resolution');
  await expect(page.getByTestId('pipe-input')).toContainText('640×360');
  await expect(page.getByTestId('pipe-memory')).toContainText('extrapolated from measured runs');   // not measured at 480p: said so, with the engine's own words
  const res = page.getByRole('radiogroup', { name: 'Resolution' });
  const plan = await planAfter(page, () => res.getByRole('radio', { name: '720p' }).click());
  expect(plan.output.requested).toMatchObject({ width: 1280, height: 720 });
  await expect(page.getByTestId('pipe-output')).toContainText('1280×720');
  await expect(page.getByTestId('pipe-processing')).toContainText('1024×576');
  await expect(page.getByTestId('pipe-processing')).not.toContainText('1280×720');
  await expect(page.getByTestId('pipe-why')).toContainText('fixed sizes');
  await expect(page.getByTestId('pipe-output-note')).toContainText('no new detail');
  await expect(page.getByTestId('pipe-differs')).toContainText('You asked for 720p');   // requested and effective values side by side
  await expect.poll(async () => (await spec(page)).resolution.preset).toBe('720p');
  // 4K is far beyond what the model can process: said so, never changed silently
  await res.getByRole('radio', { name: '4K' }).click();
  await expect(page.getByTestId('pipe-output')).toContainText('3840×2160');
  await expect(page.getByTestId('pipe-processing')).toContainText('768');
  await expect(page.getByTestId('pipe-output-note')).toContainText('scaling it up');
  await expect(page.getByTestId('pipeline')).toContainText('larger than the model can process');
  // original size: the model still works at a supported size and the output is scaled down to what you gave it
  await res.getByRole('radio', { name: 'Original' }).click();
  await expect(page.getByTestId('pipe-output')).toContainText('640×360');
  // custom: width and height follow each other while the shape is kept
  await res.getByRole('radio', { name: 'Custom' }).click();
  const w = page.getByLabel('Output width in pixels'), hh = page.getByLabel('Output height in pixels');
  await w.fill('1308'); await w.press('Tab');
  await expect(hh).toHaveValue('736');
  await expect(page.getByTestId('pipe-processing')).toContainText('736');
  await expect(page.getByTestId('pipe-memory')).toContainText('measured');      // a size the engine really measured shows its number
  await expect(page.getByTestId('pipe-memory')).not.toContainText('has not been measured');
  // free shape: the other box is left alone
  await page.getByRole('checkbox', { name: 'Keep the shape' }).uncheck();
  await hh.fill('400'); await hh.press('Tab');
  await expect(w).toHaveValue('1308');
  await expect.poll(async () => (await spec(page)).resolution).toMatchObject({ preset: 'custom', width: 1308, height: 400, lock_aspect: false });
  await expect(page.getByTestId('pipe-output')).toContainText('1308×400');
  await shot(page, 'studio-05-resolution');
});

test('6 9:16 + 5 s + 720p together: prepare makes a real file with exactly that picture and length, reuses it the second time, and leaves the original alone', async ({ page }) => {
  await startWith(page);
  const before = (await project(page)).source;
  const originalSha = sha256(await content(page, before.id));
  await tool(page, 'Aspect ratio');
  await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: '9:16' }).click();
  await tool(page, 'Trim');
  const dock = page.getByTestId('trim-dock');
  await dock.getByLabel('End', { exact: true }).fill('5'); await dock.getByLabel('End', { exact: true }).press('Enter');
  await tool(page, 'Resolution');
  await page.getByRole('radiogroup', { name: 'Resolution' }).getByRole('radio', { name: '720p' }).click();
  await expect(page.getByTestId('pipe-output')).toContainText('720×1280');
  await expect(page.getByTestId('pipe-prepared')).toContainText('202×360');
  await expect(page.getByTestId('pipe-prepared')).toContainText('5 s');
  await page.getByRole('button', { name: 'Prepare video' }).click();
  await expect(page.getByTestId('prep-done')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByTestId('prep-done')).toContainText('Prepared.');
  await expect(page.getByTestId('prep-done')).toContainText('202×360');
  const p = await project(page);
  expect(p.prepared_current).toBe(true);
  const a = p.prepared_asset;
  expect(a.origin).toBe('prepared');
  const f = probe(await content(page, a.id));
  expect(f.w).toBe(202); expect(f.h).toBe(360); expect(f.d).toBeGreaterThan(4.9); expect(f.d).toBeLessThan(5.15);
  // asking again reuses the identical copy (nothing is encoded twice)
  await page.getByRole('button', { name: 'Prepare again' }).click();
  await expect(page.getByTestId('prep-done')).toContainText('Reused the prepared copy');
  expect((await project(page)).prepared_asset.id).toBe(a.id);
  // the original is exactly what was uploaded
  expect(sha256(await content(page, before.id))).toBe(originalSha);
  expect(originalSha).toBe(sha(F.clip));
  // changing a decision makes the copy out of date, and says so
  await tool(page, 'Trim');
  await dock.getByLabel('End', { exact: true }).fill('4'); await dock.getByLabel('End', { exact: true }).press('Enter');
  await expect(page.getByTestId('prep-stale')).toBeVisible();
  await shot(page, 'studio-06-prepared');
});

test('7 references: one, three, nine and twelve pictures; which are used follows the operation; reorder by drag and keyboard; label; remove; reload keeps everything', async ({ page }) => {
  await startWith(page);
  await chooseMode(page, /^Subject replacement/);
  await stepTo(page, 'references');
  await expect(page.getByTestId('ref-count')).toHaveText('No pictures yet');
  await addPictures(page, [PICS[0]]);
  await expect(page.getByTestId('ref-count')).toHaveText('1 of 1 used in this run');
  await expect(page.getByTestId('badge-used')).toHaveText('Used in this run · 1');
  await addPictures(page, PICS.slice(1, 3));                         // 3 in all
  await expect(page.getByTestId('badge-used')).toHaveCount(1);       // swap takes one
  await expect(page.getByTestId('badge-unused')).toHaveCount(2);
  await addPictures(page, PICS.slice(3, 9));                         // 9 in all
  await addPictures(page, PICS.slice(9, 12));                        // 12 in all, added at any time
  await expect(page.getByTestId('ref-count')).toHaveText('1 of 12 used in this run');
  await expect(page.getByTestId('badge-used')).toHaveCount(1);
  await expect(page.getByTestId('badge-unused')).toHaveCount(11);
  await expect(page.getByTestId('badge-unused').first()).toHaveText('Not used: this operation takes 1 picture');
  await shot(page, 'studio-07-12-refs');
  // never implies an unused picture is in the run: the summary lists exactly one and says the others are kept
  await stepTo(page, 'generate');
  await expect(page.getByTestId('sum-used').locator('li')).toHaveCount(1);
  await expect(page.getByTestId('sum-kept').locator('li')).toHaveCount(11);
  await stepTo(page, 'references');
  await expect.poll(async () => (await refOrder(page)).length).toBe(12);
  const ids0 = await refOrder(page);
  expect((await spec(page)).references.filter((r: any) => r.active)).toHaveLength(1);
  // an unused picture can be switched on: with a limit of one it takes the place of the other, and the page says so
  await tiles(page).nth(2).getByRole('checkbox').check();
  await expect(tiles(page).nth(2).getByTestId('badge-used')).toHaveText('Used in this run · 1');
  await expect(tiles(page).nth(0).getByTestId('badge-unused')).toBeVisible();
  // reorder by drag
  await dragTile(page, 5, 1);
  await expect.poll(async () => (await refOrder(page))[1]).toBe(ids0[5]);
  // reorder by keyboard: Alt+Arrow
  await tiles(page).nth(1).focus();
  await page.keyboard.press('Alt+ArrowRight');
  await expect.poll(async () => (await refOrder(page))[2]).toBe(ids0[5]);
  await expect(tiles(page).nth(2)).toBeFocused();
  await page.keyboard.press('Alt+ArrowLeft'); await page.keyboard.press('Alt+ArrowLeft');
  await expect.poll(async () => (await refOrder(page))[0]).toBe(ids0[5]);
  // label and remove
  const first = tiles(page).nth(0);
  await first.getByPlaceholder('Label (optional)').fill('the new singer');
  const doomed = (await refOrder(page))[7];
  await tiles(page).nth(7).getByRole('button', { name: /^Remove / }).click();
  await expect(tiles(page)).toHaveCount(11);
  await expect.poll(async () => (await refOrder(page)).includes(doomed)).toBe(false);
  await expect.poll(async () => (await spec(page)).references[0].label).toBe('the new singer');
  // everything survives a reload, in the same order, with the same labels and the same pictures marked
  const snap = (await spec(page)).references;
  await page.reload();
  await stepTo(page, 'references');
  await expect(tiles(page)).toHaveCount(11);
  expect((await spec(page)).references).toEqual(snap);
  await expect(tiles(page).nth(0).getByPlaceholder('Label (optional)')).toHaveValue('the new singer');
  await expect(page.getByTestId('badge-used')).toHaveCount(1);
  // viewer for a picture
  await tiles(page).nth(0).getByRole('button', { name: /^Preview / }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await page.keyboard.press('Escape');
});

test('8 reference limits follow the operation: character replacement takes four, never more, and the person decides which four', async ({ page }) => {
  await startWith(page);
  await chooseMode(page, /^Character replacement/);
  await page.getByRole('checkbox', { name: /I understand/ }).check();
  await stepTo(page, 'references');
  await addPictures(page, PICS.slice(0, 6));
  await expect(page.getByTestId('ref-count')).toHaveText('4 of 6 used in this run');   // this operation takes four: the first four start as used
  await expect(page.getByRole('button', { name: 'Use the first 4 pictures' })).toHaveCount(0);   // nothing to offer: they already are the first four
  await tiles(page).nth(1).getByRole('checkbox').uncheck();
  await expect(page.getByTestId('ref-count')).toHaveText('3 of 6 used in this run');
  await page.getByRole('button', { name: 'Use the first 4 pictures' }).click();
  await expect(page.getByTestId('badge-used')).toHaveCount(4);
  await expect(page.getByTestId('badge-used')).toHaveText(['Used in this run · 1', 'Used in this run · 2', 'Used in this run · 3', 'Used in this run · 4']);
  await expect(page.getByTestId('badge-unused')).toHaveCount(2);
  // a fifth cannot be switched on until one is switched off, and the page says why (and the box does not stay ticked)
  await tiles(page).nth(4).getByRole('checkbox').click();
  await expect(tiles(page).nth(4).getByRole('checkbox')).not.toBeChecked();
  await expect(page.getByRole('status').filter({ hasText: 'Turn another one off first' })).toHaveCount(1);
  await expect(page.getByTestId('badge-used')).toHaveCount(4);
  await tiles(page).nth(1).getByRole('checkbox').uncheck();
  await tiles(page).nth(4).getByRole('checkbox').check();
  await expect(page.getByTestId('badge-used')).toHaveCount(4);
  await expect(tiles(page).nth(1).getByTestId('badge-unused')).toBeVisible();
  await expect(tiles(page).nth(4).getByTestId('badge-used')).toHaveText('Used in this run · 4');   // numbered in the order they are sent
  // each used picture can say which target it stands for
  const sel = tiles(page).nth(0).getByRole('combobox');
  await sel.selectOption('2');
  await expect.poll(async () => (await spec(page)).references[0]?.subject).toBe(2);
  // changing to an operation that takes one picture switches the extra ones off, visibly (nothing is deleted)
  await chooseMode(page, /^Subject replacement/);
  await stepTo(page, 'references');
  await expect(page.getByTestId('badge-used')).toHaveCount(1);
  await expect(tiles(page)).toHaveCount(6);
});

test('9 operation cards show the engine\'s status: ready, experimental needs an opt-in, unavailable cannot be chosen; models that cannot do it say why', async ({ page }) => {
  await startWith(page);
  await stepTo(page, 'operation');
  const modes = page.getByTestId('modes');
  await expect(modes.getByRole('radio')).toHaveCount(8);
  const card = (m: string) => modes.locator(`[data-mode="${m}"]`);
  await expect(card('subject_replacement')).toHaveAttribute('data-status', 'ready');
  await expect(card('character_replacement')).toHaveAttribute('data-status', 'experimental');
  await expect(card('motion_transfer')).toHaveAttribute('data-status', 'unavailable');
  await expect(card('motion_transfer')).toHaveAttribute('aria-disabled', 'true');
  await expect(card('motion_transfer')).toContainText('no motion-transfer graph is installed on this engine');
  await expect(card('reference_to_video')).toHaveAttribute('aria-disabled', 'true');
  await expect(card('video_restyling')).toContainText('restyling has not been wired or tested');
  // unavailable is not selectable
  await card('motion_transfer').click({ force: true });
  await expect(card('motion_transfer')).toHaveAttribute('aria-checked', 'false');
  expect((await spec(page)).mode).toBeNull();
  // ready is selectable straight away
  await card('subject_replacement').click();
  await expect(card('subject_replacement')).toHaveAttribute('aria-checked', 'true');
  await expect.poll(async () => (await spec(page)).mode).toBe('subject_replacement');
  await expect(page.getByTestId('experimental')).toHaveCount(0);
  // experimental needs an explicit opt-in that is stored, and Generate stays off until it is given
  await card('character_replacement').click();
  const box = page.getByTestId('experimental');
  await expect(box).toContainText('experimental');
  await expect(box.getByRole('checkbox')).not.toBeChecked();
  expect((await spec(page)).accept_experimental).toBe(false);
  await stepTo(page, 'generate');
  await expect(page.getByTestId('blockers')).toContainText('experimental');
  await stepTo(page, 'operation');
  await box.getByRole('checkbox').check();
  await expect.poll(async () => (await spec(page)).accept_experimental).toBe(true);
  await shot(page, 'studio-09-modes');
  // models: the one that makes video from a prompt is listed, greyed, with the reason; the editing model is chosen for us
  await stepTo(page, 'model');
  const models = page.getByTestId('models');
  await expect(models.locator('[aria-disabled="true"]')).toContainText('cannot edit a video you give it');
  await expect(models.locator('[aria-checked="true"]')).toContainText('openjutsu-minimax-h3');
  await expect(models.locator('[aria-disabled="true"]').getByText('Cannot do this')).toBeVisible();
  await models.locator('[aria-disabled="true"]').click({ force: true });
  await expect(models.locator('[aria-checked="true"]')).toContainText('openjutsu-minimax-h3');
  // switching back to a ready mode drops the opt-in (it is per operation)
  await chooseMode(page, /^Subject replacement/);
  await expect.poll(async () => (await spec(page)).accept_experimental).toBe(false);
});

test('10 the subject: words, kind, a box on the first frame; marking offered only because the engine advertises it; stored in the project', async ({ page }) => {
  await startWith(page);
  await chooseMode(page, /^Subject replacement/);
  const sec = page.getByTestId('subject-section');
  await expect(sec).toBeVisible();
  await sec.getByLabel('Subject description').fill('the singer on the left');
  await expect(sec.getByRole('radio', { name: 'Person' })).toHaveAttribute('aria-checked', 'true');
  await sec.getByRole('radio', { name: 'Animal' }).click();
  await expect(sec).toContainText('built for person');             // kinds the operation was not tested with are allowed but said
  await sec.getByRole('radio', { name: 'Person' }).click();
  await sec.getByRole('button', { name: 'the singer on the left' }).click();   // the example chip fills the same words
  await expect.poll(async () => (await spec(page)).subject).toMatchObject({ text: 'the singer on the left', kind: 'person' });
  // the engine advertises a box but not points
  await expect(sec.getByRole('radio', { name: 'Draw a box' })).toBeVisible();
  await expect(sec.getByRole('radio', { name: 'Click points' })).toHaveCount(0);
  await expect(page.getByTestId('no-marking')).toHaveCount(0);
  // the first frame is shown frozen at the start of the kept part
  const v = page.locator('[data-testid="stage-frame"] video');
  await expect(page.getByTestId('stage')).toContainText('First frame of the clip');
  await sec.getByRole('radio', { name: 'Draw a box' }).click();
  const o = (await page.getByTestId('subject-overlay').boundingBox())!;
  await page.mouse.move(o.x + o.width * 0.2, o.y + o.height * 0.25);
  await page.mouse.down(); await page.mouse.move(o.x + o.width * 0.45, o.y + o.height * 0.5, { steps: 5 }); await page.mouse.move(o.x + o.width * 0.6, o.y + o.height * 0.8, { steps: 5 }); await page.mouse.up();
  await expect(page.getByTestId('subject-box')).toBeVisible();
  await expect.poll(async () => (await spec(page)).subject.box).not.toBeNull();
  const b = (await spec(page)).subject.box as number[];
  expect(b[0]).toBeCloseTo(0.2, 1); expect(b[1]).toBeCloseTo(0.25, 1); expect(b[2]).toBeCloseTo(0.4, 1); expect(b[3]).toBeCloseTo(0.55, 1);
  void v;
  await shot(page, 'studio-10-subject');
  await page.reload();
  await stepTo(page, 'operation');
  await expect(page.getByTestId('subject-box')).toBeVisible();
  await expect(page.getByLabel('Subject description')).toHaveValue('the singer on the left');
  await page.getByRole('button', { name: 'Clear marks' }).click();
  await expect.poll(async () => (await spec(page)).subject.box).toBeNull();
  // the check is offered only when the server says it can run one; this server does not, so there is no button to nowhere
  await expect(page.getByRole('button', { name: 'Check selection' })).toHaveCount(0);
  // the target in the video and the pictures are kept apart
  await expect(page.getByTestId('pictures-section')).toBeVisible();
  await expect(page.getByTestId('subject-section')).toContainText('In your video');
  await expect(page.getByTestId('pictures-section')).toContainText('Your pictures');
});

/** a complete, valid project up to the Generate step */
async function readyProject(page: Page, opts: { mode?: RegExp; refs?: number; experimental?: boolean } = {}) {
  await startWith(page);
  await chooseMode(page, opts.mode ?? /^Subject replacement/);
  if (opts.experimental) await page.getByRole('checkbox', { name: /I understand/ }).check();
  await page.getByLabel('Subject description').fill('the main person');
  await stepTo(page, 'references');
  await addPictures(page, PICS.slice(0, opts.refs ?? 3));
}

test('11 Generate sends exactly the active pictures, in order; the summary says the same; the live job appears with its two buttons; a reload restores it', async ({ page }) => {
  await readyProject(page, { mode: /^Character replacement/, refs: 6, experimental: true });
  await stepTo(page, 'references');
  await expect(page.getByTestId('ref-count')).toHaveText('4 of 6 used in this run');
  await tiles(page).nth(1).getByRole('checkbox').uncheck();          // picture 2 is kept but left out
  await tiles(page).nth(4).getByRole('checkbox').check();            // picture 5 takes its place
  await dragTile(page, 4, 0);                                        // and goes first
  await expect.poll(async () => (await spec(page)).references.filter((r: any) => r.active).length).toBe(4);
  await expect(page.getByTestId('save-state')).toHaveText('All changes saved');
  const order = await refOrder(page);
  await stepTo(page, 'model');
  await expect(page.getByTestId('models').locator('[aria-checked="true"]')).toContainText('openjutsu-minimax-h3');
  await stepTo(page, 'generate');
  const sum = page.getByTestId('summary');
  await expect(sum.getByTestId('sum-used').locator('li')).toHaveCount(4);
  await expect(sum.getByTestId('sum-kept').locator('li')).toHaveCount(2);
  await expect(sum.getByTestId('sum-subject')).toContainText('the main person');
  await expect(sum).toContainText('Character replacement');
  await expect(page.getByTestId('blockers')).toHaveCount(0);
  await page.getByTestId('generate').click();
  const live = page.getByTestId('live-job');
  await expect(live).toBeVisible({ timeout: 60_000 });
  await expect(live.getByRole('button', { name: 'VIEW PROCESS' })).toBeVisible();
  // exactly what the engine got: the active pictures, in the order shown, byte for byte
  const st = await engine(page);
  const sub = st.submissions.at(-1);
  expect(sub.files.refs).toHaveLength(4);
  const sp = (await spec(page)).references as any[];
  const sha_of = new Map<string, string>();     // which fixture each library picture is: by its stored name
  for (const r of sp) { const a = await api(page, `/api/media/assets/${r.asset}`); sha_of.set(r.asset, PIC_SHA[PICS.findIndex((f) => path.basename(f) === a.name)]); }
  const sent = sub.files.refs.map((f: any) => f.sha256);
  expect(sent).toEqual(sp.filter((r) => r.active).map((r) => sha_of.get(r.asset)));
  expect(sent).not.toContain(PIC_SHA[1]);                           // the one left out is not among them
  // the project and job are in the address; the job is listed
  const job = new URL(page.url()).searchParams.get('job')!;
  expect(job).toMatch(/^mj_|^[a-z]{2}_/);
  // while the engine works the page shows the engine's own stage text and an honest bar
  await advance(page, 3, sub.job);
  await expect(live.getByTestId('job-stage')).toContainText(/loading|starting|tracking|models|subject/i, { timeout: 20_000 });
  await expect(live.getByTestId('job-progress').or(live.getByTestId('job-progress-indeterminate'))).toBeVisible();
  await shot(page, 'studio-11-live');
  // reload: the same job view comes back from the project
  await page.reload();
  await expect(page.getByTestId('live-job')).toBeVisible({ timeout: 20_000 });
  expect(new URL(page.url()).searchParams.get('job')).toBe(job);
  // and from the Jobs list after starting over
  await page.getByRole('button', { name: /^New video/ }).click();
  await expect(page.getByTestId('start')).toBeVisible();
  await page.getByTestId('open-jobs').click();
  const row = page.getByTestId('jobs-list').locator('li').first();
  await expect(row).toContainText('Character replacement');
  await row.getByRole('button', { name: /^Open / }).click();
  await expect(page.getByTestId('live-job')).toBeVisible({ timeout: 20_000 });
  await expect(page.getByTestId('summary')).toBeVisible();
  expect(await refOrder(page)).toEqual(order);
  // the run remembers what it used: after the pictures are changed it can put them back
  const used = (await spec(page)).references.map((r: any) => [r.asset, r.active]);
  await stepTo(page, 'references');
  await tiles(page).nth(0).getByRole('checkbox').uncheck();
  await expect.poll(async () => JSON.stringify((await spec(page)).references.map((r: any) => [r.asset, r.active]))).not.toBe(JSON.stringify(used));
  await stepTo(page, 'generate');
  await page.getByRole('button', { name: /Put this run's pictures and subject back/ }).click();
  await expect.poll(async () => JSON.stringify((await spec(page)).references.map((r: any) => [r.asset, r.active]))).toBe(JSON.stringify(used));
});

test('12 a finished job shows the result in the viewer, offers a download and an export at the requested size; a failed job names the failing node', async ({ page }) => {
  await readyProject(page, { refs: 1 });
  // ask for 720p so that the output differs from what the model makes
  await stepTo(page, 'prepare'); await tool(page, 'Resolution');
  await page.getByRole('radiogroup', { name: 'Resolution' }).getByRole('radio', { name: '720p' }).click();
  await stepTo(page, 'generate');
  await page.getByTestId('generate').click();
  const live = page.getByTestId('live-job');
  await expect(live).toBeVisible({ timeout: 60_000 });
  await advance(page, 200);
  await expect(live).toHaveAttribute('data-status', 'completed', { timeout: 30_000 });
  await expect(live.getByTestId('job-result')).toBeVisible();
  const href = await live.getByRole('link', { name: 'Download the result' }).getAttribute('href');
  expect(href).toContain('/content');
  expect((await page.request.get(href!)).ok()).toBe(true);
  // the result file itself is a real video (what the engine double produced), then it opens in the same viewer as every library item
  const jobId = new URL(page.url()).searchParams.get('job')!;
  const resultAsset = (await api(page, '/api/studio/jobs')).find((j: any) => j.job === jobId).outputs.find((o: any) => o.kind === 'video');
  // read until the file decodes (the result is copied from the engine just before the job shows as completed); a file that never decodes still fails
  let rf: any, lastErr: any;
  for (let i = 0; i < 20 && !rf; i++) {
    try { rf = probe(await content(page, resultAsset.id)); } catch (e) { lastErr = e; await page.waitForTimeout(500); }
  }
  if (!rf) throw lastErr;
  expect(rf.w).toBe(resultAsset.width); expect(rf.h).toBe(resultAsset.height); expect(rf.d).toBeGreaterThan(0.5);
  await live.getByTestId('job-result').getByRole('button', { name: 'Open', exact: true }).click();
  const dlg = page.getByRole('dialog');
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('video')).toHaveCount(1);
  await expect(dlg).toContainText(`${resultAsset.width}×${resultAsset.height}`);
  await page.keyboard.press('Escape');
  // the export at the requested size is offered automatically
  const offer = live.getByTestId('export-offer');
  await expect(offer).toContainText('You asked for 1280×720');
  await offer.getByRole('button', { name: /Export at output size \(1280×720\)/ }).click();
  await expect(offer).toContainText('Exported at 1280×720', { timeout: 40_000 });
  const lib = (await api(page, '/api/media/assets?origin=export')) as any[];
  expect(lib.some((a) => a.width === 1280 && a.height === 720 && a.origin === 'export')).toBe(true);
  await shot(page, 'studio-12-done');
  // a failing run: the engine reports the node that failed and the page shows it in words
  await control(page, { variant_next: 'fail' });
  await page.getByTestId('generate').click();
  await expect.poll(async () => (await engine(page)).submissions.length, { timeout: 30_000 }).toBe(2);
  await advance(page, 200);
  await expect(page.getByTestId('live-job')).toHaveAttribute('data-status', 'failed', { timeout: 30_000 });
  await expect(page.getByTestId('job-error')).toContainText('Trim Overlap');
  await expect(page.getByTestId('live-job').getByTestId('job-result')).toHaveCount(0);
});

test('13 the process and ComfyUI views open from the live job when this build has them', async ({ page }) => {
  await readyProject(page, { refs: 1 });
  await stepTo(page, 'generate');
  await page.getByTestId('generate').click();
  const live = page.getByTestId('live-job');
  await expect(live).toBeVisible({ timeout: 60_000 });
  const job = new URL(page.url()).searchParams.get('job')!;
  const ins = await api(page, `/api/studio/jobs/${job}/inspect`);
  await live.getByRole('button', { name: 'VIEW PROCESS' }).click();
  const dlg = page.getByRole('dialog', { name: 'Job process' });
  await expect(dlg).toBeVisible();
  await expect(dlg.locator('.inner')).not.toBeEmpty();
  await page.keyboard.press('Escape');
  await expect(dlg).toHaveCount(0);
  // VIEW COMFYUI only when the engine says there is a graph to show
  if (ins.comfy?.available) {
    await expect(live.getByRole('button', { name: 'VIEW COMFYUI' })).toBeVisible();
    await live.getByRole('button', { name: 'VIEW COMFYUI' }).click();
    await expect(page.getByRole('dialog', { name: 'ComfyUI graph' })).toBeVisible();
    await page.keyboard.press('Escape');
  } else await expect(live.getByRole('button', { name: 'VIEW COMFYUI' })).toHaveCount(0);
});

test('14 pictures and videos can be dropped anywhere on the page; a picture before any video is refused with a reason', async ({ page }) => {
  await openStudio(page);
  await drop(page, page.getByTestId('start'), [PICS[0]]);
  await expect(page.getByTestId('start')).toContainText('Add the video first');
  await drop(page, page.getByTestId('start'), [F.notes]);
  await expect(page.getByTestId('start')).toContainText('is not a picture or a video');
  await drop(page, page.getByTestId('start'), [F.clip]);
  await expect(page.getByTestId('panel').getByRole('heading', { name: 'Prepare the video' })).toBeVisible({ timeout: 40_000 });
  await stepTo(page, 'references');
  await drop(page, page.getByTestId('ref-gallery'), PICS.slice(0, 2));
  await expect(tiles(page)).toHaveCount(2, { timeout: 30_000 });
  // pasting a picture works too
  await page.evaluate(async (b64) => {
    const bin = atob(b64), u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i);
    const dt = new DataTransfer(); dt.items.add(new File([u], 'image.png', { type: 'image/png' }));
    window.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, readFileSync(PICS[2]).toString('base64'));
  await expect(tiles(page)).toHaveCount(3, { timeout: 30_000 });
});

test('15 a project from the Library video list: choose, continue earlier projects, delete a project without losing the video', async ({ page }) => {
  await startWith(page);
  const first = pid(page), src = (await project(page)).source.id;
  await page.getByRole('button', { name: /^New video/ }).click();
  await expect(page.getByTestId('start')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Earlier projects' })).toBeVisible();
  await page.getByRole('button', { name: 'From your Library' }).click();
  await page.getByRole('dialog', { name: 'Choose a video' }).getByRole('button', { name: /harbour/ }).first().click();
  await page.getByRole('dialog', { name: 'Choose a video' }).getByRole('button', { name: /^Attach/ }).click();
  await expect(page.getByTestId('panel').getByRole('heading', { name: 'Prepare the video' })).toBeVisible({ timeout: 30_000 });
  expect(pid(page)).not.toBe(first);
  await page.getByTestId('open-jobs').click();
  await page.getByRole('tab', { name: /^Projects/ }).click();
  const list = page.getByTestId('projects-list');
  expect(await list.locator('li').count()).toBeGreaterThanOrEqual(2);
  await list.locator('li').filter({ hasText: 'harbour' }).first().getByRole('button', { name: /^Delete / }).click();
  await list.getByRole('button', { name: 'Delete the project' }).click();
  await expect(page.getByTestId('start')).toBeVisible();   // it was the open one
  // the video itself is still in the library
  expect((await page.request.get(`/api/media/assets/${src}`)).ok()).toBe(true);
});

test('16 two windows: a save from a stale window is not silently lost; the person chooses', async ({ page, context }) => {
  await startWith(page);
  const other = await context.newPage();
  await other.goto(page.url());
  await expect(other.getByTestId('panel')).toBeVisible();
  await tool(other, 'Aspect ratio');
  await other.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: '1:1' }).click();
  await expect.poll(async () => (await spec(other)).frame.aspect).toBe('1:1');
  await tool(page, 'Aspect ratio');
  await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: '4:3' }).click();
  await expect(page.getByRole('alert').filter({ hasText: 'changed somewhere else' })).toBeVisible({ timeout: 10_000 });
  await page.getByRole('button', { name: 'Keep mine' }).click();
  await expect.poll(async () => (await spec(page)).frame.aspect).toBe('4:3');
  await other.close();
});

test.describe('on a phone', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  test('17 the core flow works at 390 px: video above the steps, nothing scrolls sideways, the controls are big enough to touch', async ({ page }) => {
    await startWith(page);
    const noSideScroll = () => page.evaluate(() => { const m = document.querySelector('main')!; return [document.documentElement.scrollWidth <= innerWidth + 1, m.scrollWidth <= m.clientWidth + 1]; });
    expect(await noSideScroll()).toEqual([true, true]);
    const st = (await page.getByTestId('stage').boundingBox())!, pn = (await panel(page).boundingBox())!;
    expect(st.y + st.height).toBeLessThanOrEqual(pn.y + 2);     // the video stage is above the step panel
    expect(st.width).toBeLessThanOrEqual(390);
    await tool(page, 'Trim');
    const dock = page.getByTestId('trim-dock');
    await dock.getByLabel('End', { exact: true }).fill('4'); await dock.getByLabel('End', { exact: true }).press('Enter');
    await expect(page.getByTestId('trim-selected')).toHaveText('0:04.00');
    await tool(page, 'Aspect ratio');
    await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: '9:16' }).click();
    expect(await noSideScroll()).toEqual([true, true]);
    // every button and control in the panel is at least 44 px tall
    const small = await panel(page).locator('button:visible, input:visible, select:visible').evaluateAll((els) => els.filter((e) => { const b = e.getBoundingClientRect(); return b.height < 43.5 && !(e as HTMLInputElement).type?.match(/checkbox|radio|range/); }).map((e) => `${e.tagName} ${(e.textContent || (e as HTMLInputElement).ariaLabel || '').trim().slice(0, 30)} ${Math.round(e.getBoundingClientRect().height)}`));
    expect(small).toEqual([]);
    await page.getByRole('button', { name: 'Prepare video' }).click();
    await expect(page.getByTestId('prep-done')).toBeVisible({ timeout: 60_000 });
    await stepTo(page, 'references');
    await addPictures(page, PICS.slice(0, 2));
    expect(await noSideScroll()).toEqual([true, true]);
    await page.getByRole('button', { name: 'Move Pictures 1 later' }).or(tiles(page).nth(0).getByRole('button', { name: /^Move .* later$/ })).first().click();   // the touch way to reorder
    await expect.poll(async () => (await refOrder(page)).length).toBe(2);
    await chooseMode(page, /^Subject replacement/);
    await page.getByLabel('Subject description').fill('the dancer');
    expect(await noSideScroll()).toEqual([true, true]);
    await stepTo(page, 'generate');
    await expect(page.getByTestId('generate')).toBeEnabled();
    await expect(page.getByTestId('summary')).toBeVisible();
    expect(await noSideScroll()).toEqual([true, true]);
    await shot(page, 'studio-17-mobile');
  });
});

async function axePass(page: Page, theme: string) {
  const { default: AxeBuilder } = await import('@axe-core/playwright');
  const t0 = Date.now();
  const axe = async (label: string) => {
    console.log(`axe ${label} at ${Math.round((Date.now() - t0) / 1000)}s`);
    await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getTiming().iterations !== Infinity).map((a) => a.finished.catch(() => null))));   // an endless progress animation never finishes
    const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
    return r.violations.map((v) => `${label}: ${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0]?.target.join(' ')} :: ${(v.nodes[0]?.any[0]?.message ?? v.nodes[0]?.failureSummary ?? '').replace(/\s+/g, ' ').slice(0, 160)}`);
  };
  const found: string[] = [];
  const me = await api(page, '/api/me');
  const setTheme = async (t: string) => { await page.request.patch('/api/me', { data: { theme: t }, headers: { 'x-csrf-token': await csrf(page) } }); };
  try {
    await setTheme(theme);
    await openStudio(page);
    found.push(...(await axe(`${theme} start`)));
    await drop(page, page.getByTestId('start'), [F.clip]);
    await expect(page.getByTestId('panel').getByRole('heading', { name: 'Prepare the video' })).toBeVisible({ timeout: 40_000 });
    for (const t of ['Trim', 'Crop', 'Resolution', 'Aspect ratio']) { await tool(page, t); await page.waitForTimeout(250); found.push(...(await axe(`${theme} prepare/${t}`))); }
    await stepTo(page, 'video'); found.push(...(await axe(`${theme} video`)));
    await chooseMode(page, /^Subject replacement/); await page.getByLabel('Subject description').fill('the dancer');
    await page.getByRole('radio', { name: 'Draw a box' }).click();
    found.push(...(await axe(`${theme} operation`)));
    await stepTo(page, 'references'); await addPictures(page, PICS.slice(0, 3)); found.push(...(await axe(`${theme} references`)));
    await stepTo(page, 'model'); found.push(...(await axe(`${theme} model`)));
    await stepTo(page, 'generate'); found.push(...(await axe(`${theme} generate`)));
    await page.getByTestId('generate').click();
    await expect(page.getByTestId('live-job')).toBeVisible({ timeout: 60_000 });
    found.push(...(await axe(`${theme} live job`)));
    await page.getByTestId('open-jobs').click();
    await expect(page.getByTestId('jobs-list')).toBeVisible();
    found.push(...(await axe(`${theme} jobs`)));
    await page.keyboard.press('Escape');
  } finally { await setTheme(me.user?.prefs?.theme ?? 'light'); }
  expect(found).toEqual([]);
}
test('18 accessibility: no WCAG A/AA violations on any step (light theme)', async ({ page }) => { test.setTimeout(240_000); await axePass(page, 'light'); });
test('19 accessibility: no WCAG A/AA violations on any step (dark theme)', async ({ page }) => { test.setTimeout(240_000); await axePass(page, 'dark'); });
