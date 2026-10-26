<script lang="ts">
  // Organisation administration: people, groups, roles, policy, usage and audit. Seeing this area is not the same as
  // seeing inside any workspace: administering people and policy never opens anyone's files.
  import { page } from '$app/state';
  import { org, ocan, loadOrg } from '$lib/ws/org.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import State from '$lib/components/admin/State.svelte';
  let { children } = $props();
  const oid = $derived(page.params.oid ?? ''), base = $derived(`/organisation/${oid}`);
  $effect(() => { if (oid && org.o?.id !== oid) loadOrg(oid); });
  const tabs = $derived([
    { href: base, label: 'People', icon: 'users', show: true, exact: true }, { href: `${base}/groups`, label: 'Groups', icon: 'users', show: true },
    { href: `${base}/roles`, label: 'Roles', icon: 'key', show: true }, { href: `${base}/policy`, label: 'Policy and limits', icon: 'sliders', show: ocan('org.policy.edit') },
    { href: `${base}/usage`, label: 'Usage', icon: 'usage', show: ocan('org.policy.edit') }, { href: `${base}/audit`, label: 'Activity log', icon: 'log', show: ocan('org.audit.view') }
  ].filter((t) => t.show));
  const on = (t: any) => (t.exact ? page.url.pathname === t.href : page.url.pathname.startsWith(t.href));
</script>
<svelte:head><title>{org.o?.name ?? 'Organisation'}</title></svelte:head>
<div class="wrap">
  <nav class="tiny muted" aria-label="Breadcrumb" style="margin-bottom:8px"><a href="/workspaces" style="color:inherit">Workspaces</a> / <span>Organisation</span></nav>
  {#if org.loading && !org.o}<p class="muted" role="status">Loading…</p>
  {:else if org.error}<State error={org.error} retry={() => loadOrg(oid)} />
  {:else if org.o}
    <header class="head"><div class="grow"><h1>{org.o.name}</h1><p class="muted" style="margin:4px 0 0">Administering people and policy. This does not open any workspace's files.</p></div>
      {#if org.o.status !== 'active'}<span class="chip warn">Suspended</span>{/if}</header>
    <nav class="tabs" aria-label="Organisation sections">{#each tabs as t}<a href={t.href} class:on={on(t)} aria-current={on(t) ? 'page' : undefined}><Icon name={t.icon} size={16} />{t.label}</a>{/each}</nav>
    {#key oid}{@render children()}{/key}
  {/if}
</div>
<style>
  .wrap { padding: 20px 28px 64px; max-width: 1200px; width: 100%; } .head { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 16px; }
  .tabs { display: flex; gap: 2px; border-bottom: 1px solid var(--border); margin-bottom: 20px; overflow-x: auto; }
  .tabs a { display: inline-flex; align-items: center; gap: 8px; padding: 0 14px; min-height: 40px; color: var(--fg-2); text-decoration: none; border-bottom: 2px solid transparent; margin-bottom: -1px; white-space: nowrap; }
  .tabs a:hover { color: var(--fg); background: var(--hover); } .tabs a.on { color: var(--fg); border-bottom-color: var(--accent); font-weight: 600; }
  @media (max-width: 700px) { .wrap { padding: 12px 16px 64px; } }
  .wrap :global(label.row:has(> input[type='checkbox'], > input[type='radio'])) { min-height: 28px; }
</style>
