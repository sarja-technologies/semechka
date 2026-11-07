import { test, expect, request, type APIRequestContext, type Page } from '@playwright/test';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { MP, TMP } from '../playwright.media.config';
import { fixtures } from './helpers';

// The administrator explorer end to end: real server, real database and disk, an engine double. People make conversations and pictures and videos
// (also a failed and a cancelled one), the owner is allowed to read content, and we look at what an administrator sees.
const BASE = `http://127.0.0.1:${MP.app}`;
const PW = 'correct horse battery staple';
const REASON = 'Legal hold request 2026-114 from counsel';
const SCREENS = path.join(import.meta.dirname, 'screens');
const F = fixtures();
test.describe.configure({ mode: 'serial' });
test.use({ storageState: path.join(TMP, 'auth', 'owner.json'), actionTimeout: 15_000 });

type Who = { ctx: APIRequestContext; csrf: string; id: string };
const shot = (page: Page, name: string) => page.screenshot({ path: path.join(SCREENS, `admin-${name}.png`) });
const searchBtn = (page: Page) => page.getByRole('search').getByRole('button', { name: 'Search', exact: true });
const h = (w: Who) => ({ 'x-csrf-token': w.csrf });
const ok = async (r: any) => { if (!r.ok()) throw new Error(`${r.url()} -> ${r.status()} ${await r.text()}`); return r; };
const J = async (r: any) => (await ok(r)).json();

let owner: Who, m1: Who, m2: Who;
let convId = '';
const jobs: Record<string, string> = {};

async function login(email: string): Promise<Who> {
  const ctx = await request.newContext({ baseURL: BASE });
  await ok(await ctx.post('/api/auth/login', { data: { email, password: PW } }));
  const me = await J(await ctx.get('/api/me'));
  return { ctx, csrf: me.csrf, id: me.user.id };
}
async function finish(w: Who, id: string) {
  for (let i = 0; i < 12; i++) { const j = await J(await w.ctx.get(`/api/media/jobs/${id}`)); if (['completed', 'failed', 'cancelled'].includes(j.status)) return j; }
  throw new Error('job did not finish');
}

