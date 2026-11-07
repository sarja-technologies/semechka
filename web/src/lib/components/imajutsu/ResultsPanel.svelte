<script lang="ts">
  // Results: every picture the runs on show made, with the seed and settings the engine used, and what you can do next: open it, download it,
  // use it as the source of the next edit, adjust it, send it as a reference, compare it with the original. Copies made without a model
  // (phone finish, adjusted, scaled, original kept outside the selection) sit under the picture they came from and say so.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import PanelCard from './PanelCard.svelte';
  import { contentUrl, fmtDims, type Asset } from '$lib/media/assets';
  import { openViewer } from '$lib/media/viewer.svelte';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  import type { Derived, JobRow, Output } from '$lib/imajutsu/types';
  let { s }: { s: Imajutsu } = $props();

  type Row = { out: Output; row: JobRow; facts: string[]; canKeep: boolean };
  const rows = $derived.by((): Row[] => {
    const res: Row[] = [];
    for (const j of s.batch) {
      const row = s.jobs.find((r) => r.job === j); if (!row) continue;
      const blk = row.imajutsu ?? {}, plan = blk.plan ?? {};
      for (const o of row.outputs ?? []) {
        if (o.kind !== 'image') continue;
        const steps = plan.steps, nat = plan.native;
        res.push({ out: o, row, canKeep: !!(blk.mask_asset || blk.selection?.box || blk.mode === 'outpaint') && ['replace_subject', 'inpaint', 'outpaint'].includes(blk.mode ?? ''),
          facts: [`${o.width}×${o.height}`, row.model ?? '', steps != null ? `${steps} steps` : '', nat && (nat.width !== o.width || nat.height !== o.height) ? `model size ${nat.width}×${nat.height}` : ''].filter(Boolean) });
      }
    }
    return res;
  });
  const copies = $derived(s.copies.map((id) => s.asset(id)).filter((a): a is Asset => !!a));
  const everything = $derived([...rows.map((r) => s.asset(r.out.id)), ...rows.flatMap((r) => r.out.derived.map((d) => s.asset(d.id))), ...copies].filter((a): a is Asset => !!a));
  const before = $derived(s.needs.source ? s.source : null);
  let keepBusy = $state(''), keepErr = $state('');
  const kindOf = (d: Derived) => d.imajutsu?.kind ?? d.origin ?? 'copy';
  const LABEL: Record<string, string> = { adjusted: 'Adjusted copy (photo adjustments, not AI)', composited: 'Your original kept outside the selection', scaled: 'Scaled copy (no new detail)', upscaled: 'Scaled copy (no new detail)' };
  const labelOf = (d: Derived) => (kindOf(d) === 'adjusted' && d.imajutsu?.recipe && !d.imajutsu.label ? LABEL.adjusted : d.imajutsu?.label || LABEL[kindOf(d)] || 'Copy');
  function compare(a: Asset) { s.picked = a.id; s.stage = 'result'; s.compare = true; }
  function show(a: Asset) { s.picked = a.id; s.stage = 'result'; }
  async function keep(a: Asset) { keepBusy = a.id; keepErr = await s.keepOutsideOf(a); keepBusy = ''; }
</script>

