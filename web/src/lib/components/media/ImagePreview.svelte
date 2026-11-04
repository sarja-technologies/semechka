<script lang="ts">
  // A picture as a card: its preview, opening the full viewer on click.
  import MediaThumb from './MediaThumb.svelte';
  import type { Asset } from '$lib/media/assets';
  let { asset, src, onopen, ratio }: { asset: Pick<Asset, 'id' | 'name'>; src?: string | null; onopen?: () => void; ratio?: string } = $props();
</script>
<button type="button" class="ip" onclick={onopen} disabled={!onopen} aria-label={`Open ${asset.name ?? 'picture'}`}>
  <MediaThumb id={asset.id} kind="image" {src} name={asset.name} {ratio} />
</button>
<style>
  .ip { all: unset; display: block; width: 100%; height: 100%; cursor: zoom-in; } .ip:disabled { cursor: default; }
  .ip:focus-visible { outline: 2px solid var(--m-ring); outline-offset: -2px; }
</style>
