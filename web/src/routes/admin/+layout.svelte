<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { app, can } from '$lib/session.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Wordmark from '$lib/components/Wordmark.svelte';
  import Palette from '$lib/components/Palette.svelte';
  import { openPalette } from '$lib/palette.svelte';
  import { ADMIN_PAGES } from '$lib/components/admin/util';
  let { children } = $props();
  let menu = $state(false);
  const items = $derived(ADMIN_PAGES.filter((p) => can(p.perm)));
  $effect(() => { if (app.ready && app.me === null) goto(`/login?next=${encodeURIComponent(page.url.pathname)}`); else if (app.ready && app.me && !can('admin.overview')) goto('/'); });
  $effect(() => { page.url.pathname; menu = false; });
  const active = (p: any) => (p.exact ? page.url.pathname === p.href : page.url.pathname.startsWith(p.href));
  const mfaBlock = $derived(app.me?.mfa_enrolment_required);
</script>
{#if app.me && can('admin.overview')}
<div class="shell">
  <header class="top">
    <button class="btn ghost icon" aria-label="Admin sections" aria-expanded={menu} aria-controls="anav" onclick={() => (menu = !menu)}><Icon name="menu" /></button>
    <a href="/admin" class="brand"><Wordmark size={16} /> <span class="tag">Admin</span></a>
    <span class="spacer"></span>
    <button class="btn sm" onclick={openPalette} aria-label="Open command palette" aria-keyshortcuts="Control+K Meta+K"><Icon name="search" size={14} /> <span class="hide-s">Search</span> <kbd>Ctrl K</kbd></button>
    <a class="btn sm" href="/">Back to app</a>
  </header>
  <nav id="anav" class:open={menu} aria-label="Admin sections">
    {#each items as p}<a href={p.href} class:on={active(p)} aria-current={active(p) ? 'page' : undefined}><Icon name={p.icon} size={16} />{p.label}</a>{/each}
  </nav>
  <main id="main" tabindex="-1">
    {#if mfaBlock}<div class="notice warn" role="alert"><div>Your organisation requires multi-factor authentication for administrators. Admin features stay closed until you <a href="/account">set it up in your account</a>.</div></div>{/if}
    {@render children()}
  </main>
</div>
<Palette />
{/if}
<style>
  .shell { display: grid; grid-template-columns: 248px minmax(0, 1fr); grid-template-rows: 52px 1fr; min-height: 100vh; }
  .top { grid-column: 1 / -1; display: flex; align-items: center; gap: 12px; padding: 0 16px; border-bottom: 1px solid var(--border); background: var(--surface); position: sticky; top: 0; z-index: 20; }
  .top .btn.icon { display: none; }
  .brand { display: flex; align-items: center; gap: 8px; color: var(--fg); text-decoration: none; }
  .tag { font: 600 11px var(--mono); border: 1px solid var(--border-strong); border-radius: 4px; padding: 1px 6px; text-transform: uppercase; letter-spacing: .06em; }
  nav { border-right: 1px solid var(--border); padding: 12px 8px; display: grid; align-content: start; gap: 2px; background: var(--surface); position: sticky; top: 52px; height: calc(100vh - 52px); overflow: auto; }
  nav a { display: flex; align-items: center; gap: 10px; padding: 0 10px; min-height: 36px; border-radius: var(--radius-sm); color: var(--fg); text-decoration: none; font-size: 14px; }
  nav a:hover { background: var(--surface-2); }
  nav a.on { background: var(--accent-soft); color: var(--fg); box-shadow: inset 3px 0 0 var(--accent); font-weight: 600; }
  main { padding: 24px 28px 64px; min-width: 0; max-width: 1200px; }
  @media (max-width: 860px) {
    .shell { grid-template-columns: minmax(0, 1fr); }
    .top .btn.icon { display: inline-flex; } .hide-s, .top kbd { display: none; }
    nav { display: none; position: fixed; inset: 52px 0 0 0; z-index: 30; height: auto; border: 0; } nav.open { display: grid; }
    nav a { min-height: 44px; }
    main { padding: 16px 16px 64px; }
  }
</style>
