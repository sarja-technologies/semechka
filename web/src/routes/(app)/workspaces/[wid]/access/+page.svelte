<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Who can do what here. Roles are given to people and groups; explicit rules can allow or deny one thing for one
  // person or group, and a deny always wins. Nothing here is cosmetic: the server enforces every row.
  import { page } from '$app/state';
  import { get, put, post, del, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can, person, groupName, roleLabel, refreshWorkspace } from '$lib/ws/current.svelte';
  import { msg, PERM_GROUPS } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import State from '$lib/components/admin/State.svelte';

  const wid = $derived(page.params.wid ?? '');
  let members = $state<any[]>([]), rules = $state<any[]>([]), loading = $state(true), error = $state('');
  const wsRoles = $derived(cur.roles.filter((r) => r.scope === 'workspace'));
  async function load() {
    try { [members, rules] = await Promise.all([get(`/api/workspaces/${wid}/members`), get(`/api/workspaces/${wid}/acl`)]); error = ''; }
    catch (e) { error = msg(e); } finally { loading = false; }
  }
  $effect(() => { wid; load(); });
  const name = (m: any) => (m.principal_type === 'group' ? groupName(m.principal_id) : person(m.principal_id));

  async function setRole(m: any, role: string) {
    try { await put(`/api/workspaces/${wid}/members`, { principal_type: m.principal_type, principal_id: m.principal_id, role }); toast('Role changed'); await load(); await refreshWorkspace(); }
    catch (e) { toast(msg(e), 'bad'); await load(); }
  }
  async function remove(m: any) {
    try { await del(`/api/workspaces/${wid}/members/${m.principal_type}/${m.principal_id}`); toast('Removed'); await load(); await refreshWorkspace(); } catch (e) { toast(msg(e), 'bad'); }
  }

  // ---- add a member ----
  let dlg = $state(false), mode = $state<'member' | 'rule'>('member'), busy = $state(false), formErr = $state('');
  let aType = $state('user'), aId = $state(''), aRole = $state('viewer');
  const taken = $derived(new Set(members.map((m) => `${m.principal_type}:${m.principal_id}`)));
  const candidates = $derived(aType === 'user' ? cur.members.filter((m) => m.status === 'active' && !taken.has(`user:${m.user_id}`)).map((m) => ({ id: m.user_id, name: m.display_name })) :
    cur.groups.filter((g) => !taken.has(`group:${g.id}`)).map((g) => ({ id: g.id, name: g.name })));
  function openAdd() { mode = 'member'; aType = 'user'; aId = ''; aRole = 'viewer'; formErr = ''; dlg = true; }
  async function add() {
    busy = true; formErr = '';
    try { await put(`/api/workspaces/${wid}/members`, { principal_type: aType, principal_id: aId, role: aRole }); dlg = false; toast('Added'); await load(); await refreshWorkspace(); }
    catch (e) { formErr = msg(e); } finally { busy = false; }
  }

  // ---- rules ----
  let rType = $state('workspace'), rRes = $state(''), rPType = $state('user'), rPId = $state(''), rEffect = $state('allow'), rPerms = $state<string[]>([]);
  let files = $state<any[]>([]), ctxs = $state<any[]>([]);
  async function openRule() {
    mode = 'rule'; rType = 'workspace'; rRes = ''; rPType = 'user'; rPId = ''; rEffect = 'allow'; rPerms = []; formErr = ''; dlg = true;
    [files, ctxs] = await Promise.all([get(`/api/workspaces/${wid}/all-files`).catch(() => []), get(`/api/workspaces/${wid}/contexts`).catch(() => [])]);
  }
  const rCands = $derived(rPType === 'user' ? cur.members.filter((m) => m.status === 'active').map((m) => ({ id: m.user_id, name: m.display_name })) : cur.groups.map((g) => ({ id: g.id, name: g.name })));
  const resName = (r: any) => (r.resource_type === 'workspace' ? 'The whole workspace' : r.resource_type === 'file' ? (files.find((f) => f.id === r.resource_id)?.name ?? 'A file') : r.resource_type === 'context' ? (ctxs.find((c) => c.id === r.resource_id)?.name ?? 'A context') : `A ${r.resource_type}`);
  $effect(() => { if (rules.some((r) => r.resource_type === 'file') && !files.length) get(`/api/workspaces/${wid}/all-files`).then((f) => (files = f)).catch(() => {}); });
  $effect(() => { if (rules.some((r) => r.resource_type === 'context') && !ctxs.length) get(`/api/workspaces/${wid}/contexts`).then((c) => (ctxs = c)).catch(() => {}); });
  async function addRule() {
    busy = true; formErr = '';
    try {
      await post(`/api/workspaces/${wid}/acl`, { resource_type: rType, resource_id: rType === 'workspace' ? wid : rRes, principal_type: rPType, principal_id: rPId, effect: rEffect, perms: rPerms });
      dlg = false; toast('Rule added'); await load();
    } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const dropRule = (r: any) => del(`/api/workspaces/${wid}/acl/${r.id}`).then(() => { toast('Rule removed'); load(); }).catch((e) => toast(msg(e), 'bad'));
  const grouped = $derived(PERM_GROUPS.map((g) => [g, cur.perms.filter((p) => p.group === g && !p.id.startsWith('org.'))] as [string, any[]]).filter(([, l]) => l.length));
  const permLabel = (id: string) => cur.perms.find((p) => p.id === id)?.label ?? id;

  // ---- explain ----
  let who = $state(''), perm = $state('file.read'), why = $state<any>(null), whyErr = $state('');
  async function explain() {
    whyErr = ''; why = null;
    try { why = await get(`/api/workspaces/${wid}/explain${qs({ perm, user_id: who })}`); } catch (e) { whyErr = msg(e); }
  }
</script>

<State {loading} {error} retry={load} />
{#if !loading && !error}
  <section aria-label="Members">
    <div class="row" style="margin-bottom:8px"><h2 class="grow">Who has access</h2>{#if can('ws.members.manage') && !cur.ws.archived}<button class="btn primary" onclick={openAdd}><Icon name="plus" size={16} /> Add person or group</button>{/if}</div>
    {#if cur.ws.parent_id && cur.ws.inherit_members}<div class="notice info"><div class="small">This workspace sits inside another one. People in the parent workspace are also members here.</div></div>{/if}
    <div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Members of this workspace</caption>
      <thead><tr><th>Name</th><th>Type</th><th>Role</th><th></th></tr></thead>
      <tbody>{#each members as m (m.principal_type + m.principal_id)}<tr>
        <td>{name(m)}</td><td><span class="chip">{m.principal_type === 'group' ? 'Group' : 'Person'}</span></td>
        <td><label class="sr-only" for="r-{m.principal_id}">Role of {name(m)}</label>
          <select id="r-{m.principal_id}" class="input" style="max-width:220px" value={m.role_id} disabled={cur.ws.archived} onchange={(e) => setRole(m, (e.currentTarget as HTMLSelectElement).value)}>
            {#each wsRoles as r}<option value={r.id}>{r.label}</option>{/each}{#if !wsRoles.find((r) => r.id === m.role_id)}<option value={m.role_id}>{roleLabel(m.role_id)}</option>{/if}</select></td>
        <td style="text-align:right"><button class="btn sm ghost" onclick={() => remove(m)} aria-label="Remove {name(m)}">Remove</button></td></tr>{/each}</tbody></table></div>
    <p class="tiny muted" style="margin-top:8px">A workspace always keeps at least one administrator. Organisation owners and administrators manage the organisation, but they only see inside this workspace if they are listed here.</p>
  </section>

  <section aria-label="Access rules" style="margin-top:28px">
    <div class="row" style="margin-bottom:8px"><h2 class="grow">Specific rules</h2>{#if can('ws.members.manage') && !cur.ws.archived}<button class="btn" onclick={openRule}><Icon name="plus" size={16} /> Add rule</button>{/if}</div>
    <p class="small muted" style="margin:0 0 8px">Rules allow or deny particular actions for one person or group, on the whole workspace or one file or context. A <strong>deny</strong> always beats an allow. Restricting a folder, file or context is done from the Files and Contexts tabs.</p>
    {#if !rules.length}<p class="muted">No specific rules. Roles decide everything.</p>
    {:else}<div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Access rules</caption>
      <thead><tr><th>Applies to</th><th>Who</th><th>Rule</th><th>Actions</th><th></th></tr></thead>
      <tbody>{#each rules as r (r.id)}<tr><td>{resName(r)}</td><td>{r.principal_type === 'group' ? groupName(r.principal_id) : person(r.principal_id)}</td>
        <td><span class="chip" class:ok={r.effect === 'allow'} class:bad={r.effect === 'deny'}>{r.effect === 'allow' ? 'Allow' : 'Deny'}</span></td>
        <td class="wrap small">{r.perms.map(permLabel).join(', ')}</td>
        <td style="text-align:right">{#if can('ws.members.manage')}<button class="btn sm ghost" onclick={() => dropRule(r)}>Remove</button>{/if}</td></tr>{/each}</tbody></table></div>{/if}
  </section>

  {#if can('ws.members.manage')}
    <section class="panel" style="margin-top:28px" aria-label="Explain access">
      <div class="panel-h"><h3>Why can or can't someone do this?</h3></div>
      <div class="panel-b">
        <form class="row" onsubmit={(e) => { e.preventDefault(); explain(); }}>
          <div class="field" style="margin:0"><label for="ew">Person</label><select id="ew" class="input" bind:value={who}><option value="">Me</option>{#each cur.members as m}<option value={m.user_id}>{m.display_name}</option>{/each}</select></div>
          <div class="field" style="margin:0"><label for="ep">Action</label><select id="ep" class="input" bind:value={perm}>{#each grouped as [g, list]}<optgroup label={g}>{#each list as p}<option value={p.id}>{p.label}</option>{/each}</optgroup>{/each}</select></div>
          <button class="btn" style="align-self:flex-end">Explain</button>
        </form>
        {#if whyErr}<div class="err" role="alert" style="margin-top:8px">{whyErr}</div>{/if}
        {#if why}<div class="notice {why.allowed ? 'ok' : 'warn'}" style="margin-top:12px" role="status"><div><strong>{why.allowed ? 'Allowed.' : 'Not allowed.'}</strong> {why.reason}{#if why.via?.length}<div class="small muted">Through: {why.via.join(', ')}</div>{/if}</div></div>{/if}
      </div>
    </section>
  {/if}
{/if}

<Modal bind:open={dlg} title={mode === 'member' ? 'Add to this workspace' : 'Add a rule'} wide={mode === 'rule'}>
  {#if mode === 'member'}
    <form id="mf" onsubmit={(e) => { e.preventDefault(); add(); }}>
      <div class="field"><label for="at">Add a</label><select id="at" class="input" bind:value={aType} onchange={() => (aId = '')}><option value="user">Person</option><option value="group">Group or department</option></select></div>
      <div class="field"><label for="ai">Who</label><select id="ai" class="input" bind:value={aId} required><option value="">Choose…</option>{#each candidates as c}<option value={c.id}>{c.name}</option>{/each}</select>
        {#if !candidates.length}<span class="hint">{aType === 'user' ? 'Everyone in the organisation is already here.' : 'There are no groups to add.'}</span>{/if}</div>
      <div class="field"><label for="ar">Role</label><select id="ar" class="input" bind:value={aRole}>{#each wsRoles as r}<option value={r.key}>{r.label}</option>{/each}</select>
        <span class="hint">{wsRoles.find((r) => r.key === aRole)?.description}</span></div>
    </form>
  {:else}
    <form id="mf" onsubmit={(e) => { e.preventDefault(); addRule(); }}>
      <div class="grid2">
        <div class="field"><label for="rt">Applies to</label><select id="rt" class="input" bind:value={rType} onchange={() => (rRes = '')}><option value="workspace">The whole workspace</option><option value="file">One file</option><option value="context">One context</option></select></div>
        {#if rType === 'file'}<div class="field"><label for="rf">File</label><select id="rf" class="input" bind:value={rRes} required><option value="">Choose…</option>{#each files as f}<option value={f.id}>{f.path ? f.path + ' / ' : ''}{f.name}</option>{/each}</select></div>
        {:else if rType === 'context'}<div class="field"><label for="rc">Context</label><select id="rc" class="input" bind:value={rRes} required><option value="">Choose…</option>{#each ctxs as c}<option value={c.id}>{c.name}</option>{/each}</select></div>{/if}
        <div class="field"><label for="pt">For a</label><select id="pt" class="input" bind:value={rPType} onchange={() => (rPId = '')}><option value="user">Person</option><option value="group">Group</option></select></div>
        <div class="field"><label for="pi">Who</label><select id="pi" class="input" bind:value={rPId} required><option value="">Choose…</option>{#each rCands as c}<option value={c.id}>{c.name}</option>{/each}</select></div>
      </div>
      <fieldset class="field" style="border:0;padding:0"><legend class="label">Effect</legend>
        <label class="row"><input type="radio" bind:group={rEffect} value="allow" /><span>Allow these actions</span></label>
        <label class="row"><input type="radio" bind:group={rEffect} value="deny" /><span>Deny these actions, whatever their role says</span></label></fieldset>
      <fieldset class="field" style="border:0;padding:0"><legend class="label">Actions</legend>
        {#each grouped as [g, list]}<h4 class="gh">{g}</h4>{#each list as p}<label class="row"><input type="checkbox" value={p.id} bind:group={rPerms} /><span>{p.label}{#if p.sensitive} <span class="chip" title="Content permissions are never granted by a wildcard">content</span>{/if}</span></label>{/each}{/each}</fieldset>
    </form>
  {/if}
  {#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (dlg = false)}>Cancel</button>
    <button class="btn primary" form="mf" type="submit" disabled={busy || (mode === 'member' ? !aId : !rPId || !rPerms.length || (rType !== 'workspace' && !rRes))}>{mode === 'member' ? 'Add' : 'Add rule'}</button>{/snippet}
</Modal>

<style>
  .grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; } .gh { margin: 10px 0 2px; color: var(--fg-muted); font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  @media (max-width: 700px) { .grid2 { grid-template-columns: minmax(0, 1fr); } }
</style>
