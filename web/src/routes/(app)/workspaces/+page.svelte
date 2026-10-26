<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // The organisation's workspaces the signed-in person can see, and (where allowed) a way to create one.
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { msg, accessLevel } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import State from '$lib/components/admin/State.svelte';

  let orgs = $state<any[]>([]), oid = $state(''), list = $state<any[]>([]), loading = $state(true), loadingList = $state(false), error = $state('');
  const org = $derived(orgs.find((o) => o.id === oid) ?? null);
  const canCreate = $derived(!!org?.my_permissions?.includes('org.workspaces.create'));
  const orgAdmin = $derived(!!org?.my_permissions?.some((p: string) => ['org.members.manage', 'org.roles.manage', 'org.policy.edit', 'org.audit.view', 'org.groups.manage'].includes(p)));
  const byId = $derived(Object.fromEntries(list.map((w) => [w.id, w])));
  // children are shown under their parent, indented, in name order
  const rows = $derived.by(() => {
    const out: any[] = [];
    const walk = (parent: string | null, depth: number) => {
      for (const w of list.filter((x) => (x.parent_id && byId[x.parent_id] ? x.parent_id : null) === parent).sort((a, b) => a.name.localeCompare(b.name))) { out.push({ ...w, depth }); walk(w.id, depth + 1); }
    };
    walk(null, 0);
    return out;
  });

  async function loadOrgs() {
    try {
      orgs = await get('/api/orgs');
      let saved = ''; try { saved = localStorage.getItem('sem.org') ?? ''; } catch { /* storage unavailable */ }
      oid = orgs.find((o) => o.id === saved)?.id ?? orgs[0]?.id ?? '';
      error = '';
    } catch (e) { error = msg(e); } finally { loading = false; }
  }
  async function loadList() {
    if (!oid) { list = []; return; }
    loadingList = true;
    try { list = await get(`/api/orgs/${oid}/workspaces`); error = ''; } catch (e) { error = msg(e); list = []; } finally { loadingList = false; }
  }
  onMount(loadOrgs);
  $effect(() => { oid; try { if (oid) localStorage.setItem('sem.org', oid); } catch { /* storage unavailable */ } loadList(); });

  let newOpen = $state(false), nName = $state(''), nDesc = $state(''), nVis = $state('private'), nParent = $state(''), nErr = $state(''), busy = $state(false);
  const parents = $derived(list.filter((w) => w.my_permissions?.includes('ws.members.manage')));
  async function create() {
    nErr = ''; busy = true;
    try {
      const w = await post(`/api/orgs/${oid}/workspaces`, { name: nName, description: nDesc, visibility: nVis, parent_id: nParent || null });
      newOpen = false; nName = nDesc = nParent = ''; nVis = 'private'; toast('Workspace created'); await loadList();
      location.assign(`/workspaces/${w.id}`);
    } catch (e) { nErr = msg(e); } finally { busy = false; }
  }
</script>

