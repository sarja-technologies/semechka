<script lang="ts">
  // Panel: Model. The app picks the model the benchmark showed to be best for this mode and says why, with the evidence line. Other models are
  // listed with their reasons; choosing one by hand is an Advanced option. A model that cannot do the mode is shown disabled, with the reason.
  import PanelCard from './PanelCard.svelte';
  import { modelName, runnable } from '$lib/imajutsu/caps';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  const mode = $derived(s.mode), rec = $derived(mode?.recommended), cur = $derived(s.entry), manual = $derived(!!s.spec.model && s.spec.model !== rec?.model_id);
  const models = $derived((mode?.models ?? []).filter((m) => m.model_id));
  const others = $derived(mode?.models ?? []);
  const refs = (m: any) => (m.limits?.references != null ? `takes ${m.limits.references} picture${m.limits.references === 1 ? '' : 's'}` : '');
  const evidence = $derived((rec?.model_id === cur?.model_id ? rec?.evidence : cur?.evidence) ?? []);
</script>

<PanelCard id="model" title="Model" {n} hint="The app chooses the model that did best in real test runs on this machine for this kind of picture.">
  {#if cur}
    <div class="cur" data-testid="model-current">
      <div class="top"><strong data-testid="model-name">{modelName(cur)}</strong>
        {#if !manual}<span class="st-badge ok">Recommended</span>{:else}<span class="st-badge info">Chosen by you</span>{/if}
        {#if cur.status === 'experimental'}<span class="st-badge warn">Experimental</span>{/if}
        {#if cur.slow}<span class="st-badge warn">Slow</span>{/if}
        {#if refs(cur)}<span class="tiny muted">{refs(cur)}</span>{/if}</div>
      {#if (rec?.model_id === cur.model_id ? rec?.why : cur.why) || cur.reason}<p class="why" data-testid="model-why">{(rec?.model_id === cur.model_id ? rec?.why : cur.why) || cur.reason}</p>{/if}
      {#if evidence.length}<p class="evi" data-testid="model-evidence"><span class="tiny muted">Benchmark evidence</span> {evidence.join(' · ')}</p>{/if}
      {#if cur.seconds_hint}<p class="evi"><span class="tiny muted">Speed</span> {cur.seconds_hint}</p>{/if}
    </div>
  {:else}
    <p class="st-note warn" role="status">No model is chosen. {mode?.reason ?? ''}</p>
  {/if}

  {#if manual && rec}
    <p class="st-note warn" data-testid="model-diff">You chose a different model from the recommended one ({rec.alias ?? rec.model_id}). <button type="button" class="linkish" onclick={() => s.chooseModel(null)}>Go back to the recommended one</button></p>
  {/if}

  {#if others.length}
    <details class="st-details" open={s.advanced}>
      <summary>All models for this mode ({others.length})</summary>
      <div class="st-in">
        <ul class="alts" aria-label="Models" data-testid="model-alternatives">
          {#each others as m (m.engine_id ?? m.model_id)}
            {@const ok = runnable(m.status) && !!m.model_id}
            <li class:off={!ok}>
              <label>
                <input type="radio" name="model" disabled={!ok || !s.advanced} checked={cur?.model_id === m.model_id} onchange={() => s.chooseModel(m.model_id === rec?.model_id ? null : m.model_id)} aria-describedby={`mr-${m.engine_id ?? m.model_id}`} />
                <span class="nm">{modelName(m)}</span>
                <span class="st-badge" class:warn={m.status === 'available' || m.status === 'experimental' || m.slow} class:bad={!ok}>{ok ? (m.model_id === cur?.model_id ? 'In use' : m.status === 'available' ? 'Works, not benchmarked' : m.status === 'experimental' ? 'Experimental' : 'Can do this') : 'Cannot do this'}</span>
                {#if m.slow && ok}<span class="st-badge warn">Slow</span>{/if}
              </label>
              <span class="rs tiny muted" id={`mr-${m.engine_id ?? m.model_id}`}>{ok ? (m.why || m.reason || 'Works, but did less well in the benchmark.') : (m.reason || 'Not available.')}{refs(m) ? ` · ${refs(m)}` : ''}</span>
            </li>
          {/each}
        </ul>
        {#if !s.advanced}<p class="st-note">To choose one yourself, turn on Advanced options at the top of the page.</p>{/if}
      </div>
    </details>
  {/if}
  {#if models.length === 0 && !cur}<p class="st-note">{mode?.reason}</p>{/if}
</PanelCard>

<style>
  .cur { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--border-strong); border-radius: var(--radius); background: var(--m-card); } .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .top strong { font-size: 15px; }
  .why { margin: 0; font-size: 13px; line-height: 1.5; } .evi { margin: 0; font-size: 12.5px; line-height: 1.45; color: var(--fg-2); }
  .alts { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; } .alts li { display: grid; gap: 1px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius-sm); } .alts li.off { opacity: .75; border-style: dashed; }
  label { display: flex; align-items: center; gap: 8px; min-height: 28px; cursor: pointer; flex-wrap: wrap; } label input { width: 18px; height: 18px; } .nm { font-weight: 600; font-size: 13.5px; } .rs { padding-left: 26px; line-height: 1.4; }
</style>
