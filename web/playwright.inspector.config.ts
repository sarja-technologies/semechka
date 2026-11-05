import { defineConfig } from '@playwright/test';

// Browser tests of the OpenJutsu Studio inspector components on their own: real Chrome, the components mounted by a tiny Vite harness
// (e2e_media/fixtures/inspector/harness) against a mock Studio API that serves real SSE, real Range-capable MP4 files and the scripted
// 7-scene swap run. No app server, no engine, no database: `npx playwright test -c playwright.inspector.config.ts`.
// (The same spec also runs under playwright.media.config.ts, which starts the full stack it does not need.)
export default defineConfig({
  testDir: './e2e_media',
  testMatch: /inspector\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 8_000 },
  reporter: [['line']],
  outputDir: '.e2e-media-tmp/inspector-results',
  use: { channel: 'chrome', headless: true, viewport: { width: 1280, height: 800 }, trace: 'retain-on-failure', screenshot: 'only-on-failure' }
});
