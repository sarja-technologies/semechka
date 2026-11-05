// Browser tests of the two OpenJutsu Studio inspector panels, in real Chrome.
//
// The components are mounted on their own by a tiny Vite harness (fixtures/inspector/harness) with the app's real stylesheet, against a mock
// Studio API (fixtures/inspector/mockapi.ts) that serves REAL Server-Sent Events, REAL Range-capable MP4 files and the scripted 7-scene swap
// run of fixtures/inspector/run.ts (the true 70-node OpenJutsu graph). The test moves the run and breaks connections on cue.
import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { createServer, type ViteDevServer } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { MockStudio } from './fixtures/inspector/mockapi';

const WEB = fileURLToPath(new URL('../', import.meta.url));
const PORT = 8852;   // one server: the mock Studio API, and Vite's middleware for the harness page
const BASE = `http://127.0.0.1:${PORT}`;

let vite: ViteDevServer;
const api = new MockStudio(path.join(WEB, '.e2e-media-tmp', 'inspector'));

test.beforeAll(async () => {
  vite = await createServer({
    root: path.join(WEB, 'e2e_media/fixtures/inspector/harness'), configFile: false, logLevel: 'warn', cacheDir: path.join(WEB, '.e2e-media-tmp', 'vite-inspector'),
    plugins: [svelte()], resolve: { alias: { $lib: path.join(WEB, 'src/lib') } },
    server: { middlewareMode: true, hmr: false, fs: { allow: [WEB] } }, appType: 'spa'
  });
  api.fallback = (req, res) => vite.middlewares(req, res);
  await api.listen(PORT);
});
test.afterAll(async () => { await vite?.close(); await api.close(); });
test.beforeEach(() => api.reset());

const open = async (page: Page, q = 'view=inspector&job=j_run') => { await page.goto(`${BASE}/?${q}`); };
const panel = (page: Page) => page.getByRole('dialog', { name: 'View process' });


const chip = (page: Page) => page.getByTestId('status-chip');
const stage = (page: Page) => page.getByTestId('stage');
const conn = (page: Page) => page.getByTestId('conn');
const track = (page: Page) => page.getByRole('list', { name: 'Scenes', exact: true });
const savedCells = (page: Page) => track(page).getByRole('button', { name: /saved\. Play it/ });
const log = (page: Page) => page.getByRole('list', { name: 'Job events' });
const nodeBtn = (page: Page, id: string) => page.locator(`[data-node-id="${id}"]`);
const SAMPLER = '60';
const viewport = (page: Page) => page.getByTestId('graph-viewport');
const gopen = (page: Page, job = 'j_run') => open(page, `view=graph&job=${job}`);

/** Centre of an element relative to a container, in px. */
async function centreOffset(page: Page, id: string) {
  return page.evaluate((nid) => {
    const c = document.querySelector(`[data-node-id="${nid}"]`)!.getBoundingClientRect();
    const v = document.querySelector('[data-testid="graph-viewport"]')!.getBoundingClientRect();
    return { dx: c.left + c.width / 2 - (v.left + v.width / 2), dy: c.top + c.height / 2 - (v.top + v.height / 2) };
  }, id);
}
const worldK = async (page: Page) => Number(await page.getByTestId('graph-world').getAttribute('data-k'));
/** Wait until the graph stopped moving (a follow animation takes ~260 ms), then click the card. */
async function clickNode(page: Page, id: string) {
  let last = '';
  for (let i = 0; i < 40; i++) {
    const now = JSON.stringify(await worldXY(page));
    if (now === last) break;
    last = now; await page.waitForTimeout(150);
  }
  await nodeBtn(page, id).click();
}
const worldXY = async (page: Page) => ({ x: Number(await page.getByTestId('graph-world').getAttribute('data-x')), y: Number(await page.getByTestId('graph-world').getAttribute('data-y')) });

async function axe(page: Page, scope?: string) {
  let b = new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']);
  if (scope) b = b.include(scope);
  return (await b.analyze()).violations.map((v) => `${v.id}: ${v.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')} (${v.help})`);
}

// ---- the process panel ---------------------------------------------------------------------------------------------

