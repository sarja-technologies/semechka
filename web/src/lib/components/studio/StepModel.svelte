<script lang="ts">
  // Step 5: which model, and the output settings. Models that cannot do the chosen operation are listed too, greyed, with the reason, so nobody
  // wonders where a model went. Estimates come from the server's plan only.
  import Icon from '$lib/components/Icon.svelte';
  import PipelineReadout from './PipelineReadout.svelte';
  import SettingsFields from './SettingsFields.svelte';
  import { STATUS_LABEL } from '$lib/studio/caps';
  import { SELECTION_FIELDS, BECOMES_FIELDS } from '$lib/studio/schema';
  import { RESOLUTIONS, ratioWords, framing } from '$lib/studio/spec';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s }: { s: Studio } = $props();
  const ch = $derived(s.choices);
  const fr = $derived(framing(s.spec, s.sw || 16, s.sh || 9));
  const res = $derived(s.spec.resolution.preset === 'custom' ? `${s.spec.resolution.width}×${s.spec.resolution.height}` : s.spec.resolution.preset === 'original' ? 'original size' : RESOLUTIONS.find((r) => r.id === s.spec.resolution.preset)?.label ?? s.spec.resolution.preset);
  const hasAdvanced = $derived(!!s.profileInfo?.inputs_schema);
</script>

<div class="st-h"><h2>Model and output</h2><p>Only models that can really do the chosen operation can be picked. The rest are shown with the reason.</p></div>

{#if !s.mode}
  <p class="st-note warn" role="status">Choose an operation first (step 4). Then the models that can do it appear here.</p>
  <button type="button" class="btn sm" onclick={() => (s.step = 'operation')}>Go to Operation</button>
{:else}
  <div class="models" role="radiogroup" aria-label="Model" data-testid="models">
    {#each ch as c (c.model.model_id)}
      {@const sel = s.spec.model === c.model.model_id}
      <button type="button" class="model" role="radio" aria-checked={sel} aria-disabled={!c.supported} class:off={!c.supported} class:on={sel} data-model={c.model.model_id} data-status={c.status}
        onclick={() => c.supported && s.chooseModel(c.model.model_id)}>
        <span class="top"><strong>{c.model.alias ?? c.model.model_id}</strong>
          <span class="st-badge" class:ok={c.status === 'ready'} class:warn={c.status === 'experimental'} class:bad={!c.supported}>{c.supported ? STATUS_LABEL[c.status] : 'Cannot do this'}</span></span>
        <span class="why">{c.supported ? (c.reason || (c.model.family ? `${c.model.family} editing` : '')) : c.reason}</span>
      </button>
    {:else}
      <p class="st-note">No model is listed for this operation.</p>
    {/each}
  </div>

  <section class="st-sec" aria-label="Output">
    <h3>Output</h3>
    <p class="st-note" data-testid="output-summary">Shape {s.spec.frame.aspect === 'original' ? `as the video (${ratioWords(fr.ratio)})` : ratioWords(fr.ratio)}{fr.treatment !== 'crop' && s.spec.frame.aspect !== 'original' ? `, ${fr.treatment === 'pad' ? 'padded with black bars' : 'stretched'}` : ''} · {res}{s.spec.trim ? ` · ${s.keptSeconds.toFixed(1)} s` : ''}. <button type="button" class="linkish" onclick={() => { s.step = 'prepare'; s.tool = 'resolution'; }}>Change in Prepare</button></p>
    <PipelineReadout {s} />
    <SettingsFields {s} only={['seed', 'clip_seconds']} label="Output settings" />
  </section>

  {#if hasAdvanced}
    <details class="st-details">
      <summary>Advanced settings</summary>
      <div class="st-in"><p class="st-note">Settings the engine offers for this operation. The defaults are fine for most videos.</p>
        <SettingsFields {s} skip={['seed', 'clip_seconds', ...SELECTION_FIELDS, ...BECOMES_FIELDS]} label="Advanced settings" /></div>
    </details>
  {/if}
{/if}
<div class="st-foot"><button type="button" class="btn" onclick={() => (s.step = 'operation')}>Back</button><button type="button" class="btn primary" onclick={() => (s.step = 'generate')}>Continue to Generate <Icon name="chevron" size={14} /></button></div>

<style>
  .models { display: grid; gap: 8px; margin-bottom: var(--s3); }
  .model { all: unset; box-sizing: border-box; display: grid; gap: 3px; padding: 10px 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--card-bg); cursor: pointer; transition: border-color var(--fast) var(--ease), background var(--fast) var(--ease); }
  .model:hover:not(.off) { background: var(--hover); } .model.on { border-color: var(--m-ring); box-shadow: 0 0 0 1px var(--m-ring) inset; background: var(--m-ring-soft); } .model:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .model.on .why { color: var(--fg-2); }
  .model.off { cursor: not-allowed; opacity: .62; background: var(--surface-2); } .top { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; } strong { font-size: 13.5px; } .why { font-size: 12px; color: var(--fg-muted); }
</style>
