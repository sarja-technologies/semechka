<script lang="ts">
  // Workspace settings: identity, the ceiling on what anyone here may do (it applies to administrators too), which
  // models are allowed, storage limits, and the measured usage. Saving writes to the server's policy, not a preference.
  import { page } from '$app/state';
  import { get, patch } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { cur, can, refreshWorkspace } from '$lib/ws/current.svelte';
  import { msg, bytes, PERM_GROUPS } from '$lib/ws/util';
  import State from '$lib/components/admin/State.svelte';

  const wid = $derived(page.params.wid ?? '');
  let name = $state(''), desc = $state(''), vis = $state('private'), inherit = $state(true), deny = $state<string[]>([]), limitModels = $state(false), allowed = $state<string[]>([]);
  let storage = $state(''), maxUp = $state(''), models = $state<any[]>([]), usage = $state<any>(null), loading = $state(true), error = $state(''), busy = $state(false);
  const manage = $derived(can('ws.members.manage') && !cur.ws?.archived), policy = $derived(can('ws.policy.edit') && !cur.ws?.archived);

  async function load() {
    const w = cur.ws; if (!w) return;
    name = w.name; desc = w.description; vis = w.visibility; inherit = w.inherit_members; deny = [...(w.policy?.deny ?? [])];
    limitModels = Array.isArray(w.policy?.models); allowed = [...(w.policy?.models ?? [])];
    storage = w.quotas?.storage_mb ?? ''; maxUp = w.quotas?.max_upload_mb ?? '';
    try {
      [usage, models] = await Promise.all([get(`/api/workspaces/${wid}/usage`), can('ws.policy.edit') ? get(`/api/workspaces/${wid}/models?mode=chat&scope=org`) : []]);
      error = '';
    } catch (e) { error = msg(e); } finally { loading = false; }
  }
  $effect(() => { cur.ws?.id; load(); });

  async function save(body: Record<string, unknown>, done: string) {
    busy = true;
    try { await patch(`/api/workspaces/${wid}`, body); toast(done); await refreshWorkspace(); await load(); } catch (e) { toast(msg(e), 'bad'); } finally { busy = false; }
  }
  const saveBasics = () => save({ name, description: desc, visibility: vis, inherit_members: inherit }, 'Saved');
  const savePolicy = () => save({ policy: { ...(cur.ws.policy ?? {}), deny, ...(limitModels ? { models: allowed } : { models: null }) } }, 'Policy saved');
  const saveQuotas = () => save({ quotas: { ...(cur.ws.quotas ?? {}), storage_mb: storage ? Number(storage) : null, max_upload_mb: maxUp ? Number(maxUp) : null } }, 'Limits saved');
  const archive = (on: boolean) => save({ archived: on }, on ? 'Archived: now read only' : 'Restored');
  const grouped = $derived(PERM_GROUPS.map((g) => [g, cur.perms.filter((p) => p.group === g && !p.id.startsWith('org.') && !['ws.view'].includes(p.id))] as [string, any[]]).filter(([, l]) => l.length));
  const pct = (used: number, mb: number) => (mb ? Math.min(100, Math.round((used / (mb * 1048576)) * 100)) : 0);
</script>

