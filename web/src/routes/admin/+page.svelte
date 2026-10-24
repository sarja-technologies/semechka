<script lang="ts">
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg, fmtTime } from '$lib/components/admin/util';
  let o = $state<any>(null); let error = $state(''); let loading = $state(true);
  async function load() { loading = true; error = ''; try { o = await get('/api/admin/overview'); } catch (e) { error = errMsg(e); } loading = false; }
  onMount(load);
</script>
<Page title="Overview" lead="Real counts from this deployment. Anything the server cannot measure is marked unavailable.">
  <State {loading} {error} retry={load} />
  {#if o}
    {#each o.setup_hints as h}<div class="notice info"><div class="grow">{h.text}</div><a class="btn sm" href={h.link}>Open</a></div>{/each}
    <div class="grid">
      <section class="panel"><div class="panel-h"><h3>People</h3></div><div class="panel-b">{#if o.people}<dl class="kv"><dt>Accounts</dt><dd>{o.people.total}</dd><dt>Waiting for approval</dt><dd>{o.people.pending_approval}</dd></dl><p style="margin:12px 0 0"><a href="/admin/people">Manage people</a></p>{:else}<span class="chip">Not permitted</span>{/if}</div></section>
      <section class="panel"><div class="panel-h"><h3>Models</h3></div><div class="panel-b"><dl class="kv"><dt>Enabled</dt><dd>{o.models.enabled}</dd><dt>Discovered</dt><dd>{o.models.discovered}</dd><dt>Backends</dt><dd>{o.backends}</dd></dl><p style="margin:12px 0 0"><a href="/admin/models">Manage models</a></p></div></section>
      <section class="panel"><div class="panel-h"><h3>Last 24 hours</h3></div><div class="panel-b"><dl class="kv"><dt>Requests</dt><dd>{o.usage_24h.requests}</dd><dt>Errors</dt><dd>{o.usage_24h.errors}</dd></dl></div></section>
      <section class="panel"><div class="panel-h"><h3>Access</h3></div><div class="panel-b"><dl class="kv"><dt>Registration</dt><dd>{o.signup_mode}</dd><dt>Exposure setting</dt><dd>{o.exposure}</dd><dt>Listening on</dt><dd>{o.bound.host}:{o.bound.port}</dd></dl></div></section>
    </div>
    {#if o.audit}
      <section class="panel" style="margin-top:16px"><div class="panel-h"><h3>Audit log</h3><span class="chip" class:ok={o.audit.ok} class:bad={!o.audit.ok}>{o.audit.ok ? `Chain intact (${o.audit.checked} events)` : `Chain broken at event ${o.audit.broken_at}`}</span></div>
        <div class="tablewrap" style="border:0;border-radius:0"><table class="t"><caption class="sr-only">Recent audit events</caption><thead><tr><th>When</th><th>Action</th><th>Target</th></tr></thead><tbody>
          {#each o.recent_audit as e}<tr><td>{fmtTime(e.ts)}</td><td class="mono">{e.action}</td><td class="mono">{e.target || '—'}</td></tr>{:else}<tr><td colspan="3" class="muted">No events yet.</td></tr>{/each}</tbody></table></div></section>
    {:else}<p class="muted">Audit status is hidden: you do not have the audit permission.</p>{/if}
  {/if}
</Page>
<style>.grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }</style>
