// Command palette registry. Anything can contribute commands by registering a source; the palette itself knows
// nothing about individual modes. A source is called each time the palette opens (synchronous, permission-aware)
// and may also provide an async `search` for results that depend on what was typed.
import { goto } from '$app/navigation';
import { app, can, loadMe, logout } from './session.svelte';
import { patch, qs } from './api';
import { navHref } from './nav';
import { ADMIN_PAGES } from './components/admin/util';
import { toast } from './toast.svelte';

export type Command = { id: string; group: string; label: string; hint?: string; keywords?: string; run: () => void | Promise<void> };
export type Source = {
  id: string;
  commands?: () => Command[];
  /** Async results for a typed query (min 2 characters). Must respect permissions server-side. */
  search?: (q: string, signal: AbortSignal) => Promise<Command[]>;
};

export const palette = $state({ open: false });
const sources: Source[] = [];
export function registerSource(s: Source) { const i = sources.findIndex((x) => x.id === s.id); if (i >= 0) sources[i] = s; else sources.push(s); }
export const openPalette = () => { palette.open = true; };
export const allCommands = () => sources.flatMap((s) => s.commands?.() ?? []);
export const searchers = () => sources.filter((s) => s.search);

const THEMES: [string, string][] = [['light', 'Light'], ['dark', 'Dark'], ['graphite', 'Graphite (dark grey)'], ['custom', 'Custom (your organisation)'], ['system', 'Match device']];

async function setPref(body: Record<string, unknown>) { try { await patch('/api/me', body); await loadMe(); } catch { toast('Could not save that preference.', 'bad'); } }

let settingsCache: { id: string; label: string; category: string }[] | null = null;

registerSource({
  id: 'navigate',
  commands: () => {
    if (!app.me) return [];
    const out: Command[] = [];
    for (const n of app.me.ui.nav as any[]) {
      if (!n.available) continue; // unavailable modes are explained on their own page; they are not offered as actions
      out.push({ id: `go:${n.id}`, group: 'Go to', label: n.label, run: () => goto(navHref(n)) });
    }
    out.push({ id: 'go:account', group: 'Go to', label: 'Account and security', keywords: 'password mfa two-factor sessions', run: () => goto('/account') });
    if (can('admin.overview')) {
      for (const p of ADMIN_PAGES.filter((p) => can(p.perm))) out.push({ id: `go:admin:${p.href}`, group: 'Administration', label: p.label, run: () => goto(p.href) });
    }
    return out;
  }
});

registerSource({
  id: 'actions',
  commands: () => {
    if (!app.me) return [];
    const out: Command[] = [];
    const chat = app.me.ui.modes?.chat?.enabled;
    if (chat) {
      out.push({ id: 'act:new', group: 'Actions', label: 'New chat', keywords: 'start conversation', run: () => goto(`/chat?new=${Date.now()}`) });
      if (app.me.ui.composer?.ephemeral) out.push({ id: 'act:new-nosave', group: 'Actions', label: 'New no-save chat', hint: 'Nothing is stored', keywords: 'private ephemeral temporary', run: () => goto(`/chat?new=${Date.now()}&nosave=1`) });
    }
    if (app.me.ui.theme?.user_choice) for (const [v, label] of THEMES) if (v === 'system' || app.me.ui.theme.choices.includes(v)) out.push({ id: `theme:${v}`, group: 'Appearance', label: `Theme: ${label}`, keywords: 'dark light appearance', run: () => setPref({ theme: v }) });
    for (const v of ['comfortable', 'compact']) out.push({ id: `dens:${v}`, group: 'Appearance', label: `Density: ${v}`, run: () => setPref({ density: v }) });
    if (app.me.ui.tech?.drawer) out.push({ id: 'act:tech', group: 'Actions', label: app.me.user.prefs.show_tech ? 'Hide technical details' : 'Show technical details', run: () => setPref({ show_tech: !app.me.user.prefs.show_tech }) });
    return out;
  }
});

registerSource({
  id: 'models',
  // Models come from the server per person (never invented here), so they are found by searching.
  search: async (q, signal) => {
    if (!app.me?.ui.modes?.chat?.enabled) return [];
    const res = await fetch('/api/chat/models', { credentials: 'same-origin', signal });
    if (!res.ok) return [];
    const models: any[] = await res.json();
    const t = q.toLowerCase();
    return models.filter((m) => `switch model ${m.alias}`.toLowerCase().includes(t) || t.includes('model')).slice(0, 8)
      .map((m) => ({ id: `model:${m.id}`, group: 'Switch model', label: m.alias, hint: m.external ? 'External service' : undefined, run: () => goto(`/chat?model=${encodeURIComponent(m.id)}&new=${Date.now()}`) }));
  }
});

registerSource({
  id: 'conversations',
  search: async (q, signal) => {
    if (!app.me?.ui.modes?.chat?.enabled) return [];
    const res = await fetch(`/api/chat/conversations${qs({ q })}`, { credentials: 'same-origin', signal });
    if (!res.ok) return [];
    return (await res.json() as any[]).slice(0, 8).map((c) => ({ id: `conv:${c.id}`, group: 'Conversations', label: c.title, run: () => goto(`/chat/${c.id}`) }));
  }
});

registerSource({
  id: 'settings',
  search: async (q, signal) => {
    if (!can('admin.settings.view')) return [];
    if (!settingsCache) {
      const res = await fetch('/api/admin/settings', { credentials: 'same-origin', signal });
      if (!res.ok) return [];
      settingsCache = (await res.json()).settings;
    }
    const t = q.toLowerCase();
    return settingsCache!.filter((s) => s.label.toLowerCase().includes(t) || s.id.includes(t)).slice(0, 8)
      .map((s) => ({ id: `set:${s.id}`, group: 'Settings', label: s.label, hint: s.category, run: () => goto(`/admin/settings?q=${encodeURIComponent(s.id)}`) }));
  }
});

registerSource({
  id: 'session',
  commands: () => app.me ? [{ id: 'act:signout', group: 'Account', label: 'Sign out', run: async () => { await logout(); goto('/login'); } }] : []
});

