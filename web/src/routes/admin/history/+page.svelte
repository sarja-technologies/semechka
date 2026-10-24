<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post, qs } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  let rows = $state<any[]>([]); let loading = $state(true); let error = $state(''); let key = $state('');
  async function load() { loading = true; try { rows = await get('/api/admin/settings-history' + qs({ key, limit: 200 })); error = ''; } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
  const v = (x: any) => (x === null ? 'inherited' : x?.v === undefined ? JSON.stringify(x) : typeof x.v === 'object' ? JSON.stringify(x.v) : String(x.v));
  async function rb(id: number) { try { await post(`/api/admin/settings-history/${id}/rollback`); toast('Rolled back.'); load(); } catch (e) { toast(errMsg(e), 'bad'); } }
</script>
<Page title="History & Rollback" lead="Every setting change with who made it. Rolling back sets the value to what it was before that change (recorded as a new change).">
  <div class="row" style="margin-bottom:12px"><label class="sr-only" for="hk">Filter by setting id</label><input id="hk" class="input mono" style="max-width:300px" placeholder="Filter by setting id, e.g. brand.app_name" bind:value={key} oninput={() => load()} /></div>
  <State {loading} {error} retry={load} empty={!loading && !error && !rows.length ? 'No changes recorded.' : ''} />
  {#if rows.length}<div class="tablewrap"><table class="t"><caption class="sr-only">Setting changes, newest first</caption><thead><tr><th>When</th><th>Setting</th><th>Scope</th><th>Before</th><th>After</th><th>By</th><th></th></tr></thead><tbody>
    {#each rows as r (r.id)}<tr><td>{fmtTime(r.ts)}</td><td class="mono tiny">{r.key}</td><td>{r.scope_type}{r.scope_id ? ' ' + r.scope_id.slice(0, 8) : ''}</td><td class="mono tiny" style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title={v(r.old)}>{v(r.old)}</td><td class="mono tiny" style="max-width:200px;overflow:hidden;text-overflow:ellipsis" title={v(r.new)}>{v(r.new)}</td><td class="mono tiny">{r.actor.slice(0, 10)}</td><td><button class="btn sm" onclick={() => rb(r.id)}>Roll back</button></td></tr>{/each}</tbody></table></div>{/if}
</Page>
