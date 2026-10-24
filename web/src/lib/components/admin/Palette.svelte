<script lang="ts">
  import { goto } from '$app/navigation';
  import { get } from '$lib/api';
  import { can } from '$lib/session.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import { ADMIN_PAGES } from './util';
  let { open = $bindable(false) }: { open: boolean } = $props();
  let q = $state(''); let sel = $state(0);
  let settings = $state<{ id: string; label: string; category: string }[]>([]);
  let loaded = false;
  $effect(() => { if (open && !loaded && can('admin.settings.view')) { loaded = true; get('/api/admin/settings').then((r) => (settings = r.settings)).catch(() => (loaded = false)); } if (open) { q = ''; sel = 0; } });
  const results = $derived.by(() => {
    const t = q.toLowerCase().trim();
    const pages = ADMIN_PAGES.filter((p) => can(p.perm) && (!t || p.label.toLowerCase().includes(t))).map((p) => ({ kind: 'Page', label: p.label, href: p.href }));
    const sets = t ? settings.filter((s) => s.label.toLowerCase().includes(t) || s.id.includes(t)).slice(0, 12).map((s) => ({ kind: 'Setting', label: `${s.label}`, sub: s.category, href: `/admin/settings?q=${encodeURIComponent(s.id)}` })) : [];
    return [...pages, ...sets].slice(0, 20);
  });
  function go(r: { href: string }) { open = false; goto(r.href); }
  function key(e: KeyboardEvent) {
    if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(results.length - 1, sel + 1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(0, sel - 1); }
    else if (e.key === 'Enter' && results[sel]) go(results[sel]);
  }
</script>
<Modal bind:open title="Search pages and settings">
  <input class="input" placeholder="Type a page or setting, e.g. retention" aria-label="Search" bind:value={q} oninput={() => (sel = 0)} onkeydown={key} />
  <ul role="listbox" aria-label="Results" style="list-style:none;margin:8px 0 0;padding:0;max-height:50vh;overflow:auto">
    {#each results as r, i}
      <li role="option" aria-selected={i === sel}><button class="res" class:on={i === sel} onclick={() => go(r)}><span class="chip">{r.kind}</span> {r.label} {#if 'sub' in r}<span class="muted small">{(r as any).sub}</span>{/if}</button></li>
    {:else}<li class="muted">No matches.</li>{/each}
  </ul>
</Modal>
<style>.res { all: unset; display: flex; gap: 8px; align-items: center; width: 100%; box-sizing: border-box; padding: 8px; cursor: pointer; border-radius: var(--radius-sm); } .res.on, .res:hover { background: var(--surface-2); }</style>
