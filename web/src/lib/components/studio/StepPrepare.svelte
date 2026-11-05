<script lang="ts">
  // Step 2: shape the video before it goes to the model: trim, crop, picture shape, size. Everything here is a decision stored in the project; the
  // original file is never touched. "Prepare" makes the real copy the model will receive.
  import Icon from '$lib/components/Icon.svelte';
  import FramingPreview from './FramingPreview.svelte';
  import PipelineReadout from './PipelineReadout.svelte';
  import { fmtDuration, fmtSize } from '$lib/media/assets';
  import { openViewer } from '$lib/media/viewer.svelte';
  import { ASPECTS, RESOLUTIONS, TREATMENTS, defaultSpec, fitInside, framing, fullRect, isFull, ratioWords, targetRatio } from '$lib/studio/spec';
  import type { Studio } from '$lib/studio/store.svelte';

  let { s }: { s: Studio } = $props();
  const f = $derived(s.spec.frame), r = $derived(s.spec.resolution);
  const sw = $derived(s.sw), sh = $derived(s.sh);
  const fr = $derived(framing(s.spec, sw || 16, sh || 9));
  const frameRatio = $derived(fr.ratio);
  const trimmed = $derived(!!s.spec.trim);
  const tool = $derived(s.tool);

  const open = (t: NonNullable<typeof s.tool>) => (s.tool = s.tool === t ? null : t);

  // ---- shape ---------------------------------------------------------------------------------------------------------------
  function setAspect(id: string) {
    const R = ASPECTS.find((a) => a.id === id)?.ratio ?? null;
    s.edit((sp) => {
      sp.frame.aspect = id;
      if (id === 'custom') sp.frame.ratio = sp.frame.ratio ?? Math.round(frameRatio * 1000) / 1000;
      const ratio = id === 'custom' ? sp.frame.ratio : R;
      if (ratio && sp.frame.treatment === 'crop') sp.crop = fitInside(sp.crop ?? fullRect(sw, sh), ratio);
      if (id === 'original' && sp.frame.treatment !== 'crop') sp.frame.treatment = 'crop';
    });
  }
  function applyCustom() {
    const ratio = customW / customH;
    if (!isFinite(ratio) || ratio <= 0) return;
    s.edit((sp) => { sp.frame.ratio = Math.round(ratio * 1000) / 1000; if (sp.frame.treatment === 'crop') sp.crop = fitInside(sp.crop ?? fullRect(sw, sh), sp.frame.ratio); });
  }
  let customW = $state(16), customH = $state(9);
  $effect(() => { if (f.aspect === 'custom' && f.ratio) { const g = reduce(f.ratio); customW = g[0]; customH = g[1]; } });
  function reduce(x: number): [number, number] { for (let d = 1; d <= 24; d++) { const n = x * d; if (Math.abs(n - Math.round(n)) < 0.01) return [Math.round(n), d]; } return [Math.round(x * 100) / 100, 1]; }
  function setTreatment(t: 'crop' | 'pad' | 'stretch') {
    s.edit((sp) => {
      const R = targetRatio(sp.frame);
      const was = sp.frame.treatment;
      sp.frame.treatment = t;
      if (t === 'crop' && R) sp.crop = fitInside(sp.crop ?? fullRect(sw, sh), R);
      // a rectangle that only existed to make the shape is dropped, so Pad and Stretch start from the whole picture
      else if (was === 'crop' && t !== 'crop' && R && sp.crop && Math.abs(sp.crop.w / sp.crop.h - R) < 0.02) sp.crop = null;
    });
  }

  // ---- crop numbers ----------------------------------------------------------------------------------------------------------
  const cr = $derived(s.spec.crop ?? fullRect(sw, sh));
  function setCrop(k: 'x' | 'y' | 'w' | 'h', v: number) {
    if (!isFinite(v)) return;
    const n = { ...cr, [k]: Math.round(v) };
    n.w = Math.max(16, Math.min(n.w, sw - n.x)); n.h = Math.max(16, Math.min(n.h, sh - n.y)); n.x = Math.max(0, Math.min(n.x, sw - n.w)); n.y = Math.max(0, Math.min(n.y, sh - n.h));
    s.edit((sp) => { sp.crop = isFull(n, sw, sh) ? null : n; });
  }

  // ---- size --------------------------------------------------------------------------------------------------------------------
  function setPreset(id: string) {
    s.edit((sp) => {
      sp.resolution.preset = id;
      if (id === 'custom') { const o = s.plan?.output?.requested; sp.resolution.width = sp.resolution.width ?? o?.width ?? s.plan?.prepared?.width ?? sw; sp.resolution.height = sp.resolution.height ?? o?.height ?? s.plan?.prepared?.height ?? sh; }
    });
  }
  const even = (v: number) => Math.max(2, Math.round(v / 2) * 2);
  function setSize(which: 'width' | 'height', v: number) {
    if (!isFinite(v) || v < 2) return;
    s.edit((sp) => {
      sp.resolution[which] = even(v);
      if (sp.resolution.lock_aspect) {
        const k = frameRatio;
        if (which === 'width') sp.resolution.height = even(v / k); else sp.resolution.width = even(v * k);
      }
    });
  }

  // ---- reset -----------------------------------------------------------------------------------------------------------------------
  let armed = $state(false), armT: ReturnType<typeof setTimeout> | undefined;
  const changed = $derived(trimmed || !!s.spec.crop || f.aspect !== 'original' || r.preset !== 'original');
  function reset() {
    if (!armed) { armed = true; clearTimeout(armT); armT = setTimeout(() => (armed = false), 4000); return; }
    armed = false; clearTimeout(armT);
    const d = defaultSpec();
    s.edit((sp) => { sp.trim = null; sp.crop = null; sp.frame = d.frame; sp.resolution = d.resolution; });
    s.tool = null;
  }

  const prepared = $derived(s.prep.asset);
  const sources = $derived(s.plan?.trim);
