<script lang="ts">
  import '../app.css';
  import { onMount } from 'svelte';
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { app, loadPublic, loadMe } from '$lib/session.svelte';
  import Toasts from '$lib/components/Toasts.svelte';
  let { children } = $props();
  const PUBLIC = ['/login', '/signup', '/forgot', '/reset', '/verify', '/setup'];

  async function boot() {
    await loadPublic();
    if (app.needsSetup) { app.ready = true; if (page.url.pathname !== '/setup') goto('/setup', { replaceState: true }); return; }
    const ok = await loadMe();
    app.ready = true;
    const path = page.url.pathname;
    if (!ok && !app.offline && !PUBLIC.some((p) => path.startsWith(p))) goto(`/login?next=${encodeURIComponent(path + page.url.search)}`, { replaceState: true });
    if (ok && (path === '/login' || path === '/signup' || path === '/setup')) goto('/', { replaceState: true });
  }
  onMount(() => {
    boot();
    const mq = matchMedia('(prefers-color-scheme: dark)');
    const onScheme = () => app.themePref === 'system' && loadPublic();
    mq.addEventListener('change', onScheme);
    const on = () => { app.offline = false; boot(); }; const off = () => (app.offline = true);
    addEventListener('online', on); addEventListener('offline', off);
    return () => { mq.removeEventListener('change', onScheme); removeEventListener('online', on); removeEventListener('offline', off); };
  });
</script>
<svelte:head><title>{app.brand.tab_title || 'Semechka'}</title></svelte:head>
<a class="skip" href="#main">Skip to content</a>
{#if app.offline}<div class="notice warn" role="alert" style="margin:0;border-radius:0">You are offline or the Semechka server is not reachable. Changes will not be saved until it is back.</div>{/if}
{#if app.ready}{@render children()}{:else}<p class="muted" style="padding:24px" role="status">Loading…</p>{/if}
<Toasts />
