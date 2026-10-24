<script lang="ts">
  // The organisation's identity: its logo (the dark-theme version on dark themes), else its name. The Semechka name is
  // shown only where a caller asks for it (first-run setup, before any branding exists), never forced elsewhere.
  import { app } from '$lib/session.svelte';
  let { size = 18, logo: useLogo = true, fallback = false }: { size?: number; logo?: boolean; fallback?: boolean } = $props();
  const b = $derived(app.brand);
  const logo = $derived(app.themeVariant === 'dark' && b.logo_dark_asset ? b.logo_dark_asset : b.logo_asset);
  const name = $derived<string>(b.short_name || b.app_name || '');
</script>
{#if useLogo && logo}
  <img src={`/api/assets/${logo}`} alt={b.app_name || ''} style="height:{size + 8}px;max-width:180px;object-fit:contain" />
{:else if name}
  <span class="wm" style="font-size:{size}px">{name}</span>
{:else if fallback}
  <span class="wm mono" style="font-size:{size}px">semechka<span class="dot">.</span></span>
{/if}
<style>
  .wm { font-weight: 600; letter-spacing: -0.01em; white-space: nowrap; }
  .mono { font-family: var(--mono); }
  .dot { color: var(--accent); }
</style>
