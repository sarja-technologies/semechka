<script lang="ts">
  import { onMount } from 'svelte';
  import { get, put } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { can } from '$lib/session.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
  import { errMsg } from '$lib/components/admin/util';
  let fields = $state<{ id: string; label: string }[]>([]); let presets = $state<Record<string, string[]>>({}); let defaults = $state<Record<string, string[]>>({}); let loading = $state(true); let error = $state(''); let err = $state('');
  async function load() { try { const r = await get('/api/admin/tech-presets'); fields = r.fields; presets = JSON.parse(JSON.stringify(r.presets)); defaults = r.defaults; error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  const all = (n: string) => presets[n]?.includes('tech.*');
  function tog(n: string, id: string) { const l = presets[n].filter((x) => x !== 'tech.*'); presets[n] = l.includes(id) ? l.filter((x) => x !== id) : [...l, id]; }
  function toggleAll(n: string) { presets[n] = all(n) ? [] : ['tech.*']; }
  async function save() { err = ''; try { await put('/api/admin/settings/tech.presets', { value: presets }); toast('Presets saved.'); load(); } catch (e) { err = errMsg(e); } }
  const canEdit = $derived(can('admin.roles.manage'));
</script>
<Page title="Technical Visibility" lead="What each person may read about the models and machines behind their answers. Seeing information and operating infrastructure are different permissions; neither includes reading anyone’s conversations.">
  <State {loading} {error} retry={load} />
  <div class="cols">
    <section class="panel"><div class="panel-h"><h3>Read (technical information)</h3></div><div class="panel-b small"><p>The <code>tech.*</code> permissions. Shown only if the backend reports the value; otherwise the person sees “not reported”, never an estimate.</p><p>Grant them to a role, a team or one person on <a href="/admin/roles">Roles</a>, <a href="/admin/teams">Teams</a> or the person’s page.</p></div></section>
    <section class="panel"><div class="panel-h"><h3>Operate (infrastructure)</h3></div><div class="panel-b small"><p>The <code>ops.*</code> permissions: change generation settings, load or unload models, cancel others’ jobs. Never implied by reading.</p></div></section>
    <section class="panel"><div class="panel-h"><h3>Content</h3></div><div class="panel-b small"><p>No administrator screen in this release shows other people’s conversations, and no permission grants it. Technical visibility never discloses prompts, hidden system instructions, secrets or file paths.</p></div></section>
  </div>
  {#if fields.length}
  <h2 style="margin:24px 0 8px">Preset shortcuts</h2>
  <p class="muted">Presets are shortcuts used by the “Apply preset” buttons in the permission editor. They are not fixed tiers: a single person can hold any mix of fields.</p>
  <div class="tablewrap"><table class="t"><caption class="sr-only">Fields included in each preset</caption><thead><tr><th>Field</th>{#each Object.keys(presets) as n}<th class="tc">{n}</th>{/each}</tr></thead><tbody>
    <tr><td><strong>Every technical field</strong> <span class="mono tiny muted">tech.*</span></td>{#each Object.keys(presets) as n}<td class="tc"><input type="checkbox" aria-label="{n}: every technical field" checked={all(n)} disabled={!canEdit} onchange={() => toggleAll(n)} /></td>{/each}</tr>
    {#each fields as f (f.id)}<tr><td>{f.label} <span class="mono tiny muted">{f.id}</span></td>{#each Object.keys(presets) as n}<td class="tc"><input type="checkbox" aria-label="{n}: {f.label}" checked={all(n) || presets[n].includes(f.id)} disabled={!canEdit || all(n)} onchange={() => tog(n, f.id)} /></td>{/each}</tr>{/each}</tbody></table></div>
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#if canEdit}<div class="row" style="margin-top:12px"><button class="btn primary" onclick={save}>Save presets</button><button class="btn" onclick={() => (presets = JSON.parse(JSON.stringify(defaults)))}>Restore defaults</button></div>{:else}<p class="hint">Editing presets needs the roles permission.</p>{/if}
  {/if}
  <h2 style="margin:28px 0 0">Details drawer</h2>
  <SettingsPanel keys={['tech.drawer_enabled']} searchable={false} />
</Page>
<style>.cols { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); } .tc { text-align: center; } code { font-size: 12px; }</style>
