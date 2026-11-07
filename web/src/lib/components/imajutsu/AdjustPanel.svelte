<script lang="ts">
  // Panel: Adjust. Photographic adjustments (exposure, colour, crop, turn ...). This is NOT AI: nothing is generated. The preview is rendered by the
  // server with the same code as the final file, and "Apply" makes a new picture; the original is never touched.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import PanelCard from './PanelCard.svelte';
  import { openViewer } from '$lib/media/viewer.svelte';
  import { GROUPS, cleanRecipe, describe, isNeutral, sizeAfter, slidersFor } from '$lib/imajutsu/adjust';
  import type { Asset } from '$lib/media/assets';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  const t = $derived(s.adjustTarget), r = $derived(s.recipe), neutral = $derived(isNeutral(r)), SL = $derived(slidersFor(s.caps));
  const after = $derived(t?.width && t?.height ? sizeAfter(t.width, t.height, cleanRecipe(r)) : null);
  const choices = $derived.by(() => {
    const out = new Map<string, Asset>();
    if (s.source) out.set(s.source.id, s.source);
    for (const id of s.copies) { const a = s.asset(id); if (a) out.set(a.id, a); }
    for (const id of s.resultIds) { const a = s.asset(id); if (a) out.set(a.id, a); }
    if (t) out.set(t.id, t);
    return [...out.values()];
  });
  const ASPECTS = [{ id: 'free', label: 'Free', v: null }, { id: 'orig', label: 'Original', v: 'orig' }, { id: '1:1', label: '1:1', v: 1 }, { id: '4:5', label: '4:5', v: 4 / 5 }, { id: '3:4', label: '3:4', v: 3 / 4 }, { id: '16:9', label: '16:9', v: 16 / 9 }, { id: '9:16', label: '9:16', v: 9 / 16 }];
  let aspId = $state('free');
  function setAspect(a: (typeof ASPECTS)[number]) { aspId = a.id; s.cropAspect = a.v === 'orig' ? (t?.width && t?.height ? t.width / t.height : null) : (a.v as number | null); }
  const val = (k: string) => (r as any)[k] ?? 0;
  const show = (k: string) => { const sl = SL.find((x) => x.key === k)!; return `${Math.round(val(k) * sl.show * 10) / 10}${sl.unit}`; };
  const name = (a: Asset) => a.origin === 'generated' ? 'Generated picture' : a.origin === 'adjusted' ? 'Adjusted copy' : a.name || 'Picture';
  // the crop is measured on the turned picture, so turning or mirroring starts the crop again
  function turn(d: number) { s.setRecipe((x) => { x.rotate = (((x.rotate ?? 0) + d) % 360 + 360) % 360; delete x.crop; }); }
  function out(which: 'width' | 'height', v: number) {
    s.setRecipe((x) => {
      if (!(v > 0)) { delete x.output; return; }
      const base = after ?? { width: t?.width ?? 1, height: t?.height ?? 1 }, ratio = base.width / base.height;
      x.output = which === 'width' ? { width: Math.round(v), height: Math.round(v / ratio) } : { width: Math.round(v * ratio), height: Math.round(v) };
    });
  }
</script>

