<script lang="ts">
  // The passages that were given to the model for one reply. Shown as retrieved, never as proof the model used them.
  let { sources, skipped = 0 }: { sources: any[]; skipped?: number } = $props();
</script>
{#if sources?.length || skipped}
  <details class="src">
    <summary class="small">{sources.length ? `Sources given to the model (${sources.length})` : 'No sources'}{skipped ? ` · ${skipped} collection${skipped > 1 ? 's' : ''} skipped` : ''}</summary>
    {#if skipped}<p class="small warn-t" role="status">{skipped} attached collection{skipped > 1 ? 's were' : ' was'} skipped because you no longer have access or {skipped > 1 ? 'they were' : 'it was'} deleted.</p>{/if}
    <p class="tiny muted" style="margin:4px 0">Found by keyword match, not by meaning. The model may not have used every passage.</p>
    <ol class="list">
      {#each sources as s (s.n)}
        <li><span class="mono tiny">[{s.n}]</span> <strong class="small">{s.file}</strong> <span class="muted tiny">passage {s.chunk + 1}</span>
          <blockquote>{s.excerpt}{s.excerpt.length >= 400 ? '…' : ''}</blockquote></li>
      {/each}
    </ol>
  </details>
{/if}
<style>
  .src { margin-top: 8px; border-left: 2px solid var(--border-strong); padding-left: 10px; }
  summary { cursor: pointer; color: var(--fg-muted); }
  .list { margin: 4px 0 0; padding-left: 0; list-style: none; display: grid; gap: 8px; }
  blockquote { margin: 2px 0 0; font-size: 12.5px; color: var(--fg-muted); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 9em; overflow: auto; }
  .warn-t { color: var(--warn); }
</style>
