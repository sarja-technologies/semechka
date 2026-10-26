import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { test, expect, noHorizontalScroll } from './support/fixtures';
import { apiAs, setSetting } from './support/api';
import { USERS } from './support/env';
import { shot } from './support/shots';

// The real application, a real browser, real background jobs. The fake model answers agent turns from a fixed script.
test.describe.configure({ mode: 'serial' });

/** Fail on anything a person would experience as broken: console errors and failed API calls. */
function watch(page: Page) {
  const bad: string[] = [];
  page.on('console', (m) => m.type() === 'error' && bad.push(`console: ${m.text()}`));
  page.on('pageerror', (e) => bad.push(`exception: ${e.message}`));
  page.on('response', (r) => r.url().includes('/api/') && r.status() >= 400 && r.status() !== 404 && bad.push(`${r.status()} ${r.url()}`));
  return bad;
}
async function axe(page: Page) {
  await page.evaluate(() => Promise.all(document.getAnimations().map((a) => a.finished.catch(() => null))));
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze();
  return r.violations.map((v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.length}) e.g. ${v.nodes[0]?.target.join(' ')}`);
}
const upload = (page: Page, name: string, text: string) => page.locator('input[type=file]').first().setInputFiles({ name, mimeType: 'text/markdown', buffer: Buffer.from(text) });

let wsUrl = '';
const flags: Record<string, unknown> = {};

test.beforeAll(async () => {
  for (const k of ['cloud.workspaces.enabled', 'cloud.agents.enabled']) flags[k] = await setSetting(k, true);
});
test.afterAll(async () => { for (const [k, v] of Object.entries(flags)) await setSetting(k, v ?? false); });

test('Workspaces is in the navigation only once switched on, and the owner can create one', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto('/chat');
  await page.getByRole('link', { name: 'Workspaces' }).first().click();
  await expect(page.getByRole('heading', { level: 1, name: 'Workspaces' })).toBeVisible();
  await expect(page.getByText('No workspaces visible to you yet.')).toBeVisible();
  await page.getByRole('button', { name: 'New workspace' }).first().click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Marketing');
  await page.getByLabel('Description').fill('October campaign and brand material');
  await page.getByRole('button', { name: 'Create workspace' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Marketing' })).toBeVisible();
  wsUrl = page.url();
  await expect(page.getByText('Administrator')).toBeVisible();
  for (const tab of ['Files', 'Contexts', 'Agents', 'Tasks', 'Access', 'Settings']) await expect(page.getByRole('link', { name: tab, exact: true })).toBeVisible();
  expect(bad).toEqual([]);
});

test('files: upload, read in the background, search with sources, folders, rename, move', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto(wsUrl);
  await expect(page.getByText('This folder is empty.')).toBeVisible();
  await upload(page, 'campaign-brief.md', '# October campaign\nGoal: grow newsletter signups by twenty percent with a video-led launch on October 14.\n\n## Budget\nThe video budget is thirty thousand dollars.\n');
  const row = page.getByRole('row', { name: /campaign-brief\.md/ });
  await expect(row).toBeVisible();
  await expect(row.getByText('Searchable')).toBeVisible({ timeout: 30_000 }); // indexed by the real background worker
  await page.getByLabel('Search this workspace').fill('newsletter signups');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const hit = page.getByRole('region', { name: 'Search results' });
  await expect(hit.getByText('campaign-brief.md').first()).toBeVisible();
  await expect(hit.getByText(/thirty thousand|newsletter signups/).first()).toBeVisible();
  await expect(hit.getByText(/October campaign|keyword/).first()).toBeVisible(); // where it came from
  await shot(page, 'workspace-files-search');
  await hit.getByRole('button', { name: 'Clear' }).click();

  await page.getByRole('button', { name: 'New folder' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Reports');
  await page.getByRole('button', { name: 'Create folder' }).click();
  await expect(page.getByRole('button', { name: 'Reports', exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Reports', exact: true }).click();
  await expect(page).toHaveURL(/folder=/);
  await upload(page, 'q3.md', '# Q3\nRevenue grew twelve percent.\n');
  await expect(page.getByRole('row', { name: /q3\.md/ })).toBeVisible();
  await page.getByRole('navigation', { name: 'Folder path' }).getByRole('button', { name: 'Marketing', exact: true }).click(); // breadcrumb back to the root
  await expect(page.getByRole('row', { name: /campaign-brief\.md/ })).toBeVisible();

  // rename, then move into the folder
  await page.getByRole('button', { name: 'Actions for campaign-brief.md' }).click();
  await page.getByRole('menuitem', { name: 'Rename' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('brief.md');
  await page.getByRole('button', { name: 'Rename', exact: true }).click();
  await expect(page.getByRole('row', { name: /brief\.md/ })).toBeVisible();
  await page.getByRole('button', { name: 'Actions for brief.md' }).click();
  await page.getByRole('menuitem', { name: 'Move to…' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reports', exact: true }).click();
  await page.getByRole('button', { name: 'Move here' }).click();
  await expect(page.getByRole('row', { name: /brief\.md/ })).toHaveCount(0);
  expect(bad).toEqual([]);
});

test('files: edit text as a new version, history, restore; trash and restore', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto(wsUrl);
  await page.getByRole('button', { name: 'New document' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('notes');
  await page.getByLabel('Content').fill('first draft');
  await page.getByRole('button', { name: 'Create document' }).click();
  await expect(page.getByRole('row', { name: /notes\.md/ })).toBeVisible(); // saved as Markdown without being asked for an extension
  await page.getByRole('button', { name: 'Actions for notes.md' }).click();
  await page.getByRole('menuitem', { name: 'Edit text' }).click();
  await expect(page.getByLabel('Text')).toHaveValue('first draft');
  await page.getByLabel('Text').fill('second draft');
  await page.getByRole('button', { name: 'Save' }).click();
  await expect(page.getByText('v2')).toBeVisible();
  await page.getByRole('button', { name: 'Actions for notes.md' }).click();
  await page.getByRole('menuitem', { name: 'Version history' }).click();
  const hist = page.getByRole('dialog');
  await expect(hist.getByText('Current')).toBeVisible();
  await hist.getByRole('row', { name: /v1/ }).getByRole('button', { name: 'Restore' }).click();
  await expect(page.getByText('v3')).toBeVisible(); // restoring adds a version; nothing is rewritten
  await page.getByRole('button', { name: 'Actions for notes.md' }).click();
  await page.getByRole('menuitem', { name: 'Move to trash' }).click();
  await page.getByRole('button', { name: 'Move to trash' }).last().click();
  await expect(page.getByRole('row', { name: /notes\.md/ })).toHaveCount(0);
  await page.getByRole('button', { name: 'Trash' }).click();
  await page.getByRole('row', { name: /notes\.md/ }).getByRole('button', { name: 'Restore' }).click();
  await page.getByRole('button', { name: 'Back to files' }).click();
  await expect(page.getByRole('row', { name: /notes\.md/ })).toBeVisible();
  expect(bad).toEqual([]);
});

test('contexts: create, add files, search with provenance, history', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto(`${wsUrl}/contexts`);
  await page.getByRole('button', { name: 'New context' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Campaign October');
  await page.getByRole('button', { name: 'Create context' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Campaign October' })).toBeVisible();
  await page.getByRole('button', { name: 'Add files' }).click();
  await page.getByLabel('Filter').fill('brief');
  await page.getByRole('dialog').getByRole('checkbox').first().check();
  await page.getByRole('button', { name: 'Add selected' }).click();
  await expect(page.getByRole('row', { name: /brief\.md/ })).toBeVisible();
  await page.getByLabel('Question').fill('video launch budget');
  await page.getByRole('button', { name: 'Search', exact: true }).click();
  const res = page.getByRole('region', { name: 'Ask this context' });
  await expect(res.getByText('brief.md').first()).toBeVisible();
  await expect(res.getByText(/version 1/).first()).toBeVisible();
  await expect(res.getByText(/Searched 1 file/)).toBeVisible();
  await page.getByRole('button', { name: 'History' }).click();
  await expect(page.getByRole('dialog').getByText('Current')).toBeVisible();
  await page.getByRole('button', { name: 'Close', exact: true }).first().click();
  await shot(page, 'workspace-contexts');
  expect(bad).toEqual([]);
});

test('access: a member sees what they were given and nothing more; removing them closes it at once', async ({ as, browser }) => {
  const owner = await as('owner'); const bad = watch(owner);
  await owner.goto(`${wsUrl}/access`);
  await owner.getByRole('button', { name: 'Add person or group' }).click();
  await owner.getByLabel('Who', { exact: true }).selectOption({ label: USERS.member.name });
  await owner.getByLabel('Role', { exact: true }).selectOption({ label: 'Viewer' });
  await owner.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(owner.getByRole('row', { name: new RegExp(USERS.member.name) })).toBeVisible();
  await shot(owner, 'workspace-access');

  const member = await as('member'); const mbad = watch(member);
  await member.goto('/workspaces');
  await member.getByRole('link', { name: /Marketing/ }).first().click();
  await expect(member.getByRole('heading', { level: 1, name: 'Marketing' })).toBeVisible();
  await expect(member.getByText('Viewer', { exact: true })).toBeVisible();
  await expect(member.getByRole('button', { name: 'Upload' })).toHaveCount(0);        // cannot write
  await expect(member.getByRole('link', { name: 'Access', exact: true })).toHaveCount(0); // cannot administer
  await expect(member.getByRole('link', { name: 'Settings', exact: true })).toHaveCount(0);
  await member.getByRole('button', { name: 'Reports', exact: true }).click();
  await expect(member.getByRole('row', { name: /brief\.md/ })).toBeVisible();
  await member.getByRole('button', { name: 'brief.md', exact: true }).click();
  await expect(member.getByRole('dialog').getByText('October campaign')).toBeVisible(); // can read
  expect(mbad).toEqual([]);

  // an explicit deny beats the role
  await owner.getByRole('button', { name: 'Add rule' }).click();
  await owner.getByLabel('For a', { exact: true }).selectOption('user');
  await owner.getByLabel('Who', { exact: true }).last().selectOption({ label: USERS.member.name });
  await owner.getByLabel('Deny these actions, whatever their role says').check();
  await owner.getByRole('dialog').getByLabel('Download files and artifacts').check();
  await owner.getByRole('button', { name: 'Add rule' }).last().click();
  await expect(owner.getByRole('row', { name: /Deny/ })).toBeVisible();
  await owner.getByLabel('Person', { exact: true }).selectOption({ label: USERS.member.name });
  await owner.getByLabel('Action', { exact: true }).selectOption({ label: 'Download files and artifacts' });
  await owner.getByRole('button', { name: 'Explain' }).click();
  await expect(owner.getByText('Not allowed.')).toBeVisible();
  await owner.getByRole('row', { name: /Deny/ }).getByRole('button', { name: 'Remove' }).click();

  // the guest was never added: the workspace does not exist for them
  const guest = await as('guest');
  await guest.goto(wsUrl);
  await expect(guest.getByText('does not exist, or you do not have access')).toBeVisible();

  await owner.getByRole('row', { name: new RegExp(USERS.member.name) }).getByRole('button', { name: /Remove/ }).click();
  await member.reload();
  await expect(member.getByText('does not exist, or you do not have access')).toBeVisible();
  expect(bad).toEqual([]);
  void browser;
});

test('agents: create, run a real task in the background, see steps and the file it saved', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto(`${wsUrl}/agents`);
  await page.getByRole('button', { name: 'New agent' }).first().click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).first().fill('Campaign planner');
  await page.getByRole('textbox', { name: 'Instructions', exact: true }).fill('You write short management documents.');
  await page.getByRole('button', { name: 'Create agent' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Campaign planner' })).toBeVisible();
  await expect(page.getByText('First available chat model').or(page.getByText('Small Assistant')).first()).toBeVisible();
  await page.getByRole('textbox', { name: 'Task', exact: true }).fill('Write a short report from the workspace files.');
  await page.getByRole('button', { name: 'Start task' }).click();
  await expect(page).toHaveURL(/\/runs\/ar_/);
  await expect(page.getByText('Done', { exact: true }).first()).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole('region', { name: 'Answer' }).getByText('agent-report.docx')).toBeVisible();
  await expect(page.getByRole('region', { name: 'Files produced' }).getByText('agent-report.docx')).toBeVisible();
  await expect(page.getByText('Tool: list_files')).toBeVisible();
  await expect(page.getByText('Tool: create_document')).toBeVisible();
  await page.getByLabel('Show inputs and results').check();
  await expect(page.getByText('Prepared by the browser-test agent').first().or(page.getByText('"blocks"').first())).toBeVisible();
  await shot(page, 'workspace-run');
  // the report really is in the workspace, and is read in the background like any upload
  await page.goto(wsUrl);
  const row = page.getByRole('row', { name: /agent-report\.docx/ });
  await expect(row).toBeVisible();
  await expect(row.getByText('Searchable')).toBeVisible({ timeout: 30_000 });
  await page.goto(`${wsUrl}/tasks`);
  await expect(page.getByRole('link', { name: /Write a short report/ })).toBeVisible();
  expect(bad).toEqual([]);
});

test('agents: an action that needs approval waits for a person', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto(`${wsUrl}/agents`);
  await page.getByRole('button', { name: 'New agent' }).first().click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).first().fill('Careful writer');
  await page.getByLabel('write_text_file').check();
  await page.getByLabel('Ask a person before').selectOption('always');
  await page.getByRole('button', { name: 'Create agent' }).click();
  await page.getByRole('textbox', { name: 'Task', exact: true }).fill('ask-for-approval please save a note');
  await page.getByRole('button', { name: 'Start task' }).click();
  await expect(page.getByText('Waiting for your approval')).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText('Waiting for approval').first()).toBeVisible();
  await shot(page, 'workspace-approval');
  await page.getByRole('button', { name: 'Approve' }).click();
  await expect(page.getByText('Done', { exact: true }).first()).toBeVisible({ timeout: 60_000 });
  await page.goto(wsUrl);
  await expect(page.getByRole('row', { name: /approved-note\.md/ })).toBeVisible();
  expect(bad).toEqual([]);
});

test('settings: policy and limits are saved to the server and shown honestly', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto(`${wsUrl}/settings`);
  await expect(page.getByText(/Storage: /)).toBeVisible();
  await page.getByLabel('Storage (MB)').fill('50');
  await page.getByRole('button', { name: 'Save limits' }).click();
  await expect(page.getByText('Limits saved')).toBeVisible();
  await page.reload();
  await expect(page.getByLabel('Storage (MB)')).toHaveValue('50');
  await expect(page.getByRole('progressbar', { name: 'Storage used' })).toBeVisible();
  await page.getByLabel('Agents may use the network').check();
  await page.getByRole('button', { name: 'Save policy' }).click();
  await expect(page.getByText('Policy saved')).toBeVisible();
  const a = await apiAs('owner'); const list = await (await a.get('/api/orgs')).json();
  const ws = (await (await a.get(`/api/orgs/${list[0].id}/workspaces`)).json()).find((w: any) => w.name === 'Marketing');
  expect(ws.policy.deny).toContain('agent.tool.network'); expect(ws.quotas.storage_mb).toBe(50);
  await a.dispose();
  expect(bad).toEqual([]);
});

test('organisation: people, groups, roles, policy, usage and the activity log', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto('/workspaces');
  await page.getByRole('link', { name: 'Organisation' }).click();
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  await expect(page.getByRole('row', { name: new RegExp(USERS.member.name) })).toBeVisible();
  await expect(page.getByRole('row', { name: new RegExp(USERS.owner.name) }).getByRole('combobox')).toHaveValue(/or_/);

  await page.getByRole('link', { name: 'Groups' }).click();
  await page.getByRole('button', { name: 'New group' }).first().click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Finance');
  await page.getByRole('button', { name: 'Create group' }).click();
  await expect(page.getByRole('heading', { level: 2, name: /Finance/ })).toBeVisible();
  await page.getByLabel('Add a person').selectOption({ label: USERS.member.name });
  await page.getByRole('button', { name: 'Add', exact: true }).click();
  await expect(page.getByRole('row', { name: new RegExp(USERS.member.name) })).toBeVisible();

  await page.getByRole('link', { name: 'Roles' }).click();
  await page.getByRole('button', { name: 'New role' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Reviewer');
  await page.getByRole('dialog').getByLabel('Grant: Read files and folders').check();
  await page.getByRole('dialog').getByLabel('Deny: Download files and artifacts').check();
  await page.getByRole('button', { name: 'Save role' }).click();
  await expect(page.getByRole('row', { name: /Reviewer/ })).toBeVisible();
  await shot(page, 'organisation-roles');

  await page.getByRole('link', { name: 'Policy and limits' }).click();
  await page.getByLabel('Largest single upload (MB)').fill('25');
  await page.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(page.getByText('Saved')).toBeVisible();

  await page.getByRole('link', { name: 'Usage' }).click();
  await expect(page.getByRole('heading', { name: 'Storage' })).toBeVisible();
  await expect(page.getByRole('row', { name: /Tasks/ })).toBeVisible();
  await page.getByRole('link', { name: 'Activity log' }).click();
  await expect(page.getByRole('row', { name: /ws\.create/ })).toBeVisible();
  await expect(page.getByRole('row', { name: /role\.save/ })).toBeVisible();
  expect(bad).toEqual([]);
});

test('platform administration: organisations and the cloud status page', async ({ as }) => {
  const page = await as('owner'); const bad = watch(page);
  await page.goto('/admin/organisations');
  await expect(page.getByRole('heading', { level: 1, name: 'Organisations' })).toBeVisible();
  await expect(page.getByRole('row', { name: /Default/ })).toBeVisible();
  await page.getByRole('button', { name: 'New organisation' }).click();
  await page.getByRole('textbox', { name: 'Name', exact: true }).fill('Globex');
  await page.getByLabel("First owner's email").fill(USERS.tech.email);
  await page.getByRole('button', { name: 'Create organisation' }).click();
  const row = page.getByRole('row', { name: /Globex/ });
  await expect(row).toBeVisible();
  await row.getByRole('button', { name: 'Suspend' }).click();
  await expect(row.getByText('Suspended')).toBeVisible();
  await row.getByRole('button', { name: 'Reactivate' }).click();
  await expect(row.getByText('Active')).toBeVisible();
  await page.goto('/admin/cloud');
  await expect(page.getByRole('heading', { level: 1, name: 'Cloud workspaces' })).toBeVisible();
  await expect(page.getByText('Measured over the last 24 hours')).toBeVisible();
  await expect(page.getByRole('row', { name: /ingest/ }).first()).toBeVisible();
  await expect(page.getByRole('row', { name: /agent/ }).first()).toBeVisible();
  await shot(page, 'admin-cloud');
  // creating a second organisation switched off automatic joining of the first
  const a = await apiAs('owner'); const o = (await (await a.get('/api/admin/orgs')).json()).find((x: any) => x.is_default);
  expect(o.auto_join).toBe(false); await a.dispose();
  expect(bad).toEqual([]);
});

test('every new page works on a phone and passes the accessibility audit', async ({ as }) => {
  const page = await as('owner');
  await page.setViewportSize({ width: 375, height: 812 });
  const a = await apiAs('owner'); const list = await (await a.get('/api/orgs')).json(); await a.dispose();
  const routes = ['/workspaces', wsUrl.replace(/^https?:\/\/[^/]+/, ''), `${wsUrl.replace(/^https?:\/\/[^/]+/, '')}/contexts`, `${wsUrl.replace(/^https?:\/\/[^/]+/, '')}/agents`,
    `${wsUrl.replace(/^https?:\/\/[^/]+/, '')}/tasks`, `${wsUrl.replace(/^https?:\/\/[^/]+/, '')}/access`, `${wsUrl.replace(/^https?:\/\/[^/]+/, '')}/settings`,
    `/organisation/${list[0].id}`, `/organisation/${list[0].id}/roles`, `/organisation/${list[0].id}/policy`, `/organisation/${list[0].id}/usage`, `/organisation/${list[0].id}/audit`, '/admin/organisations', '/admin/cloud'];
  const problems: string[] = [];
  for (const r of routes) {
    await page.goto(r);
    await expect(page.getByRole('heading').first()).toBeVisible();
    await expect(page.getByText('Loading…')).toHaveCount(0, { timeout: 15_000 });
    await noHorizontalScroll(page);
    for (const v of await axe(page)) problems.push(`${r}: ${v}`);
  }
  expect(problems).toEqual([]);
});
