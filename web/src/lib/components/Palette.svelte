<script lang="ts">
  import { tick } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { palette, allCommands, searchers, type Command } from '$lib/palette.svelte';
  import Modal from './Modal.svelte';

  let q = $state(''), sel = $state(0), remote = $state<Command[]>([]), pending = $state(false), base = $state<Command[]>([]);
  let ctl: AbortController | null = null, timer: ReturnType<typeof setTimeout>;

  function global(e: KeyboardEvent) {
    if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === 'k' && app.me) { e.preventDefault(); palette.open = !palette.open; }
  }
  $effect(() => { if (palette.open) { q = ''; sel = 0; remote = []; base = allCommands(); } else { ctl?.abort(); } });

  const local = $derived.by(() => {
    const t = q.toLowerCase().trim();
    if (!t) return base.filter((c) => c.group === 'Go to' || c.group === 'Actions').slice(0, 14);
    return base.filter((c) => `${c.group} ${c.label} ${c.keywords ?? ''}`.toLowerCase().includes(t));
  });
  const results = $derived([...local, ...remote.filter((r) => !local.some((l) => l.id === r.id))].slice(0, 24));

  function onInput() {
    sel = 0; clearTimeout(timer); ctl?.abort(); remote = [];
    const t = q.trim();
    if (t.length < 2) { pending = false; return; }
    pending = true;
    timer = setTimeout(async () => {
      ctl = new AbortController(); const mine = ctl;
      const lists = await Promise.all(searchers().map((s) => s.search!(t, mine.signal).catch(() => [] as Command[])));
      if (mine.signal.aborted) return;
      remote = lists.flat(); pending = false;
    }, 200);
  }
  async function run(c: Command) { palette.open = false; await tick(); await c.run(); }
  function key(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(results.length - 1, sel + 1); scrollSel(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); scrollSel(); }
    else if (e.key === 'Enter' && results[sel]) { e.preventDefault(); run(results[sel]); }
  }
  function scrollSel() { tick().then(() => document.getElementById(`pal-${sel}`)?.scrollIntoView({ block: 'nearest' })); }
  const status = $derived(pending ? 'Searching…' : results.length ? `${results.length} result${results.length === 1 ? '' : 's'}` : 'No matches');
</script>
<svelte:window onkeydown={global} />
<Modal bind:open={palette.open} title="Command palette">
  <input class="input" role="combobox" aria-expanded="true" aria-controls="pal-list" aria-autocomplete="list" aria-activedescendant={results[sel] ? `pal-${sel}` : undefined}
    placeholder="Go to a page, start a chat, switch model, search conversations" aria-label="Search commands" bind:value={q} oninput={onInput} onkeydown={key} />
  <p class="sr-only" role="status" aria-live="polite">{status}</p>
  <ul id="pal-list" role="listbox" aria-label="Results" class="list">
    {#each results as r, i (r.id)}
      {#if i === 0 || results[i - 1].group !== r.group}<li role="presentation" class="grp tiny muted">{r.group}</li>{/if}
      <!-- svelte-ignore a11y_click_events_have_key_events -->
      <li id={`pal-${i}`} role="option" aria-selected={i === sel} class="opt" class:on={i === sel} onclick={() => run(r)} onmousemove={() => (sel = i)}>
        <span class="grow">{r.label}</span>{#if r.hint}<span class="muted tiny">{r.hint}</span>{/if}
      </li>
    {:else}<li role="presentation" class="muted" style="padding:8px">{pending ? 'Searching…' : 'No matches.'}</li>{/each}
  </ul>
  <p class="tiny muted" style="margin:8px 0 0">Up and down to move, Enter to run, Esc to close.</p>
</Modal>
<style>
  .list { list-style: none; margin: 8px 0 0; padding: 0; max-height: 50vh; overflow: auto; }
  .grp { padding: 8px 8px 2px; font-weight: 600; }
  .opt { display: flex; gap: 8px; align-items: center; padding: 0 8px; min-height: 40px; border-radius: var(--radius-sm); cursor: pointer; }
  .opt.on { background: var(--accent-soft); box-shadow: inset 3px 0 0 var(--accent); }
</style>