test.beforeAll(async () => {
  const oc = await request.newContext({ baseURL: BASE, storageState: path.join(TMP, 'auth', 'owner.json') });
  const me = await J(await oc.get('/api/me'));
  owner = { ctx: oc, csrf: me.csrf, id: me.user.id };
  await ok(await oc.put(`/api/admin/users/${owner.id}/override`, { headers: h(owner), data: { grants: ['admin.content.read', 'admin.content.instance'], denies: [] } }));
  await ok(await oc.put('/api/admin/settings/privacy.admin_content_access', { headers: h(owner), data: { value: 'audited', confirm_weaker: true } }));
  for (const [email, name] of [['mia@admin.test', 'Mia Oduya'], ['max@admin.test', 'Max Lindqvist']])
    await ok(await oc.post('/api/admin/users', { headers: h(owner), data: { email, display_name: name, password: PW, role_id: 'member', team_ids: [] } }));
  m1 = await login('mia@admin.test'); m2 = await login('max@admin.test');
  const chat = (await J(await m1.ctx.get('/api/models/catalog?operation=text'))).default;
  const imageModel = (await J(await m1.ctx.get('/api/media/options?kind=image'))).models[0].id;
  const videoModel = (await J(await m1.ctx.get('/api/media/options?kind=video'))).models[0].id;
  convId = (await J(await m1.ctx.post('/api/chat/conversations', { headers: h(m1), data: { model_id: chat } }))).id;
  await ok(await m1.ctx.post(`/api/chat/conversations/${convId}/generate`, { headers: h(m1), data: { content: 'Plan a poster for the harbour festival', model_id: chat } }));
  await ok(await m1.ctx.patch(`/api/chat/conversations/${convId}`, { headers: h(m1), data: { title: 'Harbour festival poster' } }));
  const att = await J(await m1.ctx.post('/api/chat/attachments', { headers: h(m1), multipart: { file: { name: 'sketch.png', mimeType: 'image/png', buffer: readFileSync(F.a) } } }));
  const g = await J(await m1.ctx.post(`/api/chat/conversations/${convId}/generate`, { headers: h(m1), data: { operation: 'image', content: 'a festival poster with boats at dusk', model_id: imageModel, attachments: [att.id], media: { width: 512, height: 512, steps: 4 } } }));
  jobs.chat = g.message.media.id; await finish(m1, jobs.chat);
  const v = await J(await m1.ctx.post('/api/media/jobs', { headers: h(m1), data: { kind: 'video', model_id: videoModel, prompt: 'waves rolling over the pier', width: 512, height: 320, frames: 17, fps: 16, steps: 20 } }));
  jobs.video = v.id; await finish(m1, jobs.video);
  const img = (p: string, extra = {}) => m2.ctx.post('/api/media/jobs', { headers: h(m2), data: { kind: 'image', model_id: imageModel, prompt: p, width: 512, height: 512, steps: 4, seed: 11, ...extra } });
  jobs.direct = (await J(await img('a red bicycle leaning on a brick wall'))).id; await finish(m2, jobs.direct);
  jobs.failed = (await J(await img('boom this one fails'))).id; await finish(m2, jobs.failed);
  jobs.slow = (await J(await img('slow render that never ends'))).id;
  await ok(await m2.ctx.post(`/api/media/jobs/${jobs.slow}/cancel`, { headers: h(m2) }));
  // an OpenJutsu run with a source video and a reference picture
  const oj = (await J(await m2.ctx.get('/api/media/options?kind=video'))).models.find((m: any) => m.profile);
  if (oj) {
    const up = async (file: string, name: string) => (await J(await m2.ctx.post(`/api/media/uploads?name=${name}`, { headers: h(m2), data: readFileSync(file) }))).id as string;
    const [clip, ref] = [await up(F.clip, 'harbour.mp4'), await up(F.a, 'fox.png')];
    const r = await m2.ctx.post('/api/media/profile-jobs', { headers: h(m2), multipart: { model_id: oj.id, save: 'true', inputs: JSON.stringify({ prompt: 'replace the person with a fox' }), 'asset:video': clip, 'asset:refs': ref } });
    if (!r.ok()) throw new Error(`profile job: ${r.status()} ${await r.text()}`);
    { jobs.openjutsu = (await r.json()).id; await finish(m2, jobs.openjutsu); }
  }
});

test('1 an ordinary person gets no explorer: the page redirects and the API answers 403', async ({ browser }) => {
  const ctx = await browser.newContext({ baseURL: BASE, storageState: await m1.ctx.storageState() });
  const page = await ctx.newPage();
  await page.goto('/admin/content');
  await expect(page.getByRole('heading', { name: 'Conversations & media' })).toHaveCount(0);
  for (const r of [await m1.ctx.get('/api/admin/content/explorer/filters'), await m1.ctx.post('/api/admin/content/explorer/media', { headers: h(m1), data: { reason: REASON } })]) expect(r.status()).toBe(403);
  await ctx.close();
});

test('2 nothing is listed until a reason is given; the reason prompt is calm and cancellable', async ({ page }) => {
  await page.goto('/admin/content');
  await expect(page.getByRole('heading', { name: 'Conversations & media' })).toBeVisible();
  await expect(page.getByText('Every view is recorded in the audit log and shown to the owner.')).toBeVisible();
  await expect(page.getByText('Nothing has been listed yet.').first()).toBeVisible();
  const before = (await J(await owner.ctx.get('/api/admin/audit?action=content.&per_page=5'))).total;
  await searchBtn(page).click();
  const dlg = page.getByRole('dialog', { name: 'Search conversations' });
  await expect(dlg).toBeVisible();
  await expect(dlg.getByRole('button', { name: 'Record and continue' })).toBeDisabled();
  await dlg.getByLabel('Reason for access').fill('too short');
  await expect(dlg.getByRole('button', { name: 'Record and continue' })).toBeDisabled();
  await shot(page, '01-reason');
  await dlg.getByRole('button', { name: 'Cancel' }).click();
  await expect(dlg).toHaveCount(0);
  expect((await J(await owner.ctx.get('/api/admin/audit?action=content.&per_page=5'))).total).toBe(before); // cancelling recorded nothing
});

