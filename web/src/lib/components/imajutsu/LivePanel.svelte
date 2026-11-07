<script lang="ts">
  // The runs just started. One card per picture asked for; each reads the engine's own trace.
  import PanelCard from './PanelCard.svelte';
  import RunCard from './RunCard.svelte';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
</script>

<PanelCard id="live" title="Live job" hint="What the engine is doing right now. Nothing here is estimated: when the engine has no number, the bar says so.">
  {#if !s.batch.length}
    <p class="st-note" data-testid="live-empty">No run yet. When you press Generate, it appears here and stays after you leave. Earlier runs are under Jobs.</p>
  {:else}
    <div class="runs" data-testid="runs">{#each s.batch as j, i (j)}<RunCard {s} job={j} index={i} total={s.batch.length} />{/each}</div>
  {/if}
</PanelCard>
<style>.runs { display: grid; gap: 10px; }</style>
