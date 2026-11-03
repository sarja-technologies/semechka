// Development-only: drives headless Chrome against the Work Mode mock and saves screenshots of every screen.
//   node dev/work-mock/shoot.mjs <outdir> [baseUrl] [--manage]
// Needs the mock running (MOCK_FREEZE=1 for stable pictures) and the app built with `pnpm build`.
import { chromium } from '@playwright/test';
import fs from 'node:fs';
const out = process.argv[2]; const base = process.argv[3] || 'http://127.0.0.1:8791';
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: '/opt/google/chrome/chrome', args: ['--no-sandbox'] });
const SIZES = { desktop: { width: 1440, height: 900 }, phone: { width: 375, height: 812 } };
const problems = [];

const FILTER = process.env.FILTER ? new RegExp(process.env.FILTER) : null;
async function run(label, size, scheme, url, steps = async () => {}) {
  if (FILTER && !FILTER.test(`${label}-${size}-${scheme}`)) return;
  const ctx = await browser.newContext({ viewport: SIZES[size], colorScheme: scheme, hasTouch: size === 'phone', reducedMotion: 'reduce' });
  const page = await ctx.newPage();
  page.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') problems.push(`[${label}] ${m.type()}: ${m.text()}`); });
  page.on('pageerror', (e) => problems.push(`[${label}] pageerror: ${e.message}`));
  await page.goto(base + url, { waitUntil: 'load' });
  await page.waitForTimeout(1500);
  await steps(page);
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${out}/${label}-${size}-${scheme}.png` });
  // overflow check: nothing may push the page sideways
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  if (over > 0) problems.push(`[${label}-${size}-${scheme}] horizontal overflow ${over}px`);
  await ctx.close();
}
const click = (sel) => async (p) => { await p.locator(sel).first().click(); await p.waitForTimeout(400); };
const seq = (...fs) => async (p) => { for (const f of fs) await f(p); };

for (const scheme of ['light', 'dark']) {
  for (const size of ['desktop', 'phone']) {
    await run('01-work-home', size, scheme, '/work');
    await run('02-running', size, scheme, '/work/ws_run1', async (p) => { await p.getByRole('button', { name: /^python/ }).first().click().catch(() => {}); await p.getByRole('button', { name: /^shell/ }).first().click(); });
    await run('03-approval', size, scheme, '/work/ws_appr');
    await run('04-failed', size, scheme, '/work/ws_fail', click('button[aria-expanded="false"].head >> nth=1'));
    await run('05-job', size, scheme, '/work/ws_job', size === 'phone' ? click('text=Workspace') : async () => {});
    await run('06-files', size, scheme, '/work/ws_run1', seq(size === 'phone' ? click('button.seg:has-text("Workspace")') : async () => {}, click('button.node:has-text("data")'), click('button.node:has-text("sales.csv")')));
    await run('07-artifacts', size, scheme, '/work/ws_done', seq(size === 'phone' ? click('button.seg:has-text("Workspace")') : async () => {}, click('role=tab[name=/Artifacts/]')));
    await run('08-jobs', size, scheme, '/work/ws_job', seq(size === 'phone' ? click('button.seg:has-text("Workspace")') : async () => {}, click('role=tab[name=/Jobs/]')));
    await run('09-skills-tab', size, scheme, '/work/ws_run1', seq(size === 'phone' ? click('button.seg:has-text("Workspace")') : async () => {}, click('role=tab[name=/Skills/]')));
    await run('10-new-session', size, scheme, '/work', click('button:has-text("New session")'));
    await run('11-skills', size, scheme, '/work/skills');
    await run('12-skill-edit', size, scheme, '/work/skills', click('button:has-text("Edit") >> nth=0'));
    await run('13-skill-readonly', size, scheme, '/work/skills', click('button:has-text("View") >> nth=0'));
    await run('14-skill-import', size, scheme, '/work/skills', click('button:has-text("Import")'));
  }
}
// states: 404, 403, offline, delete confirmation, preview tab of the editor, upload errors, rate limit
await run('20-notfound', 'desktop', 'light', '/work/ws_nope');
await run('21-denied', 'desktop', 'light', '/work/ws_403');
await run('22-offline', 'desktop', 'light', '/work/ws_run1', async (p) => { await p.context().setOffline(true); await p.waitForTimeout(2500); });
await run('23-delete-confirm', 'desktop', 'light', '/work/ws_done', click('button[aria-label^="Delete session Resize"]'));
await run('24-skill-preview', 'desktop', 'dark', '/work/skills', seq(click('button:has-text("Edit") >> nth=0'), click('button:has-text("Preview")')));
await run('25-rate-limit', 'desktop', 'light', '/work/ws_done', async (p) => { await p.locator('#wk-msg').fill('trigger-limit please'); await p.keyboard.press('Enter'); await p.waitForTimeout(600); });
await run('26-upload-too-big', 'desktop', 'light', '/work/ws_done', async (p) => {
  await p.locator('input[type=file][aria-label="Choose files to upload"]').setInputFiles({ name: 'big.bin', mimeType: 'application/octet-stream', buffer: Buffer.alloc(3 * 1024 * 1024, 1) });
  await p.waitForTimeout(1200);
});
await run('27-image-zoom', 'desktop', 'light', '/work/ws_done', seq(click('role=tab[name=/Artifacts/]'), click('button[aria-label^="Open a larger preview"] >> nth=0')));
await run('28-no-work-nav', 'desktop', 'light', '/chat');
await browser.close();
fs.writeFileSync(`${out}/console-problems.txt`, problems.join('\n') + '\n');
console.log(problems.length ? problems.join('\n') : 'no console problems');
