<script lang="ts">
  // Usage, measured from what actually ran: storage, activity and agent work. No content, no estimates.
  import { page } from '$app/state';
  import { get } from '$lib/api';
  import { bytes, msg } from '$lib/ws/util';
  import State from '$lib/components/admin/State.svelte';
  const oid = $derived(page.params.oid ?? '');
  let u = $state<any>(null), loading = $state(true), error = $state('');
  async function load() { try { u = await get(`/api/orgs/${oid}/usage`); error = ''; } catch (e) { error = msg(e); } finally { loading = false; } }
  $effect(() => { oid; load(); });
  const pct = (a: number, mb: number) => (mb ? Math.min(100, Math.round((a / (mb * 1048576)) * 100)) : 0);
  const tok = (r: any) => (r.prompt_tokens + r.completion_tokens).toLocaleString();
</script>
<State {loading} {error} retry={load} />
{#if u}
  <div class="cards">
    <section class="panel"><div class="panel-h"><h3>Storage</h3></div><div class="panel-b">
      <p class="big">{bytes(u.storage.used_bytes)}{#if u.storage.quota_mb} <span class="muted small">of {u.storage.quota_mb} MB</span>{/if}</p>
      {#if u.storage.quota_mb}<div class="meter" role="progressbar" aria-valuenow={pct(u.storage.used_bytes, u.storage.quota_mb)} aria-valuemin="0" aria-valuemax="100" aria-label="Storage used"><span style="width:{pct(u.storage.used_bytes, u.storage.quota_mb)}%"></span></div>{/if}
      <p class="small muted">{u.storage.files} files{#if u.storage.trashed} · {u.storage.trashed} in the trash{/if}</p></div></section>
    <section class="panel"><div class="panel-h"><h3>What exists</h3></div><div class="panel-b"><dl class="kv"><dt>People</dt><dd>{u.counts.members}</dd><dt>Workspaces</dt><dd>{u.counts.workspaces}</dd><dt>Contexts</dt><dd>{u.counts.contexts}</dd><dt>Agents</dt><dd>{u.counts.agents}</dd></dl></div></section>
    <section class="panel"><div class="panel-h"><h3>Agent work</h3></div><div class="panel-b"><table class="t"><caption class="sr-only">Agent work</caption><thead><tr><th></th><th class="num">Last 24 hours</th><th class="num">Last 30 days</th></tr></thead>
      <tbody><tr><th scope="row">Tasks</th><td class="num">{u.agent_runs.last_24h.runs}{#if u.quotas.agent_runs_per_day} / {u.quotas.agent_runs_per_day}{/if}</td><td class="num">{u.agent_runs.last_30d.runs}</td></tr>
        <tr><th scope="row">Model calls</th><td class="num">{u.agent_runs.last_24h.model_calls}</td><td class="num">{u.agent_runs.last_30d.model_calls}</td></tr>
        <tr><th scope="row">Tool calls</th><td class="num">{u.agent_runs.last_24h.tool_calls}</td><td class="num">{u.agent_runs.last_30d.tool_calls}</td></tr>
        <tr><th scope="row">Tokens</th><td class="num">{tok(u.agent_runs.last_24h)}</td><td class="num">{tok(u.agent_runs.last_30d)}</td></tr>
        <tr><th scope="row">Working time (s)</th><td class="num">{u.agent_runs.last_24h.seconds}</td><td class="num">{u.agent_runs.last_30d.seconds}</td></tr>
        <tr><th scope="row">Sandbox time (s)</th><td class="num">{u.agent_runs.sandbox_seconds_24h}</td><td class="num"></td></tr></tbody></table></div></section>
    <section class="panel"><div class="panel-h"><h3>Background jobs (24 h)</h3></div><div class="panel-b">
      {#if !Object.keys(u.jobs_24h).length}<p class="muted">None.</p>{:else}<dl class="kv">{#each Object.entries(u.jobs_24h) as [k, v]}<dt>{k}</dt><dd>{v}</dd>{/each}</dl>{/if}</div></section>
  </div>
{/if}
<style>
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 16px; align-items: start; } .big { font-size: 24px; font-weight: 600; margin-bottom: 8px; }
  .meter { height: 8px; border-radius: 99px; background: var(--surface-2); border: 1px solid var(--border); overflow: hidden; margin-bottom: 8px; } .meter span { display: block; height: 100%; background: var(--accent); }
</style>
