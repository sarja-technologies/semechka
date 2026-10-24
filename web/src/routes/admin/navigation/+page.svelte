<script lang="ts">
  import { onMount } from 'svelte';
  import { get, put } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import SettingsPanel from '$lib/components/admin/SettingsPanel.svelte';
  import { errMsg } from '$lib/components/admin/util';
  import { loadRoles, type Opt } from '$lib/components/admin/lookup';
  let items = $state<any[]>([]); let orig = '[]'; let error = $state(''); let loading = $state(true); let msg = $state('');
  let roles = $state<Opt[]>([]); let role = $state('member'); let pv = $state<any>(null); let pvErr = $state('');
  async function load() {
    try { const r = await get('/api/admin/settings?q=navigation'); const s = r.settings.find((x: any) => x.id === 'nav.items'); items = JSON.parse(JSON.stringify(s.value)); orig = JSON.stringify(items); error = ''; } catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(async () => { load(); roles = await loadRoles(); preview(); });
  const dirty = $derived(JSON.stringify(items) !== orig);
  function move(i: number, d: number) { const j = i + d; if (j < 0 || j >= items.length) return; [items[i], items[j]] = [items[j], items[i]]; msg = `${items[j].label || items[j].id} moved to position ${j + 1}.`; }
  async function save() { try { await put('/api/admin/settings/nav.items', { value: items.map((i) => ({ id: i.id, label: i.label.trim(), enabled: i.enabled })) }); toast('Navigation saved.'); await load(); preview(); } catch (e) { error = errMsg(e); } }
  async function preview() { pvErr = ''; try { pv = await get(`/api/admin/preview/${role}`); } catch (e) { pvErr = errMsg(e); } }
  let drag = -1;
</script>
<Page title="Layout & Navigation" lead="Order, rename and hide navigation items. Hiding an item is presentation only: the server still checks permissions on every request.">
  <State {loading} {error} retry={load} />
  {#if items.length}
  <section class="panel"><div class="panel-h"><h3>Navigation items</h3><div class="row"><button class="btn primary sm" disabled={!dirty} onclick={save}>Save order</button>{#if dirty}<button class="btn sm" onclick={() => (items = JSON.parse(orig))}>Revert</button>{/if}</div></div>
    <div class="tablewrap" style="border:0"><table class="t"><caption class="sr-only">Navigation items in display order</caption><thead><tr><th>Move</th><th>Item</th><th>Label (blank = default)</th><th>Shown</th></tr></thead><tbody>
      {#each items as it, i (it.id)}
        <tr draggable="true" ondragstart={() => (drag = i)} ondragover={(e) => e.preventDefault()} ondrop={() => { if (drag >= 0 && drag !== i) { const [m] = items.splice(drag, 1); items.splice(i, 0, m); } drag = -1; }}>
          <td><button class="btn ghost icon sm" aria-label="Move {it.id} up" disabled={i === 0} onclick={() => move(i, -1)}><span style="transform:rotate(180deg);display:inline-flex"><Icon name="chevdown" size={14} /></span></button><button class="btn ghost icon sm" aria-label="Move {it.id} down" disabled={i === items.length - 1} onclick={() => move(i, 1)}><Icon name="chevdown" size={14} /></button></td>
          <td><span class="row" style="gap:8px"><Icon name={it.id} size={16} /> <span class="mono">{it.id}</span></span></td>
          <td><input class="input" aria-label="Label for {it.id}" maxlength="40" bind:value={it.label} style="max-width:220px" /></td>
          <td><input type="checkbox" aria-label="Show {it.id}" bind:checked={it.enabled} /></td></tr>
      {/each}</tbody></table></div>
    <p class="hint" style="padding:8px 16px;margin:0">Drag rows or use the arrow buttons. The page you land on after sign-in is set below. Per-role and per-team versions of the order can be set in All settings → Navigation items.</p>
    <div class="sr-only" role="status" aria-live="polite">{msg}</div>
  </section>{/if}
  <SettingsPanel keys={['nav.home', 'nav.sidebar_collapsed', 'nav.suggested_prompts']} searchable={false} />
  <h2 style="margin:28px 0 8px">View as role</h2>
  <p class="muted">Shows the navigation and the exact permissions the server enforces for a role, using only synthetic context. Team rules and per-person overrides are not applied; no one's messages are shown.</p>
  <div class="row"><label for="pr" class="label">Role</label><select id="pr" class="input" style="max-width:260px" bind:value={role} onchange={preview}>{#each roles as r}<option value={r.id}>{r.label}</option>{/each}</select></div>
  {#if pvErr}<p class="err">{pvErr}</p>{/if}
  {#if pv}<div class="pvg">
    <section class="panel"><div class="panel-h"><h3>Navigation this role sees</h3></div><div class="panel-b"><ul class="nv">{#each pv.ui.nav as n}<li><Icon name={n.id} size={16} /> {n.label} {#if !n.available}<span class="chip warn" title={n.reason}>{n.reason}</span>{/if}</li>{:else}<li class="muted">Nothing.</li>{/each}</ul><p class="hint">Starts at <code>{pv.ui.home}</code>.</p></div></section>
    <section class="panel"><div class="panel-h"><h3>Permissions enforced ({pv.permissions.length})</h3></div><div class="panel-b" style="max-height:260px;overflow:auto"><ul class="mono tiny" style="margin:0;padding-left:16px">{#each pv.permissions as p}<li>{p}</li>{/each}</ul></div></section>
  </div>{/if}
</Page>
<style>.pvg { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); margin-top: 12px; } .nv { list-style: none; margin: 0; padding: 0; display: grid; gap: 8px; } .nv li { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; } tr[draggable='true'] { cursor: grab; }</style>
