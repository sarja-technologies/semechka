<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg } from '$lib/components/admin/util';
  let s = $state<any>(null); let loading = $state(true); let error = $state(''); let at = $state('');
  async function load() { loading = true; error = ''; try { s = await get('/api/admin/system'); at = new Date().toLocaleTimeString(); } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  const pct = (a?: number | null, b?: number | null) => (a != null && b ? Math.round((a / b) * 100) : null);
  const dur = (n: number) => (n > 3600 ? `${Math.floor(n / 3600)} h ${Math.floor((n % 3600) / 60)} min` : `${Math.floor(n / 60)} min`);
</script>
<Page title="System & Nodes" lead="Measured values from this host and its backends. This release manages a single node; there is no cluster scheduler or queue, so none is shown.">
  {#snippet actions()}<button class="btn" onclick={load} disabled={loading}>Refresh</button>{#if at}<span class="muted small">Read at {at}</span>{/if}{/snippet}
  <State loading={loading && !s} {error} retry={load} />
  {#if s}
    <div class="g">
      <section class="panel"><div class="panel-h"><h3>Host</h3></div><div class="panel-b"><dl class="kv"><dt>Platform</dt><dd>{s.host.os}</dd><dt>Architecture</dt><dd>{s.host.arch}</dd><dt>CPU</dt><dd>{s.host.cpu_model ?? 'Unavailable'} ({s.host.cpu_count} threads)</dd><dt>Load (1 min)</dt><dd>{s.host.load_1m ?? 'Unavailable'}</dd><dt>Service uptime</dt><dd>{dur(s.uptime_s)}</dd></dl>
        {#if s.host.memory_total_mb}{@const used = s.host.memory_total_mb - s.host.memory_available_mb}<div class="bar" role="img" aria-label="Memory {pct(used, s.host.memory_total_mb)} percent used"><span style="width:{pct(used, s.host.memory_total_mb)}%"></span></div><p class="small">Memory: {used} of {s.host.memory_total_mb} MiB in use ({pct(used, s.host.memory_total_mb)}%)</p>{:else}<p class="muted">Memory: Unavailable</p>{/if}
        <p class="small" style="margin:0">Storage for data: {s.storage.free_gb ?? '—'} GiB free of {s.storage.total_gb ?? '—'} GiB</p></div></section>
      <section class="panel"><div class="panel-h"><h3>Accelerators</h3></div><div class="panel-b">
        {#if s.gpus === null}<p class="muted">Unavailable. {s.gpu_note}</p>
        {:else}{#each s.gpus as g}<div style="margin-bottom:12px"><strong>{g.name}</strong> <span class="mono tiny muted">driver {g.driver}</span>
          {#if g.unified_memory}<p class="small" style="margin:4px 0 0">Unified memory: this accelerator shares the system RAM shown under Host, so the driver reports no separate VRAM · utilisation {g.utilization_pct ?? 'n/a'}% · {g.temperature_c ?? 'n/a'} °C</p>
          {:else}<div class="bar" role="img" aria-label="VRAM {pct(g.vram_used_mb, g.vram_total_mb)} percent used"><span style="width:{pct(g.vram_used_mb, g.vram_total_mb)}%"></span></div>
          <p class="small" style="margin:4px 0 0">VRAM {g.vram_used_mb} of {g.vram_total_mb} MiB · utilisation {g.utilization_pct ?? 'n/a'}% · {g.temperature_c ?? 'n/a'} °C</p>{/if}</div>{/each}{/if}</div></section>
    </div>
    <h2 style="margin:24px 0 8px">Backends</h2>
    {#if !s.backends.length}<p class="muted">No backend configured. <a href="/admin/backends">Add one</a>.</p>{/if}
    <div class="stack">{#each s.backends as b}<section class="panel"><div class="panel-h"><h3>{b.name}</h3><span class="chip {b.health.ok ? 'ok' : 'bad'}">{b.health.ok ? 'Reachable' : 'Not reachable'}</span></div><div class="panel-b">
      <dl class="kv"><dt>Adapter</dt><dd>{b.adapter}</dd><dt>Version</dt><dd>{b.health.version ?? 'not reported'}</dd><dt>Detail</dt><dd>{b.health.detail}</dd><dt>Location</dt><dd>{b.external ? 'External service' : 'Local / own network'}</dd></dl>
      {#if b.system}<details style="margin-top:8px"><summary>Backend-reported system information</summary><pre class="mono tiny" style="overflow:auto;max-height:300px">{JSON.stringify(b.system, null, 2)}</pre></details>
      {:else if !b.telemetry_supported}<p class="hint" style="margin:8px 0 0">This adapter does not provide hardware telemetry.</p>{:else if b.health.ok}<p class="hint" style="margin:8px 0 0">The backend returned no telemetry.</p>{/if}</div></section>{/each}</div>
  {/if}
</Page>
<style>.g { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); } .bar { height: 10px; background: var(--surface-2); border: 1px solid var(--border-strong); border-radius: 3px; overflow: hidden; margin: 8px 0 4px; } .bar span { display: block; height: 100%; background: var(--accent); }</style>
