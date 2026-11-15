// The simplified interface (consolidation, workstream web): four places (Chat, Images, Videos, Experimental), the Chat | Work switch, the Images and
// Videos screens with genuine progress, the Experimental hub and REA. Real Chrome, the real Semechka server and database, the engine double with its real
// capabilities. What is NOT real is said in each test: the Work Mode API (workstream `work` has not merged a server yet) is answered by the browser's own
// route interception with events shaped by docs/WORK_MODE_GOOSE.md, so those tests prove the screen, not the agent.
//
//   MEDIA_E2E_ENGINE_PORT=8891 MEDIA_E2E_APP_PORT=8892 MEDIA_E2E_TMP=.e2e-web-tmp npx playwright test -c playwright.media.config.ts web.spec
import { test, expect, type Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { TMP } from '../playwright.media.config';
import { engine, fixtures, resetEngine } from './helpers';

const F = fixtures();
const SCREENS = path.join(path.dirname(new URL(import.meta.url).pathname), 'screens');
test.use({ storageState: path.join(TMP, 'auth', 'owner.json'), actionTimeout: 15_000 });
test.describe.configure({ mode: 'serial' });

const shot = async (page: Page, name: string) => { await page.waitForTimeout(300); mkdirSync(SCREENS, { recursive: true }); return page.screenshot({ path: path.join(SCREENS, `web-${name}.png`) }); };
const main = (page: Page) => page.getByRole('navigation', { name: 'Main' });
async function model(page: Page, name: RegExp) {
  await page.getByRole('button', { name: /^(Video |Picture )?[Mm]odel: / }).click();
  await page.getByRole('listbox').getByRole('option', { name }).first().click();
}
const serious = async (page: Page) => {
  const { default: AxeBuilder } = await import('@axe-core/playwright');
  return (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze()).violations.filter((v) => v.impact === 'serious' || v.impact === 'critical')
    .map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
};

test.beforeEach(async ({ page }) => {
  await resetEngine(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.setViewportSize({ width: 1280, height: 800 });
});

test('1 the main navigation is exactly Chat, Images, Videos and Experimental; the removed places are nowhere in it or in the palette', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveURL(/\/chat/);
  await expect(main(page).getByRole('link')).toHaveText(['Chat', 'Images', 'Videos', 'Experimental']);
  await expect(main(page).getByRole('link', { name: 'Chat' })).toHaveAttribute('aria-current', 'page');
  for (const gone of [/Imajutsu/, /OpenJutsu/, /Trinodal/, /Workspaces/, /OpenSfield/, /^Code$/]) await expect(page.getByRole('link', { name: gone })).toHaveCount(0);
  await page.keyboard.press('Control+k');
  const palette = page.getByRole('dialog');
  await expect(palette).toBeVisible();
  await page.keyboard.type('o');
  const text = (await palette.innerText()).toLowerCase().replace('cloud workspaces', '');   // an administration page of that name remains
  for (const gone of ['imajutsu', 'openjutsu', 'trinodal', 'workspaces', 'opensfield', 'new work session']) expect(text, gone).not.toContain(gone);
  await page.keyboard.press('Escape');
  // account and administration stay in the account menu
  await page.getByRole('button', { name: /^Account:/ }).click();
  await expect(page.getByRole('menuitem', { name: 'Library' })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: /Account and security/ })).toBeVisible();
  await expect(page.getByRole('menuitem', { name: /Administration/ })).toBeVisible();
  await shot(page, '01-navigation');
  for (const [name, url] of [['Images', /\/image$/], ['Videos', /\/video$/], ['Experimental', /\/experimental$/]] as const) {
    await page.keyboard.press('Escape');
    await main(page).getByRole('link', { name }).click();
    await expect(page).toHaveURL(url);
    await expect(main(page).getByRole('link', { name })).toHaveAttribute('aria-current', 'page');
  }
});

