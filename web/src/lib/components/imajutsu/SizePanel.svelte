<script lang="ts">
  // Panel: Size. The shape and size you want, and what the model really makes. The readout is the server's plan: the model works at its own
  // native size, and anything bigger is made by scaling afterwards, which adds size but no detail.
  import Icon from '$lib/components/Icon.svelte';
  import PanelCard from './PanelCard.svelte';
  import { ASPECTS, QUALITY, fmtDims, fmtMb, fmtSecs } from '$lib/imajutsu/spec';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  const sp = $derived(s.spec), p = $derived(s.plan), key = $derived(s.key), src = $derived(s.source);
  const list = $derived((s.aspects ?? ASPECTS).filter((a) => a.id !== 'custom' && a.id !== 'source').map((a) => ({ id: a.id, label: ASPECTS.find((x) => x.id === a.id)?.label ?? a.label, w: a.w, h: a.h })));
  const chips = $derived([...(s.needs.source && src && key !== 'outpaint' && key !== 'upscale' ? [{ id: 'source', label: 'Same shape as the picture', w: null, h: null }] : []), ...list, { id: 'custom', label: 'Custom', w: null, h: null }]);
  const nat = $derived(p?.native), out = $derived(p?.output), sc = $derived(out?.scaling);
  const est = $derived(p?.estimate), mem = $derived(est?.memory), tm = $derived(est?.seconds);
  const custom = $derived(sp.aspect.id === 'custom');
  const up = $derived(key === 'upscale');
  const factors = $derived(up ? (src?.width && src?.height ? [2, 3, 4].map((k) => ({ k, w: src.width! * k, h: src.height! * k })) : []) : nat ? [1, 1.5, 2, 3].map((k) => ({ k, w: Math.round(nat.width * k), h: Math.round(nat.height * k) })) : []);

  function pick(id: string, w: number | null, h: number | null) {
    s.edit((x) => {
      if (id === 'custom') {
        const ow = out?.width ?? nat?.width ?? 1024, oh = out?.height ?? nat?.height ?? 1024;
        x.aspect = { id: 'custom', w: ow, h: oh }; x.size.output = { width: ow, height: oh };
      } else { x.aspect = { id, w, h }; x.size.output = null; x.size.native = null; }
      if (key === 'outpaint') x.outpaint = { ...x.outpaint, target: null, pad: null };
    });
  }
  const lock = $state({ on: true });
  function setDim(which: 'w' | 'h', v: number) {
    if (!(v >= 16)) return;
    s.edit((x) => {
      const cw = x.size.output?.width ?? x.aspect.w ?? v, ch = x.size.output?.height ?? x.aspect.h ?? v, r = cw / ch;
      const w = which === 'w' ? Math.round(v) : lock.on ? Math.round(v * r) : cw, h = which === 'h' ? Math.round(v) : lock.on ? Math.round(v / r) : ch;
      x.aspect = { id: 'custom', w, h }; x.size.output = { width: w, height: h };
    });
  }
  function setOutput(w: number, h: number, factor: number) { s.edit((x) => { x.size.output = factor === 1 && !up ? null : { width: w, height: h }; }); }
  const anchors = [0, 0.5, 1];
  const AN = ['top left', 'top', 'top right', 'left', 'centre', 'right', 'bottom left', 'bottom', 'bottom right'];
  const known = (b?: string) => b === 'measured' || b === 'extrapolated';
  const heading = $derived(up ? 'Make it this big' : key === 'outpaint' ? 'Size of the new picture' : 'Shape and size');
</script>

