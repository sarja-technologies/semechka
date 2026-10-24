<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { app, loadMe, logout } from '$lib/session.svelte';
  import { patch } from '$lib/api';
  import Icon from '$lib/components/Icon.svelte';
  import Wordmark from '$lib/components/Wordmark.svelte';
  let { children } = $props();
  let collapsed = $state(false), menu = $state(false), gkey = false;
  const nav = $derived<any[]>(app.me?.ui.nav ?? []);
  const path = $derived(page.url.pathname);
  const href = (n: any) => (n.available ? (n.id === 'usage' ? '/usage' : n.id === 'help' ? '/help' : n.id === 'chat' ? '/chat' : n.id === 'image' || n.id === 'video' ? `/${n.id}` : `/unavailable/${n.id}`) : `/unavailable/${n.id}`);
  const active = (n: any) => path.startsWith(href(n)) || (n.id === 'chat' && path.startsWith('/chat'));
  const brand = $derived(app.brand);
  $effect(() => { if (app.me) collapsed = localStorageGet() ?? app.me.ui.sidebar_collapsed; });
  $effect(() => { if (app.me?.mfa_enrolment_required && path !== '/account') goto('/account', { replaceState: true }); });
  function localStorageGet(): boolean | null { try { const v = localStorage.getItem('sem.rail'); return v === null ? null : v === '1'; } catch { return null; } }
  function toggleRail() { collapsed = !collapsed; try { localStorage.setItem('sem.rail', collapsed ? '1' : '0'); } catch { /* storage unavailable */ } }
  async function setTheme(theme: string) { await patch('/api/me', { theme }); await loadMe(); }
  async function setDensity(density: string) { await patch('/api/me', { density }); await loadMe(); }
  async function signOut() { await logout(); goto('/login'); }
  function keys(e: KeyboardEvent) {
    const t = e.target as HTMLElement; if (t.matches('input, textarea, select, [contenteditable]') || e.ctrlKey || e.metaKey || e.altKey) return;
    if (e.key === '/') { const s = document.querySelector<HTMLElement>('[data-search]'); if (s) { e.preventDefault(); s.focus(); } }
    else if (gkey) { gkey = false; if (e.key === 'c') goto('/chat'); else if (e.key === 'a' && app.me?.admin) goto('/admin'); else if (e.key === 'u') goto('/usage'); }
    else if (e.key === 'g') { gkey = true; setTimeout(() => (gkey = false), 900); }
  }
  onMount(() => { addEventListener('keydown', keys); return () => removeEventListener('keydown', keys); });
