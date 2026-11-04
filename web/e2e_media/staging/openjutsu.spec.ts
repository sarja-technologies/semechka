import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { cards, composer, drop, waitReady } from '../helpers';
import { STG } from '../../playwright.staging.config';

const OUT = process.env.STAGING_OUT ?? '.';
test.use({ storageState: path.join(OUT, 'staging-owner.json') });
const shot = (page: any, n: string) => page.screenshot({ path: path.join(OUT, `${n}.png`) });
const sha = (b: Buffer) => createHash('sha256').update(b).digest('hex');

test('real OpenJutsu: drag a video in, trim and crop it, send it, and the engine gets exactly that file', async ({ page }) => {
  const clip = path.join(STG.fix, 'src_3s_480p.mp4'), ref = path.join(STG.fix, 'ref_woman_yellow_coat.png');
  await page.goto('/chat?new=' + Date.now());
  await page.getByRole('radio', { name: 'Video' }).click();
  await page.getByRole('button', { name: /model: /i }).click();
  await page.getByRole('listbox').getByRole('option', { name: /openjutsu/i }).first().click();
  // this engine publishes one operation (swap): no operation chooser, the same tray
  console.log('operation chooser shown:', await page.getByRole('radiogroup', { name: /Operation/ }).count());
  await drop(page, composer(page), [clip, ref]);
  await waitReady(page, 2);
  await expect(cards(page).nth(0).getByRole('combobox')).toHaveValue('video');
  await expect(cards(page).nth(1).getByRole('combobox')).toHaveValue('refs');
  await page.waitForTimeout(500);
  await shot(page, 's1-tray');
  // trim and crop the real clip
  await cards(page).first().getByRole('button', { name: /^Edit / }).click();
  const dlg = page.getByRole('dialog', { name: /src_3s_480p/ });
  await expect(dlg.getByRole('slider', { name: 'Trim start' })).toBeVisible();
  await dlg.getByLabel('Start', { exact: true }).fill('0.25'); await page.keyboard.press('Enter');
  await dlg.getByLabel('End', { exact: true }).fill('2.75'); await page.keyboard.press('Enter');
  await dlg.getByRole('button', { name: '4:3' }).click();
  await expect(dlg.locator('.out')).toContainText('640 × 480 · 0:02.50');
  await page.waitForTimeout(600);
  await shot(page, 's2-editor');
  await dlg.getByRole('button', { name: 'Use as source' }).click();
  await expect(dlg).toHaveCount(0, { timeout: 120_000 });
  await expect(cards(page).first()).toHaveAttribute('aria-label', /edited/);
  await page.getByLabel('Message', { exact: true }).fill('the woman in the black coat');
  await shot(page, 's3-ready');
  const edited = (await (await page.request.get('/api/media/assets?origin=edit')).json())[0];
  const editedBytes = Buffer.from(await (await page.request.get(`/api/media/assets/${edited.id}/content`)).body());
  writeFileSync(path.join(OUT, 'edited.mp4'), editedBytes);
  console.log('edited asset', edited.id, edited.width + 'x' + edited.height, edited.duration + 's', 'sha256', sha(editedBytes));
  // send to the real engine
  await page.getByRole('button', { name: /^Make video/ }).click();
  await expect(page.locator('article.msg.user .ac')).toHaveCount(2);
  await shot(page, 's4-sent');
  // follow the real job
  let last = '', bad = 0;
  const t0 = Date.now();
  let job: any = null;
  for (;;) {
    // poll the way the browser does while a job runs (list, the job itself, the Library), a few times a second: none of it may fail
    const burst = await Promise.all([page.request.get('/api/media/jobs?kind=video'), page.request.get('/api/media/jobs?kind=video'), page.request.get('/api/media/assets?limit=5')]);
    for (const r of burst) if (!r.ok()) { bad++; console.log('NON-200 while the job runs:', r.status(), r.url()); }
    job = (await burst[0].json())[0];
    const one = await page.request.get(`/api/media/jobs/${job.id}`);
    if (!one.ok()) { bad++; console.log('NON-200 on the job itself:', one.status()); }
    const line = `${job.status} ${job.progress_text ?? ''} ${job.progress != null ? Math.round(job.progress * 100) + '%' : ''}`;
    if (line !== last) { console.log(`[${Math.round((Date.now() - t0) / 1000)}s] ${line}`); last = line; }
    if (['completed', 'failed', 'cancelled'].includes(job.status)) break;
    await page.waitForTimeout(700);
    if (Date.now() - t0 > 25 * 60_000) throw new Error('job did not finish in 25 minutes');
  }
  console.log('job', job.id, job.status, job.error ?? '', 'sources', JSON.stringify(job.params?.sources));
  expect(bad, 'requests that failed while the job ran').toBe(0);
  expect(job.status).toBe('completed');
  expect(job.params.sources.video).toEqual([edited.id]);
  const res = job.assets[0];
  const resBytes = Buffer.from(await (await page.request.get(`/api/media/assets/${res.id}/content`)).body());
  writeFileSync(path.join(OUT, 'result.mp4'), resBytes);
  const pr = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', path.join(OUT, 'result.mp4')]).toString());
  const v = pr.streams.find((s: any) => s.codec_type === 'video');
  console.log('result', res.id, `${v.width}x${v.height}`, v.codec_name, 'duration', pr.format.duration, 'size', resBytes.length);
  await expect(page.locator('article.msg.assistant video')).toBeVisible({ timeout: 60_000 });
  await page.waitForTimeout(800);
  await shot(page, 's5-result');
  console.log('JOB_ID', job.id, 'EDITED_SHA', sha(editedBytes));
});
