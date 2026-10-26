<script lang="ts">
  // One task of an agent, live. Everything here is read from the durable record of what actually happened: model turns,
  // tool calls with their real outcomes, files produced, and anything the final answer claims that did not happen.
  import { page } from '$app/state';
  import { get, post } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can } from '$lib/ws/current.svelte';
  import { msg, ago, duration, stateChip, stateLabel, bytes } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Markdown from '$lib/components/Markdown.svelte';
  import State from '$lib/components/admin/State.svelte';

  const wid = $derived(page.params.wid ?? ''), rid = $derived(page.params.rid ?? '');
  let r = $state<any>(null), agent = $state<any>(null), error = $state(''), detail = $state(false), busy = $state(false);
  const live = $derived(r && ['queued', 'running', 'awaiting_approval'].includes(r.status));
  const waiting = $derived((r?.steps ?? []).filter((s: any) => s.status === 'awaiting_approval'));

  async function load() {
    try { r = await get(`/api/runs/${rid}?detail=${detail ? 1 : 0}`); error = ''; if (!agent) agent = await get(`/api/agents/${r.agent_id}`).catch(() => null); }
    catch (e) { error = msg(e); }
  }
  $effect(() => { rid; detail; load(); });
  $effect(() => { if (!live) return; const t = setInterval(load, 2000); return () => clearInterval(t); });

  async function decide(step: number, approve: boolean) {
    busy = true;
    try { await post(`/api/runs/${rid}/approve`, { step, approve }); toast(approve ? 'Approved. The task continues.' : 'Declined. The agent has been told.'); await load(); }
    catch (e) { toast(msg(e), 'bad'); } finally { busy = false; }
  }
  const cancel = () => post(`/api/runs/${rid}/cancel`).then(() => { toast('Cancelling'); load(); }).catch((e) => toast(msg(e), 'bad'));
  const label = (s: any) => (s.kind === 'model' ? 'Thinking' : s.kind === 'tool' ? `Tool: ${s.tool}` : s.kind);
  const toolSteps = $derived((r?.steps ?? []).filter((s: any) => s.kind === 'tool'));
  const model = $derived((r?.steps ?? []).find((s: any) => s.model)?.model);
</script>

