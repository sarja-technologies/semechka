<script lang="ts">
  // Engine jobs started by this session, from GET /state. Progress shows only what the engine reported.
  import type { EngineJob } from '$lib/work/types';
  import JobCard from './JobCard.svelte';
  let { jobs, loaded }: { jobs: EngineJob[]; loaded: boolean } = $props();
  const active = $derived(jobs.filter((j) => j.status === 'queued' || j.status === 'running'));
  const past = $derived(jobs.filter((j) => !(j.status === 'queued' || j.status === 'running')));
</script>

{#if !loaded}<p class="muted small" role="status">Loading…</p>
{:else if !jobs.length}<p class="muted small">No engine jobs yet. When the session makes a picture or a video, the job and its real progress show here.</p>
{:else}
  <div class="stack" style="gap:var(--s3)">
    {#if active.length}<h3 class="h">Running now</h3>{#each active as j (j.id)}<JobCard job={j} />{/each}{/if}
    {#if past.length}<h3 class="h">Earlier</h3>{#each past as j (j.id)}<JobCard job={j} />{/each}{/if}
  </div>
{/if}
<style>.h { font-size: 12px; font-weight: 600; color: var(--fg-muted); text-transform: none; margin: 0; }</style>
