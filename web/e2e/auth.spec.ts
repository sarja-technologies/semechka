import { test, expect, signIn } from './support/fixtures';
import { apiAs, setSetting } from './support/api';
import { PW, URLS, USERS } from './support/env';
import { totp } from './support/totp';

test.describe.configure({ mode: 'serial' });

test.describe('sign-in', () => {
  test('wrong credentials give one generic message; protected pages send you to sign in and back', async ({ page }) => {
    await page.goto('/chat/does-not-matter');
    await expect(page).toHaveURL(/\/login\?next=%2Fchat%2Fdoes-not-matter/);
    await page.getByLabel('Email').fill('nobody@e2e.test');
    await page.getByLabel('Password').fill('wrong password here');
    await page.getByRole('button', { name: /sign in/i }).click();
    const unknown = await page.getByRole('alert').innerText();
    await page.getByLabel('Email').fill(USERS.member.email);
    await page.getByLabel('Password').fill('also wrong password');
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('alert')).toHaveText(unknown); // an attacker cannot tell which addresses exist
    await page.getByLabel('Password').fill(USERS.member.password);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page).toHaveURL(/\/chat\/does-not-matter/);
  });

  test('session survives reload, ends on sign-out, and the old cookie stops working', async ({ page, context }) => {
    await signIn(page, USERS.member.email, USERS.member.password);
    await expect(page).toHaveURL(/\/chat/);
    await page.reload();
    await expect(page.getByRole('navigation', { name: 'Modes' })).toBeVisible();
    const before = await context.cookies();
    await page.getByRole('button', { name: new RegExp(USERS.member.name) }).click();
    await page.getByRole('button', { name: 'Sign out' }).click();
    await expect(page).toHaveURL(/\/login/);
    await context.addCookies(before); // replay the stolen cookie
    expect((await page.request.get('/api/me')).status()).toBe(401);
  });

  test('cookie names are private to this instance', async ({ page, context }) => {
    await signIn(page, USERS.member.email, USERS.member.password);
    await expect(page).toHaveURL(/\/chat/);
    const names = (await context.cookies()).map((c) => c.name);
    expect(names.some((n) => n.startsWith('smk_'))).toBe(true);
    expect((await context.cookies()).every((c) => c.httpOnly && c.sameSite === 'Lax')).toBe(true);
  });

  test('an account that is suspended loses access immediately', async ({ page }) => {
    const owner = await apiAs('owner');
    const made = await owner.post('/api/admin/users', { email: 'temp@e2e.test', display_name: 'Temp User', password: PW, role_id: 'member', team_ids: [] });
    const id = (await made.json()).id;
    await signIn(page, 'temp@e2e.test', PW);
    await expect(page).toHaveURL(/\/chat/);
    expect((await owner.patch(`/api/admin/users/${id}`, { status: 'suspended' })).ok()).toBe(true);
    expect((await page.request.get('/api/me')).status()).toBe(401);
    await page.reload();
    await expect(page).toHaveURL(/\/login/);
    await owner.dispose();
  });

  test('repeated failures lock the account and a correct password does not bypass it', async ({ page }) => {
    const owner = await apiAs('owner');
    await owner.post('/api/admin/users', { email: 'locked@e2e.test', display_name: 'Lock Me', password: PW, role_id: 'member', team_ids: [] });
    await page.goto('/login');
    for (let i = 0; i < 9; i++) {
      await page.getByLabel('Email').fill('locked@e2e.test');
      await page.getByLabel('Password').fill(`wrong password ${i}`);
      await page.getByRole('button', { name: /sign in/i }).click();
      await expect(page.getByRole('alert')).toBeVisible();
    }
    await page.getByLabel('Password').fill(PW);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByRole('alert')).toContainText(/locked|too many/i);
    await expect(page).toHaveURL(/\/login/);
    const audit = await (await owner.get('/api/admin/audit?per_page=100')).json();
    expect(audit.items.some((e: any) => e.action === 'account.locked')).toBe(true);
    await owner.dispose();
  });
});

