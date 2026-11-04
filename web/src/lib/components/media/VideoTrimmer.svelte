<script lang="ts">
  // The trim timeline: frames of the video along a track, two handles that bound the part to keep, a playhead, and exact start/end times.
  // Times are seconds in the real file; the handles snap to whole frames when the frame rate is known.
  import Icon from '$lib/components/Icon.svelte';
  import { fmtDuration, parseTime, stripUrl, type Asset } from '$lib/media/assets';

  let { asset, duration, start = $bindable(0), end = $bindable(0), current = 0, onseek, oninput: onedit }:
    { asset: Asset; duration: number; start: number; end: number; current?: number; onseek?: (t: number) => void; oninput?: (which: 'start' | 'end') => void } = $props();

  const fps = $derived(asset.media?.fps && asset.media.fps > 0 ? asset.media.fps : 0);
  const frame = $derived(fps ? 1 / fps : 0.01);
  const MIN = 0.1;
  let track: HTMLDivElement | undefined = $state(), dragging = $state<'start' | 'end' | 'head' | null>(null);
  let stripFailed = $state(false);
  const n = $derived(Math.max(8, Math.min(24, Math.round((track?.clientWidth ?? 600) / 56))));
  const snap = (t: number) => (fps ? Math.round(t * fps) / fps : t);
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));
  const pct = (t: number) => `${(clamp(t, 0, duration) / duration) * 100}%`;
  const at = (e: PointerEvent) => { const b = track!.getBoundingClientRect(); return clamp(((e.clientX - b.left) / b.width) * duration, 0, duration); };

  function down(e: PointerEvent, which: 'start' | 'end') {
    e.preventDefault(); e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging = which;
  }
  function move(e: PointerEvent) {
    if (!dragging) return;
    const t = snap(at(e));
    if (dragging === 'start') { start = clamp(t, 0, end - MIN); onseek?.(start); onedit?.('start'); }
    else if (dragging === 'end') { end = clamp(t, start + MIN, duration); onseek?.(end); onedit?.('end'); }
    else onseek?.(t);
  }
  const up = () => (dragging = null);
  function trackDown(e: PointerEvent) {
    if (e.button !== 0) return;
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging = 'head'; onseek?.(snap(at(e)));
  }
  function keyHandle(e: KeyboardEvent, which: 'start' | 'end') {
    const step = e.shiftKey ? 1 : frame;
    const d = e.key === 'ArrowLeft' ? -step : e.key === 'ArrowRight' ? step : 0;
    if (!d) return;
    e.preventDefault(); e.stopPropagation();
    if (which === 'start') { start = clamp(snap(start + d), 0, end - MIN); onseek?.(start); }
    else { end = clamp(snap(end + d), start + MIN, duration); onseek?.(end); }
    onedit?.(which);
  }

  // exact times typed in
  let sTxt = $state(''), eTxt = $state(''), sBad = $state(false), eBad = $state(false);
  $effect(() => { sTxt = fmtDuration(start, true); sBad = false; });
  $effect(() => { eTxt = fmtDuration(end, true); eBad = false; });
  function commitStart() { const t = parseTime(sTxt); if (t == null || t < 0 || t > end - MIN) { sBad = true; return; } start = snap(t); onseek?.(start); onedit?.('start'); }
  function commitEnd() { const t = parseTime(eTxt); if (t == null || t > duration + 0.001 || t < start + MIN) { eBad = true; return; } end = Math.min(duration, snap(t)); onseek?.(end); onedit?.('end'); }
  const reset = () => { start = 0; end = duration; onseek?.(0); onedit?.('start'); };
  const changed = $derived(start > 0.001 || end < duration - 0.001);
</script>

