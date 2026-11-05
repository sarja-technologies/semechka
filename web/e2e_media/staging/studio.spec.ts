// OpenJutsu Studio against the real STAGING instance (never production): real Semechka 0.9.0, real staging Sarness engine, real ComfyUI and MiniMax H3 on the Spark.
// Vehicle replacement from the UI: upload a dirt-bike clip, trim and crop it, add three pictures of another bike, choose Vehicle replacement, name the subject,
// generate, watch the real process, open the real graph, and check the finished file.
import { test, expect, type Page } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { writeFileSync } from 'node:fs';
import path from 'node:path';
import { drop } from '../helpers';
import { STG } from '../../playwright.staging.config';

const OUT = process.env.STAGING_OUT ?? '.';
test.use({ storageState: path.join(OUT, 'staging-owner.json') });
const shot = (page: Page, n: string) => page.screenshot({ path: path.join(OUT, `${n}.png`), fullPage: false });
const api = async (page: Page, p: string) => (await page.request.get(p)).json();
const stepTo = (page: Page, id: string) => page.locator(`button[data-step="${id}"]`).click();
const tool = (page: Page, name: string) => page.getByTestId('panel').getByRole('toolbar', { name: 'Prepare tools' }).getByRole('button', { name, exact: true }).click();

function probe(buf: Buffer) {
  const f = path.join(OUT, `probe-${Date.now()}.bin`); writeFileSync(f, buf);
  const j = JSON.parse(execFileSync('ffprobe', ['-v', 'error', '-print_format', 'json', '-show_streams', '-show_format', f]).toString());
  const v = j.streams.find((s: any) => s.codec_type === 'video');
  return { w: +v.width, h: +v.height, d: +j.format.duration, audio: j.streams.some((s: any) => s.codec_type === 'audio') };
}

