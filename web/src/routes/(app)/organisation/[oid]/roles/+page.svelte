<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // Roles are named sets of permissions. Edit the shipped ones or make your own. "Content" permissions (reading and
  // changing files, contexts, memory) can only be granted by name: no catch-all ever includes them.
  import { page } from '$app/state';
  import { post, put, del } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { org, ocan, reloadOrg } from '$lib/ws/org.svelte';
  import { msg, PERM_GROUPS } from '$lib/ws/util';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  const oid = $derived(page.params.oid ?? '');
  const manage = $derived(ocan('org.roles.manage'));
  let dlg = $state(false), isNew = $state(false), role = $state<any>(null), label = $state(''), key = $state(''), scope = $state('workspace'), desc = $state('');
  let st = $state<Record<string, 'grant' | 'deny' | ''>>({}), extraG = $state<string[]>([]), extraD = $state<string[]>([]), formErr = $state(''), busy = $state(false);
  const ids = $derived(new Set(org.perms.map((p) => p.id)));
  const shown = $derived(org.perms.filter((p) => (scope === 'org' ? p.id.startsWith('org.') : !p.id.startsWith('org.'))));
  const grouped = $derived(PERM_GROUPS.map((g) => [g, shown.filter((p) => p.group === g)] as [string, any[]]).filter(([, l]) => l.length));
  function open(r: any | null) {
    isNew = !r; role = r; formErr = ''; label = r?.label ?? ''; key = ''; scope = r?.scope ?? 'workspace'; desc = r?.description ?? '';
    st = {}; for (const p of org.perms) st[p.id] = r?.grants.includes(p.id) ? 'grant' : r?.denies.includes(p.id) ? 'deny' : '';
    extraG = (r?.grants ?? []).filter((x: string) => !ids.has(x)); extraD = (r?.denies ?? []).filter((x: string) => !ids.has(x)); dlg = true;
  }
  async function save() {
    busy = true; formErr = '';
    const grants = [...Object.keys(st).filter((k) => st[k] === 'grant'), ...extraG], denies = [...Object.keys(st).filter((k) => st[k] === 'deny'), ...extraD];
    try {
      if (isNew) await post(`/api/orgs/${oid}/roles`, { key: key || undefined, scope, label, description: desc, grants, denies });
      else await put(`/api/orgs/${oid}/roles/${role.id}`, { label, description: desc, grants, denies });
      dlg = false; toast('Role saved'); await reloadOrg();
    } catch (e) { formErr = msg(e); } finally { busy = false; }
  }
  const remove = (r: any) => del(`/api/orgs/${oid}/roles/${r.id}`).then(() => { toast('Role deleted'); return reloadOrg(); }).catch((e) => toast(msg(e), 'bad'));
  const count = (r: any) => r.grants.length;
</script>

<div class="row" style="margin-bottom:12px"><h2 class="grow">Roles</h2>{#if manage}<button class="btn primary" onclick={() => open(null)}><Icon name="plus" size={16} /> New role</button>{/if}</div>
{#each [['org', 'Organisation roles', 'What someone can do for the organisation itself: people, groups, policy.'], ['workspace', 'Workspace roles', 'What someone can do inside a workspace they belong to: files, contexts, agents.']] as [sc, title, lead]}
  <section style="margin-bottom:24px"><h3>{title}</h3><p class="small muted" style="margin:2px 0 8px">{lead}</p>
    <div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">{title}</caption><thead><tr><th>Role</th><th>What it is for</th><th>Permissions</th><th></th></tr></thead>
      <tbody>{#each org.roles.filter((r) => r.scope === sc) as r (r.id)}<tr><td><strong>{r.label}</strong> {#if r.system}<span class="chip">Shipped</span>{/if}</td><td class="wrap small muted">{r.description}</td>
        <td class="num">{count(r)}{#if r.denies.length} <span class="chip bad">{r.denies.length} denied</span>{/if}</td>
        <td style="text-align:right"><button class="btn sm ghost" onclick={() => open(r)}>{manage ? 'Edit' : 'View'}</button>{#if manage && !r.system}<button class="btn sm ghost" onclick={() => remove(r)}>Delete</button>{/if}</td></tr>{/each}</tbody></table></div></section>
{/each}

<Modal bind:open={dlg} title={isNew ? 'New role' : `${manage ? 'Edit' : 'View'} role: ${role?.label ?? ''}`} wide>
  <form id="rf" onsubmit={(e) => { e.preventDefault(); save(); }}>
    <div class="grid2">
      <div class="field"><label for="rl">Name</label><input id="rl" class="input" bind:value={label} maxlength="80" required disabled={!manage} /></div>
      {#if isNew}<div class="field"><label for="rs">Used for</label><select id="rs" class="input" bind:value={scope}><option value="workspace">Inside workspaces</option><option value="org">The organisation itself</option></select></div>{/if}
    </div>
    <div class="field"><label for="rd">Description</label><input id="rd" class="input" bind:value={desc} disabled={!manage} /></div>
    <table class="t mx"><caption class="sr-only">Permissions</caption><thead><tr><th>Permission</th><th class="c">Not set</th><th class="c">Grant</th><th class="c">Deny</th></tr></thead>
      {#each grouped as [g, list]}
        <tbody><tr class="gr"><th colspan="4">{g}</th></tr>
          {#each list as p (p.id)}<tr><td class="wrap">{p.label}{#if p.sensitive} <span class="chip" title="Granted only by name; no catch-all includes it">content</span>{/if}<div class="tiny muted mono">{p.id}</div></td>
            {#each [['', 'Not set'], ['grant', 'Grant'], ['deny', 'Deny']] as [v, l]}<td class="c"><input type="radio" name="p-{p.id}" value={v} bind:group={st[p.id]} disabled={!manage} aria-label="{l}: {p.label}" /></td>{/each}</tr>{/each}</tbody>
      {/each}
    </table>
    {#if extraG.length || extraD.length}<p class="small muted" style="margin-top:8px">Also in this role (set through the API, kept as is): {[...extraG, ...extraD.map((x) => 'deny ' + x)].join(', ')}</p>{/if}
    <p class="tiny muted" style="margin-top:8px">A deny always wins over a grant, wherever it comes from. Changes apply to everyone holding the role immediately.</p>
    {#if formErr}<div class="err" role="alert">{formErr}</div>{/if}
  </form>
  {#snippet footer()}<button class="btn" onclick={() => (dlg = false)}>{manage ? 'Cancel' : 'Close'}</button>{#if manage}<button class="btn primary" form="rf" type="submit" disabled={busy || !label.trim()}>Save role</button>{/if}{/snippet}
</Modal>
<style>
  .grid2 { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; } table.mx { width: 100%; } table.mx .c { text-align: center; width: 84px; } table.mx tr.gr th { background: var(--surface-2); position: static; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; }
  table.mx td { height: auto; padding-top: 6px; padding-bottom: 6px; } @media (max-width: 700px) { .grid2 { grid-template-columns: minmax(0, 1fr); } }
</style>