<div class="tr">
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="track" bind:this={track} onpointerdown={trackDown} onpointermove={move} onpointerup={up} onpointercancel={up}>
    {#if !stripFailed}
      <img class="strip" src={stripUrl(asset, n, 56)} alt="" draggable="false" onerror={() => (stripFailed = true)} />
    {/if}
    <span class="dim l" style:width={pct(start)}></span>
    <span class="dim r" style:left={pct(end)} style:right="0"></span>
    <span class="sel" style:left={pct(start)} style:width={`calc(${pct(end)} - ${pct(start)})`}></span>
    <span class="head" style:left={pct(current)} aria-hidden="true"></span>
    <button type="button" class="h s" style:left={pct(start)} role="slider" aria-label="Trim start" aria-valuemin={0} aria-valuemax={Math.max(0, end - MIN)} aria-valuenow={start}
      aria-valuetext={fmtDuration(start, true)} onpointerdown={(e) => down(e, 'start')} onkeydown={(e) => keyHandle(e, 'start')}><i></i></button>
    <button type="button" class="h e" style:left={pct(end)} role="slider" aria-label="Trim end" aria-valuemin={start + MIN} aria-valuemax={duration} aria-valuenow={end}
      aria-valuetext={fmtDuration(end, true)} onpointerdown={(e) => down(e, 'end')} onkeydown={(e) => keyHandle(e, 'end')}><i></i></button>
  </div>
  <div class="times">
    <label class="t"><span>Start</span><input class="in" class:bad={sBad} bind:value={sTxt} inputmode="decimal" aria-invalid={sBad} onblur={commitStart} onkeydown={(e) => e.key === 'Enter' && commitStart()} /></label>
    <span class="len" title="Length of the kept part"><Icon name="film" size={13} /> {fmtDuration(end - start, true)}{changed ? ` of ${fmtDuration(duration, true)}` : ''}</span>
    <label class="t r"><span>End</span><input class="in" class:bad={eBad} bind:value={eTxt} inputmode="decimal" aria-invalid={eBad} onblur={commitEnd} onkeydown={(e) => e.key === 'Enter' && commitEnd()} /></label>
    <button type="button" class="rs" onclick={reset} disabled={!changed} title="Reset the trim"><Icon name="undo" size={14} /> Reset</button>
  </div>
  {#if sBad || eBad}<p class="bad-note tiny" role="status">Use a time like 1:05.25 inside the video, with the start before the end.</p>{/if}
</div>

<style>
  .tr { display: grid; gap: 8px; }
  .track { position: relative; height: 56px; border-radius: 8px; background: #17181a; overflow: visible; cursor: pointer; touch-action: none; user-select: none; }
  .strip { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: fill; border-radius: 8px; opacity: 0.9; pointer-events: none; }
  .dim { position: absolute; top: 0; bottom: 0; background: rgba(8, 9, 10, 0.72); pointer-events: none; }
  .dim.l { left: 0; border-radius: 8px 0 0 8px; } .dim.r { border-radius: 0 8px 8px 0; }
  .sel { position: absolute; top: -2px; bottom: -2px; border-top: 2px solid #fff; border-bottom: 2px solid #fff; pointer-events: none; box-sizing: border-box; }
  .head { position: absolute; top: -5px; bottom: -5px; width: 2px; margin-left: -1px; background: var(--m-ring); border-radius: 1px; pointer-events: none; box-shadow: 0 0 0 1px rgba(0,0,0,.35); }
  .h { all: unset; position: absolute; top: -2px; bottom: -2px; width: 14px; margin-left: -7px; cursor: ew-resize; display: grid; place-items: center; z-index: 2; touch-action: none; }
  .h::before { content: ''; position: absolute; inset: 0 -8px; }
  .h i { width: 14px; height: 100%; background: #fff; border-radius: 4px; display: block; position: relative; box-shadow: 0 1px 4px rgba(0,0,0,.5); }
  .h i::after { content: ''; position: absolute; left: 50%; top: 22%; bottom: 22%; width: 2px; margin-left: -1px; background: #6a6d70; border-radius: 1px; }
  .h.s i { border-radius: 6px 2px 2px 6px; } .h.e i { border-radius: 2px 6px 6px 2px; }
  .h:focus-visible i { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .times { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; color: var(--m-chrome-muted); font-size: 12px; }
  .t { display: inline-flex; align-items: center; gap: 6px; } .t.r { margin-left: auto; }
  .in { all: unset; box-sizing: border-box; width: 84px; padding: 5px 8px; border-radius: 7px; background: rgba(255,255,255,.07); border: 1px solid var(--m-chrome-line); color: var(--m-chrome-fg);
    font-variant-numeric: tabular-nums; font-size: 12.5px; text-align: center; }
  .in:focus { border-color: var(--m-ring); box-shadow: 0 0 0 3px var(--m-ring-soft); } .in.bad { border-color: #ef6d67; }
  .len { display: inline-flex; align-items: center; gap: 5px; color: var(--m-chrome-fg); font-variant-numeric: tabular-nums; }
  .rs { all: unset; display: inline-flex; align-items: center; gap: 5px; padding: 5px 9px; border-radius: 7px; cursor: pointer; color: var(--m-chrome-muted); }
  .rs:hover:not(:disabled) { background: rgba(255,255,255,.08); color: var(--m-chrome-fg); } .rs:disabled { opacity: .4; cursor: default; } .rs:focus-visible { outline: 2px solid var(--m-ring); }
  .bad-note { margin: 0; color: #f0a19d; }
  @media (max-width: 560px) { .t.r { margin-left: 0; } .in { width: 76px; } }
</style>
