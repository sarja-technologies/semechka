// Small helpers shared by the Workspaces and Organisation screens.
import { ApiError } from '$lib/api';

export const msg = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened. Try again.');

export function bytes(n: number | null | undefined): string {
  if (n === null || n === undefined) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(n < 10240 ? 1 : 0)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(n < 10485760 ? 1 : 0)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}

export function ago(ts: number | null | undefined): string {
  if (!ts) return '';
  const s = Math.max(0, Date.now() / 1000 - ts);
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)} d ago`;
  return new Date(ts * 1000).toLocaleDateString();
}

export function duration(a: number | null | undefined, b: number | null | undefined): string {
  if (!a) return '';
  const s = Math.max(0, Math.round((b ?? Date.now() / 1000) - a));
  return s < 60 ? `${s} s` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** Chip class for a job or run state. */
export const stateChip = (s: string) =>
  ({ succeeded: 'ok', running: 'info', queued: '', awaiting_approval: 'warn', failed: 'bad', cancelled: '', done: 'ok', error: 'bad', denied: 'bad', interrupted: 'warn' })[s] ?? '';

export const stateLabel = (s: string) => ({ awaiting_approval: 'Waiting for approval', succeeded: 'Done', queued: 'Queued', running: 'Running', failed: 'Failed', cancelled: 'Cancelled' } as Record<string, string>)[s] ?? s;

export const indexChip = (s: string) => ({ ok: ['ok', 'Searchable'], pending: ['', 'Indexing'], unsupported: ['', 'Not searchable'], failed: ['bad', 'Could not read'] } as Record<string, [string, string]>)[s] ?? ['', s];

export const fileIcon = (kind: string) => (kind === 'image' ? 'image' : kind === 'video' ? 'video' : kind === 'audio' ? 'audio' : kind === 'code' ? 'code' : 'log');

/** Plain-language summary of what the signed-in person can do in a workspace, from its permission list. */
export function accessLevel(perms: string[] = []): string {
  const has = (p: string) => perms.includes(p);
  if (has('ws.members.manage')) return 'Administrator';
  if (has('file.delete')) return 'Editor';
  if (has('file.write')) return 'Contributor';
  if (has('file.read')) return 'Viewer';
  return 'Listed only';
}

export const PERM_GROUPS = ['Organisation', 'Workspace', 'Content', 'Agents', 'Jobs'];
