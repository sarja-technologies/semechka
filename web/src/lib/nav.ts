// One place that maps a navigation entry (from /api/me ui.pages) to its route, shared by the rail, the mobile bar
// and the command palette so a newly added mode needs a single change.
export type NavEntry = { id: string; label: string; available: boolean; reason?: string };

const ROUTES: Record<string, string> = { chat: '/chat', usage: '/usage', help: '/help', image: '/image', video: '/video', code: '/code', knowledge: '/knowledge', agents: '/agents', library: '/library', workspaces: '/workspaces' };

/** Route for a mode. Modes with their own page register it here; everything else lands on the explanatory page. */
export function registerRoute(id: string, href: string) { ROUTES[id] = href; }

export const navHref = (n: NavEntry) => (n.available ? (ROUTES[n.id] ?? `/unavailable/${n.id}`) : `/unavailable/${n.id}`);

/** Trinodal AI: its management UI is served by Semechka at /trinodal/ (a separate page, so these are full page loads). Training
 *  links are offered only to people who hold trinodal.train; the server checks every request again. */
export type TrinodalLink = { id: string; label: string; href: string; train?: boolean };
export const TRINODAL_LINKS: TrinodalLink[] = [
  { id: 'dashboard', label: 'Dashboard', href: '/trinodal/' },
  { id: 'network', label: 'Network Explorer', href: '/trinodal/#/network' },
  { id: 'training', label: 'Training Studio', href: '/trinodal/#/training', train: true },
  { id: 'jobs', label: 'Training Jobs', href: '/trinodal/#/training/jobs', train: true },
  { id: 'evaluations', label: 'Evaluations', href: '/trinodal/#/evaluations' }
];
export const trinodalLinks = (t: { view: boolean; train: boolean }) => (t.view ? TRINODAL_LINKS.filter((l) => !l.train || t.train) : []);
