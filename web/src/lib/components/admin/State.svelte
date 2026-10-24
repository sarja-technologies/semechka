<script lang="ts">
  // Honest loading / error / empty block.
  let { loading = false, error = '', empty = '', retry }: { loading?: boolean; error?: string; empty?: string; retry?: () => void } = $props();
</script>
{#if loading}<p class="muted" role="status">Loading…</p>
{:else if error}<div class="notice bad" role="alert"><div class="grow">{error}</div>{#if retry}<button class="btn sm" onclick={retry}>Retry</button>{/if}</div>
{:else if empty}<p class="muted">{empty}</p>{/if}
