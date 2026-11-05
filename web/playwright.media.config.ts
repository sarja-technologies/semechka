import { defineConfig } from '@playwright/test';
import { existsSync, mkdirSync, rmSync } from 'node:fs';
import path from 'node:path';
import { BACKEND, PY, WEB } from './e2e/support/env';

// Browser tests of the media composer: real Chrome, the real Semechka server, the real database and disk, real ffmpeg, and an inference-engine
// double that speaks the Sarness job protocol with OpenJutsu's real input schema. Nothing about the media features is mocked.
// The ports and scratch folder can be moved with environment variables, so several people (or agents) can run this suite side by side.
export const MP = { engine: Number(process.env.MEDIA_E2E_ENGINE_PORT || 8841), app: Number(process.env.MEDIA_E2E_APP_PORT || 8842) };
export const TMP = path.join(WEB, process.env.MEDIA_E2E_TMP || '.e2e-media-tmp');
if (process.env.TEST_WORKER_INDEX === undefined) { rmSync(TMP, { recursive: true, force: true }); mkdirSync(path.join(TMP, 'data'), { recursive: true }); mkdirSync(path.join(TMP, 'auth'), { recursive: true }); }

export default defineConfig({
  testDir: './e2e_media',
  testMatch: /.*\.spec\.ts/,
  testIgnore: /staging\//,
  fullyParallel: false,
  workers: 1,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: [['list']],
  globalSetup: './e2e_media/setup.ts',
  use: { channel: 'chrome', headless: true, viewport: { width: 1280, height: 800 }, baseURL: `http://127.0.0.1:${MP.app}`, trace: 'retain-on-failure', screenshot: 'only-on-failure', acceptDownloads: true },
  webServer: [
    { command: `${PY} -m uvicorn e2e_media.fake_engine:app --app-dir ${WEB} --host 127.0.0.1 --port ${MP.engine}`, cwd: BACKEND, url: `http://127.0.0.1:${MP.engine}/__state`, reuseExistingServer: false, env: { PYTHONPATH: `${BACKEND}:${WEB}` } },
    { command: `${PY} -c "from semechka.cli import main; main()" serve --port ${MP.app}`, cwd: BACKEND, url: `http://127.0.0.1:${MP.app}/api/health`, reuseExistingServer: false, timeout: 60_000,
      env: { PYTHONPATH: BACKEND, SEMECHKA_DATA_DIR: path.join(TMP, 'data'), SEMECHKA_HOST: '127.0.0.1', SEMECHKA_WEB_DIR: path.join(WEB, 'build'), PYTHONUNBUFFERED: '1' } }
  ]
});
