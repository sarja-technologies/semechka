// Calls of the administrator explorer. Each one first obtains the stated reason (see reason.svelte.ts) and sends nothing when the person cancels.
import { get, post } from '$lib/api';
import { askReason } from './reason.svelte';
import { toApiFilters, type Filters } from './explorer';

export const EX = '/api/admin/content/explorer';
export type Meta = {
  scope: 'instance' | 'organisation'; users: { id: string; label: string }[]; models: { id: string; alias: string; kind: string }[];
  workflows: string[]; statuses: string[]; limits: { conversations: number; media: number; scan: number };
};
export const loadMeta = () => get<Meta>(`${EX}/filters`);

const LIST_WHY = 'Listing records one access in the audit log. The people whose items appear on the page are told.';
export async function listConversations(f: Filters, cursor: string | null, limit = 30) {
  const reason = await askReason('browse', 'list', 'Search conversations', LIST_WHY);
  return reason ? post(`${EX}/conversations`, { reason, filters: toApiFilters(f), cursor, limit }) : null;
}
export async function listMedia(f: Filters, cursor: string | null, limit = 30) {
  const reason = await askReason('browse', 'list', 'Search the media library', LIST_WHY);
  return reason ? post(`${EX}/media`, { reason, filters: toApiFilters(f), cursor, limit }) : null;
}
export async function readConversation(id: string, title: string) {
  const reason = await askReason('item', `conversation:${id}`, 'Open this conversation', `Opening “${title || 'Untitled'}” records an access and tells its owner.`);
  return reason ? post(`/api/admin/content/conversations/${id}`, { reason }) : null;
}
export async function readJob(id: string) {
  const reason = await askReason('item', `job:${id}`, 'Open this generation', 'Opening a generation shows its prompt, settings and files, and records an access that its owner can see.');
  return reason ? post(`${EX}/media/jobs/${id}`, { reason }) : null;
}
export async function readAsset(id: string) {
  const reason = await askReason('item', `asset:${id}`, 'Open this item', 'Opening an item records an access that its owner can see.');
  return reason ? post(`${EX}/media/assets/${id}`, { reason }) : null;
}
/** A short-lived link for one stored file. The reason is the one already given for the item this file belongs to. */
export async function fileLink(type: 'asset' | 'attachment', id: string, mode: 'view' | 'download') {
  const reason = await askReason('browse', 'list', mode === 'download' ? 'Download this file' : 'Preview this file', 'Each preview and download is recorded separately.');
  return reason ? post<{ url: string; filename: string | null; mime: string; size: number }>(`${EX}/files/access`, { reason, type, id, mode }) : null;
}
