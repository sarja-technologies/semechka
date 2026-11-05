<script lang="ts" module>
  export type Rect = { x: number; y: number; w: number; h: number };
</script>

<script lang="ts">
  // The crop frame laid over a picture or video: drag it, drag the edges and corners, or use the arrow keys. The rectangle is kept in pixels of the
  // real file (so what the server cuts is exactly what is drawn); the screen only decides how big a pixel looks. Fills its parent, which must be
  // exactly as big as the media is shown.
  let { w, h, rect = $bindable(null), aspect = null, zoom = 1, even = false, min = 16, onchange }:
    { w: number; h: number; rect: Rect | null; aspect?: number | null; zoom?: number; even?: boolean; min?: number; onchange?: (r: Rect) => void } = $props();

  let host: HTMLDivElement | undefined = $state();
  type Handle = 'move' | 'n' | 's' | 'e' | 'w' | 'ne' | 'nw' | 'se' | 'sw';
  let drag = $state<{ handle: Handle; px: number; py: number; start: Rect; k: number } | null>(null);
  const r = $derived(rect ?? { x: 0, y: 0, w, h });
  const snap = (v: number) => (even ? Math.round(v / 2) * 2 : Math.round(v));
  const clamp = (v: number, lo: number, hi: number) => Math.max(lo, Math.min(hi, v));

  function commit(n: Rect) {
    const out = { x: snap(n.x), y: snap(n.y), w: snap(n.w), h: snap(n.h) };
    out.w = Math.min(out.w, w - out.x); out.h = Math.min(out.h, h - out.y);
    rect = out; onchange?.(out);
  }

  /** Fit a rectangle of the wanted shape around an anchor, inside the frame. */
  function resize(h0: Handle, s: Rect, dx: number, dy: number): Rect {
    let { x, y, w: rw, h: rh } = s;
    const right = s.x + s.w, bottom = s.y + s.h;
    if (h0.includes('e')) rw = clamp(s.w + dx, min, w - s.x);
    if (h0.includes('w')) { const nx = clamp(s.x + dx, 0, right - min); x = nx; rw = right - nx; }
    if (h0.includes('s')) rh = clamp(s.h + dy, min, h - s.y);
    if (h0.includes('n')) { const ny = clamp(s.y + dy, 0, bottom - min); y = ny; rh = bottom - ny; }
    if (aspect) {
      const horizontal = h0 === 'e' || h0 === 'w', vertical = h0 === 'n' || h0 === 's';
      // which dimension leads: the one the person is dragging; corners follow the larger change
      const byWidth = horizontal || (!vertical && Math.abs(dx) * 1 >= Math.abs(dy) * aspect);
      let nw = byWidth ? rw : rh * aspect, nh = byWidth ? rw / aspect : rh;
      // the frame the anchor leaves room for
      const maxW = (h0.includes('w') ? right : w - s.x) , maxH = (h0.includes('n') ? bottom : h - s.y);
      const cx = s.x + s.w / 2, cy = s.y + s.h / 2;
      const roomW = horizontal || !vertical ? maxW : 2 * Math.min(cx, w - cx), roomH = vertical || !horizontal ? maxH : 2 * Math.min(cy, h - cy);
      const k = Math.min(1, roomW / nw, roomH / nh);
      nw *= k; nh *= k;
      if (nw < min) { nw = min; nh = min / aspect; }
      if (nh < min) { nh = min; nw = min * aspect; }
      x = h0.includes('w') ? right - nw : h0 === 'n' || h0 === 's' ? clamp(cx - nw / 2, 0, w - nw) : s.x;
      y = h0.includes('n') ? bottom - nh : h0 === 'e' || h0 === 'w' ? clamp(cy - nh / 2, 0, h - nh) : s.y;
      rw = nw; rh = nh;
    }
    return { x, y, w: rw, h: rh };
  }

  function down(e: PointerEvent, handle: Handle) {
    if (e.button !== 0 || !host) return;
    e.preventDefault(); e.stopPropagation();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    drag = { handle, px: e.clientX, py: e.clientY, start: { ...r }, k: host.getBoundingClientRect().width / w };
  }
  function move(e: PointerEvent) {
    if (!drag) return;
    const dx = (e.clientX - drag.px) / drag.k, dy = (e.clientY - drag.py) / drag.k, s = drag.start;
    if (drag.handle === 'move') commit({ ...s, x: clamp(s.x + dx, 0, w - s.w), y: clamp(s.y + dy, 0, h - s.h) });
    else commit(resize(drag.handle, s, dx, dy));
  }
  const up = () => (drag = null);

  function key(e: KeyboardEvent) {
    const step = e.shiftKey ? 10 : 1;
    const d: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
    const v = d[e.key];
    if (!v) return;
    e.preventDefault(); e.stopPropagation();
    if (e.altKey) commit(resize('se', r, v[0], v[1]));
    else commit({ ...r, x: clamp(r.x + v[0], 0, w - r.w), y: clamp(r.y + v[1], 0, h - r.h) });
  }

  const pct = (v: number, of: number) => `${(v / of) * 100}%`;
  const handles: Handle[] = ['nw', 'n', 'ne', 'e', 'se', 's', 'sw', 'w'];
