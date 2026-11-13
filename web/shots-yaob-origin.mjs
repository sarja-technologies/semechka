// Browser check of the Yaob Origin web search UI against a dev instance (vite on 5173 -> semechka on 8780 -> the
// fake Yaob server backend/tests/fake_yaob.py). Writes screenshots to docs/evidence/yaob-origin/.
//   node shots-yaob-origin.mjs [base] [password]
import { chromium } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:5173';
const PW = process.argv[3] ?? 'dev passphrase 2026 yaob';
const OUT = path.resolve('../docs/evidence/yaob-origin');
mkdirSync(OUT, { recursive: true });
const problems = [];
const check = (ok, what) => { if (!ok) problems.push(what); console.log(`${ok ? 'ok  ' : 'FAIL'} ${what}`); };

const browser = await chromium.launch({ channel: 'chrome', headless: true });
for (const theme of ['light', 'dark']) {
  for (const [vw, vh, tag] of [[1440, 900, 'desktop'], [390, 844, 'phone']]) {
    const ctx = await browser.newContext({ viewport: { width: vw, height: vh }, deviceScaleFactor: 1, hasTouch: tag === 'phone', isMobile: tag === 'phone' });
    const page = await ctx.newPage();
    const errors = [];
    page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
    page.on('pageerror', (e) => errors.push(String(e)));
    const login = await ctx.request.post(`${BASE}/api/auth/login`, { data: { email: 'owner@example.test', password: PW } });
    check(login.ok(), `${tag}/${theme}: login`);
    const csrf = (await (await ctx.request.get(`${BASE}/api/me`)).json()).csrf;
    await ctx.request.patch(`${BASE}/api/me`, { data: { theme, web_search: 'auto' }, headers: { 'x-csrf-token': csrf } });
    const shot = async (name) => { await page.waitForTimeout(350); return page.screenshot({ path: path.join(OUT, `${tag}-${theme}-${name}.png`) }); };

    await page.goto(`${BASE}/chat`);
    await page.locator('#msg').waitFor();
    // the plain model offers no web search control
    const trig = page.locator('.mc .trigger').first();
    await trig.click();
    await page.getByRole('option', { name: /Small Assistant/ }).click();
    check(await page.getByTestId('web-search').count() === 0, `${tag}/${theme}: no web search control for a model without the capability`);
    await trig.click();
    await page.getByRole('option', { name: /Yaob Origin N119M/ }).click();
    check((await trig.innerText()).includes('Yaob Origin N119M'), `${tag}/${theme}: picker label is the runtime name`);
    const ws = page.getByTestId('web-search');
    check(await ws.isVisible(), `${tag}/${theme}: web search control shown for Yaob Origin`);
    check((await ws.getAttribute('aria-label')) === 'Web search: Auto', `${tag}/${theme}: control shows its value (Auto)`);
    await shot('1-composer');
    await ws.click();
    await page.getByRole('menu', { name: 'Web search' }).waitFor();
    await shot('2-menu');
    const saving = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
    await page.getByRole('menuitemradio', { name: /^On/ }).click();
    check((await saving).ok(), `${tag}/${theme}: PATCH /api/me accepted`);
    check((await ws.getAttribute('aria-label')) === 'Web search: On', `${tag}/${theme}: choosing On updates the control`);
    const saved = (await (await ctx.request.get(`${BASE}/api/me`)).json()).user.prefs.web_search;
    check(saved === 'on', `${tag}/${theme}: choice saved on the account (${saved})`);
    const back = page.waitForResponse((r) => r.url().endsWith('/api/me') && r.request().method() === 'PATCH');
    await ws.click(); await page.getByRole('menuitemradio', { name: /^Auto/ }).click(); await back;

    // ask something time-sensitive: the fake server searches in auto mode
    await page.locator('#msg').fill('What changed in the latest Semechka release?');
    await page.keyboard.press('Enter');
    const searching = page.locator('article .state', { hasText: 'Searching the web…' });
    await searching.waitFor({ timeout: 8000 });
    check(true, `${tag}/${theme}: "Searching the web…" shown`);
    await shot('3-searching');
    await page.locator('article .state', { hasText: 'Reading 3 sources…' }).waitFor({ timeout: 8000 });
    check(true, `${tag}/${theme}: "Reading 3 sources…" shown`);
    await shot('4-reading');
    await page.locator('article[data-role="assistant"]').last().waitFor({ timeout: 15000 });
    await page.waitForFunction(() => !document.querySelector('[aria-busy="true"]'), null, { timeout: 15000 });
    const msg = page.locator('article[data-role="assistant"]').last();
    const cites = msg.locator('a.cite');
    check(await cites.count() === 3, `${tag}/${theme}: three inline citation links (${await cites.count()})`);
    check((await msg.innerText()).includes('[7]'), `${tag}/${theme}: a marker without a source stays plain text`);
    const c1 = cites.first();
    check((await c1.getAttribute('href')) === 'https://example.org/semechka/0.12' && (await c1.getAttribute('target')) === '_blank'
      && (await c1.getAttribute('rel'))?.includes('noopener') && (await c1.getAttribute('rel'))?.includes('noreferrer'), `${tag}/${theme}: citation 1 links to source 1 in a new tab, noopener noreferrer`);
    const box = msg.getByTestId('web-sources');
    check(await box.isVisible() && !(await box.evaluate((d) => d.open)), `${tag}/${theme}: sources collapsed under the answer`);
    await msg.scrollIntoViewIfNeeded();
    await shot('5-answer-collapsed');
    await box.locator('summary').click();
    check(await box.evaluate((d) => d.open), `${tag}/${theme}: sources expand`);
    const links = box.locator('a');
    check(await links.count() === 3, `${tag}/${theme}: three source links`);
    const l = links.first();
    check((await l.getAttribute('target')) === '_blank' && (await l.getAttribute('rel'))?.includes('noreferrer'), `${tag}/${theme}: source link opens in a new tab safely`);
    check((await box.innerText()).includes('example.org'), `${tag}/${theme}: site shown`);
    await box.evaluate((d) => d.scrollIntoView({ block: 'end' }));
    await shot('6-answer-expanded');
    // reload: the stored message shows the same sources and links
    await page.reload();
    await page.locator('article[data-role="assistant"]').last().waitFor();
    const after = page.locator('article[data-role="assistant"]').last();
    check(await after.locator('a.cite').count() === 3 && await after.getByTestId('web-sources').count() === 1, `${tag}/${theme}: sources and citations after reload`);
    // a horizontal scrollbar would be a layout failure
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    check(!overflow, `${tag}/${theme}: no horizontal overflow`);
    check(errors.length === 0, `${tag}/${theme}: no console errors ${errors.slice(0, 2).join(' | ')}`);
    await ctx.close();
  }
}
await browser.close();
console.log(problems.length ? `\n${problems.length} problem(s)` : '\nall checks passed');
process.exit(problems.length ? 1 : 0);
