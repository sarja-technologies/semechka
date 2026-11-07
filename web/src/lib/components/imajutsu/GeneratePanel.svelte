<script lang="ts">
  // Panel: Generate. How many pictures, what stops it in plain words, and a summary of exactly what is sent to the model: the source, the
  // selection, the numbered pictures that are used, the ones that are not, the model and the size it works at.
  import PanelCard from './PanelCard.svelte';
  import { modelName } from '$lib/imajutsu/caps';
  import { fmtDims } from '$lib/imajutsu/spec';
  import type { Imajutsu, Panel } from '$lib/imajutsu/store.svelte';
  let { s, n = 0 }: { s: Imajutsu; n?: number } = $props();
  const bl = $derived(s.blockers), sp = $derived(s.spec), p = $derived(s.plan), nd = $derived(s.needs);
  const refs = $derived(p?.references);
  const nm = (id: string, i: number) => s.asset(id)?.name ?? `Picture ${i}`;
  const roleName = (r?: string | null) => (r ? s.roles.find((x) => x.id === r)?.label ?? r : '');
  const upscale = $derived(sp.mode === 'upscale');
  const mode = $derived(s.mode);
  const refine = $derived(mode?.refine);
  function go(panel: Panel) { s.panel = panel; document.getElementById(`pn-${panel}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); }
  const sel = $derived(sp.mask_asset ? 'Saved as a picture (white = change)' : sp.selection.box ? 'A box on the picture' : s.maskStale ? 'Will be saved when you press Generate' : 'None');
</script>

<PanelCard id="generate" title={upscale && s.upscaleOnly ? 'Scale up' : 'Generate'} {n}>
  {#if !s.upscaleOnly}
    <div class="st-sec" style="padding:0;border:0">
      <h3 id="cnt-h">How many pictures</h3>
      <div class="st-seg" role="radiogroup" aria-labelledby="cnt-h" data-testid="count">
        {#each [1, 2, 3, 4] as c (c)}<button type="button" role="radio" aria-checked={sp.count === c} onclick={() => s.edit((x) => { x.count = c; })}>{c}</button>{/each}
      </div>
      {#if sp.count > 1}<p class="st-note">Each picture is its own run with its own seed, one after the other.</p>{/if}
    </div>
  {/if}
  {#if upscale}
    <label class="keep"><input type="checkbox" checked={sp.upscale.refine} disabled={!refine?.available} onchange={(e) => s.edit((x) => { x.upscale.refine = e.currentTarget.checked; })} data-testid="refine" />
      <span><strong>Refine after scaling</strong> <span class="st-note">{refine?.available ? `A light second pass by a model (${refine.recommended?.why ? refine.recommended.why : 'image to image'}). It can change texture; the scaling step itself adds none.` : 'Not available: no installed model can refine a picture here.'}</span></span></label>
  {/if}

  <section class="acc" aria-label="What will be sent" data-testid="accounting">
    <h3>What will be sent</h3>
    <dl>
      {#if nd.model && !s.upscaleOnly}<dt>Model</dt><dd data-testid="acc-model">{p?.model?.alias ?? (modelName(s.entry) || 'None chosen')}</dd>{/if}
      {#if s.upscaleOnly}<dt>Method</dt><dd data-testid="acc-model">Scaling (Lanczos), no model</dd>{/if}
      {#if nd.source}<dt>Source picture</dt><dd data-testid="acc-source">{s.source ? `${s.source.name ?? 'Picture'} · ${fmtDims(s.source)}` : 'None yet'}</dd>{/if}
      {#if nd.selection === 'required' || (nd.selection === 'optional' && s.hasSel)}<dt>Selection</dt><dd data-testid="acc-mask">{s.hasSel ? sel : 'None'}{sp.selection.invert ? ', inverted' : ''}{sp.selection.feather ? `, soft edge ${sp.selection.feather} px` : ''}</dd>{/if}
      {#if nd.selection === 'area'}<dt>New area</dt><dd data-testid="acc-area">{p?.outpaint ? `${fmtDims(p.outpaint.canvas)} canvas, your picture inside` : 'Waiting for the size'}</dd>{/if}
      {#if nd.refs !== 'none' && refs}
        <dt>Pictures used</dt>
        <dd data-testid="acc-used">{#if refs.used.length}<ol>{#each refs.used as x, k (x.position)}<li>{k + 1}. {nm(x.asset, x.position)}{x.role ? ` (${roleName(x.role)})` : ''}</li>{/each}</ol>{:else}None{/if}</dd>
        {#if refs.not_used.length}<dt>Not used</dt><dd data-testid="acc-unused"><ul>{#each refs.not_used as x (x.position)}<li>{nm(x.asset, x.position)} <span class="muted">· {x.reason}</span></li>{/each}</ul></dd>{/if}
      {/if}
      {#if p?.native && !s.upscaleOnly}<dt>The model works at</dt><dd data-testid="acc-native">{fmtDims(p.native)}</dd>{/if}
      <dt>You get</dt><dd data-testid="acc-output">{p?.output ? fmtDims(p.output) : '–'}{p?.output?.scaling && p.output.scaling !== 'none' ? ` (${p.output.scaling === 'upscale' ? 'scaled up, no new detail' : 'scaled down'})` : ''}</dd>
    </dl>
  </section>

  {#if s.advanced && !s.upscaleOnly}
    <div class="st-row" data-testid="adv-gen">
      <label class="st-field"><span>Seed (empty = random)</span><input class="input num" type="number" inputmode="numeric" value={sp.seed ?? ''} placeholder="random" data-testid="seed-input" onchange={(e) => s.edit((x) => { const v = e.currentTarget.value; x.seed = v === '' ? null : Math.max(0, Math.round(Number(v))); })} /></label>
      <label class="st-field"><span>Steps (empty = quality preset)</span><input class="input num" type="number" inputmode="numeric" min="1" max="200" value={sp.advanced.steps ?? ''} placeholder={p?.steps ? String(p.steps) : 'default'} data-testid="steps-input" onchange={(e) => s.edit((x) => { const v = e.currentTarget.value; if (v === '') delete x.advanced.steps; else x.advanced.steps = Math.max(1, Math.round(Number(v))); })} /></label>
      <label class="st-field"><span>Guidance (empty = model default)</span><input class="input num" type="number" step="0.1" min="0" max="30" value={sp.advanced.guidance ?? ''} placeholder="default" data-testid="guidance-input" onchange={(e) => s.edit((x) => { const v = e.currentTarget.value; if (v === '') delete x.advanced.guidance; else x.advanced.guidance = Number(v); })} /></label>
      {#if ['edit', 'inpaint', 'restyle', 'advanced'].includes(sp.mode)}
        <label class="st-field"><span>Strength (empty = default)</span><input class="input num" type="number" step="0.05" min="0.05" max="1" value={sp.advanced.denoise ?? ''} placeholder="default" onchange={(e) => s.edit((x) => { const v = e.currentTarget.value; if (v === '') delete x.advanced.denoise; else x.advanced.denoise = Number(v); })} /></label>
      {/if}
    </div>
  {/if}

  {#if bl.length}
    <ul class="bl" aria-label="Before you can generate" data-testid="blockers">{#each bl as b, i (i)}<li><button type="button" class="linkish" onclick={() => go(b.panel)}>{b.text}</button></li>{/each}</ul>
  {/if}
  {#each p?.warnings ?? [] as w}<p class="st-note warn" role="status" data-testid="plan-warning">{w}</p>{/each}
  {#if s.gen.error}<div class="notice bad" role="alert"><div>{s.gen.error}</div></div>{/if}
  <div class="st-foot">
    <span class="tiny muted" role="status" data-testid="save-hint">{s.maskStale ? 'The selection is saved when you generate.' : ''}</span>
    <button type="button" class="btn primary go" onclick={() => s.runGenerate()} disabled={bl.length > 0 || s.gen.busy} data-testid="generate">{s.gen.busy ? 'Starting…' : s.upscaleOnly ? 'Scale up' : sp.count > 1 ? `Generate ${sp.count} pictures` : 'Generate'}</button>
  </div>
</PanelCard>

<style>
  .acc { padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--m-card); display: grid; gap: 6px; } h3 { font-size: 13px; }
  dl { margin: 0; display: grid; grid-template-columns: max-content minmax(0, 1fr); gap: 4px 14px; font-size: 13px; } dt { color: var(--fg-muted); } dd { margin: 0; overflow-wrap: anywhere; } ol, ul { margin: 0; padding-left: 18px; }
  .num { width: 150px; } .bl { margin: 0; padding-left: 18px; font-size: 13px; display: grid; gap: 2px; } .bl .linkish { color: var(--fg); text-align: left; }
  .go { min-height: 44px; padding: 0 22px; font-size: 15px; } .keep { display: flex; gap: 10px; align-items: flex-start; font-size: 13px; cursor: pointer; } .keep input { margin-top: 3px; width: 18px; height: 18px; flex: none; }
</style>
