<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import Pager from '$lib/components/admin/Pager.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  import { loadRoles, loadTeams, type Opt } from '$lib/components/admin/lookup';
  let rows = $state<any[]>([]); let total = $state(0); let loading = $state(true); let error = $state('');
  let q = $state(''); let status = $state(''); let role = $state(''); let team = $state(''); let sort = $state('created_at'); let order = $state('desc'); let pg = $state(1); const per = 25;
  let roles = $state<Opt[]>([]); let teams = $state<Opt[]>([]);
  let picked = $state<Set<string>>(new Set()); let allMatching = $state(false);
  async function load() {
    loading = true; error = '';
    try { const r = await get('/api/admin/users' + qs({ q, status, role, team, sort, order, page: pg, per_page: per })); rows = r.items; total = r.total; } catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(async () => { load(); roles = await loadRoles(); teams = await loadTeams(); });
  let t: any; const filt = () => { clearTimeout(t); t = setTimeout(() => { pg = 1; picked = new Set(); allMatching = false; load(); }, 250); };
  const sortBy = (c: string) => { if (sort === c) order = order === 'asc' ? 'desc' : 'asc'; else { sort = c; order = 'asc'; } load(); };
  const pageAll = $derived(rows.length > 0 && rows.every((r) => picked.has(r.id)));
  function togglePage() { picked = pageAll ? new Set() : new Set(rows.map((r) => r.id)); allMatching = false; }
  function toggle(id: string) { const n = new Set(picked); n.has(id) ? n.delete(id) : n.add(id); picked = n; allMatching = false; }
  const count = $derived(allMatching ? total : picked.size);
  // bulk
  let bulkOpen = $state(false); let action = $state('approve'); let bulkRole = $state(''); let bulkErr = $state(''); let typed = $state('');
  const LABEL: Record<string, string> = { approve: 'Approve', activate: 'Reactivate', suspend: 'Suspend', delete: 'Delete', add_role: 'Add role', remove_role: 'Remove role' };
  function openBulk(a: string) { action = a; bulkErr = ''; typed = ''; bulkOpen = true; }
  async function runBulk() {
    bulkErr = '';
    try {
      const body: any = { action, expected_count: count, role_id: bulkRole || undefined };
      if (allMatching) body.all_matching = { q: q || undefined, status: status || undefined, role: role || undefined, team: team || undefined }; else body.ids = [...picked];
      const r = await post('/api/admin/users/bulk', body);
      bulkOpen = false; picked = new Set(); allMatching = false;
      toast(`${LABEL[action]}: ${r.done} done${r.skipped.length ? `, ${r.skipped.length} skipped (${r.skipped[0].reason})` : ''}.`, r.skipped.length ? 'info' : 'ok'); load();
    } catch (e) { bulkErr = errMsg(e); }
  }
  // create
  let cOpen = $state(false); let c = $state({ email: '', display_name: '', password: '', role_id: 'member' }); let cErr = $state('');
  async function create() { cErr = ''; try { await post('/api/admin/users', c); cOpen = false; c = { email: '', display_name: '', password: '', role_id: 'member' }; toast('Account created.'); load(); } catch (e) { cErr = errMsg(e); } }
  const chip = (s: string) => (s === 'active' ? 'ok' : s === 'suspended' ? 'bad' : 'warn');
</script>
<Page title="People & Accounts" lead="Everyone with an account. Permissions come from roles, teams and per-person overrides; open a person to see exactly why.">
  {#snippet actions()}<button class="btn primary" onclick={() => (cOpen = true)}>Create account</button>{/snippet}
  <div class="row" style="margin-bottom:12px" role="search">
    <div style="flex:1 1 220px;max-width:320px"><label class="sr-only" for="pq">Search people</label><input id="pq" class="input" type="search" placeholder="Search name or email" bind:value={q} oninput={filt} /></div>
    <label class="sr-only" for="ps">Status</label><select id="ps" class="input" style="max-width:190px" bind:value={status} onchange={filt}><option value="">Any status</option><option value="active">Active</option><option value="pending_approval">Waiting for approval</option><option value="pending_verification">Unverified email</option><option value="suspended">Suspended</option></select>
    <label class="sr-only" for="pr">Role</label><select id="pr" class="input" style="max-width:190px" bind:value={role} onchange={filt}><option value="">Any role</option>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select>
    <label class="sr-only" for="pt">Team</label><select id="pt" class="input" style="max-width:190px" bind:value={team} onchange={filt}><option value="">Any team</option>{#each teams as r}<option value={r.id}>{r.label}</option>{/each}</select>
  </div>
  {#if count > 0}
    <div class="notice info" role="region" aria-label="Bulk actions"><div class="grow">
      <strong>{count}</strong> {allMatching ? 'people matching the current filters' : 'selected on this page'}.
      {#if pageAll && !allMatching && total > rows.length}<button class="btn sm ghost" onclick={() => (allMatching = true)}>Select all {total} matching across all pages</button>{/if}
      {#if allMatching}<button class="btn sm ghost" onclick={() => (allMatching = false)}>Only this page ({rows.length})</button>{/if}
    </div><div class="row">{#each ['approve', 'activate', 'suspend', 'add_role', 'remove_role', 'delete'] as a}<button class="btn sm" class:danger={a === 'delete' || a === 'suspend'} onclick={() => openBulk(a)}>{LABEL[a]}</button>{/each}</div></div>
  {/if}
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No one matches these filters.' : ''} />
  {#if rows.length}
  <div class="tablewrap"><table class="t"><caption class="sr-only">People, sorted by {sort} {order}ending</caption>
    <thead><tr><th style="width:40px"><input type="checkbox" aria-label="Select all {rows.length} people on this page" checked={pageAll} onchange={togglePage} /></th>
      <th aria-sort={sort === 'name' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}><button onclick={() => sortBy('name')}>Name</button></th>
      <th aria-sort={sort === 'email' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}><button onclick={() => sortBy('email')}>Email</button></th><th>Roles</th><th>Teams</th>
      <th aria-sort={sort === 'status' ? (order === 'asc' ? 'ascending' : 'descending') : 'none'}><button onclick={() => sortBy('status')}>Status</button></th><th><button onclick={() => sortBy('last_login')}>Last sign-in</button></th></tr></thead>
    <tbody>{#each rows as u (u.id)}
      <tr><td><input type="checkbox" aria-label="Select {u.display_name}" checked={picked.has(u.id)} onchange={() => toggle(u.id)} /></td>
        <td><a href="/admin/people/{u.id}">{u.display_name}</a></td><td class="mono">{u.email}</td>
        <td>{u.roles.map((r: any) => r.label).join(', ')}</td><td>{u.teams.map((r: any) => r.name).join(', ') || '—'}</td>
        <td><span class="chip {chip(u.status)}">{u.status.replace('_', ' ')}</span>{#if u.mfa} <span class="chip">MFA</span>{/if}</td><td>{fmtTime(u.last_login_at)}</td></tr>{/each}</tbody></table></div>
  {/if}
  <Pager bind:page={pg} perPage={per} {total} onchange={() => { picked = new Set(); allMatching = false; load(); }} />
</Page>
<Modal bind:open={bulkOpen} title="{LABEL[action]} {count} {count === 1 ? 'person' : 'people'}?">
  <p>{allMatching ? `This applies to all ${total} people matching the current filters, on every page.` : `This applies to the ${picked.size} people you selected on this page.`}</p>
  {#if action === 'add_role' || action === 'remove_role'}<div class="field"><label for="br">Role</label><select id="br" class="input" bind:value={bulkRole}><option value="">Choose a role…</option>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select></div>{/if}
  {#if action === 'delete'}<p class="err">Deleting removes accounts and their saved conversations. This cannot be undone.</p>{/if}
  {#if bulkErr}<p class="err" role="alert">{bulkErr}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (bulkOpen = false)}>Cancel</button><button class="btn {action === 'delete' ? 'danger solid' : 'primary'}" disabled={(action.endsWith('role') && !bulkRole)} onclick={runBulk}>{LABEL[action]} {count}</button>{/snippet}
</Modal>
<Modal bind:open={cOpen} title="Create account">
  <div class="field"><label for="ce">Email</label><input id="ce" class="input" type="email" bind:value={c.email} /></div>
  <div class="field"><label for="cn">Display name</label><input id="cn" class="input" bind:value={c.display_name} /></div>
  <div class="field"><label for="cp">Initial password</label><input id="cp" class="input" type="password" autocomplete="new-password" bind:value={c.password} /><span class="hint">Share it through a safe channel; the person can change it after signing in.</span></div>
  <div class="field"><label for="cr">Role</label><select id="cr" class="input" bind:value={c.role_id}>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select></div>
  {#if cErr}<p class="err" role="alert">{cErr}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (cOpen = false)}>Cancel</button><button class="btn primary" onclick={create}>Create</button>{/snippet}
</Modal>
