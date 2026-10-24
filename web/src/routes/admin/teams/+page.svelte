<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, put, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import PermMatrix from '$lib/components/admin/PermMatrix.svelte';
  import { errMsg } from '$lib/components/admin/util';
  import { loadTeams } from '$lib/components/admin/lookup';
  let rows = $state<any[]>([]); let loading = $state(true); let error = $state('');
  let open = $state(false); let cur = $state<any>(null); let name = $state(''); let grants = $state<string[]>([]); let denies = $state<string[]>([]); let err = $state('');
  let delT = $state<any>(null); let delOpen = $state(false);
  async function load() { loading = true; try { rows = await get('/api/admin/teams'); error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  function edit(t: any) { cur = t; name = t?.name ?? ''; grants = [...(t?.grants ?? [])]; denies = [...(t?.denies ?? [])]; err = ''; open = true; }
  async function save() { err = ''; try { if (cur) await put(`/api/admin/teams/${cur.id}`, { name, grants, denies }); else await post('/api/admin/teams', { name, grants, denies }); await loadTeams(true); open = false; toast('Team saved.'); load(); } catch (e) { err = errMsg(e); } }
  async function remove() { try { await del(`/api/admin/teams/${delT.id}`); await loadTeams(true); delOpen = false; toast('Team deleted.'); load(); } catch (e) { toast(errMsg(e), 'bad'); } }
</script>
<Page title="Teams" lead="Groups of people. A team can add permissions or deny them; a team deny overrides any role grant for its members.">
  {#snippet actions()}<button class="btn primary" onclick={() => edit(null)}>Create team</button>{/snippet}
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No teams yet. Teams let you give a group access to specific models or modes.' : ''} />
  {#if rows.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Teams</caption><thead><tr><th>Team</th><th>Members</th><th>Grants</th><th>Denies</th><th></th></tr></thead><tbody>
    {#each rows as t (t.id)}<tr><td>{t.name}</td><td class="num">{t.members}</td><td class="mono tiny wrap">{t.grants.join(', ') || '—'}</td><td class="mono tiny wrap">{t.denies.join(', ') || '—'}</td><td><button class="btn sm" onclick={() => edit(t)}>Edit</button> <button class="btn sm danger" onclick={() => { delT = t; delOpen = true; }}>Delete</button></td></tr>{/each}</tbody></table></div>{/if}
</Page>
<Modal bind:open title={cur ? `Edit ${cur.name}` : 'Create team'} wide>
  <div class="field"><label for="tn">Team name</label><input id="tn" class="input" bind:value={name} /></div>
  <PermMatrix bind:grants bind:denies who="members of this team" />
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (open = false)}>Cancel</button><button class="btn primary" onclick={save}>Save team</button>{/snippet}
</Modal>
<Modal bind:open={delOpen} title="Delete {delT?.name}?"><p>The team’s {delT?.members} members stay; its permission rules and any model rules naming it stop applying.</p>{#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete team</button>{/snippet}</Modal>
