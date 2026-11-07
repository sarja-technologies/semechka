<script lang="ts">
  // Before / after: the two pictures stacked, with a divider you drag or move with the arrow keys. Both are the real files; nothing is blended.
  import { contentUrl, type Asset } from '$lib/media/assets';
  let { before, after, beforeLabel = 'Before', afterLabel = 'After' }: { before: Asset; after: Asset; beforeLabel?: string; afterLabel?: string } = $props();
  let pos = $state(50), box: HTMLDivElement | undefined = $state(), drag = $state(false);
  const ratio = $derived(after.width && after.height ? `${after.width} / ${after.height}` : '1 / 1');
  function at(e: PointerEvent) { if (!box) return; const r = box.getBoundingClientRect(); pos = Math.min(100, Math.max(0, ((e.clientX - r.left) / r.width) * 100)); }
</script>

<div class="cmp" data-testid="compare">
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="box" bind:this={box} style:aspect-ratio={ratio} onpointerdown={(e) => { drag = true; (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); at(e); }} onpointermove={(e) => drag && at(e)} onpointerup={() => (drag = false)} onpointercancel={() => (drag = false)}>
    <img class="a" src={contentUrl(after)} alt={`${afterLabel} picture`} draggable="false" />
    <img class="b" src={contentUrl(before)} alt={`${beforeLabel} picture`} draggable="false" style:clip-path={`inset(0 ${100 - pos}% 0 0)`} />
    <span class="bar" style:left={`${pos}%`} aria-hidden="true"><i></i></span>
    <span class="tag l" aria-hidden="true">{beforeLabel}</span><span class="tag r" aria-hidden="true">{afterLabel}</span>
  </div>
  <input type="range" min="0" max="100" step="1" bind:value={pos} aria-label={`Compare ${beforeLabel} and ${afterLabel}: how much of the ${beforeLabel} picture shows`} data-testid="compare-slider" />
</div>

<style>
  .cmp { display: grid; gap: 6px; min-width: 0; justify-items: center; }
  .box { position: relative; width: 100%; max-height: min(70vh, 760px); border-radius: 10px; overflow: hidden; border: 1px solid var(--border); background: var(--m-card); touch-action: pan-y; cursor: ew-resize; user-select: none; }
  img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: contain; pointer-events: none; }
  .bar { position: absolute; top: 0; bottom: 0; width: 2px; margin-left: -1px; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.5); pointer-events: none; }
  .bar i { position: absolute; top: 50%; left: 50%; width: 26px; height: 26px; margin: -13px; border-radius: 50%; background: #fff; box-shadow: 0 0 0 1px rgba(0,0,0,.5), 0 2px 6px rgba(0,0,0,.4); }
  .tag { position: absolute; top: 8px; padding: 2px 8px; border-radius: 8px; font-size: 11.5px; background: rgba(14,15,16,.78); color: #fff; } .l { left: 8px; } .r { right: 8px; }
  input[type='range'] { width: min(100%, 420px); min-height: 32px; accent-color: var(--accent); } @media (pointer: coarse) { input[type='range'] { min-height: 44px; } }
</style>
