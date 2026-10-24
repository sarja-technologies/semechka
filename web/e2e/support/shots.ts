import type { Page } from '@playwright/test';
import { mkdirSync } from 'node:fs';
import path from 'node:path';
import { ROOT } from './env';

/** Screenshots go to web/shots-out (git-ignored, not cleaned between runs) by default; set E2E_SHOTS=docs to refresh the curated set in docs/screenshots. */
const OUT = process.env.E2E_SHOTS === 'docs' ? path.join(ROOT, 'docs/screenshots') : path.join(ROOT, 'web/shots-out');
export async function shot(page: Page, name: string, fullPage = false) {
  mkdirSync(OUT, { recursive: true });
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage, animations: 'disabled' });
}
