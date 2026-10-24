<script lang="ts">
  // Live engine state and model load/unload. Everything shown is what the engine reports through /api/engines
  // (nothing estimated here); what each person sees is filtered by the server. Before loading or unloading, the
  // server's own check is shown: blockers (the button stays off) and warnings (shown before you confirm).
  import { onMount, onDestroy } from 'svelte';
  import { get, post } from '$lib/api';
  import { can } from '$lib/session.svelte';
  import { toast } from '$lib/toast.svelte';
  import State from './State.svelte';
  import { errMsg } from './util';
  import Icon from '$lib/components/Icon.svelte';
  import ModelIcon from '$lib/components/chat/ModelIcon.svelte';

  let engines = $state<any[]>([]), models = $state<any[]>([]), loading = $state(true), error = $state(''), q = $state(''), at = $state(0);
  const operate = $derived(can('ops.model.load'));
  let timer: ReturnType<typeof setInterval> | undefined;

  async function refresh() {
    try { engines = (await get('/api/engines')).engines; error = ''; at = Date.now(); }
    catch (e) { error = errMsg(e); }
    loading = false;
  }
  async function loadModels() {
    if (!can('admin.models.manage') && !operate) return;
    try { models = (await get('/api/admin/models?per_page=500')).items ?? []; } catch { models = []; }
  }
  const opName = (o: any) => { const m = models.find((x) => x.id === o.model_id); return m ? name(m) : o.model; };
  const busyOp = $derived(engines.some((e) => e.operation?.state === 'running'));
  function schedule() { clearInterval(timer); timer = setInterval(() => { if (!document.hidden) refresh(); }, busyOp ? 2000 : 6000); }
  $effect(() => { void busyOp; schedule(); });
  onMount(() => { refresh(); loadModels(); });
  onDestroy(() => clearInterval(timer));

  // ---- formatting ---------------------------------------------------------------------------------------------
  const gb = (mb?: number | null) => (mb == null ? '' : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`);
  const ago = (t?: number | null) => { if (!t) return ''; const s = Math.max(0, Math.round(Date.now() / 1000 - t)); return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : `${Math.round(s / 3600)} h ago`; };
  const LLM: Record<string, string> = { ready: 'Ready', loading: 'Loading', stopped: 'Nothing loaded', error: 'Failed', starting: 'Starting' };
  const KIND: Record<string, string> = { llm: 'Chat', media: 'Picture or video', other: 'Other' };
  const kindOf = (m: any) => (m.real_id?.startsWith('image:') || m.real_id?.startsWith('video:') ? 'media' : m.real_id?.startsWith('local:') ? 'llm' : 'other');
  const name = (m: any) => m.meta?.engine_name || m.alias;
  const known = $derived(new Set(engines.map((e) => e.id)));
  const shown = $derived(models.filter((m) => known.has(m.backend_id) && m.available !== false && (!q || `${m.alias} ${name(m)}`.toLowerCase().includes(q.toLowerCase()))));
  const loadedIds = $derived(new Set(engines.flatMap((e) => [e.status?.llm?.model_ref?.id, ...(e.status?.worker?.model_refs ?? []).map((r: any) => r?.id)]).filter(Boolean)));
  const loadingId = $derived(engines.find((e) => e.operation?.state === 'running')?.operation?.model_id);
  const stateOf = (m: any) => (loadingId === m.id ? 'working' : loadedIds.has(m.id) ? 'loaded' : 'idle');

  // ---- load / unload: show the server's check, then confirm ----------------------------------------------------
  let open = $state<string | null>(null), verdict = $state<any>(null), checking = $state(false), sending = $state(false), opErr = $state('');
  async function ask(m: any) {
    if (open === m.id) { open = null; return; }
    open = m.id; verdict = null; opErr = ''; checking = true;
    try { verdict = await get(`/api/admin/models/${m.id}/lifecycle`); } catch (e) { opErr = errMsg(e); }
    checking = false;
  }
  async function act(m: any, op: 'load' | 'unload') {
    sending = true; opErr = '';
    try {
      const r = await post(`/api/admin/models/${m.id}/${op}`);
      if (r.noop) toast(r.noop);
      else if (r.operation?.state === 'failed') opErr = r.operation.error || 'The engine refused.';
      else { toast(op === 'load' ? (r.operation?.state === 'done' ? `${name(m)} is loaded.` : `Loading ${name(m)}…`) : `${name(m)} was unloaded.`); open = null; }
    } catch (e) { opErr = errMsg(e); }
    sending = false; refresh();
  }
</script>

<State {loading} {error} retry={refresh} />
{#each engines as e (e.id)}
  {@const s = e.status}
  <section class="panel eng" aria-label={e.name}>
    <div class="panel-h">
      <div class="eh"><h2>{e.name}</h2>
        {#if !e.reachable}<span class="chip bad">Not reachable</span>
        {:else if s?.health?.status === 'ok'}<span class="chip ok">Healthy</span>
        {:else if s?.health}<span class="chip warn">{s.health.status}</span>{/if}
        {#if s?.engine?.version}<span class="tiny muted">Engine {s.engine.version}{s.engine.api_version ? ` · API ${s.engine.api_version}` : ''}</span>{/if}
      </div>
      <span class="tiny muted" aria-live="off">{at ? `Updated ${new Date(at).toLocaleTimeString()}` : ''}</span>
    </div>
    <div class="panel-b grid">
      {#if e.operation}
        {@const o = e.operation}
        <div class="notice {o.state === 'failed' ? 'bad' : o.state === 'running' ? 'info' : ''}" role="status">
          <div class="grow">
            {#if o.state === 'running'}<strong>{o.op === 'load' ? 'Loading' : 'Unloading'} {opName(o)}…</strong> started {ago(o.started_at)}. Other people's replies keep running.
            {:else if o.state === 'failed'}<strong>{o.op === 'load' ? 'Loading' : 'Unloading'} {opName(o)} failed.</strong> {o.error}
            {:else}<strong>{o.op === 'load' ? 'Loaded' : 'Unloaded'} {opName(o)}</strong> {ago(o.finished_at)}.{/if}
          </div>
        </div>
      {/if}
      {#if s?.memory}
        {@const mm = s.memory}
        {@const pct = mm.total_mb && mm.used_mb != null ? Math.round((mm.used_mb / mm.total_mb) * 100) : null}
        <div class="stat">
          <span class="k">Memory</span>
          {#if pct != null}
            <div class="bar" role="meter" aria-valuemin="0" aria-valuemax="100" aria-valuenow={pct} aria-label="Memory in use"><span style:width="{pct}%" class:high={pct > 85}></span></div>
            <span class="v">{gb(mm.used_mb)} of {gb(mm.total_mb)} in use{mm.unified ? ' (shared by the GPU and the system)' : ''}{mm.gpu ? ` · ${mm.gpu}` : ''}</span>
          {:else}<span class="v muted">Not reported</span>{/if}
        </div>
      {/if}
      {#if s?.llm}
        <div class="stat"><span class="k">Chat model</span>
          <span class="v">{#if s.llm.model_id}<strong>{s.llm.model_ref?.alias ?? s.llm.model_id}</strong> · {LLM[s.llm.state] ?? s.llm.state}{s.llm.ctx ? ` · ${s.llm.ctx.toLocaleString()} context` : ''}{s.llm.vram_estimate_mb ? ` · about ${gb(s.llm.vram_estimate_mb)}` : ''}
            {:else}{LLM[s.llm.state] ?? s.llm.state ?? 'Unknown'}{/if}
            {#if s.llm.active_streams}<span class="chip warn">{s.llm.active_streams} {s.llm.active_streams === 1 ? 'reply' : 'replies'} streaming</span>{/if}
            {#if s.llm.error}<span class="err small">{s.llm.error}</span>{/if}</span></div>
      {/if}
      {#if s?.worker}
        <div class="stat"><span class="k">Pictures and video</span>
          <span class="v">{s.worker.busy ? 'Working' : s.worker.alive ? 'Idle' : 'Not running'}{#if s.worker.model_refs?.length}{' · holding'} {s.worker.model_refs.map((r: any, i: number) => r?.alias ?? s.worker.loaded[i]).join(', ')}{/if}</span></div>
      {/if}
      {#if s?.jobs}
        <div class="stat"><span class="k">Engine jobs</span>
          <span class="v">{s.jobs.running ?? 0} running · {s.jobs.queued ?? 0} waiting{s.jobs.concurrency ? ` · ${s.jobs.concurrency} at a time` : ''}{s.jobs.busy_reason ? ` · ${s.jobs.busy_reason}` : ''}</span></div>
      {/if}
      <div class="stat"><span class="k">This server</span><span class="v">{e.this_server.running} in progress · {e.this_server.waiting} waiting their turn</span></div>
      {#if e.queue?.length}
        <div class="tw"><table class="table">
          <thead><tr><th>Job</th><th>Model</th><th>Status</th><th>Progress</th><th>From</th></tr></thead>
          <tbody>{#each e.queue as j, i (j.id ?? i)}<tr>
            <td>{j.kind}</td><td>{j.model_ref?.alias ?? j.model ?? ''}</td>
            <td>{j.status}{j.queue_position != null ? ` (#${j.queue_position})` : ''}{j.waiting_reason ? ` · ${j.waiting_reason}` : ''}</td>
            <td>{j.progress != null ? `${Math.round(j.progress * 100)}%` : ''}</td>
            <td>{j.own ? 'This server' : 'Another client (contents hidden)'}</td></tr>{/each}</tbody>
        </table></div>
      {:else if e.queue}<p class="small muted">No engine jobs running or waiting.</p>{/if}
    </div>
  </section>
{/each}

{#if engines.length && models.length}
  <section class="panel">
    <div class="panel-h"><h2>Models on these engines</h2>
      <div class="search"><Icon name="search" size={15} /><label class="sr-only" for="eq">Search models</label><input id="eq" type="search" placeholder="Search" bind:value={q} /></div>
    </div>
    <ul class="ml">
      {#each shown as m (m.id)}
        {@const st = stateOf(m)}
        {@const k = kindOf(m)}
        <li class:open={open === m.id}>
          <div class="mr">
            <ModelIcon icon={m.icon} label={name(m)} size={20} />
            <span class="mn"><strong>{name(m)}</strong><span class="tiny muted">{m.alias !== name(m) ? `${m.alias} · ` : ''}{KIND[k]}{m.meta?.vram_estimate_mb ? ` · about ${gb(m.meta.vram_estimate_mb)}` : m.meta?.size_bytes ? ` · ${gb(Math.round(m.meta.size_bytes / 1048576))} on disk` : ''}{m.enabled ? '' : ' · off for people'}</span></span>
            {#if st === 'loaded'}<span class="chip ok">Loaded</span>{:else if st === 'working'}<span class="chip info">Working…</span>{/if}
            {#if operate}<button class="btn sm" aria-expanded={open === m.id} onclick={() => ask(m)} disabled={st === 'working'}>{st === 'loaded' ? 'Unload…' : k === 'media' ? 'Free memory…' : 'Load…'}</button>{/if}
          </div>
          {#if open === m.id}
            <div class="chk">
              {#if checking}<p class="small muted" role="status">Checking with the engine…</p>
              {:else if verdict}
                {@const op = st === 'loaded' || k === 'media' ? 'unload' : 'load'}
                {@const v = verdict[op]}
                {#if v.noop}<p class="small">{v.noop}</p>
                {:else}
                  {#each v.blockers as b}<div class="notice bad" role="alert"><div>{b}</div></div>{/each}
                  {#each v.warnings as w}<div class="notice warn"><div>{w}</div></div>{/each}
                  {#if v.allowed}
                    <div class="row" style="gap:8px">
                      <button class="btn primary sm" disabled={sending} onclick={() => act(m, op)}>{op === 'load' ? `Load ${name(m)}` : `Unload ${name(m)}`}</button>
                      <button class="btn ghost sm" onclick={() => (open = null)}>Cancel</button>
                      {#if op === 'load'}<span class="tiny muted">The chat model that is loaded now is replaced; loading can take a few minutes for large models.</span>{/if}
                    </div>
                  {/if}
                {/if}
                {#if k === 'media'}<p class="tiny muted">Picture and video models load by themselves when someone uses them; here you can only free their memory.</p>{/if}
              {/if}
              {#if opErr}<div class="notice bad" role="alert"><div>{opErr}</div></div>{/if}
            </div>
          {/if}
        </li>
      {:else}<li class="muted small pad">No model matches.</li>{/each}
    </ul>
  </section>
{/if}

<style>
  .eng { margin-bottom: 16px; }
  .eh { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .eh h2 { font-size: 15px; margin: 0; }
  .grid { display: grid; gap: 10px; }
  .grid .notice { margin: 0; }
  .stat { display: grid; grid-template-columns: 160px minmax(0, 1fr); gap: 4px 12px; align-items: center; font-size: 14px; }
  .stat .k { color: var(--fg-muted); font-size: 13px; }
  .stat .v { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
  .stat .bar { grid-column: 2; }
  .stat .bar + .v { grid-column: 2; font-size: 13px; color: var(--fg-2); }
  .bar { height: 8px; border-radius: 999px; background: var(--bg-2); overflow: hidden; max-width: 420px; }
  .bar span { display: block; height: 100%; background: var(--fg-2); border-radius: inherit; transition: width var(--medium) var(--ease); }
  .bar span.high { background: var(--warn); }
  .tw { overflow-x: auto; }
  .search { display: flex; align-items: center; gap: 6px; color: var(--fg-muted); padding: 0 10px; border-radius: 999px; background: var(--bg-2); min-height: 30px; }
  .search input { all: unset; font-size: 13px; color: var(--fg); width: 160px; }
  .ml { list-style: none; margin: 0; padding: 4px; }
  .ml li { border-radius: var(--radius-sm); }
  .ml li.open { background: var(--bg-2); }
  .mr { display: flex; align-items: center; gap: 10px; padding: 8px; min-height: 44px; }
  .mn { flex: 1; min-width: 0; display: grid; gap: 1px; }
  .mn strong { font-weight: 500; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chk { padding: 0 8px 10px 38px; display: grid; gap: 8px; }
  .chk .notice { margin: 0; }
  .pad { padding: 12px; }
  @media (max-width: 719px) { .stat { grid-template-columns: minmax(0, 1fr); } .stat .bar, .stat .bar + .v { grid-column: 1; } .chk { padding-left: 8px; } }
</style>
