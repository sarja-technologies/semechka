<script lang="ts">
  // A unified diff. Added and removed lines carry their + and - as text, so colour is never the only cue.
  import { diffLines } from '$lib/workmode/fold';
  let { unified, path = '' }: { unified: string; path?: string } = $props();
  const lines = $derived(diffLines(unified));
</script>
<div class="diff" role="group" aria-label={path ? `Changes to ${path}` : 'Changes'}>
  {#each lines as l, i (i)}<div class="ln {l.t}"><span class="gut" aria-hidden="true"></span><code>{l.s || ' '}</code></div>{/each}
</div>
<style>
  .diff { font: 12px/1.55 var(--mono); border: 1px solid var(--border); border-radius: var(--radius-sm); overflow: auto; max-height: 360px; background: var(--card-bg); }
  .ln { display: grid; grid-template-columns: 4px minmax(0, 1fr); min-width: max-content; }
  .ln code { font: inherit; white-space: pre; padding: 0 10px 0 6px; }
  .ln.add { background: var(--success-soft); } .ln.add .gut { background: var(--success); }
  .ln.del { background: var(--danger-soft); } .ln.del .gut { background: var(--danger); }
  .ln.hunk { background: var(--info-soft); color: var(--info); }
  .ln.meta { color: var(--fg-muted); }
</style>
