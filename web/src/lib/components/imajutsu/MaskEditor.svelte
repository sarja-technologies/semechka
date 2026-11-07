<script lang="ts">
  // The selection editor: paint, erase or box the part of the picture the model should change. The selection is a list of simple operations in
  // pixels of the SOURCE picture, so the translucent overlay here and the PNG the server receives (white = change) come from the same list. The
  // strokes are kept in the project, so reopening the editor (or reloading the page) brings them back and they can still be undone.
  import { onMount, untrack } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import { contentUrl } from '$lib/media/assets';
  import { MAX_PTS, MAX_WORK, addOp, coverage, hasSelection, paintOps, round1, thin } from '$lib/imajutsu/mask';
  import type { MaskDoc, MaskOp } from '$lib/imajutsu/types';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';

  let { s, onclose }: { s: Imajutsu; onclose?: () => void } = $props();
  const src = $derived(s.source);
  const W = $derived(src?.width ?? 0), H = $derived(src?.height ?? 0);

  type Snap = { ops: MaskOp[]; invert: boolean };
  const doc = $derived<MaskDoc>(s.maskDoc ?? { w: W, h: H, ops: [] });
  const sel = $derived(s.spec.selection);
  let past = $state<Snap[]>([]), future = $state<Snap[]>([]);
  let tool = $state<'brush' | 'erase' | 'box' | 'hand'>('brush');
  let brush = $state(40), spaceDown = $state(false);
  let zoom = $state(1), pan = $state({ x: 0, y: 0 }), fit = $state(0.2);
  let host: HTMLDivElement | undefined = $state(), view: HTMLDivElement | undefined = $state(), cv: HTMLCanvasElement | undefined = $state();
  let cursor = $state<{ x: number; y: number; on: boolean }>({ x: 0, y: 0, on: false });
  let live = $state(''), cover = $state<number | null>(null), numbers = $state({ x: 0, y: 0, w: 0, h: 0 });
  let current = $state<MaskOp | null>(null);
  const pointers = new Map<number, { x: number; y: number }>();
  let pinch: { d: number; zoom: number; cx: number; cy: number; pan: { x: number; y: number } } | null = null;
  let panDrag: { x: number; y: number; pan: { x: number; y: number } } | null = null;
  let boxStart: { x: number; y: number } | null = null;
  const k = $derived(Math.min(1, MAX_WORK / Math.max(W, H, 1)));

  // ---- start from the picture's own size
  $effect(() => {
    if (!W || !H) return;
    untrack(() => { brush = Math.max(8, Math.round(Math.max(W, H) / 22)); numbers = { x: 0, y: 0, w: Math.round(W / 2), h: Math.round(H / 2) }; past = []; future = []; });
  });

  // ---- keep the project in step and the overlay drawn
  function commit(next: MaskDoc, record = true) {
    if (record) { past = [...past.slice(-60), { ops: doc.ops, invert: sel.invert }]; future = []; }
    s.setMaskDoc({ w: next.w || W, h: next.h || H, ops: next.ops });
  }
  const setSel = (fn: (x: typeof sel) => void) => s.edit((sp) => fn(sp.selection));
  const maskCv = typeof document !== 'undefined' ? document.createElement('canvas') : null;
  function draw() {
    if (!cv || !maskCv || !W) return;
    const cw = Math.max(1, Math.round(W * k)), ch = Math.max(1, Math.round(H * k));
    if (maskCv.width !== cw || maskCv.height !== ch) { maskCv.width = cw; maskCv.height = ch; }
    if (cv.width !== cw || cv.height !== ch) { cv.width = cw; cv.height = ch; }
    const m = maskCv.getContext('2d')!;
    m.clearRect(0, 0, cw, ch);
    paintOps(m, current && current.t !== 'rect' ? [...doc.ops, current] : doc.ops, k);
    const c = cv.getContext('2d')!;
    c.clearRect(0, 0, cw, ch);
    const col = 'rgba(255, 64, 110, 0.55)';
    c.save();
    if (sel.feather > 0 && 'filter' in c) c.filter = `blur(${sel.feather * k / 2}px)`;
    if (sel.invert) { c.fillStyle = col; c.fillRect(0, 0, cw, ch); c.globalCompositeOperation = 'destination-out'; c.drawImage(maskCv, 0, 0); }
    else { c.drawImage(maskCv, 0, 0); c.globalCompositeOperation = 'source-in'; c.fillStyle = col; c.fillRect(0, 0, cw, ch); }
    c.restore();
    if (current && current.t === 'rect') {
      c.save(); c.setLineDash([8, 6]); c.lineWidth = 2; c.strokeStyle = current.sub ? '#fff' : '#ff406e'; c.fillStyle = current.sub ? 'rgba(255,255,255,.18)' : 'rgba(255,64,110,.25)';
      c.fillRect(current.x * k, current.y * k, current.w * k, current.h * k); c.strokeRect(current.x * k, current.y * k, current.w * k, current.h * k); c.restore();
    }
  }
  $effect(() => { void doc; void sel.invert; void sel.feather; void current; void k; void cv; untrack(draw); });
  $effect(() => {
    const d = doc, inv = sel.invert; if (!d.w) return;
    const t = setTimeout(() => { try { cover = hasSelection(d) ? Math.round(coverage(d, inv) * 100) : 0; } catch { cover = null; } }, 120);
    return () => clearTimeout(t);
  });

  // ---- layout: fit the picture in the box, zoom and pan around it
  function measure() {
    if (!host || !W) return;
    const cw = host.clientWidth, ch = Math.min(window.innerHeight * 0.68, 720);
    fit = Math.max(0.01, Math.min(cw / W, ch / H));
  }
  onMount(() => {
    measure();
    const ro = new ResizeObserver(measure); if (host) ro.observe(host);
    addEventListener('resize', measure);
    return () => { ro.disconnect(); removeEventListener('resize', measure); };
  });
  $effect(() => { void W; void H; untrack(() => { measure(); resetView(); }); });
  const vw = $derived(W * fit), vh = $derived(H * fit);
  function resetView() { zoom = 1; pan = { x: 0, y: 0 }; }
  function clampPan(p: { x: number; y: number }, z = zoom) {
    const mx = Math.max(0, vw * z - vw * 0.15), my = Math.max(0, vh * z - vh * 0.15);
    return { x: Math.min(vw * 0.85, Math.max(-mx, p.x)), y: Math.min(vh * 0.85, Math.max(-my, p.y)) };
  }
  function zoomAt(f: number, cx: number, cy: number) {
    const nz = Math.min(24, Math.max(1, zoom * f));
    const wx = (cx - pan.x) / zoom, wy = (cy - pan.y) / zoom;
    zoom = nz; pan = nz === 1 ? { x: 0, y: 0 } : clampPan({ x: cx - wx * nz, y: cy - wy * nz }, nz);
  }
  const center = () => ({ x: vw / 2, y: vh / 2 });
  const toSrc = (e: { clientX: number; clientY: number }) => {
    const r = view!.getBoundingClientRect();
    return { x: ((e.clientX - r.left - pan.x) / zoom) / fit, y: ((e.clientY - r.top - pan.y) / zoom) / fit };
  };
  const local = (e: { clientX: number; clientY: number }) => { const r = view!.getBoundingClientRect(); return { x: e.clientX - r.left, y: e.clientY - r.top }; };
  const clampPt = (p: { x: number; y: number }) => ({ x: Math.min(W, Math.max(0, p.x)), y: Math.min(H, Math.max(0, p.y)) });

  // ---- pointer: draw with one finger or the mouse, pan with space/middle/hand, pinch with two fingers
  function down(e: PointerEvent) {
    if (!view || !W) return;
    view.focus({ preventScroll: true });
    pointers.set(e.pointerId, local(e));
    view.setPointerCapture(e.pointerId);
    if (pointers.size === 2) {                              // a second finger turns the gesture into pinch/pan and abandons the stroke
      current = null; boxStart = null;
      const [a, b] = [...pointers.values()];
      pinch = { d: Math.hypot(a.x - b.x, a.y - b.y) || 1, zoom, cx: (a.x + b.x) / 2, cy: (a.y + b.y) / 2, pan: { ...pan } };
      return;
    }
    if (pointers.size > 2) return;
    if (e.button === 1 || tool === 'hand' || spaceDown) { const l = local(e); panDrag = { x: l.x, y: l.y, pan: { ...pan } }; return; }
    if (e.button !== 0 && e.pointerType === 'mouse') return;
    const p = clampPt(toSrc(e));
    if (tool === 'box') { boxStart = p; current = { t: 'rect', x: p.x, y: p.y, w: 0, h: 0, sub: e.altKey }; return; }
    current = { t: tool === 'erase' ? 'erase' : 'brush', size: brush, pts: [round1(p.x), round1(p.y)] };
  }
  function move(e: PointerEvent) {
    if (!view) return;
    const l = local(e);
    cursor = { x: l.x, y: l.y, on: e.pointerType !== 'touch' };
    if (!pointers.has(e.pointerId)) return;
    pointers.set(e.pointerId, l);
    if (pinch && pointers.size >= 2) {
      const [a, b] = [...pointers.values()], d = Math.hypot(a.x - b.x, a.y - b.y) || 1, cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
      const nz = Math.min(24, Math.max(1, pinch.zoom * d / pinch.d));
      const wx = (pinch.cx - pinch.pan.x) / pinch.zoom, wy = (pinch.cy - pinch.pan.y) / pinch.zoom;
      zoom = nz; pan = nz === 1 ? { x: 0, y: 0 } : clampPan({ x: cx - wx * nz, y: cy - wy * nz }, nz);
      return;
    }
    if (panDrag) { pan = clampPan({ x: panDrag.pan.x + l.x - panDrag.x, y: panDrag.pan.y + l.y - panDrag.y }); return; }
    if (!current) return;
    const p = clampPt(toSrc(e));
    if (current.t === 'rect' && boxStart) {
      current = { t: 'rect', x: Math.min(boxStart.x, p.x), y: Math.min(boxStart.y, p.y), w: Math.abs(p.x - boxStart.x), h: Math.abs(p.y - boxStart.y), sub: e.altKey || current.sub };
    } else if (current.t !== 'rect') {
      const pts = current.pts, lx = pts[pts.length - 2], ly = pts[pts.length - 1];
      if (Math.hypot(p.x - lx, p.y - ly) >= Math.max(0.5, 0.6 / (fit * zoom)) && pts.length < MAX_PTS) current = { ...current, pts: [...pts, round1(p.x), round1(p.y)] };
    }
  }
  function up(e: PointerEvent) {
    pointers.delete(e.pointerId);
    if (view?.hasPointerCapture(e.pointerId)) view.releasePointerCapture(e.pointerId);
    if (pointers.size < 2) pinch = null;
    if (panDrag && pointers.size === 0) { panDrag = null; return; }
    if (pointers.size > 0 && !current) return;
    const c = current; current = null; boxStart = null;
    if (!c) return;
    if (c.t === 'rect') { if (c.w >= 2 && c.h >= 2) { commit(addOp(doc, { ...c, x: Math.round(c.x), y: Math.round(c.y), w: Math.round(c.w), h: Math.round(c.h) })); say(c.sub ? 'Box removed from the selection.' : 'Box added to the selection.'); } return; }
    commit(addOp(doc, { ...c, pts: thin(c.pts, Math.max(0.5, c.size / 12)) }));
    say(c.t === 'erase' ? 'Erased part of the selection.' : 'Painted part of the selection.');
  }
  function cancel(e: PointerEvent) { pointers.delete(e.pointerId); if (pointers.size < 2) pinch = null; if (!pointers.size) { panDrag = null; current = null; boxStart = null; } }
  function wheel(e: WheelEvent) {
    e.preventDefault();
    const l = local(e), f = Math.exp(-e.deltaY * (e.ctrlKey ? 0.01 : 0.0016));
    zoomAt(f, l.x, l.y);
  }
  $effect(() => { const v = view; if (!v) return; v.addEventListener('wheel', wheel, { passive: false }); return () => v.removeEventListener('wheel', wheel); });

  // ---- actions (every one is also a button: nothing needs a pointer)
  const say = (t: string) => { live = t; };
  function undo() { const p = past[past.length - 1]; if (!p) return; future = [{ ops: doc.ops, invert: sel.invert }, ...future]; past = past.slice(0, -1); commit({ ...doc, ops: p.ops }, false); setSel((x) => { x.invert = p.invert; }); say('Undone.'); }
  function redo() { const f = future[0]; if (!f) return; past = [...past, { ops: doc.ops, invert: sel.invert }]; future = future.slice(1); commit({ ...doc, ops: f.ops }, false); setSel((x) => { x.invert = f.invert; }); say('Redone.'); }
  function clear() { if (!doc.ops.length && !sel.invert) return; past = [...past.slice(-60), { ops: doc.ops, invert: sel.invert }]; future = []; commit({ ...doc, ops: [] }, false); setSel((x) => { x.invert = false; }); say('Selection cleared.'); }
  function invert() { past = [...past.slice(-60), { ops: doc.ops, invert: sel.invert }]; future = []; const now = !sel.invert; setSel((x) => { x.invert = now; }); say(now ? 'Selection inverted: everything except what you marked will change.' : 'Selection back to normal.'); }
  function all() { commit(addOp(doc, { t: 'rect', x: 0, y: 0, w: W, h: H })); say('The whole picture is selected.'); }
  function addNumbers() {
    const n = { x: Math.max(0, Math.round(numbers.x)), y: Math.max(0, Math.round(numbers.y)), w: Math.round(numbers.w), h: Math.round(numbers.h) };
    n.w = Math.min(n.w, W - n.x); n.h = Math.min(n.h, H - n.y);
    if (n.w < 1 || n.h < 1) { say('That box is outside the picture.'); return; }
    commit(addOp(doc, { t: 'rect', ...n })); say(`Box added: ${n.w} by ${n.h} pixels.`);
  }
  function size(f: number) { brush = Math.max(2, Math.min(Math.round(Math.max(W, H) / 3), Math.round(brush * f))); }
  function feather(v: number) { setSel((x) => { x.feather = Math.max(0, Math.min(200, Math.round(v))); }); }

  function key(e: KeyboardEvent) {
    const t = e.target as HTMLElement;
    if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
    const mod = e.ctrlKey || e.metaKey, kk = e.key.toLowerCase();
    if (e.key === ' ') { if (t === view) { e.preventDefault(); spaceDown = true; } return; }
    if (mod && kk === 'z') { e.preventDefault(); e.shiftKey ? redo() : undo(); return; }
    if (mod && kk === 'y') { e.preventDefault(); redo(); return; }
    if (mod) return;
    if (kk === 'b') { tool = 'brush'; say('Brush.'); } else if (kk === 'e') { tool = 'erase'; say('Eraser.'); } else if (kk === 'r') { tool = 'box'; say('Box select. Hold Alt to take a box away.'); } else if (kk === 'h') { tool = 'hand'; say('Move the picture.'); }
    else if (kk === 'i') invert();
    else if (e.key === '[') size(0.8); else if (e.key === ']') size(1.25);
    else if (e.key === '0') { resetView(); say('Fitted.'); }
    else if (e.key === '+' || e.key === '=') zoomAt(1.4, center().x, center().y); else if (e.key === '-') zoomAt(1 / 1.4, center().x, center().y);
    else if ((e.key === 'Delete' || e.key === 'Backspace') && t === view) { e.preventDefault(); clear(); }
    else if (e.key.startsWith('Arrow') && t === view && zoom > 1) { e.preventDefault(); const d = 40; pan = clampPan({ x: pan.x + (e.key === 'ArrowLeft' ? d : e.key === 'ArrowRight' ? -d : 0), y: pan.y + (e.key === 'ArrowUp' ? d : e.key === 'ArrowDown' ? -d : 0) }); }
  }
  function keyup(e: KeyboardEvent) { if (e.key === ' ') spaceDown = false; }

  async function save() { await s.saveMask(); say(s.maskError ? s.maskError : 'Selection saved.'); }
  const fresh = $derived(hasSelection(doc) && !s.maskStale);
  const tools = [
    { id: 'brush', label: 'Brush', k: 'B', icon: 'edit' }, { id: 'erase', label: 'Eraser', k: 'E', icon: 'eraser' },
    { id: 'box', label: 'Box select', k: 'R', icon: 'boxsel' }, { id: 'hand', label: 'Move', k: 'H', icon: 'move' }
  ] as const;
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ed" data-testid="mask-editor" onkeydown={key} onkeyup={keyup}>
  <div class="tb" role="toolbar" aria-label="Selection tools">
    <div class="grp" role="radiogroup" aria-label="Tool">
      {#each tools as t (t.id)}
        <button type="button" role="radio" aria-checked={tool === t.id} class="tool" data-tool={t.id} onclick={() => (tool = t.id)} title={`${t.label} (${t.k})`} aria-keyshortcuts={t.k}><Icon name={t.icon} size={16} /><span>{t.label}</span></button>
      {/each}
    </div>
    <div class="grp">
      <button type="button" class="btn sm" onclick={undo} disabled={!past.length} data-testid="mask-undo" aria-keyshortcuts="Control+Z"><Icon name="undo" size={14} /> Undo</button>
      <button type="button" class="btn sm" onclick={redo} disabled={!future.length} data-testid="mask-redo" aria-keyshortcuts="Control+Shift+Z"><Icon name="redo" size={14} /> Redo</button>
      <button type="button" class="btn sm" onclick={invert} aria-pressed={sel.invert} data-testid="mask-invert" aria-keyshortcuts="I">Invert</button>
      <button type="button" class="btn sm" onclick={all} data-testid="mask-all">Select all</button>
      <button type="button" class="btn sm" onclick={clear} disabled={!doc.ops.length && !sel.invert} data-testid="mask-clear">Clear</button>
    </div>
    <div class="grp">
      <button type="button" class="btn sm icon" onclick={() => zoomAt(1 / 1.4, center().x, center().y)} aria-label="Zoom out" disabled={zoom <= 1}>−</button>
      <button type="button" class="btn sm" onclick={resetView} aria-label={`Fit to screen, now ${Math.round(zoom * 100)} percent`} data-testid="mask-zoom">{Math.round(zoom * 100)}%</button>
      <button type="button" class="btn sm icon" onclick={() => zoomAt(1.4, center().x, center().y)} aria-label="Zoom in"><Icon name="plus" size={13} /></button>
    </div>
  </div>

  <div class="sl">
    <label class="st-field"><span>Brush size: <b data-testid="mask-size">{brush}</b> px</span><input type="range" min="2" max={Math.max(40, Math.round(Math.max(W, H) / 3))} step="1" bind:value={brush} aria-label="Brush size in pixels" disabled={tool === 'box' || tool === 'hand'} /></label>
    <label class="st-field"><span>Soft edge: <b data-testid="mask-feather">{sel.feather}</b> px</span><input type="range" min="0" max="200" step="1" value={sel.feather} oninput={(e) => feather(Number(e.currentTarget.value))} aria-label="Soft edge in pixels" /></label>
  </div>

  <div class="host" bind:this={host}>
    {#if src && W}
      <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
      <div class="view" bind:this={view} tabindex="0" role="group" aria-label="Selection canvas" aria-describedby="mask-help" style:width={`${vw}px`} style:height={`${vh}px`} class:grab={tool === 'hand' || spaceDown} class:cross={tool !== 'hand' && !spaceDown}
        onpointerdown={down} onpointermove={move} onpointerup={up} onpointercancel={cancel} onpointerleave={() => (cursor.on = false)} data-testid="mask-canvas-wrap">
        <div class="world" style:transform={`translate(${pan.x}px, ${pan.y}px) scale(${zoom})`}>
          <img src={contentUrl(src)} alt="" draggable="false" />
          <canvas bind:this={cv} data-testid="mask-overlay" aria-hidden="true"></canvas>
        </div>
        {#if cursor.on && (tool === 'brush' || tool === 'erase') && !spaceDown}
          <i class="ring" class:er={tool === 'erase'} style:left={`${cursor.x}px`} style:top={`${cursor.y}px`} style:width={`${brush * fit * zoom}px`} style:height={`${brush * fit * zoom}px`}></i>
        {/if}
      </div>
    {:else}
      <p class="st-note warn" role="status">Add a picture first. The selection is drawn on it.</p>
    {/if}
  </div>
  <p id="mask-help" class="st-note">Draw with the brush, take parts away with the eraser, or drag a box (hold Alt to take a box away). Scroll or pinch to zoom, hold Space and drag to move.
    Keys: B brush, E eraser, R box, H move, [ and ] size, I invert, Ctrl+Z undo, Ctrl+Shift+Z redo, 0 fit. Everything can also be done with the buttons.</p>

  <div class="st-row stat" data-testid="mask-status">
    <span class="st-badge" class:ok={cover != null && cover > 0}>{cover == null ? '' : cover === 0 ? 'Nothing selected yet' : `About ${cover}% of the picture will change`}</span>
    <span class="tiny muted" role="status">{#if s.maskBusy}Saving…{:else if fresh}Saved{:else if hasSelection(doc)}Not saved yet{/if}</span>
    <span class="grow"></span>
    <button type="button" class="btn" onclick={save} disabled={!hasSelection(doc) || fresh || s.maskBusy} data-testid="mask-save">Save selection</button>
    {#if onclose}<button type="button" class="btn primary" onclick={async () => { await save(); onclose?.(); }} data-testid="mask-done">Done</button>{/if}
  </div>
  {#if s.maskError}<p class="st-note bad" role="alert">{s.maskError}</p>{/if}

  <details class="st-details">
    <summary>Box by numbers</summary>
    <div class="st-in">
      <p class="st-note">Pixels of your picture ({W}×{H}). Useful when you want an exact box, or cannot use a pointer.</p>
      <div class="st-row">
        {#each [['x', 'Left'], ['y', 'Top'], ['w', 'Width'], ['h', 'Height']] as [f, l] (f)}
          <label class="st-field"><span>{l}</span><input class="input num" type="number" min="0" max={f === 'x' || f === 'w' ? W : H} bind:value={numbers[f as 'x']} data-testid={`box-${f}`} /></label>
        {/each}
        <button type="button" class="btn" onclick={addNumbers} data-testid="box-add">Add box</button>
      </div>
    </div>
  </details>
  <span class="sr-only" role="status" aria-live="polite">{live}</span>
</div>

<style>
  .ed { display: grid; gap: 10px; min-width: 0; }
  .tb { display: flex; flex-wrap: wrap; gap: 8px 14px; align-items: center; } .grp { display: inline-flex; gap: 4px; flex-wrap: wrap; align-items: center; }
  .tool { all: unset; box-sizing: border-box; min-height: 36px; padding: 0 10px; border-radius: var(--radius-sm); border: 1px solid var(--border); background: var(--card-bg); cursor: pointer; display: inline-flex; gap: 6px; align-items: center; font-size: 13px; color: var(--fg-2); }
  .tool[aria-checked='true'] { background: var(--fg); color: var(--bg); border-color: var(--fg); } .tool:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; } .tool:hover:not([aria-checked='true']) { background: var(--hover); }
  .btn[aria-pressed='true'] { background: var(--selected); border-color: var(--border-strong); font-weight: 600; }
  @media (pointer: coarse) { .tool, .tb :global(.btn) { min-height: 44px; } .tb :global(.btn.icon) { width: 44px; } }
  .sl { display: flex; flex-wrap: wrap; gap: 8px 20px; } .sl .st-field { min-width: 200px; flex: 1; } input[type='range'] { width: 100%; min-height: 32px; accent-color: var(--accent); }
  .host { display: grid; justify-items: center; min-width: 0; padding: 4px; border-radius: var(--radius); background: repeating-conic-gradient(color-mix(in srgb, var(--fg) 6%, var(--card-bg)) 0% 25%, var(--card-bg) 0% 50%) 0 0 / 18px 18px; border: 1px solid var(--border); }
  .view { position: relative; overflow: hidden; touch-action: none; user-select: none; outline: none; border-radius: 4px; background: #111; }
  .view:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; } .cross { cursor: crosshair; } .grab { cursor: grab; }
  .world { position: absolute; left: 0; top: 0; width: 100%; height: 100%; transform-origin: 0 0; will-change: transform; }
  .world img, .world canvas { position: absolute; inset: 0; width: 100%; height: 100%; display: block; } .world img { object-fit: fill; pointer-events: none; }
  .world canvas { pointer-events: none; image-rendering: auto; }
  .ring { position: absolute; transform: translate(-50%, -50%); border-radius: 50%; border: 1.5px solid #fff; box-shadow: 0 0 0 1px rgba(0, 0, 0, .7), inset 0 0 0 1px rgba(0, 0, 0, .5); pointer-events: none; } .ring.er { border-style: dashed; }
  .stat { align-items: center; } .grow { flex: 1; }
  .num { width: 96px; }
</style>
