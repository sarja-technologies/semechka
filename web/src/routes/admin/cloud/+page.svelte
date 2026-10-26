<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Operational view of workspaces, agents and the sandbox: switches, queue, measured durations, memory and storage.
  // Counts and measurements only; it never shows what a job is about.
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import { bytes, msg, ago, duration, stateChip, stateLabel } from '$lib/ws/util';
  import { can } from '$lib/session.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';

  let s = $state<any>(null), jobs = $state<any[]>([]), loading = $state(true), error = $state('');
  async function load() {
    try { [s, jobs] = await Promise.all([get('/api/admin/cloud/status'), can('ops.queue.view') ? get('/api/admin/cloud/jobs?limit=40') : []]); error = ''; }
    catch (e) { error = msg(e); } finally { loading = false; }
  }
  onMount(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); });
  const perf = $derived<[string, any][]>(s ? Object.entries(s.performance_24h) : []);
  const sw = $derived(s ? [
    ['Workspaces area', 'cloud.workspaces.enabled', s.workspaces_enabled],
    ['Cloud agents', 'cloud.agents.enabled', s.sandbox.agents_enabled],
    ['Code sandbox', 'sandbox.enabled', s.sandbox.enabled]] : []);
</script>
<Page title="Cloud workspaces" lead="Switches, the background queue and measured performance of workspaces, agents and the code sandbox.">
  <State {loading} {error} retry={load} />
  {#if s}
    <div class="cards">
      <section class="panel"><div class="panel-h"><h3>Switches</h3><a class="btn sm ghost" href="/admin/settings">Open settings</a></div><div class="panel-b">
        <dl class="kv">{#each sw as [l, , on]}<dt>{l}</dt><dd>{on === undefined ? 'see settings' : on ? 'On' : 'Off'}</dd>{/each}</dl>
        <p class="tiny muted" style="margin-top:8px">Each is a setting named <code>cloud.workspaces.enabled</code>, <code>cloud.agents.enabled</code> and <code>sandbox.enabled</code>. All start off.</p></div></section>
      <section class="panel"><div class="panel-h"><h3>Services</h3></div><div class="panel-b"><dl class="kv">
        <dt>Search by meaning</dt><dd>{s.embeddings.configured ? (s.embeddings.healthy ? `Ready (${s.embeddings.model})` : 'Configured, not answering') : 'Not configured: keyword search only'}</dd>
        <dt>Code sandbox</dt><dd>{#if !s.sandbox.enabled}Off{:else if s.sandbox.ready}Ready ({s.sandbox.image}){:else}Not ready: {s.sandbox.detail}{/if}</dd>
        <dt>Memory available</dt><dd>{s.memory_available_mb != null ? bytes(s.memory_available_mb * 1048576) : 'unknown'}</dd><dt>Load average</dt><dd>{s.load_avg.join(' / ')}</dd>
        <dt>Agent tasks in progress</dt><dd>{s.agent_runs_active}</dd><dt>Oldest queued job</dt><dd>{s.oldest_queued_age_s != null ? `${Math.round(s.oldest_queued_age_s)} s` : 'none waiting'}</dd></dl></div></section>
      <section class="panel"><div class="panel-h"><h3>Stored files</h3></div><div class="panel-b"><p class="big">{bytes(s.storage.total_bytes)}</p>
        <table class="t"><caption class="sr-only">Storage by organisation</caption><tbody>{#each s.storage.organisations as o}<tr><td>{o.slug}</td><td class="num">{bytes(o.bytes)}{#if o.quota_mb} <span class="muted">/ {o.quota_mb} MB</span>{/if}</td></tr>{/each}</tbody></table></div></section>
    </div>
    <section class="panel" style="margin-top:16px" aria-label="Measured performance"><div class="panel-h"><h3>Measured over the last 24 hours</h3></div>
      {#if !Object.keys(s.performance_24h).length}<div class="panel-b"><p class="muted">No finished jobs yet.</p></div>
      {:else}<div class="tablewrap" use:scrollable style="border:0;border-radius:0"><table class="t"><caption class="sr-only">Job durations</caption><thead><tr><th>Kind</th><th class="num">Finished</th><th class="num">Average</th><th class="num">95th percentile</th><th class="num">Slowest</th><th>By state</th></tr></thead>
        <tbody>{#each perf as [k, v]}<tr><td>{k}</td><td class="num">{v.finished_24h}</td><td class="num">{v.avg_s} s</td><td class="num">{v.p95_s} s</td><td class="num">{v.max_s} s</td>
          <td>{#each Object.entries(s.jobs[k] ?? {}) as [st, n]}<span class="chip {stateChip(st)}">{stateLabel(st)} {n}</span> {/each}</td></tr>{/each}</tbody></table></div>{/if}
    </section>
    {#if jobs.length}
      <section class="panel" style="margin-top:16px" aria-label="Queue"><div class="panel-h"><h3>Queue</h3><span class="small muted">What kind of work and in which organisation. Never what it is about.</span></div>
        <div class="tablewrap" use:scrollable style="border:0;border-radius:0"><table class="t"><caption class="sr-only">Recent jobs</caption><thead><tr><th>State</th><th>Kind</th><th>Organisation</th><th>Queued</th><th>Took</th><th>Tries</th><th>Note</th></tr></thead>
          <tbody>{#each jobs as j (j.id)}<tr><td><span class="chip {stateChip(j.status)}">{stateLabel(j.status)}</span></td><td>{j.kind}</td><td class="mono tiny">{j.org_id.slice(0, 10)}</td><td class="muted">{ago(j.created_at)}</td><td class="muted">{duration(j.started_at, j.finished_at)}</td><td class="num">{j.attempts}</td><td class="wrap small muted">{j.error}</td></tr>{/each}</tbody></table></div></section>
    {/if}
  {/if}
</Page>
<style>.cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); gap: 16px; align-items: start; } .big { font-size: 24px; font-weight: 600; margin-bottom: 8px; }</style>