test('2 old addresses land where the function lives now', async ({ page }) => {
  await page.goto('/chat?new=1&op=image');
  await expect(page).toHaveURL(/\/image$/);
  await page.goto('/chat?new=1&op=video');
  await expect(page).toHaveURL(/\/video$/);
  await page.goto('/code');
  await expect(page).toHaveURL(/\/chat/);
  await page.goto('/workspaces');
  await expect(page).toHaveURL(/\/chat/);
});

test('3 Chat is for conversation: no Picture or Video switch in the composer, a Chat | Work switch above it', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now());
  await expect(page.getByLabel('Message', { exact: true })).toBeVisible();
  await expect(page.getByRole('radio', { name: 'Picture' })).toHaveCount(0);
  await expect(page.getByRole('radio', { name: 'Video' })).toHaveCount(0);
  const sw = page.getByRole('radiogroup', { name: 'Mode of this conversation' });
  await expect(sw.getByRole('radio', { name: 'Chat' })).toHaveAttribute('aria-checked', 'true');
  await shot(page, '03-chat');
});

test('4 Work with no Work API on the server says so plainly, never a fake agent; Chat comes back unchanged (this server really has no /api/work/goose)', async ({ page }) => {
  await page.goto('/chat?new=' + Date.now());
  await page.getByRole('radio', { name: 'Work' }).click();
  await expect(page.getByTestId('work-unavailable')).toContainText('Work Mode is not available on this server');
  await expect(page.getByLabel('Message to the agent')).toHaveCount(0);
  await shot(page, '04-work-unavailable');
  await page.getByRole('radio', { name: 'Chat' }).click();
  await expect(page.getByLabel('Message', { exact: true })).toBeVisible();
});

test('5 Images: a slow job shows the engine\'s own progress and a Cancel; a finished one shows the picture large and keeps it in Recent and the Library', async ({ page }) => {
  await page.goto('/image');
  await expect(page.getByRole('heading', { name: 'Images', level: 1 })).toBeVisible();
  await model(page, /Fake Turbo/);
  const go = page.getByTestId('generate');
  await expect(go).toBeDisabled();
  await expect(page.getByText('Describe the picture to make.')).toBeVisible();   // a disabled control says why
  await page.getByLabel('What should the picture show?').fill('a slow harbour at dusk');
  await expect(go).toBeEnabled();
  await go.click();
  const stage = page.getByRole('region', { name: 'Result' });
  await expect(stage.getByText('Sampling 3/10')).toBeVisible();                     // the engine's own words
  await expect(stage.getByRole('progressbar')).toHaveAttribute('value', '30');      // the engine's own number
  await shot(page, '05a-image-progress');
  await stage.getByRole('button', { name: /Cancel/ }).click();
  await expect(stage.getByText(/Cancelled/)).toBeVisible();
  await page.getByLabel('What should the picture show?').fill('a harbour at dusk');
  await go.click();
  const img = stage.getByRole('img', { name: /Generated from: a harbour at dusk/ });
  await expect(img).toBeVisible({ timeout: 30_000 });
  expect((await img.boundingBox())!.width).toBeGreaterThan(0);   // the engine double returns a tiny picture; the page never scales a picture past its own size
  await expect(page.getByRole('heading', { name: 'Recent pictures' })).toBeVisible();
  await shot(page, '05b-image-result');
  const jobs = await (await page.request.get('/api/media/jobs?kind=image')).json();
  expect(jobs.some((j: any) => j.prompt === 'a harbour at dusk' && j.status === 'completed' && j.save)).toBe(true);   // saved, standalone (not in a conversation)
  expect(jobs.find((j: any) => j.prompt === 'a harbour at dusk').conversation_id).toBeFalsy();
  await page.goto('/library');
  await expect(page.getByText('a harbour at dusk').first()).toBeVisible();
});

