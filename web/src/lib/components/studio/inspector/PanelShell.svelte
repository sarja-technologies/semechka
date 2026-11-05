<script lang="ts">
  // The frame both inspector panels share: a drawer on the right (full-screen sheet on a phone) or an inline block the page
  // places itself. Non-modal: the Studio page behind it stays usable on a desktop; Escape and the close button leave.
  import { onMount } from 'svelte';
  import type { Snippet } from 'svelte';
  import Icon from '../../Icon.svelte';
  let { title, subtitle = '', mode = 'drawer', width = 560, onclose, actions, children }: {
    title: string; subtitle?: string; mode?: 'drawer' | 'inline'; width?: number; onclose?: () => void; actions?: Snippet; children: Snippet;
  } = $props();
  let root: HTMLElement | undefined = $state();
  let opener: Element | null = null;
  onMount(() => {
    if (mode !== 'drawer') return;
    opener = document.activeElement;
    root?.querySelector<HTMLElement>('[data-close]')?.focus({ preventScroll: true });
    return () => { if (opener instanceof HTMLElement && document.contains(opener)) opener.focus({ preventScroll: true }); };
  });
  function key(e: KeyboardEvent) {
    if (e.key === 'Escape' && onclose && !e.defaultPrevented && !(e.target instanceof HTMLInputElement && e.target.value)) { e.stopPropagation(); onclose(); }
  }
</script>
<section bind:this={root} class="shell" class:drawer={mode === 'drawer'} class:inline={mode === 'inline'} style:--w="{width}px" role={mode === 'drawer' ? 'dialog' : 'region'} aria-label={title} onkeydown={key} tabindex="-1">
  <header>
    <div class="grow"><h2>{title}</h2>{#if subtitle}<p class="tiny muted sub">{subtitle}</p>{/if}</div>
    {#if actions}<div class="acts">{@render actions()}</div>{/if}
    {#if onclose}<button class="btn ghost icon" data-close aria-label="Close {title}" onclick={() => onclose?.()}><Icon name="x" /></button>{/if}
  </header>
  <div class="body">{@render children()}</div>
</section>
<style>
  .shell { display: flex; flex-direction: column; min-height: 0; background: var(--bg); color: var(--fg); outline: none; }
  .drawer { position: fixed; top: 0; right: 0; bottom: 0; z-index: 70; width: min(var(--w), 100vw); border-left: 1px solid var(--border-strong); box-shadow: var(--shadow); }
  .inline { border: 1px solid var(--border); border-radius: var(--radius-menu); overflow: hidden; height: 100%; }
  header { display: flex; align-items: center; gap: var(--s2); padding: 10px 12px 10px 16px; border-bottom: 1px solid var(--border); background: var(--header-bg); flex: none; }
  h2 { font-size: 16px; } .sub { margin: 0; }
  .acts { display: flex; gap: var(--s2); align-items: center; }
  .body { flex: 1; min-height: 0; display: flex; flex-direction: column; }
  @media (max-width: 700px) { .drawer { width: 100vw; height: 100dvh; border-left: 0; } header { padding-left: 12px; } }
</style>
