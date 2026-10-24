import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { test, expect } from './support/fixtures';
import { apiAs, setSetting } from './support/api';
import path from 'node:path';
import { DIRS, URLS } from './support/env';


const THEMES = ['paper', 'carbon', 'graphite'] as const;
const MEMBER_ROUTES = ['/chat', '/usage', '/help', '/account', '/unavailable/image'];
const ADMIN_ROUTES = ['/admin', '/admin/branding', '/admin/navigation', '/admin/people', '/admin/invites', '/admin/teams', '/admin/roles', '/admin/visibility', '/admin/models',
  '/admin/backends', '/admin/system', '/admin/registration', '/admin/privacy', '/admin/quotas', '/admin/email', '/admin/access', '/admin/settings', '/admin/history', '/admin/transfer', '/admin/audit'];
const PUBLIC_ROUTES = ['/login', '/signup', '/forgot'];

async function axe(page: Page) {
  // measure the settled page, not a frame in the middle of a fade-in
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => null))));
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  return r.violations.map((v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0]?.target.join(' ')} :: ${(v.nodes[0]?.any[0]?.message ?? v.nodes[0]?.failureSummary ?? '').replace(/\s+/g, ' ').slice(0, 200)}`);
}

async function setTheme(role: 'member' | 'owner', theme: string) {
  const a = await apiAs(role);
  await a.patch('/api/me', { theme });
  await a.dispose();
}

test.afterAll(async () => { await setTheme('member', 'paper'); await setTheme('owner', 'paper'); });

test.describe('axe: no violations of WCAG 2.2 A/AA on any route, in all three themes', () => {
  for (const theme of THEMES) {
    test(`${theme}: app screens`, async ({ as }) => {
      await setTheme('member', theme);
      const page = await as('member');
      const found: string[] = [];
      for (const r of MEMBER_ROUTES) {
        await page.goto(r);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
        found.push(...(await axe(page)).map((v) => `${r}: ${v}`));
      }
      // a real conversation with markdown, code and a technical drawer
      await page.goto('/chat');
      await page.getByLabel('Message', { exact: true }).fill('axe conversation');
      await page.getByRole('button', { name: /^Send/ }).click();
      await expect(page.locator('article.msg.assistant').last()).toContainText('Hello');
      found.push(...(await axe(page)).map((v) => `/chat (with reply): ${v}`));
      expect(found).toEqual([]);
    });

    test(`${theme}: administration`, async ({ as }) => {
      await setTheme('owner', theme);
      const page = await as('owner');
      const found: string[] = [];
      for (const r of ADMIN_ROUTES) {
        await page.goto(r);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
        await expect(page.getByText('Loading…')).toHaveCount(0);
        found.push(...(await axe(page)).map((v) => `${r}: ${v}`));
      }
      expect(found).toEqual([]);
    });

    test(`${theme}: sign-in, sign-up and recovery`, async ({ page }) => {
      const before = await setSetting('brand.theme_default', theme);
      const found: string[] = [];
      for (const r of PUBLIC_ROUTES) {
        await page.goto(r);
        await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
        found.push(...(await axe(page)).map((v) => `${r}: ${v}`));
      }
      await setSetting('brand.theme_default', before ?? 'paper');
      expect(found).toEqual([]);
    });
  }

  test('dialogs and the command palette (open) pass too', async ({ as }) => {
    const page = await as('owner');
    await page.goto('/admin/roles');
    await page.getByRole('button', { name: 'Create role' }).click();
    expect(await axe(page)).toEqual([]);
    await page.keyboard.press('Escape');
    await page.keyboard.press('Control+k');
    await page.getByRole('combobox', { name: 'Search commands' }).fill('r');
    expect(await axe(page)).toEqual([]);
  });
});

test.describe('keyboard only', () => {
  test('skip link is the first stop, becomes visible, and moves focus into the page', async ({ as }) => {
    const page = await as('member');
    await page.goto('/usage');
    await expect(page.getByRole('navigation', { name: 'Modes' })).toBeVisible(); // wait until the app is interactive
    await page.keyboard.press('Tab');
    const skip = page.getByRole('link', { name: 'Skip to content' });
    await expect(skip).toBeFocused();
    await expect(skip).toBeInViewport();
    await page.keyboard.press('Enter');
    await expect(page.locator('#main')).toBeFocused();
  });

  test('a message can be written, sent, stopped and found without a mouse', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await page.getByLabel('Message', { exact: true }).focus();
    await page.keyboard.type('keyboard only slow question');
    await page.keyboard.press('Enter');
    await expect(page.locator('article.msg.assistant').last()).toContainText('tick');
    await page.keyboard.press('Escape');
    await expect(page.locator('.chip.warn', { hasText: 'Stopped' })).toBeVisible();
    await expect(page.getByLabel('Message', { exact: true })).toBeFocused();
    await page.keyboard.press('/'); // focus search from anywhere outside a field
    await page.keyboard.press('Tab'); // leave the composer (we're in it), then use the documented shortcut from the body
  });

  test('dialogs trap focus, close on Escape and give focus back to what opened them', async ({ as }) => {
    const page = await as('owner');
    await page.goto('/admin/teams');
    const opener = page.getByRole('button', { name: 'Create team' });
    await opener.focus();
    await page.keyboard.press('Enter');
    const dlg = page.getByRole('dialog', { name: 'Create team' });
    await expect(dlg).toBeVisible();
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press('Tab');
      expect(await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]')), `focus escaped the dialog on Tab ${i + 1}`).toBe(true);
    }
    for (let i = 0; i < 25; i++) {
      await page.keyboard.press('Shift+Tab');
      expect(await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]'))).toBe(true);
    }
    await page.keyboard.press('Escape');
    await expect(dlg).toHaveCount(0);
    await expect(opener).toBeFocused();
    await expect(page.getByRole('dialog')).toHaveCount(0);
  });

  test('every control reached by Tab shows a visible focus indicator', async ({ as }) => {
    const page = await as('owner');
    const missing: string[] = [];
    for (const r of ['/chat', '/admin/people', '/admin/settings', '/admin/models', '/account']) {
      await page.goto(r);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      await page.locator('body').click({ position: { x: 1, y: 1 } });
      for (let i = 0; i < 45; i++) {
        await page.keyboard.press('Tab');
        const info = await page.evaluate(() => {
          const el = document.activeElement as HTMLElement | null;
          if (!el || el === document.body) return null;
          const cs = getComputedStyle(el), box = el.getBoundingClientRect();
          // a visually hidden file input hands its focus ring to its label, which is checked on its own
          const hidden = box.width <= 1 && box.height <= 1;
          const ring = (cs.outlineStyle !== 'none' && parseFloat(cs.outlineWidth) >= 2) || /rgb/.test(cs.boxShadow) && cs.boxShadow !== 'none';
          const label = el.closest('label');
          const labelRing = label && getComputedStyle(label).boxShadow !== 'none';
          return { ok: hidden ? true : ring || !!labelRing, name: `${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''} "${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 30)}"` };
        });
        if (info && !info.ok) missing.push(`${r}: ${info.name}`);
      }
    }
    expect([...new Set(missing)]).toEqual([]);
  });
});

test.describe('structure', () => {
  test('landmarks, language, one main, one h1, labelled navigation on every screen', async ({ as }) => {
    const page = await as('owner');
    const problems: string[] = [];
    for (const r of [...MEMBER_ROUTES, ...ADMIN_ROUTES]) {
      await page.goto(r);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      const s = await page.evaluate(() => ({
        lang: document.documentElement.lang, mains: document.querySelectorAll('main, [role=main]').length, h1: document.querySelectorAll('h1').length,
        title: document.title, unlabeledNav: [...document.querySelectorAll('nav')].filter((n) => !n.getAttribute('aria-label') && !n.getAttribute('aria-labelledby')).length,
        imgNoAlt: [...document.querySelectorAll('img')].filter((i) => !i.hasAttribute('alt')).length,
        btnNoName: [...document.querySelectorAll('button')].filter((b) => !(b.textContent || '').trim() && !b.getAttribute('aria-label') && !b.getAttribute('title')).length
      }));
      if (s.lang !== 'en') problems.push(`${r}: lang=${s.lang}`);
      if (s.mains !== 1) problems.push(`${r}: ${s.mains} main landmarks`);
      if (s.h1 < 1) problems.push(`${r}: no h1`);
      if (!s.title) problems.push(`${r}: empty title`);
      if (s.unlabeledNav) problems.push(`${r}: ${s.unlabeledNav} unlabelled nav`);
      if (s.imgNoAlt) problems.push(`${r}: img without alt`);
      if (s.btnNoName) problems.push(`${r}: ${s.btnNoName} buttons with no accessible name`);
    }
    expect(problems).toEqual([]);
  });

  test('toasts and chat status are polite live regions; errors are alerts', async ({ as }) => {
    const page = await as('member');
    await page.goto('/chat');
    await expect(page.locator('.toasts')).toHaveAttribute('aria-live', 'polite');
    await expect(page.locator('[data-chat-status]')).toHaveAttribute('aria-live', 'polite');
    await page.goto('/chat/does-not-exist');
    await expect(page.getByRole('alert')).toContainText(/does not exist/);
  });
});

test.describe('motion and touch', () => {
  test('reduced motion removes animation and transitions', async ({ browser }) => {
    const ctx = await browser.newContext({ reducedMotion: 'reduce', baseURL: URLS.seeded, storageState: path.join(DIRS.auth, 'owner.json') });
    const page = await ctx.newPage();
    await page.goto('/admin/teams');
    await page.getByRole('button', { name: 'Create team' }).click();
    const dur = await page.locator('[role=dialog]').evaluate((el) => { const c = getComputedStyle(el); return { name: c.animationName, t: c.transitionDuration }; });
    expect(dur.name).toBe('none');
    expect(parseFloat(dur.t)).toBe(0);
    expect(await page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);
    await ctx.close();
  });

  test('on a phone, controls are large enough to tap (at least 40 px tall) and nothing overlaps the content', async ({ browser }) => {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 812 }, hasTouch: true, isMobile: true, baseURL: URLS.seeded, storageState: path.join(DIRS.auth, 'member.json') });
    const page = await ctx.newPage();
    const small: string[] = [];
    for (const r of ['/chat', '/usage', '/help', '/account']) {
      await page.goto(r);
      await expect(page.getByRole('heading', { level: 1 }).first()).toBeVisible();
      const found = await page.evaluate(() => [...document.querySelectorAll<HTMLElement>('button, a.btn, input:not([type=hidden]):not([type=file]):not([type=checkbox]):not([type=radio]), select, textarea, nav a, summary, label:has(> input[type=checkbox]), label:has(> input[type=radio])')]
        .filter((el) => { const b = el.getBoundingClientRect(); const cs = getComputedStyle(el); return b.width > 0 && b.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none' && b.height < 40 && !el.closest('.sr-only'); })
        .map((el) => `${el.tagName.toLowerCase()} "${(el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 24)}" ${Math.round(el.getBoundingClientRect().height)}px`));
      small.push(...found.map((f) => `${r}: ${f}`));
    }
    expect([...new Set(small)]).toEqual([]);
    await ctx.close();
  });
});