{#snippet actions(a: Asset, canKeep: boolean)}
  <div class="acts" role="group" aria-label={`Actions for the ${fmtDims(a)} picture`}>
    <button type="button" class="btn sm" onclick={() => openViewer(a, { siblings: everything })} data-testid="res-open"><Icon name="expand" size={14} /> Open</button>
    <a class="btn sm" href={contentUrl(a, { download: true })} download data-testid="res-download"><Icon name="download" size={14} /> Download</a>
    <button type="button" class="btn sm" onclick={() => s.useAsSource(a)} data-testid="res-use-source">Use as source</button>
    <button type="button" class="btn sm" onclick={() => s.adjustPicture(a)} data-testid="res-adjust">Adjust</button>
    <button type="button" class="btn sm" onclick={() => s.sendAsReference(a)} data-testid="res-ref">Send as reference</button>
    {#if before && before.id !== a.id}<button type="button" class="btn sm" onclick={() => compare(a)} data-testid="res-compare"><Icon name="compare" size={14} /> Compare</button>{/if}
    {#if canKeep}<button type="button" class="btn sm" disabled={keepBusy === a.id} onclick={() => keep(a)} data-testid="res-keep">{keepBusy === a.id ? 'Making…' : 'Keep my original outside the selection'}</button>{/if}
  </div>
{/snippet}

<PanelCard id="results" title="Results" hint="Every result is a new picture in your Library. Your own pictures are never changed.">
  {#if !rows.length && !copies.length}
    <p class="st-note" data-testid="results-empty">{s.batch.length ? 'The pictures appear here as soon as they are finished.' : 'Nothing yet. Press Generate.'}</p>
  {/if}
  {#if keepErr}<p class="st-note bad" role="alert">{keepErr}</p>{/if}
  <ul class="res" aria-label="Results" data-testid="results">
    {#each rows as r (r.out.id)}
      {@const a = s.asset(r.out.id)}
      <li class:on={s.picked === r.out.id} data-testid="result" data-asset={r.out.id} data-job={r.row.job}>
        {#if a}<button type="button" class="th" onclick={() => show(a)} aria-label={`Show this result on the stage, ${fmtDims(a)}`} aria-pressed={s.picked === r.out.id}><MediaThumb id={a.id} kind="image" name="Result" ratio={`${r.out.width || 1} / ${r.out.height || 1}`} fit="contain" /></button>{:else}<span class="th"></span>{/if}
        <div class="tx">
          <p class="meta" data-testid="result-meta">{#if r.out.seed != null}<span class="st-badge" data-testid="result-seed">seed {r.out.seed}</span>{/if} {r.facts.join(' · ')}</p>
          {#if a}{@render actions(a, r.canKeep && !r.out.derived.some((d) => kindOf(d) === 'composited'))}{/if}
          {#if r.out.derived.length}
            <ul class="der" aria-label="Copies made from this picture">
              {#each r.out.derived as d (d.id)}
                {@const da = s.asset(d.id)}
                <li data-testid="derived" data-kind={kindOf(d)}>
                  {#if da}<button type="button" class="dth" onclick={() => show(da)} aria-label={`Show on the stage: ${labelOf(d)}`}><MediaThumb id={da.id} kind="image" name="" ratio={`${d.width || 1} / ${d.height || 1}`} fit="contain" /></button>{/if}
                  <div class="dx"><span class="meta"><span class="st-badge info">{labelOf(d)}</span> {d.width}×{d.height}</span>{#if da}{@render actions(da, false)}{/if}</div>
                </li>
              {/each}
            </ul>
          {/if}
        </div>
      </li>
    {/each}
    {#each copies as a (a.id)}
      <li class:on={s.picked === a.id} data-testid="copy" data-asset={a.id}>
        <button type="button" class="th" onclick={() => show(a)} aria-label={`Show this copy on the stage, ${fmtDims(a)}`} aria-pressed={s.picked === a.id}><MediaThumb id={a.id} kind="image" name="Copy" ratio={`${a.width || 1} / ${a.height || 1}`} fit="contain" /></button>
        <div class="tx"><p class="meta"><span class="st-badge info">{a.origin === 'adjusted' ? 'Adjusted copy' : a.origin === 'upscaled' || a.origin === ('scaled' as string) ? 'Scaled copy' : 'Copy'}</span> {fmtDims(a)} <span class="tiny muted">not made by a model</span></p>{@render actions(a, false)}</div>
      </li>
    {/each}
  </ul>
</PanelCard>

<style>
  .res { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; }
  .res > li { display: grid; grid-template-columns: 112px minmax(0, 1fr); gap: 12px; align-items: start; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); } .res > li.on { border-color: var(--fg); box-shadow: 0 0 0 1px var(--fg) inset; }
  .th { all: unset; display: block; width: 112px; height: 112px; border-radius: 8px; overflow: hidden; border: 1px solid var(--border); cursor: pointer; background: var(--m-card); } .th:focus-visible, .dth:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  .tx { display: grid; gap: 8px; min-width: 0; } .meta { margin: 0; font-size: 12.5px; color: var(--fg-2); display: flex; gap: 6px; align-items: center; flex-wrap: wrap; } .acts { display: flex; flex-wrap: wrap; gap: 6px; }
  .acts :global(.btn) { min-height: 32px; } @media (pointer: coarse), (max-width: 560px) { .acts :global(.btn) { min-height: 44px; } }
  .der { list-style: none; margin: 4px 0 0; padding: 8px 0 0; border-top: 1px dashed var(--border-strong); display: grid; gap: 8px; } .der li { display: grid; grid-template-columns: 56px minmax(0, 1fr); gap: 10px; align-items: start; }
  .dth { all: unset; display: block; width: 56px; height: 56px; border-radius: 6px; overflow: hidden; border: 1px solid var(--border); cursor: pointer; } .dx { display: grid; gap: 6px; min-width: 0; }
  @media (max-width: 560px) { .res > li { grid-template-columns: 84px minmax(0, 1fr); } .th { width: 84px; height: 84px; } }
</style>
