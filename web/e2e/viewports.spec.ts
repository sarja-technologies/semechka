import type { Page } from '@playwright/test';
import { test, expect, VIEWPORTS, noHorizontalScroll, modelButton } from './support/fixtures';
import { shot } from './support/shots';

const APP = ['/usage', '/help', '/account', '/unavailable/image'];
const ADMIN = ['/admin', '/admin/branding', '/admin/navigation', '/admin/people', '/admin/invites', '/admin/teams', '/admin/roles', '/admin/visibility', '/admin/models',
  '/admin/backends', '/admin/system', '/admin/registration', '/admin/privacy', '/admin/quotas', '/admin/email', '/admin/access', '/admin/settings', '/admin/history', '/admin/transfer', '/admin/audit'];

/** Elements that poke out of the viewport horizontally without sitting inside something that scrolls or clips. */
async function stickingOut(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const W = document.documentElement.clientWidth, out: string[] = [];
    for (const el of document.querySelectorAll<HTMLElement>('body *')) {
      const b = el.getBoundingClientRect(), cs = getComputedStyle(el);
      if (b.width === 0 || b.height === 0 || cs.visibility === 'hidden' || cs.position === 'fixed' && b.right <= W + 1) continue;
      if (b.right <= W + 1 && b.left >= -1) continue;
      let p: HTMLElement | null = el.parentElement, clipped = false;
      while (p && p !== document.body) { const o = getComputedStyle(p); if (/(auto|scroll|hidden|clip)/.test(o.overflowX)) { clipped = true; break; } p = p.parentElement; }
      if (!clipped && !el.closest('.sr-only, .skip, [aria-hidden=true]')) out.push(`${el.tagName.toLowerCase()}.${String(el.className).split(' ')[0]} "${(el.textContent || '').trim().slice(0, 25)}" right=${Math.round(b.right)} of ${W}`);
    }
    return out.slice(0, 5);
  });
}

for (const v of VIEWPORTS) {
  test.describe(`${v.name} px`, () => {
    test.use({ viewport: { width: v.width, height: v.height } });

    test('app screens fit and are readable', async ({ as }) => {
      const page = await as('member');
      await page.setViewportSize({ width: v.width, height: v.height });
      await page.goto('/chat');
      await expect(page.getByRole('heading', { name: 'How can I help?' })).toBeVisible();
      await noHorizontalScroll(page);
      expect(await stickingOut(page)).toEqual([]);
      await shot(page, `app-chat-empty-${v.name}`);
      await page.getByLabel('Message', { exact: true }).fill('Show me a table and some code');
      await page.getByRole('button', { name: /^Send/ }).click();
      await expect(page.locator('article.msg.assistant').last()).toContainText('Hello');
      await expect(page.getByRole('button', { name: /^Send/ })).toBeVisible();
      await noHorizontalScroll(page);
      expect(await stickingOut(page)).toEqual([]);
      await shot(page, `app-chat-reply-${v.name}`);
      // the model picker shows its whole label: nothing is cut off
      const fits = await modelButton(page).locator('.name').evaluate((el) => ({ need: el.scrollWidth, have: el.clientWidth, label: el.textContent!.trim() }));
      expect(fits.need, `model picker truncates "${fits.label}"`).toBeLessThanOrEqual(fits.have + 1);
      for (const r of APP) {
        await page.goto(r);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
        await noHorizontalScroll(page);
        expect(await stickingOut(page), r).toEqual([]);
        await shot(page, `app${r.replace(/\//g, '-')}-${v.name}`);
      }
    });

    test('dialogs fit inside the viewport and scroll internally', async ({ as }) => {
      const page = await as('owner');
      await page.setViewportSize({ width: v.width, height: v.height });
      await page.goto('/admin/roles');
      await page.getByRole('button', { name: 'Create role' }).click();
      const dlg = page.getByRole('dialog');
      await expect(dlg).toBeVisible();
      const box = (await dlg.boundingBox())!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(v.width + 1);
      expect(box.y + box.height).toBeLessThanOrEqual(v.height + 1);
      // the footer actions stay reachable
      await expect(dlg.getByRole('button', { name: 'Save role' })).toBeInViewport();
      await shot(page, `dialog-role-${v.name}`);
      await page.keyboard.press('Escape');
      await page.keyboard.press('Control+k');
      await expect(page.getByRole('dialog', { name: 'Command palette' })).toBeVisible();
      await shot(page, `palette-${v.name}`);
    });

    test('administration pages fit', async ({ as }) => {
      const page = await as('owner');
      await page.setViewportSize({ width: v.width, height: v.height });
      const bad: string[] = [];
      for (const r of ADMIN) {
        await page.goto(r);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
        await expect(page.getByText('Loading…')).toHaveCount(0);
        const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
        if (over > 1) bad.push(`${r}: page scrolls sideways by ${over}px`);
        const out = await stickingOut(page);
        if (out.length) bad.push(`${r}: ${out.join('; ')}`);
        if (['/admin', '/admin/people', '/admin/models', '/admin/settings', '/admin/roles', '/admin/system', '/admin/branding'].includes(r)) await shot(page, `admin${r.replace(/\//g, '-')}-${v.name}`);
      }
      expect(bad).toEqual([]);
    });
  });
}

test('the administration menu opens on a phone and every item is reachable', async ({ as }) => {
  const page = await as('owner');
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto('/admin');
  await page.getByRole('button', { name: 'Admin sections' }).click();
  const nav = page.locator('nav#anav');
  await expect(nav.getByRole('link', { name: 'Audit', exact: false })).toBeVisible();
  await shot(page, 'admin-menu-375');
  await nav.getByRole('link', { name: 'Teams' }).click();
  await expect(page).toHaveURL(/\/admin\/teams/);
  await expect(nav).not.toBeVisible(); // the menu closes after choosing
});