test('real OpenJutsu Studio: vehicle replacement end to end through the UI', async ({ page }) => {
  const clip = path.join(STG.fix, 'bike_clip.mp4');
  const refs = [1, 2, 3].map((i) => path.join(STG.fix, `ref_bike_${i}.png`));
  await page.goto('/openjutsu?new=1');
  await expect(page.getByTestId('start')).toBeVisible();
  await shot(page, 'st-01-start');
  await drop(page, page.getByTestId('start'), [clip]);
  await expect(page.getByTestId('panel').getByRole('heading', { name: 'Prepare the video' })).toBeVisible({ timeout: 90_000 });
  await expect(page.getByTestId('stage-frame')).toBeVisible();
  await expect.poll(() => new URL(page.url()).searchParams.get('project')).toBeTruthy();
  const pid = new URL(page.url()).searchParams.get('project')!;

  // trim to 2.5 s, crop to 4:3, 480p
  await tool(page, 'Trim');
  const dock = page.getByTestId('trim-dock');
  await dock.getByLabel('Start', { exact: true }).fill('0'); await dock.getByLabel('Start', { exact: true }).press('Enter');
  await dock.getByLabel('End', { exact: true }).fill('2.5'); await dock.getByLabel('End', { exact: true }).press('Enter');
  await expect(page.getByTestId('trim-selected')).toHaveText('0:02.50');
  await tool(page, 'Aspect ratio');
  await page.getByRole('radiogroup', { name: 'Aspect ratio' }).getByRole('radio', { name: '4:3' }).click();
  await tool(page, 'Resolution');
  await page.getByRole('radiogroup', { name: 'Resolution' }).getByRole('radio', { name: '480p' }).click();
  await expect(page.getByTestId('pipeline')).toBeVisible();
  await shot(page, 'st-02-prepare');
  const pipeText = await page.getByTestId('pipeline').innerText();
  console.log('PIPELINE:', pipeText.replace(/\s+/g, ' ').slice(0, 500));
  await page.getByRole('button', { name: /Prepare video|Prepare again/ }).click();
  await expect(page.getByTestId('prep-done')).toBeVisible({ timeout: 180_000 });
  await shot(page, 'st-03-prepared');
  const prepared = (await api(page, `/api/studio/projects/${pid}`)).prepared_asset ?? (await api(page, `/api/studio/projects/${pid}`)).prepared;
  console.log('prepared asset:', JSON.stringify(prepared)?.slice(0, 300));

  // references: three pictures of another bike; vehicle replacement takes one
  await stepTo(page, 'references');
  await page.locator('input[type=file][accept="image/*"]').setInputFiles(refs);
  await expect(page.getByTestId('ref-gallery').locator('li[data-ref-index]')).toHaveCount(3, { timeout: 60_000 });

  // operation: Vehicle replacement, subject in words
  await stepTo(page, 'operation');
  await page.getByTestId('modes').getByRole('radio', { name: /Vehicle replacement/ }).click();
  await page.getByLabel('Subject description').fill('the dirt bike');
  await page.getByRole('radiogroup', { name: 'Kind of subject' }).getByRole('radio', { name: /Vehicle/ }).click();
  await shot(page, 'st-04-operation');
  await stepTo(page, 'references');
  await expect(page.getByTestId('ref-count')).toBeVisible();
  await shot(page, 'st-05-references');   // 1 used, 2 "not used: this operation takes 1 picture"

  await stepTo(page, 'model');
  await shot(page, 'st-06-model');
  await stepTo(page, 'generate');
  await shot(page, 'st-07-generate');
  await page.getByTestId('generate').click();
  const live = page.getByTestId('live-job');
  await expect(live).toBeVisible({ timeout: 120_000 });
  const job = new URL(page.url()).searchParams.get('job')!;
  console.log('job', job);

  // watch the real process
  await live.getByRole('button', { name: 'VIEW PROCESS' }).click();
  const dlg = page.getByRole('dialog', { name: 'Job process' });
  await expect(dlg).toBeVisible();
  await expect(dlg).toContainText(/Starting|Loading|Tracking|Generating|Scene|queued|running/i, { timeout: 120_000 });
  await page.waitForTimeout(40_000);
  await shot(page, 'st-08-process-running');
  const ins = await api(page, `/api/studio/jobs/${job}/inspect`);
  console.log('inspect mid-run:', JSON.stringify({ status: ins.status, stage: ins.stage, nodes: ins.nodes, scenes: ins.scenes?.completed + '/' + ins.scenes?.total, comfy: ins.comfy }));
  await page.keyboard.press('Escape');
  await live.getByRole('button', { name: 'VIEW COMFYUI' }).click();
  const g = page.getByRole('dialog', { name: 'ComfyUI graph' });
  await expect(g).toBeVisible();
  await page.waitForTimeout(3_000);
  await shot(page, 'st-09-comfy-graph');
  await page.keyboard.press('Escape');

  // finished
  await expect(live).toHaveAttribute('data-status', 'completed', { timeout: 900_000 });
  const done = await api(page, `/api/studio/jobs/${job}/inspect`);
  expect(done.status).toBe('completed'); expect(done.output.state).toBe('ready');
  console.log('final inspect:', JSON.stringify({ stage: done.stage, output: done.output, loop: done.loop?.iterations_ended, conditioning: done.conditioning }));
  await expect(live.getByTestId('job-result')).toBeVisible();
  await shot(page, 'st-10-result');
  const list = (await api(page, '/api/studio/jobs')) as any[];
  const mine = list.find((j) => j.job === job);
  const asset = mine.outputs.find((o: any) => o.kind === 'video');
  const buf = Buffer.from(await (await page.request.get(`/api/media/assets/${asset.id}/content`)).body());
  writeFileSync(path.join(OUT, 'studio-vehicle-result.mp4'), buf);
  const pr = probe(buf);
  console.log('result file:', JSON.stringify(pr));
  expect(pr.d).toBeGreaterThan(2); expect(pr.w).toBeGreaterThan(300);
  // the saved job still knows all three references (one used, two kept)
  const jobDetail = await api(page, `/api/media/jobs/${job}`);
  const studio = jobDetail.params?.studio;
  console.log('job params studio refs:', JSON.stringify(studio?.references));
  expect(studio.references.length).toBe(3);
  expect(studio.references.filter((r: any) => r.active).length).toBe(1);
  // a reload restores the same job view
  await page.reload();
  await expect(page.getByTestId('live-job')).toHaveAttribute('data-status', 'completed');
  await shot(page, 'st-11-after-reload');
});
