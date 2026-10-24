<script lang="ts">
  import { onMount, tick } from 'svelte';
  import type { Snippet } from 'svelte';
  let { open = $bindable(false), title, children, footer, wide = false }: { open: boolean; title: string; children: Snippet; footer?: Snippet; wide?: boolean } = $props();
  let dlg: HTMLDivElement | undefined = $state();
  let opener: Element | null = null;
  $effect(() => {
    if (open) { opener = document.activeElement; tick().then(() => (dlg?.querySelector<HTMLElement>('[autofocus], input, select, textarea, button:not([data-close])') ?? dlg)?.focus()); }
    else if (opener instanceof HTMLElement) { opener.focus(); opener = null; }
  });
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape') { e.stopPropagation(); open = false; }
    if (e.key !== 'Tab' || !dlg) return;
    const f = [...dlg.querySelectorAll<HTMLElement>('a[href], button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])')];
    if (!f.length) return;
    const first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  }
</script>
{#if open}
  <div class="scrim" role="presentation" onclick={() => (open = false)}></div>
  <div class="dlg fade" class:wide role="dialog" aria-modal="true" aria-label={title} tabindex="-1" bind:this={dlg} onkeydown={key}>
    <header><h3>{title}</h3><button class="btn ghost icon sm" data-close aria-label="Close" onclick={() => (open = false)}>×</button></header>
    <div class="body">{@render children()}</div>
    {#if footer}<footer>{@render footer()}</footer>{/if}
  </div>
{/if}
<style>
  .scrim { position: fixed; inset: 0; background: rgba(10, 12, 12, 0.5); z-index: 90; }
  .dlg { position: fixed; z-index: 91; left: 50%; top: 50%; transform: translate(-50%, -50%); width: min(480px, calc(100vw - 24px)); max-height: calc(100vh - 24px); display: flex; flex-direction: column;
    background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); }
  .dlg.wide { width: min(760px, calc(100vw - 24px)); }
  header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; border-bottom: 1px solid var(--border); }
  .body { padding: 16px; overflow: auto; }
  footer { padding: 12px 16px; border-top: 1px solid var(--border); display: flex; gap: 8px; justify-content: flex-end; flex-wrap: wrap; }
</style>