</script>

<!-- svelte-ignore a11y_no_static_element_interactions -->
<div class="ov" bind:this={host} style:--z={zoom} onpointermove={move} onpointerup={up} onpointercancel={up}>
  <span class="dim" style:left="0" style:top="0" style:right="0" style:height={pct(r.y, h)} aria-hidden="true"></span>
  <span class="dim" style:left="0" style:right="0" style:top={pct(r.y + r.h, h)} style:bottom="0" aria-hidden="true"></span>
  <span class="dim" style:left="0" style:top={pct(r.y, h)} style:height={pct(r.h, h)} style:width={pct(r.x, w)} aria-hidden="true"></span>
  <span class="dim" style:right="0" style:top={pct(r.y, h)} style:height={pct(r.h, h)} style:left={pct(r.x + r.w, w)} aria-hidden="true"></span>
  <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
  <div class="rect" class:drag={!!drag} style:left={pct(r.x, w)} style:top={pct(r.y, h)} style:width={pct(r.w, w)} style:height={pct(r.h, h)}
    role="group" aria-label={`Crop frame, ${Math.round(r.w)} by ${Math.round(r.h)} pixels at ${Math.round(r.x)}, ${Math.round(r.y)}. Arrow keys move it; Alt with arrows resizes.`}
    tabindex="0" onkeydown={key} onpointerdown={(e) => down(e, 'move')}>
    <span class="grid" aria-hidden="true"></span>
    {#each handles as hd (hd)}
      <span class="hd {hd}" onpointerdown={(e) => down(e, hd)} aria-hidden="true"></span>
    {/each}
    <span class="dims" aria-hidden="true">{Math.round(r.w)} × {Math.round(r.h)}</span>
  </div>
</div>

<style>
  .ov { position: absolute; inset: 0; touch-action: none; }
  .dim { position: absolute; background: rgba(8, 9, 10, 0.62); pointer-events: none; }
  .rect { position: absolute; outline: 1.5px solid rgba(255, 255, 255, 0.92); outline-offset: -1px; cursor: move; touch-action: none; }
  .rect:focus-visible { outline: 2px solid var(--m-ring); }
  .grid { position: absolute; inset: 0; pointer-events: none; opacity: 0; transition: opacity var(--fast) var(--ease);
    background: linear-gradient(to right, transparent 33.2%, rgba(255,255,255,.35) 33.3%, transparent 33.5%, transparent 66.5%, rgba(255,255,255,.35) 66.6%, transparent 66.8%),
      linear-gradient(to bottom, transparent 33.2%, rgba(255,255,255,.35) 33.3%, transparent 33.5%, transparent 66.5%, rgba(255,255,255,.35) 66.6%, transparent 66.8%); }
  .rect:hover .grid, .rect.drag .grid, .rect:focus-visible .grid { opacity: 1; }
  .hd { position: absolute; width: calc(14px / var(--z)); height: calc(14px / var(--z)); margin: calc(-7px / var(--z)); background: #fff; border-radius: calc(3px / var(--z));
    box-shadow: 0 0 0 calc(1px / var(--z)) rgba(0, 0, 0, 0.45); touch-action: none; }
  .hd::after { content: ''; position: absolute; inset: calc(-10px / var(--z)); }
  .nw { left: 0; top: 0; cursor: nwse-resize; } .n { left: 50%; top: 0; cursor: ns-resize; } .ne { left: 100%; top: 0; cursor: nesw-resize; }
  .e { left: 100%; top: 50%; cursor: ew-resize; } .se { left: 100%; top: 100%; cursor: nwse-resize; } .s { left: 50%; top: 100%; cursor: ns-resize; }
  .sw { left: 0; top: 100%; cursor: nesw-resize; } .w { left: 0; top: 50%; cursor: ew-resize; }
  .n, .s { width: calc(26px / var(--z)); margin-left: calc(-13px / var(--z)); } .e, .w { height: calc(26px / var(--z)); margin-top: calc(-13px / var(--z)); }
  .dims { position: absolute; left: 6px; top: 6px; transform: scale(calc(1 / var(--z))); transform-origin: top left; white-space: nowrap; pointer-events: none;
    font-size: 11px; line-height: 1; padding: 4px 7px; border-radius: 6px; background: rgba(14, 15, 16, 0.82); color: #f2f2f2; font-variant-numeric: tabular-nums; }
  @media (pointer: coarse) { .hd { width: calc(22px / var(--z)); height: calc(22px / var(--z)); margin: calc(-11px / var(--z)); } }
</style>
