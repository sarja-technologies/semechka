<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Groups and departments. They can nest: someone in a sub-team counts as being in its department. A department
  // manager can look after their own group without being an organisation administrator.
  import { page } from '$app/state';
  import { post, put } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { org, ocan, reloadOrg, pname } from '$lib/ws/org.svelte';
  import { msg } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  const oid = $derived(page.params.oid ?? '');
  const manage = $derived(ocan('org.groups.manage'));
  let sel = $state('');
  const g = $derived(org.groups.find((x) => x.id === sel) ?? null);
  const rows = $derived.by(() => {
    const out: any[] = []; const byParent = (p: string | null) => org.groups.filter((x) => (x.parent_id && org.groups.some((y) => y.id === x.parent_id) ? x.parent_id : null) === p).sort((a, b) => a.name.localeCompare(b.name));
    const walk = (p: string | null, d: number) => { for (const x of byParent(p)) { out.push({ ...x, depth: d }); walk(x.id, d + 1); } }; walk(null, 0); return out;
  });
  let dlg = $state(false), name = $state(''), kind = $state('department'), parent = $state(''), formErr = $state(''), busy = $state(false);
  async function create() {
    busy = true; formErr = '';
    try { const x = await post(`/api/orgs/${oid}/groups`, { name, kind, parent_id: parent || null }); dlg = false; name = parent = ''; toast('Group created'); await reloadOrg(); sel = x.id; } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  let add = $state('');
  const candidates = $derived(org.members.filter((m) => m.status === 'active' && !g?.members.some((x: any) => x.user_id === m.user_id)));
  const setMember = (uid: string, present: boolean, is_admin = false) => put(`/api/orgs/${oid}/groups/${sel}/members`, { user_id: uid, present, is_admin }).then(() => { add = ''; return reloadOrg(); }).catch((e) => toast(msg(e), 'bad'));
</script>

<div class="cx">
  <aside class="side" aria-label="Groups">
    {#if manage}<button class="btn primary" style="width:100%" onclick={() => { formErr = ''; dlg = true; }}><Icon name="plus" size={16} /> New group</button>{/if}
    {#if !rows.length}<p class="muted small">No groups yet. Use groups for departments such as Marketing or Finance, then give a whole group access to a workspace in one step.</p>{/if}
    <ul>{#each rows as x (x.id)}<li><button class="ci" class:on={x.id === sel} style="padding-left:{10 + x.depth * 18}px" aria-current={x.id === sel ? 'true' : undefined} onclick={() => (sel = x.id)}>
      <span class="n">{x.name}</span><span class="tiny muted">{x.kind} · {x.members.length} {x.members.length === 1 ? 'person' : 'people'}</span></button></li>{/each}</ul>
  </aside>
  <section aria-label="Group members">
    {#if !g}<div class="panel"><div class="panel-b"><p class="muted">Choose a group.</p></div></div>
    {:else}
      <h2 style="margin-bottom:8px">{g.name} <span class="chip">{g.kind}</span></h2>
      <div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Members of {g.name}</caption>
        <thead><tr><th>Name</th><th>Manager</th><th></th></tr></thead>
        <tbody>{#each g.members as m (m.user_id)}<tr><td>{pname(m.user_id)}</td>
          <td>{#if ocan('org.groups.manage')}<label class="row"><input type="checkbox" checked={m.is_admin} onchange={(e) => setMember(m.user_id, true, (e.currentTarget as HTMLInputElement).checked)} aria-label="{pname(m.user_id)} manages this group" /></label>{:else}{m.is_admin ? 'Manager' : ''}{/if}</td>
          <td style="text-align:right"><button class="btn sm ghost" onclick={() => setMember(m.user_id, false)} aria-label="Remove {pname(m.user_id)}">Remove</button></td></tr>
        {:else}<tr><td colspan="3" class="muted">Nobody yet.</td></tr>{/each}</tbody></table></div>
      <form class="row" style="margin-top:12px" onsubmit={(e) => { e.preventDefault(); if (add) setMember(add, true); }}>
        <label class="sr-only" for="am">Add a person</label><select id="am" class="input" style="max-width:320px" bind:value={add}><option value="">Add a person…</option>{#each candidates as c}<option value={c.user_id}>{c.display_name}</option>{/each}</select>
        <button class="btn" disabled={!add}>Add</button></form>
      <p class="tiny muted" style="margin-top:8px">Managers can add and remove people in this group and its sub-groups. Only organisation administrators can appoint a manager.</p>
    {/if}
  </section>
</div>
<Modal bind:open={dlg} title="New group">
  <form id="gf" onsubmit={(e) => { e.preventDefault(); create(); }}>
    <div class="field"><label for="gn">Name</label><input id="gn" class="input" bind:value={name} maxlength="120" required /></div>
    <div class="field"><label for="gk">Kind</label><select id="gk" class="input" bind:value={kind}><option value="department">Department</option><option value="team">Team</option><option value="group">Group</option></select></div>
    <div class="field"><label for="gp">Inside</label><select id="gp" class="input" bind:value={parent}><option value="">Nothing, a top-level group</option>{#each rows as x}<option value={x.id}>{'— '.repeat(x.depth)}{x.name}</option>{/each}</select></div>
    {#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
  </form>
  {#snippet footer()}<button class="btn" onclick={() => (dlg = false)}>Cancel</button><button class="btn primary" form="gf" type="submit" disabled={busy || !name.trim()}>Create group</button>{/snippet}
</Modal>
<style>
  .cx { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 24px; align-items: start; } .side { display: grid; gap: 8px; } .side ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
  .ci { all: unset; cursor: pointer; display: grid; gap: 2px; padding: 8px 10px; border-radius: var(--radius-sm); box-sizing: border-box; width: 100%; } .ci:hover { background: var(--hover); }
  .ci.on { background: var(--selected); box-shadow: inset 3px 0 0 var(--accent); } .ci:focus-visible { outline: 2px solid var(--accent); } .ci .n { font-weight: 600; }
  @media (max-width: 860px) { .cx { grid-template-columns: minmax(0, 1fr); } }
</style>
