<script lang="ts">
  // What each part of the conversation interface shows, for everyone or per role and team. Display only: hiding
  // something never grants or removes a permission, and the server still checks every request.
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import Page from '$lib/components/admin/Page.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
  import { errMsg } from '$lib/components/admin/util';
  import { loadRoles, type Opt } from '$lib/components/admin/lookup';

  let roles = $state<Opt[]>([]), role = $state('member'), pv = $state<any>(null), err = $state(''), labels = $state<Record<string, string>>({});
  async function preview() { err = ''; try { pv = await get(`/api/admin/preview/${role}`); } catch (e) { err = errMsg(e); pv = null; } }
  onMount(() => {
    loadRoles().then((r) => (roles = r)).catch(() => {}); preview();
    get('/api/admin/settings?q=ui.').then((r) => (labels = Object.fromEntries(r.settings.filter((s: any) => s.id.startsWith('ui.')).map((s: any) => [s.id.slice(3), s.label])))).catch(() => { /* labels fall back to ids */ });
    window.addEventListener('semechka:settings', preview);
    return () => window.removeEventListener('semechka:settings', preview);
  });
  const flags = $derived<[string, boolean][]>(pv ? Object.entries(pv.ui.ui ?? {}) as [string, boolean][] : []);
  const ops = $derived<[string, any][]>(pv ? Object.entries(pv.ui.operations ?? {}) : []);
  const OPL: Record<string, string> = { text: 'Chat', code: 'Code', vision: 'Sending images', image: 'Pictures', video: 'Videos' };
</script>

<Page title="Interface" lead="Show or hide parts of the conversation interface for everyone, or differently per role and team. This is display only: the server checks permissions on every request whether or not a control is visible.">
  <div class="cols">
    <div><SettingsPanel categories={['Interface']} searchable={false} /></div>
    <aside class="panel side">
      <div class="panel-h"><h3>What a role sees</h3></div>
      <div class="panel-b stack">
        <div class="field"><label for="ir">Role</label><select id="ir" class="input" bind:value={role} onchange={preview}>{#each roles as r (r.id)}<option value={r.id}>{r.label}</option>{/each}</select>
          <span class="hint">Role and deployment values only; team rules and per-person overrides are not applied.</span></div>
        {#if err}<p class="err" role="alert">{err}</p>{/if}
        {#if pv}
          <div><strong class="small">Kinds of turn</strong>
            <ul class="fl">{#each ops as [k, o] (k)}<li><span class="chip {o.enabled ? 'ok' : ''}">{o.enabled ? 'Offered' : 'Not offered'}</span> {OPL[k] ?? k}{#if !o.enabled && o.reason}<span class="tiny muted"> · {o.reason}</span>{/if}</li>{/each}</ul></div>
          <div><strong class="small">Interface parts</strong>
            <ul class="fl">{#each flags as [k, v] (k)}<li><span class="chip {v ? 'ok' : ''}">{v ? 'Shown' : 'Hidden'}</span> {labels[k] ?? k}</li>{/each}</ul></div>
        {/if}
      </div>
    </aside>
  </div>
</Page>

<style>
  .cols { display: grid; grid-template-columns: minmax(0, 1fr) minmax(280px, 340px); gap: 20px; align-items: start; }
  .side { position: sticky; top: 16px; }
  .fl { list-style: none; margin: 6px 0 0; padding: 0; display: grid; gap: 4px; font-size: 13px; }
  .fl .chip { min-width: 74px; justify-content: center; }
  @media (max-width: 1000px) { .cols { grid-template-columns: minmax(0, 1fr); } .side { position: static; } }
</style>
