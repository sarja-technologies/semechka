import { defineConfig } from '@playwright/test';

// Live authenticated UI tests against the STAGING instance (a real server on the Spark, real engine, real models).
// No web server is started and nothing is seeded: accounts exist already. Run through scripts/spark/live_ui.sh, which reads
// the synthetic credentials over ssh into the environment for this process only.
export default defineConfig({
  testDir: './e2e-live',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 35 * 60_000,
  expect: { timeout: 15_000 },
  reporter: [['list']],
  use: { actionTimeout: 30_000, navigationTimeout: 45_000, channel: 'chrome', headless: true, viewport: { width: 1280, height: 800 }, baseURL: process.env.STAGING_URL ?? 'http://192.168.20.193:8790', trace: 'retain-on-failure', screenshot: 'on' }
});
