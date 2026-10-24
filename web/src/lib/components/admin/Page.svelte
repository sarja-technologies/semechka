<script lang="ts">
  import type { Snippet } from 'svelte';
  let { title, lead = '', actions, children }: { title: string; lead?: string; actions?: Snippet; children: Snippet } = $props();
</script>
<svelte:head><title>{title} · Admin</title></svelte:head>
<header class="ph">
  <div class="grow"><h1>{title}</h1>{#if lead}<p class="muted" style="margin:4px 0 0;max-width:70ch">{lead}</p>{/if}</div>
  {#if actions}<div class="row">{@render actions()}</div>{/if}
</header>
{@render children()}
<style>.ph { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 20px; } h1 { font-size: 22px; }</style>
