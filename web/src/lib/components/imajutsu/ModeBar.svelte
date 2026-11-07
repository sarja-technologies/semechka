<script lang="ts">
  // The ten modes. Each shows straight from the server's capabilities whether it can run: an unavailable one is disabled and says why.
  import { MODES, modeState } from '$lib/imajutsu/caps';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s }: { s: Imajutsu } = $props();
  const rows = $derived(MODES.map((m) => ({ ...m, st: modeState(s.caps, m.key) })));
  const down = $derived(rows.filter((r) => !r.st.ok));
  const cur = $derived(s.project ? s.key : '');
  function key(e: KeyboardEvent) {
    const els = [...(e.currentTarget as HTMLElement).querySelectorAll<HTMLButtonElement>('button[role=radio]:not([aria-disabled=true])')];
    const i = els.indexOf(document.activeElement as HTMLButtonElement);
    const d = e.key === 'ArrowRight' || e.key === 'ArrowDown' ? 1 : e.key === 'ArrowLeft' || e.key === 'ArrowUp' ? -1 : 0;
    if (d && i >= 0) { e.preventDefault(); els[(i + d + els.length) % els.length].focus(); }
  }
</script>

<div class="bar">
  <!-- svelte-ignore a11y_interactive_supports_focus -->
  <div class="modes" role="radiogroup" aria-label="What do you want to do?" data-testid="modes" onkeydown={key}>
    {#each rows as r (r.key)}
      <button type="button" role="radio" aria-checked={cur === r.key} aria-disabled={!r.st.ok} data-mode={r.key} class:off={!r.st.ok} title={r.st.ok ? r.blurb : `${r.label} is unavailable: ${r.st.reason}`}
        tabindex={cur === r.key || (!cur && r.key === rows.find((x) => x.st.ok)?.key) ? 0 : -1}
        onclick={() => { if (r.st.ok) s.chooseMode(r.key); }}>
        <span>{r.label}</span>{#if r.st.ok && r.st.status === 'experimental'}<i class="exp">Experimental</i>{/if}{#if !r.st.ok}<span class="sr-only">. Unavailable: {r.st.reason}</span>{/if}
      </button>
    {/each}
  </div>
  {#if down.length}
    <ul class="why" aria-label="Modes that are unavailable" data-testid="mode-reasons">
      {#each down as r (r.key)}<li><strong>{r.label}</strong> is unavailable: {r.st.reason}</li>{/each}
    </ul>
  {/if}
</div>

<style>
  .bar { display: grid; gap: 6px; min-width: 0; }
  .modes { display: flex; gap: 6px; overflow-x: auto; padding: 2px 2px 6px; scrollbar-width: thin; scroll-snap-type: x proximity; }
  button { all: unset; box-sizing: border-box; flex: none; scroll-snap-align: start; min-height: 44px; padding: 0 14px; border-radius: 999px; cursor: pointer; font-size: 13.5px; color: var(--fg-2); border: 1px solid var(--border); background: var(--card-bg);
    display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; transition: background var(--fast) var(--ease), border-color var(--fast) var(--ease); }
  button:hover:not([aria-disabled='true']) { background: var(--hover); }
  button[aria-checked='true'], button[aria-checked='true']:hover { background: var(--fg); color: var(--bg); border-color: var(--fg); font-weight: 600; }
  button:focus-visible { outline: 2px solid var(--m-ring); outline-offset: 2px; }
  button.off { opacity: .55; cursor: not-allowed; border-style: dashed; }
  .exp { font-style: normal; font-size: 10.5px; padding: 0 6px; border-radius: 8px; border: 1px solid currentColor; opacity: .8; }
  .why { list-style: none; margin: 0; padding: 0; display: grid; gap: 1px; font-size: 12px; color: var(--fg-muted); } .why strong { font-weight: 600; color: var(--fg-2); }
</style>
