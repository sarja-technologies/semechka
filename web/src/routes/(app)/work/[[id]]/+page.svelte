<script lang="ts">
  // Work Mode: the list of sessions on the left, the open session on the right. On a phone they are two screens.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { work, loadOptions, loadSessions, removeSession } from '$lib/work/store.svelte';
  import { deleteSession, problem } from '$lib/work/client';
  import type { WorkSession } from '$lib/work/types';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import SessionList from '$lib/components/work/SessionList.svelte';
  import SessionView from '$lib/components/work/SessionView.svelte';
  import NewSession from '$lib/components/work/NewSession.svelte';

  const op = $derived(app.me?.ui?.operations?.work);
  const id = $derived(page.params.id ?? null);
  let now = $state(Date.now());
  let newOpen = $state(false), delRow = $state<WorkSession | null>(null), delOpen = $state(false), delBusy = $state(false);
  $effect(() => { if (!delOpen) delRow = null; });

  onMount(() => {
    if (!op?.enabled) return;
    loadOptions(); loadSessions();
    const tick = setInterval(() => { now = Date.now(); }, 30000);
    const poll = setInterval(() => { if (!document.hidden) loadSessions(); }, 15000);
    const vis = () => { if (!document.hidden) loadSessions(); };
    document.addEventListener('visibilitychange', vis);
    return () => { clearInterval(tick); clearInterval(poll); document.removeEventListener('visibilitychange', vis); };
  });
  // asked to start a new session from elsewhere (?new=1)
  $effect(() => { if (page.url.searchParams.has('new') && op?.enabled) { newOpen = true; goto(page.url.pathname, { replaceState: true }); } });

  async function remove() {
    if (!delRow) return;
    delBusy = true;
    const row = delRow;
    try { await deleteSession(row.id); removeSession(row.id); delOpen = false; toast('Session deleted'); if (row.id === id) goto('/work'); }
    catch (e) { const p = problem(e); if (p.kind === 'notfound') { removeSession(row.id); delOpen = false; if (row.id === id) goto('/work'); } else toast(p.message, 'bad'); }
    finally { delBusy = false; }
  }
  const denied = $derived(work.optionsError?.kind === 'denied');
</script>

<svelte:head><title>Work</title></svelte:head>

{#if !op?.enabled}
  <div class="gate"><h1>Work</h1><div class="notice warn" role="status"><div><strong>Work Mode is not available to you.</strong> {op?.reason || 'It is turned off, or your account does not have permission to use it. If you think you should have access, ask your administrator.'}</div></div><a class="btn" href="/chat">Go to chat</a></div>
{:else if denied}
  <div class="gate"><h1>Work</h1><div class="notice warn" role="alert"><div><strong>You do not have access to Work Mode.</strong> {work.optionsError?.message}</div></div></div>
{:else}
  <div class="work">
    <aside class="list" class:away={!!id} aria-label="Sessions">
      <div class="lh">
        <h1>Work</h1>
        <a class="btn ghost sm" href="/work/skills"><Icon name="knowledge" size={14} /> Skills</a>
        <button class="btn primary sm" onclick={() => (newOpen = true)}><Icon name="plus" size={14} /> New session</button>
      </div>
      <div class="ls">
        <SessionList sessions={work.sessions} activeId={id} loaded={work.sessionsLoaded} error={work.sessionsError} {now} onretry={loadSessions} ondelete={(s) => { delRow = s; delOpen = true; }} />
      </div>
    </aside>
    <section class="view" class:away={!id} aria-label="Session">
      {#if id}
        {#key id}<SessionView {id} {now} />{/key}
      {:else}
        <div class="intro">
          <h2>Give the model a task and a workspace of its own</h2>
          <p class="muted">A session runs in an isolated container with its own files. The model can run commands, write code, make pictures and videos through the engine, and hand back files. It keeps working across refreshes and restarts.</p>
          {#if work.optionsError && work.optionsError.kind !== 'denied'}<div class="notice bad" role="alert"><div>{work.optionsError.message} <button class="btn sm" onclick={() => loadOptions(true)}>Try again</button></div></div>{/if}
          <button class="btn primary" onclick={() => (newOpen = true)}><Icon name="plus" size={16} /> New session</button>
        </div>
      {/if}
    </section>
  </div>
{/if}

<NewSession bind:open={newOpen} options={work.options} />
<Modal bind:open={delOpen} title="Delete this session?">
  <p>“{delRow?.title || 'Untitled session'}” will be deleted together with its conversation, its container and every file in its workspace. Artifacts you saved to your Library stay there. This cannot be undone.</p>
  {#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" disabled={delBusy} onclick={remove}>{delBusy ? 'Deleting…' : 'Delete session'}</button>{/snippet}
</Modal>

<style>
  .gate { padding: var(--s5); max-width: 640px; } .gate h1 { margin-bottom: var(--s3); }
  .work { flex: 1; min-height: 0; display: grid; grid-template-columns: 264px minmax(0, 1fr); }
  .list { border-right: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; background: var(--bg); }
  .lh { display: flex; align-items: center; gap: 6px; padding: var(--s3); flex-wrap: wrap; } .lh h1 { flex: 1; font-size: 18px; }
  .ls { flex: 1; min-height: 0; overflow: auto; padding-bottom: var(--s3); }
  .view { display: flex; flex-direction: column; min-height: 0; min-width: 0; }
  .intro { padding: var(--s6) var(--s5); max-width: 560px; display: grid; gap: var(--s3); justify-items: start; }
  .intro h2 { font-size: 20px; }
  @media (max-width: 899px) {
    .work { grid-template-columns: minmax(0, 1fr); }
    .list.away, .view.away { display: none; }
    .list { border-right: 0; }
  }
</style>
