<script lang="ts">
  // One tool call of the agent: what it ran, its live terminal output, and the file diff it made. Running calls are open; finished ones fold to one line.
  import Icon from '$lib/components/Icon.svelte';
  import DiffView from './DiffView.svelte';
  import type { Item } from '$lib/workmode/types';
  let { t }: { t: Extract<Item, { k: 'tool' }> } = $props();
  const label = { running: 'Running', completed: 'Done', failed: 'Failed', cancelled: 'Stopped' } as const;
  let manual = $state<boolean | null>(null);
  const open = $derived(manual ?? (t.status === 'running' || t.status === 'failed' || !!t.diff?.unified));
  const cmd = $derived.by(() => {
    const i = t.input ?? {};
    for (const k of ['command', 'cmd', 'path', 'file_path', 'pattern']) if (typeof i[k] === 'string' && i[k]) return i[k] as string;
    return '';
  });
  let out: HTMLPreElement | undefined = $state();
  $effect(() => { void t.output; if (out && t.status === 'running') out.scrollTop = out.scrollHeight; });
  const last = $derived(t.output.trimEnd().split('\n').at(-1) ?? '');
</script>
<section class="tool" data-status={t.status} data-tool={t.tool} aria-label={`Tool: ${t.title}`}>
  <button type="button" class="head" aria-expanded={open} onclick={() => (manual = !open)}>
    <span class="chev" class:shut={!open}><Icon name="chevdown" size={14} /></span>
    {#if !t.title.startsWith(t.tool)}<span class="name">{t.tool}</span>{/if}
    <span class="ttl" title={t.title}>{t.title}</span>
    <span class="st {t.status}">{#if t.status === 'running'}<span class="dot" aria-hidden="true"></span>{:else if t.status === 'completed'}<Icon name="check" size={12} />{:else}<Icon name={t.status === 'failed' ? 'warn' : 'stop'} size={12} />{/if}{label[t.status]}{#if t.exit_code != null && t.exit_code !== 0}&nbsp;· exit {t.exit_code}{/if}</span>
  </button>
  {#if !open && last}<p class="peek" title={last}>{last}</p>{/if}
  {#if open}
    <div class="inner">
      {#if cmd && cmd !== t.title}<pre class="cmd"><code>{cmd}</code></pre>{/if}
      {#if t.output || t.status === 'running'}
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <pre class="term" bind:this={out} role="region" tabindex="0" aria-label="Terminal output">{t.output || (t.status === 'running' ? 'No output yet.' : '')}</pre>
      {/if}
      {#if t.diff?.unified}<DiffView unified={t.diff.unified} path={t.diff.path} />{/if}
    </div>
  {/if}
</section>
<style>
  .tool { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); margin: 0 0 12px; overflow: hidden; }
  .head { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 8px; width: 100%; min-height: 36px; padding: 0 10px; cursor: pointer; }
  .head:hover { background: var(--hover); } .head:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .chev { display: grid; color: var(--fg-muted); transition: transform 120ms ease-out; } .chev.shut { transform: rotate(-90deg); }
  .name { font: 600 12px var(--mono); color: var(--fg-2); text-transform: none; }
  .ttl { flex: 1; min-width: 0; font: 13px var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg); }
  .st { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; font-weight: 500; white-space: nowrap; color: var(--fg-muted); }
  .st.completed { color: var(--success); } .st.failed { color: var(--danger); } .st.running { color: var(--info); }
  .dot { width: 6px; height: 6px; border-radius: 50%; background: currentColor; }
  @media (prefers-reduced-motion: no-preference) { .dot { animation: pulse 1.1s ease-in-out infinite; } @keyframes pulse { 50% { opacity: 0.25; } } }
  .peek { margin: 0; padding: 0 10px 8px 32px; font: 12px var(--mono); color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .inner { display: grid; gap: 8px; padding: 0 10px 10px; }
  pre { margin: 0; font: 12px/1.55 var(--mono); }
  .cmd { background: var(--bg-2); border-radius: var(--radius-sm); padding: 6px 10px; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; }
  .term { background: var(--m-stage); color: #e6e6e6; border-radius: var(--radius-sm); padding: 8px 10px; overflow: auto; max-height: 320px; white-space: pre-wrap; overflow-wrap: anywhere; }
  .term:focus-visible { outline: 2px solid var(--accent); }
</style>
