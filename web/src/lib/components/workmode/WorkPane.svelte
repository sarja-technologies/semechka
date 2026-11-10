<script lang="ts">
  // Work Mode inside the conversation: the agent's timeline (its words, tool calls, terminal output, file diffs, approval questions) and a box to
  // tell it what to do next. The agent loop is upstream Goose on the server; this screen only shows what the server reports.
  import { onMount, tick, untrack } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Markdown from '$lib/components/Markdown.svelte';
  import StreamText from '$lib/components/StreamText.svelte';
  import ToolCard from './ToolCard.svelte';
  import ApprovalCard from './ApprovalCard.svelte';
  import ChangesCard from './ChangesCard.svelte';
  import FilesPanel from './FilesPanel.svelte';
  import { WorkRun } from '$lib/workmode/store.svelte';

  let { cid, ensure, workable = true, why = '' }: { cid: string | null; ensure: () => Promise<string>; workable?: boolean; why?: string } = $props();
  const run = new WorkRun();
  let text = $state(''), box: HTMLTextAreaElement | undefined = $state(), scroller: HTMLDivElement | undefined = $state(), stick = true;
  // a conversation that has just been saved by the first Work prompt keeps its live session: do not reopen it
  let adopted: string | null = null;
  $effect(() => { const id = cid; untrack(() => { if (id && id === adopted) return; adopted = null; run.open(id); }); });
  $effect(() => () => run.close());

  const f = $derived(run.fold), s = $derived(f.state), items = $derived(f.items);
  const pending = $derived(items.some((i) => i.k === 'approval' && !i.decision));
  const lastAssistantKey = $derived([...items].reverse().find((i) => i.k === 'assistant')?.key);
  const sessionOn = $derived(!!run.session);
  let filesOpen = $state(false);
  const filesStamp = $derived(items.filter((i) => i.k === 'files').length);

  // how long the current wait has lasted, measured here (never invented): used for the model-load and start-up lines
  let since = $state(Date.now()), tickNow = $state(Date.now());
  let lastState = '';
  $effect(() => { const st = s; if (st !== lastState) { const was = lastState; lastState = st; since = Date.now(); if (st === 'idle' && was && was !== 'idle') run.refreshStatus(); } });
  onMount(() => { const t = setInterval(() => (tickNow = Date.now()), 1000); return () => clearInterval(t); });
  const waited = $derived(Math.max(0, Math.round((tickNow - since) / 1000)));
  const modelLabel = $derived(run.status?.models.find((m) => m.id === run.model)?.label ?? run.model);
  const model = $derived(run.status?.models.find((m) => m.id === run.model));
  const statusLine = $derived(
    s === 'starting' ? `Starting the sandbox (${waited} s). The first start takes longer than the next ones.`
    : s === 'loading_model' ? `Loading ${modelLabel} into memory (${waited} s). A model that is not loaded takes 25 to 90 seconds, sometimes longer.`
    : s === 'awaiting_approval' ? 'Waiting for your decision above.'
    : s === 'cancelling' ? 'Stopping the agent…'
    : s === 'running' ? (f.items.at(-1)?.k === 'tool' && (f.items.at(-1) as any).status === 'running' ? 'Running a command…' : 'The agent is working.')
    : run.sending ? 'Sending…' : '');

  function pin() { if (stick && scroller) scroller.scrollTop = scroller.scrollHeight; }
  $effect(() => { void f; tick().then(pin); });
  function onScroll() { if (scroller) stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80; }

  async function send() {
    const t = text.trim(); if (!t || run.busy || !workable) return;
    stick = true;
    const ok = await run.send(t, async () => { const id = await ensure(); adopted = id; return id; });
    if (ok) { text = ''; autosize(); }
  }
  function key(e: KeyboardEvent) {
    if (e.isComposing) return;
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    else if (e.key === 'Escape' && run.busy) { e.preventDefault(); run.cancel(); }
  }
  function autosize() { tick().then(() => { if (!box) return; box.style.height = 'auto'; box.style.height = Math.min(box.scrollHeight, 200) + 'px'; }); }
  export function focus() { box?.focus(); }
  onMount(() => box?.focus());
</script>

