// Small pure formatters and status mapping shared by the Work Mode screens.
import type { ContainerState, SessionStatus, JobStatus, Autonomy, NetworkMode } from './types';

export type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'plain';

export function formatBytes(n: number | null | undefined): string {
  if (n === null || n === undefined || !Number.isFinite(n) || n < 0) return '';
  if (n < 1024) return `${Math.round(n)} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return `${v >= 100 || Number.isInteger(v) ? Math.round(v) : v.toFixed(1)} ${units[i]}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) return '';
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  if (m < 60) return r ? `${m} min ${r} s` : `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

/** "just now", "5 min ago", "3 h ago", "2 d ago", else the date. `now` is injectable for tests. */
export function relativeTime(iso: string | undefined, now = Date.now()): string {
  if (!iso) return '';
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 45) return 'just now';
  if (s < 3600) return `${Math.max(1, Math.round(s / 60))} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  if (s < 7 * 86400) return `${Math.round(s / 86400)} d ago`;
  return new Date(t).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
}

export const SESSION_STATUS: Record<SessionStatus, { label: string; tone: Tone; busy: boolean }> = {
  idle: { label: 'Ready', tone: 'plain', busy: false },
  running: { label: 'Working', tone: 'info', busy: true },
  awaiting_approval: { label: 'Needs your approval', tone: 'warn', busy: true },
  paused: { label: 'Paused', tone: 'warn', busy: false },
  failed: { label: 'Failed', tone: 'bad', busy: false },
  cancelled: { label: 'Cancelled', tone: 'plain', busy: false }
};
export const sessionStatus = (s: string) => SESSION_STATUS[s as SessionStatus] ?? { label: s || 'Unknown', tone: 'plain' as Tone, busy: false };

export const CONTAINER_STATE: Record<ContainerState, { label: string; tone: Tone }> = {
  absent: { label: 'No container yet', tone: 'plain' },
  stopped: { label: 'Container stopped', tone: 'plain' },
  running: { label: 'Container running', tone: 'ok' },
  error: { label: 'Container error', tone: 'bad' }
};
export const containerState = (s: string) => CONTAINER_STATE[s as ContainerState] ?? { label: s || 'Unknown', tone: 'plain' as Tone };

export const JOB_STATUS: Record<JobStatus, { label: string; tone: Tone; active: boolean }> = {
  queued: { label: 'Waiting in the engine queue', tone: 'plain', active: true },
  running: { label: 'Running', tone: 'info', active: true },
  completed: { label: 'Finished', tone: 'ok', active: false },
  failed: { label: 'Failed', tone: 'bad', active: false },
  cancelled: { label: 'Cancelled', tone: 'plain', active: false }
};
export const jobStatus = (s: string) => JOB_STATUS[s as JobStatus] ?? { label: s || 'Unknown', tone: 'plain' as Tone, active: false };

export const networkLabel = (n: NetworkMode | string | undefined) => (n === 'internet' ? 'Internet access' : 'No network');
export const autonomyLabel = (a: Autonomy | string | undefined) => (a === 'ask' ? 'Asks before each action' : 'Works on its own');

/** A progress value the engine reported, as a whole percent; null when it did not report one (never guessed). */
export function percent(p: number | null | undefined): number | null {
  if (typeof p !== 'number' || !Number.isFinite(p)) return null;
  return Math.round(Math.min(1, Math.max(0, p)) * 100);
}

/** Clip long text for display without cutting the data: returns the shown part and how much was left out. */
export function clip(text: string, maxChars: number, maxLines = 400): { shown: string; hidden: number } {
  let shown = text;
  const lines = shown.split('\n');
  if (lines.length > maxLines) shown = lines.slice(0, maxLines).join('\n');
  if (shown.length > maxChars) shown = shown.slice(0, maxChars);
  return { shown, hidden: text.length - shown.length };
}

const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|jsonl|ya?ml|toml|ini|cfg|conf|log|py|js|mjs|ts|tsx|jsx|sh|bash|html?|css|xml|svg|sql|rs|go|c|h|cpp|java|rb|php|tex|r)$/i;
export function isTextFile(name: string, mime?: string): boolean {
  if (mime) { if (mime.startsWith('text/')) return true; if (/json|xml|yaml|javascript|x-sh|csv/.test(mime)) return true; }
  return TEXT_EXT.test(name);
}
export const isImage = (name: string, mime?: string) => (mime ? mime.startsWith('image/') && mime !== 'image/svg+xml' : /\.(png|jpe?g|gif|webp|bmp|avif)$/i.test(name));
export const isVideo = (name: string, mime?: string) => (mime ? mime.startsWith('video/') : /\.(mp4|webm|mov|m4v)$/i.test(name));
