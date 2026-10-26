<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Agents: persistent identities with instructions, a model, the tools they may use and the contexts they may search.
  // What an agent can actually do is always the overlap of its configuration, the workspace rules and the person running it.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { get, post, patch, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can } from '$lib/ws/current.svelte';
  import { msg, ago, stateChip, stateLabel, duration } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import State from '$lib/components/admin/State.svelte';

  const wid = $derived(page.params.wid ?? '');
  const sel = $derived(page.url.searchParams.get('a') ?? '');
  let list = $state<any[]>([]), tools = $state<any[]>([]), models = $state<any[]>([]), contexts = $state<any[]>([]), loading = $state(true), error = $state('');
  let agent = $state<any>(null), runs = $state<any[]>([]), mem = $state<any>(null), aErr = $state('');
  const manage = $derived(can('agent.manage') && !cur.ws?.archived);
  const mayRun = $derived(can('agent.use') && !cur.ws?.archived);

  async function loadList() {
    try { list = await get(`/api/workspaces/${wid}/agents`); error = ''; } catch (e) { error = msg(e); } finally { loading = false; }
  }
  async function loadSide() {
    try { [tools, models, contexts] = await Promise.all([get('/api/agent-tools'), get(`/api/workspaces/${wid}/models?mode=chat`).catch(() => []), get(`/api/workspaces/${wid}/contexts`).catch(() => [])]); } catch { /* shown when the editor opens */ }
  }
  async function loadAgent() {
    runs = []; mem = null; aErr = '';
    if (!sel) { agent = null; return; }
    try {
      agent = await get(`/api/agents/${sel}`);
      runs = (await get(`/api/workspaces/${wid}/runs?limit=30`)).filter((r: any) => r.agent_id === sel);
      if (agent.memory.persistent && can('mem.read')) mem = await get(`/api/agents/${sel}/memory`);
    } catch (e) { aErr = msg(e); agent = null; }
  }
  $effect(() => { wid; loadList(); loadSide(); });
  $effect(() => { sel; loadAgent(); });
  // while any run of this agent is active, keep its list fresh
  $effect(() => { if (!runs.some((r) => ['queued', 'running', 'awaiting_approval'].includes(r.status))) return; const t = setInterval(loadAgent, 3000); return () => clearInterval(t); });
  const choose = (id: string) => goto(id ? `/workspaces/${wid}/agents?a=${id}` : `/workspaces/${wid}/agents`);
  const toolInfo = (n: string) => tools.find((t) => t.name === n);
  const GROUPS: [string, string[]][] = [['Read and search', ['search_context', 'list_files', 'read_file', 'read_table', 'table_stats', 'calc']],
    ['Create and revise files', ['create_document', 'create_spreadsheet', 'create_presentation', 'write_text_file']], ['Memory', ['remember', 'recall']],
    ['Run code and reach the internet', ['python', 'shell', 'fetch_url']]];

  // ---- editor ----
  let dlg = $state(false), isNew = $state(true), busy = $state(false), formErr = $state('');
  let f = $state<any>({});
  function blank() {
    return { name: '', description: '', instructions: '', model_id: '', tools: ['search_context', 'list_files', 'read_file', 'read_table', 'table_stats', 'calc', 'create_document'], allCtx: true, context_ids: [] as string[],
      persistent: false, retention: '', approval: 'sensitive', max_steps: 12, timeout_s: 900, allow: '' };
  }
  function openNew() { isNew = true; f = blank(); formErr = ''; dlg = true; }
  function openEdit() {
    const a = agent; isNew = false; formErr = '';
    f = { name: a.name, description: a.description, instructions: a.instructions ?? '', model_id: a.model_id ?? '', tools: [...a.tools], allCtx: a.context_ids.includes('*'), context_ids: a.context_ids.filter((c: string) => c !== '*'),
      persistent: a.memory.persistent, retention: a.memory.retention_days ?? '', approval: a.approval, max_steps: a.limits.max_steps, timeout_s: a.limits.timeout_s, allow: (a.network?.allow ?? []).join('\n') };
    dlg = true;
  }
  const toggleTool = (n: string) => { f.tools = f.tools.includes(n) ? f.tools.filter((x: string) => x !== n) : [...f.tools, n]; };
  const body = () => ({ name: f.name, description: f.description, instructions: f.instructions, model_id: f.model_id || null, tools: f.tools,
    context_ids: f.allCtx ? ['*'] : f.context_ids, memory: { persistent: f.persistent, retention_days: f.retention ? Number(f.retention) : null }, approval: f.approval,
    limits: { max_steps: Number(f.max_steps), timeout_s: Number(f.timeout_s) }, network: { allow: f.allow.split(/\s+/).filter(Boolean) } });
  async function save() {
    busy = true; formErr = '';
    try {
      const a = isNew ? await post(`/api/workspaces/${wid}/agents`, body()) : await patch(`/api/agents/${sel}`, body());
      dlg = false; toast(isNew ? 'Agent created' : 'Agent updated'); await loadList(); if (isNew) choose(a.id); else await loadAgent();
    } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const archive = () => patch(`/api/agents/${sel}`, { archived: true }).then(() => { toast('Agent archived'); choose(''); loadList(); }).catch((e) => toast(msg(e), 'bad'));

  // ---- run ----
  let task = $state(''), starting = $state(false), runErr = $state('');
  async function start() {
    starting = true; runErr = '';
    try { const r = await post(`/api/agents/${sel}/runs`, { task }); task = ''; goto(`/workspaces/${wid}/runs/${r.run_id}`); } catch (e) { runErr = msg(e); } finally { starting = false; }
  }

  // ---- memory ----
  let newMem = $state(''), editMem = $state<string | null>(null), editText = $state('');
  const addMem = () => post(`/api/agents/${sel}/memory`, { text: newMem }).then(() => { newMem = ''; loadAgent(); }).catch((e) => toast(msg(e), 'bad'));
  const saveMem = (id: string) => patch(`/api/memory/${id}`, { text: editText }).then(() => { editMem = null; toast('Memory corrected'); loadAgent(); }).catch((e) => toast(msg(e), 'bad'));
  const delMem = (id: string) => del(`/api/memory/${id}`).then(() => { toast('Forgotten'); loadAgent(); }).catch((e) => toast(msg(e), 'bad'));
</script>

<div class="ag">
  <aside class="side" aria-label="Agents">
    {#if manage}<button class="btn primary" style="width:100%" onclick={openNew}><Icon name="plus" size={16} /> New agent</button>{/if}
    <State {loading} {error} retry={loadList} />
    {#if !loading && !error && !list.length}<p class="muted small">No agents yet. An agent works on tasks in this workspace: it reads the documents it is allowed to, uses its tools, and saves what it makes here.</p>{/if}
    <ul>{#each list as a (a.id)}
      <li><button class="ci" class:on={a.id === sel} aria-current={a.id === sel ? 'true' : undefined} onclick={() => choose(a.id)}><span class="n">{a.name}</span>
        <span class="tiny muted">{a.tools.length} tool{a.tools.length === 1 ? '' : 's'}{#if a.memory.persistent} · remembers{/if}</span></button></li>
    {/each}</ul>
  </aside>

  <section aria-label="Agent details">
    {#if !sel}
      <div class="panel"><div class="panel-b"><p><strong>Choose an agent</strong>{manage ? ', or create one.' : '.'}</p>
        <p class="muted">Agents never get more access than the person who starts them. Actions that run code or reach the internet can wait for a person's approval first.</p></div></div>
    {:else if aErr}<div class="notice bad" role="alert"><div>{aErr}</div></div>
    {:else if agent}
      <header class="head">
        <div class="grow"><h2>{agent.name}</h2>{#if agent.description}<p class="muted" style="margin:2px 0 0">{agent.description}</p>{/if}</div>
        <div class="row">
          <span class="chip">Config v{agent.version}</span>
          {#if manage}<button class="btn sm" onclick={openEdit}><Icon name="edit" size={14} /> Edit</button><button class="btn sm danger" onclick={archive}>Archive</button>{/if}
        </div>
      </header>

      <section class="panel" aria-label="Start a task">
        <div class="panel-h"><h3>Give it a task</h3></div>
        <div class="panel-b">
          {#if mayRun}
            <form onsubmit={(e) => { e.preventDefault(); start(); }}>
              <div class="field"><label for="tk" class="sr-only">Task</label>
                <textarea id="tk" class="input" rows="4" bind:value={task} placeholder="Describe what you want done, for example: Read our October campaign brief and budget and write a strategy document with a schedule." maxlength="20000"></textarea></div>
              <div class="row"><button class="btn primary" disabled={starting || !task.trim()}>Start task</button>
                <span class="small muted">It keeps working if you close this page. You can come back to it under Tasks.</span></div>
              {#if runErr}<div class="err" role="alert" style="margin-top:8px">{runErr}</div>{/if}
            </form>
          {:else}<p class="muted">Your role in this workspace does not include running agents.</p>{/if}
        </div>
      </section>

      <section class="panel" style="margin-top:16px" aria-label="Configuration">
        <div class="panel-h"><h3>What it can do</h3></div>
        <div class="panel-b">
          <dl class="kv">
            <dt>Model</dt><dd>{models.find((m) => m.id === agent.model_id)?.alias ?? (agent.model_id ? 'A model you may not use' : 'First available chat model')}</dd>
            <dt>Tools</dt><dd class="tl">{#each agent.tools as t}<span class="chip" title={toolInfo(t)?.summary}>{t}{#if toolInfo(t)?.sensitive} <Icon name="shield" size={11} />{/if}</span>{/each}</dd>
            <dt>Contexts</dt><dd>{agent.context_ids.includes('*') ? 'All available in this workspace' : agent.context_ids.length ? `${agent.context_ids.length} chosen` : 'None'}</dd>
            <dt>Approval</dt><dd>{agent.approval === 'always' ? 'Every action waits for a person' : agent.approval === 'never' ? 'Never asks' : 'Code, command-line and internet actions wait for a person'}</dd>
            <dt>Memory</dt><dd>{agent.memory.persistent ? `Remembers between tasks${agent.memory.retention_days ? `, for ${agent.memory.retention_days} days` : ''}` : 'Off: starts fresh every time'}</dd>
            <dt>Limits</dt><dd>{agent.limits.max_steps} steps, {Math.round(agent.limits.timeout_s / 60)} min</dd>
            {#if agent.network?.allow?.length}<dt>Websites</dt><dd>{agent.network.allow.join(', ')}</dd>{/if}
          </dl>
          {#if manage && agent.instructions}<h4 style="margin:12px 0 4px">Instructions</h4><p class="small prewrap">{agent.instructions}</p>{/if}
        </div>
      </section>

      <section class="panel" style="margin-top:16px" aria-label="Recent tasks">
        <div class="panel-h"><h3>Recent tasks</h3><a class="btn sm ghost" href="/workspaces/{wid}/tasks">All tasks</a></div>
        {#if !runs.length}<div class="panel-b"><p class="muted">No tasks yet.</p></div>
        {:else}<div class="tablewrap" use:scrollable style="border:0;border-radius:0"><table class="t"><caption class="sr-only">Recent tasks</caption>
          <thead><tr><th>Status</th><th>Task</th><th>Started</th><th>Took</th></tr></thead>
          <tbody>{#each runs as r (r.id)}<tr><td><span class="chip {stateChip(r.status)}">{stateLabel(r.status)}</span></td>
            <td class="wrap"><a href="/workspaces/{wid}/runs/{r.id}">{(r.task ?? 'Task by another person').slice(0, 90)}</a></td>
            <td class="muted">{ago(r.created_at)}</td><td class="muted">{duration(r.started_at, r.finished_at)}</td></tr>{/each}</tbody></table></div>{/if}
      </section>

      {#if agent.memory.persistent && mem}
        <section class="panel" style="margin-top:16px" aria-label="Memory">
          <div class="panel-h"><h3>Memory</h3><span class="small muted">Only what was saved on purpose. You can correct or delete any of it.</span></div>
          <div class="panel-b">
            {#if !mem.items.length}<p class="muted">Nothing saved yet.</p>{/if}
            <ul class="mem">{#each mem.items as m (m.id)}
              <li>{#if editMem === m.id}
                <textarea class="input" rows="3" aria-label="Correct this memory" bind:value={editText}></textarea>
                <div class="row" style="margin-top:6px"><button class="btn sm primary" onclick={() => saveMem(m.id)} disabled={!editText.trim()}>Save</button><button class="btn sm" onclick={() => (editMem = null)}>Cancel</button></div>
              {:else}
                <p class="prewrap">{m.text}</p>
                <div class="row tiny muted"><span>{m.source === 'user' ? 'Written by a person' : 'Saved by the agent'} · {ago(m.created_at)}{#if m.expires_at} · expires {new Date(m.expires_at * 1000).toLocaleDateString()}{/if}</span><span class="spacer"></span>
                  {#if can('mem.write')}<button class="btn sm ghost" onclick={() => { editMem = m.id; editText = m.text; }}>Correct</button><button class="btn sm ghost" onclick={() => delMem(m.id)}>Forget</button>{/if}</div>
              {/if}</li>{/each}</ul>
            {#if can('mem.write')}<form class="row" style="margin-top:12px" onsubmit={(e) => { e.preventDefault(); addMem(); }}>
              <label class="sr-only" for="nm">Add to memory</label><input id="nm" class="input grow" bind:value={newMem} placeholder="Tell it something to remember" maxlength="2000" /><button class="btn" disabled={!newMem.trim()}>Add</button></form>{/if}
          </div>
        </section>
      {/if}
    {/if}
  </section>
</div>

<Modal bind:open={dlg} title={isNew ? 'New agent' : 'Edit agent'} wide>
  <form id="af" onsubmit={(e) => { e.preventDefault(); save(); }}>
    <div class="grid2">
      <div class="field"><label for="an">Name</label><input id="an" class="input" bind:value={f.name} maxlength="120" required /></div>
      <div class="field"><label for="am">Model</label>
        <select id="am" class="input" bind:value={f.model_id}><option value="">First available chat model</option>{#each models as m}<option value={m.id}>{m.alias}</option>{/each}</select>
        {#if !models.length}<span class="hint">No model is available to you here, so this agent could not run yet.</span>{/if}</div>
    </div>
    <div class="field"><label for="ad">Description <span class="muted">(optional)</span></label><input id="ad" class="input" bind:value={f.description} /></div>
    <div class="field"><label for="ai">Instructions</label><textarea id="ai" class="input" rows="4" bind:value={f.instructions} placeholder="How it should work and write, what to avoid, who it works for."></textarea></div>

    <fieldset class="field"><legend class="label">Tools</legend>
      {#each GROUPS as [title, names]}
        <h4 class="gh">{title}</h4>
        {#each names.map((n) => toolInfo(n)).filter(Boolean) as t}
          <label class="row tool"><input type="checkbox" checked={f.tools.includes(t.name)} onchange={() => toggleTool(t.name)} />
            <span class="grow"><strong>{t.name}</strong> <span class="muted">{t.summary}</span>
              {#if t.sensitive}<span class="chip warn"><Icon name="shield" size={11} /> Waits for approval by default</span>{/if}</span></label>
        {/each}
      {/each}
      <span class="hint">Whoever runs the agent also needs the matching permission in this workspace; an agent cannot lend a person rights they lack.</span>
    </fieldset>

    <fieldset class="field"><legend class="label">Contexts it may search</legend>
      <label class="row"><input type="radio" bind:group={f.allCtx} value={true} /><span>All contexts available in this workspace</span></label>
      <label class="row"><input type="radio" bind:group={f.allCtx} value={false} /><span>Only these</span></label>
      {#if !f.allCtx}<div class="pickbox">{#each contexts as c}<label class="row"><input type="checkbox" value={c.id} bind:group={f.context_ids} />{c.name}</label>{:else}<span class="muted small">There are no contexts yet.</span>{/each}</div>{/if}
    </fieldset>

    <div class="grid2">
      <div class="field"><label for="ap">Ask a person before</label>
        <select id="ap" class="input" bind:value={f.approval}><option value="sensitive">Running code, commands or internet access</option><option value="always">Every action</option><option value="never">Nothing (not recommended with code tools)</option></select></div>
      <div class="field"><label class="row" for="pm"><input id="pm" type="checkbox" bind:checked={f.persistent} /><span>Remember things between tasks</span></label>
        {#if f.persistent}<label for="rd" class="small" style="margin-top:6px">Forget after (days, blank = keep)</label><input id="rd" class="input" type="number" min="1" bind:value={f.retention} />{/if}</div>
      <div class="field"><label for="ms">Most steps per task</label><input id="ms" class="input" type="number" min="1" max="60" bind:value={f.max_steps} /></div>
      <div class="field"><label for="ts">Time limit (seconds)</label><input id="ts" class="input" type="number" min="30" max="21600" bind:value={f.timeout_s} /></div>
    </div>
    {#if f.tools.includes('fetch_url')}
      <div class="field"><label for="al">Websites it may open</label><textarea id="al" class="input mono" rows="3" bind:value={f.allow} placeholder="example.com&#10;intranet.example.org"></textarea>
        <span class="hint">One host per line. Empty means no internet at all. Private and internal addresses are always blocked.</span></div>
    {/if}
    {#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
  </form>
  {#snippet footer()}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" form="af" type="submit" disabled={busy || !f.name?.trim()}>{isNew ? 'Create agent' : 'Save changes'}</button>{/snippet}
</Modal>

<style>
  .ag { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 24px; align-items: start; }
  .side { display: grid; gap: 8px; } .side ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
  .ci { all: unset; cursor: pointer; display: grid; gap: 2px; padding: 8px 10px; border-radius: var(--radius-sm); box-sizing: border-box; width: 100%; }
  .ci:hover { background: var(--hover); } .ci.on { background: var(--selected); box-shadow: inset 3px 0 0 var(--accent); } .ci:focus-visible { outline: 2px solid var(--accent); } .ci .n { font-weight: 600; overflow-wrap: anywhere; }
  .head { display: flex; gap: 12px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; }
  .tl { display: flex; flex-wrap: wrap; gap: 4px; font-family: var(--font); } .prewrap { white-space: pre-wrap; margin: 0; }
  .grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; } .gh { margin: 10px 0 2px; color: var(--fg-muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  .tool { padding: 3px 0; } .pickbox { max-height: 30vh; overflow: auto; display: grid; gap: 2px; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px; margin-top: 6px; }
  .mem { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; } .mem li { padding-top: 10px; border-top: 1px solid var(--border); } .mem li:first-child { padding-top: 0; border-top: 0; }
  fieldset.field { border: 0; padding: 0; margin: 0 0 16px; }
  @media (max-width: 860px) { .ag { grid-template-columns: minmax(0, 1fr); } .grid2 { grid-template-columns: minmax(0, 1fr); } }
</style>