<div class="work" data-work-state={s}>
  {#if run.phase === 'loading'}
    <p class="muted pad" role="status">Checking Work Mode…</p>
  {:else if run.phase === 'unavailable'}
    <div class="pad"><div class="notice {run.why?.kind === 'none' || run.why?.kind === 'disabled' || run.why?.kind === 'denied' ? 'info' : 'warn'}" role="status" data-testid="work-unavailable">
      <div><strong>{run.why?.message}</strong>
        {#if run.why?.kind === 'none'} Ask your administrator whether it is planned. You can keep chatting in Chat mode.
        {:else if run.why?.kind === 'not_ready'} The server is not ready to run it yet. Try again in a few minutes.
        {:else if run.why?.kind === 'offline'} Check your connection and open this conversation again.{/if}</div></div></div>
  {:else}
    {#if run.status && !run.status.ready}
      <div class="pad"><div class="notice warn" role="status"><div><strong>Work Mode is set up but not ready.</strong> {run.status.problems.join('. ') || 'The server did not say why.'}</div></div></div>
    {/if}
    <div class="scroll" bind:this={scroller} onscroll={onScroll} tabindex="-1" aria-label="Agent timeline" data-work-scroll>
      <div class="col">
        {#if !items.length && !pending}
          <div class="intro">
            <h1>Give the agent a task</h1>
            <p class="muted">Work Mode runs an agent in a private sandbox with its own files and a terminal. It has no internet. You see every command it runs, and
              {run.autonomy === 'auto' ? 'it never asks first.' : run.autonomy === 'smart' ? 'it asks before anything sensitive.' : 'it asks before each action.'}</p>
            <p class="small muted">Ask it to read, write or test code, or to work on files you add to this conversation.</p>
          </div>
        {/if}
        {#each items as it (it.key)}
          {#if it.k === 'user'}
            <article class="msg user" aria-label="You"><div class="bubble">{it.text}</div></article>
          {:else if it.k === 'assistant'}
            <article class="msg" aria-label="Agent">
              {#if it.text}<div class="who"><Icon name="work" size={16} /><span>Agent</span></div>{/if}
              {#if it.reasoning}<details class="think"><summary class="small muted">Thinking</summary><pre>{it.reasoning}</pre></details>{/if}
              {#if it.text}{#if it.key === lastAssistantKey && run.busy}<StreamText text={it.text} />{:else}<Markdown text={it.text} />{/if}{/if}
            </article>
          {:else if it.k === 'tool'}<ToolCard t={it} />
          {:else if it.k === 'approval'}<ApprovalCard a={it} ondecide={(id, d) => run.decide(id, d)} />
          {:else if it.k === 'files'}<ChangesCard c={it} cid={run.cid} />
          {:else if it.k === 'model'}
            <p class="line" data-model-phase={it.phase}>{it.phase === 'loading' ? `Loading ${it.model}…` : it.phase === 'ready' ? `${it.model} is loaded.` : `${it.model} could not be loaded.`}{#if it.message} {it.message}{/if}</p>
          {:else if it.k === 'notice'}<p class="line"><Icon name="info" size={13} /> {it.message}</p>
          {:else if it.k === 'error'}<div class="notice bad" role="alert"><div>{it.message}{#if it.retryable} Send your message again to retry.{/if}</div></div>
          {:else if it.k === 'end'}<p class="line">{it.reason === 'cancelled' ? 'Stopped.' : it.reason === 'interrupted' ? 'Interrupted by a server restart. Send a message to continue.' : it.reason === 'max_turns' ? 'Stopped: it reached its limit of steps for one task.' : it.reason === 'refusal' ? 'The model declined this request.' : `Stopped (${it.reason}).`}</p>
          {/if}
        {/each}
        {#if run.error}<div class="notice bad" role="alert" data-testid="work-error"><div>{run.error}</div></div>{/if}
      </div>
    </div>

    <div class="dock">
      <div class="status" role="status" aria-live="polite" data-testid="work-status">
        {#if statusLine}<span class="dot" aria-hidden="true"></span>{statusLine}{/if}
        {#if run.conn === 'retrying'}<span class="warn"> Reconnecting to the session…</span>{/if}
      </div>
      {#if filesOpen && run.cid && sessionOn}<FilesPanel cid={run.cid} refresh={filesStamp} />{/if}
      <div class="box">
        <label class="sr-only" for="work-msg">Message to the agent</label>
        <textarea id="work-msg" rows="1" bind:this={box} bind:value={text} onkeydown={key} oninput={autosize} disabled={!workable}
          placeholder={workable ? (items.length ? 'Tell the agent what to do next' : 'Describe the task') : why}></textarea>
        <div class="bar">
          <label class="sel"><span class="sr-only">Model</span>
            <select class="input" aria-label="Agent model" value={run.model} disabled={run.busy && sessionOn} onchange={(e) => run.change({ model: e.currentTarget.value })}>
              {#each run.status?.models ?? [] as m (m.id)}<option value={m.id}>{m.label}{m.state === 'loaded' ? '' : m.state === 'loading' ? ' (loading)' : ' (not loaded)'}</option>{/each}
              {#if !run.status?.models.length}<option value="">No coding model is available</option>{/if}
            </select></label>
          <label class="sel"><span class="sr-only">When to ask</span>
            <select class="input" aria-label="When the agent asks first" value={run.autonomy} onchange={(e) => run.change({ autonomy: e.currentTarget.value })}>
              {#each run.status?.autonomy ?? [] as a (a.id)}<option value={a.id}>{a.label}</option>{/each}
            </select></label>
          <span class="spacer"></span>
          {#if run.busy && s !== 'idle'}
            <button type="button" class="round stop" onclick={() => run.cancel()} aria-label="Stop the agent (Esc)" title="Stop the agent (Esc)"><Icon name="stop" size={14} /></button>
          {:else}
            <button type="button" class="round send" onclick={send} disabled={!text.trim() || !workable || run.sending || !run.model} aria-label="Send (Enter)" title={!workable ? why : !run.model ? 'No coding model is available to you.' : 'Send (Enter)'}><Icon name="arrowup" size={18} /></button>
          {/if}
        </div>
      </div>
      <p class="tiny muted foot">
        {#if model && model.state !== 'loaded' && !sessionOn}{model.label} is not loaded yet; the first message waits for it.{/if}
        {#if f.usage}<span class="mono">Context {f.usage.used.toLocaleString()} of {f.usage.size.toLocaleString()}</span>{/if}
        {#if sessionOn}<button type="button" class="linkish" aria-expanded={filesOpen} onclick={() => (filesOpen = !filesOpen)}>{filesOpen ? 'Hide files' : 'Files'}</button>{/if}
        {#if sessionOn}<button type="button" class="linkish" onclick={() => run.stopProcess()} disabled={run.busy} title="Stops the sandbox. Files and history stay; the next message starts it again.">Stop sandbox</button>{/if}
      </p>
    </div>
  {/if}
</div>

<style>
  .work { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  .pad { padding: 16px 24px; max-width: var(--msg-w); margin: 0 auto; width: 100%; box-sizing: border-box; }
  .scroll { flex: 1; min-height: 0; overflow-y: auto; overscroll-behavior: contain; outline: none; }
  .col { max-width: var(--msg-w); margin: 0 auto; padding: 16px 24px 24px; width: 100%; min-height: 100%; display: flex; flex-direction: column; box-sizing: border-box; }
  .intro { margin: auto 0 0; padding: 48px 0 24px; text-align: center; display: grid; gap: 10px; justify-items: center; } .intro h1 { font-size: 28px; font-weight: 400; letter-spacing: -0.015em; } .intro p { margin: 0; max-width: 58ch; }
  .msg { margin: 0 0 18px; display: flex; flex-direction: column; } .msg.user { align-items: flex-end; }
  .bubble { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--user-msg-bg); border-radius: var(--radius); padding: 8px 14px; max-width: min(85%, 72ch); }
  .who { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; font-size: 13px; font-weight: 600; }
  .think pre { white-space: pre-wrap; font: 12px/1.5 var(--mono); color: var(--fg-muted); margin: 4px 0 8px; border-left: 2px solid var(--border-strong); padding-left: 10px; max-height: 240px; overflow: auto; }
  .line { margin: 0 0 12px; font-size: 13px; color: var(--fg-muted); display: flex; gap: 6px; align-items: center; }
  .dock { flex: none; padding: 0 24px 14px; background: var(--bg); } .dock > :global(*) { max-width: var(--msg-w); margin-inline: auto; width: 100%; box-sizing: border-box; }
  .status { min-height: 22px; font-size: 13px; color: var(--fg-muted); display: flex; align-items: center; gap: 8px; padding: 0 4px 4px; } .status .warn { color: var(--warn); }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); flex: none; }
  @media (prefers-reduced-motion: no-preference) { .dot { animation: pulse 1.1s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.25; } } }
  .box { background: var(--composer-bg); border: 1px solid var(--border-strong); border-radius: var(--radius-composer); box-shadow: var(--shadow-sm); padding: 10px 12px 8px; display: grid; gap: 8px; }
  .box:focus-within { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent); }
  textarea { all: unset; box-sizing: border-box; width: 100%; min-height: 24px; max-height: 200px; resize: none; font: inherit; line-height: 1.5; color: var(--fg); overflow-y: auto; }
  textarea::placeholder { color: var(--fg-muted); }
  .bar { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .spacer { flex: 1; }
  .sel { display: inline-flex; } .sel select { min-height: 32px; width: auto; max-width: 220px; border-radius: 999px; padding: 2px 10px; font-size: 13px; }
  .round { all: unset; box-sizing: border-box; width: 34px; height: 34px; border-radius: 50%; display: grid; place-items: center; cursor: pointer; background: var(--btn-primary-bg); color: var(--btn-primary-fg); }
  .round:disabled { opacity: 0.4; cursor: not-allowed; } .round:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .foot { display: flex; gap: 16px; flex-wrap: wrap; margin: 6px 0 0; align-items: center; } .foot .linkish { color: var(--fg-muted); } .foot .linkish:first-of-type { margin-left: auto; }
  @media (max-width: 719px) { .col { padding: 12px 12px 16px; } .dock { padding: 0 8px 8px; } .pad { padding: 12px; } .sel { flex: 1 1 150px; } .sel select { max-width: none; width: 100%; } .spacer { display: none; } .round { margin-left: auto; } }
</style>
