<script lang="ts">
  // Step 6: check what will be sent, then start. The summary lists exactly what the model receives (numbered pictures in order) and, separately,
  // what is kept but not sent. Generate stays off, with the plain reason next to it, until nothing is missing.
  import Icon from '$lib/components/Icon.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import LiveJob from './LiveJob.svelte';
  import { fmtDuration } from '$lib/media/assets';
  import { framing, ratioWords, fmtSecs } from '$lib/studio/spec';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s }: { s: Studio } = $props();
  const sp = $derived(s.spec);
  const bl = $derived(s.blockers);
  const used = $derived(sp.references.map((r, i) => ({ r, i, st: s.states[i] })).filter((x) => x.st.used).sort((a, b) => a.st.order - b.st.order));
  const kept = $derived(sp.references.map((r, i) => ({ r, i, st: s.states[i] })).filter((x) => !x.st.used));
  const fr = $derived(framing(sp, s.sw || 16, s.sh || 9));
  const out = $derived(s.plan?.output?.requested);
  const proc = $derived(s.plan?.processing);
  const name = (r: { label: string; asset: string }, i: number) => r.label || s.assets[r.asset]?.name || `Picture ${i + 1}`;
  const start = async () => { await s.runGenerate(); };
</script>

<div class="st-h"><h2>Ready to generate</h2><p>This is everything the model will receive. Nothing else influences the result.</p></div>

{#if s.jobId}
  <section class="live" aria-label="Live job"><h3 class="lh">Live job</h3><LiveJob {s} job={s.jobId} /></section>
{/if}

<section class="sum" aria-label="What will be sent" data-testid="summary">
  <h3>What will be sent</h3>
  <dl>
    <div><dt>Operation</dt><dd>{s.mode ? `${s.mode.label}${s.mode.status === 'experimental' ? ' (experimental)' : ''}` : 'Not chosen'} · {s.entry?.alias ?? s.caps?.models.find((m) => m.model_id === sp.model)?.alias ?? 'no model'}</dd></div>
    <div><dt>Video</dt><dd data-testid="sum-video">{#if !s.usesVideo}Not used by this operation
      {:else}{s.source?.name ?? 'Video'}: {sp.trim ? `${fmtDuration(sp.trim.start, true)} to ${fmtDuration(sp.trim.end, true)} (${fmtSecs(s.keptSeconds)})` : `the whole ${fmtSecs(s.duration)}`}, {sp.frame.aspect === 'original' ? 'its own shape' : `${ratioWords(fr.ratio)} (${sp.frame.treatment})`}{#if s.plan?.prepared}, {s.plan.prepared.width}×{s.plan.prepared.height}{/if}{/if}</dd></div>
    {#if s.usesVideo}<div><dt>Subject</dt><dd data-testid="sum-subject">{sp.subject.text.trim() ? `“${sp.subject.text.trim()}” (${sp.subject.kind})` : 'No description'}{sp.subject.box ? ', box marked' : ''}{sp.subject.points.length ? `, ${sp.subject.points.length} point${sp.subject.points.length === 1 ? '' : 's'}` : ''}</dd></div>{/if}
    <div><dt>Result size</dt><dd data-testid="sum-size">{out ? `${out.width}×${out.height}` : 'not worked out yet'}{proc ? `; the model works at ${proc.width}×${proc.height}` : ''}</dd></div>
  </dl>
  <h4>Pictures sent, in this order</h4>
  {#if used.length}
    <ol class="pics" data-testid="sum-used">
      {#each used as u (u.i)}<li><span class="th"><MediaThumb id={u.r.asset} kind="image" name={name(u.r, u.i)} ratio="1 / 1" /></span><span class="n">{u.st.order}</span><span class="cap" title={name(u.r, u.i)}>{name(u.r, u.i)}{u.r.subject ? ` · target ${u.r.subject}` : ''}</span></li>{/each}
    </ol>
  {:else}<p class="st-note" data-testid="sum-none">No pictures are sent.</p>{/if}
  {#if kept.length}
    <h4>Kept but not sent</h4>
    <ul class="keep" data-testid="sum-kept">
      {#each kept as u (u.i)}<li><span class="th sm"><MediaThumb id={u.r.asset} kind="image" name={name(u.r, u.i)} ratio="1 / 1" /></span><span class="st-note"><strong>{name(u.r, u.i)}</strong> · {u.st.note}</span></li>{/each}
    </ul>
  {/if}
</section>

{#if bl.length}
  <ul class="blk" data-testid="blockers" aria-label="Still needed">
    {#each bl as b (b.text)}<li><Icon name="warn" size={14} /><span>{b.text}</span><button type="button" class="linkish" onclick={() => (s.step = b.step)}>Fix</button></li>{/each}
  </ul>
{/if}
{#if s.gen.error}<p class="st-note bad" role="alert" data-testid="gen-error">{s.gen.error}</p>{/if}

<div class="st-foot">
  <button type="button" class="btn" onclick={() => (s.step = 'model')}>Back</button>
  <button type="button" class="btn primary go" onclick={start} disabled={bl.length > 0 || s.gen.busy} aria-describedby={bl.length ? 'gen-why' : undefined} data-testid="generate">
    <Icon name="play" size={15} /> {s.gen.busy ? 'Starting…' : s.jobId ? 'Generate again' : 'Generate'}</button>
</div>
{#if bl.length}<p class="st-note" id="gen-why">Generate is off: {bl[0].text}</p>{/if}

<style>
  .live { margin-bottom: var(--s4); display: grid; gap: 8px; } .lh { font-size: 14px; }
  .sum { display: grid; gap: 8px; padding: 12px; border-radius: var(--radius); border: 1px solid var(--border); background: var(--m-card); } .sum > h3 { font-size: 14px; } h4 { font-size: 12.5px; margin-top: 4px; }
  dl { margin: 0; display: grid; gap: 6px; } dl div { display: grid; grid-template-columns: 92px minmax(0, 1fr); gap: 10px; } dt { color: var(--fg-muted); font-size: 12.5px; } dd { margin: 0; font-size: 13px; overflow-wrap: anywhere; }
  .pics { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 10px; } .pics li { position: relative; width: 76px; display: grid; gap: 3px; }
  .th { display: block; border-radius: 9px; overflow: hidden; border: 1px solid var(--border); aspect-ratio: 1; } .th.sm { width: 34px; flex: none; border-radius: 6px; }
  .n { position: absolute; left: 4px; top: 4px; min-width: 20px; height: 20px; padding: 0 6px; border-radius: 10px; display: grid; place-items: center; font: 600 11.5px var(--font); background: rgba(14,15,16,.85); color: #fff; }
  .cap { font-size: 11px; color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .keep { list-style: none; margin: 0; padding: 0; display: grid; gap: 6px; } .keep li { display: flex; align-items: center; gap: 8px; }
  .blk { list-style: none; margin: var(--s3) 0 0; padding: 10px 12px; display: grid; gap: 6px; border-radius: var(--radius); background: var(--warn-soft); border: 1px solid color-mix(in srgb, var(--warn) 35%, var(--border)); }
  .blk li { display: flex; gap: 8px; align-items: flex-start; font-size: 13px; } .blk li span { flex: 1; } .blk :global(svg) { color: var(--warn); margin-top: 2px; flex: none; }
  .go { min-width: 150px; } #gen-why { text-align: right; margin-top: 6px; }
</style>
