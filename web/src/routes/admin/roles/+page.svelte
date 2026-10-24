<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, put, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import PermMatrix from '$lib/components/admin/PermMatrix.svelte';
  import { can } from '$lib/session.svelte';
  import { errMsg } from '$lib/components/admin/util';
  import { loadRoles } from '$lib/components/admin/lookup';
  let rows = $state<any[]>([]); let loading = $state(true); let error = $state('');
  let open = $state(false); let cur = $state<any>(null); let f = $state({ label: '', description: '', rank: 100 }); let grants = $state<string[]>([]); let denies = $state<string[]>([]); let err = $state('');
  let delT = $state<any>(null); let delOpen = $state(false);
  const canEdit = $derived(can('admin.roles.manage'));
  async function load() { loading = true; try { rows = await get('/api/admin/roles'); error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  function edit(r: any) { cur = r; f = { label: r?.label ?? '', description: r?.description ?? '', rank: r?.rank ?? 100 }; grants = [...(r?.grants ?? [])]; denies = [...(r?.denies ?? [])]; err = ''; open = true; }
  async function save() { err = ''; const b = { ...f, rank: Number(f.rank), grants, denies }; try { if (cur) await put(`/api/admin/roles/${cur.id}`, b); else await post('/api/admin/roles', b); await loadRoles(true); open = false; toast('Role saved.'); load(); } catch (e) { err = errMsg(e); } }
  async function remove() { try { await del(`/api/admin/roles/${delT.id}`); await loadRoles(true); delOpen = false; toast('Role deleted.'); load(); } catch (e) { toast(errMsg(e), 'bad'); delOpen = false; } }
</script>
<Page title="Roles & Permissions" lead="Roles are editable templates. Renaming one never changes its internal id, so every rule that refers to it keeps working. You can only hand out permissions you hold yourself.">
  {#snippet actions()}{#if canEdit}<button class="btn primary" onclick={() => edit(null)}>Create role</button>{/if}{/snippet}
  <State {loading} {error} retry={load} />
  {#if rows.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Roles</caption><thead><tr><th>Role</th><th>Internal id</th><th>People</th><th>Priority</th><th>Grants</th><th>Denies</th><th></th></tr></thead><tbody>
    {#each rows as r (r.id)}<tr><td><strong>{r.label}</strong><div class="hint wrap" style="max-width:34ch;white-space:normal">{r.description}</div></td><td class="mono">{r.id}</td><td class="num">{r.members}</td><td class="num">{r.rank}</td><td class="num" title={r.grants.join(', ')}>{r.grants.length} rules</td><td class="num">{r.denies.length}</td>
      <td>{#if canEdit}<button class="btn sm" onclick={() => edit(r)}>Edit</button>{#if !r.system} <button class="btn sm danger" onclick={() => { delT = r; delOpen = true; }}>Delete</button>{/if}{:else}<button class="btn sm" onclick={() => edit(r)}>View</button>{/if}</td></tr>{/each}</tbody></table></div>{/if}
</Page>
<Modal bind:open title={cur ? `${canEdit ? 'Edit' : 'View'} ${cur.label}` : 'Create role'} wide>
  <div class="row"><div class="field grow"><label for="rl">Display name</label><input id="rl" class="input" bind:value={f.label} disabled={!canEdit} /></div><div class="field"><label for="rk">Priority (lower wins settings merges)</label><input id="rk" class="input" type="number" min="0" bind:value={f.rank} disabled={!canEdit} style="max-width:120px" /></div></div>
  <div class="field"><label for="rd">Description</label><input id="rd" class="input" bind:value={f.description} disabled={!canEdit} /></div>
  {#if cur?.id === 'owner'}<div class="notice info"><div>The owner role must keep the “*” grant so the platform cannot be locked out. Permissions marked sensitive are never covered by “*” and must be granted explicitly.</div></div>{/if}
  {#if canEdit}<PermMatrix bind:grants bind:denies who="people in this role" />{:else}<p class="mono tiny">grants: {grants.join(', ')}<br />denies: {denies.join(', ') || '—'}</p>{/if}
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (open = false)}>Close</button>{#if canEdit}<button class="btn primary" onclick={save}>Save role</button>{/if}{/snippet}
</Modal>
<Modal bind:open={delOpen} title="Delete {delT?.label}?"><p>This role has {delT?.members} people. Roles with people must be emptied first.</p>{#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete role</button>{/snippet}</Modal>
