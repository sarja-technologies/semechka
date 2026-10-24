<script lang="ts">
  import { page } from '$app/state';
  import { app } from '$lib/session.svelte';
  const mode = $derived(page.params.mode ?? '');
  const st = $derived(app.me?.ui.modes?.[mode]);
  const label = $derived(app.me?.ui.nav.find((n: any) => n.id === mode)?.label ?? mode);
</script>
<div style="padding:24px;max-width:640px">
  <h1 style="margin-bottom:12px">{label}</h1>
  {#if st?.enabled}
    <div class="notice info">This section is available but has no page in this release.</div>
  {:else}
    <div class="notice warn" role="status"><div><strong>Not available.</strong> {st?.reason ?? 'This section does not exist or you do not have access.'}</div></div>
  {/if}
  <p class="small muted">Sections that are turned off or not permitted are never usable, even by direct request. If you think you should have access, ask your administrator.</p>
  <a class="btn" href="/chat">Go to chat</a>
</div>
