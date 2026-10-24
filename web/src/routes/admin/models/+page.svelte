<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, patch, put, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { can } from '$lib/session.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import Pager from '$lib/components/admin/Pager.svelte';
  import { errMsg, fmtBytes, fmtParams } from '$lib/components/admin/util';
  import { loadRoles, loadTeams, loadUsers, type Opt, type Rule } from '$lib/components/admin/lookup';
  import CatalogBoard from '$lib/components/admin/CatalogBoard.svelte';
  import GrantsEditor from '$lib/components/admin/GrantsEditor.svelte';
  import EnginePanel from '$lib/components/admin/EnginePanel.svelte';
  let tab = $state<'catalogue' | 'running' | 'engine'>('catalogue');
  const seeEngines = $derived(can('ops.model.load') || can('ops.queue.view') || can('admin.system.view'));
  let rows = $state<any[]>([]); let total = $state(0); let loading = $state(true); let error = $state('');
  let q = $state(''); let tag = $state(''); let capability = $state(''); let backend = $state(''); let enabled = $state(''); let pg = $state(1); const per = 20;
  let backends = $state<any[]>([]); let roles = $state<Opt[]>([]); let teams = $state<Opt[]>([]); let users = $state<Opt[]>([]);
  let picked = $state<Set<string>>(new Set()); let allMatching = $state(false);
  async function load() { loading = true; error = ''; try { const r = await get('/api/admin/models' + qs({ q, tag, capability, backend, enabled, page: pg, per_page: per })); rows = r.items; total = r.total; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(async () => { load(); roles = await loadRoles(); teams = await loadTeams(); loadUsers().then((u) => (users = u)).catch(() => {}); try { backends = await get('/api/admin/backends'); } catch { /* no backend permission */ } });
  let t: any; const filt = () => { clearTimeout(t); t = setTimeout(() => { pg = 1; picked = new Set(); allMatching = false; load(); }, 250); };
  const pageAll = $derived(rows.length > 0 && rows.every((r) => picked.has(r.id)));
  const togglePage = () => { picked = pageAll ? new Set() : new Set(rows.map((r) => r.id)); allMatching = false; };
  const toggle = (id: string) => { const n = new Set(picked); n.has(id) ? n.delete(id) : n.add(id); picked = n; allMatching = false; };
  const count = $derived(allMatching ? total : picked.size);
  // bulk
  let bOpen = $state(false); let bAct = $state('enable'); let bVal = $state(''); let bErr = $state('');
  const BL: Record<string, string> = { enable: 'Enable', disable: 'Disable', add_tag: 'Add tag', remove_tag: 'Remove tag', grant_role: 'Allow role', revoke_role: 'Remove role rule', deny_team: 'Deny team' };
  async function runBulk() {
    bErr = '';
    try { const body: any = { action: bAct, expected_count: count, value: bVal || undefined }; if (allMatching) body.all_matching = { q: q || undefined, tag: tag || undefined, capability: capability || undefined, backend: backend || undefined, enabled: enabled === '' ? undefined : enabled === 'true' }; else body.ids = [...picked];
      const r = await post('/api/admin/models/bulk', body); bOpen = false; picked = new Set(); allMatching = false; toast(`${BL[bAct]}: ${r.done} models.`); load(); } catch (e) { bErr = errMsg(e); }
  }
  // edit
  let eOpen = $state(false); let m = $state<any>(null); let e = $state<any>({}); let eErr = $state(''); let rules = $state<Rule[]>([]); let tagsText = $state('');
  function edit(r: any) { m = r; e = { alias: r.alias, enabled: r.enabled, pinned: r.pinned, system_prompt: '', keepPrompt: true, max_tokens: r.gen_limits.max_tokens ?? '', temperature: r.gen_limits.temperature ?? '', allow_tools: r.allow_tools }; tagsText = (r.tags ?? []).join(', '); rules = r.grants.map((g: any) => ({ ...g, subject_id: g.subject_type === 'everyone' ? '*' : g.subject_id })); eErr = ''; eOpen = true; }
  async function saveModel() {
    eErr = '';
    try {
      const gl: any = {}; if (e.max_tokens !== '' && e.max_tokens !== null) gl.max_tokens = Number(e.max_tokens); if (e.temperature !== '' && e.temperature !== null) gl.temperature = Number(e.temperature);
      const body: any = { alias: e.alias, enabled: e.enabled, pinned: e.pinned, tags: tagsText.split(',').map((x: string) => x.trim()).filter(Boolean), gen_limits: gl, allow_tools: e.allow_tools };
      if (!e.keepPrompt) body.system_prompt = e.system_prompt;
      await patch(`/api/admin/models/${m.id}`, body);
      await put(`/api/admin/models/${m.id}/grants`, { grants: rules.map((r) => ({ ...r, subject_id: r.subject_type === 'everyone' ? '' : r.subject_id })) });
      eOpen = false; toast('Model saved.'); load();
    } catch (x) { eErr = errMsg(x); }
  }
  const caps = ['chat', 'vision', 'tools', 'embeddings', 'image', 'video', 'audio'];
</script>
<Page title="Models" lead="Discovered models stay off, with nobody allowed, until you turn them on and allow someone. Groups, order and icons only change how the chooser looks; access comes from each model's rules, and a deny always wins.">
  <div class="tabs" role="tablist" aria-label="Models">
    <button role="tab" aria-selected={tab === 'catalogue'} class:on={tab === 'catalogue'} onclick={() => (tab = 'catalogue')}>Catalogue</button>
    {#if seeEngines}<button role="tab" aria-selected={tab === 'running'} class:on={tab === 'running'} onclick={() => (tab = 'running')}>Running</button>{/if}
    <button role="tab" aria-selected={tab === 'engine'} class:on={tab === 'engine'} onclick={() => { tab = 'engine'; load(); }}>Engine settings and bulk access</button>
  </div>
  {#if tab === 'catalogue'}<CatalogBoard />{:else if tab === 'running'}<EnginePanel />{:else}
  <div class="row" style="margin-bottom:12px" role="search">
    <div style="flex:1 1 220px;max-width:300px"><label class="sr-only" for="mq">Search models</label><input id="mq" class="input" type="search" placeholder="Search alias or real id" bind:value={q} oninput={filt} /></div>
    <label class="sr-only" for="mt">Capability</label><select id="mt" class="input" style="max-width:160px" bind:value={capability} onchange={filt}><option value="">Any capability</option>{#each caps as c}<option>{c}</option>{/each}</select>
    <label class="sr-only" for="mb">Backend</label><select id="mb" class="input" style="max-width:180px" bind:value={backend} onchange={filt}><option value="">Any backend</option>{#each backends as b}<option value={b.id}>{b.name}</option>{/each}</select>
    <label class="sr-only" for="me">State</label><select id="me" class="input" style="max-width:150px" bind:value={enabled} onchange={filt}><option value="">Any state</option><option value="true">Enabled</option><option value="false">Disabled</option></select>
    <label class="sr-only" for="mg">Tag</label><input id="mg" class="input" style="max-width:130px" placeholder="Tag" bind:value={tag} oninput={filt} />
  </div>
  {#if count > 0}<div class="notice info" role="region" aria-label="Bulk actions"><div class="grow"><strong>{count}</strong> {allMatching ? 'models matching the filters' : 'selected on this page'}.
    {#if pageAll && !allMatching && total > rows.length}<button class="btn sm ghost" onclick={() => (allMatching = true)}>Select all {total} matching across all pages</button>{/if}{#if allMatching}<button class="btn sm ghost" onclick={() => (allMatching = false)}>Only this page ({rows.length})</button>{/if}</div>
    <div class="row">{#each Object.keys(BL) as a}<button class="btn sm" onclick={() => { bAct = a; bVal = ''; bErr = ''; bOpen = true; }}>{BL[a]}</button>{/each}</div></div>{/if}
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No models yet. Add a backend, then use Sync models to discover them.' : ''} />
  {#if rows.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Models</caption><thead><tr><th style="width:40px"><input type="checkbox" aria-label="Select all {rows.length} models on this page" checked={pageAll} onchange={togglePage} /></th><th>Alias</th><th>Real id</th><th>Backend</th><th>Capabilities</th><th>Reported</th><th>Access rules</th><th>State</th><th></th></tr></thead><tbody>
    {#each rows as r (r.id)}<tr>
      <td><input type="checkbox" aria-label="Select {r.alias}" checked={picked.has(r.id)} onchange={() => toggle(r.id)} /></td>
      <td><strong>{r.alias}</strong>{#if r.pinned} <span class="chip">pinned</span>{/if}</td><td class="mono tiny" title={r.real_id} style="max-width:200px;overflow:hidden;text-overflow:ellipsis">{r.real_id}</td>
      <td>{r.backend}{#if r.external} <span class="chip warn">external</span>{/if}</td><td>{r.capabilities.join(', ')}</td>
      <td class="mono tiny">{[r.meta.params ? fmtParams(r.meta.params) : r.meta.params_label, r.meta.quantization, r.meta.context ? `${r.meta.context} ctx` : null].filter(Boolean).join(' · ') || 'nothing reported'}</td>
      <td>{r.grants.length ? `${r.grants.filter((g: any) => g.effect === 'allow').length} allow, ${r.grants.filter((g: any) => g.effect === 'deny').length} deny` : 'nobody'}</td>
      <td>{#if !r.available}<span class="chip bad">missing on backend</span>{:else if r.enabled}<span class="chip ok">enabled</span>{:else}<span class="chip">disabled</span>{/if}</td>
      <td><button class="btn sm" onclick={() => edit(r)}>Edit</button></td></tr>{/each}</tbody></table></div>{/if}
  <Pager bind:page={pg} perPage={per} {total} onchange={() => { picked = new Set(); allMatching = false; load(); }} />
  {/if}
</Page>

<Modal bind:open={bOpen} title="{BL[bAct]} — {count} {count === 1 ? 'model' : 'models'}?">
  <p>{allMatching ? `Applies to all ${total} models matching the filters, on every page.` : `Applies to the ${picked.size} models selected on this page.`}</p>
  {#if bAct === 'add_tag' || bAct === 'remove_tag'}<div class="field"><label for="bv">Tag</label><input id="bv" class="input" bind:value={bVal} /></div>
  {:else if bAct === 'grant_role' || bAct === 'revoke_role'}<div class="field"><label for="bv">Role</label><select id="bv" class="input" bind:value={bVal}><option value="">Choose…</option>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select></div>
  {:else if bAct === 'deny_team'}<div class="field"><label for="bv">Team</label><select id="bv" class="input" bind:value={bVal}><option value="">Choose…</option>{#each teams as r}<option value={r.id}>{r.label}</option>{/each}</select></div>{/if}
  {#if bErr}<p class="err" role="alert">{bErr}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (bOpen = false)}>Cancel</button><button class="btn primary" onclick={runBulk}>{BL[bAct]} {count}</button>{/snippet}
</Modal>

<Modal bind:open={eOpen} title={m ? `Edit ${m.alias}` : ''} wide>
  {#if m}
  <div class="row"><div class="field grow"><label for="ea">Alias (what people see)</label><input id="ea" class="input" bind:value={e.alias} /></div>
    <label class="row" style="gap:8px"><input type="checkbox" bind:checked={e.enabled} /> Enabled</label><label class="row" style="gap:8px"><input type="checkbox" bind:checked={e.pinned} /> Pinned</label></div>
  <dl class="kv" style="margin-bottom:16px"><dt>Model id</dt><dd class="mono">{m.real_id}</dd>{#if m.real_id_full}<dt>Full identifier</dt><dd class="mono">{m.real_id_full}</dd>{/if}<dt>Backend</dt><dd>{m.backend} ({m.runtime})</dd>
    {#each Object.entries(m.meta).filter(([k]) => !['loaded'].includes(k)) as [k, v]}<dt>{k}</dt><dd>{k.includes('bytes') ? fmtBytes(v as number) : String(v)}</dd>{/each}</dl>
  {#if !Object.keys(m.meta).length}<p class="hint">The backend reported no technical details for this model, so none are shown.</p>{/if}
  <div class="field"><label for="et">Tags (comma separated)</label><input id="et" class="input" bind:value={tagsText} /><span class="hint">Descriptive only. A tag never changes what people may do.</span></div>
  <div class="field"><label for="es">System instructions</label>{#if e.keepPrompt}<div class="row"><span class="muted">{m.system_prompt_set ? 'Set (hidden from everyone but this form).' : 'None.'}</span><button class="btn sm" onclick={() => (e.keepPrompt = false)}>{m.system_prompt_set ? 'Replace' : 'Add'}</button></div>{:else}<textarea id="es" class="input" rows="4" bind:value={e.system_prompt} placeholder="Leave empty to remove"></textarea>{/if}</div>
  <div class="row"><div class="field"><label for="emt">Max output tokens</label><input id="emt" class="input" type="number" min="1" bind:value={e.max_tokens} style="max-width:140px" /></div><div class="field"><label for="ete">Temperature</label><input id="ete" class="input" type="number" step="0.1" min="0" bind:value={e.temperature} style="max-width:120px" /></div></div>

  <fieldset class="fs"><legend>Who may use this model</legend>
    <GrantsEditor bind:rules {roles} {teams} {users} modes={['chat', 'code', 'vision', 'image', 'video']} />
  </fieldset>

  <fieldset class="fs"><legend>Operate</legend>
    <p class="hint" style="margin:0">Loading and unloading is a separate permission (ops.model.load) from editing the catalogue. It happens under <strong>Running</strong>, which shows the engine's memory and anything that would interrupt someone's reply before you confirm.</p>
    <div class="row">{#if can('ops.model.load')}<button class="btn sm" onclick={() => { eOpen = false; tab = 'running'; }}>Open Running</button>{:else}<span class="hint">You do not hold ops.model.load.</span>{/if}{#if m.meta.loaded !== undefined}<span class="chip">last seen {m.meta.loaded ? 'loaded' : 'not loaded'}</span>{/if}</div></fieldset>
  {#if eErr}<p class="err" role="alert">{eErr}</p>{/if}{/if}
  {#snippet footer()}<button class="btn" onclick={() => (eOpen = false)}>Cancel</button><button class="btn primary" onclick={saveModel}>Save model</button>{/snippet}
</Modal>
<style>.tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 16px; } .tabs button { all: unset; cursor: pointer; padding: 8px 12px; font-size: 14px; border-bottom: 2px solid transparent; margin-bottom: -1px; } .tabs button.on { border-color: var(--accent); font-weight: 600; } .tabs button:focus-visible { outline: 2px solid var(--accent); } .fs { min-width: 0; border: 1px solid var(--border); border-radius: var(--radius-sm); margin: 0 0 16px; display: grid; gap: 10px; padding: 12px; } legend { font-weight: 600; font-size: 13px; padding: 0 4px; } .fs :global(table.t) { border: 0; }</style>