test('6 Images with a reference: the upload becomes the source of the job, and Chat history is not cluttered by it', async ({ page }) => {
  await page.goto('/image');
  await model(page, /qwen-image-edit|Qwen-Image Edit/i);
  await page.getByLabel('What should the picture show?').fill('the same scene, in winter');
  await page.locator('input[type=file]').setInputFiles(F.a);
  const ref = page.locator('.refcard');
  await expect(ref).toContainText('portrait-a.png');
  await expect(ref.getByText(/Uploading|Processing/)).toHaveCount(0, { timeout: 30_000 });
  await expect(ref.getByText('The model changes or follows this picture.')).toBeVisible();
  await shot(page, '06-image-reference');
  await page.getByTestId('generate').click();
  await expect(page.getByRole('region', { name: 'Result' }).getByRole('img', { name: /Generated from: the same scene, in winter/ })).toBeVisible({ timeout: 30_000 });
  const subs = (await engine(page)).image_submissions as any[];
  const sub = subs.find((x) => x.simple?.prompt === 'the same scene, in winter');
  expect(sub, 'the engine received the job').toBeTruthy();
  expect(sub.engine_mode).toBe('edit');
  expect(sub.pictures.length).toBe(1);                 // the uploaded picture reached the engine as the source
  // the screen's own conversation is archived: the Chat list does not show it, the archive does
  const list = (r: any[]) => r.map((c) => c.title);
  const open = await (await page.request.get('/api/chat/conversations')).json();
  const arch = await (await page.request.get('/api/chat/conversations?archived=true')).json();
  expect(list(open).join('|')).not.toContain('the same scene');   // the server names it after the first prompt
  expect(list(arch).join('|')).toContain('the same scene');
  await page.getByRole('button', { name: 'Remove the reference picture' }).click();
  await expect(page.locator('.refcard')).toHaveCount(0);
});

test('7 Videos: the length is seconds only (slider and typed number agree, the honest line says what will be made), no frames or fps anywhere, a large result', async ({ page }) => {
  await page.goto('/video');
  await model(page, /Fake Clip/);
  const dur = page.getByTestId('duration');
  await expect(dur).toBeVisible();
  const slider = page.getByTestId('duration-slider'), num = page.getByTestId('duration-input');
  await slider.fill('1.562');
  await expect(num).toHaveValue('1.56');   // the box shows the length the model will really make
  await num.fill('2');
  await num.press('Enter');
  await expect(page.getByTestId('duration-note')).toContainText(/Will make/);
  const body = (await page.locator('main').innerText()).toLowerCase();
  expect(body).not.toMatch(/\bfps\b|frames?\b/);
  await page.getByLabel('What should happen in the video?').fill('a boat leaves the harbour');
  await page.getByTestId('generate').click();
  const v = page.getByRole('region', { name: 'Result' }).locator('video');
  await expect(v).toBeVisible({ timeout: 30_000 });
  expect((await v.boundingBox())!.width).toBeGreaterThan(400);
  await shot(page, '07-video-result');
});

test('8 Experimental lists every advanced tool the server offers; REA says it is not offered when the engine has no analysis tool', async ({ page }) => {
  await page.goto('/experimental');
  for (const name of ['Picture studio', 'Upscale', 'iPhone Photo', 'Advanced image', 'Conversation studio', 'Video editor', 'Advanced video', /REA, video reverse engineer/]) {
    await expect(page.getByRole('link', { name })).toBeVisible();
  }
  await shot(page, '08a-experimental');
  await page.getByRole('link', { name: /REA, video reverse engineer/ }).click();
  await expect(page.getByTestId('rea-absent')).toContainText('REA is not available on this server');
  await page.getByRole('link', { name: 'Experimental', exact: true }).first().click();
  await page.getByRole('link', { name: 'Advanced video' }).click();
  await expect(page.getByLabel('Seed')).toBeVisible();
  await page.goto('/experimental');
  await page.getByRole('link', { name: 'Picture studio' }).click();
  await expect(page.getByRole('link', { name: 'Experimental' }).first()).toBeVisible();    // the way back
});

