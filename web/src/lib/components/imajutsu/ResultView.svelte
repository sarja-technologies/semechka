<script lang="ts">
  // The stage showing one result, large, with before / after against the original when there is one.
  import Compare from './Compare.svelte';
  import { contentUrl, fmtDims } from '$lib/media/assets';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
  const a = $derived(s.asset(s.picked ?? s.resultIds[0] ?? s.copies[0]));
  const before = $derived(s.needs.source && a && s.source && s.source.id !== a.id ? s.source : null);
</script>
<div class="rv" data-testid="result-view">
  {#if !a}
    <p class="st-note" role="status">The picture appears here as soon as it is finished.</p>
  {:else}
    {#if before}
      <label class="tg"><input type="checkbox" bind:checked={s.compare} data-testid="compare-toggle" /> Compare with your original</label>
    {/if}
    {#if before && s.compare}
      <Compare {before} after={a} beforeLabel="Original" afterLabel="Result" />
    {:else}
      <img src={contentUrl(a)} alt={`Result, ${fmtDims(a)}`} data-testid="result-image" />
    {/if}
    <p class="tiny muted">{fmtDims(a)}</p>
  {/if}
</div>
<style>
  .rv { display: grid; gap: 8px; justify-items: center; min-width: 0; } img { max-width: 100%; max-height: min(72vh, 780px); border-radius: 10px; border: 1px solid var(--border); background: var(--m-card); }
  .tg { justify-self: start; display: inline-flex; align-items: center; gap: 8px; font-size: 13px; min-height: 36px; cursor: pointer; } .tg input { width: 18px; height: 18px; }
</style>