<svelte:head><title>Workspaces</title></svelte:head>
<div class="wrap">
  <header class="ph">
    <div class="grow"><h1>Workspaces</h1><p class="muted" style="margin:4px 0 0;max-width:70ch">Shared places for an organisation's files, knowledge and agents. You only see what you have been given access to.</p></div>
    <div class="row">
      {#if orgAdmin}<a class="btn" href="/organisation/{oid}"><Icon name="users" size={16} /> Organisation</a>{/if}
      {#if canCreate}<button class="btn primary" onclick={() => { nErr = ''; newOpen = true; }}><Icon name="plus" size={16} /> New workspace</button>{/if}
    </div>
  </header>

  <State loading={loading} {error} retry={loadOrgs} />
  {#if !loading && !error && !orgs.length}
    <div class="notice info" role="status"><div><strong>You are not part of an organisation yet.</strong> Ask an administrator to add you; workspaces belong to organisations.</div></div>
  {:else if orgs.length}
    {#if orgs.length > 1}
      <div class="field" style="max-width:320px"><label for="org">Organisation</label>
        <select id="org" class="input" bind:value={oid}>{#each orgs as o}<option value={o.id}>{o.name}</option>{/each}</select></div>
    {:else}<p class="muted small" style="margin-bottom:12px">{org?.name}</p>{/if}

    {#if loadingList}<p class="muted" role="status">Loading…</p>
    {:else if !rows.length && !error}
      <div class="panel"><div class="panel-b">
        <p><strong>No workspaces visible to you yet.</strong></p>
        <p class="muted">{canCreate ? 'Create the first one to start sharing files and knowledge.' : 'When someone adds you to a workspace it will appear here.'}</p>
        {#if canCreate}<button class="btn primary" onclick={() => { nErr = ''; newOpen = true; }}><Icon name="plus" size={16} /> New workspace</button>{/if}
      </div></div>
    {:else if rows.length}
      <div class="tablewrap" use:scrollable><table class="t">
        <caption class="sr-only">Workspaces in {org?.name}</caption>
        <thead><tr><th>Name</th><th>Visibility</th><th>Your access</th><th></th></tr></thead>
        <tbody>
          {#each rows as w (w.id)}
            <tr>
              <td class="wrap" style="padding-left:{12 + w.depth * 22}px"><a href="/workspaces/{w.id}" class="nm"><Icon name="workspaces" size={16} /> {w.name}</a>
                {#if w.description}<div class="tiny muted">{w.description}</div>{/if}</td>
              <td><span class="chip">{w.visibility === 'shared' ? 'Shared' : 'Private'}</span>{#if w.archived} <span class="chip warn">Archived</span>{/if}</td>
              <td><span class="chip" class:ok={w.my_permissions?.includes('file.read')}>{accessLevel(w.my_permissions)}</span></td>
              <td style="text-align:right"><a class="btn sm ghost" href="/workspaces/{w.id}">Open</a></td>
            </tr>
          {/each}
        </tbody>
      </table></div>
      <p class="tiny muted" style="margin-top:8px"><strong>Shared</strong> workspaces are visible to everyone in the organisation, but their contents are open only to the people given access. <strong>Listed only</strong> means you can see that a workspace exists, not what is in it.</p>
    {/if}
  {/if}
</div>

<Modal bind:open={newOpen} title="New workspace">
  <form id="nw" onsubmit={(e) => { e.preventDefault(); create(); }}>
    <div class="field"><label for="n">Name</label><input id="n" class="input" bind:value={nName} maxlength="160" required /></div>
    <div class="field"><label for="d">Description <span class="muted">(optional)</span></label><textarea id="d" class="input" bind:value={nDesc} rows="2"></textarea></div>
    <fieldset class="field" style="border:0;padding:0">
      <legend class="label">Who can see that it exists</legend>
      <label class="row"><input type="radio" bind:group={nVis} value="private" /><span><strong>Private.</strong> Only people you add can see it or its contents.</span></label>
      <label class="row"><input type="radio" bind:group={nVis} value="shared" /><span><strong>Shared.</strong> Everyone in the organisation sees it listed; contents stay limited to the people you add.</span></label>
    </fieldset>
    {#if parents.length}
      <div class="field"><label for="p">Inside another workspace <span class="muted">(optional)</span></label>
        <select id="p" class="input" bind:value={nParent}><option value="">No, a top-level workspace</option>{#each parents as p}<option value={p.id}>{p.name}</option>{/each}</select>
        <span class="hint">People in the parent workspace are also members of this one.</span></div>
    {/if}
    {#if nErr}<div class="err" role="alert">{nErr}</div>{/if}
  </form>
  {#snippet footer()}<button class="btn" onclick={() => (newOpen = false)}>Cancel</button><button class="btn primary" form="nw" type="submit" disabled={busy || !nName.trim()}>Create workspace</button>{/snippet}
</Modal>

<style>
  .wrap { padding: 24px 28px 64px; max-width: 1100px; width: 100%; }
  .ph { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; margin-bottom: 20px; }
  .nm { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; color: var(--fg); text-decoration: none; }
  .nm:hover { text-decoration: underline; }
  @media (max-width: 700px) { .wrap { padding: 16px 16px 64px; } }
</style>
