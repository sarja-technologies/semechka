<script lang="ts">
  // A tool call and its result. Collapsed by default: tool, the command in one line and the outcome. Expanded: the whole
  // command and the output the server returned (clipped here only for display; the notice says how much).
  import { toolFailed, toolLabel, type Item } from '$lib/work/events';
  import { formatDuration, clip } from '$lib/work/format';
  import Icon from '$lib/components/Icon.svelte';

  let { item }: { item: Extract<Item, { type: 'tool' }> } = $props();
  const r = $derived(item.result);
  const failed = $derived(toolFailed(r));
  const label = $derived(toolLabel(item));
  const duration = $derived(formatDuration(r?.duration_ms));
  const fullCommand = $derived(item.call.command ?? (item.call.args ? JSON.stringify(item.call.args, null, 2) : ''));
  const out = $derived(clip(r?.output ?? '', 6000, 200));
  let open = $state(false), showAll = $state(false);
  const outText = $derived(showAll ? (r?.output ?? '') : out.shown);
  const id = $derived(`tool-${item.key.replace(/\W/g, '')}`);
</script>

<div class="tool" class:bad={failed}>
  <button class="head" aria-expanded={open} aria-controls={id} onclick={() => (open = !open)}>
    <span class="chev" class:open><Icon name="chevron" size={14} /></span>
    <span class="name">{item.call.tool}</span>
    <code class="cmd" title={label}>{label.split('\n')[0]}</code>
    <span class="res">
      {#if !r}<span class="chip info">Running</span>
      {:else if failed}<span class="chip bad">{typeof r.exit_code === 'number' ? `Failed, exit ${r.exit_code}` : 'Failed'}</span>
      {:else}<span class="chip ok">{typeof r.exit_code === 'number' ? `Done, exit ${r.exit_code}` : 'Done'}</span>{/if}
      {#if duration}<span class="tiny muted">{duration}</span>{/if}
    </span>
  </button>
  {#if open}
    <div class="body" id={id}>
      {#if fullCommand}<p class="lbl">{item.call.command ? 'Command' : 'Arguments'}</p><pre>{fullCommand}</pre>{/if}
      {#if !r}<p class="muted small">Waiting for the result.</p>
      {:else}
        {#if r.error}<p class="lbl">Error</p><pre class="errtext">{r.error}</pre>{/if}
        {#if r.output}
          <p class="lbl">Output</p><pre>{outText}</pre>
          {#if out.hidden > 0 && !showAll}<p class="tiny muted">{out.hidden.toLocaleString()} more characters not shown. <button class="linkish" onclick={() => (showAll = true)}>Show everything received</button></p>{/if}
          {#if r.truncated}<p class="tiny muted">The tool cut this output short before it reached the interface.</p>{/if}
        {:else if !r.error}<p class="muted small">No output.</p>{/if}
      {/if}
    </div>
  {/if}
</div>

<style>
  .tool { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .tool.bad { border-color: color-mix(in srgb, var(--danger) 40%, var(--border)); }
  .head { all: unset; box-sizing: border-box; width: 100%; display: flex; align-items: center; gap: 8px; padding: 8px 10px; cursor: pointer; min-height: 36px; border-radius: var(--radius); }
  .head:hover { background: var(--hover); }
  .head:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .chev { display: inline-flex; flex: none; transition: transform var(--fast) var(--ease); color: var(--fg-muted); } .chev.open { transform: rotate(90deg); }
  .name { font-weight: 600; font-size: 13px; flex: none; }
  .cmd { flex: 1 1 auto; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; color: var(--fg-2); font-size: 12.5px; }
  .res { display: inline-flex; align-items: center; gap: 6px; flex: none; }
  .body { padding: 0 12px 12px; display: grid; gap: 4px; }
  .lbl { margin: 8px 0 0; font-size: 12px; font-weight: 600; color: var(--fg-muted); }
  pre { margin: 0; padding: 8px 10px; background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); font: 12.5px/1.5 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 320px; overflow: auto; }
  .errtext { color: var(--danger); }
  @media (max-width: 560px) { .head { flex-wrap: wrap; } .cmd { flex-basis: 100%; order: 3; padding-left: 22px; } }
</style>
