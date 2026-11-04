import { expect, type Page, type Locator } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdirSync, readFileSync, writeFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import { MP, TMP } from '../playwright.media.config';

export const FIX = path.join(TMP, 'fixtures');
export const SHOTS = path.join(TMP, '..', 'shots-media');
mkdirSync(FIX, { recursive: true });
mkdirSync(SHOTS, { recursive: true });

function ff(args: string[], out: string) {
  if (existsSync(out)) return out;
  execFileSync('ffmpeg', ['-nostdin', '-loglevel', 'error', '-y', ...args, out]);
  return out;
}
/** Real media made with ffmpeg: a 6 s 640×360 clip with sound whose picture changes, reference pictures with faces' worth of detail, and junk. */
export function fixtures() {
  const clip = ff(['-f', 'lavfi', '-i', 'testsrc2=size=640x360:rate=24:duration=6', '-f', 'lavfi', '-i', 'sine=frequency=330:duration=6', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-movflags', '+faststart'], path.join(FIX, 'harbour.mp4'));
  const mov = ff(['-f', 'lavfi', '-i', 'testsrc=size=320x240:rate=24:duration=2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p'], path.join(FIX, 'phone.mov'));
  const webm = ff(['-f', 'lavfi', '-i', 'testsrc=size=320x180:rate=15:duration=2', '-c:v', 'libvpx-vp9', '-b:v', '150k'], path.join(FIX, 'loop.webm'));
  const a = ff(['-f', 'lavfi', '-i', 'mandelbrot=size=600x600', '-frames:v', '1'], path.join(FIX, 'portrait-a.png'));
  const b = ff(['-f', 'lavfi', '-i', 'gradients=size=500x700:c0=0x336699:c1=0xddaa55', '-frames:v', '1', '-q:v', '2'], path.join(FIX, 'portrait-b.jpg'));
  const c = ff(['-f', 'lavfi', '-i', 'testsrc2=size=480x480', '-frames:v', '1'], path.join(FIX, 'portrait-c.png'));
  const junk = path.join(FIX, 'broken.mp4');
  if (!existsSync(junk)) writeFileSync(junk, Buffer.concat([Buffer.from([0, 0, 0, 24]), Buffer.from('ftypmp42'), Buffer.alloc(4000, 7)]));
  const notes = path.join(FIX, 'notes.txt');
  if (!existsSync(notes)) writeFileSync(notes, 'Shot list for the harbour clip.\n');
  const big = ff(['-f', 'lavfi', '-i', 'testsrc2=size=1280x720:rate=30:duration=40', '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '14', '-pix_fmt', 'yuv420p'], path.join(FIX, 'big.mp4'));
  return { clip, mov, webm, a, b, c, junk, notes, big };
}

export const sha = (p: string) => createHash('sha256').update(readFileSync(p)).digest('hex');
export const sha256 = (b: Buffer) => createHash('sha256').update(b).digest('hex');

const MIME: Record<string, string> = { '.mp4': 'video/mp4', '.mov': 'video/quicktime', '.webm': 'video/webm', '.png': 'image/png', '.jpg': 'image/jpeg', '.txt': 'text/plain' };

/** Drop real files on an element the way the browser does: dragenter, dragover, drop with a DataTransfer that holds the files. */
export async function drop(page: Page, target: Locator, files: string[]) {
  const payload = files.map((f) => ({ name: path.basename(f), type: MIME[path.extname(f)] ?? '', b64: readFileSync(f).toString('base64') }));
  const handle = await target.elementHandle();
  await page.evaluate(({ el, payload }) => {
    const dt = new DataTransfer();
    for (const p of payload) { const bin = atob(p.b64); const u = new Uint8Array(bin.length); for (let i = 0; i < bin.length; i++) u[i] = bin.charCodeAt(i); dt.items.add(new File([u], p.name, { type: p.type })); }
    const fire = (type: string) => (el as Element).dispatchEvent(new DragEvent(type, { bubbles: true, cancelable: true, dataTransfer: dt }));
    fire('dragenter'); fire('dragover'); fire('drop');
  }, { el: handle, payload });
}
/** Only the first part of the drag: the drop target lights up and stays lit. */
export async function dragOver(page: Page, target: Locator, files: string[]) {
  const payload = files.map((f) => ({ name: path.basename(f), type: MIME[path.extname(f)] ?? '' }));
  const handle = await target.elementHandle();
  await page.evaluate(({ el, payload }) => {
    const dt = new DataTransfer();
    for (const p of payload) dt.items.add(new File([new Uint8Array(4)], p.name, { type: p.type }));
    for (const t of ['dragenter', 'dragover']) (el as Element).dispatchEvent(new DragEvent(t, { bubbles: true, cancelable: true, dataTransfer: dt }));
  }, { el: handle, payload });
}

export const engine = async (page: Page) => (await page.request.get(`http://127.0.0.1:${MP.engine}/__state`)).json();
export const resetEngine = (page: Page) => page.request.post(`http://127.0.0.1:${MP.engine}/__reset`);
export const shot = (page: Page | Locator, name: string) => page.screenshot({ path: path.join(SHOTS, `${name}.png`) });

export const composer = (page: Page) => page.locator('.composer');
export const tray = (page: Page) => page.getByRole('list', { name: 'Attached files' });
export const cards = (page: Page) => tray(page).locator('article.card');
export async function waitReady(page: Page, n: number) {
  await expect(cards(page)).toHaveCount(n);
  await expect(tray(page).getByRole('progressbar')).toHaveCount(0, { timeout: 30_000 });
}
export async function chooseModel(page: Page, name: RegExp) {
  await page.getByRole('button', { name: /^(Video |Picture )?[Mm]odel: / }).click();
  await page.getByRole('listbox').getByRole('option', { name }).first().click();
}
