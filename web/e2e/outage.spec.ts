import { spawn, type ChildProcess } from 'node:child_process';
import { test, expect, modelOptions, chooseModel } from './support/fixtures';
import { apiAs } from './support/api';
import { BACKEND, PY } from './support/env';

const PORT = 8828;
let proc: ChildProcess | null = null;

async function up() {
  proc = spawn(PY, ['-m', 'uvicorn', 'fakebackend:app', '--app-dir', 'tests', '--host', '127.0.0.1', '--port', String(PORT)], { cwd: BACKEND, env: { ...process.env, PYTHONPATH: BACKEND }, stdio: 'ignore' });
  for (let i = 0; i < 60; i++) { try { if ((await fetch(`http://127.0.0.1:${PORT}/v1/models`)).ok) return; } catch { /* not yet */ } await new Promise((r) => setTimeout(r, 250)); }
  throw new Error('fake backend did not start');
}
async function down() {
  if (!proc) return;
  const p = proc; proc = null;
  p.kill('SIGKILL');
  await new Promise((r) => p.once('exit', r));
}
test.afterAll(down);

test('a backend that goes away produces a clear, recoverable error, and chat resumes when it returns', async ({ as }) => {
  await up();
  const owner = await apiAs('owner');
  const made = await (await owner.post('/api/admin/backends', { name: 'Outage test engine', adapter: 'openai_compat', base_url: `http://127.0.0.1:${PORT}/v1` })).json();
  expect((await owner.post(`/api/admin/backends/${made.id}/sync`)).ok()).toBe(true);
  const models = (await (await owner.get('/api/admin/models')).json()).items.filter((m: any) => m.backend_id === made.id);
  expect(models.length).toBe(2);
  const target = models.find((m: any) => m.real_id === 'fake-small');
  await owner.patch(`/api/admin/models/${target.id}`, { alias: 'Outage Model', enabled: true });
  expect((await owner.put(`/api/admin/models/${target.id}/grants`, { grants: [{ subject_type: 'role', subject_id: 'member', effect: 'allow' }] })).ok()).toBe(true);

  const page = await as('member');
  await page.goto('/chat');
  await expect.poll(() => modelOptions(page)).toContain('Outage Model');
  await chooseModel(page, 'Outage Model');
  await page.getByLabel('Message', { exact: true }).fill('before the outage');
  await page.getByRole('button', { name: /^Send/ }).click();
  await expect(page.locator('article.msg.assistant').last()).toContainText('Hello from the fake model');

  await down();
  await page.getByLabel('Message', { exact: true }).fill('during the outage');
  await page.getByRole('button', { name: /^Send/ }).click();
  // a plain-language error, no stack trace or address, and the person can try again
  const err = page.getByRole('alert').first();
  await expect(err).toBeVisible({ timeout: 20_000 });
  const text = await err.innerText();
  expect(text.length).toBeGreaterThan(10);
  expect(text).not.toMatch(/127\.0\.0\.1|Traceback|ECONNREFUSED|httpx|openai_compat/i);
  await expect(page.getByRole('button', { name: /^Send/ })).toBeVisible();
  await expect(page.getByLabel('Message', { exact: true })).toBeEnabled();
  await expect(page.locator('[data-chat-status]')).toContainText(/failed/i);

  // an administrator sees the truth about the backend
  const t = await (await owner.post(`/api/admin/backends/${made.id}/test`)).json();
  expect(JSON.stringify(t).toLowerCase()).toMatch(/fail|unreachable|refused|error|false/);

  await up();
  await page.getByLabel('Message', { exact: true }).fill('after the outage');
  await expect(async () => {
    await page.getByRole('button', { name: /^Send/ }).click();
    await expect(page.locator('article.msg.assistant').last()).toContainText('Hello from the fake model', { timeout: 6000 });
    await expect(page.locator('article.msg.user').last()).toContainText('after the outage');
  }).toPass({ timeout: 45_000 });

  await owner.ctx.fetch(`/api/admin/backends/${made.id}`, { method: 'DELETE', headers: { 'x-csrf-token': owner.csrf } });
  await owner.dispose();
});
