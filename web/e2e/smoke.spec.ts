import { test, expect, signIn } from './support/fixtures';
import { USERS } from './support/env';

test('sign in, see the organisation brand, land in chat, sign out', async ({ page }) => {
  await signIn(page, USERS.member.email, USERS.member.password);
  await expect(page).toHaveURL(/\/chat/);
  await expect(page).toHaveTitle('Banana AI');
  await expect(page.getByRole('navigation', { name: 'Modes' })).toBeVisible();
  await page.getByRole('button', { name: /Max Member/ }).click();
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page).toHaveURL(/\/login/);
});
