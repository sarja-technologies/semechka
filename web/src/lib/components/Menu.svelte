<script lang="ts">
  // An actions menu: a real button that opens a list of menu items. Escape and an outside click close it and give focus
  // back; Arrow keys move between items. Items call `close()` after they act.
  import { tick } from 'svelte';
  import type { Snippet } from 'svelte';
  import Icon from './Icon.svelte';
  let { label, children }: { label: string; children: Snippet<[() => void]> } = $props();
  let open = $state(false), btn: HTMLButtonElement | undefined = $state(), pop: HTMLDivElement | undefined = $state();
  async function toggle() { open = !open; if (open) { await tick(); pop?.querySelector<HTMLElement>('[role="menuitem"]')?.focus(); } }
  function close(focus = true) { open = false; if (focus) btn?.focus(); }
  function key(e: KeyboardEvent) {
    const items = [...(pop?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); items[(i + 1) % items.length]?.focus(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); items[(i - 1 + items.length) % items.length]?.focus(); }
    else if (e.key === 'Tab') close(false);
  }
  function outside(e: MouseEvent) { if (open && !pop?.contains(e.target as Node) && !btn?.contains(e.target as Node)) close(false); }
</script>
<svelte:window onclick={outside} />
<span class="menu">
  <button type="button" class="btn ghost icon sm" bind:this={btn} aria-label={label} aria-haspopup="menu" aria-expanded={open} onclick={toggle}><Icon name="more" size={16} /></button>
  {#if open}
    <!-- svelte-ignore a11y_interactive_supports_focus -->
    <div class="pop" role="menu" aria-label={label} bind:this={pop} onkeydown={key} tabindex="-1">{@render children(() => close(true))}</div>
  {/if}
</span>
<style>
  .menu { position: relative; display: inline-block; text-align: left; }
  .pop { position: absolute; right: 0; top: 100%; z-index: 30; min-width: 190px; padding: 4px; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius-menu); box-shadow: var(--shadow); display: grid; }
  .pop :global(button), .pop :global(a) { all: unset; cursor: pointer; padding: 8px 10px; border-radius: var(--radius-sm); font-size: 14px; color: var(--fg); box-sizing: border-box; }
  .pop :global(button:hover), .pop :global(a:hover) { background: var(--hover); } .pop :global(button:focus-visible), .pop :global(a:focus-visible) { outline: 2px solid var(--accent); }
  .pop :global(.dng) { color: var(--danger); }
</style>
