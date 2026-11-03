<script lang="ts">
  // The session is waiting for a yes or no on one action. Nothing runs until it is answered.
  import { answerApproval, problem } from '$lib/work/client';
  import type { Item } from '$lib/work/events';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';

  let { item, sessionId, onanswered }: { item: Extract<Item, { type: 'approval' }>; sessionId: string; onanswered: () => void } = $props();
  const d = $derived(item.data);
  let busy = $state<'' | 'approve' | 'deny'>('');
  const pending = $derived(d.status === 'pending');
  const done: Record<string, [string, string]> = { approved: ['Approved', 'ok'], denied: ['Denied', 'bad'], expired: ['No longer waiting', ''] };

  async function answer(approve: boolean) {
    busy = approve ? 'approve' : 'deny';
    try { await answerApproval(sessionId, d.step, approve); onanswered(); }
    catch (e) { toast(problem(e).message, 'bad'); }
    finally { busy = ''; }
  }
</script>

<div class="appr" class:pending role="group" aria-label={`Approval for ${d.tool}`}>
  <div class="top">
    <Icon name="approve" size={16} />
    <strong>{pending ? 'Needs your approval' : 'Approval'}</strong>
    <span class="name mono">{d.tool}</span>
    <span class="spacer"></span>
    {#if !pending}{@const x = done[d.status] ?? [d.status, '']}<span class="chip {x[1]}">{x[0]}</span>{/if}
  </div>
  {#if item.summary}<p class="small sum">{item.summary}</p>{/if}
  {#if d.command}<pre>{d.command}</pre>{/if}
  {#if d.reason}<p class="small muted">Why it asks: {d.reason}</p>{/if}
  {#if pending}
    <div class="row">
      <button class="btn primary" disabled={!!busy} onclick={() => answer(true)}>{busy === 'approve' ? 'Approving…' : 'Approve'}</button>
      <button class="btn" disabled={!!busy} onclick={() => answer(false)}>{busy === 'deny' ? 'Denying…' : 'Deny'}</button>
      <span class="tiny muted">The session stays paused on this step until you answer.</span>
    </div>
  {/if}
</div>

<style>
  .appr { border: 1px solid var(--border); border-radius: var(--radius); padding: var(--s3); background: var(--surface); display: grid; gap: 8px; }
  .appr.pending { border-color: var(--warn); background: var(--warn-soft); }
  .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
  .name { font-size: 12.5px; color: var(--fg-2); }
  .sum { margin: 0; }
  pre { margin: 0; padding: 8px 10px; background: var(--bg); border: 1px solid var(--border); border-radius: var(--radius-sm); font: 12.5px/1.5 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; max-height: 200px; overflow: auto; }
</style>
