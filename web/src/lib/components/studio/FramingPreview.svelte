<script lang="ts">
  // "Final framing": the picture exactly as it will be handed to the model after cropping, padding or stretching, at the stage's current moment.
  import { contentUrl } from '$lib/media/assets';
  import { framing } from '$lib/studio/spec';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s, height = 150 }: { s: Studio; height?: number } = $props();
  const a = $derived(s.source);
  const sw = $derived(s.sw || 16), sh = $derived(s.sh || 9);
  const fr = $derived(framing(s.spec, sw, sh));
  const c = $derived(fr.content);
  let el: HTMLVideoElement | undefined = $state();
  $effect(() => { const t = s.cur; if (el && el.readyState >= 1 && Math.abs(el.currentTime - t) > 0.04) el.currentTime = t; });
</script>

{#if a}
  <figure class="fp" aria-label="Final framing preview" data-testid="framing-preview" data-ratio={fr.ratio.toFixed(3)} data-treatment={fr.treatment}>
    <div class="fr" style:--ratio={fr.ratio} style:--h={`${height}px`}>
      <div class="ct" style:left={`${((1 - fr.fill.w) / 2) * 100}%`} style:top={`${((1 - fr.fill.h) / 2) * 100}%`} style:width={`${fr.fill.w * 100}%`} style:height={`${fr.fill.h * 100}%`}>
        <!-- svelte-ignore a11y_media_has_caption -->
        <video bind:this={el} src={contentUrl(a)} muted playsinline preload="metadata" tabindex="-1" aria-hidden="true"
          style:width={`${(sw / c.w) * 100}%`} style:height={`${(sh / c.h) * 100}%`} style:left={`${-(c.x / c.w) * 100}%`} style:top={`${-(c.y / c.h) * 100}%`}></video>
      </div>
    </div>
    <figcaption class="tiny muted">What the model gets: {Math.round(c.w)}×{Math.round(c.h)} part of the picture{fr.treatment === 'pad' && fr.fill.w * fr.fill.h < 0.999 ? ', with black bars' : fr.treatment === 'stretch' ? ', stretched to fit' : ''}</figcaption>
  </figure>
{/if}

<style>
  .fp { margin: 0; display: grid; gap: 6px; justify-items: start; }
  .fr { position: relative; height: var(--h); aspect-ratio: var(--ratio); max-width: 100%; background: #000; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); }
  .ct { position: absolute; overflow: hidden; }
  video { position: absolute; display: block; object-fit: fill; max-width: none; }
</style>
