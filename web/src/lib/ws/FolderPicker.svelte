<script lang="ts">
  // Lazy folder tree: expands one level at a time from the server, so people only ever see folders they may open.
  import { get } from '$lib/api';
  import Icon from '$lib/components/Icon.svelte';
  let { wid, value = $bindable<string | null>(null), exclude = '' }: { wid: string; value: string | null; exclude?: string } = $props();
  type Node = { id: string; name: string; open: boolean; loaded: boolean; kids: Node[]; error?: string };
  let root = $state<Node>({ id: '', name: 'Workspace root', open: true, loaded: false, kids: [] });
  async function load(n: Node) {
    try {
      const r = await get(`/api/workspaces/${wid}/files${n.id ? `?folder_id=${n.id}` : ''}`);
      n.kids = r.folders.filter((f: any) => f.id !== exclude).map((f: any) => ({ id: f.id, name: f.name, open: false, loaded: false, kids: [] }));
      n.loaded = true; n.error = '';
    } catch { n.error = 'Could not load this folder.'; }
  }
  $effect(() => { if (!root.loaded) load(root); });
  async function toggle(n: Node) { n.open = !n.open; if (n.open && !n.loaded) await load(n); }
</script>
{#snippet branch(n: Node, depth: number)}
  <li role="treeitem" aria-selected={value === (n.id || null)} aria-expanded={n.open}>
    <div class="r" style="padding-left:{depth * 18}px">
      <button type="button" class="tg" aria-label={n.open ? 'Collapse' : 'Expand'} onclick={() => toggle(n)}><Icon name={n.open ? 'chevdown' : 'chevron'} size={14} /></button>
      <button type="button" class="pick" class:on={value === (n.id || null)} onclick={() => (value = n.id || null)}><Icon name="folder" size={15} /> {n.name}</button>
    </div>
    {#if n.error}<div class="err small" style="padding-left:{depth * 18 + 28}px">{n.error}</div>{/if}
    {#if n.open && n.kids.length}<ul role="group">{#each n.kids as k (k.id)}{@render branch(k, depth + 1)}{/each}</ul>{/if}
  </li>
{/snippet}
<ul role="tree" aria-label="Folders" class="tree">{@render branch(root, 0)}</ul>
<style>
  .tree, .tree ul { list-style: none; margin: 0; padding: 0; }
  .r { display: flex; align-items: center; gap: 2px; }
  .tg { all: unset; cursor: pointer; width: 24px; height: 28px; display: grid; place-items: center; color: var(--fg-muted); } .tg:focus-visible { outline: 2px solid var(--accent); }
  .pick { all: unset; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; padding: 4px 8px; border-radius: var(--radius-sm); flex: 1; min-height: 28px; }
  .pick:hover { background: var(--hover); } .pick.on { background: var(--selected); font-weight: 600; } .pick:focus-visible { outline: 2px solid var(--accent); }
</style>
