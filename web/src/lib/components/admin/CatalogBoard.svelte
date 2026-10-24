<script lang="ts">
  // The model chooser as people will see it: the order of every model, the groups (headings) above them, pins,
  // hidden entries, each model's profile and icon, and who may use it. Order and grouping are saved on the server.
  // Groups and order are presentation only; access comes from the rules in each model's Access section.
  import { onMount } from 'svelte';
  import { get, post, patch, put, del, upload } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ModelIcon from '$lib/components/chat/ModelIcon.svelte';
  import { can } from '$lib/session.svelte';
  import State from './State.svelte';
  import GrantsEditor from './GrantsEditor.svelte';
  import { errMsg } from './util';
  import { loadRoles, loadTeams, loadUsers, type Opt, type Rule } from './lookup';

  let cat = $state<any>(null), loading = $state(true), error = $state(''), q = $state(''), live = $state('');
  let roles = $state<Opt[]>([]), teams = $state<Opt[]>([]), users = $state<Opt[]>([]), backends = $state<any[]>([]);
  async function load() {
    try { cat = await get('/api/admin/catalog'); error = ''; } catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(() => {
    load();
    if (can('admin.users.view')) { // the lists of roles, teams and people need admin.users.view
      loadRoles().then((r) => (roles = r)).catch(() => {}); loadTeams().then((r) => (teams = r)).catch(() => {});
      loadUsers().then((u) => (users = u)).catch(() => {});
    } get('/api/admin/backends').then((b) => (backends = b)).catch(() => {});
  });
  const byId = $derived(new Map<string, any>((cat?.models ?? []).map((m: any) => [m.id, m])));
  const shown = $derived((cat?.models ?? []).filter((m: any) => !q || `${m.alias} ${m.profile.provider_label ?? ''} ${m.profile.family ?? ''}`.toLowerCase().includes(q.toLowerCase())));
  const line = (p: any) => [p.provider_label, p.family, p.parameters, p.quantization, p.context_length ? `${Math.round(p.context_length / 1024)}k context` : null].filter(Boolean).join(' · ');
  const access = (m: any) => {
    if (!m.enabled) return { t: 'Off', c: '' };
    if (!m.grants.length) return { t: 'Nobody', c: 'warn' };
    if (m.grants.some((g: any) => g.subject_type === 'everyone' && g.effect === 'allow')) return { t: 'Everyone', c: 'ok' };
    return { t: `${m.grants.filter((g: any) => g.effect === 'allow').length} allow, ${m.grants.filter((g: any) => g.effect === 'deny').length} deny`, c: 'ok' };
  };

  // ---- ordering: drag and drop, or the move buttons (keyboard) -------------------------------------------------
  type Drag = { kind: 'model' | 'member' | 'group'; id: string; gid?: string } | null;
  let drag = $state<Drag>(null), over = $state<{ list: string; id: string | null; after: boolean } | null>(null);
  const moved = <T,>(a: T[], from: number, to: number) => { const b = [...a]; const [x] = b.splice(from, 1); b.splice(to, 0, x); return b; };
  async function saveOrder(ids: string[], what: string) {
    const prev = cat.models; cat.models = ids.map((i) => byId.get(i));
    try { await put('/api/admin/catalog/order', { ids }); live = what; } catch (e) { cat.models = prev; toast(errMsg(e), 'bad'); }
  }
  async function saveMembers(gid: string, ids: string[], what: string) {
    const g = cat.groups.find((x: any) => x.id === gid); const prev = g.members; g.members = ids;
    try { await put(`/api/admin/catalog/groups/${gid}/members`, { ids }); live = what; await load(); } catch (e) { g.members = prev; toast(errMsg(e), 'bad'); }
  }
  async function saveGroups(ids: string[], what: string) {
    const prev = cat.groups; cat.groups = ids.map((i) => prev.find((g: any) => g.id === i));
    try { await put('/api/admin/catalog/groups-order', { ids }); live = what; } catch (e) { cat.groups = prev; toast(errMsg(e), 'bad'); }
  }
  function step(list: 'all' | 'group' | 'groups', id: string, d: -1 | 1, gid?: string) {
    if (list === 'all') { const ids = cat.models.map((m: any) => m.id); const i = ids.indexOf(id), j = i + d; if (j < 0 || j >= ids.length) return; saveOrder(moved(ids, i, j), `${byId.get(id).alias} moved to position ${j + 1} of ${ids.length}.`); }
    else if (list === 'group') { const g = cat.groups.find((x: any) => x.id === gid); const i = g.members.indexOf(id), j = i + d; if (j < 0 || j >= g.members.length) return; saveMembers(gid!, moved(g.members, i, j), `${byId.get(id).alias} moved to position ${j + 1} in ${g.name}.`); }
    else { const ids = cat.groups.map((g: any) => g.id); const i = ids.indexOf(id), j = i + d; if (j < 0 || j >= ids.length) return; saveGroups(moved(ids, i, j), `Group moved to position ${j + 1}.`); }
  }
  function overRow(e: DragEvent, list: string, id: string | null) {
    if (!drag) return;
    const ok = (drag.kind === 'group') === (list === 'groups') && !(list === 'all' && drag.kind !== 'model');
    if (!ok) return;
    e.preventDefault();
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    over = { list, id, after: e.clientY > r.top + r.height / 2 };
  }
  function dropOn(list: string) {
    const d = drag, o = over; drag = null; over = null;
    if (!d || !o || o.list !== list) return;
    if (list === 'all') {
      const ids = cat.models.map((m: any) => m.id).filter((x: string) => x !== d.id);
      let j = o.id ? ids.indexOf(o.id) + (o.after ? 1 : 0) : ids.length;
      ids.splice(j, 0, d.id); saveOrder(ids, `${byId.get(d.id).alias} moved to position ${j + 1}.`);
    } else if (list === 'groups') {
      const ids = cat.groups.map((g: any) => g.id).filter((x: string) => x !== d.id);
      const j = o.id ? ids.indexOf(o.id) + (o.after ? 1 : 0) : ids.length; ids.splice(j, 0, d.id); saveGroups(ids, 'Group moved.');
    } else {
      const gid = list.slice(2); const g = cat.groups.find((x: any) => x.id === gid);
      const ids = g.members.filter((x: string) => x !== d.id);
      const j = o.id ? ids.indexOf(o.id) + (o.after ? 1 : 0) : ids.length; ids.splice(j, 0, d.id);
      saveMembers(gid, ids, d.gid === gid ? `${byId.get(d.id).alias} moved in ${g.name}.` : `${byId.get(d.id).alias} added to ${g.name}.`);
    }
  }
  const end = () => { drag = null; over = null; };
  const mark = (list: string, id: string | null) => (over && over.list === list && over.id === id ? (over.after ? 'after' : 'before') : '');

  // ---- quick toggles and bulk ---------------------------------------------------------------------------------
  async function flip(m: any, k: 'pinned' | 'hidden') {
    const v = !m[k];
    try { await patch(`/api/admin/catalog/models/${m.id}`, { [k]: v }); m[k] = v; live = `${m.alias} ${k === 'pinned' ? (v ? 'pinned' : 'unpinned') : v ? 'hidden from the chooser' : 'shown in the chooser'}.`; }
    catch (e) { toast(errMsg(e), 'bad'); }
  }
  let picked = $state<Set<string>>(new Set()), bulkGroup = $state('');
  const pick = (id: string) => { const n = new Set(picked); n.has(id) ? n.delete(id) : n.add(id); picked = n; };
  async function bulk(action: string) {
    try {
      const r = await post('/api/admin/catalog/bulk', { action, ids: [...picked], ...(action.endsWith('group') ? { group_id: bulkGroup } : {}) });
      toast(`Done for ${r.done} model${r.done === 1 ? '' : 's'}.`); picked = new Set(); await load();
    } catch (e) { toast(errMsg(e), 'bad'); }
  }

  // ---- groups ---------------------------------------------------------------------------------------------------
  let gOpen = $state(false), gEdit = $state<any>(null), gErr = $state(''), aud = $state({ type: 'role', id: '' });
  function editGroup(g?: any) { gEdit = g ? { ...g, audience: [...(g.audience ?? [])] } : { name: '', description: '', collapsed: false, audience: [] }; gErr = ''; gOpen = true; }
  async function saveGroup() {
    gErr = '';
    const body = { name: gEdit.name, description: gEdit.description, collapsed: gEdit.collapsed, audience: gEdit.audience };
    try { if (gEdit.id) await patch(`/api/admin/catalog/groups/${gEdit.id}`, body); else await post('/api/admin/catalog/groups', body); gOpen = false; toast('Group saved.'); await load(); }
    catch (e) { gErr = errMsg(e); }
  }
  let gDel = $state<any>(null);
  async function deleteGroup() { const g = gDel; gDel = null; try { await del(`/api/admin/catalog/groups/${g.id}`); toast('Group removed. Its models are still in the catalogue.'); await load(); } catch (e) { toast(errMsg(e), 'bad'); } }
  const audName = (a: any) => ((a.type === 'role' ? roles : a.type === 'team' ? teams : users).find((o) => o.id === a.id)?.label ?? a.id);

  // ---- one model: profile, icon, access ------------------------------------------------------------------------
  const FIELDS: [string, string, string][] = [['family', 'Family', 'e.g. Qwen3'], ['parameters', 'Parameters', 'e.g. 32B or 30B-A3B'], ['quantization', 'Quantisation', 'e.g. Q4_K_M or FP8']];
  const SRC: Record<string, string> = { admin: 'set by you', backend: 'reported by the backend', name: 'read from the model name' };
  const CAPS = ['chat', 'vision', 'tools', 'image', 'video', 'embeddings'];
  let mOpen = $state(false), m = $state<any>(null), f = $state<any>({}), rules = $state<Rule[]>([]), enabled = $state(false), mErr = $state(''), busy = $state(false);
  function editModel(x: any) {
    m = x; const a = x.admin_profile ?? {};
    f = { alias: x.alias, provider: a.provider ?? '', family: a.family ?? '', parameters: a.parameters ?? '', quantization: a.quantization ?? '', context_length: a.context_length ?? '',
          description: a.description ?? '', modality: a.modality ?? '', icon_asset: a.icon_asset ?? '', capabilities: a.capabilities ?? null, ctx: x.defaults?.ctx ?? '' };
    rules = x.grants.map((g: any) => ({ ...g, subject_id: g.subject_type === 'everyone' ? '*' : g.subject_id })); enabled = x.enabled; mErr = ''; mOpen = true;
  }
  const detected = (k: string) => (m?.profile?._source?.[k] && m.profile._source[k] !== 'admin' ? m.profile[k] : '');
  async function iconUp(e: Event) {
    const inp = e.currentTarget as HTMLInputElement; const file = inp.files?.[0]; inp.value = ''; if (!file) return;
    try { f.icon_asset = (await upload('/api/admin/catalog/icons', file)).id; } catch (x) { mErr = errMsg(x); }
  }
  async function saveModel() {
    mErr = ''; busy = true;
    try {
      const profile: any = {};
      for (const k of ['provider', 'family', 'parameters', 'quantization', 'description', 'modality', 'icon_asset']) if (f[k]) profile[k] = f[k];
      if (f.context_length !== '' && f.context_length !== null) profile.context_length = Number(f.context_length);
      if (f.capabilities) profile.capabilities = f.capabilities;
      await patch(`/api/admin/catalog/models/${m.id}`, { alias: f.alias, profile, defaults: { ctx: f.ctx === '' || f.ctx === null ? null : Number(f.ctx) } });
      if (enabled !== m.enabled) await patch(`/api/admin/models/${m.id}`, { enabled });
      await put(`/api/admin/models/${m.id}/grants`, { grants: rules.map((r) => ({ ...r, subject_id: r.subject_type === 'everyone' ? '' : r.subject_id })) });
      mOpen = false; toast(`${f.alias} saved.`); await load();
    } catch (x) { mErr = errMsg(x); } finally { busy = false; }
  }

  // ---- provider icons and manual registration --------------------------------------------------------------
  let pOpen = $state(false);
  async function providerIcon(pid: string, e: Event | null) {
    try {
      let icon: string | null = null;
      if (e) { const inp = e.currentTarget as HTMLInputElement; const file = inp.files?.[0]; inp.value = ''; if (!file) return; icon = (await upload('/api/admin/catalog/icons', file)).id; }
      await put(`/api/admin/catalog/providers/${pid}/icon`, { icon }); toast(icon ? 'Icon saved.' : 'Back to the lettermark.'); await load();
    } catch (x) { toast(errMsg(x), 'bad'); }
  }
  let rOpen = $state(false), reg = $state({ backend_id: '', real_id: '', alias: '', capabilities: ['chat'] as string[] }), rErr = $state('');
  async function register() {
    rErr = '';
    try {
      const r = await post('/api/admin/models', { ...reg, alias: reg.alias || undefined });
      rOpen = false; toast('Registered. It is off and nobody can use it until you allow it.'); await load();
      const x = byId.get(r.id); if (x) editModel(x);
    } catch (x) { rErr = errMsg(x); }
  }
  let unreg = $state<any>(null);
  async function unregister() { const x = unreg; unreg = null; try { await del(`/api/admin/models/${x.id}`); mOpen = false; toast('Removed.'); await load(); } catch (e) { toast(errMsg(e), 'bad'); } }
</script>

<State {loading} {error} retry={load} />
{#if cat}
  <div class="sr-only" aria-live="polite">{live}</div>
  <div class="tb row">
    <div style="flex:1 1 220px;max-width:320px"><label class="sr-only" for="cq">Filter models</label><input id="cq" class="input" type="search" placeholder="Filter by name or provider" bind:value={q} /></div>
    <span class="spacer"></span>
    <button class="btn sm" onclick={() => editGroup()}><Icon name="plus" size={14} /> New group</button>
    <button class="btn sm" onclick={() => { reg = { backend_id: backends[0]?.id ?? '', real_id: '', alias: '', capabilities: ['chat'] }; rErr = ''; rOpen = true; }} disabled={!backends.length} title={backends.length ? '' : 'Needs a backend, and permission to see backends'}>Register a model</button>
    <button class="btn sm ghost" onclick={() => (pOpen = true)}>Provider icons</button>
  </div>

  {#if picked.size}
    <div class="notice info bulk" role="region" aria-label="Bulk actions"><div class="grow"><strong>{picked.size}</strong> selected</div>
      <div class="row">
        <button class="btn sm" onclick={() => bulk('pin')}>Pin</button><button class="btn sm" onclick={() => bulk('unpin')}>Unpin</button>
        <button class="btn sm" onclick={() => bulk('hide')}>Hide</button><button class="btn sm" onclick={() => bulk('show')}>Show</button>
        {#if cat.groups.length}
          <label class="sr-only" for="bg">Group</label><select id="bg" class="input" style="max-width:180px;min-height:30px" bind:value={bulkGroup}><option value="">Group…</option>{#each cat.groups as g (g.id)}<option value={g.id}>{g.name}</option>{/each}</select>
          <button class="btn sm" disabled={!bulkGroup} onclick={() => bulk('add_to_group')}>Add to group</button><button class="btn sm" disabled={!bulkGroup} onclick={() => bulk('remove_from_group')}>Remove from group</button>
        {/if}
        <button class="btn ghost sm" onclick={() => (picked = new Set())}>Clear</button>
      </div></div>
  {/if}

  <div class="board">
    <section class="panel">
      <div class="panel-h"><h3>Order in the chooser</h3><span class="hint">{q ? 'Clear the filter to reorder.' : 'Drag, or use the arrows.'} Pinned models are also listed first under Pinned.</span></div>
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <ol class="list" role="list" ondragover={(e) => { if (drag?.kind === 'model' && !over) e.preventDefault(); }} ondrop={(e) => { e.preventDefault(); dropOn('all'); }}>
        {#each shown as x, i (x.id)}
          {@const a = access(x)}
          <li class="mrow {mark('all', x.id)}" class:dim={x.hidden || !x.enabled} class:dragging={drag?.id === x.id && drag?.kind === 'model'}
            draggable={!q} ondragstart={(e) => { drag = { kind: 'model', id: x.id }; e.dataTransfer?.setData('text/plain', x.id); }} ondragend={end}
            ondragover={(e) => overRow(e, 'all', x.id)}>
            <input type="checkbox" aria-label={`Select ${x.alias}`} checked={picked.has(x.id)} onchange={() => pick(x.id)} />
            <span class="grip" aria-hidden="true"><Icon name="grip" size={14} /></span>
            <ModelIcon icon={x.profile.icon} label={x.alias} size={22} />
            <span class="nm"><strong>{x.alias}</strong><span class="tiny muted">{line(x.profile) || 'No details reported'}</span></span>
            <span class="chips">
              {#if !x.available}<span class="chip bad">Missing on backend</span>{/if}
              {#if x.external}<span class="chip warn">External</span>{/if}
              {#if x.groups.length}<span class="chip">{x.groups.length} group{x.groups.length === 1 ? '' : 's'}</span>{/if}
              <span class="chip {a.c}" title="Who may use it">{a.t}</span>
            </span>
            <span class="acts">
              <button class="btn ghost icon sm" class:on={x.pinned} aria-pressed={x.pinned} onclick={() => flip(x, 'pinned')} aria-label={x.pinned ? `Unpin ${x.alias}` : `Pin ${x.alias}`} title={x.pinned ? 'Pinned' : 'Pin'}><Icon name="pin" size={15} /></button>
              <button class="btn ghost icon sm" aria-pressed={x.hidden} onclick={() => flip(x, 'hidden')} aria-label={x.hidden ? `Show ${x.alias} in the chooser` : `Hide ${x.alias} from the chooser`} title={x.hidden ? 'Hidden from the chooser' : 'Shown in the chooser'}><Icon name={x.hidden ? 'eyeoff' : 'eye'} size={15} /></button>
              {#if !q}
                <button class="btn ghost icon sm" onclick={() => step('all', x.id, -1)} disabled={i === 0} aria-label={`Move ${x.alias} up`}><Icon name="up" size={14} /></button>
                <button class="btn ghost icon sm" onclick={() => step('all', x.id, 1)} disabled={i === shown.length - 1} aria-label={`Move ${x.alias} down`}><Icon name="down" size={14} /></button>
              {/if}
              <button class="btn sm" onclick={() => editModel(x)}>Edit</button>
            </span>
          </li>
        {:else}<li class="muted small pad">{q ? 'No model matches.' : 'No models yet. Add a backend and sync it, or register a model.'}</li>{/each}
      </ol>
    </section>

    <section class="groups">
      <div class="gh"><h3>Groups</h3><span class="hint">Headings in the chooser. A model can be in any number of groups; models in none are listed under “Other models”. Drag models from the left onto a group.</span></div>
      <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
      <ol class="glist" role="list" ondrop={(e) => { e.preventDefault(); dropOn('groups'); }}>
        {#each cat.groups as g, gi (g.id)}
          <li class="panel grp {mark('groups', g.id)}" ondragover={(e) => overRow(e, 'groups', g.id)}>
            <div class="panel-h">
              <span class="grip" draggable="true" role="button" tabindex="-1" aria-hidden="true" ondragstart={(e) => { drag = { kind: 'group', id: g.id }; e.dataTransfer?.setData('text/plain', g.id); }} ondragend={end}><Icon name="grip" size={14} /></span>
              <div class="grow"><strong>{g.name}</strong>{#if g.collapsed} <span class="chip">Folded</span>{/if}{#if g.audience.length} <span class="chip" title={g.audience.map(audName).join(', ')}>Shown to {g.audience.length}</span>{/if}
                {#if g.description}<div class="tiny muted">{g.description}</div>{/if}</div>
              <button class="btn ghost icon sm" onclick={() => step('groups', g.id, -1)} disabled={gi === 0} aria-label={`Move group ${g.name} up`}><Icon name="up" size={14} /></button>
              <button class="btn ghost icon sm" onclick={() => step('groups', g.id, 1)} disabled={gi === cat.groups.length - 1} aria-label={`Move group ${g.name} down`}><Icon name="down" size={14} /></button>
              <button class="btn ghost icon sm" onclick={() => editGroup(g)} aria-label={`Edit group ${g.name}`}><Icon name="edit" size={14} /></button>
              <button class="btn ghost icon sm" onclick={() => (gDel = g)} aria-label={`Remove group ${g.name}`}><Icon name="trash" size={14} /></button>
            </div>
            <!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
            <ol class="mem" class:target={drag?.kind === 'model' || (drag?.kind === 'member' && drag.gid === g.id)} role="list"
              ondragover={(e) => { if (drag && drag.kind !== 'group') { e.preventDefault(); if (!over || over.list !== 'g:' + g.id) over = { list: 'g:' + g.id, id: null, after: true }; } }}
              ondrop={(e) => { e.preventDefault(); e.stopPropagation(); dropOn('g:' + g.id); }}>
              {#each g.members as mid, mi (mid)}
                {@const x = byId.get(mid)}
                {#if x}
                  <li class="mrow small {mark('g:' + g.id, mid)}" draggable="true" ondragstart={(e) => { e.stopPropagation(); drag = { kind: 'member', id: mid, gid: g.id }; e.dataTransfer?.setData('text/plain', mid); }} ondragend={end}
                    ondragover={(e) => { e.stopPropagation(); if (drag && drag.kind !== 'group') { e.preventDefault(); const r = (e.currentTarget as HTMLElement).getBoundingClientRect(); over = { list: 'g:' + g.id, id: mid, after: e.clientY > r.top + r.height / 2 }; } }}>
                    <span class="grip" aria-hidden="true"><Icon name="grip" size={13} /></span>
                    <ModelIcon icon={x.profile.icon} label={x.alias} size={18} />
                    <span class="nm">{x.alias}{#if x.hidden} <span class="tiny muted">(hidden)</span>{/if}</span>
                    <button class="btn ghost icon sm" onclick={() => step('group', mid, -1, g.id)} disabled={mi === 0} aria-label={`Move ${x.alias} up in ${g.name}`}><Icon name="up" size={13} /></button>
                    <button class="btn ghost icon sm" onclick={() => step('group', mid, 1, g.id)} disabled={mi === g.members.length - 1} aria-label={`Move ${x.alias} down in ${g.name}`}><Icon name="down" size={13} /></button>
                    <button class="btn ghost icon sm" onclick={() => saveMembers(g.id, g.members.filter((y: string) => y !== mid), `${x.alias} removed from ${g.name}.`)} aria-label={`Remove ${x.alias} from ${g.name}`}><Icon name="x" size={13} /></button>
                  </li>
                {/if}
              {:else}<li class="tiny muted pad">Empty. Drag models here, or select them and use Add to group.</li>{/each}
            </ol>
          </li>
        {:else}<li class="muted small">No groups. Everyone sees one list in your order.</li>{/each}
      </ol>
    </section>
  </div>
{/if}

<Modal bind:open={mOpen} title={m ? `Edit ${m.alias}` : ''} wide>
  {#if m}
    {#if mErr}<div class="notice bad" role="alert"><div>{mErr}</div></div>{/if}
    <div class="two">
      <div class="field"><label for="pa">Name people see</label><input id="pa" class="input" maxlength="120" bind:value={f.alias} /></div>
      <div class="field"><label for="pp">Provider</label>
        <select id="pp" class="input" bind:value={f.provider}><option value="">{detected('provider') ? `Detected: ${m.profile.provider_label}` : 'Not known'}</option>{#each cat.providers as p (p.id)}<option value={p.id}>{p.label}</option>{/each}</select>
        {#if m.profile._source?.provider}<span class="hint">Currently {SRC[m.profile._source.provider]}.</span>{/if}</div>
      {#each FIELDS as [k, l, ph] (k)}
        <div class="field"><label for={`p-${k}`}>{l}</label><input id={`p-${k}`} class="input" maxlength="80" bind:value={f[k]} placeholder={detected(k) || ph} />
          <span class="hint">{m.profile._source?.[k] ? `Currently ${m.profile[k]}, ${SRC[m.profile._source[k]]}.` : 'Not reported. Leave empty rather than guess.'}</span></div>
      {/each}
      <div class="field"><label for="pc">Context length (tokens)</label><input id="pc" class="input" type="number" min="1" bind:value={f.context_length} placeholder={String(detected('context_length') || '')} />
        <span class="hint">{m.profile._source?.context_length ? `Currently ${m.profile.context_length.toLocaleString()}, ${SRC[m.profile._source.context_length]}.` : 'Not reported.'}</span></div>
      <div class="field"><label for="pm">Kind</label><select id="pm" class="input" bind:value={f.modality}><option value="">{detected('modality') ? `Detected: ${m.profile.modality}` : 'Not known'}</option><option value="text">Text</option><option value="text+image">Text and images</option><option value="image">Picture generation</option><option value="video">Video generation</option></select></div>
      <div class="field"><label for="px">Load with context window</label><input id="px" class="input" type="number" min="512" max="2000000" bind:value={f.ctx} placeholder="Engine default" /><span class="hint">Only engines that load models on demand use this.</span></div>
    </div>
    <div class="field"><label for="pd">Description shown in the chooser</label><textarea id="pd" class="input" rows="2" maxlength="500" bind:value={f.description} placeholder={detected('description') || 'One line: what it is good at'}></textarea></div>
    <fieldset class="fs"><legend>Capabilities</legend>
      <label class="row small"><input type="checkbox" checked={!f.capabilities} onchange={(e) => (f.capabilities = (e.currentTarget as HTMLInputElement).checked ? null : [...(m.reported_capabilities ?? [])])} /> Use what the backend reports ({(m.reported_capabilities ?? []).join(', ') || 'nothing'})</label>
      {#if f.capabilities}
        <div class="row">{#each CAPS as c (c)}<label class="row small" style="gap:4px"><input type="checkbox" checked={f.capabilities.includes(c)} onchange={() => (f.capabilities = f.capabilities.includes(c) ? f.capabilities.filter((y: string) => y !== c) : [...f.capabilities, c])} /> {c}</label>{/each}</div>
        <p class="hint" style="margin:0">These decide what the model is offered for: for example, pictures are sent only to models with vision. Claiming a capability the model lacks gives people errors, not features.</p>
      {/if}
    </fieldset>
    <fieldset class="fs"><legend>Icon</legend>
      <div class="row"><ModelIcon icon={f.icon_asset ? { asset: f.icon_asset } : m.profile.icon} label={f.alias} size={32} />
        <label class="btn sm"><Icon name="upload" size={14} /> Upload an icon<input type="file" accept="image/png,image/webp,image/jpeg,image/svg+xml" class="sr-only" onchange={iconUp} /></label>
        {#if f.icon_asset}<button class="btn ghost sm" onclick={() => (f.icon_asset = '')}>Use the provider's icon</button>{/if}</div>
      <p class="hint" style="margin:0">{cat.icon_note}</p>
    </fieldset>
    <fieldset class="fs"><legend>Access</legend>
      <label class="row small"><input type="checkbox" bind:checked={enabled} /> Turned on (off: nobody can use it, whatever the rules say)</label>
      <GrantsEditor bind:rules {roles} {teams} {users} modes={cat.grant_modes} />
    </fieldset>
  {/if}
  {#snippet footer()}
    {#if m?.manual}<button class="btn ghost danger" onclick={() => (unreg = m)}>Remove…</button><span class="spacer"></span>{/if}
    <button class="btn" onclick={() => (mOpen = false)}>Cancel</button><button class="btn primary" onclick={saveModel} disabled={busy || !f.alias?.trim()}>Save</button>
  {/snippet}
</Modal>

<Modal bind:open={gOpen} title={gEdit?.id ? `Edit ${gEdit.name}` : 'New group'}>
  {#if gEdit}
    {#if gErr}<div class="notice bad" role="alert"><div>{gErr}</div></div>{/if}
    <div class="field"><label for="gn">Name</label><input id="gn" class="input" maxlength="80" bind:value={gEdit.name} /></div>
    <div class="field"><label for="gd">Description</label><input id="gd" class="input" maxlength="300" bind:value={gEdit.description} placeholder="Optional, shown under the heading" /></div>
    <label class="row small"><input type="checkbox" bind:checked={gEdit.collapsed} /> Folded until opened</label>
    <fieldset class="fs"><legend>Show this heading to</legend>
      <p class="hint" style="margin:0">Everyone when empty. This only changes what people see; it never lets anyone use a model.</p>
      {#each gEdit.audience as a, i (a.type + a.id)}<div class="row small"><span class="grow">{a.type}: {audName(a)}</span><button class="btn ghost sm" onclick={() => (gEdit.audience = gEdit.audience.filter((_: any, j: number) => j !== i))}>Remove</button></div>{/each}
      <div class="row"><select class="input" style="max-width:110px" aria-label="Kind" bind:value={aud.type} onchange={() => (aud.id = '')}><option value="role">Role</option><option value="team">Team</option><option value="user">Person</option></select>
        <select class="input" style="max-width:240px" aria-label="Who" bind:value={aud.id}><option value="">Choose…</option>{#each (aud.type === 'role' ? roles : aud.type === 'team' ? teams : users) as o (o.id)}<option value={o.id}>{o.label}</option>{/each}</select>
        <button class="btn sm" disabled={!aud.id} onclick={() => { if (!gEdit.audience.some((x: any) => x.type === aud.type && x.id === aud.id)) gEdit.audience = [...gEdit.audience, { ...aud }]; aud.id = ''; }}>Add</button></div>
    </fieldset>
  {/if}
  {#snippet footer()}<button class="btn" onclick={() => (gOpen = false)}>Cancel</button><button class="btn primary" onclick={saveGroup} disabled={!gEdit?.name?.trim()}>Save group</button>{/snippet}
</Modal>

<Modal open={!!gDel} title={`Remove the group ${gDel?.name ?? ''}?`}>
  <p>Only the heading goes. Its models stay in the catalogue, in any other group, and keep their access rules.</p>
  {#snippet footer()}<button class="btn" onclick={() => (gDel = null)}>Cancel</button><button class="btn danger solid" onclick={deleteGroup}>Remove group</button>{/snippet}
</Modal>

<Modal bind:open={pOpen} title="Provider icons" wide>
  <p class="small">{cat?.icon_note}</p>
  <ul class="prov">
    {#each cat?.providers ?? [] as p (p.id)}
      <li><ModelIcon icon={p.icon ? { asset: p.icon } : { mark: p.mark, tone: p.tone }} label={p.label} size={24} /><span class="grow small">{p.label}</span>
        <label class="btn ghost sm">Upload<input type="file" accept="image/png,image/webp,image/jpeg,image/svg+xml" class="sr-only" onchange={(e) => providerIcon(p.id, e)} /></label>
        {#if p.icon}<button class="btn ghost sm" onclick={() => providerIcon(p.id, null)}>Lettermark</button>{/if}</li>
    {/each}
  </ul>
  {#snippet footer()}<button class="btn" onclick={() => (pOpen = false)}>Close</button>{/snippet}
</Modal>

<Modal bind:open={rOpen} title="Register a model">
  <p class="small">For a model the backend serves but does not list. It starts turned off, with nobody allowed, exactly like a discovered model. Nothing is checked against the backend until someone uses it.</p>
  {#if rErr}<div class="notice bad" role="alert"><div>{rErr}</div></div>{/if}
  <div class="field"><label for="rb">Backend</label><select id="rb" class="input" bind:value={reg.backend_id}>{#each backends as b (b.id)}<option value={b.id}>{b.name}</option>{/each}</select></div>
  <div class="field"><label for="ri">Model identifier on that backend</label><input id="ri" class="input mono" maxlength="300" bind:value={reg.real_id} spellcheck="false" placeholder="exactly as the backend's API expects it" /></div>
  <div class="field"><label for="rn">Name people see</label><input id="rn" class="input" maxlength="120" bind:value={reg.alias} placeholder="Derived from the identifier when empty" /></div>
  <div class="row">{#each CAPS as c (c)}<label class="row small" style="gap:4px"><input type="checkbox" checked={reg.capabilities.includes(c)} onchange={() => (reg.capabilities = reg.capabilities.includes(c) ? reg.capabilities.filter((y) => y !== c) : [...reg.capabilities, c])} /> {c}</label>{/each}</div>
  {#snippet footer()}<button class="btn" onclick={() => (rOpen = false)}>Cancel</button><button class="btn primary" onclick={register} disabled={!reg.backend_id || !reg.real_id.trim() || !reg.capabilities.length}>Register</button>{/snippet}
</Modal>

<Modal open={!!unreg} title={`Remove ${unreg?.alias ?? ''}?`}>
  <p>Only models you registered by hand and nobody has used can be removed.</p>
  {#snippet footer()}<button class="btn" onclick={() => (unreg = null)}>Cancel</button><button class="btn danger solid" onclick={unregister}>Remove</button>{/snippet}
</Modal>

<style>
  .tb { margin-bottom: 12px; flex-wrap: wrap; }
  .bulk { margin-bottom: 12px; }
  .board { display: grid; grid-template-columns: minmax(0, 3fr) minmax(300px, 2fr); gap: 16px; align-items: start; }
  .list, .glist, .mem, .prov { list-style: none; margin: 0; padding: 0; }
  .mrow { display: flex; align-items: center; gap: 8px; padding: 6px 12px; border-top: 1px solid var(--border); background: var(--card-bg); position: relative; }
  .mrow.dim .nm strong { color: var(--fg-muted); }
  .mrow.dragging { opacity: 0.5; }
  .mrow.before { box-shadow: inset 0 2px 0 var(--accent); } .mrow.after { box-shadow: inset 0 -2px 0 var(--accent); }
  .grip { color: var(--fg-muted); cursor: grab; display: grid; }
  .nm { flex: 1; min-width: 0; display: grid; } .nm strong, .nm > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chips { display: flex; gap: 4px; flex-wrap: wrap; justify-content: flex-end; }
  .acts { display: flex; gap: 2px; align-items: center; }
  .acts .on { color: var(--accent); }
  .groups { display: grid; gap: 10px; }
  .gh h3 { margin: 0 0 4px; } .gh .hint { margin: 0; }
  .glist { display: grid; gap: 10px; }
  .grp.before { box-shadow: 0 -3px 0 var(--accent); } .grp.after { box-shadow: 0 3px 0 var(--accent); }
  .grp .panel-h { gap: 4px; align-items: flex-start; }
  .mem { min-height: 40px; }
  .mem.target { outline: 1px dashed var(--border-strong); outline-offset: -4px; }
  .mem .mrow { padding: 4px 8px 4px 12px; }
  .pad { padding: 10px 12px; }
  .two { display: grid; gap: 4px 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }
  .fs { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); margin: 0 0 12px; display: grid; gap: 8px; padding: 12px; }
  legend { font-weight: 600; font-size: 13px; padding: 0 4px; }
  .prov li { display: flex; gap: 10px; align-items: center; padding: 4px 0; border-bottom: 1px solid var(--border); }
  @media (max-width: 1000px) { .board { grid-template-columns: minmax(0, 1fr); } .chips { display: none; } }
</style>
