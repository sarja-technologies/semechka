<script lang="ts">
  // One workspace: header, who you are here, and the tabs your permissions allow. The tabs share one loaded workspace.
  import { page } from '$app/state';
  import { cur, can, loadWorkspace } from '$lib/ws/current.svelte';
  import { accessLevel } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import State from '$lib/components/admin/State.svelte';
  let { children } = $props();
  const wid = $derived(page.params.wid ?? '');
  $effect(() => { if (wid && cur.ws?.id !== wid) loadWorkspace(wid); });
  const base = $derived(`/workspaces/${wid}`);
  const tabs = $derived([
    { href: base, label: 'Files', icon: 'folder', show: can('file.read'), exact: true },
    { href: `${base}/contexts`, label: 'Contexts', icon: 'context', show: can('ctx.read') },
    { href: `${base}/agents`, label: 'Agents', icon: 'agents', show: can('agent.view') },
    { href: `${base}/tasks`, label: 'Tasks', icon: 'history', show: can('ws.view') },
    { href: `${base}/access`, label: 'Access', icon: 'lock', show: can('ws.members.manage') },
    { href: `${base}/settings`, label: 'Settings', icon: 'settings', show: can('ws.members.manage') || can('ws.policy.edit') }
  ].filter((t) => t.show));
  const on = (t: any) => (t.exact ? page.url.pathname === t.href : page.url.pathname.startsWith(t.href));
  const listedOnly = $derived(cur.ws && !can('file.read') && !can('ctx.read') && !can('agent.view'));
</script>

<svelte:head><title>{cur.ws?.name ?? 'Workspace'} · Workspaces</title></svelte:head>
<div class="wrap">
  <nav class="crumb tiny muted" aria-label="Breadcrumb"><a href="/workspaces">Workspaces</a>{#if cur.org}<span aria-hidden="true"> / </span><span>{cur.org.name}</span>{/if}</nav>
  {#if cur.loading && !cur.ws}<p class="muted" role="status">Loading…</p>
  {:else if cur.error}<State error={cur.error} retry={() => loadWorkspace(wid)} /><p><a class="btn sm" href="/workspaces">Back to workspaces</a></p>
  {:else if cur.ws}
    <header class="head">
      <div class="grow">
        <h1>{cur.ws.name}</h1>
        {#if cur.ws.description}<p class="muted" style="margin:4px 0 0;max-width:70ch">{cur.ws.description}</p>{/if}
      </div>
      <div class="row">
        <span class="chip">{cur.ws.visibility === 'shared' ? 'Shared' : 'Private'}</span>
        {#if cur.ws.archived}<span class="chip warn">Archived, read only</span>{/if}
        <span class="chip" class:ok={can('file.read')} title="What you can do here">{accessLevel(cur.ws.my_permissions)}</span>
      </div>
    </header>
    {#if listedOnly}
      <div class="notice info" role="status"><div><strong>You can see that this workspace exists, but not what is in it.</strong> Ask one of its administrators to add you.</div></div>
    {:else}
      <nav class="tabs" aria-label="Workspace sections">
        {#each tabs as t}<a href={t.href} class:on={on(t)} aria-current={on(t) ? 'page' : undefined}><Icon name={t.icon} size={16} />{t.label}</a>{/each}
      </nav>
      {#key wid}{@render children()}{/key}
    {/if}
  {/if}
</div>

<style>
  .wrap { padding: 20px 28px 64px; max-width: 1200px; width: 100%; }
  .crumb { margin-bottom: 8px; } .crumb a { color: inherit; }
  .head { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; }
  .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-bottom: 20px; overflow-x: auto; }
  .tabs a { display: inline-flex; align-items: center; gap: 8px; padding: 0 14px; min-height: 40px; color: var(--fg-2); text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap; }
  .tabs a:hover { color: var(--fg); background: var(--hover); }
  .tabs a.on { color: var(--fg); border-bottom-color: var(--accent); font-weight: 600; }
  @media (max-width: 700px) { .wrap { padding: 12px 16px 64px; } }
  .wrap :global(label.row:has(> input[type='checkbox'], > input[type='radio'])) { min-height: 28px; }
</style>
