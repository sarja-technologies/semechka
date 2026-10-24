// Conversation list, folders and the model catalogue, shared by the sidebar, the header and the conversation view.
import { get, qs, ApiError } from '$lib/api';

export type ConvRow = { id: string; title: string; updated_at: number; created_at?: number; model_id: string | null;
  expires_at: number | null; pinned: boolean; folder_id: string | null; mode: string; archived?: boolean };
export type Folder = { id: string; name: string; sort: number };
export type Operation = 'text' | 'code' | 'vision' | 'image' | 'video';

const PAGE = 60;

export const list = $state({
  workspace: 'chat' as 'chat' | 'code',
  items: [] as ConvRow[],
  hasMore: false,
  loading: false,
  error: '',
  q: '',
  archived: false,
  folders: [] as Folder[],
  sidebarOpen: false, // the sheet on narrow screens
  title: '' // the open conversation's name, shown in the header
});

let seq = 0;
export async function loadList(reset = true) {
  const mine = ++seq;
  list.loading = true;
  try {
    const before = reset ? undefined : list.items.filter((c) => !c.pinned).at(-1)?.updated_at;
    const rows: ConvRow[] = await get(`/api/chat/conversations${qs({ mode: list.workspace === 'code' ? 'code' : undefined, q: list.q || undefined, archived: list.archived || undefined, limit: list.q ? undefined : PAGE, before })}`);
    if (mine !== seq) return;
    list.items = reset ? rows : [...list.items, ...rows.filter((r) => !list.items.some((x) => x.id === r.id))];
    list.hasMore = !list.q && rows.length >= PAGE;
    list.error = '';
  } catch (e) {
    if (mine === seq) list.error = e instanceof ApiError ? e.message : 'Could not load conversations.';
  } finally {
    if (mine === seq) list.loading = false;
  }
}

export function upsert(row: Partial<ConvRow> & { id: string }) {
  const i = list.items.findIndex((c) => c.id === row.id);
  if (i >= 0) list.items[i] = { ...list.items[i], ...row };
  else list.items = [{ title: 'New conversation', updated_at: Date.now() / 1000, model_id: null, expires_at: null, pinned: false, folder_id: null, mode: list.workspace, ...row } as ConvRow, ...list.items];
}

export function touch(id: string, title?: string) {
  const i = list.items.findIndex((c) => c.id === id);
  if (i < 0) return;
  const row = { ...list.items[i], updated_at: Date.now() / 1000, ...(title ? { title } : {}) };
  list.items = [row, ...list.items.slice(0, i), ...list.items.slice(i + 1)];
}

export function drop(id: string) { list.items = list.items.filter((c) => c.id !== id); }

export async function loadFolders() {
  try { list.folders = await get('/api/chat/folders'); } catch { list.folders = []; }
}

// ---- model catalogue per kind of turn (cached for the session; refreshed on demand) --------------------------------
const catalogs = $state<Record<string, any>>({});
const inflight: Record<string, Promise<any> | undefined> = {};

export function catalog(op: Operation) { return catalogs[op]; }

export async function loadCatalog(op: Operation, force = false): Promise<any> {
  if (!force && catalogs[op]) return catalogs[op];
  if (!force && inflight[op]) return inflight[op];
  inflight[op] = get(`/api/models/catalog?operation=${op}`)
    .then((c) => { catalogs[op] = c; return c; })
    .catch((e) => { catalogs[op] = { error: e instanceof ApiError ? e.message : 'Could not load models.', models: [], groups: [], ungrouped: [], pinned: [] }; return catalogs[op]; })
    .finally(() => { inflight[op] = undefined; });
  return inflight[op];
}

export function resetCatalogs() { for (const k of Object.keys(catalogs)) delete catalogs[k]; }

/** Date buckets for the sidebar, newest first. */
export function dateGroups(rows: ConvRow[]): [string, ConvRow[]][] {
  const now = new Date(); const d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
  const g: [string, ConvRow[]][] = [['Today', []], ['Yesterday', []], ['Previous 7 days', []], ['Previous 30 days', []], ['Older', []]];
  for (const c of rows) {
    const t = c.updated_at;
    (t >= d0 ? g[0] : t >= d0 - 86400 ? g[1] : t >= d0 - 7 * 86400 ? g[2] : t >= d0 - 30 * 86400 ? g[3] : g[4])[1].push(c);
  }
  return g.filter(([, l]) => l.length);
}