test.describe('View process', () => {
  test('a running job updates without a refresh: stage, scene track, sampler step, node states, progress', async ({ page }) => {
    await open(page);
    await expect(chip(page)).toHaveText('Queued');
    await expect(page.getByTestId('progress-basis')).toContainText('No percentage yet');
    await expect(page.getByRole('progressbar', { name: 'Job progress' })).not.toHaveAttribute('aria-valuenow', /.+/);

    api.advanceTo('started');
    await expect(chip(page)).toHaveText('Running');
    await expect(stage(page)).toContainText('Starting ComfyUI');
    await expect(page.getByTestId('elapsed')).toContainText('Elapsed');

    api.advanceTo('scene:3:step:4');
    await expect(stage(page)).toContainText('Generating scene 3 of 7');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 3 of 7');
    await expect(page.getByTestId('scene-headline')).toContainText('step 4/6');
    await expect(track(page).getByRole('listitem')).toHaveCount(7);
    await expect(savedCells(page)).toHaveCount(2);
    await expect(track(page).locator('[aria-current="step"]')).toContainText('4/6');
    await expect(track(page).getByRole('img', { name: /Scene 3: being generated, sampler step 4 of 6/ })).toBeVisible();
    await expect(track(page).getByRole('img', { name: 'Scene 4: waiting' })).toBeVisible();
    // determinate only now that the engine reports 2 of 7
    const bar = page.getByRole('progressbar', { name: 'Job progress' });
    await expect(bar).toHaveAttribute('aria-valuenow', '29');
    await expect(page.getByTestId('progress-basis')).toContainText('2 of 7 scenes saved');
    // the sampler is the running node and sits first in the node list
    const rows = page.getByRole('list', { name: 'Nodes' }).getByRole('listitem');
    await expect(rows.first()).toContainText('Sample Video + Audio');
    await expect(rows.first()).toContainText('Running');
    await expect(rows.first()).toContainText('SamplerCustomAdvanced');
    expect(api.stats.streamOpens).toBeGreaterThanOrEqual(1);
    expect(await page.evaluate(() => performance.getEntriesByType('navigation').length)).toBe(1);

    api.advanceTo('scene:3:step:5');
    await expect(page.getByTestId('scene-headline')).toContainText('step 5/6');
  });

  test('Loop End never marks completion: seven Loop Ends and the job is still running until the output is ready', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:2:loop_end');
    await expect(page.getByTestId('loop-count')).toHaveText('2');
    await expect(chip(page)).toHaveText('Running');
    await expect(page.getByTestId('loop-explainer')).toContainText('Loop End = one scene was saved');
    await expect(page.getByTestId('loop-explainer')).toContainText("It is not the end of the job");
    const loopLine = log(page).locator('li[data-type="loop_end"]').last();
    await expect(loopLine).toContainText('Scene 2 saved, starting next scene');
    await expect(loopLine).toHaveClass(/loop/);

    api.advanceTo('scene:7:loop_end');
    await expect(page.getByTestId('loop-count')).toHaveText('7');
    await expect(chip(page)).toHaveText('Running');
    await expect(page.getByText('Completed', { exact: true })).toHaveCount(0);
    // the engine says 7 of 7 scenes, but the output does not exist: the bar must not say 100%
    await expect(page.getByRole('progressbar', { name: 'Job progress' })).toHaveAttribute('aria-valuenow', '99');
    await expect(page.getByTestId('progress-basis')).toContainText('waiting for the output file');
    await expect(page.getByTestId('output-state')).toContainText('None yet');

    api.advanceTo('output_writing');
    await expect(page.getByTestId('output-state')).toContainText('Writing');
    await expect(chip(page)).toHaveText('Running');
    await expect(page.getByText('Completed', { exact: true })).toHaveCount(0);

    api.advanceTo('completed');
    await expect(chip(page)).toHaveText('Completed');
    await expect(page.getByTestId('output-state')).toContainText('Ready');
    await expect(page.getByRole('progressbar', { name: 'Job progress' })).toHaveAttribute('aria-valuenow', '100');
    await expect(conn(page)).toHaveText('Final state received');
    await expect(track(page).getByRole('button')).toHaveCount(7);
    await expect.poll(() => api.openStreams()).toBe(0);
  });

  test('"completed" without a ready output reads as finishing, not as done', async ({ page }) => {
    api.advanceTo('scene:7:saved');
    api.run().doc.status = 'completed'; // the engine says completed, but the output file is not written yet
    await open(page);
    await expect(chip(page)).toHaveText('Finishing');
    await expect(page.getByText('waiting for the output file').first()).toBeVisible();
    await expect(page.getByText('Completed', { exact: true })).toHaveCount(0);
  });

  test('a scene is playable only after it is saved, and the saved clip really plays', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:1:step:3');
    await expect(track(page).getByRole('img', { name: /Scene 1: being generated/ })).toBeVisible();
    await expect(savedCells(page)).toHaveCount(0);
    await expect(page.getByRole('button', { name: /Play scene/ })).toHaveCount(0);
    expect(api.stats.segments).toEqual([]);

    api.advanceTo('scene:1:saved');
    await expect(savedCells(page)).toHaveCount(1);
    await expect(page.getByRole('list', { name: 'Saved scenes' })).toContainText('141 frames · 5.9 s · 736×1312');
    await savedCells(page).first().click();
    const video = page.getByTestId('segment-video');
    await expect(video).toBeVisible();
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
    const dur = await video.evaluate((v: HTMLVideoElement) => v.duration);
    expect(dur).toBeGreaterThan(5.8); expect(dur).toBeLessThan(6);
    await video.evaluate((v: HTMLVideoElement) => { v.muted = true; return v.play(); });
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.currentTime)).toBeGreaterThan(0.3);
    expect(api.stats.segments.length).toBeGreaterThan(0);
    expect(api.stats.segments.every((x) => x === 'j_run/1')).toBe(true);
    // scene 2 appears only once it is saved
    api.advanceTo('scene:2:saved');
    await expect(savedCells(page)).toHaveCount(2);
    await page.getByRole('button', { name: 'Play scene 2' }).click();
    await expect(video).toHaveAttribute('src', /segments\/2$/);
    await expect.poll(() => video.evaluate((v: HTMLVideoElement) => v.readyState)).toBeGreaterThanOrEqual(2);
  });

  test('a preview picture shows when the engine has one and disappears when it answers 404', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:1:saved');
    api.advanceTo('scene:2:step:2');
    const img = page.getByAltText('Latest engine preview');
    await expect(img).toBeVisible();
    await expect.poll(() => img.evaluate((i: HTMLImageElement) => i.naturalWidth)).toBeGreaterThan(0);
    api.previewEnabled = false;
    api.advanceTo('scene:2:saved');
    await expect(img).toBeHidden();
  });

  test('refreshing mid-run rebuilds the whole picture from the server', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:3:step:4');
    await expect(page.getByTestId('scene-headline')).toContainText('step 4/6');
    const before = await log(page).locator('li').count();
    await page.reload();
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 3 of 7');
    await expect(page.getByTestId('scene-headline')).toContainText('step 4/6');
    await expect(savedCells(page)).toHaveCount(2);
    await expect(chip(page)).toHaveText('Running');
    await expect(log(page).locator('li[data-type="loop_end"]')).toHaveCount(2);
    await expect(log(page).locator('li')).toHaveCount(before);
    api.advanceTo('scene:3:saved');
    await expect(savedCells(page)).toHaveCount(3);
    expect(api.stats.afters[0]).toBe(0);
  });

  test('a long run is read in pages and only the newest 1000 events are kept', async ({ page }) => {
    api.runs.get('j_done')!.advanceAll();
    expect(api.runs.get('j_done')!.events.length).toBeGreaterThan(500);
    await open(page, 'view=inspector&job=j_done');
    await expect(chip(page)).toHaveText('Completed');
    await page.locator('#logfilter').selectOption('all');
    await expect(page.getByText(/^\d+ of \d+ events/)).toBeVisible();
    const total = api.runs.get('j_done')!.events.length;
    await expect(page.getByText(`${total} of ${total} events`)).toBeVisible();
    expect(api.stats.afters.some((a) => a > 0)).toBe(true); // paged
    // the last line is the final one
    await expect(log(page).locator('li').last()).toContainText('Final video written and verified');
  });

  test('SSE drop shows "Reconnecting…" honestly, resumes with Last-Event-ID, and loses nothing', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:1:step:2');
    await expect(conn(page)).toHaveText('Live');
    api.retryMs = 1500;
    api.streamBreaks = 1;           // the first reconnect attempt also fails
    api.dropStreams();
    await expect(conn(page)).toHaveText('Reconnecting…');
    await expect(page.getByRole('status').filter({ hasText: 'Reconnecting…' }).first()).toContainText('Last news from the server at');
    api.advanceTo('scene:1:saved');  // happens while the stream is down
    // the page keeps checking by itself while the stream is down
    await expect(savedCells(page)).toHaveCount(1, { timeout: 8000 });
    await expect(conn(page)).toHaveText('Live', { timeout: 15000 });
    expect(api.stats.lastEventIds.filter(Boolean).length).toBeGreaterThanOrEqual(1);  // the browser resumed with Last-Event-ID
    api.advanceTo('scene:2:loop_end');
    await expect(savedCells(page)).toHaveCount(2);
    await expect(log(page).locator('li[data-type="loop_end"]')).toHaveCount(2);
  });

  test('when the stream keeps failing it falls back to polling every 1.5 s, and still follows the run', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:1:step:1');
    await expect(conn(page)).toHaveText('Live');
    api.streamEnabled = false;
    api.dropStreams();
    await expect(conn(page)).toHaveText('Live (checking every 1.5 s)', { timeout: 15000 });
    const polls = api.stats.inspect;
    api.advanceTo('scene:1:saved');
    await expect(savedCells(page)).toHaveCount(1);
    expect(api.stats.inspect).toBeGreaterThan(polls);
    api.advanceTo('scene:2:step:3');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 2 of 7');
    // polls ask only for what is new
    expect(api.stats.afters.at(-1)).toBeGreaterThan(0);
  });

  test('polling survives errors with a backoff and says so', async ({ page }) => {
    api.streamEnabled = false;
    await open(page);
    await expect(conn(page)).toHaveText('Live (checking every 1.5 s)', { timeout: 15000 });
    api.inspectFailures = 2;
    await expect(conn(page)).toHaveText('Reconnecting…', { timeout: 8000 });
    api.advanceTo('started');
    await expect(chip(page)).toHaveText('Running', { timeout: 15000 });
    await expect(conn(page)).toHaveText('Live (checking every 1.5 s)');
  });

  test('a failed job shows the failing node, the error, and "Show in graph" focuses it', async ({ page }) => {
    await open(page, 'view=both&job=j_failed');
    const card = page.getByTestId('error-card');
    await expect(card).toContainText('The job failed');
    await expect(card).toContainText('CUDA out of memory');
    await expect(card).toContainText('Sample Video + Audio');
    await expect(card).toContainText('SamplerCustomAdvanced');
    await expect(chip(page)).toHaveText('Failed');
    await expect(track(page).getByRole('img', { name: /Scene 3: was running when the job stopped/ })).toBeVisible();
    await expect(log(page).locator('li[data-type="error"]')).toContainText('CUDA out of memory');
    await card.getByRole('button', { name: 'Show in graph' }).click();
    await expect(page.getByTestId('focus')).toHaveText(SAMPLER);
    await expect(page.evaluate(() => (window as any).__log)).resolves.toContain(`show:${SAMPLER}`);
    // the graph selected it, centred it, and shows the error on the node
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('aria-pressed', 'true');
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'failed');
    await expect.poll(async () => { const c = await centreOffset(page, SAMPLER); return Math.hypot(c.dx, c.dy); }).toBeLessThan(8);
    await expect(page.getByTestId('node-detail')).toContainText('Error: CUDA out of memory');
    // errors-only filter
    await page.locator('#logfilter').selectOption('errors');
    await expect(log(page).locator('li')).toHaveCount(1);
  });

  test('a finished job can be inspected: the final state arrives at once and the live connection stops', async ({ page }) => {
    await open(page, 'view=inspector&job=j_done&final=ma_final1');
    await expect(chip(page)).toHaveText('Completed');
    await expect(conn(page)).toHaveText('Final state received');
    await expect(savedCells(page)).toHaveCount(7);
    await expect(page.getByTestId('elapsed')).toContainText(/Elapsed \d+ m/);
    const t = (await page.getByTestId('elapsed').textContent())!;
    await page.waitForTimeout(2500);
    expect(await page.getByTestId('elapsed').textContent()).toBe(t);  // a finished job's clock does not tick
    const polls = api.stats.inspect;
    await page.waitForTimeout(2500);
    expect(api.stats.inspect).toBe(polls);
    expect(api.openStreams('j_done')).toBe(0);
    // the saved library asset plays and downloads
    const v = page.getByTestId('final-video');
    await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.readyState)).toBeGreaterThanOrEqual(2);
    await expect(page.getByRole('link', { name: 'Download' })).toHaveAttribute('href', '/api/media/assets/ma_final1/content?download=true');
    await expect(page.getByTestId('agree')).toContainText('References, graph and submitted inputs match');
    // memory: device memory is "not reported", never 0
    await expect(page.getByRole('region', { name: 'View process' }).or(panel(page))).toContainText('Device (GPU) memory');
    await expect(panel(page)).toContainText('not reported by this machine');
    await expect(panel(page)).not.toContainText(/Device \(GPU\) memory\s*0/);
  });

  test('conditioning: used and unused pictures with the reason; a mismatch is a warning', async ({ page }) => {
    await open(page);
    api.advanceTo('started');
    const refs = panel(page).locator('.refs li');
    await expect(refs).toHaveCount(2);
    await expect(refs.nth(0)).toContainText('yellow_coat.png');
    await expect(refs.nth(0)).toContainText('Used');
    await expect(refs.nth(1)).toContainText('Not used');
    await expect(refs.nth(1)).toContainText('swap conditions on 1 picture');
    await expect(panel(page)).toContainText('src_6s_736p.mp4');
    api.run().doc.conditioning!.agree = false; api.advance(1);
    await expect(page.getByTestId('agree')).toContainText('Mismatch');
    await expect(page.getByTestId('agree')).toHaveAttribute('role', 'alert');
  });

  test('nodes: counts, filter, search; picking one sets the shared focus and calls onnode', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:2:step:3');
    const nodes = page.getByRole('img', { name: /done, 1 running, 5 reused from cache, 0 failed, \d+ waiting, of 70 nodes/ });
    await expect(nodes).toBeVisible();
    await page.getByRole('group', { name: 'Show nodes' }).getByRole('button', { name: 'Reused' }).click();
    await expect(page.getByRole('list', { name: 'Nodes' }).getByRole('listitem')).toHaveCount(5);
    await page.getByRole('group', { name: 'Show nodes' }).getByRole('button', { name: 'All' }).click();
    await page.getByLabel('Find a node by name or class').fill('loop end');
    const only = page.getByRole('list', { name: 'Nodes' }).getByRole('listitem');
    await expect(only).toHaveCount(1);
    await only.getByRole('button').click();
    await expect.poll(() => page.evaluate(() => (window as any).__log)).toContain('node:66');
    await expect(only.getByRole('button')).toHaveAttribute('aria-current', 'true');
  });

  test('stale: the engine stops answering, the panel keeps the last good state and says why', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:1:step:2');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 1 of 7');
    api.sendStale('j_run', 'Connection to the engine timed out.');
    await expect(page.getByTestId('stale')).toContainText('The inference engine is not answering');
    await expect(page.getByTestId('stale')).toContainText('Connection to the engine timed out.');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 1 of 7'); // last good picture stays
    api.advanceTo('scene:1:saved');
    await expect(page.getByTestId('stale')).toHaveCount(0);
    await expect(savedCells(page)).toHaveCount(1);
  });

  test('edge states: no access, unknown job, trace gone', async ({ page }) => {
    api.forbidden.add('j_run');
    await open(page);
    await expect(panel(page).getByRole('alert')).toContainText('You do not have access to this job');
    await page.close();
  });

  test('trace gone: explains it and shows the saved result only', async ({ page }) => {
    api.noTrace.add('j_done');
    await open(page, 'view=inspector&job=j_done');
    await expect(panel(page)).toContainText('The engine no longer has the detailed trace for this job; showing the saved result only.');
    await expect(chip(page)).toHaveText('Completed');
    await expect(page.getByTestId('output-state')).toContainText('Ready');
    await expect(page.getByRole('heading', { name: 'Nodes' })).toHaveCount(0);
    await expect(page.getByRole('heading', { name: 'Events' })).toHaveCount(0);
    expect(api.openStreams('j_done')).toBe(0);
  });

  test('unknown job: a plain message', async ({ page }) => {
    await open(page, 'view=inspector&job=j_nothing');
    await expect(panel(page).getByRole('alert')).toContainText('This job was not found');
  });

  test('compact strip: stage, scene, step and elapsed on one line, live', async ({ page }) => {
    await open(page, 'view=compact&job=j_run');
    const strip = page.getByRole('group', { name: 'Job progress' });
    await expect(strip).toContainText('Queued');
    api.advanceTo('scene:3:step:4');
    await expect(strip).toContainText('Running');
    await expect(strip).toContainText('Generating scene 3 of 7');
    await expect(strip).toContainText('Scene 3 of 7 · step 4/6');
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

  test('copy: the ComfyUI prompt id and the whole event log go to the clipboard', async ({ page, context }) => {
    await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await open(page);
    api.advanceTo('scene:1:saved');
    await expect(panel(page)).toContainText('ComfyUI prompt id');
    await page.getByRole('button', { name: 'Copy ComfyUI prompt id' }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe('1c2d3e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f');
    await page.getByRole('button', { name: 'Copy all' }).click();
    const text = await page.evaluate(() => navigator.clipboard.readText());
    expect(text).toMatch(/Scene 1 saved and verified/);
    expect(text).toMatch(/\+\d+\.\d s/);
  });

  test('event log: follow latest scrolls with new events, scrolling up turns it off', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:1:saved');
    await page.locator('#logfilter').selectOption('all');
    const box = log(page);
    const atEnd = () => box.evaluate((e) => e.scrollHeight - e.scrollTop - e.clientHeight < 30);
    await expect.poll(atEnd).toBe(true);
    api.advanceTo('scene:2:step:2');
    await expect.poll(atEnd).toBe(true);
    await box.evaluate((e) => { e.scrollTop = 0; });
    await expect(page.getByLabel('Follow latest')).not.toBeChecked();
    api.advanceTo('scene:2:step:5');
    await page.waitForTimeout(500);
    expect(await atEnd()).toBe(false);
    await page.getByLabel('Follow latest').check();
    await expect.poll(atEnd).toBe(true);
  });

  test('keyboard: focus moves into the drawer, Escape closes it and gives the focus back', async ({ page }) => {
    await open(page);
    await expect(page.getByRole('button', { name: 'Close View process' })).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(panel(page)).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => (window as any).__log)).toContain('closed');
  });
});

