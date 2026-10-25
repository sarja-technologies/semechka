import { defineConfig } from '@playwright/test';
import { BACKEND, DIRS, PORTS, PY, URLS, WEB, resetState } from './e2e/support/env';
import path from 'node:path';

resetState();

const server = (port: number, dir: string) => ({
  command: `${PY} -c "from semechka.cli import main; main()" serve --port ${port}`,
  cwd: BACKEND,
  url: `http://127.0.0.1:${port}/api/health`,
  reuseExistingServer: false,
  timeout: 60_000,
  env: { PYTHONPATH: BACKEND, SEMECHKA_DATA_DIR: dir, SEMECHKA_HOST: '127.0.0.1', SEMECHKA_WEB_DIR: path.join(WEB, 'build'), PYTHONUNBUFFERED: '1' }
});

// Real browsers installed on this machine (none downloaded), real HTTP servers, a fresh data directory per run.
// Chrome runs every spec. E2E_FIREFOX=1 runs them in the system Firefox instead, driven over WebDriver BiDi; a snap
// Firefox can only open profiles under ~/snap/firefox, so point TMPDIR there:
//   mkdir -p ~/snap/firefox/common/pw-tmp && E2E_FIREFOX=1 TMPDIR=~/snap/firefox/common/pw-tmp npx playwright test
const FIREFOX = !!process.env.E2E_FIREFOX;
export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.spec\.ts/,
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 8_000 },
  reporter: [['list'], ['json', { outputFile: `test-results/report${FIREFOX ? '-firefox' : ''}.json` }]],
  globalSetup: './e2e/global-setup.ts',
  use: { ...(FIREFOX ? { browserName: 'firefox' as const, channel: 'moz-firefox' } : { channel: 'chrome' }), headless: true, viewport: { width: 1280, height: 800 }, baseURL: URLS.seeded, trace: 'retain-on-failure', screenshot: 'only-on-failure' },
  webServer: [
    { command: `${PY} -m uvicorn fakebackend:app --app-dir tests --host 127.0.0.1 --port ${PORTS.fake}`, cwd: BACKEND, url: `${URLS.fake}/models`, reuseExistingServer: false, env: { PYTHONPATH: BACKEND } },
    server(PORTS.fresh, DIRS.fresh),
    server(PORTS.seeded, DIRS.seeded)
  ]
});