test.describe('registration modes', () => {
  test.afterAll(async () => { await setSetting('signup.mode', 'admin'); await setSetting('signup.require_approval', true); });

  test('admin-only: the page and the API both refuse', async ({ page }) => {
    await setSetting('signup.mode', 'admin');
    await page.goto('/signup');
    await expect(page.getByText(/administrator/i).first()).toBeVisible();
    await expect(page.getByLabel('Your name')).toHaveCount(0);
    const r = await page.request.post('/api/auth/signup', { data: { email: 'x@e2e.test', password: PW, display_name: 'X' } });
    expect(r.status()).toBe(403);
  });

  test('public with approval: sign-up waits, the administrator approves in People, then the person signs in', async ({ page, as }) => {
    await setSetting('signup.mode', 'public');
    await setSetting('signup.require_approval', true);
    await page.goto('/signup');
    await expect(page.getByText(/must be approved/i)).toBeVisible();
    await page.getByLabel('Your name').fill('Pat Pending');
    await page.getByLabel('Email').fill('pat@e2e.test');
    await page.getByLabel('Password').fill(PW);
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.locator('main .notice.ok')).toBeVisible();
    await signIn(page, 'pat@e2e.test', PW);
    await expect(page.getByRole('alert')).toContainText(/approv/i);

    const admin = await as('admin');
    await admin.goto('/admin/people');
    await admin.getByLabel('Status').selectOption('pending_approval');
    await admin.getByRole('checkbox', { name: 'Select Pat Pending' }).check();
    await admin.getByRole('button', { name: 'Approve', exact: true }).click();
    await admin.getByRole('button', { name: /^Approve 1$/ }).click();
    await signIn(page, 'pat@e2e.test', PW);
    await expect(page).toHaveURL(/\/chat/);
    // approval never grants more than the default role
    expect((await page.request.get('/api/admin/users')).status()).toBe(403);
  });

  test('invite-only: an invitation link works once', async ({ page, as }) => {
    await setSetting('signup.mode', 'invite');
    await page.goto('/signup');
    await page.getByLabel('Your name').fill('No Invite');
    await page.getByLabel('Email').fill('noinvite@e2e.test');
    await page.getByLabel('Password').fill(PW);
    await page.getByLabel('Invitation code').fill('not-a-real-invite');
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.getByRole('alert')).toBeVisible();

    const admin = await as('admin');
    await admin.goto('/admin/invites');
    await admin.getByRole('button', { name: 'Create invitation' }).click();
    await admin.getByRole('button', { name: 'Create', exact: true }).click();
    const link = await admin.getByLabel('Invitation link').inputValue();
    expect(link).toContain('/signup?invite=');
    await admin.getByRole('button', { name: 'Done' }).click();

    await page.goto(new URL(link).pathname + new URL(link).search);
    await page.getByLabel('Your name').fill('Ivy Invited');
    await page.getByLabel('Email').fill('ivy@e2e.test');
    await page.getByLabel('Password').fill(PW);
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.locator('main .notice.ok')).toBeVisible();
    await signIn(page, 'ivy@e2e.test', PW);
    await expect(page).toHaveURL(/\/chat/);
    const again = await page.request.post('/api/auth/signup', { data: { email: 'ivy2@e2e.test', password: PW, display_name: 'Ivy Two', invite: link.split('invite=')[1] } });
    expect(again.status()).toBe(403); // single use
  });

  test('approved domains only', async ({ page }) => {
    await setSetting('signup.mode', 'domain');
    await setSetting('signup.allowed_domains', ['lab.example']);
    await setSetting('signup.require_approval', false);
    const bad = await page.request.post('/api/auth/signup', { data: { email: 'x@other.example', password: PW, display_name: 'X' } });
    expect(bad.status()).toBe(403);
    await page.goto('/signup');
    await page.getByLabel('Your name').fill('Dom Ain');
    await page.getByLabel('Email').fill('dom@lab.example');
    await page.getByLabel('Password').fill(PW);
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.locator('main .notice.ok')).toBeVisible();
    await setSetting('signup.allowed_domains', []);
  });
});

test.describe('multi-factor authentication', () => {
  test('enrol with an authenticator code, then sign-in demands the code', async ({ page }) => {
    const owner = await apiAs('owner');
    await owner.post('/api/admin/users', { email: 'mfa@e2e.test', display_name: 'Mia Factor', password: PW, role_id: 'member', team_ids: [] });
    await owner.dispose();
    await signIn(page, 'mfa@e2e.test', PW);
    await expect(page).toHaveURL(/\/chat/);
    await page.goto('/account');
    await page.getByRole('button', { name: 'Set up', exact: true }).click();
    const secret = (await page.locator('dd', { hasText: /^[A-Z2-7]{16,}$/ }).first().innerText()).trim();
    await page.getByLabel('Code', { exact: true }).fill(totp(secret));
    await page.getByRole('button', { name: 'Confirm and turn on' }).click();
    await expect(page.locator('.chip.ok', { hasText: 'On' })).toBeVisible();
    await page.context().clearCookies();
    await page.goto('/login');
    await page.getByLabel('Email').fill('mfa@e2e.test');
    await page.getByLabel('Password').fill(PW);
    await page.getByRole('button', { name: /sign in/i }).click();
    await expect(page.getByLabel('Authentication code')).toBeVisible();
    await page.getByLabel('Authentication code').fill('000000');
    await page.getByRole('button', { name: /Verify/ }).click();
    await expect(page.getByRole('alert')).toBeVisible();
    await page.getByLabel('Authentication code').fill(totp(secret));
    await page.getByRole('button', { name: /Verify/ }).click();
    await expect(page).toHaveURL(/\/chat/);
  });
});

test('separate instances: an account on one server cannot sign in to the other', async ({ page }) => {
  await page.goto(`${URLS.fresh}/login`);
  const r = await page.request.post(`${URLS.fresh}/api/auth/login`, { data: { email: USERS.member.email, password: USERS.member.password } });
  expect(r.status()).toBe(401);
});
