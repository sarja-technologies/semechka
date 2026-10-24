<script lang="ts">
  // The signed-in shell: header across the top, conversation sidebar on the left, the page beside it.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount, untrack } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { setCustomCss } from '$lib/theme/theme';
  import { list, loadList, loadFolders, resetCatalogs } from '$lib/chat/store.svelte';
  import AppHeader from '$lib/components/chat/AppHeader.svelte';
  import Sidebar from '$lib/components/chat/Sidebar.svelte';
  import Palette from '$lib/components/Palette.svelte';
  let { children } = $props();

  const path = $derived(page.url.pathname);
  const workspace = $derived<'chat' | 'code'>(path.startsWith('/code') ? 'code' : 'chat');
  const base = $derived(`/${workspace}`);
  const flags = $derived(app.me?.ui.ui ?? {});
  const hasSidebar = $derived(flags.sidebar !== false && !!app.me?.ui.modes?.[workspace]?.enabled);
  const brand = $derived<any>({ ...app.brand, ...(app.me?.ui.brand ?? {}) });

  // collapsed on wide screens: the person's last choice, else the administrator's default
  let collapsed = $state(false);
  $effect(() => { if (app.me) untrack(() => { collapsed = readBool('sem.sidebar') ?? !!app.me.ui.sidebar_collapsed; }); });
  function readBool(k: string): boolean | null { try { const v = localStorage.getItem(k); return v === null ? null : v === '1'; } catch { return null; } }
  function toggle() {
    if (matchMedia('(max-width: 899px)').matches) { list.sidebarOpen = !list.sidebarOpen; return; }
    collapsed = !collapsed;
    try { localStorage.setItem('sem.sidebar', collapsed ? '1' : '0'); } catch { /* storage unavailable */ }
  }

  // the list follows the workspace (chat or code) and the account
  $effect(() => {
    const w = workspace, uid = app.me?.user.id;
    if (!uid || !hasSidebar) return;
    untrack(() => { list.workspace = w; list.q = ''; list.archived = false; loadList(true); if (flags.folders) loadFolders(); });
  });
  $effect(() => { app.me?.user.id; untrack(resetCatalogs); }); // another account sees other models

  // Administrator CSS styles the conversation pages only; account and security pages always look standard.
  $effect(() => { setCustomCss(path.startsWith('/account') ? null : app.me?.ui.theme?.css); });
  $effect(() => () => setCustomCss(null));
  $effect(() => { if (app.me?.mfa_enrolment_required && path !== '/account') goto('/account', { replaceState: true }); });

  const activeId = $derived(path.startsWith(base + '/') ? page.params.id ?? null : null);
  const newChat = () => goto(`${base}?new=${Date.now()}`);
  function deleted(id: string) { if (id === activeId) goto(base); }

  onMount(() => {
    try { const w = localStorage.getItem('sem.sidebar.w'); if (w) document.documentElement.style.setProperty('--sidebar-user-w', w); } catch { /* storage unavailable */ }
    let gkey = false;
    const keys = (e: KeyboardEvent) => {
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.shiftKey && e.key.toLowerCase() === 'o') { e.preventDefault(); newChat(); return; }
      if (mod && e.shiftKey && e.key.toLowerCase() === 's') { e.preventDefault(); toggle(); return; }
      const t = e.target as HTMLElement;
      if (t.matches('input, textarea, select, [contenteditable]') || mod || e.altKey) return;
      if (e.key === '/') { const s = document.querySelector<HTMLElement>('[data-search]'); if (s) { e.preventDefault(); if (collapsed) toggle(); s.focus(); } }
      else if (gkey) { gkey = false; if (e.key === 'c') goto('/chat'); else if (e.key === 'a' && app.me?.admin) goto('/admin'); else if (e.key === 'u') goto('/usage'); }
      else if (e.key === 'g') { gkey = true; setTimeout(() => (gkey = false), 900); }
    };
    addEventListener('keydown', keys);
    return () => removeEventListener('keydown', keys);
  });
</script>

{#if app.me}
  <div class="app">
    <AppHeader sidebar={hasSidebar} {collapsed} ontoggle={toggle} onnew={newChat} title={list.title} />
    <div class="body">
      {#if hasSidebar && !collapsed}<Sidebar {activeId} onnew={newChat} ondeleted={deleted} oncollapse={toggle} />
      {:else if hasSidebar}<div class="mobile-only"><Sidebar {activeId} onnew={newChat} ondeleted={deleted} oncollapse={toggle} /></div>{/if}
      <main id="main" tabindex="-1">{@render children()}</main>
    </div>
    {#if brand.footer_text || brand.powered_by}
      <footer class="tiny muted">
        {#if brand.footer_text}<span>{brand.footer_text}</span>{/if}
        {#if brand.powered_by}<span>Powered by Semechka</span>{/if}
      </footer>
    {/if}
  </div>
{/if}
<Palette />

<style>
  .app { display: flex; flex-direction: column; height: 100dvh; min-height: 0; }
  .body { flex: 1; display: flex; min-height: 0; }
  main { flex: 1; min-width: 0; min-height: 0; overflow: auto; display: flex; flex-direction: column; }
  main:focus { outline: none; }
  footer { flex: none; display: flex; gap: 16px; flex-wrap: wrap; justify-content: center; padding: 4px 16px; border-top: 1px solid var(--border); background: var(--header-bg); }
  .mobile-only { display: none; }
  @media (max-width: 899px) { .mobile-only { display: contents; } }
</style>
