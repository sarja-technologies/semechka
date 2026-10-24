// One place that maps a navigation entry (from /api/me ui.nav) to its route, shared by the rail, the mobile bar
// and the command palette so a newly added mode needs a single change.
export type NavEntry = { id: string; label: string; available: boolean; reason?: string };

const ROUTES: Record<string, string> = { chat: '/chat', usage: '/usage', help: '/help' };

/** Route for a mode. Modes with their own page register it here; everything else lands on the explanatory page. */
export function registerRoute(id: string, href: string) { ROUTES[id] = href; }

export const navHref = (n: NavEntry) => (n.available ? (ROUTES[n.id] ?? `/unavailable/${n.id}`) : `/unavailable/${n.id}`);
