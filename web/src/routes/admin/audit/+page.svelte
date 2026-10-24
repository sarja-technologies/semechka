<script lang="ts">
  import { onMount } from 'svelte';
  import { get, qs } from '$lib/api';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import Pager from '$lib/components/admin/Pager.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  let rows = $state<any[]>([]); let total = $state(0); let pg = $state(1); const per = 50; let loading = $state(true); let error = $state(''); let action = $state(''); let ver = $state<any>(null); let open = $state<number | null>(null);
  async function load() { loading = true; try { const r = await get('/api/admin/audit' + qs({ action, page: pg, per_page: per })); rows = r.items; total = r.total; error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  let t: any; const filt = () => { clearTimeout(t); t = setTimeout(() => { pg = 1; load(); }, 250); };
  async function verify() { ver = null; try { ver = await get('/api/admin/audit/verify'); } catch (e) { error = errMsg(e); } }
</script>
<Page title="Activity / Audit" lead="Hash-chained record of administrative and security events. Message text and secrets are never written here.">
  {#snippet actions()}<button class="btn" onclick={verify}>Verify chain</button>{#if ver}<span class="chip {ver.ok ? 'ok' : 'bad'}" role="status">{ver.ok ? `Intact: ${ver.checked} events checked` : `Broken at event ${ver.broken_at}`}</span>{/if}{/snippet}
  <div class="row" style="margin-bottom:12px"><label class="sr-only" for="aa">Action prefix</label><input id="aa" class="input mono" style="max-width:300px" placeholder="Action prefix, e.g. setting. or user." bind:value={action} oninput={filt} /></div>
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No events match.' : ''} />
  {#if rows.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Audit events, newest first</caption><thead><tr><th>#</th><th>When</th><th>Who</th><th>Action</th><th>Target</th><th>From</th><th>Detail</th></tr></thead><tbody>
    {#each rows as e (e.id)}<tr><td class="num">{e.id}</td><td>{fmtTime(e.ts)}</td><td>{e.actor_label}</td><td class="mono tiny">{e.action}</td><td class="mono tiny">{e.target || '—'}</td><td class="mono tiny">{e.ip || '—'}</td>
      <td class="wrap" style="max-width:320px">{#if Object.keys(e.detail).length}<button class="btn sm ghost" aria-expanded={open === e.id} onclick={() => (open = open === e.id ? null : e.id)}>{open === e.id ? 'Hide' : 'Show'}</button>{#if open === e.id}<pre class="mono tiny" style="white-space:pre-wrap;margin:4px 0 0">{JSON.stringify(e.detail, null, 1)}</pre>{/if}{:else}—{/if}</td></tr>{/each}</tbody></table></div>{/if}
  <Pager bind:page={pg} perPage={per} {total} onchange={load} />
</Page>
