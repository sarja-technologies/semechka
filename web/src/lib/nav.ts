// One place that decides what the shell offers. The primary navigation is exactly four places: Chat, Images, Videos and
// Experimental. Everything else (Library, knowledge, assistants, usage, help, account, administration) is secondary and
// reachable from the account menu and the command palette. What is offered always comes from /api/me; the server checks
// every request again, so hiding an entry is a courtesy, never the guard.
export type NavEntry = { id: string; label: string; available: boolean; reason?: string };
export type PrimaryId = 'chat' | 'image' | 'video' | 'experimental';
export type PrimaryEntry = { id: PrimaryId; label: string; href: string; icon: string; match: (path: string) => boolean };

const starts = (...p: string[]) => (path: string) => p.some((x) => path === x || path.startsWith(x + '/'));

const ALL: PrimaryEntry[] = [
  { id: 'chat', label: 'Chat', href: '/chat', icon: 'chat', match: starts('/chat') },
  { id: 'image', label: 'Images', href: '/image', icon: 'image', match: starts('/image') },
  { id: 'video', label: 'Videos', href: '/video', icon: 'video', match: starts('/video') },
  // the standalone Imajutsu and OpenJutsu pages are only reachable from Experimental, so they keep its tab lit
  { id: 'experimental', label: 'Experimental', href: '/experimental', icon: 'sliders', match: starts('/experimental', '/imajutsu', '/openjutsu') }
];

/** True when the server offers any experimental tool: advanced generation, the picture studio, the video editor, or analysing a video. */
export function experimentalAvailable(ui: any): boolean {
  const o = ui?.operations ?? {};
  return !!(o.image?.enabled || o.video?.enabled || o.imajutsu?.enabled || o.openjutsu?.enabled);
}

/** The four primary places this person may use. A place the server does not offer is left out entirely (no dead link). */
export function primaryNav(ui: any): PrimaryEntry[] {
  if (!ui) return [];
  const o = ui.operations ?? {};
  const on: Record<PrimaryId, boolean> = {
    chat: !!ui.modes?.chat?.enabled,
    image: !!o.image?.enabled,
    video: !!o.video?.enabled,
    experimental: experimentalAvailable(ui)
  };
  return ALL.filter((e) => on[e.id]);
}

/** Which primary place a path belongs to (the active tab), or null on account, administration and the secondary pages. */
export const primaryOf = (path: string): PrimaryId | null => ALL.find((e) => e.match(path))?.id ?? null;

// ---- secondary pages (account menu) -------------------------------------------------------------------------------------
const SECONDARY: Record<string, { href: string; icon: string }> = {
  library: { href: '/library', icon: 'library' },
  knowledge: { href: '/knowledge', icon: 'knowledge' },
  agents: { href: '/agents', icon: 'agents' },
  usage: { href: '/usage', icon: 'usage' },
  help: { href: '/help', icon: 'help' }
};

/** Library, knowledge, assistants, usage and help, when the server offers them. Workspaces, Code and the old Work page are not offered. */
export function secondaryNav(ui: any): { id: string; label: string; href: string; icon: string }[] {
  const pages: NavEntry[] = ui?.pages ?? ui?.nav ?? [];
  return pages.filter((p) => p.available && SECONDARY[p.id]).map((p) => ({ id: p.id, label: p.label, ...SECONDARY[p.id] }));
}

/** Where `home` from /api/me lands: the chat unless it is not offered, then the first primary place. */
export function homeHref(ui: any): string {
  const h = ui?.home;
  const prim = primaryNav(ui);
  if (h && SECONDARY[h]) return SECONDARY[h].href;
  return prim.find((p) => p.id === 'chat')?.href ?? prim[0]?.href ?? '/account';
}
