<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Everything running or finished in this workspace that you are allowed to see: agent tasks and background jobs
  // (reading new files so they can be searched). States are the real states of durable jobs, not estimates.
  import { page } from '$app/state';
  import { get, post } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can } from '$lib/ws/current.svelte';
  import { msg, ago, duration, stateChip, stateLabel } from '$lib/ws/util';
  import State from '$lib/components/admin/State.svelte';

  const wid = $derived(page.params.wid ?? '');
  let runs = $state<any[]>([]), jobs = $state<any[]>([]), loading = $state(true), error = $state('');
  async function load() {
    try { [runs, jobs] = await Promise.all([get(`/api/workspaces/${wid}/runs?limit=50`), get(`/api/workspaces/${wid}/jobs?limit=100`)]); error = ''; }
    catch (e) { error = msg(e); } finally { loading = false; }
  }
  $effect(() => { wid; load(); });
  const active = (s: string) => ['queued', 'running', 'awaiting_approval'].includes(s);
  $effect(() => { if (![...runs, ...jobs].some((x) => active(x.status))) return; const t = setInterval(load, 3000); return () => clearInterval(t); });
  const cancelRun = (id: string) => post(`/api/runs/${id}/cancel`).then(() => { toast('Cancelling'); load(); }).catch((e) => toast(msg(e), 'bad'));
  const cancel = (id: string) => post(`/api/jobs/${id}/cancel`).then(() => { toast('Cancelling'); load(); }).catch((e) => toast(msg(e), 'bad'));
  const kind = (k: string) => ({ ingest: 'Reading a file', embed: 'Preparing a file for search by meaning', agent: 'Agent task' } as Record<string, string>)[k] ?? k;
  const background = $derived(jobs.filter((j) => j.kind !== 'agent'));
</script>

<State {loading} {error} retry={load} />
{#if !loading && !error}
  <section aria-label="Agent tasks">
    <h2 style="margin-bottom:8px">Agent tasks</h2>
    {#if !runs.length}<p class="muted">No agent tasks yet. Start one from the Agents tab.</p>
    {:else}<div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Agent tasks</caption>
      <thead><tr><th>Status</th><th>Task</th><th>Started</th><th>Took</th><th></th></tr></thead>
      <tbody>{#each runs as r (r.id)}<tr>
        <td><span class="chip {stateChip(r.status)}">{stateLabel(r.status)}</span></td>
        <td class="wrap"><a href="/workspaces/{wid}/runs/{r.id}">{r.task ? r.task.slice(0, 100) : 'Task started by another person'}</a></td>
        <td class="muted">{ago(r.created_at)}</td><td class="muted">{duration(r.started_at, r.finished_at)}</td>
        <td style="text-align:right">{#if active(r.status)}<button class="btn sm ghost" onclick={() => cancelRun(r.id)}>Cancel</button>{/if}</td></tr>{/each}</tbody></table></div>{/if}
  </section>

  <section aria-label="Background work" style="margin-top:28px">
    <h2 style="margin-bottom:8px">Background work</h2>
    <p class="small muted" style="margin:0 0 8px">New and changed files are read here so they can be searched and used by agents. {can('job.manage_all') ? 'You see everyone\'s work in this workspace.' : 'You see your own.'}</p>
    {#if !background.length}<p class="muted">Nothing yet.</p>
    {:else}<div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Background work</caption>
      <thead><tr><th>Status</th><th>What</th><th>Started</th><th>Took</th><th>Note</th><th></th></tr></thead>
      <tbody>{#each background as j (j.id)}<tr>
        <td><span class="chip {stateChip(j.status)}">{stateLabel(j.status)}</span></td><td>{kind(j.kind)}</td><td class="muted">{ago(j.created_at)}</td><td class="muted">{duration(j.started_at, j.finished_at)}</td>
        <td class="wrap small muted">{j.error || (j.result?.status === 'unsupported' ? 'This kind of file has no text to read' : j.result?.chunks ? `${j.result.chunks} passages` : '')}</td>
        <td style="text-align:right">{#if active(j.status)}<button class="btn sm ghost" onclick={() => cancel(j.id)}>Cancel</button>{/if}</td></tr>{/each}</tbody></table></div>{/if}
  </section>
{/if}
