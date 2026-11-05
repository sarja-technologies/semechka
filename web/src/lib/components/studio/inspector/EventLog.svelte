<script lang="ts">
  // The engine's own trace, newest at the bottom. Capped (renders the last N, "show earlier" grows it), filterable, copyable.
  import { tick } from 'svelte';
  import { describeEvent, filterEvents, eventsToText, relTime, type LogFilter } from '../../../studio/inspector/logic.ts';
  import type { InspectEvent } from '../../../studio/inspector/types.ts';
  import { copyText } from '../../../studio/inspector/clip';
  import Icon from '../../Icon.svelte';
  let { events, startedAt = null }: { events: InspectEvent[]; startedAt?: number | null } = $props();
  let filter = $state<LogFilter>('milestones');
  let follow = $state(true);
  let cap = $state(150);
  let copied = $state(false);
  let box: HTMLOListElement | undefined = $state();
  const filtered = $derived(filterEvents(events, filter));
  const shown = $derived(filtered.slice(Math.max(0, filtered.length - cap)));
  const hidden = $derived(filtered.length - shown.length);
  let programmatic = false;
  $effect(() => {
    void shown.length; void filter;
    if (follow && box) { const b = box; tick().then(() => { programmatic = true; b.scrollTop = b.scrollHeight; }); }
  });
  function onscroll() {
    if (!box) return;
    if (programmatic) { programmatic = false; return; }
    const atEnd = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
    if (!atEnd && follow) follow = false;
  }
  async function copy() { copied = await copyText(eventsToText(filtered, startedAt)); setTimeout(() => (copied = false), 2000); }
  const FILTERS: [LogFilter, string][] = [['milestones', 'Milestones'], ['all', 'Everything'], ['errors', 'Errors only']];
</script>
<div class="tools">
  <label class="row small" for="logfilter">Show
    <select id="logfilter" class="input sel" bind:value={filter}>{#each FILTERS as [f, l] (f)}<option value={f}>{l}</option>{/each}</select>
  </label>
  <label class="row small follow"><input type="checkbox" bind:checked={follow} /> Follow latest</label>
  <span class="spacer"></span>
  <button class="btn sm" onclick={copy}><Icon name="copy" size={14} /> {copied ? 'Copied' : 'Copy all'}</button>
</div>
<p class="tiny muted count" aria-live="polite">{filtered.length} of {events.length} events{events.length >= 1000 ? ' (newest 1000 kept)' : ''}</p>
{#if hidden > 0}<button class="btn sm earlier" onclick={() => (cap += 150)}>Show {Math.min(150, hidden)} earlier</button>{/if}
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<ol class="log" bind:this={box} {onscroll} tabindex="0" aria-label="Job events">
  {#each shown as e (e.seq)}
    {@const l = describeEvent(e)}
    <li class="ev {l.tone}" data-type={e.type}>
      <span class="t mono tiny">{relTime(e.t, startedAt) || `#${e.seq}`}</span>
      <span class="lab tiny">{#if l.tone === 'loop'}<Icon name="branch" size={12} />{/if}{l.label}</span>
      <span class="msg">{l.text}</span>
    </li>
  {:else}
    <li class="empty muted small">{filter === 'errors' ? 'No errors reported.' : 'No events yet.'}</li>
  {/each}
</ol>
<style>
  .tools { display: flex; flex-wrap: wrap; gap: 10px; align-items: center; }
  .sel { width: auto; min-height: 30px; padding: 2px 8px; }
  .follow { gap: 6px; } .count { margin: 6px 0; } .earlier { margin-bottom: 6px; }
  .log { list-style: none; margin: 0; padding: 0; max-height: 340px; overflow: auto; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .ev { display: grid; grid-template-columns: 66px 74px 1fr; gap: 8px; padding: 5px 10px; font-size: 13px; border-top: 1px solid var(--border); align-items: baseline; }
  .ev:first-child { border-top: 0; }
  .t { color: var(--fg-muted); white-space: nowrap; } .lab { color: var(--fg-muted); display: inline-flex; align-items: center; gap: 3px; font-weight: 600; } .msg { overflow-wrap: anywhere; }
  .ev.ok .lab { color: var(--success); } .ev.bad { background: var(--danger-soft); } .ev.bad .lab { color: var(--danger); }
  .ev.info .lab { color: var(--info); }
  .ev.loop { box-shadow: inset 3px 0 0 var(--accent); background: color-mix(in srgb, var(--accent) 8%, var(--card-bg)); } .ev.loop .lab { color: var(--accent); }
  .empty { padding: 10px; display: block; }
  @media (max-width: 520px) { .ev { grid-template-columns: 58px 1fr; } .ev .lab { display: none; } .ev.loop .lab, .ev.bad .lab { display: inline-flex; } }
</style>
