<script lang="ts">
  // An engine job (image or video generation). The bar is drawn only from a number the engine reported.
  import { jobStatus, percent } from '$lib/work/format';
  import type { EngineJob } from '$lib/work/types';

  let { job }: { job: EngineJob } = $props();
  const st = $derived(jobStatus(job.status));
  const pct = $derived(percent(job.progress));
  const kind = $derived(job.kind === 'video' ? 'Video' : job.kind === 'image' ? 'Picture' : job.kind ? job.kind : 'Engine job');
</script>

<div class="job">
  <div class="top">
    <strong>{kind}</strong>{#if job.model}<span class="muted small">{job.model}</span>{/if}
    <span class="spacer"></span>
    <span class="chip {st.tone === 'plain' ? '' : st.tone}">{st.label}</span>
  </div>
  {#if job.phase}<p class="phase small">{job.phase}</p>{/if}
  {#if st.active && pct !== null}
    <div class="bar" role="progressbar" aria-label={`${kind} progress`} aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct}><span style={`width:${pct}%`}></span></div>
    <p class="tiny muted pct">{pct}%</p>
  {:else if st.active && job.status === 'running'}
    <p class="tiny muted">The engine has not reported progress for this job.</p>
  {:else if job.status === 'queued' && job.queue_position != null}
    <p class="tiny muted">Place {job.queue_position} in the queue.</p>
  {/if}
  {#if job.error}<p class="err small">{job.error}</p>{/if}
  <p class="tiny muted mono id">{job.id}</p>
</div>

<style>
  .job { border: 1px solid var(--border); border-radius: var(--radius); padding: var(--s3); background: var(--surface); display: grid; gap: 6px; }
  .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .phase { margin: 0; }
  .bar { height: 8px; border-radius: var(--radius-chip); background: var(--surface-2); border: 1px solid var(--border); overflow: hidden; }
  .bar span { display: block; height: 100%; background: var(--accent); }
  .pct { margin: 0; font-variant-numeric: tabular-nums; }
  .err { margin: 0; } .id { margin: 0; overflow-wrap: anywhere; }
  @media (prefers-reduced-motion: no-preference) { .bar span { transition: width var(--medium) var(--ease); } }
</style>