<PanelCard id="adjust" title="Adjust the picture" {n} hint="Photographic changes only: light, colour, crop. Nothing is generated.">
  {#snippet head()}<span class="st-badge info" title="No AI model runs here">Not AI</span>{/snippet}
  {#if !t}
    <p class="st-note" data-testid="adjust-empty">Add a picture, or press Adjust on a result, to work on it here.</p>
  {:else}
    <div class="tgt" data-testid="adjust-target">
      <button type="button" class="th" onclick={() => openViewer(t, { siblings: [t] })} aria-label="Open the picture being adjusted"><MediaThumb id={t.id} kind="image" name={name(t)} ratio="1 / 1" /></button>
      <div class="tx"><label class="st-field"><span>Picture to adjust</span>
        <select class="input" value={t.id} onchange={(e) => { const a = choices.find((x) => x.id === e.currentTarget.value); if (a) s.adjustPicture(a); }} data-testid="adjust-pick">
          {#each choices as a (a.id)}<option value={a.id}>{name(a)} · {a.width}×{a.height}</option>{/each}
        </select></label>
        <span class="tiny muted" data-testid="adjust-dims">{t.width}×{t.height}{after && (after.width !== t.width || after.height !== t.height) ? ` → ${after.width}×${after.height}` : ''}</span></div>
    </div>

    {#each GROUPS as g (g.id)}
      <fieldset class="grp"><legend>{g.label}</legend>
        {#each SL.filter((x) => x.group === g.id) as sl (sl.key)}
          <div class="sl">
            <label for={`adj-${sl.key}`}>{sl.label}</label>
            <input id={`adj-${sl.key}`} type="range" min={sl.min} max={sl.max} step={sl.step} value={val(sl.key)} data-testid={`adj-${sl.key}`} aria-describedby={`adj-${sl.key}-h`}
              oninput={(e) => { const v = Number(e.currentTarget.value); s.setRecipe((x) => { (x as any)[sl.key] = v; }); }} ondblclick={() => s.setRecipe((x) => { delete (x as any)[sl.key]; })} />
            <output for={`adj-${sl.key}`} data-testid={`adj-${sl.key}-v`}>{show(sl.key)}</output>
            <span id={`adj-${sl.key}-h`} class="sr-only">{sl.hint} Double-click to reset.</span>
          </div>
        {/each}
      </fieldset>
    {/each}

    <fieldset class="grp"><legend>Crop and turn</legend>
      <div class="st-row">
        <button type="button" class="btn" onclick={() => { s.cropOn = !s.cropOn; if (s.cropOn) s.stage = 'adjust'; }} aria-pressed={s.cropOn} data-testid="crop-toggle"><Icon name="crop" size={15} /> {s.cropOn ? 'Done cropping' : r.crop ? 'Change crop' : 'Crop'}</button>
        {#if r.crop}<button type="button" class="btn ghost" onclick={() => s.setRecipe((x) => { delete x.crop; })}>Remove crop</button>{/if}
        <button type="button" class="btn" onclick={() => turn(-90)} aria-label="Turn left 90 degrees"><Icon name="rotate" size={15} /> Left</button>
        <button type="button" class="btn" onclick={() => turn(90)} aria-label="Turn right 90 degrees"><Icon name="rotate" size={15} /> Right</button>
        <button type="button" class="btn" aria-pressed={!!r.flip} onclick={() => s.setRecipe((x) => { x.flip = !x.flip; delete x.crop; })}><Icon name="flip" size={15} /> Mirror</button>
      </div>
      {#if s.cropOn}
        <div class="st-seg" role="radiogroup" aria-label="Crop shape" data-testid="crop-aspects">
          {#each ASPECTS as a (a.id)}<button type="button" role="radio" aria-checked={aspId === a.id} onclick={() => setAspect(a)}>{a.label}</button>{/each}
        </div>
        <p class="st-note">Drag the frame or its edges. With the frame selected, arrow keys move it and Alt with the arrows resizes it.</p>
      {/if}
      {#if r.crop}<p class="st-note" data-testid="crop-readout">Crop: {r.crop.w}×{r.crop.h} from {r.crop.x}, {r.crop.y}</p>{/if}
      <div class="sl"><label for="adj-straighten">Straighten</label>
        <input id="adj-straighten" type="range" min="-10" max="10" step="0.1" value={r.straighten ?? 0} data-testid="adj-straighten" oninput={(e) => { const v = Number(e.currentTarget.value); s.setRecipe((x) => { x.straighten = v; }); }} ondblclick={() => s.setRecipe((x) => { delete x.straighten; })} />
        <output for="adj-straighten">{(r.straighten ?? 0).toFixed(1)}°</output></div>
    </fieldset>

    <fieldset class="grp"><legend>Output size</legend>
      <div class="st-row">
        <label class="st-field"><span>Width (px)</span><input class="input num" type="number" min="16" value={r.output?.width ?? ''} placeholder={String(after?.width ?? '')} onchange={(e) => out('width', Number(e.currentTarget.value))} data-testid="adj-out-w" /></label>
        <label class="st-field"><span>Height (px)</span><input class="input num" type="number" min="16" value={r.output?.height ?? ''} placeholder={String(after?.height ?? '')} onchange={(e) => out('height', Number(e.currentTarget.value))} data-testid="adj-out-h" /></label>
        {#if r.output}<button type="button" class="btn ghost" onclick={() => s.setRecipe((x) => { delete x.output; })}>Same size</button>{/if}
      </div>
      <p class="st-note">Changing the size here only scales the picture. It adds size, not detail.</p>
    </fieldset>

    <div class="st-foot">
      <button type="button" class="btn" onclick={() => { s.resetRecipe(); s.cropOn = false; }} disabled={neutral} data-testid="adjust-reset">Reset</button>
      <span class="grow tiny muted" data-testid="adjust-summary">{neutral ? 'No changes yet' : describe(r).join(' · ')}</span>
      <button type="button" class="btn primary" onclick={() => s.applyAdjust()} disabled={neutral || s.adjustBusy.busy} data-testid="adjust-apply">{s.adjustBusy.busy ? 'Making the picture…' : 'Apply: make a new picture'}</button>
    </div>
    <p class="st-note">Apply saves a new picture in your Library. <strong>The original is not changed.</strong></p>
    {#if s.adjustBusy.error}<p class="st-note bad" role="alert">{s.adjustBusy.error}</p>{/if}
    {#if s.adjustBusy.done}
      {@const d = s.adjustBusy.done}
      <div class="done" role="status" data-testid="adjust-done"><MediaThumb id={d.id} kind="image" name="Adjusted copy" ratio="1 / 1" />
        <div><strong>New picture made</strong><span class="tiny muted" data-testid="adjust-done-dims">{d.width}×{d.height}</span>
          <div class="st-row"><button type="button" class="btn sm" onclick={() => openViewer(d, { siblings: [d] })}>Open</button><button type="button" class="btn sm" onclick={() => s.useAsSource(d)}>Use as source</button></div></div></div>
    {/if}
  {/if}
</PanelCard>

<style>
  .tgt { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; align-items: center; } .th { all: unset; display: block; width: 64px; height: 64px; border-radius: 10px; overflow: hidden; border: 1px solid var(--border); cursor: zoom-in; } .th:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .tx { display: grid; gap: 4px; min-width: 0; }
  .grp { margin: 0; padding: 0; border: 0; display: grid; gap: 6px; min-width: 0; padding-top: var(--s2); border-top: 1px solid var(--border); } legend { font-size: 13px; font-weight: 600; padding: 0; float: left; width: 100%; margin-bottom: 4px; } legend + * { clear: both; }
  .sl { display: grid; grid-template-columns: 92px minmax(0, 1fr) 54px; align-items: center; gap: 8px; font-size: 13px; } .sl output { text-align: right; font-variant-numeric: tabular-nums; color: var(--fg-2); font-size: 12.5px; }
  input[type='range'] { width: 100%; min-height: 32px; accent-color: var(--accent); } @media (pointer: coarse) { input[type='range'] { min-height: 44px; } }
  .num { width: 110px; } .grow { flex: 1; min-width: 12ch; }
  .done { display: grid; grid-template-columns: 64px minmax(0, 1fr); gap: 12px; align-items: center; padding: 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--success-soft); } .done > :global(.th) { border-radius: 8px; overflow: hidden; width: 64px; height: 64px; } .done div div { display: grid; gap: 4px; }
  @media (max-width: 560px) { .sl { grid-template-columns: 78px minmax(0, 1fr) 48px; } }
</style>
