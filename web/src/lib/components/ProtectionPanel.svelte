<script lang="ts">
  import { get, qs, ApiError } from '$lib/api';
  let { modelId = '', ephemeral = false }: { modelId?: string; ephemeral?: boolean } = $props();
  let data = $state<any>(null), error = $state('');
  $effect(() => {
    const m = modelId, e = ephemeral; data = null; error = '';
    get(`/api/chat/protection${qs({ model_id: m || undefined, ephemeral: e || undefined })}`).then((d) => (data = d)).catch((x) => (error = x instanceof ApiError ? x.message : 'Could not load.'));
  });
  const chip = (s: string) => (s === 'ok' ? 'ok' : s === 'warn' ? 'warn' : 'info');
  const word = (s: string) => (s === 'ok' ? 'In place' : s === 'warn' ? 'Note' : 'Info');
</script>
{#if error}<div class="notice bad" role="alert">{error}</div>
{:else if !data}<p class="muted small" role="status">Loading…</p>
{:else}
  <p class="small" style="margin-bottom:12px">{data.summary}</p>
  <dl class="pl">
    {#each data.lines as l}
      <div class="r"><dt><span class="chip {chip(l.status)}">{word(l.status)}</span> <strong>{l.topic}</strong></dt><dd>{l.text}</dd></div>
    {/each}
  </dl>
  {#if data.organisation_notice}<div class="notice info"><div class="small" style="white-space:pre-wrap">{data.organisation_notice}</div></div>{/if}
{/if}
<style>
  .pl { margin: 0; display: grid; gap: 12px; } .r { display: grid; gap: 2px; } dt { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; } dd { margin: 0; color: var(--fg-muted); font-size: 13px; max-width: 72ch; }
</style>
