import { test, expect, signIn, appReady, signOut } from './support/fixtures';
import { USERS } from './support/env';

test('sign in, see the organisation brand, land in chat, sign out', async ({ page }) => {
  await signIn(page, USERS.member.email, USERS.member.password);
  await expect(page).toHaveURL(/\/chat/);
  await expect(page).toHaveTitle('Banana AI');
  await appReady(page);
  await expect(page.getByRole('button', { name: /^Account: Max Member/ })).toBeVisible();
  await expect(page.getByRole('banner')).toContainText('Banana Corporation Private AI');
  await signOut(page);
  await expect(page).toHaveURL(/\/login/);
});