test('3 conversations of every person: search, open one, see its media in context', async ({ page }) => {
  await page.goto('/admin/content');
  await searchBtn(page).click();
  await page.getByLabel('Reason for access').fill(REASON);
  await page.getByRole('button', { name: 'Record and continue' }).click();
  const list = page.getByRole('region', { name: 'Conversations' });
  await expect(list.getByText('Harbour festival poster')).toBeVisible();
  await shot(page, '02-conversations');
  // a title search
  await page.getByLabel('Search titles').fill('FESTIVAL');
  await searchBtn(page).click();
  await expect(list.getByRole('listitem')).toHaveCount(1);
  await list.getByRole('button', { name: /Harbour festival poster/ }).click();
  await page.getByRole('dialog', { name: 'Open this conversation' }).getByRole('button', { name: 'Record and continue' }).click();
  const view = page.getByRole('region', { name: 'Opened conversation' });
  await expect(view.getByText('Plan a poster for the harbour festival')).toBeVisible();
  const thumb = view.getByRole('list', { name: 'Results' }).locator('img').first();
  await expect.poll(() => thumb.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await expect(view.getByRole('list', { name: /Attachments/ }).getByText('sketch.png').first()).toBeVisible();
  await shot(page, '03-conversation');
  // the result opens full size, from the existing store
  await view.getByRole('button', { name: /Preview image result/ }).click();
  const pv = page.getByRole('dialog', { name: /Picture/ });
  const big = pv.locator('img');
  await expect.poll(() => big.evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await shot(page, '04-preview');
  const dl = page.waitForEvent('download');
  await pv.getByRole('button', { name: 'Download' }).click();
  const file = await dl;
  expect(file.suggestedFilename()).toMatch(/^semechka-image-.*\.png$/);
  await pv.getByLabel('Close').click();
  // the owner sees every one of these accesses
  const seen = await J(await m1.ctx.get('/api/me/content-access'));
  expect(seen.events.map((e: any) => e.kind)).toEqual(expect.arrayContaining(['content.list', 'content.read', 'media.admin.view', 'media.admin.download']));
  expect(seen.events.every((e: any) => e.reason === REASON)).toBe(true);
});

test('4 the media library: every workflow and status, filters, and a detail drawer with parameters, inputs and results', async ({ page }) => {
  await page.goto('/admin/content?tab=media');
  await searchBtn(page).click();
  await page.getByLabel('Reason for access').fill(REASON);
  await page.getByRole('button', { name: 'Record and continue' }).click();
  const grid = page.getByRole('list', { name: 'Media library' });
  await expect(grid.getByRole('listitem').first()).toBeVisible();
  const total = await grid.getByRole('listitem').count();
  expect(total).toBeGreaterThanOrEqual(5);
  await expect.poll(() => grid.locator('img').first().evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await shot(page, '05-media');
  // filters: failed
  await page.locator('#pn-media').getByLabel('Status').selectOption('failed');
  await searchBtn(page).click();
  await expect(grid.getByRole('listitem')).toHaveCount(1);
  await expect(grid.getByText('Failed').first()).toBeVisible();
  await shot(page, '06-media-failed');
  await page.locator('#pn-media').getByLabel('Status').selectOption('cancelled');
  await searchBtn(page).click();
  await expect(grid.getByRole('listitem')).toHaveCount(1);
  await page.getByRole('button', { name: 'Reset filters' }).click();
  await page.locator('#pn-media').getByLabel('Media type').selectOption('video');
  await searchBtn(page).click();
  await expect(grid.getByRole('listitem')).toHaveCount(jobs.openjutsu ? 3 : 1);   // the video, the OpenJutsu run and the uploaded source clip
  await page.locator('#pn-media').getByLabel('Workflow').selectOption('video_generation');
  await searchBtn(page).click();
  await expect(grid.getByRole('listitem')).toHaveCount(1);
  // a person filter
  await page.getByRole('button', { name: 'Reset filters' }).click();
  await page.locator('#pn-media').getByLabel('Person').selectOption({ label: 'Max Lindqvist (max@admin.test)' });
  await page.locator('#pn-media').getByLabel('Created from').fill(new Date().toISOString().slice(0, 10));
  await searchBtn(page).click();
  await expect(grid.getByRole('listitem').first()).toBeVisible();
  // open a generation
  await grid.getByRole('button', { name: /Image generation image by Max Lindqvist, Completed/ }).first().click();
  await page.getByRole('dialog', { name: 'Open this generation' }).getByRole('button', { name: 'Record and continue' }).click();
  const dr = page.getByRole('dialog', { name: 'Generation record' });
  await expect(dr.getByText('a red bicycle leaning on a brick wall')).toBeVisible();
  await expect(dr.getByText('image/png · 512 × 512')).toBeVisible();
  await expect(dr.locator('dt', { hasText: 'steps' })).toBeVisible();
  await expect.poll(() => dr.locator('img').first().evaluate((i: HTMLImageElement) => i.complete && i.naturalWidth > 0)).toBe(true);
  await shot(page, '07-drawer');
  await page.keyboard.press('Escape');
  await expect(dr).toHaveCount(0);
});

test('5 a failed generation shows its error; an OpenJutsu run shows its inputs; a video plays from the store', async ({ page }) => {
  await page.goto('/admin/content?tab=media');
  await searchBtn(page).click();
  await page.getByLabel('Reason for access').fill(REASON);
  await page.getByRole('button', { name: 'Record and continue' }).click();
  const grid = page.getByRole('list', { name: 'Media library' });
  await grid.getByRole('button', { name: /, Failed/ }).first().click();
  await page.getByRole('dialog', { name: 'Open this generation' }).getByRole('button', { name: 'Record and continue' }).click();
  const dr = page.getByRole('dialog', { name: 'Generation record' });
  await expect(dr.getByText('out of memory')).toBeVisible();
  await expect(dr.getByText('The generation failed and produced no file.')).toBeVisible();
  await shot(page, '08-failed');
  await page.keyboard.press('Escape');
  if (jobs.openjutsu) {
    await grid.getByRole('button', { name: /OpenJutsu video by Max Lindqvist/ }).first().click();
    await page.getByRole('dialog', { name: 'Open this generation' }).getByRole('button', { name: 'Record and continue' }).click();
    await expect(dr.getByText('Inputs sent to the model')).toBeVisible();
    await expect(dr.locator('strong', { hasText: 'Source' })).toBeVisible();
    await expect(dr.getByText('Reference').first()).toBeVisible();
    await shot(page, '09-openjutsu');
    await page.keyboard.press('Escape');
  }
  await grid.getByRole('button', { name: /Video generation video by Mia Oduya/ }).first().click();
  await page.getByRole('dialog', { name: 'Open this generation' }).getByRole('button', { name: 'Record and continue' }).click();
  await dr.getByRole('button', { name: /Preview or download/ }).first().click();
  const v = page.getByRole('dialog', { name: /Video/ }).locator('video');
  await expect(v).toBeVisible();
  await expect.poll(() => v.evaluate((x: HTMLVideoElement) => x.readyState || (x.error ? -1 : 0)), { timeout: 15_000 }).not.toBe(0);
  await shot(page, '10-video');
});

test('6 phone width and dark mode stay usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/admin/content?tab=media');
  await searchBtn(page).click();
  await page.getByLabel('Reason for access').fill(REASON);
  await page.getByRole('button', { name: 'Record and continue' }).click();
  const grid = page.getByRole('list', { name: 'Media library' });
  await expect(grid.getByRole('listitem').first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await shot(page, '11-mobile-dark');
  await grid.getByRole('button').first().click();
  await page.getByRole('dialog', { name: 'Open this generation' }).getByRole('button', { name: 'Record and continue' }).click();
  await expect(page.getByRole('dialog', { name: 'Generation record' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await shot(page, '12-mobile-drawer');
});

test('7 switched off by policy, the explorer says so and the API refuses', async ({ page }) => {
  await ok(await owner.ctx.put('/api/admin/settings/privacy.admin_content_access', { headers: h(owner), data: { value: 'off' } }));
  try {
    await page.goto('/admin/content');
    await expect(page.getByText(/Turned off by the privacy policy/)).toBeVisible();
    const r = await owner.ctx.post('/api/admin/content/explorer/media', { headers: h(owner), data: { reason: REASON } });
    expect(r.status()).toBe(403);
    expect((await r.json()).detail.code).toBe('disabled');
    await shot(page, '13-policy-off');
  } finally {
    await ok(await owner.ctx.put('/api/admin/settings/privacy.admin_content_access', { headers: h(owner), data: { value: 'audited', confirm_weaker: true } }));
  }
});