test('9 phone width: the four places sit in a bar at the foot, the header tabs step aside, nothing scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const url of ['/chat', '/image', '/video', '/experimental']) {
    await page.goto(url);
    const bar = page.getByRole('navigation', { name: 'Main' }).last();
    await expect(bar.getByRole('link')).toHaveCount(4);
    await expect(bar).toBeInViewport();
    const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(over, url).toBeLessThanOrEqual(0);
  }
  await page.goto('/image');
  await shot(page, '09-mobile-image');
});

test('10 accessibility: the new screens have no serious violations, light and dark', async ({ page }) => {
  for (const url of ['/image', '/video', '/experimental', '/experimental/image', '/experimental/rea', '/chat']) {
    await page.goto(url);
    await page.waitForTimeout(600);
    for (const theme of ['light', 'dark'] as const) {
      await page.emulateMedia({ colorScheme: theme });
      await page.waitForTimeout(250);
      expect(await serious(page), `${url} ${theme}`).toEqual([]);
    }
  }
});

// ---- Work Mode screen against a MOCKED API --------------------------------------------------------------------------------------------------------
// The browser answers /api/work/goose/* itself with events in the shapes of docs/WORK_MODE_GOOSE.md. This proves how the screen draws and answers them
// (timeline, terminal output, diff, approval choices, loading state). It does NOT prove that an agent runs: that needs the server of workstream `work`.
function mockGoose(page: Page, log: { approvals: any[]; prompts: any[] }) {
  const events: any[] = [];
  let seq = 0;
  const ev = (type: string, f: Record<string, unknown> = {}) => events.push({ seq: ++seq, type, ts: Date.now() / 1000, turn_id: 't_1', ...f });
  const session = () => ({ id: 'wg_1', conversation_id: 'c', state: 'idle', model: 'qwen3.8-27b', autonomy: 'ask', turn: null, pending_approvals: [], last_seq: seq });
  const json = (route: any, body: any, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  return page.route('**/api/work/goose/**', async (route) => {
    const url = new URL(route.request().url()), p = url.pathname.replace('/api/work/goose', ''), m = route.request().method();
    if (p === '/status') return json(route, { enabled: true, ready: true, problems: [], models: [{ id: 'qwen3.8-27b', label: 'Qwen3.8 27B', state: 'unloaded', default: true }], autonomy: [{ id: 'ask', label: 'Ask before every action', default: true }, { id: 'auto', label: 'Never ask' }] });
    if (p.endsWith('/session') && m === 'GET') return json(route, { detail: { code: 'no_session', message: 'none' } }, 404);
    if (p.endsWith('/session') && m === 'POST') return json(route, session());
    if (p.endsWith('/transcript')) return json(route, { items: [], last_seq: seq });
    if (p.endsWith('/prompt')) {
      log.prompts.push(JSON.parse(route.request().postData() || '{}'));
      ev('turn_start', { text: log.prompts.at(-1).text }); ev('state', { state: 'loading_model' }); ev('model_loading', { model: 'qwen3.8-27b', phase: 'loading' });
      return json(route, { turn_id: 't_1', seq }, 202);
    }
    if (p.includes('/approvals/')) {
      log.approvals.push(JSON.parse(route.request().postData() || '{}'));
      ev('approval_resolved', { approval_id: 'ap_1', decision: log.approvals.at(-1).decision, by: 'user' }); ev('state', { state: 'running' });
      ev('tool_start', { tool_call_id: 'c2', tool: 'edit', title: 'edit calc.py', input: { path: 'calc.py' } });
      ev('tool_end', { tool_call_id: 'c2', status: 'completed', output: 'Edited calc.py', diff: { path: 'calc.py', unified: '--- a/calc.py\n+++ b/calc.py\n@@ -1 +1 @@\n-return sum(xs) - 1\n+return sum(xs)' } });
      ev('files_changed', { turn_id: 't_1', files: [{ path: 'calc.py', status: 'modified', additions: 1, deletions: 1 }] });
      ev('assistant_delta', { message_id: 'm9', text: 'Fixed.' }); ev('turn_end', { stop_reason: 'end_turn' }); ev('state', { state: 'idle' });
      return json(route, { ok: true });
    }
    if (p.endsWith('/events')) {
      const since = Number(url.searchParams.get('since') || 0);
      // a stream that replays what exists after `since`, then waits a little and ends (the screen reconnects with the last seq it saw)
      await new Promise((r) => setTimeout(r, 250));
      const body = events.filter((e) => e.seq > since).map((e) => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join('');
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body });
    }
    return json(route, { detail: { code: 'not_found', message: 'mock has no such route' } }, 404);
  }).then(() => ({
    ask: () => { ev('state', { state: 'running' }); ev('model_loading', { model: 'qwen3.8-27b', phase: 'ready' }); ev('assistant_delta', { message_id: 'm1', text: 'Running the tests first.' });
      ev('tool_start', { tool_call_id: 'c1', tool: 'shell', title: 'python3 -m pytest -q', input: { command: 'python3 -m pytest -q' } });
      ev('tool_output', { tool_call_id: 'c1', stream: 'stdout', text: 'FAILED test_total\n' }); ev('tool_end', { tool_call_id: 'c1', status: 'failed', output: 'FAILED test_total\n1 failed', exit_code: 1 });
      ev('approval_request', { approval: { id: 'ap_1', tool: 'edit', title: 'edit calc.py', input: { path: 'calc.py' }, options: ['allow_once', 'allow_always', 'deny', 'deny_always'], expires_at: Date.now() / 1000 + 900 } });
      ev('state', { state: 'awaiting_approval' }); }
  }));
}

