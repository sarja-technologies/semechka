<script lang="ts">
  // The "+" of the composer: add from this device, or from the Library. A real menu (arrow keys, Escape, outside click), small and quiet.
  import { tick } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  let { onupload, onlibrary, withLibrary = true }: { onupload: () => void; onlibrary: () => void; withLibrary?: boolean } = $props();
  let open = $state(false), btn: HTMLButtonElement | undefined = $state(), pop: HTMLDivElement | undefined = $state();
  async function toggle() {
    if (!withLibrary) { onupload(); return; }
    open = !open;
    if (open) { await tick(); pop?.querySelector<HTMLElement>('[role="menuitem"]')?.focus(); }
  }
  function close(focus = true) { open = false; if (focus) btn?.focus(); }
  function key(e: KeyboardEvent) {
    const items = [...(pop?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Tab') close(false);
  }
  const pick = (f: () => void) => () => { close(false); f(); };
</script>
<svelte:window onclick={(e) => { if (open && !pop?.contains(e.target as Node) && !btn?.contains(e.target as Node)) close(false); }} />
<span class="am">
  <button type="button" class="round" bind:this={btn} aria-label="Add pictures, videos or files" title="Add pictures, videos or files" aria-haspopup={withLibrary ? 'menu' : undefined} aria-expanded={withLibrary ? open : undefined} onclick={toggle}><Icon name="plus" size={18} /></button>
  {#if open}
    <div class="pop" role="menu" aria-label="Add" bind:this={pop} onkeydown={key} tabindex="-1">
      <button type="button" role="menuitem" onclick={pick(onupload)}><Icon name="upload" size={16} /> Upload from this device</button>
      <button type="button" role="menuitem" onclick={pick(onlibrary)}><Icon name="library" size={16} /> Choose from your Library</button>
    </div>
  {/if}
</span>
<style>
  .am { position: relative; display: inline-flex; }
  .round { all: unset; box-sizing: border-box; display: inline-grid; place-items: center; width: 32px; height: 32px; border-radius: 999px; cursor: pointer; color: var(--fg-muted); flex: none;
    transition: background var(--fast) var(--ease), color var(--fast) var(--ease); }
  .round:hover, .round[aria-expanded='true'] { background: var(--hover); color: var(--fg); } .round:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .pop { position: absolute; left: 0; bottom: calc(100% + 8px); z-index: 40; min-width: 230px; padding: 4px; display: grid; background: var(--surface); border: 1px solid var(--border-strong);
    border-radius: var(--radius-menu); box-shadow: var(--shadow); animation: up var(--fast) var(--ease); }
  @keyframes up { from { opacity: 0; transform: translateY(4px); } }
  .pop button { all: unset; box-sizing: border-box; display: flex; align-items: center; gap: 10px; padding: 9px 10px; border-radius: var(--radius-sm); cursor: pointer; font-size: 14px; color: var(--fg); }
  .pop button:hover { background: var(--hover); } .pop button:focus-visible { outline: 2px solid var(--accent); }
  @media (pointer: coarse) { .round { width: 40px; height: 40px; } .pop button { padding: 12px 10px; } }
  @media (prefers-reduced-motion: reduce) { .pop { animation: none; } }
</style>
