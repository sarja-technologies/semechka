<script lang="ts">
  // Marks on the first frame of the prepared clip: a box drawn by dragging, or points added by clicking (a click with the "exclude" tool, or Shift,
  // marks what must NOT be part of the subject). Everything is kept as fractions of the frame, so it stays right at any size on screen.
  // The marks are optional help for the words; the words alone always work, which is also the way for people who cannot use a pointer.
  let { mode, box = $bindable(null), points = $bindable([]), onchange }:
    { mode: 'none' | 'box' | 'points' | 'exclude'; box: [number, number, number, number] | null; points: { x: number; y: number; positive: boolean }[]; onchange?: () => void } = $props();

  let host: HTMLDivElement | undefined = $state();
  let drag = $state<{ x0: number; y0: number; x1: number; y1: number } | null>(null);
  const clamp = (v: number) => Math.max(0, Math.min(1, v));
  const at = (e: PointerEvent) => { const b = host!.getBoundingClientRect(); return { x: clamp((e.clientX - b.left) / b.width), y: clamp((e.clientY - b.top) / b.height) }; };

  function down(e: PointerEvent) {
    if (mode === 'none' || e.button !== 0) return;
    const p = at(e);
    if (mode === 'box') { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); drag = { x0: p.x, y0: p.y, x1: p.x, y1: p.y }; return; }
    points = [...points, { x: round(p.x), y: round(p.y), positive: mode === 'points' && !e.shiftKey }]; onchange?.();
  }
  function move(e: PointerEvent) { if (drag) { const p = at(e); drag = { ...drag, x1: p.x, y1: p.y }; } }
  function up() {
    if (!drag) return;
    const x = Math.min(drag.x0, drag.x1), y = Math.min(drag.y0, drag.y1), w = Math.abs(drag.x1 - drag.x0), h = Math.abs(drag.y1 - drag.y0);
    drag = null;
    if (w < 0.02 || h < 0.02) return; // a click, not a box
    box = [round(x), round(y), round(w), round(h)]; onchange?.();
  }
  const round = (v: number) => Math.round(v * 1000) / 1000;
  function drop(i: number, e: Event) { e.stopPropagation(); points = points.filter((_, k) => k !== i); onchange?.(); }
  const live = $derived(drag ? [Math.min(drag.x0, drag.x1), Math.min(drag.y0, drag.y1), Math.abs(drag.x1 - drag.x0), Math.abs(drag.y1 - drag.y0)] as const : box);
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="so" class:on={mode !== 'none'} bind:this={host} onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={up} data-testid="subject-overlay">
  {#if live}
    <span class="box" class:drawing={!!drag} style:left={`${live[0] * 100}%`} style:top={`${live[1] * 100}%`} style:width={`${live[2] * 100}%`} style:height={`${live[3] * 100}%`} data-testid="subject-box"></span>
  {/if}
  {#each points as p, i (i)}
    <button type="button" class="pt" class:neg={!p.positive} style:left={`${p.x * 100}%`} style:top={`${p.y * 100}%`} onpointerdown={(e) => e.stopPropagation()} onclick={(e) => drop(i, e)}
      aria-label={`${p.positive ? 'Point on the subject' : 'Point to leave out'} ${i + 1}. Press to remove.`} title="Remove this point"><i aria-hidden="true">{p.positive ? '+' : '−'}</i></button>
  {/each}
</div>

<style>
  .so { position: absolute; inset: 0; z-index: 3; touch-action: none; }
  .so.on { cursor: crosshair; }
  .box { position: absolute; border: 2px solid #5fd0c4; background: rgba(95, 208, 196, 0.16); border-radius: 3px; pointer-events: none; box-shadow: 0 0 0 1px rgba(0, 0, 0, .5); }
  .box.drawing { border-style: dashed; }
  .pt { all: unset; position: absolute; width: 26px; height: 26px; margin: -13px 0 0 -13px; border-radius: 50%; display: grid; place-items: center; cursor: pointer;
    background: #1c8f82; color: #fff; box-shadow: 0 0 0 2px #fff, 0 1px 6px rgba(0, 0, 0, .55); }
  .pt.neg { background: #b3302b; }
  .pt i { font: 700 16px/1 var(--font); font-style: normal; }
  .pt:focus-visible { outline: 2px solid #fff; outline-offset: 3px; }
  @media (pointer: coarse) { .pt { width: 34px; height: 34px; margin: -17px 0 0 -17px; } }
</style>
