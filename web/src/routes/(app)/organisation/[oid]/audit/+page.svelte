<script lang="ts">
  import { scrollable } from '$lib/ws/scrollable';
  // The organisation's activity log: who changed access, membership, policy and agents, and when. It records that
  // things happened, never what was in anyone's files or conversations.
  import { page } from '$app/state';
  import { get, qs } from '$lib/api';
  import { org, pname } from '$lib/ws/org.svelte';
  import { msg, ago } from '$lib/ws/util';
  import State from '$lib/components/admin/State.svelte';
  const oid = $derived(page.params.oid ?? '');
  let ev = $state<any[]>([]), loading = $state(true), error = $state(''), f = $state(''), limit = $state(100);
  async function load() { try { ev = await get(`/api/orgs/${oid}/audit${qs({ limit })}`); error = ''; } catch (e) { error = msg(e); } finally { loading = false; } }
  $effect(() => { oid; limit; load(); });
  const rows = $derived(ev.filter((e) => !f || e.action.includes(f.toLowerCase()) || pname(e.actor).toLowerCase().includes(f.toLowerCase())));
  const brief = (d: any) => Object.entries(d ?? {}).filter(([, v]) => v !== '' && v !== null && typeof v !== 'object').slice(0, 4).map(([k, v]) => `${k}: ${String(v).slice(0, 40)}`).join(' · ');
</script>
<div class="row" style="margin-bottom:12px"><h2 class="grow">Activity log</h2><label class="sr-only" for="af">Filter</label><input id="af" class="input" style="max-width:260px" placeholder="Filter by action or person" bind:value={f} /></div>
<State {loading} {error} retry={load} />
{#if !loading && !error}
  <div class="tablewrap" use:scrollable><table class="t"><caption class="sr-only">Organisation activity</caption><thead><tr><th>When</th><th>Who</th><th>What</th><th>About</th></tr></thead>
    <tbody>{#each rows as e (e.id)}<tr><td class="muted" title={new Date(e.ts * 1000).toLocaleString()}>{ago(e.ts)}</td><td>{e.actor?.startsWith('u_') ? pname(e.actor) : (e.actor || 'System')}</td><td><code>{e.action}</code></td><td class="wrap small muted">{brief(e.detail)}</td></tr>
      {:else}<tr><td colspan="4" class="muted">Nothing matches.</td></tr>{/each}</tbody></table></div>
  {#if ev.length >= limit}<p style="margin-top:8px"><button class="btn sm" onclick={() => (limit += 200)}>Show older</button></p>{/if}
{/if}
