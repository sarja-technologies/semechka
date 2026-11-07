<script lang="ts">
  // One panel of the workspace: a numbered card with a plain heading and one line saying what it is for.
  import type { Snippet } from 'svelte';
  let { id, title, hint = '', n = 0, children, head }: { id: string; title: string; hint?: string; n?: number; children: Snippet; head?: Snippet } = $props();
</script>

<section class="pc" id={`pn-${id}`} aria-labelledby={`pn-${id}-h`} data-testid={`panel-${id}`} data-panel={id}>
  <header>
    {#if n}<span class="no" aria-hidden="true">{n}</span>{/if}
    <div class="tx"><h2 id={`pn-${id}-h`}>{title}</h2>{#if hint}<p>{hint}</p>{/if}</div>
    {#if head}<div class="hd">{@render head()}</div>{/if}
  </header>
  <div class="bd">{@render children()}</div>
</section>

<style>
  .pc { min-width: 0; padding: var(--s4); border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--card-bg); display: grid; gap: var(--s3); scroll-margin-top: 12px; }
  header { display: flex; align-items: flex-start; gap: 10px; }
  .no { flex: none; width: 22px; height: 22px; margin-top: 1px; border-radius: 50%; display: grid; place-items: center; font: 600 12px var(--font); border: 1.5px solid var(--border-strong); color: var(--fg-2); }
  .tx { min-width: 0; flex: 1; } h2 { font-size: 15px; } p { margin: 2px 0 0; color: var(--fg-muted); font-size: 12.5px; line-height: 1.45; max-width: 62ch; }
  .hd { flex: none; } .bd { display: grid; gap: var(--s3); min-width: 0; }
  @media (max-width: 560px) { .pc { padding: 12px; } }
</style>