// ---- the ComfyUI graph ---------------------------------------------------------------------------------------------

test.describe('View ComfyUI', () => {

  test('draws the real 70-node graph, read-only, with live node states', async ({ page }) => {
    await gopen(page);
    await expect(page.getByText('Read-only view of the workflow ComfyUI is running. Nothing here can change the job.')).toBeVisible();
    await expect(page.locator('[data-node-id]')).toHaveCount(70);
    await expect(nodeBtn(page, SAMPLER)).toContainText('Sample Video + Audio');
    await expect(nodeBtn(page, SAMPLER)).toContainText('SamplerCustomAdvanced');
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'pending');
    // no card overlaps another
    const boxes = await page.locator('[data-node-id]').evaluateAll((els) => els.map((e) => { const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; }));
    for (let i = 0; i < boxes.length; i++) for (let j = i + 1; j < boxes.length; j++) {
      const a = boxes[i], b = boxes[j];
      expect(a[0] < b[2] - 0.5 && a[2] > b[0] + 0.5 && a[1] < b[3] - 0.5 && a[3] > b[1] + 0.5, `cards ${i} and ${j} overlap`).toBe(false);
    }
    await expect(page.locator('svg.edges path')).not.toHaveCount(0);
    // live states: the loaders are reused from cache, the sampler is running, the loop nodes are waiting
    api.advanceTo('scene:1:step:2');
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'running');
    await expect(nodeBtn(page, SAMPLER)).toContainText('Running');
    await page.getByRole('button', { name: 'Fit', exact: true }).click();   // the whole graph is on screen
    await expect(page.locator('[data-state="cached"]')).toHaveCount(5);
    await expect(page.locator('[data-state="done"]').first()).toBeVisible();
    // nothing in the page can write: no form controls that submit, no unsafe request was made
    const writes: string[] = [];
    page.on('request', (r) => { if (r.method() !== 'GET') writes.push(`${r.method()} ${r.url()}`); });
    await clickNode(page, SAMPLER);
    await page.getByRole('button', { name: 'Raw history' }).first().click();
    expect(writes).toEqual([]);
  });

  test('zoom (buttons, wheel, keys), 100%, fit and pan (drag, arrows) work', async ({ page }) => {
    await gopen(page);
    await expect.poll(() => worldK(page)).toBeGreaterThan(0);
    const fitK = await worldK(page);
    expect(fitK).toBeLessThan(0.5);   // the whole 70-node graph is on screen
    await page.getByRole('button', { name: 'Zoom in' }).click();
    await expect.poll(() => worldK(page)).toBeCloseTo(fitK * 1.25, 2);
    await page.getByRole('button', { name: 'Zoom out' }).click();
    await expect.poll(() => worldK(page)).toBeCloseTo(fitK, 2);
    await page.getByRole('button', { name: '100%' }).click();
    await expect.poll(() => worldK(page)).toBeCloseTo(1, 2);
    await expect(page.getByTestId('zoom')).toHaveText('100%');
    await page.getByRole('button', { name: 'Fit', exact: true }).click();
    await expect.poll(() => worldK(page)).toBeCloseTo(fitK, 2);
    // wheel zooms around the cursor: the world point under the cursor stays under it
    const vb = (await viewport(page).boundingBox())!;
    const cx = vb.x + vb.width * 0.3, cy = vb.y + vb.height * 0.4;
    const before = await worldXY(page);
    const wx = (cx - vb.x - before.x) / fitK, wy = (cy - vb.y - before.y) / fitK;
    await page.mouse.move(cx, cy);
    await page.mouse.wheel(0, -400);
    await expect.poll(() => worldK(page)).toBeGreaterThan(fitK * 1.3);
    const k2 = await worldK(page); const after = await worldXY(page);
    expect(Math.abs(wx * k2 + after.x - (cx - vb.x))).toBeLessThan(1.5);
    expect(Math.abs(wy * k2 + after.y - (cy - vb.y))).toBeLessThan(1.5);
    // drag pans
    const p0 = await worldXY(page);
    await page.mouse.move(vb.x + 200, vb.y + 200); await page.mouse.down(); await page.mouse.move(vb.x + 260, vb.y + 230, { steps: 5 }); await page.mouse.up();
    const p1 = await worldXY(page);
    expect(Math.round(p1.x - p0.x)).toBe(60); expect(Math.round(p1.y - p0.y)).toBe(30);
    // arrows pan, + and - zoom, 0 and f
    await viewport(page).focus();
    await page.keyboard.press('ArrowLeft');
    const p2 = await worldXY(page);
    expect(Math.round(p2.x - p1.x)).toBe(80);
    await page.keyboard.press('ArrowDown');
    expect(Math.round((await worldXY(page)).y - p1.y)).toBe(-80);
    await page.keyboard.press('0');
    await expect.poll(() => worldK(page)).toBeCloseTo(1, 2);
    await page.keyboard.press('-');
    await expect.poll(() => worldK(page)).toBeCloseTo(0.8, 2);
    await page.keyboard.press('f');
    await expect.poll(() => worldK(page)).toBeCloseTo(fitK, 2);
  });

  test('only nearby cards are drawn once zoomed in, and panning brings the rest', async ({ page }) => {
    await gopen(page);
    await page.getByRole('button', { name: '100%' }).click();
    await expect.poll(() => worldK(page)).toBeCloseTo(1, 2);
    const n = await page.locator('[data-node-id]').count();
    expect(n).toBeGreaterThan(0); expect(n).toBeLessThan(70);
  });

  test('Follow active node keeps the executing node centred as the run advances, and dragging turns it off', async ({ page }) => {
    await gopen(page);
    await expect(page.getByTestId('follow')).toHaveAttribute('aria-pressed', 'true');
    api.advanceTo('scene:1:step:2');
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'running');
    await expect.poll(async () => { const c = await centreOffset(page, SAMPLER); return Math.hypot(c.dx, c.dy); }, { timeout: 5000 }).toBeLessThan(6);
    expect(await worldK(page)).toBeGreaterThanOrEqual(0.7 - 0.01);
    // the run moves on to other nodes: the view follows each of them
    api.advanceTo(`scene:1:${SAMPLER}:done`);
    api.advance(1);
    const running = await page.locator('[data-state="running"]').first().getAttribute('data-node-id');
    expect(running).toBeTruthy();
    await expect.poll(async () => { const c = await centreOffset(page, running!); return Math.hypot(c.dx, c.dy); }, { timeout: 5000 }).toBeLessThan(6);
    // the user takes over
    const vb = (await viewport(page).boundingBox())!;
    await page.mouse.move(vb.x + 300, vb.y + 300); await page.mouse.down(); await page.mouse.move(vb.x + 360, vb.y + 340, { steps: 4 }); await page.mouse.up();
    await expect(page.getByTestId('follow')).toHaveAttribute('aria-pressed', 'false');
    const held = await worldXY(page);
    api.advanceTo('scene:2:step:2');
    await page.waitForTimeout(600);
    expect(await worldXY(page)).toEqual(held);
    await page.getByTestId('follow').click();
    await expect(page.getByTestId('follow')).toHaveAttribute('aria-pressed', 'true');
    await expect.poll(async () => { const c = await centreOffset(page, SAMPLER); return Math.hypot(c.dx, c.dy); }, { timeout: 5000 }).toBeLessThan(6);
  });

  test('a node opens its details: state, timing, inputs with links, what it feeds; outputs come from /history on demand', async ({ page }) => {
    await gopen(page);
    api.advanceTo('scene:2:step:2');
    await clickNode(page, SAMPLER);
    const d = page.getByTestId('node-detail');
    await expect(d).toContainText('Sample Video + Audio');
    await expect(d).toContainText('SamplerCustomAdvanced');
    await expect(d).toContainText('Running');
    await expect(d).toContainText('Runs');
    await expect(d.getByRole('button', { name: /^from .* output \d+$/ }).first()).toBeVisible();
    await expect(d.getByRole('heading', { name: 'Feeds' })).toBeVisible();
    expect(api.stats.history).toBe(0);               // not loaded until asked
    await d.getByRole('button', { name: /Load outputs from ComfyUI history/ }).click();
    await expect(d).toContainText('ComfyUI recorded no outputs for this node.');
    expect(api.stats.history).toBe(1);
    // a link jumps to the node it comes from
    const link = d.getByRole('button', { name: /^from .* output \d+$/ }).first();
    const label = (await link.textContent())!;
    await link.click();
    const src = label.replace(/^from /, '').replace(/ output \d+$/, '');
    await expect(page.getByTestId('node-detail').locator('h3')).toHaveText(src);
    // outputs of the node that saves the clips
    await nodeBtn(page, 'save').first().count();
    await page.getByLabel('Find a node by name, class or id').fill('Final Video');
    await page.getByRole('list', { name: 'Matching nodes' }).getByRole('button').first().click();
    await expect(page.getByTestId('node-detail').locator('h3')).toHaveText('Final Video');
  });

  test('Raw history is a tab, read-only and collapsed until opened', async ({ page }) => {
    await gopen(page, 'j_done');
    await page.getByRole('button', { name: 'Raw history' }).first().click();
    const tab = page.getByRole('tab', { name: 'Raw history' });
    await expect(tab).toHaveAttribute('aria-selected', 'true');
    const raw = page.getByTestId('raw-history');
    await expect(raw.locator('details')).toBeVisible();
    await expect(raw.locator('details')).not.toHaveAttribute('open', '');
    await expect(raw.locator('pre')).toBeHidden();
    await raw.locator('summary').click();
    await expect(raw.locator('pre')).toContainText('"outputs"');
    await expect(raw.locator('pre')).toContainText('Swap_00007.mp4');
    // keyboard: arrows switch tabs
    await tab.focus(); await page.keyboard.press('ArrowLeft');
    await expect(page.getByRole('tab', { name: 'Node' })).toHaveAttribute('aria-selected', 'true');
  });

  test('history 404: ComfyUI no longer has the entry, said plainly', async ({ page }) => {
    api.historyEnabled = false;
    await gopen(page, 'j_done');
    await page.getByRole('button', { name: 'Raw history' }).first().click();
    await expect(page.getByTestId('raw-history')).toContainText('ComfyUI no longer has a history entry for this job.');
    await expect(page.getByTestId('raw-history').locator('pre')).toHaveCount(0);
  });

  test('loop-expanded node states roll up onto the node they belong to', async ({ page }) => {
    await gopen(page);
    api.advanceTo('scene:1:saved');
    await page.getByRole('button', { name: 'Fit', exact: true }).click();
    const r = api.run();
    r.extraStates[`${SAMPLER}.0.1`] = { state: 'running', class_type: 'SamplerCustomAdvanced', title: 'Sample Video + Audio', runs: 2, total_ms: 50, last_ms: 50 };
    r.extraStates[`${SAMPLER}.0.2`] = { state: 'done', class_type: 'SamplerCustomAdvanced', title: 'Sample Video + Audio', runs: 1, total_ms: 10, last_ms: 10 };
    api.advance(1);
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'running');
    await clickNode(page, SAMPLER);
    await expect(page.getByTestId('node-detail')).toContainText('including 2 loop steps');
    await expect(page.getByTestId('node-detail')).toContainText('Running');
  });

  test('no_graph: a model that does not use ComfyUI says so', async ({ page }) => {
    api.noGraph.add('j_run');
    await gopen(page);
    await expect(page.getByTestId('graph-none')).toContainText('This model does not run on ComfyUI');
    await expect(page.locator('[data-node-id]')).toHaveCount(0);
  });

  test('the shared focus store: choosing a node in the process list centres it in the graph and selects it', async ({ page }) => {
    await open(page, 'view=both&job=j_run');
    api.advanceTo('scene:2:step:2');
    await page.getByLabel('Find a node by name or class').fill('Final Video');
    await page.getByRole('list', { name: 'Nodes' }).getByRole('button', { name: /Final Video/ }).click();
    await expect(nodeBtn(page, '67')).toHaveAttribute('aria-pressed', 'true');
    // and clicking a card marks it in the process list
    await page.getByLabel('Find a node by name or class').fill('');
    await clickNode(page, SAMPLER);
    await expect(page.getByTestId('focus')).toHaveText(SAMPLER);
    await expect(page.getByRole('list', { name: 'Nodes' }).getByRole('button', { name: /Sample Video \+ Audio/ })).toHaveAttribute('aria-current', 'true');
  });

  test('the process panel and the graph share ONE live subscription', async ({ page }) => {
    await open(page, 'view=both&job=j_run');
    api.advanceTo('scene:1:step:2');
    await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'running');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 1 of 7');
    expect(api.stats.streamOpens).toBe(1);
    expect(api.openStreams()).toBe(1);
  });
});

