<script lang="ts">
  // The four main places as a bar at the foot of a phone-sized screen (the header carries them on wider screens).
  import { page } from '$app/state';
  import { app } from '$lib/session.svelte';
  import { primaryNav, primaryOf } from '$lib/nav';
  import Icon from '$lib/components/Icon.svelte';
  const tabs = $derived(primaryNav(app.me?.ui));
  const active = $derived(primaryOf(page.url.pathname));
</script>

{#if tabs.length > 1}
  <nav class="bnav" aria-label="Main">
    {#each tabs as t (t.id)}
      <a href={t.href} class:on={active === t.id} aria-current={active === t.id ? 'page' : undefined} data-nav={t.id}><Icon name={t.icon} size={20} /><span>{t.label}</span></a>
    {/each}
  </nav>
{/if}

<style>
  .bnav { display: none; }
  @media (max-width: 899px) {
    .bnav { flex: none; display: grid; grid-auto-flow: column; grid-auto-columns: 1fr; background: var(--header-bg); border-top: 1px solid var(--border); padding-bottom: env(safe-area-inset-bottom); }
    a { display: grid; justify-items: center; align-content: center; gap: 2px; min-height: 56px; padding: 6px 2px; color: var(--fg-muted); text-decoration: none; font-size: 12px; font-weight: 500; }
    a.on { color: var(--fg); }
    a.on :global(svg) { color: var(--accent); }
    a:focus-visible { outline: 2px solid var(--accent); outline-offset: -3px; }
  }
</style>
