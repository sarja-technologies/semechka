import { existsSync, rmSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const WEB = path.resolve(HERE, '../..');
export const ROOT = path.resolve(WEB, '..');
export const BACKEND = path.join(ROOT, 'backend');
export const TMP = path.join(WEB, '.e2e-tmp');

// A worktree has no virtualenv of its own; fall back to the one in the main checkout.
function findPython(): string {
  const cands = [process.env.SEMECHKA_PY, path.join(ROOT, '.venv/bin/python'), path.resolve(ROOT, '../../../.venv/bin/python'), path.resolve(ROOT, '../../.venv/bin/python')];
  const hit = cands.find((c) => c && existsSync(c));
  if (!hit) throw new Error('No Python virtualenv found. Set SEMECHKA_PY to a python with the backend dependencies installed.');
  return hit;
}
export const PY = findPython();

export const PORTS = { fake: 8829, fresh: 8821, seeded: 8822 } as const;
export const URLS = {
  fake: `http://127.0.0.1:${PORTS.fake}/v1`,
  fresh: `http://127.0.0.1:${PORTS.fresh}`,
  seeded: `http://127.0.0.1:${PORTS.seeded}`
};
export const DIRS = { fresh: path.join(TMP, 'fresh'), seeded: path.join(TMP, 'seeded'), auth: path.join(TMP, 'auth') };

/** Only the Playwright parent process resets state; workers load this module too and must not. */
export function resetState() {
  if (process.env.TEST_WORKER_INDEX !== undefined) return;
  rmSync(TMP, { recursive: true, force: true });
  for (const d of Object.values(DIRS)) mkdirSync(d, { recursive: true });
}

export const PW = 'correct horse battery staple';
export const USERS = {
  owner: { email: 'owner@e2e.test', name: 'Olga Owner', password: PW },
  member: { email: 'member@e2e.test', name: 'Max Member', password: PW },
  tech: { email: 'tech@e2e.test', name: 'Tess Technical', password: PW },
  admin: { email: 'admin@e2e.test', name: 'Ada Admin', password: PW },
  guest: { email: 'guest@e2e.test', name: 'Gus Guest', password: PW }
};