</script>
{#if app.me}
<div class="shell" class:collapsed>
  <aside class="rail" aria-label="Primary">
    <div class="top"><a href="/" class="wm" aria-label="Home"><Wordmark size={17} /></a>
      <button class="btn ghost icon sm collapse" onclick={toggleRail} aria-label={collapsed ? 'Expand navigation' : 'Collapse navigation'} aria-expanded={!collapsed}><Icon name="menu" /></button></div>
    <nav>
      {#each nav as n}
        <a href={href(n)} class="item" class:on={active(n)} class:dim={!n.available} aria-current={active(n) ? 'page' : undefined} title={n.available ? n.label : `${n.label}: ${n.reason}`}>
          <Icon name={n.id} /><span class="lbl">{n.label}</span>{#if !n.available}<span class="chip lbl tiny">Unavailable</span>{/if}
        </a>
      {/each}
    </nav>
    <div class="bottom">
      {#if app.me.admin}<a href="/admin" class="item" class:on={path.startsWith('/admin')} title="Administration"><Icon name="settings" /><span class="lbl">Administration</span></a>{/if}
      <div class="usr">
        <button class="item" onclick={() => (menu = !menu)} aria-expanded={menu} aria-haspopup="menu"><Icon name="user" /><span class="lbl grow ellip">{app.me.user.display_name}</span></button>
        {#if menu}
          <div class="menu fade" role="menu" tabindex="-1" onkeydown={(e) => e.key === 'Escape' && (menu = false)}>
            <div class="small muted" style="padding:6px 10px">{app.me.user.email}<br />{app.me.roles.map((r: any) => r.label).join(', ')}</div>
            <hr style="margin:4px 0" />
            <label class="mrow small" for="th">Theme</label>
            <select id="th" class="input" value={app.me.user.prefs.theme ?? 'default'} onchange={(e) => setTheme(e.currentTarget.value === 'default' ? app.brand.theme_default || 'paper' : e.currentTarget.value)}>
              <option value="paper">Paper</option><option value="carbon">Carbon</option><option value="graphite">Graphite</option><option value="system">Match system</option>
            </select>
            <label class="mrow small" for="de">Density</label>
            <select id="de" class="input" value={app.me.user.prefs.density ?? app.me.ui.density} onchange={(e) => setDensity(e.currentTarget.value)}>
              <option value="comfortable">Comfortable</option><option value="compact">Compact</option>
            </select>
            <hr style="margin:8px 0" />
            <a class="btn ghost sm" style="width:100%;justify-content:flex-start" href="/account" onclick={() => (menu = false)}>Account and security</a>
            <button class="btn ghost sm" style="width:100%;justify-content:flex-start" onclick={signOut}>Sign out</button>
          </div>
        {/if}
      </div>
    </div>
  </aside>
  <div class="main">
    <main id="main" tabindex="-1">{@render children()}</main>
    {#if brand.footer_text || brand.support_url || brand.powered_by}
      <footer class="small muted">
        {#if brand.footer_text}<span>{brand.footer_text}</span>{/if}
        {#if brand.support_url}<a href={brand.support_url} target="_blank" rel="noopener noreferrer">Help and support</a>{/if}
        {#if brand.powered_by}<span>Powered by Semechka</span>{/if}
      </footer>
    {/if}
  </div>
  <nav class="bar" aria-label="Primary (mobile)">
    {#each nav.slice(0, 5) as n}<a href={href(n)} class:on={active(n)} class:dim={!n.available} aria-current={active(n) ? 'page' : undefined}><Icon name={n.id} /><span>{n.label}</span></a>{/each}
    <a href="/account" class:on={path.startsWith('/account')}><Icon name="user" /><span>Account</span></a>
  </nav>
</div>
{/if}
<style>
  .shell { display: grid; grid-template-columns: var(--rail) 1fr; height: 100dvh; }
  .shell.collapsed { grid-template-columns: 56px 1fr; }
  .rail { background: var(--surface); border-right: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; padding: 8px; }
  .top { display: flex; align-items: center; justify-content: space-between; padding: 6px 4px 12px; gap: 4px; }
  .wm { text-decoration: none; color: var(--fg); overflow: hidden; }
  nav { display: grid; gap: 2px; align-content: start; flex: 1; overflow: auto; }
  .item { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 10px; padding: 0 10px; min-height: 38px; border-radius: var(--radius-sm); color: var(--fg); cursor: pointer; text-decoration: none; width: 100%; font-size: 14px; }
  .item:hover { background: var(--surface-2); } .item:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  .item.on { background: var(--accent-soft); color: var(--accent); font-weight: 600; }
  .item.dim { color: var(--fg-muted); }
  .ellip { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .bottom { display: grid; gap: 2px; position: relative; }
  .usr { position: relative; }
  .menu { position: absolute; bottom: 44px; left: 0; width: 240px; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); padding: 8px; z-index: 30; }
  .mrow { display: block; margin: 6px 0 2px; }
  .collapsed .lbl, .collapsed .wm { display: none; } .collapsed .top { justify-content: center; } .collapsed .menu { left: 56px; bottom: 0; }
  .main { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  main { flex: 1; min-height: 0; overflow: auto; }
  main:focus { outline: none; }
  footer { display: flex; gap: 16px; flex-wrap: wrap; padding: 8px 16px; border-top: 1px solid var(--border); background: var(--surface); }
  .bar { display: none; }
  @media (max-width: 719px) {
    .shell, .shell.collapsed { grid-template-columns: 1fr; grid-template-rows: 1fr auto; }
    .rail { display: none; }
    .bar { display: flex; border-top: 1px solid var(--border); background: var(--surface); }
    .bar a { flex: 1; display: grid; justify-items: center; gap: 2px; padding: 6px 2px; font-size: 11px; color: var(--fg-muted); text-decoration: none; min-height: 52px; align-content: center; }
    .bar a.on { color: var(--accent); font-weight: 600; }
  }
</style>