<State {loading} {error} retry={load} />
{#if cur.ws && !loading}
  {#if cur.ws.archived}<div class="notice warn" role="status"><div>This workspace is archived and read only. {#if can('ws.delete')}<button class="linkish" onclick={() => archive(false)}>Restore it</button>{/if}</div></div>{/if}
  <div class="cols">
    <section class="panel" aria-label="General"><div class="panel-h"><h3>General</h3></div><div class="panel-b">
      <form onsubmit={(e) => { e.preventDefault(); saveBasics(); }}>
        <div class="field"><label for="wn">Name</label><input id="wn" class="input" bind:value={name} maxlength="160" disabled={!manage} required /></div>
        <div class="field"><label for="wd">Description</label><textarea id="wd" class="input" rows="3" bind:value={desc} disabled={!manage}></textarea></div>
        <fieldset class="field" style="border:0;padding:0"><legend class="label">Visibility</legend>
          <label class="row"><input type="radio" bind:group={vis} value="private" disabled={!manage} /><span><strong>Private.</strong> Only members can see that it exists.</span></label>
          <label class="row"><input type="radio" bind:group={vis} value="shared" disabled={!manage} /><span><strong>Shared.</strong> Listed for everyone in the organisation; contents stay limited to members.</span></label></fieldset>
        {#if cur.ws.parent_id}<label class="row"><input type="checkbox" bind:checked={inherit} disabled={!manage} /><span>People in the parent workspace are also members here</span></label>{/if}
        {#if manage}<div style="margin-top:12px"><button class="btn primary" disabled={busy || !name.trim()}>Save</button></div>{/if}
      </form></div></section>

    <section class="panel" aria-label="Usage"><div class="panel-h"><h3>Usage</h3></div><div class="panel-b">
      {#if usage}
        <p class="small" style="margin-bottom:4px">Storage: <strong>{bytes(usage.storage.used_bytes)}</strong>{#if usage.storage.quota_mb} of {usage.storage.quota_mb} MB{/if} · {usage.storage.files} files</p>
        {#if usage.storage.quota_mb}<div class="meter" role="progressbar" aria-valuenow={pct(usage.storage.used_bytes, usage.storage.quota_mb)} aria-valuemin="0" aria-valuemax="100" aria-label="Storage used"><span style="width:{pct(usage.storage.used_bytes, usage.storage.quota_mb)}%"></span></div>{/if}
        <dl class="kv" style="margin-top:12px"><dt>Contexts</dt><dd>{usage.contexts}</dd><dt>Agents</dt><dd>{usage.agents}</dd>
          <dt>Agent tasks (24 h)</dt><dd>{usage.agent_runs_24h.runs}</dd><dt>Tokens (24 h)</dt><dd>{(usage.agent_runs_24h.prompt_tokens + usage.agent_runs_24h.completion_tokens).toLocaleString()}</dd></dl>
      {/if}</div></section>
  </div>

  {#if policy}
    <section class="panel" style="margin-top:16px" aria-label="Policy"><div class="panel-h"><h3>What is switched off here</h3><span class="small muted">Applies to everyone in this workspace, administrators included.</span></div><div class="panel-b">
      <form onsubmit={(e) => { e.preventDefault(); savePolicy(); }}>
        <div class="cols3">{#each grouped as [g, list]}<fieldset class="field" style="border:0;padding:0"><legend class="label">{g}</legend>
          {#each list as p}<label class="row"><input type="checkbox" value={p.id} bind:group={deny} /><span>{p.label}</span></label>{/each}</fieldset>{/each}</div>
        <fieldset class="field" style="border:0;padding:0;margin-top:8px"><legend class="label">Language models</legend>
          <label class="row"><input type="radio" bind:group={limitModels} value={false} /><span>Any model the organisation allows</span></label>
          <label class="row"><input type="radio" bind:group={limitModels} value={true} /><span>Only these</span></label>
          {#if limitModels}<div class="pickbox">{#each models as m}<label class="row"><input type="checkbox" value={m.id} bind:group={allowed} />{m.alias}</label>{:else}<span class="muted small">No models are available.</span>{/each}</div>{/if}</fieldset>
        <button class="btn primary" disabled={busy}>Save policy</button>
      </form></div></section>

    <section class="panel" style="margin-top:16px" aria-label="Limits"><div class="panel-h"><h3>Limits</h3></div><div class="panel-b">
      <form class="row" onsubmit={(e) => { e.preventDefault(); saveQuotas(); }}>
        <div class="field" style="margin:0"><label for="qs">Storage (MB)</label><input id="qs" class="input" type="number" min="1" placeholder="No limit" bind:value={storage} /></div>
        <div class="field" style="margin:0"><label for="qu">Largest upload (MB)</label><input id="qu" class="input" type="number" min="1" placeholder="Organisation default" bind:value={maxUp} /></div>
        <button class="btn primary" style="align-self:flex-end" disabled={busy}>Save limits</button></form>
      <p class="tiny muted" style="margin-top:8px">An organisation-wide limit also applies; the smaller one wins.</p></div></section>
  {/if}

  {#if can('ws.delete') && !cur.ws.archived}
    <section class="panel" style="margin-top:16px" aria-label="Archive"><div class="panel-h"><h3>Archive</h3></div><div class="panel-b">
      <p class="small">Archiving makes the workspace read only. Nothing is deleted, and an administrator can restore it.</p>
      <button class="btn danger" onclick={() => archive(true)} disabled={busy}>Archive this workspace</button></div></section>
  {/if}
{/if}

<style>
  .cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: start; } .cols3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 0 24px; }
  .meter { height: 8px; border-radius: 99px; background: var(--surface-2); border: 1px solid var(--border); overflow: hidden; } .meter span { display: block; height: 100%; background: var(--accent); }
  .pickbox { max-height: 30vh; overflow: auto; display: grid; gap: 2px; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px; margin: 6px 0 12px; }
  @media (max-width: 860px) { .cols { grid-template-columns: minmax(0, 1fr); } }
</style>