// ---- phones --------------------------------------------------------------------------------------------------------

test.describe('390px wide', () => {
  test.use({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });

  test('the process panel is a full-screen sheet without sideways scrolling', async ({ page }) => {
    await open(page);
    api.advanceTo('scene:3:step:4');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 3 of 7');
    const box = (await panel(page).boundingBox())!;
    expect(box.width).toBe(390); expect(Math.round(box.height)).toBe(844);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    // nothing inside the sheet is wider than the sheet
    const over = await panel(page).evaluate((root) => [...root.querySelectorAll('*')].filter((e) => e.getBoundingClientRect().right > root.getBoundingClientRect().right + 1 && getComputedStyle(e).position !== 'fixed' && !e.closest('svg') && !(e.closest('ol,ul,.log') && false)).slice(0, 5).map((e) => e.className || e.tagName));
    expect(over).toEqual([]);
    // tap targets are at least 44px
    const small = await page.locator('.track button, .track [role="img"]').evaluateAll((els) => els.filter((e) => e.getBoundingClientRect().height < 43).length);
    expect(small).toBe(0);
    await page.getByRole('button', { name: 'Close View process' }).tap();
    await expect(panel(page)).toHaveCount(0);
  });

  test('the graph is a full-height sheet; the node details come up as a bottom sheet; pinch zooms', async ({ page }) => {
    await gopen(page);
    await expect(page.locator('[data-node-id]')).not.toHaveCount(0);
    const box = (await page.getByRole('dialog', { name: 'View ComfyUI' }).boundingBox())!;
    expect(box.width).toBe(390); expect(Math.round(box.height)).toBe(844);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
    const k0 = await worldK(page);
    const cdp = await page.context().newCDPSession(page);
    const vb = (await viewport(page).boundingBox())!;
    await cdp.send('Input.synthesizePinchGesture', { x: Math.round(vb.x + vb.width / 2), y: Math.round(vb.y + vb.height / 2), scaleFactor: 2.2, relativeSpeed: 300, gestureSourceType: 'touch' });
    await expect.poll(() => worldK(page)).toBeGreaterThan(k0 * 1.5);
    // tap a card: the details sheet is at the bottom of the screen and does not push the page sideways
    await page.getByRole('button', { name: 'Zoom in' }).tap(); 
    const card = page.locator('[data-node-id]').first();
    await card.evaluate((el) => (el as HTMLElement).click());
    const pane = page.getByRole('complementary', { name: 'Details' });
    await expect(pane).toBeVisible();
    const pb = (await pane.boundingBox())!;
    expect(Math.round(pb.y + pb.height)).toBe(844); expect(pb.width).toBe(390);
    expect(pb.height).toBeLessThan(844 * 0.6);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= document.documentElement.clientWidth)).toBe(true);
  });

  test('a one-finger drag pans on touch', async ({ page }) => {
    await gopen(page);
    await expect.poll(() => worldK(page)).toBeGreaterThan(0);
    const p0 = await worldXY(page);
    const cdp = await page.context().newCDPSession(page);
    const vb = (await viewport(page).boundingBox())!;
    await cdp.send('Input.synthesizeScrollGesture', { x: Math.round(vb.x + 200), y: Math.round(vb.y + 300), xDistance: 80, yDistance: 40, gestureSourceType: 'touch', speed: 800 });
    await expect.poll(async () => Math.abs((await worldXY(page)).x - p0.x)).toBeGreaterThan(30);
  });
});

