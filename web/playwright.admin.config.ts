import { cpSync, existsSync, rmSync } from 'node:fs';
import path from 'node:path';

// Browser test of the administrator explorer (Conversations & media) on its own: the media harness (real server, engine double, real disk) on its own
// ports and scratch folder, serving a private copy of the web build so it can run next to other suites:
//   cd web && npx vite build && npx playwright test -c playwright.admin.config.ts
process.env.MEDIA_E2E_ENGINE_PORT ||= '8851';
process.env.MEDIA_E2E_APP_PORT ||= '8852';
process.env.MEDIA_E2E_TMP ||= '.e2e-admin-tmp';
const base = (await import('./playwright.media.config')).default;
const WEB = path.resolve(import.meta.dirname);
const BUILD = path.join(WEB, '.e2e-admin-build');
if (process.env.TEST_WORKER_INDEX === undefined) {
  if (!existsSync(path.join(WEB, 'build', 'index.html'))) throw new Error('Run `npx vite build` first.');
  rmSync(BUILD, { recursive: true, force: true });
  cpSync(path.join(WEB, 'build'), BUILD, { recursive: true });
}
export default {
  ...base,
  testMatch: /admin-explorer\.spec\.ts/,
  use: { ...base.use, actionTimeout: 15_000, navigationTimeout: 30_000 },
  webServer: (base.webServer as any[]).map((w) => (w.env?.SEMECHKA_WEB_DIR ? { ...w, env: { ...w.env, SEMECHKA_WEB_DIR: BUILD } } : w))
};
