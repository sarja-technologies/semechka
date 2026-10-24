<script lang="ts">
  // Chooses an assistant and knowledge collections for a conversation. Renders nothing when neither mode is available.
  import { onMount } from 'svelte';
  import { get } from '$lib/api';
  import { app } from '$lib/session.svelte';
  import Modal from './Modal.svelte';
  import Icon from './Icon.svelte';
  let { assistantId = $bindable(null), collectionIds = $bindable([]), disabled = false, onchange }:
    { assistantId: string | null; collectionIds: string[]; disabled?: boolean; onchange?: () => void } = $props();
  const modes = $derived(app.me?.ui.modes ?? {});
  const canA = $derived(!!modes.agents?.enabled), canK = $derived(!!modes.knowledge?.enabled);
  let assistants = $state<any[]>([]), collections = $state<any[]>([]), open = $state(false), error = $state('');
  const assistant = $derived(assistants.find((a) => a.id === assistantId));
  const fromAssistant = $derived(new Set<string>(assistant?.collection_ids ?? []));
  const chosen = $derived(new Set<string>([...collectionIds, ...fromAssistant]));
  onMount(async () => {
    try {
      if (canA) assistants = await get('/api/assistants');
      if (canK) collections = await get('/api/knowledge/collections');
    } catch { error = 'Could not load assistants or collections.'; }
  });
  function toggle(id: string, on: boolean) {
    collectionIds = on ? [...new Set([...collectionIds, id])] : collectionIds.filter((x) => x !== id);
    onchange?.();
  }
</script>
{#if canA || canK}
  <div class="ctx row" style="gap:8px">
    {#if canA}
      <label class="sr-only" for="asst">Assistant</label>
      <select id="asst" class="input asel" bind:value={assistantId} {disabled} onchange={() => onchange?.()}>
        <option value={null}>No assistant</option>
        {#each assistants as a}<option value={a.id}>{a.name}{a.visibility !== 'private' ? ` (${a.visibility === 'team' ? a.team : 'everyone'})` : ''}</option>{/each}
      </select>
    {/if}
    {#if canK}
      <button class="btn sm" {disabled} onclick={() => (open = true)} aria-haspopup="dialog"><Icon name="knowledge" size={15} /> Knowledge{chosen.size ? ` (${chosen.size})` : ''}</button>
    {/if}
    {#if error}<span class="err" role="status">{error}</span>{/if}
  </div>
  <Modal bind:open title="Knowledge for this conversation">
    <p class="small muted">Before each reply, Semechka searches the selected collections for passages containing your words and gives the best matches to the model. This is <strong>keyword search</strong>: it does not understand meaning or synonyms.</p>
    {#if !collections.length}
      <p>You have no collections yet. <a href="/knowledge">Create one</a> and upload documents.</p>
    {:else}
      <ul class="clist">
        {#each collections as c (c.id)}
          <li><label class="row" style="gap:8px"><input type="checkbox" checked={chosen.has(c.id)} disabled={fromAssistant.has(c.id)} onchange={(e) => toggle(c.id, e.currentTarget.checked)} />
            <span class="grow"><strong>{c.name}</strong> <span class="muted small">{c.documents} documents{c.team ? ` · team ${c.team}` : ''}{fromAssistant.has(c.id) ? ' · included by the assistant' : ''}</span></span></label></li>
        {/each}
      </ul>
    {/if}
    {#snippet footer()}<a class="btn" href="/knowledge">Manage collections</a><button class="btn primary" onclick={() => (open = false)}>Done</button>{/snippet}
  </Modal>
{/if}
<style>
  .asel { width: auto; min-width: 140px; max-width: 220px; }
  .clist { list-style: none; margin: 8px 0 0; padding: 0; display: grid; gap: 8px; }
  @media (max-width: 719px) { .asel { min-width: 0; flex: 1; } }
</style>
