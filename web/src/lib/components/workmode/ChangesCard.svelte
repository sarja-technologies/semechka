<script lang="ts">
  // What the agent changed in the workspace during one turn (a checkpoint comparison made by the server). The full diff loads when asked for.
  import Icon from '$lib/components/Icon.svelte';
  import DiffView from './DiffView.svelte';
  import { changes } from '$lib/workmode/client';
  import type { Item } from '$lib/workmode/types';
  let { c, cid }: { c: Extract<Item, { k: 'files' }>; cid: string | null } = $props();
  let diff = $state<string | null>(null), cut = $state(false), loading = $state(false), err = $state(''), open = $state(false);
  const add = $derived(c.files.reduce((n, f) => n + (f.additions ?? 0), 0)), del = $derived(c.files.reduce((n, f) => n + (f.deletions ?? 0), 0));
  const word = { added: 'added', modified: 'changed', deleted: 'deleted' } as const;
  async function toggle() {
    open = !open;
    if (open && diff === null && cid && c.turn) {
      loading = true; err = '';
      try { const r = await changes(cid, c.turn); diff = r.diff ?? ''; cut = !!r.truncated; } catch (e: any) { err = e?.message ?? 'Could not load the changes.'; } finally { loading = false; }
    }
  }
</script>
<section class="files" aria-label="Files changed">
  <div class="h"><Icon name="edit" size={15} /><strong>{c.files.length} file{c.files.length === 1 ? '' : 's'} changed</strong><span class="n"><span class="ad">+{add}</span> <span class="de">−{del}</span></span>
    {#if cid && c.turn}<button type="button" class="btn ghost sm" aria-expanded={open} onclick={toggle}>{open ? 'Hide changes' : 'Show changes'}</button>{/if}</div>
  <ul>{#each c.files as f (f.path)}<li><span class="s">{word[f.status] ?? f.status}</span> <code>{f.path}</code>{#if !f.binary && (f.additions || f.deletions)}&nbsp;&nbsp;<span class="tiny muted">+{f.additions ?? 0} −{f.deletions ?? 0}</span>{:else if f.binary}&nbsp;&nbsp;<span class="tiny muted">binary</span>{/if}</li>{/each}</ul>
  {#if c.truncated}<p class="tiny muted">More files changed than are listed.</p>{/if}
  {#if open}{#if loading}<p class="small muted" role="status">Loading the changes…</p>{:else if err}<p class="err small" role="alert">{err}</p>{:else if diff}<DiffView unified={diff} />{#if cut}<p class="tiny muted">The diff is cut at its size limit.</p>{/if}{:else}<p class="small muted">No text changes to show.</p>{/if}{/if}
</section>
<style>
  .files { border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); padding: 10px 12px; margin: 0 0 14px; display: grid; gap: 6px; }
  .h { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .h .btn { margin-left: auto; }
  .n { font: 12px var(--mono); } .ad { color: var(--success); } .de { color: var(--danger); }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; font-size: 13px; } li code { font-size: 12px; overflow-wrap: anywhere; } .s { color: var(--fg-muted); font-size: 12px; min-width: 56px; display: inline-block; }
  p { margin: 0; }
</style>
