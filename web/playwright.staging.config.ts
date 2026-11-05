import { defineConfig } from '@playwright/test';
import path from 'node:path';

// The same real-Chrome scenarios against a deployed STAGING instance (never production): real engine, real OpenJutsu. Needs
//   STAGING_URL   (default http://192.168.20.193:8790)   STAGING_EMAIL, STAGING_PW   a synthetic staging account   STAGING_FIX   folder with the fixtures
export const STG = { url: process.env.STAGING_URL ?? 'http://192.168.20.193:8790', fix: process.env.STAGING_FIX ?? '' };
export default defineConfig({
  testDir: './e2e_media/staging',
  testMatch: /.*\.spec\.ts/,
  workers: 1,
  timeout: 1_800_000,
  expect: { timeout: 30_000 },
  reporter: [['list']],
  globalSetup: './e2e_media/staging/setup.ts',
  use: { channel: 'chrome', headless: true, viewport: { width: 1280, height: 800 }, baseURL: STG.url, trace: 'off', screenshot: 'only-on-failure' },
  outputDir: path.join(process.env.STAGING_OUT ?? 'test-results', 'staging')
});
