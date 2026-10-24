<script lang="ts">
  import { onMount } from 'svelte';
  import { get, post } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Page from '$lib/components/admin/Page.svelte';
  import State from '$lib/components/admin/State.svelte';
  import { errMsg } from '$lib/components/admin/util';

  let data = $state<any>(null), error = $state(''), loading = $state(true), at = $state(''), paused = $state(false);
  let confirm = $state<null | { ticket: string; running: boolean; user: string }>(null), confirmOpen = $state(false);
  $effect(() => { if (!confirmOpen) confirm = null; });
  $effect(() => { confirmOpen = confirm !== null; });

  async function load() {
    try { data = await get('/api/queue/admin'); error = ''; at = new Date().toLocaleTimeString(); } catch (e) { error = errMsg(e); }
    loading = false;
  }
  onMount(() => {
    load();
    const t = setInterval(() => { if (!paused && !document.hidden) load(); }, 2000);
    return () => clearInterval(t);
  });
  async function promote(ticket: string) { try { await post(`/api/queue/tickets/${ticket}/promote`); toast('Moved to the front'); load(); } catch (e) { toast(errMsg(e), 'bad'); load(); } }
  async function cancel() {
    if (!confirm) return; const c = confirm; confirm = null;
    try { await post(`/api/queue/tickets/${c.ticket}/cancel`); toast(c.running ? 'Asked the request to stop' : 'Removed from the queue'); } catch (e) { toast(errMsg(e), 'bad'); }
    load();
  }
  const secs = (n?: number | null) => (n == null ? '—' : `${n.toFixed(1)} s`);
  const busy = $derived((data?.backends ?? []).some((b: any) => b.running.length || b.waiting.length));
</script>

<Page title="Queue" lead="Live requests per backend. Each backend works on a fixed number of replies at once; the rest wait here, ordered by role priority, then by who is using the least, then by arrival. The numbers are read from the running scheduler.">
  {#snippet actions()}
    <label class="small row" style="gap:6px"><input type="checkbox" bind:checked={paused} /> Pause updates</label>
    <button class="btn" onclick={load}>Refresh</button>{#if at}<span class="muted small">Read at {at}</span>{/if}
  {/snippet}
  <State loading={loading && !data} {error} retry={load} />
  {#if data}
    {#if !data.backends.length}
      <div class="notice info"><div>Nothing has used a backend since this service started, so there is no queue state yet. Limits are set under <a href="/admin/quotas">Quotas</a>.</div></div>
    {:else if !busy}
      <p class="muted" role="status">No request is running or waiting right now.</p>
    {/if}
    <div class="stack">
      {#each data.backends as b (b.backend_id)}
        <section class="panel" aria-label={`Queue for ${b.backend}`}>
          <div class="panel-h">
            <h3>{b.backend}</h3>
            <div class="row">
              <span class="chip {b.running.length >= b.limit ? 'warn' : 'ok'}">{b.running.length} of {b.limit} running</span>
              <span class="chip">{b.waiting.length} waiting</span>
              <span class="chip mono" title="Replies completed since this service started">{b.served} served</span>
            </div>
          </div>
          <div class="panel-b">
            <p class="small muted" style="margin-bottom:12px">
              Per-person limit while others wait: {b.user_limit}.
              {#if b.recent_wait_avg_s != null}Waits over the last {b.recent_samples} requests: average {secs(b.recent_wait_avg_s)}, longest {secs(b.recent_wait_max_s)}.{:else}No requests have finished waiting yet.{/if}
            </p>
            <div class="tablewrap"><table class="t">
              <caption class="sr-only">Requests on {b.backend}</caption>
              <thead><tr><th scope="col">State</th><th scope="col">#</th><th scope="col">Person</th><th scope="col">Kind</th><th scope="col">Model</th><th scope="col" class="num">Waited</th><th scope="col" class="num">Running</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
              <tbody>
                {#each b.running as r (r.ticket)}
                  <tr><td><span class="chip ok">Running</span></td><td>—</td><td>{r.user}</td><td>{r.kind}</td><td>{r.label}</td><td class="num">{secs(r.waited_s)}</td><td class="num">{secs(r.running_s)}</td>
                    <td>{#if data.can_cancel}<button class="btn sm danger" onclick={() => (confirm = { ticket: r.ticket, running: true, user: r.user })}>Stop</button>{/if}</td></tr>
                {/each}
                {#each b.waiting as r (r.ticket)}
                  <tr><td><span class="chip info">Waiting</span>{#if r.boost}<span class="chip warn" style="margin-left:4px">Promoted</span>{/if}</td><td class="num">{r.position}</td><td>{r.user}</td><td>{r.kind}</td><td>{r.label}</td><td class="num">{secs(r.waited_s)}</td><td class="num">—</td>
                    <td><div class="row" style="gap:4px;flex-wrap:nowrap">
                      {#if data.can_promote && r.position > 1}<button class="btn sm" onclick={() => promote(r.ticket)}>Move to front</button>{/if}
                      {#if data.can_cancel}<button class="btn sm danger" onclick={() => (confirm = { ticket: r.ticket, running: false, user: r.user })}>Cancel</button>{/if}
                    </div></td></tr>
                {/each}
                {#if !b.running.length && !b.waiting.length}<tr><td colspan="8" class="muted">Idle</td></tr>{/if}
              </tbody>
            </table></div>
          </div>
        </section>
      {/each}
    </div>
    {#if !data.can_cancel && !data.can_promote}<p class="hint" style="margin-top:12px">You can watch the queue. Cancelling or reordering needs the “cancel other people’s jobs” or “change queue priority” permission.</p>{/if}
    <p class="hint" style="margin-top:12px">Stopping a running reply ends it at its next token; the person sees that an administrator stopped it. Request text is never shown here.</p>
  {/if}
</Page>

<Modal bind:open={confirmOpen} title={confirm?.running ? 'Stop this reply?' : 'Remove from the queue?'}>
  <p>{confirm?.running ? `${confirm?.user}'s reply is being written. It will stop and keep the part already written.` : `${confirm?.user}'s request has not started. They will see that an administrator cancelled it.`} This is recorded in the audit log.</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirm = null)}>Keep it</button><button class="btn danger solid" onclick={cancel}>{confirm?.running ? 'Stop reply' : 'Cancel request'}</button>{/snippet}
</Modal>
