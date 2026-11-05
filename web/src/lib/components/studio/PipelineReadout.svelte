<script lang="ts">
  // What happens to the picture size on its way through: the video you gave, what is prepared from it, the size the model really works at, and the
  // size you get back. All four numbers are the server's plan; this component only lays them out and says in words what each step means.
  import Icon from '$lib/components/Icon.svelte';
  import { fmtMb, fmtSecs } from '$lib/studio/spec';
  import type { Studio } from '$lib/studio/store.svelte';
  let { s }: { s: Studio } = $props();
  const p = $derived(s.plan);
  const dims = (d?: { width: number; height: number } | null) => (d && d.width && d.height ? `${d.width}×${d.height}` : '–');
  const out = $derived(p?.output);
  const proc = $derived(p?.processing);
  const mem = $derived(proc?.memory);
  const sc = $derived(out?.scaling);
  const reqPreset = $derived(s.spec.resolution.preset);
  const differs = $derived(!!proc && !!out?.requested && !proc.exact && proc.width * proc.height !== out.requested.width * out.requested.height);
</script>

<section class="pr" aria-label="Resolution pipeline" data-testid="pipeline" aria-busy={s.planBusy}>
  {#if !p}
    <p class="st-note" role="status">{s.planError || (s.planBusy ? 'Working out the sizes…' : 'The sizes appear here once the server has worked them out.')}</p>
  {:else}
    <ol class="pipe">
      <li data-testid="pipe-input"><span class="k"><i>1</i>Your video</span><strong>{dims(p.input)}</strong><span class="sub">{p.input?.duration ? fmtSecs(p.input.duration) : ''}{p.input?.rotation ? ` · rotated ${p.input.rotation}°` : ''}</span></li>
      <li data-testid="pipe-prepared"><span class="k"><i>2</i>Prepared</span><strong>{dims(p.prepared)}</strong><span class="sub">{p.trim ? fmtSecs(p.trim.seconds) : ''}{p.prepared?.scale ? ' · scaled' : ''}</span></li>
      <li data-testid="pipe-processing" class="proc"><span class="k"><i>3</i>Model works at</span><strong>{dims(proc)}</strong><span class="sub">{proc?.preset ?? ''}{proc?.exact ? ' · as asked' : proc ? ' · nearest supported' : ''}</span></li>
      <li data-testid="pipe-output"><span class="k"><i>4</i>You get</span><strong>{dims(out?.requested)}</strong><span class="sub">{sc === 'upscale' ? 'scaled up' : sc === 'downscale' ? 'scaled down' : sc === 'none' ? 'as made' : ''}</span></li>
    </ol>

    {#if proc?.why}<p class="st-note" data-testid="pipe-why"><Icon name="info" size={13} /> {proc.why}</p>{/if}
    {#if differs && reqPreset !== 'original'}<p class="st-note warn" data-testid="pipe-differs">You asked for {out?.requested?.preset ?? reqPreset} ({dims(out?.requested)}). The model works at {dims(proc)}.</p>{/if}
    {#if out?.note}<p class="st-note" class:warn={sc === 'upscale'} data-testid="pipe-output-note">{out.note}</p>
    {:else if sc === 'upscale'}<p class="st-note warn" data-testid="pipe-output-note">The model makes {dims(out?.native)}; {dims(out?.requested)} is made by scaling it up. Scaling up adds size but no new detail.</p>{/if}

    <p class="st-note" data-testid="pipe-memory">
      {#if mem && mem.basis !== 'none' && mem.peak_mb}Memory needed: about {fmtMb(mem.peak_mb)} ({mem.basis === 'measured' ? 'measured on this machine' : 'extrapolated from measured runs'}).{#if mem.note}{' '}{mem.note}{/if}
      {:else}Memory use has not been measured at this size, so no estimate is shown.{/if}
      {#if p.seconds?.estimate && p.seconds.basis && p.seconds.basis !== 'none'}{' '}Expected time: about {fmtSecs(p.seconds.estimate)} ({p.seconds.basis}).{/if}
    </p>
    {#each p.warnings ?? [] as w}<p class="st-note warn" role="status">{w}</p>{/each}
    {#each p.errors ?? [] as w}<p class="st-note bad" role="alert">{w}</p>{/each}
  {/if}
</section>

<style>
  .pr { display: grid; gap: 6px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--m-card); }
  .pipe { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fit, minmax(138px, 1fr)); gap: 6px; }
  .pipe li { display: grid; gap: 1px; padding: 8px 10px; border-radius: 9px; background: var(--card-bg); border: 1px solid var(--border); align-content: start; min-width: 0; }
  .pipe li.proc { border-color: var(--border-strong); }
  .k { font-size: 11px; color: var(--fg-muted); font-weight: 500; display: flex; align-items: center; gap: 6px; }
  .k i { font-style: normal; width: 16px; height: 16px; border-radius: 50%; display: grid; place-items: center; font-size: 10px; font-weight: 600; border: 1px solid var(--border-strong); color: var(--fg-2); flex: none; }
  strong { font-size: 14px; font-variant-numeric: tabular-nums; }
  .sub { font-size: 11.5px; color: var(--fg-muted); min-height: 1em; }
  .st-note :global(svg) { vertical-align: -2px; }
</style>
