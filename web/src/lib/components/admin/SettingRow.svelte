<script lang="ts">
  import { untrack } from 'svelte';
  import { post, put, del, get, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import { errMsg } from './util';
  import { loadRoles, loadTeams, loadUsers, type Opt } from './lookup';
  let { s, onsaved }: { s: any; onsaved: () => void } = $props();
  const uid = $derived('set-' + s.id.replace(/\W/g, '-'));
  let scope = $state('deployment'); let scopeId = $state('');
  let opts = $state<Opt[]>([]);
  // The editor starts from the saved value; later edits are the person's own until they save or reset.
  let draft = $state<any>(untrack(() => toDraft(s.value)));
  let error = $state(''); let busy = $state(false);
  let confirmOpen = $state(false); let impact = $state<any>(null);
  let inspectUser = $state(''); let users = $state<Opt[]>([]); let eff = $state<any>(null);
  function toDraft(v: any) { return s.type === 'list' ? (v ?? []).join('\n') : s.type === 'json' ? JSON.stringify(v, null, 1) : s.secret ? '' : v; }
  function fromDraft(): any {
    if (s.type === 'list') return String(draft).split('\n').map((x) => x.trim()).filter(Boolean);
    if (s.type === 'json') return JSON.parse(draft);
    if (s.type === 'int') return draft === '' || draft === null ? NaN : Number(draft);
    return draft;
  }
  const dirty = $derived.by(() => { if (s.secret) return draft !== ''; try { return JSON.stringify(fromDraft()) !== JSON.stringify(s.value); } catch { return true; } });
  $effect(() => { s.value; draft = toDraft(s.value); });
  async function pickScope() {
    scopeId = ''; eff = null; error = '';
    opts = scope === 'role' ? await loadRoles() : scope === 'team' ? await loadTeams() : scope === 'user' ? (users = users.length ? users : await loadUsers()) : [];
  }
  const body = (confirm = false) => ({ value: fromDraft(), scope_type: scope, scope_id: scopeId, confirm_weaker: confirm });
  async function save() {
    error = '';
    if (scope !== 'deployment' && !scopeId) { error = 'Choose which ' + scope + ' this applies to.'; return; }
    try { body(); } catch { error = 'That is not valid JSON.'; return; }
    busy = true;
    try {
      if (s.sensitive_change) {
        impact = await post(`/api/admin/settings/${s.id}/impact`, body());
        if (impact.weakens_protection) { confirmOpen = true; busy = false; return; }
      }
      await commit(false);
    } catch (e) { error = errMsg(e); busy = false; }
  }
  async function commit(confirm: boolean) {
    busy = true; error = '';
    try { await put(`/api/admin/settings/${s.id}`, body(confirm)); confirmOpen = false; toast(`Saved “${s.label}”.` + (s.apply === 'restart' ? ' Takes effect after a restart.' : '')); window.dispatchEvent(new Event('semechka:settings')); onsaved(); }
    catch (e) { error = errMsg(e); confirmOpen = false; }
    busy = false;
  }
  async function reset() {
    if (!scopeId) return;
    try { await del(`/api/admin/settings/${s.id}?scope_type=${scope}&scope_id=${encodeURIComponent(scopeId)}`); toast('Now inherits again.'); onsaved(); } catch (e) { error = errMsg(e); }
  }
  async function inspect() {
    if (!users.length) users = await loadUsers();
    if (!inspectUser) { eff = null; return; }
    try { eff = await get(`/api/admin/settings/${s.id}/effective?user_id=${inspectUser}`); } catch (e) { error = errMsg(e); }
  }
  const show = (v: any) => (v === null || v === undefined ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
  const blocked = $derived(s.prerequisite && !s.prerequisite.ok && s.type === 'bool');
</script>
<article class="set" id={uid}>
  <div class="head">
    <h3 id={uid + '-l'}><label for={uid + '-i'}>{s.label}</label></h3>
    <span class="row" style="gap:6px">
      {#if s.is_default}<span class="chip">Default</span>{:else}<span class="chip info">Changed</span>{/if}
      {#if s.apply === 'restart'}<span class="chip warn">Needs restart</span>{/if}
      {#if s.sensitive_change}<span class="chip">Confirms if weaker</span>{/if}
      {#each s.overrides as o}<span class="chip info" title="Override at {o.scope_type} scope">{o.scope_type}: {o.scope_id.slice(0, 10)}</span>{/each}
    </span>
  </div>
  {#if s.description}<p class="hint" style="margin:2px 0 8px">{s.description}</p>{/if}
  {#if s.effect}<p class="hint" style="margin:0 0 8px"><strong>Effect:</strong> {s.effect}</p>{/if}
  {#if s.prerequisite && !s.prerequisite.ok}<div class="notice warn" style="margin:0 0 8px">Unavailable here: {s.prerequisite.why}</div>{/if}
  {#if !s.can_edit}<p class="hint">You can read this setting but not change it (needs {s.edit_perm}).</p>{/if}

  <div class="ctl">
    {#if s.scopes.length > 1}
      <div class="sc">
        <label class="tiny" for={uid + '-sc'}>Applies to</label>
        <select id={uid + '-sc'} class="input" bind:value={scope} onchange={pickScope} disabled={!s.can_edit}>
          {#each s.scopes as sc}<option value={sc}>{sc === 'deployment' ? 'Everyone (deployment)' : `A ${sc}`}</option>{/each}
        </select>
        {#if scope !== 'deployment'}
          <select class="input" aria-label="Which {scope}" bind:value={scopeId}><option value="">Choose a {scope}…</option>{#each opts as o}<option value={o.id}>{o.label}</option>{/each}</select>
        {/if}
      </div>
    {/if}
    <div class="w">
      {#if s.type === 'bool'}
        <label class="row" style="gap:8px"><input id={uid + '-i'} type="checkbox" bind:checked={draft} disabled={!s.can_edit || blocked} aria-describedby={error ? uid + '-e' : undefined} /> {draft ? 'On' : 'Off'}</label>
      {:else if s.type === 'int' || s.type === 'float'}
        <input id={uid + '-i'} class="input mono" type="number" min={s.min ?? undefined} max={s.max ?? undefined} bind:value={draft} disabled={!s.can_edit} style="max-width:180px" />
        {#if s.min != null}<span class="hint">{s.min} to {s.max}</span>{/if}
      {:else if s.id === 'signup.default_role'}
        <select id={uid + '-i'} class="input" bind:value={draft} disabled={!s.can_edit} style="max-width:260px">{#await loadRoles() then rs}{#each rs as r}<option value={r.id}>{r.label} ({r.id})</option>{/each}{/await}</select>
      {:else if s.type === 'enum'}
        <select id={uid + '-i'} class="input" bind:value={draft} disabled={!s.can_edit} style="max-width:260px">{#each s.choices as c}<option value={c}>{c}</option>{/each}</select>
      {:else if s.type === 'color'}
        <div class="row" style="gap:8px"><input type="color" aria-label="{s.label} colour picker" value={draft} oninput={(e) => (draft = (e.target as HTMLInputElement).value.toUpperCase())} disabled={!s.can_edit} style="width:44px;height:36px;padding:2px" /><input id={uid + '-i'} class="input mono" bind:value={draft} disabled={!s.can_edit} style="max-width:140px" /></div>
      {:else if s.type === 'text' || s.type === 'list' || s.type === 'json'}
        <textarea id={uid + '-i'} class="input" class:mono={s.type !== 'text'} rows={s.type === 'text' ? 3 : 5} bind:value={draft} disabled={!s.can_edit} placeholder={s.type === 'list' ? 'One per line' : ''}></textarea>
      {:else if s.secret}
        <input id={uid + '-i'} class="input" type="password" autocomplete="new-password" bind:value={draft} placeholder={s.value ? 'Set. Type to replace; it is never shown.' : 'Not set'} disabled={!s.can_edit} style="max-width:360px" />
      {:else}
        <input id={uid + '-i'} class="input" bind:value={draft} disabled={!s.can_edit} aria-describedby={error ? uid + '-e' : undefined} style="max-width:480px" />
      {/if}
    </div>
  </div>
  {#if error}<p class="err" id={uid + '-e'} role="alert">{error}</p>{/if}
  {#if s.can_edit}
    <div class="row" style="margin-top:8px">
      <button class="btn primary sm" disabled={!dirty || busy} onclick={save}>{busy ? 'Saving…' : 'Save'}</button>
      {#if dirty}<button class="btn sm" onclick={() => { draft = toDraft(s.value); error = ''; }}>Revert</button>{/if}
      {#if scope !== 'deployment' && scopeId}<button class="btn sm" onclick={reset}>Reset to inherited</button>{/if}
      <details class="insp"><summary class="small">Effective value for a person</summary>
        <div class="row" style="margin-top:6px"><select class="input" aria-label="Person" bind:value={inspectUser} onchange={inspect} style="max-width:320px" onfocus={async () => { if (!users.length) users = await loadUsers(); }}><option value="">Choose a person…</option>{#each users as u}<option value={u.id}>{u.label}</option>{/each}</select></div>
        {#if eff}<dl class="kv" style="margin-top:8px"><dt>Value</dt><dd>{show(eff.value)}</dd><dt>Rule</dt><dd>{eff.rule}</dd><dt>Source</dt><dd>{eff.source ? `${eff.source.scope_type}${eff.source.scope_id ? ' ' + eff.source.scope_id : ''}` : 'default'}</dd>{#each eff.layers as l}<dt>Layer</dt><dd>{l.scope_type} {l.scope_id} = {show(l.value)}</dd>{/each}</dl>{/if}
      </details>
    </div>
  {/if}
</article>
<Modal bind:open={confirmOpen} title="This change reduces protection">
  {#if impact}<p>“{s.label}” changes from <code>{show(impact.old)}</code> to <code>{show(impact.new)}</code>.</p>{#if impact.effect}<p>{impact.effect}</p>{/if}<p class="muted">The change is recorded in the audit log and can be rolled back from History.</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (confirmOpen = false)}>Cancel</button><button class="btn primary" onclick={() => commit(true)}>Apply anyway</button>{/snippet}
</Modal>
<style>
  .set { padding: 16px 0; border-bottom: 1px solid var(--border); }
  .head { display: flex; justify-content: space-between; gap: 8px; flex-wrap: wrap; align-items: baseline; }
  h3 { font-size: 15px; }
  .ctl { display: grid; gap: 8px; }
  .sc { display: flex; gap: 8px; flex-wrap: wrap; align-items: end; } .sc select { max-width: 260px; } .sc label { width: 100%; }
  .insp { margin-left: auto; } summary { cursor: pointer; }
</style>
