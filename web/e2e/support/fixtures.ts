import { test as base, expect, type Page, type BrowserContext } from '@playwright/test';
import path from 'node:path';
import { DIRS, URLS, USERS } from './env';

type Role = keyof typeof USERS;
export const VIEWPORTS = [
  { name: '375', width: 375, height: 812 },
  { name: '768', width: 768, height: 1024 },
  { name: '1280', width: 1280, height: 800 },
  { name: '1920', width: 1920, height: 1080 }
] as const;

/** `as(role)` opens a page already signed in as that role (saved session from global setup). */
export const test = base.extend<{ as: (role: Role) => Promise<Page>; ctxs: BrowserContext[] }>({
  ctxs: async ({}, use) => { const list: BrowserContext[] = []; await use(list); for (const c of list) await c.close(); },
  as: async ({ browser, ctxs }, use) => {
    await use(async (role: Role) => {
      const ctx = await browser.newContext({ storageState: path.join(DIRS.auth, `${role}.json`), baseURL: URLS.seeded, viewport: { width: 1280, height: 800 } });
      ctxs.push(ctx);
      return ctx.newPage();
    });
  }
});
export { expect };

export async function signIn(page: Page, email: string, password: string) {
  await page.goto('/login');
  await page.getByLabel('Email').fill(email);
  await page.getByLabel('Password').fill(password);
  await page.getByRole('button', { name: /sign in/i }).click();
}

export async function noHorizontalScroll(page: Page) {
  const over = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(over, 'page scrolls sideways').toBeLessThanOrEqual(1);
}

/** The composer's model chooser (a listbox in a popover). */
export const modelButton = (page: Page) => page.getByRole('button', { name: /^Model: / });
export async function modelOptions(page: Page): Promise<string[]> {
  await modelButton(page).click();
  const names = await page.getByRole('listbox', { name: 'Models' }).getByRole('option').locator('.al').allInnerTexts();
  await page.keyboard.press('Escape');
  return [...new Set(names.map((n) => n.trim()))];
}
export async function chooseModel(page: Page, name: string) {
  await modelButton(page).click();
  await page.getByRole('listbox', { name: 'Models' }).getByRole('option', { name: new RegExp(name) }).first().click();
  await expect(modelButton(page)).toHaveAccessibleName(`Model: ${name}`);
}
/** The signed-in shell is ready when the conversation sidebar and the account button are there. */
export const appReady = (page: Page) => expect(page.getByRole('button', { name: /^Account: / })).toBeVisible();
export async function signOut(page: Page) {
  await page.getByRole('button', { name: /^Account: / }).click();
  await page.getByRole('menuitem', { name: 'Sign out' }).click();
}