</script>

<div class="st-h"><h2>Prepare the video</h2><p>Cut it to the part you need, choose the shape and size. Your original video stays exactly as it is; this makes a copy.</p></div>

<div class="tb" role="toolbar" aria-label="Prepare tools">
  <div class="st-seg">
    <button type="button" aria-pressed={tool === 'trim'} onclick={() => open('trim')}><Icon name="scissors" size={14} /> Trim</button>
    <button type="button" aria-pressed={tool === 'crop'} onclick={() => open('crop')}><Icon name="crop" size={14} /> Crop</button>
    <button type="button" aria-pressed={tool === 'resolution'} onclick={() => open('resolution')}><Icon name="sliders" size={14} /> Resolution</button>
    <button type="button" aria-pressed={tool === 'aspect'} onclick={() => open('aspect')}><Icon name="expand" size={14} /> Aspect ratio</button>
    <button type="button" class="rs" class:armed onclick={reset} disabled={!changed}><Icon name="undo" size={14} /> {armed ? 'Press again to reset' : 'Reset'}</button>
  </div>
</div>

<div class="body">
  {#if tool === 'trim'}
    <section class="st-sec" aria-label="Trim" data-testid="tool-trim">
      <h3>Trim</h3>
      <p class="st-note">Drag the white handles on the timeline under the video, or type exact start and end times there.</p>
      <dl class="cmp">
        <div><dt>Original</dt><dd data-testid="trim-original">{fmtDuration(s.duration, true)}</dd></div>
        <div><dt>Selected</dt><dd data-testid="trim-selected">{fmtDuration(s.keptSeconds, true)}</dd></div>
        <div><dt>From</dt><dd>{fmtDuration(s.trimStart, true)}</dd></div>
        <div><dt>To</dt><dd>{fmtDuration(s.trimEnd, true)}</dd></div>
      </dl>
      {#if s.maxSeconds}<p class="st-note" class:warn={s.usesVideo && s.keptSeconds > s.maxSeconds + 0.01}>Maximum for the chosen model: {s.maxSeconds} s.</p>{/if}
      <div class="st-row"><button type="button" class="btn sm" disabled={!trimmed} onclick={() => s.edit((sp) => { sp.trim = null; })}><Icon name="undo" size={13} /> Use the whole video</button></div>
    </section>
  {:else if tool === 'crop'}
    <section class="st-sec" aria-label="Crop" data-testid="tool-crop">
      <h3>Crop</h3>
      <p class="st-note">Drag the frame on the video, or its edges and corners. Arrow keys move it when it is focused; Alt with arrows resizes it. Measurements are in pixels of the picture as you see it.</p>
      {#if f.aspect !== 'original' && f.treatment === 'crop'}
        <label class="chk"><input type="checkbox" checked={f.lock} onchange={(e) => s.edit((sp) => { sp.frame.lock = e.currentTarget.checked; })} /> Keep the {ratioWords(targetRatio(f) ?? frameRatio)} shape while resizing</label>
      {:else}
        <p class="st-note">The frame is free. To crop to a fixed shape, choose one under <button type="button" class="linkish" onclick={() => (s.tool = 'aspect')}>Aspect ratio</button>.</p>
      {/if}
      <div class="nums">
        {#each [['x', 'Left'], ['y', 'Top'], ['w', 'Width'], ['h', 'Height']] as [k, l] (k)}
          <label class="st-field"><span>{l}</span><input class="input" type="number" inputmode="numeric" min="0" step="2" value={Math.round(cr[k as 'x'])} onchange={(e) => setCrop(k as 'x', Number(e.currentTarget.value))} aria-label={`Crop ${l.toLowerCase()} in pixels`} /></label>
        {/each}
      </div>
      <div class="st-row"><button type="button" class="btn sm" disabled={!s.spec.crop} onclick={() => s.edit((sp) => { sp.crop = null; })}><Icon name="undo" size={13} /> Remove crop</button></div>
      <FramingPreview {s} />
    </section>
  {:else if tool === 'aspect'}
    <section class="st-sec" aria-label="Aspect ratio" data-testid="tool-aspect">
      <h3>Aspect ratio</h3>
      <div class="st-seg" role="radiogroup" aria-label="Aspect ratio">
        {#each ASPECTS as a (a.id)}<button type="button" role="radio" aria-checked={f.aspect === a.id} onclick={() => setAspect(a.id)}>{a.label}</button>{/each}
      </div>
      {#if f.aspect === 'custom'}
        <div class="st-row">
          <label class="st-field"><span>Width</span><input class="input narrow" type="number" min="1" step="1" value={customW} onchange={(e) => { customW = Number(e.currentTarget.value); applyCustom(); }} aria-label="Ratio width" /></label>
          <span class="colon" aria-hidden="true">:</span>
          <label class="st-field"><span>Height</span><input class="input narrow" type="number" min="1" step="1" value={customH} onchange={(e) => { customH = Number(e.currentTarget.value); applyCustom(); }} aria-label="Ratio height" /></label>
        </div>
      {/if}
      {#if f.aspect !== 'original'}
        <h3>When the shape changes</h3>
        <div class="st-seg" role="radiogroup" aria-label="Treatment">
          {#each TREATMENTS as t (t.id)}<button type="button" role="radio" aria-checked={f.treatment === t.id} onclick={() => setTreatment(t.id)}>{t.label}{#if t.id === 'stretch'}<span class="st-badge warn">distorts</span>{/if}</button>{/each}
        </div>
        <p class="st-note" class:warn={f.treatment === 'stretch'} data-testid="treatment-note">{TREATMENTS.find((t) => t.id === f.treatment)?.note}</p>
      {:else}
        <p class="st-note">The video keeps its own shape ({ratioWords(sw / (sh || 1))}).</p>
      {/if}
      <FramingPreview {s} />
    </section>
  {:else if tool === 'resolution'}
    <section class="st-sec" aria-label="Resolution" data-testid="tool-resolution">
      <h3>Resolution</h3>
      <p class="st-note">The size you want back. The model works at sizes it supports, so the picture below shows what really happens.</p>
      <div class="st-seg" role="radiogroup" aria-label="Resolution">
        {#each RESOLUTIONS as p (p.id)}<button type="button" role="radio" aria-checked={r.preset === p.id} onclick={() => setPreset(p.id)}>{p.label}</button>{/each}
      </div>
      {#if r.preset === 'custom'}
        <div class="st-row">
          <label class="st-field"><span>Width (px)</span><input class="input narrow" type="number" min="2" step="2" value={r.width ?? ''} onchange={(e) => setSize('width', Number(e.currentTarget.value))} aria-label="Output width in pixels" /></label>
          <span class="colon" aria-hidden="true">×</span>
          <label class="st-field"><span>Height (px)</span><input class="input narrow" type="number" min="2" step="2" value={r.height ?? ''} onchange={(e) => setSize('height', Number(e.currentTarget.value))} aria-label="Output height in pixels" /></label>
          <label class="chk"><input type="checkbox" checked={r.lock_aspect} onchange={(e) => s.edit((sp) => { sp.resolution.lock_aspect = e.currentTarget.checked; })} /> Keep the shape</label>
        </div>
      {/if}
    </section>
  {:else}
    <p class="st-note intro">Pick a tool above. Every change is saved as you go, and nothing changes your original video.</p>
  {/if}

  <section class="st-sec" aria-label="Sizes">
    <h3>Sizes, step by step</h3>
    <PipelineReadout {s} />
  </section>

  <details class="st-details">
    <summary>Advanced</summary>
    <div class="st-in">
      <p class="st-note">How the cut is made: {#if sources?.mode}<strong>{sources.mode === 'copy' ? 'fast copy' : 'exact re-encode'}</strong>{sources.why ? ` (${sources.why})` : ''}{:else}decided by the server when it prepares.{/if}</p>
      <p class="st-note">Picture part kept: {Math.round(fr.content.w)}×{Math.round(fr.content.h)} at {Math.round(fr.content.x)}, {Math.round(fr.content.y)}.</p>
      {#if s.plan?.input?.audio !== false}<label class="chk"><input type="checkbox" checked={s.spec.audio === 'mute'} onchange={(e) => s.edit((sp) => { sp.audio = e.currentTarget.checked ? 'mute' : 'keep'; })} /> Remove the sound from the prepared copy</label>{/if}
      {#if s.plan?.input?.fps}<p class="st-note">Frame rate {s.plan.input.fps} fps{s.plan.input.audio ? ', with sound' : ', no sound'}.</p>{/if}
    </div>
  </details>

  <section class="st-sec prep" aria-label="Prepare" data-testid="prepare">
    {#if s.prep.state === 'running'}
      <div class="st-prog" role="progressbar" aria-label="Preparing the video" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(s.prep.progress * 100)}><i style:width={`${Math.max(4, s.prep.progress * 100)}%`}></i></div>
      <div class="st-row"><span class="st-note" role="status">Preparing… {Math.round(s.prep.progress * 100)}%</span><button type="button" class="btn sm" onclick={() => s.cancelPrepare()}>Cancel</button></div>
    {:else}
      <div class="st-row">
        <button type="button" class="btn primary" onclick={() => s.runPrepare()} disabled={!s.project || !s.source || !s.usesVideo}><Icon name="film" size={15} /> {s.prep.state === 'done' && !s.prepStale ? 'Prepare again' : 'Prepare video'}</button>
        {#if prepared && s.prep.state === 'done'}<button type="button" class="btn sm" onclick={() => openViewer(prepared, {})}>View prepared video</button>{/if}
      </div>
      {#if s.prep.state === 'done' && !s.prepStale && prepared}
        <p class="st-note ok" role="status" data-testid="prep-done"><Icon name="check" size={13} /> {s.prep.reused ? 'Reused the prepared copy you already have.' : 'Prepared.'} {prepared.width}×{prepared.height}, {fmtDuration(prepared.duration, true)}, {fmtSize(prepared.size)}. It is in your Library as a prepared copy.</p>
      {:else if s.prepStale}
        <p class="st-note warn" role="status" data-testid="prep-stale">You changed something after the last copy was made. Prepare again to update it.</p>
      {:else if s.prep.state === 'error'}
        <p class="st-note bad" role="alert">{s.prep.error}</p>
      {:else}
        <p class="st-note">Optional: Generate prepares the video for you. Preparing now lets you check the copy first.</p>
      {/if}
    {/if}
  </section>
</div>

<style>
  .tb { margin-bottom: var(--s3); } .body { display: grid; gap: var(--s3); }
  .rs { margin-left: auto; }
  .rs.armed { border-color: var(--danger) !important; color: var(--danger) !important; }
  .cmp { margin: 0; display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; }
  .cmp div { padding: 6px 8px; border-radius: 8px; background: var(--m-card); border: 1px solid var(--border); } .cmp dt { font-size: 11px; color: var(--fg-muted); } .cmp dd { margin: 0; font-variant-numeric: tabular-nums; font-weight: 600; font-size: 13px; }
  .nums { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 8px; } .narrow { width: 96px; } .colon { padding-bottom: 8px; color: var(--fg-muted); }
  .chk { display: inline-flex; align-items: center; gap: 8px; font-size: 13px; min-height: 34px; cursor: pointer; }
  .intro { padding: 4px 0; } .prep { border-top: 1px solid var(--border); }
  @media (max-width: 560px) { .cmp, .nums { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
</style>
