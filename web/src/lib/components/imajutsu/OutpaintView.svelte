<script lang="ts">
  // Outpaint: the new picture's frame with your picture in it. The hatched part is the area the model will make. It is drawn from the server's plan.
  import { contentUrl } from '$lib/media/assets';
  import { fmtDims } from '$lib/imajutsu/spec';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
  const src = $derived(s.source), og = $derived(s.plan?.outpaint);
  const geo = $derived.by(() => {
    if (!src?.width || !src?.height || !og?.canvas) return null;
    const c = og.canvas, p = og.pad;
    return { x: p.left / c.width * 100, y: p.top / c.height * 100, w: src.width / c.width * 100, h: src.height / c.height * 100 };
  });
</script>
<div class="op" data-testid="outpaint-view">
  {#if src && geo && og}
    <div class="frame" style:aspect-ratio={`${og.canvas.width} / ${og.canvas.height}`} role="img" aria-label={`The new canvas, ${fmtDims(og.canvas)}. Your picture sits inside it; the hatched area around it is new.`}>
      <span class="in" style:left={`${geo.x}%`} style:top={`${geo.y}%`} style:width={`${geo.w}%`} style:height={`${geo.h}%`}><img src={contentUrl(src)} alt="" draggable="false" /></span>
    </div>
    <p class="st-note" data-testid="outpaint-note">New canvas {fmtDims(og.canvas)}. Your picture {fmtDims(src)} stays as it is; the hatched area is what the model makes ({og.pad.left} px left, {og.pad.right} right, {og.pad.top} top, {og.pad.bottom} bottom).</p>
  {:else}
    <p class="st-note" role="status">{!src ? 'Add a picture first.' : s.plan?.errors?.length ? s.plan.errors[0] : 'The new area appears once the server has worked it out. Choose a shape or a size bigger than your picture.'}</p>
  {/if}
</div>
<style>
  .op { display: grid; gap: 8px; justify-items: center; min-width: 0; } .frame { position: relative; width: min(100%, 560px); max-height: 70vh; border: 1px solid var(--border-strong); border-radius: 8px; overflow: hidden;
    background: repeating-linear-gradient(45deg, color-mix(in srgb, var(--fg) 10%, var(--card-bg)) 0 8px, var(--card-bg) 8px 16px); }
  .in { position: absolute; display: block; box-shadow: 0 0 0 2px var(--fg); } .in img { width: 100%; height: 100%; display: block; object-fit: fill; }
</style>
