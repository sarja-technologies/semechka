<script lang="ts">
  // Earlier runs and projects. Opening a run brings its project back with it: pictures, selection, adjustments and results.
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import { toast } from '$lib/toast.svelte';
    import { MODES } from '$lib/imajutsu/caps';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, open = $bindable(false) }: { s: Imajutsu; open: boolean } = $props();
  let tab = $state<'jobs' | 'projects'>('jobs'), confirm = $state<string | null>(null);
  $effect(() => { if (open) { s.refreshJobs(); s.refreshProjects(); } });
  const when = (t?: number | string) => (t ? new Date(typeof t === 'number' ? t * 1000 : t).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' }) : '');
  const modeName = (id?: string) => MODES.find((x) => x.key === id)?.label ?? s.caps?.modes.find((x) => x.id === id)?.label ?? id ?? 'Run';
  const tone = (st: string) => (st === 'completed' ? 'ok' : st === 'failed' ? 'bad' : st === 'cancelled' ? '' : 'info');
  const LABEL: Record<string, string> = { queued: 'Waiting', running: 'Running', completed: 'Finished', failed: 'Failed', cancelled: 'Cancelled' };
  async function openJob(j: any) { await s.openJob(j); open = false; }
  async function openProject(id: string) { await s.open(id); open = false; }
  async function remove(id: string) { try { await s.removeProject(id); confirm = null; toast('Project deleted. Your pictures and results are kept.'); } catch (e) { toast(errText(e), 'bad'); } }
</script>

<Modal bind:open title="Jobs and projects" wide>
  <div class="st-seg tabs" role="tablist" aria-label="Show">
    <button type="button" role="tab" aria-selected={tab === 'jobs'} onclick={() => (tab = 'jobs')}>Jobs ({s.jobs.length})</button>
    <button type="button" role="tab" aria-selected={tab === 'projects'} onclick={() => (tab = 'projects')}>Projects ({s.projects.length})</button>
  </div>
  {#if tab === 'jobs'}
    {#if !s.jobs.length}<p class="st-note" role="status">No runs yet. When you generate, it appears here and stays after you leave.</p>{/if}
    <ul class="rows" aria-label="Jobs" data-testid="jobs-list">
      {#each s.jobs as j (j.job)}
        {@const out = (j.outputs ?? []).map((o) => o.id)}
        <li>
          <span class="th">{#if out[0]}<MediaThumb id={out[0]} kind="image" ratio="1 / 1" badge={false} />{:else}<Icon name="image" size={18} />{/if}</span>
          <span class="tx"><strong>{modeName(j.mode)}</strong><span class="tiny muted">{[j.model, j.batch && j.batch.count > 1 ? `${j.batch.index + 1} of ${j.batch.count}` : '', when(j.created_at)].filter(Boolean).join(' · ')}</span></span>
          <span class="st-badge" class:ok={tone(j.status) === 'ok'} class:bad={tone(j.status) === 'bad'} class:info={tone(j.status) === 'info'}>{LABEL[j.status] ?? j.status}</span>
          <button type="button" class="btn sm" onclick={() => openJob(j)} aria-label={`Open ${modeName(j.mode)} run from ${when(j.created_at)}`}>Open</button>
        </li>
      {/each}
    </ul>
  {:else}
    {#if !s.projects.length}<p class="st-note" role="status">No projects yet.</p>{/if}
    <ul class="rows" aria-label="Projects" data-testid="projects-list">
      {#each s.projects as p (p.id)}
        <li>
          <span class="th">{#if p.thumbnail || p.thumb_asset}<MediaThumb id={(p.thumbnail || p.thumb_asset) as string} kind="image" ratio="1 / 1" badge={false} />{:else}<Icon name="image" size={18} />{/if}</span>
          <span class="tx"><strong>{p.title || 'Untitled project'}</strong><span class="tiny muted">{when(p.updated_at)}</span></span>
          {#if p.last_job?.status}<span class="st-badge">{LABEL[p.last_job.status] ?? p.last_job.status}</span>{:else}<span></span>{/if}
          <span class="acts">
            <button type="button" class="btn sm" onclick={() => openProject(p.id)}>Open</button>
            {#if confirm === p.id}<button type="button" class="btn sm danger" onclick={() => remove(p.id)}>Delete the project</button><button type="button" class="btn sm ghost" onclick={() => (confirm = null)}>Keep</button>
            {:else}<button type="button" class="btn sm ghost" onclick={() => (confirm = p.id)} aria-label={`Delete ${p.title || 'project'}`}><Icon name="trash" size={14} /></button>{/if}
          </span>
        </li>
      {/each}
    </ul>
    <p class="st-note">Deleting a project never deletes your pictures or results.</p>
  {/if}
</Modal>

<style>
  .tabs { margin-bottom: var(--s3); } .rows { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; }
  li { display: grid; grid-template-columns: 56px minmax(0, 1fr) auto auto; gap: 10px; align-items: center; padding: 6px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); }
  .th { border-radius: 7px; overflow: hidden; background: var(--m-card); display: grid; place-items: center; aspect-ratio: 1; color: var(--fg-muted); } .tx { display: grid; min-width: 0; } strong { font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .acts { display: flex; gap: 6px; flex-wrap: wrap; justify-content: flex-end; }
  @media (max-width: 560px) { li { grid-template-columns: 48px minmax(0, 1fr) auto; } li > :global(button), .acts { grid-column: 1 / -1; justify-content: flex-start; } }
</style>
