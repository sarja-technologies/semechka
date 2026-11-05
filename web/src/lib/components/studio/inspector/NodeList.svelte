<script lang="ts">
  // The nodes of the ComfyUI workflow with what each is doing now: running first, then failed, then recently finished.
  import { nodeRows, nodeCounts, nodeBar, type NodeFilter } from '../../../studio/inspector/logic.ts';
  import type { InspectEvent, InspectSummary } from '../../../studio/inspector/types.ts';
  import { formatDuration } from '../../../work/format';
  import { focusedNode } from '../../../studio/inspector/focus.svelte';
  import StateGlyph from './StateGlyph.svelte';
  let { doc, events, onnode }: { doc: InspectSummary; events: InspectEvent[]; onnode?: (id: string) => void } = $props();
  let filter = $state<NodeFilter>('all');
  let query = $state('');
  let all = $state(false);
  const LIMIT = 40;
  const counts = $derived(nodeCounts(doc));
  const bar = $derived(counts ? nodeBar(counts) : []);
  const rows = $derived(nodeRows(doc, events, filter, query));
  const shown = $derived(all ? rows : rows.slice(0, LIMIT));
  const LABEL: Record<string, string> = { done: 'Done', running: 'Running', pending: 'Waiting', cached: 'Reused', failed: 'Failed' };
  const FILTERS: [NodeFilter, string][] = [['all', 'All'], ['running', 'Running'], ['failed', 'Failed'], ['done', 'Done'], ['cached', 'Reused'], ['pending', 'Waiting']];
  const pick = (id: string) => { focusedNode.id = id; onnode?.(id); };
</script>
{#if counts}
  <div class="bar" role="img" aria-label="{counts.done} done, {counts.running} running, {counts.cached} reused from cache, {counts.failed} failed, {counts.pending} waiting, of {counts.total} nodes">
    {#each bar as s (s.key)}<i class={s.key} style:width="{s.pct}%"></i>{/each}
  </div>
  <ul class="legend">
    {#each [['done', counts.done], ['running', counts.running], ['cached', counts.cached], ['failed', counts.failed], ['pending', counts.pending]] as [k, n] (k)}
      <li><StateGlyph state={String(k)} size={13} /><span>{LABEL[String(k)]}</span> <b>{n}</b></li>
    {/each}
    <li class="muted">of {counts.total}</li>
  </ul>
{/if}
{#if doc.node_states}
  <div class="tools">
    <div class="chips" role="group" aria-label="Show nodes">
      {#each FILTERS as [f, l] (f)}<button class="chipb" aria-pressed={filter === f} onclick={() => { filter = f; all = false; }}>{l}</button>{/each}
    </div>
    <input class="input find" type="search" placeholder="Find a node" aria-label="Find a node by name or class" bind:value={query} />
  </div>
  {#if rows.length === 0}
    <p class="muted small">No node matches.</p>
  {:else}
    <ul class="list" aria-label="Nodes">
      {#each shown as r (r.id)}
        <li>
          <button class="row-btn" class:active={r.state === 'running'} aria-current={focusedNode.id === r.id ? 'true' : undefined} onclick={() => pick(r.id)}>
            <StateGlyph state={r.state} /><span class="sr-only">{LABEL[r.state] ?? r.state}: </span>
            <span class="t"><span class="name">{r.title}</span><span class="cls tiny">{r.class_type} · #{r.id}</span></span>
            <span class="num tiny">{[r.runs != null ? `${r.runs}×` : '', r.last_ms != null ? formatDuration(r.last_ms) : ''].filter(Boolean).join(' · ')}</span>
          </button>
        </li>
      {/each}
    </ul>
    {#if rows.length > shown.length}<button class="btn sm" onclick={() => (all = true)}>Show all {rows.length} nodes</button>{/if}
  {/if}
{:else}
  <p class="muted small">Node details are not reported for this job (it does not run on ComfyUI).</p>
{/if}
<style>
  .bar { display: flex; height: 12px; border-radius: 6px; overflow: hidden; border: 1px solid var(--border-strong); background: var(--surface-2); }
  .bar i { display: block; height: 100%; min-width: 3px; }
  .bar .done { background: var(--success); } .bar .cached { background: repeating-linear-gradient(90deg, var(--info) 0 3px, transparent 3px 5px); }
  .bar .running { background: var(--accent); } .bar .failed { background: var(--danger); } .bar .pending { background: transparent; }
  .legend { display: flex; flex-wrap: wrap; gap: 4px 14px; list-style: none; padding: 0; margin: 8px 0 12px; font-size: 13px; }
  .legend li { display: inline-flex; align-items: center; gap: 5px; }
  .tools { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; margin-bottom: 8px; }
  .chips { display: flex; flex-wrap: wrap; gap: 4px; }
  .chipb { font: 500 12px/1 var(--font); min-height: 28px; padding: 0 10px; border-radius: var(--radius-chip); border: 1px solid var(--border-strong); background: var(--surface); color: var(--fg-2); cursor: pointer; }
  .chipb[aria-pressed='true'] { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-color: var(--btn-primary-bg); }
  .find { flex: 1 1 140px; min-width: 120px; max-width: 220px; }
  .list { list-style: none; margin: 0 0 8px; padding: 0; border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; }
  .list li + li { border-top: 1px solid var(--border); }
  .row-btn { all: unset; box-sizing: border-box; width: 100%; display: flex; align-items: center; gap: 10px; padding: 7px 10px; cursor: pointer; min-height: 40px; }
  .row-btn:hover { background: var(--hover); } .row-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .row-btn.active { background: color-mix(in srgb, var(--accent) 12%, var(--card-bg)); }
  .row-btn[aria-current='true'] { box-shadow: inset 3px 0 0 var(--accent); }
  .t { display: grid; min-width: 0; flex: 1; } .name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
  .cls { color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-family: var(--mono); }
  .num { font-family: var(--mono); color: var(--fg-muted); white-space: nowrap; }
</style>