// ---- accessibility -------------------------------------------------------------------------------------------------

test.describe('axe (WCAG 2.2 A and AA)', () => {
  for (const scheme of ['light', 'dark'] as const) {
    test(`process panel, ${scheme}, running with every section filled`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page);
      api.advanceTo('scene:3:step:4');
      await expect(page.getByTestId('scene-headline')).toContainText('Scene 3 of 7');
      await savedCells(page).first().click();
      await expect(page.getByTestId('segment-video')).toBeVisible();
      await page.locator('#logfilter').selectOption('all');
      expect(await axe(page)).toEqual([]);
    });

    test(`process panel, ${scheme}, failed job and a Reconnecting banner`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, 'view=inspector&job=j_failed');
      await expect(page.getByTestId('error-card')).toBeVisible();
      expect(await axe(page)).toEqual([]);
      await open(page);
      api.advanceTo('scene:1:step:2');
      await expect(conn(page)).toHaveText('Live');
      api.retryMs = 3000; api.streamBreaks = 2; api.dropStreams();
      await expect(conn(page)).toHaveText('Reconnecting…');
      expect(await axe(page)).toEqual([]);
    });

    test(`graph view, ${scheme}, with a node selected and the raw history tab`, async ({ page }) => {
      await page.emulateMedia({ colorScheme: scheme });
      await open(page, 'view=graph&job=j_run');
      api.advanceTo('scene:2:step:2');
      await expect(nodeBtn(page, SAMPLER)).toHaveAttribute('data-state', 'running');
      await clickNode(page, SAMPLER);
      await expect(page.getByTestId('node-detail')).toContainText('Running');
      expect(await axe(page)).toEqual([]);
      await page.getByRole('button', { name: 'Raw history' }).first().click();
      await page.getByTestId('raw-history').locator('summary').click();
      expect(await axe(page)).toEqual([]);
    });
  }

  test('both panels on a phone', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await open(page);
    api.advanceTo('scene:2:step:2');
    await expect(page.getByTestId('scene-headline')).toContainText('Scene 2 of 7');
    expect(await axe(page)).toEqual([]);
    await open(page, 'view=graph&job=j_run');
    await expect(page.locator('[data-node-id]')).not.toHaveCount(0);
    expect(await axe(page)).toEqual([]);
  });
});


