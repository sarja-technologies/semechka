import { chromium } from '@playwright/test';
const [, , email, pw, tag, ...paths] = process.argv;
const base = 'http://192.168.20.193:8790';
const b = await chromium.launch({ channel: 'chrome' }); const ctx = await b.newContext({ viewport: { width: 1280, height: 860 } }); const p = await ctx.newPage(); p.setDefaultTimeout(15000);
await p.goto(base + '/login'); await p.locator('input[type=email], input[name=email]').first().fill(email); await p.locator('input[type=password]').first().fill(pw);
await p.keyboard.press('Enter'); await p.waitForTimeout(2500);
for (const path of paths) {
  const [route, name] = path.split('|');
  await p.goto(base + route); await p.waitForTimeout(2500);
  if (name.startsWith('chat-iphone')) { await p.getByRole('radio', { name: /picture/i }).or(p.getByText('Picture', { exact: true })).first().click(); await p.waitForTimeout(1500); if (process.env.CLICK_CHIP) { await p.getByRole('button', { name: /iPhone Photo/i }).first().click(); await p.waitForTimeout(1500); } }
  await p.screenshot({ path: `/home/dembele/oj-staging-out/imajutsu/${tag}-${name}.png` });
}
await b.close();
