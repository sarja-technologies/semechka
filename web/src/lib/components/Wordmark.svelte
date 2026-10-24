<script lang="ts">
  import { app } from '$lib/session.svelte';
  let { size = 18 }: { size?: number } = $props();
  const b = $derived(app.brand);
</script>
{#if b.logo_asset}
  <img src={`/api/assets/${b.logo_asset}`} alt={b.app_name || ''} style="height:{size + 8}px;max-width:180px;object-fit:contain" />
{:else if b.app_name && b.app_name !== 'Semechka'}
  <span class="wm" style="font-size:{size}px">{b.short_name || b.app_name}</span>
{:else}
  <span class="wm mono" style="font-size:{size}px">semechka<span class="dot">.</span></span>
{/if}
<style>
  .wm { font-weight: 600; letter-spacing: -0.01em; white-space: nowrap; }
  .mono { font-family: var(--mono); }
  .dot { color: var(--accent); }
</style>
