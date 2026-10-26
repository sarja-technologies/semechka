<script lang="ts">
  // Organisation policy: what is switched off for the whole organisation, which models may be used, storage and
  // activity limits, and how long things are kept. The server applies every one of these on every request.
  import { page } from '$app/state';
  import { get, patch } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { org, ocan, reloadOrg } from '$lib/ws/org.svelte';
  import { msg, PERM_GROUPS } from '$lib/ws/util';
  import State from '$lib/components/admin/State.svelte';

  const oid = $derived(page.params.oid ?? '');
  let deny = $state<string[]>([]), limitModels = $state(false), allowed = $state<string[]>([]), models = $state<any[]>([]), autoJoin = $state(false);
  let q = $state<Record<string, string>>({}), ret = $state<Record<string, string>>({}), busy = $state(false), error = $state(''), loading = $state(true);
  const QUOTAS: [string, string, string][] = [['storage_mb', 'Storage for the whole organisation (MB)', 'No limit'], ['max_upload_mb', 'Largest single upload (MB)', '100'], ['workspaces', 'Workspaces', 'No limit'],
    ['contexts', 'Contexts', 'No limit'], ['agents', 'Agents', 'No limit'], ['agent_runs_per_day', 'Agent tasks per day', 'No limit']];
  const RET: [string, string, string][] = [['trash_days', 'Keep deleted files for (days)', '30'], ['keep_versions', 'Keep this many versions per file', 'All'], ['job_days', 'Keep finished background work for (days)', '30'],
    ['access_log_days', 'Keep file access records for (days)', '365']];
  async function load() {
    const o = org.o; if (!o) return;
    deny = [...(o.policy?.deny ?? [])]; limitModels = Array.isArray(o.policy?.models); allowed = [...(o.policy?.models ?? [])]; autoJoin = o.auto_join;
    q = Object.fromEntries(QUOTAS.map(([k]) => [k, o.quotas?.[k] ?? ''])); ret = Object.fromEntries(RET.map(([k]) => [k, o.policy?.[k] ?? '']));
    try { if (ocan('org.models.manage')) models = await get(`/api/orgs/${oid}/models`); error = ''; } catch (e) { error = msg(e); } finally { loading = false; }
  }
  $effect(() => { org.o?.id; load(); });
  const num = (v: string) => (v === '' || v === null ? null : Number(v));
  async function save() {
    busy = true;
    const policy: Record<string, unknown> = { ...(org.o.policy ?? {}), deny, models: limitModels ? allowed : null };
    for (const [k] of RET) policy[k] = num(ret[k]) ?? undefined;
    const quotas: Record<string, unknown> = { ...(org.o.quotas ?? {}) };
    for (const [k] of QUOTAS) quotas[k] = num(q[k]);
    try { await patch(`/api/orgs/${oid}`, { policy, quotas, ...(org.o.is_default ? { auto_join: autoJoin } : {}) }); toast('Saved'); await reloadOrg(); await load(); } catch (e) { toast(msg(e), 'bad'); } finally { busy = false; }
  }
  const grouped = $derived(PERM_GROUPS.map((g) => [g, org.perms.filter((p) => p.group === g && !p.id.startsWith('org.') && p.id !== 'ws.view')] as [string, any[]]).filter(([, l]) => l.length));
</script>
<State {loading} {error} />
{#if !loading && org.o}
<form onsubmit={(e) => { e.preventDefault(); save(); }}>
  <section class="panel" aria-label="Switched off"><div class="panel-h"><h3>Switched off for the whole organisation</h3><span class="small muted">Nobody here can do these things, whatever their role.</span></div><div class="panel-b">
    <div class="cols3">{#each grouped as [g, list]}<fieldset class="field" style="border:0;padding:0"><legend class="label">{g}</legend>{#each list as p}<label class="row"><input type="checkbox" value={p.id} bind:group={deny} disabled={!ocan('org.policy.edit')} /><span>{p.label}</span></label>{/each}</fieldset>{/each}</div></div></section>

  <section class="panel" style="margin-top:16px" aria-label="Models"><div class="panel-h"><h3>Language models</h3></div><div class="panel-b">
    <label class="row"><input type="radio" bind:group={limitModels} value={false} /><span>Every model the server offers this person</span></label>
    <label class="row"><input type="radio" bind:group={limitModels} value={true} /><span>Only these</span></label>
    {#if limitModels}<div class="pickbox">{#each models as m}<label class="row"><input type="checkbox" value={m.id} bind:group={allowed} />{m.alias} <span class="muted tiny">{m.capabilities.join(', ')}</span></label>{:else}<span class="muted small">No models are enabled on this server.</span>{/each}</div>{/if}
    <p class="tiny muted" style="margin-top:8px">A workspace can narrow this further. People still need permission to use a model on this server.</p></div></section>

  <div class="cols" style="margin-top:16px">
    <section class="panel" aria-label="Limits"><div class="panel-h"><h3>Limits</h3></div><div class="panel-b">{#each QUOTAS as [k, l, ph]}<div class="field"><label for="q-{k}">{l}</label><input id="q-{k}" class="input" type="number" min="1" placeholder={ph} bind:value={q[k]} /></div>{/each}</div></section>
    <section class="panel" aria-label="Retention"><div class="panel-h"><h3>How long things are kept</h3></div><div class="panel-b">{#each RET as [k, l, ph]}<div class="field"><label for="r-{k}">{l}</label><input id="r-{k}" class="input" type="number" min="1" placeholder={ph} bind:value={ret[k]} /></div>{/each}
      <p class="tiny muted">Cleaned up automatically by the server's regular retention sweep. Deleted files are removed for good after their period; older versions beyond the limit are removed; the current version is never touched.</p></div></section>
  </div>
  {#if org.o.is_default}<label class="row" style="margin-top:16px"><input type="checkbox" bind:checked={autoJoin} /><span>New accounts join this organisation automatically <span class="muted">(single-company servers; switched off by itself as soon as a second organisation exists)</span></span></label>{/if}
  {#if ocan('org.policy.edit')}<div style="margin-top:16px"><button class="btn primary" disabled={busy}>Save</button></div>{/if}
</form>
{/if}
<style>
  .cols { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; align-items: start; } .cols3 { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 0 24px; }
  .pickbox { max-height: 30vh; overflow: auto; display: grid; gap: 2px; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px; margin-top: 8px; } @media (max-width: 860px) { .cols { grid-template-columns: minmax(0, 1fr); } }
</style>