<svelte:head><title>Task · {cur.ws?.name ?? 'Workspace'}</title></svelte:head>
<p class="tiny"><a href="/workspaces/{wid}/tasks">← All tasks</a></p>
<State {error} retry={load} loading={!r && !error} />
{#if r}
  <header class="head">
    <div class="grow">
      <h2>{agent?.name ?? 'Agent task'}</h2>
      {#if r.task}<p class="task">{r.task}</p>{:else}<p class="muted">Started by another person. Its content is private to them; you can see its progress.</p>{/if}
    </div>
    <div class="row">
      <span class="chip {stateChip(r.status)}" role="status">{stateLabel(r.status)}</span>
      {#if live}<button class="btn sm" onclick={cancel}>Cancel</button>{/if}
    </div>
  </header>

  {#if r.status === 'queued'}<div class="notice info" role="status"><div class="small">Waiting for a worker. This is a real queue, not an animation: it starts as soon as there is capacity, and it keeps going if you close this page.</div></div>{/if}
  {#if r.error}<div class="notice bad" role="alert"><div><strong>{r.status === 'cancelled' ? 'Stopped.' : 'It could not finish.'}</strong> {r.error}</div></div>{/if}
  {#each r.warnings ?? [] as w}<div class="notice warn" role="alert"><div><strong>Not verified.</strong> {w}</div></div>{/each}

  {#each waiting as s}
    <div class="notice warn" role="alert">
      <div class="grow"><strong>Waiting for your approval:</strong> the agent wants to use <code>{s.tool}</code>.
        {#if can('agent.approve')}<div class="small muted">Nothing has run yet. Approving lets that one action happen under the rules of this workspace; declining tells the agent no.</div>
        {:else}<div class="small muted">Someone with approval rights in this workspace must decide.</div>{/if}</div>
      {#if can('agent.approve')}<div class="row"><button class="btn primary sm" disabled={busy} onclick={() => decide(s.n, true)}><Icon name="check" size={14} /> Approve</button><button class="btn sm" disabled={busy} onclick={() => decide(s.n, false)}>Decline</button></div>{/if}
    </div>
  {/each}

  {#if r.final}
    <section class="panel" aria-label="Answer"><div class="panel-h"><h3>Answer</h3></div><div class="panel-b"><Markdown text={r.final} /></div></section>
  {/if}

  {#if r.artifacts?.length}
    <section class="panel" style="margin-top:16px" aria-label="Files produced"><div class="panel-h"><h3>Files it saved</h3><a class="btn sm ghost" href="/workspaces/{wid}">Open Files</a></div>
      <ul class="list">{#each r.artifacts as a}<li><Icon name="log" size={16} /> <strong>{a.name}</strong> <span class="chip">version {a.version}</span>
        {#if can('file.download')}<a class="btn sm ghost" href="/api/files/{a.file_id}/content?download=1" download>Download</a>{/if}</li>{/each}</ul></section>
  {:else if r.status === 'succeeded'}<p class="small muted" style="margin-top:12px">This task did not save any files.</p>{/if}

  {#if r.sources?.length}
    <section class="panel" style="margin-top:16px" aria-label="Sources"><div class="panel-h"><h3>What it read</h3></div>
      <ul class="list">{#each r.sources as s}<li><strong>{s.file}</strong> {#if s.loc}<span class="chip">{s.loc}</span>{/if}<span class="chip">version {s.version}</span>{#if s.mode}<span class="chip info">{s.mode}</span>{/if}</li>{/each}</ul></section>
  {/if}

  <section class="panel" style="margin-top:16px" aria-label="Steps">
    <div class="panel-h"><h3>What it did</h3>
      {#if r.task}<label class="row small"><input type="checkbox" bind:checked={detail} /> Show inputs and results</label>{/if}</div>
    <div class="panel-b">
      {#if !r.steps.length}<p class="muted">Nothing has happened yet.</p>{/if}
      <ol class="steps">{#each r.steps as s (s.n)}
        <li class:tool={s.kind === 'tool'}>
          <div class="row"><span class="chip {stateChip(s.status)}">{s.status === 'awaiting_approval' ? 'waiting' : s.status}</span><strong>{label(s)}</strong>
            {#if s.seconds != null}<span class="tiny muted">{s.seconds} s</span>{/if}{#if s.model}<span class="tiny muted">{s.model}</span>{/if}{#if s.tokens}<span class="tiny muted mono">{s.tokens.prompt_tokens ?? 0} in / {s.tokens.completion_tokens ?? 0} out</span>{/if}</div>
          {#if s.error}<div class="err small">{s.error}</div>{/if}
          {#if detail && s.kind === 'tool' && s.args}<pre class="io">{s.args}</pre>{/if}
          {#if detail && s.result}<pre class="io">{s.result.length > 4000 ? s.result.slice(0, 4000) + '\n…' : s.result}</pre>{/if}
        </li>{/each}</ol>
    </div>
  </section>

  <p class="small muted" style="margin-top:12px">
    Started {ago(r.created_at)}{#if r.started_at} · took {duration(r.started_at, r.finished_at)}{/if}
    · {r.usage.model_calls ?? 0} model call{(r.usage.model_calls ?? 0) === 1 ? '' : 's'}, {r.usage.tool_calls ?? 0} tool call{(r.usage.tool_calls ?? 0) === 1 ? '' : 's'}
    · {(r.usage.prompt_tokens ?? 0) + (r.usage.completion_tokens ?? 0)} tokens{#if model} · {model}{/if}
  </p>
{/if}

<style>
  .head { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; }
  .task { margin: 6px 0 0; padding: 8px 12px; background: var(--surface-2); border-radius: var(--radius-sm); max-width: 80ch; white-space: pre-wrap; }
  .list { list-style: none; margin: 0; padding: 12px 16px; display: grid; gap: 8px; } .list li { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .steps { list-style: none; margin: 0; padding: 0; display: grid; gap: 10px; } .steps li { padding: 8px 0 8px 12px; border-left: 3px solid var(--border); } .steps li.tool { border-left-color: var(--accent); }
  .io { margin: 6px 0 0; max-height: 220px; overflow: auto; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px 10px; font: 12px/1.5 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