<PanelCard id="size" title={heading} {n} hint={up ? 'Scaling, not a model: it adds size but no new detail.' : key === 'outpaint' ? 'Pick a bigger shape than your picture; the model fills the added area.' : 'Pick a shape. The model makes it at its own best size; anything bigger is scaled up afterwards.'}>
  {#if !up}
    <div class="st-seg" role="radiogroup" aria-label="Shape" data-testid="aspects">
      {#each chips as c (c.id)}<button type="button" role="radio" aria-checked={sp.aspect.id === c.id} data-aspect={c.id} onclick={() => pick(c.id, c.w, c.h)}>{c.label}</button>{/each}
    </div>
  {/if}

  {#if custom || up}
    <div class="st-row" data-testid="custom-size">
      <label class="st-field"><span>Width (px)</span><input class="input w" type="number" inputmode="numeric" min="64" max="8192" value={sp.size.output?.width ?? ''} onchange={(e) => setDim('w', Number(e.currentTarget.value))} data-testid="size-w" /></label>
      <label class="st-field"><span>Height (px)</span><input class="input w" type="number" inputmode="numeric" min="64" max="8192" value={sp.size.output?.height ?? ''} onchange={(e) => setDim('h', Number(e.currentTarget.value))} data-testid="size-h" /></label>
      <label class="lock"><input type="checkbox" bind:checked={lock.on} /> <Icon name="lock" size={14} /> Keep the shape</label>
    </div>
  {/if}

  {#if factors.length && !(custom && !up)}
    <div class="st-sec" style="padding:0;border:0">
      <h3 id="out-h">{up ? 'How much bigger' : 'Output size'}</h3>
      <div class="st-seg" role="radiogroup" aria-labelledby="out-h" data-testid="scales">
        {#each factors as f (f.k)}
          {@const on = sp.size.output ? sp.size.output.width === f.w && sp.size.output.height === f.h : !up && f.k === 1}
          <button type="button" role="radio" aria-checked={on} onclick={() => setOutput(f.w, f.h, f.k)}>{up ? `${f.k}×` : f.k === 1 ? 'As the model makes it' : `${f.k}× bigger`} <span class="tiny muted">{f.w}×{f.h}</span></button>
        {/each}
      </div>
      <p class="st-note">{up ? 'The picture is scaled with a sharp filter. This is not a model, so no new detail appears.' : 'The model always works at its own size. A bigger output is that picture scaled up afterwards, with no new detail.'}</p>
    </div>
  {/if}

  {#if key === 'outpaint'}
    <div class="st-sec" style="padding:0;border:0">
      <h3 id="anc-h">Where your picture sits</h3>
      <div class="anc" role="radiogroup" aria-labelledby="anc-h" data-testid="anchors">
        {#each anchors as ay, r (ay)}{#each anchors as ax, c (ax)}
          {@const on = sp.outpaint.anchor.x === ax && sp.outpaint.anchor.y === ay}
          <button type="button" role="radio" aria-checked={on} aria-label={`Put your picture at the ${AN[r * 3 + c]}`} onclick={() => s.edit((x) => { x.outpaint = { ...x.outpaint, anchor: { x: ax, y: ay }, pad: null }; })}><i></i></button>
        {/each}{/each}
      </div>
      <p class="st-note">The new area grows on the other sides.</p>
    </div>
  {/if}

  {#if s.needs.model}
    <div class="st-sec" style="padding:0;border:0">
      <h3 id="q-h">Quality</h3>
      <div class="st-seg" role="radiogroup" aria-labelledby="q-h" data-testid="quality">
        {#each QUALITY as q (q.id)}<button type="button" role="radio" aria-checked={sp.quality === q.id} title={q.note} onclick={() => s.edit((x) => { x.quality = q.id; })}>{q.label}</button>{/each}
      </div>
      <p class="st-note">{QUALITY.find((q) => q.id === sp.quality)?.note}{p?.steps ? ` The plan uses ${p.steps} steps.` : ''}</p>
    </div>
  {/if}

  <section class="pr" aria-label="Size pipeline" data-testid="pipeline" aria-busy={s.planBusy}>
    {#if !p || (!nat && !out)}
      <p class="st-note" role="status">{s.planError || (s.planBusy ? 'Working out the sizes…' : 'The sizes appear here once the server has worked them out.')}</p>
    {:else}
      <ol class="pipe">
        {#if !up}<li data-testid="pipe-native" class="proc"><span class="k"><i>1</i>Model makes</span><strong>{fmtDims(nat)}</strong><span class="sub">its native size</span></li>{/if}
        <li data-testid="pipe-output"><span class="k"><i>{up ? 1 : 2}</i>You get</span><strong>{fmtDims(out)}</strong><span class="sub">{sc === 'upscale' ? 'scaled up, no new detail' : sc === 'downscale' ? 'scaled down' : sc === 'none' ? 'as made' : ''}</span></li>
      </ol>
      {#if nat?.why && !up}<p class="st-note" data-testid="pipe-why"><Icon name="info" size={13} /> {nat.why}</p>{/if}
      {#if out?.note}<p class="st-note" class:warn={sc === 'upscale'} data-testid="pipe-output-note">{out.note}</p>{/if}
      {#if p.outpaint}<p class="st-note" data-testid="pipe-outpaint">New canvas {fmtDims(p.outpaint.canvas)}: {p.outpaint.pad.left}px added on the left, {p.outpaint.pad.right} right, {p.outpaint.pad.top} top, {p.outpaint.pad.bottom} bottom.</p>{/if}
      <p class="st-note" data-testid="pipe-estimate">
        {#if mem && known(mem.basis) && mem.peak_mb}Memory needed: about {fmtMb(mem.peak_mb)} ({mem.basis === 'measured' ? 'measured on this machine' : 'extrapolated from measured runs'}).{/if}
        {#if tm && known(tm.basis) && tm.estimate} Expected time: about {fmtSecs(tm.estimate)} ({tm.basis === 'measured' ? 'measured on this machine' : 'extrapolated from measured runs'}).{/if}
        {#if !(mem && known(mem.basis) && mem.peak_mb) && !(tm && known(tm.basis) && tm.estimate)}{up ? 'Scaling takes a moment and uses little memory.' : p.model?.seconds_hint ? `Time and memory have not been measured at this size, so no estimate is shown. The benchmark note for this model: ${p.model.seconds_hint}.` : 'Time and memory have not been measured at this size, so no estimate is shown.'}{/if}
      </p>
      {#each p.warnings ?? [] as w}<p class="st-note warn" role="status">{w}</p>{/each}
      {#each p.errors ?? [] as w}<p class="st-note bad" role="alert">{w}</p>{/each}
    {/if}
  </section>
</PanelCard>

<style>
  .w { width: 110px; } .lock { display: inline-flex; align-items: center; gap: 6px; font-size: 13px; min-height: 34px; cursor: pointer; color: var(--fg-2); }
  .anc { display: inline-grid; grid-template-columns: repeat(3, 44px); gap: 4px; }
  .anc button { all: unset; box-sizing: border-box; width: 44px; height: 44px; border-radius: 8px; border: 1px solid var(--border); background: var(--card-bg); cursor: pointer; display: grid; place-items: center; } .anc button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .anc i { width: 14px; height: 14px; border-radius: 3px; border: 1.5px solid var(--border-strong); } .anc button[aria-checked='true'] { border-color: var(--fg); background: var(--selected); } .anc button[aria-checked='true'] i { background: var(--fg); border-color: var(--fg); }
  .pr { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--m-card); }
  .pipe { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 6px; }
  .pipe li { display: grid; gap: 1px; padding: 8px 10px; border-radius: 9px; background: var(--card-bg); border: 1px solid var(--border); align-content: start; min-width: 0; } .pipe li.proc { border-color: var(--border-strong); }
  .k { font-size: 11px; color: var(--fg-muted); font-weight: 500; display: flex; align-items: center; gap: 6px; }
  .k i { font-style: normal; width: 16px; height: 16px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 600; border: 1px solid var(--border-strong); color: var(--fg-2); flex: none; }
  strong { font-size: 14px; font-variant-numeric: tabular-nums; } .sub { font-size: 11.5px; color: var(--fg-muted); min-height: 1em; }
  .st-note :global(svg) { vertical-align: -2px; }
</style>
