<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // People: who belongs to the organisation and what organisation-level role each holds.
  import { page } from '$app/state';
  import { post, patch, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { org, ocan, reloadOrg, rname } from '$lib/ws/org.svelte';
  import { msg } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  const oid = $derived(page.params.oid ?? '');
  const manage = $derived(ocan('org.members.manage'));
  const orgRoles = $derived(org.roles.filter((r) => r.scope === 'org'));
  let q = $state('');
  const rows = $derived(org.members.filter((m) => !q || m.display_name.toLowerCase().includes(q.toLowerCase()) || (m.email ?? '').toLowerCase().includes(q.toLowerCase())));
  let dlg = $state(false), email = $state(''), role = $state('member'), formErr = $state(''), busy = $state(false);
  async function add() {
    busy = true; formErr = '';
    try { await post(`/api/orgs/${oid}/members`, { email, roles: [role] }); dlg = false; email = ''; toast('Added'); await reloadOrg(); } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const act = (fn: () => Promise<unknown>, done: string) => fn().then(() => { toast(done); return reloadOrg(); }).catch((e) => { toast(msg(e), 'bad'); return reloadOrg(); });
  const setRole = (m: any, r: string) => act(() => patch(`/api/orgs/${oid}/members/${m.user_id}`, { roles: [r] }), 'Role changed');
  const toggle = (m: any) => act(() => patch(`/api/orgs/${oid}/members/${m.user_id}`, { status: m.status === 'active' ? 'suspended' : 'active' }), m.status === 'active' ? 'Suspended: no access to anything in this organisation' : 'Reactivated');
  const remove = (m: any) => act(() => del(`/api/orgs/${oid}/members/${m.user_id}`), 'Removed from the organisation and all its workspaces');
</script>

<div class="row" style="margin-bottom:12px">
  <h2 class="grow">People <span class="muted">({org.members.length})</span></h2>
  <label class="sr-only" for="pq">Filter people</label><input id="pq" class="input" style="max-width:240px" placeholder="Filter" bind:value={q} />
  {#if manage}<button class="btn primary" onclick={() => { formErr = ''; dlg = true; }}><Icon name="plus" size={16} /> Add person</button>{/if}
</div>
<div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">People in the organisation</caption>
  <thead><tr><th>Name</th>{#if manage}<th>Email</th>{/if}<th>Role</th><th>Status</th>{#if manage}<th></th>{/if}</tr></thead>
  <tbody>{#each rows as m (m.user_id)}<tr>
    <td>{m.display_name}</td>{#if manage}<td class="muted">{m.email}</td>{/if}
    <td>{#if manage}<label class="sr-only" for="r-{m.user_id}">Role of {m.display_name}</label><select id="r-{m.user_id}" class="input" style="max-width:240px" value={m.role_ids[0]} onchange={(e) => setRole(m, (e.currentTarget as HTMLSelectElement).value)}>{#each orgRoles as r}<option value={r.id}>{r.label}</option>{/each}</select>{:else}{m.role_ids.map(rname).join(', ')}{/if}</td>
    <td><span class="chip" class:ok={m.status === 'active'} class:warn={m.status !== 'active'}>{m.status === 'active' ? 'Active' : 'Suspended'}</span></td>
    {#if manage}<td style="text-align:right"><button class="btn sm ghost" onclick={() => toggle(m)}>{m.status === 'active' ? 'Suspend' : 'Reactivate'}</button><button class="btn sm ghost" onclick={() => remove(m)} aria-label="Remove {m.display_name}">Remove</button></td>{/if}</tr>{/each}</tbody></table></div>
<p class="tiny muted" style="margin-top:8px">An organisation always keeps at least one active owner. Removing someone ends their access to every workspace here at once. Their role in the organisation does not by itself let them read any workspace's files.</p>

<Modal bind:open={dlg} title="Add a person">
  <form id="pf" onsubmit={(e) => { e.preventDefault(); add(); }}>
    <div class="field"><label for="pe">Email</label><input id="pe" class="input" type="email" bind:value={email} required /><span class="hint">They need an account on this server first; an administrator creates accounts.</span></div>
    <div class="field"><label for="pr">Role in the organisation</label><select id="pr" class="input" bind:value={role}>{#each orgRoles as r}<option value={r.key}>{r.label}</option>{/each}</select>
      <span class="hint">{orgRoles.find((r) => r.key === role)?.description}</span></div>
    {#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
  </form>
  {#snippet footer()}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" form="pf" type="submit" disabled={busy || !email.trim()}>Add</button>{/snippet}
</Modal>