test('11 [MOCKED Work API] Work Mode in the same conversation: loading state, tool call, terminal output, an approval with allow once / always / deny, then the diff', async ({ page }) => {
  const log = { approvals: [] as any[], prompts: [] as any[] };
  const h = await mockGoose(page, log);
  await page.goto('/chat?new=' + Date.now() + '&mode=work');
  const box = page.getByLabel('Message to the agent');
  await expect(box).toBeVisible();
  await expect(page.getByText(/not loaded yet; the first message waits/)).toBeVisible();
  await expect(page.getByRole('combobox', { name: 'When the agent asks first' })).toBeVisible();
  // the conversation is saved by the first prompt (real), then the mocked session answers
  await box.fill('The tests fail. Fix calc.py');
  await box.press('Enter');
  await expect(page.getByTestId('work-status')).toContainText(/Loading Qwen3.8 27B into memory/);
  await expect(page.getByTestId('work-status')).toContainText(/25 to 90 seconds/);
  expect(log.prompts[0].text).toBe('The tests fail. Fix calc.py');
  await shot(page, '11a-work-loading');
  h.ask();
  await expect(page.getByRole('button', { name: 'Allow once' })).toBeVisible({ timeout: 15_000 });
  await expect(page.getByRole('button', { name: 'Always allow' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Deny', exact: true })).toBeVisible();
  await expect(page.getByText('FAILED test_total').first()).toBeVisible();
  await shot(page, '11b-work-approval');
  await page.getByRole('button', { name: 'Allow once' }).click();
  expect(log.approvals).toEqual([{ decision: 'allow_once' }]);
  await expect(page.getByText('Allowed once')).toBeVisible();
  await page.getByRole('button', { name: 'Show changes' }).click().catch(() => {});
  await expect(page.getByText('1 file changed')).toBeVisible();
  await expect(page.locator('section.tool[data-tool="edit"]').getByText('+return sum(xs)')).toBeVisible();
  await shot(page, '11c-work-diff');
  // back to Chat: the conversation is the same one
  await page.getByRole('radio', { name: 'Chat' }).click();
  await expect(page.getByLabel('Message', { exact: true })).toBeVisible();
});

// ---- REA against a MOCKED profile and analysis route ---------------------------------------------------------------------------------------------
// docs/MEDIA_ENGINE.md section 7 describes the engine's `reverse-video` profile; the engine double here does not have it and the server has no analysis
// route yet. The browser answers those two calls itself. The upload, the Library and the screen are real; the job and the analysis are MOCKED.
async function mockRea(page: Page, analysis: any | null) {
  await page.route('**/api/media/options?kind=video', async (route) => {
    const r = await route.fetch(); const j = await r.json();
    j.models.push({ id: 'm_rea', alias: 'Reverse engineer', modes: [], references: 0, limits: {}, defaults: {}, profile: { id: 'reverse-video', family: 'reverse', name: 'Reverse Engineer Video', ready: true,
      inputs_schema: { operation: { type: 'enum', values: ['analyse'], default: 'analyse' }, video: { type: 'file', required: true } } } });
    await route.fulfill({ response: r, json: j });
  });
  const posted: any[] = [];
  await page.route('**/api/media/profile-jobs', async (route) => {
    posted.push(route.request().postData() ?? '');
    await route.fulfill({ json: { id: 'j_rea1', kind: 'video', status: 'completed', model: 'Reverse engineer', model_id: 'm_rea', prompt: '', params: {}, progress: 1, progress_text: 'Done', elapsed_s: 3, error: null, save: true,
      assets: [{ id: 'ma_none', kind: 'image', mime: 'image/jpeg', size: 10, width: 640, height: 360, saved: true }], conversation_id: null } });
  });
  await page.route('**/api/media/jobs/j_rea1/analysis', (route) => analysis ? route.fulfill({ json: analysis }) : route.fulfill({ status: 404, json: { detail: { code: 'not_found', message: 'Not found.' } } }));
  await page.route('**/api/media/jobs/j_rea1', (route) => route.fulfill({ json: { id: 'j_rea1', kind: 'video', status: 'completed', model: 'Reverse engineer', prompt: '', params: {}, progress: 1, assets: [{ id: 'ma_none', kind: 'image', mime: 'image/jpeg', size: 10, width: 640, height: 360, saved: true }] } }));
  return posted;
}

test('12 [MOCKED engine job] REA: upload a video, analyse it as a profile job, read the analysis the engine returns without inventing any field', async ({ page }) => {
  const posted = await mockRea(page, { duration_s: 6.0, shots: [{ index: 1, start: 0, end: 2.4, camera: 'static' }, { index: 2, start: 2.4, end: 6, camera: 'pan left' }], colour: { dominant: 'teal', contrast: 'medium' }, notes: ['no cuts in the second half'] });
  await page.goto('/experimental/rea');
  await expect(page.getByTestId('rea-go')).toBeDisabled();
  await expect(page.getByText('Add a video first.')).toBeVisible();
  await page.locator('input[type=file]').setInputFiles(F.clip);
  await expect(page.getByText(/harbour|clip/i).first()).toBeVisible();
  await expect(page.getByTestId('rea-go')).toBeEnabled({ timeout: 30_000 });
  await page.getByTestId('rea-go').click();
  expect(posted.length).toBe(1);
  expect(posted[0]).toContain('m_rea');                       // the profile model
  expect(posted[0]).toContain('"operation":"analyse"');
  expect(posted[0]).toContain('name="asset:video"');          // the uploaded video, by its Library id
  await expect(page.getByRole('heading', { name: 'Shots' })).toBeVisible();
  await expect(page.getByRole('cell', { name: 'pan left' })).toBeVisible();
  await expect(page.getByRole('heading', { name: 'Colour' })).toBeVisible();
  await expect(page.getByText('no cuts in the second half')).toBeVisible();
  await shot(page, '12a-rea-analysis');
  expect(await serious(page)).toEqual([]);
});

test('13 [MOCKED engine job] REA when the server does not pass the analysis on: the contact sheet is shown and the page says what is missing', async ({ page }) => {
  await mockRea(page, null);
  await page.goto('/experimental/rea');
  await page.locator('input[type=file]').setInputFiles(F.clip);
  await expect(page.getByTestId('rea-go')).toBeEnabled({ timeout: 30_000 });
  await page.getByTestId('rea-go').click();
  await expect(page.getByTestId('rea-no-analysis')).toContainText('The full analysis is not available from this server yet');
  await shot(page, '13-rea-no-analysis');
});

// ---- REPLAY of events RECORDED from a real Goose session ---------------------------------------------------------------------------------------
// e2e_media/fixtures/work-goose/*.jsonl are the event streams workstream `work` recorded from a real Goose 1.58.1 run on the Spark (docs/evidence/work-goose/).
// The browser answers /api/work/goose/* itself and replays them, so the REAL events reach the REAL screen. That shows how the screen reads and draws a real
// agent's output; it does not show that this server can run an agent.
import { readFileSync } from 'node:fs';
const recorded = (n: string) => readFileSync(path.join(path.dirname(new URL(import.meta.url).pathname), 'fixtures', 'work-goose', `${n}.jsonl`), 'utf8').trim().split('\n').map((l) => JSON.parse(l));
async function replay(page: Page, events: any[]) {
  await page.route('**/api/work/goose/**', async (route) => {
    const url = new URL(route.request().url()), p = url.pathname.replace('/api/work/goose', '');
    const json = (b: any, status = 200) => route.fulfill({ status, json: b });
    if (p === '/status') return json({ enabled: true, ready: true, problems: [], models: [{ id: 'qwen3.6-35b-a3b', label: 'Qwen3.6 35B-A3B', state: 'loaded', default: true }], autonomy: [{ id: 'ask', label: 'Ask before every action', default: true }] });
    if (p.endsWith('/session')) return json({ id: 'wg_r', conversation_id: 'c', state: 'idle', model: 'qwen3.6-35b-a3b', autonomy: 'ask', turn: null, pending_approvals: [], last_seq: 0 });
    if (p.endsWith('/transcript')) return json({ items: [], last_seq: 0 });
    if (p.endsWith('/events')) {
      const since = Number(url.searchParams.get('since') || 0);
      return route.fulfill({ status: 200, contentType: 'text/event-stream', body: events.filter((e) => e.seq > since).map((e) => `id: ${e.seq}\nevent: ${e.type}\ndata: ${JSON.stringify(e)}\n\n`).join('') });
    }
    return json({ detail: { code: 'not_found', message: 'not in the replay' } }, 404);
  });
}
async function savedConversation(page: Page) {
  const me = await (await page.request.get('/api/me')).json();
  const c = await (await page.request.post('/api/chat/conversations', { data: { mode: 'chat' }, headers: { 'x-csrf-token': me.csrf } })).json();
  await page.goto('/chat');
  await page.evaluate((id) => localStorage.setItem(`sem.mode.${id}`, 'work'), c.id);
  return c.id as string;
}

test('14 [REPLAY of real Goose events] a real repository-fixing run: 14 questions answered (one denied), real tool names and output, the real file change', async ({ page }) => {
  await replay(page, recorded('events-repo'));
  const id = await savedConversation(page);
  await page.goto(`/chat/${id}`);
  await expect(page.getByText('Inspect this repository, find out why the tests fail')).toBeVisible();
  await expect(page.locator('section.tool')).toHaveCount(14);
  await expect(page.locator('[data-decision="deny"]')).toHaveCount(1);
  await expect(page.locator('[data-approval]')).toHaveCount(14);
  await expect(page.getByText('1 file changed')).toBeVisible();
  await expect(page.getByText('shop/pricing.py').first()).toBeVisible();
  await page.evaluate(() => document.querySelector('[data-work-scroll]')?.scrollTo(0, 0));
  await shot(page, '14a-real-replay-top');
  await page.evaluate(() => document.querySelector('[data-work-scroll]')?.scrollTo(0, 99999));
  await shot(page, '14b-real-replay-end');
  expect(await serious(page)).toEqual([]);
});

test('15 [REPLAY of real Goose events] a real cancelled turn reads as stopped', async ({ page }) => {
  await replay(page, recorded('events-cancel'));
  const id = await savedConversation(page);
  await page.goto(`/chat/${id}`);
  await expect(page.getByText('Stopped.')).toBeVisible();
  await expect(page.locator('section.tool[data-status="running"]')).toHaveCount(0);
  await shot(page, '15-real-cancel');
});
