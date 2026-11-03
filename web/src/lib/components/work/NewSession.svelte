<script lang="ts">
  // Start a session. Only what GET /api/work/options allows is offered; the server checks everything again.
  import { goto } from '$app/navigation';
  import { createSession, problem } from '$lib/work/client';
  import { upsertSession, work, loadOptions } from '$lib/work/store.svelte';
  import { categoryLabel } from '$lib/work/skill';
  import type { WorkOptions } from '$lib/work/types';
  import { untrack } from 'svelte';
  import Modal from '$lib/components/Modal.svelte';

  let { open = $bindable(false), options }: { open: boolean; options: WorkOptions | null } = $props();

  const models = $derived(options?.models ?? []);
  const skills = $derived((options?.skills ?? []).filter((s) => s.enabled));
  const networkOffered = $derived(!!options?.network_allowed && !!options?.perms?.network);

  let title = $state(''), objective = $state(''), modelId = $state(''), skillIds = $state<string[]>([]);
  let autonomy = $state<'contained' | 'ask'>('contained'), internet = $state(false);
  let busy = $state(false), err = $state('');

  $effect(() => {
    if (!open) return;
    untrack(() => {
      title = ''; objective = ''; skillIds = []; autonomy = 'contained'; internet = false; err = ''; busy = false;
      if (!options) loadOptions();
    });
  });
  // pick a sensible default model once the list is known, without disturbing a choice already made
  $effect(() => { if (open && !models.some((m) => m.id === untrack(() => modelId))) modelId = (models.find((m) => m.ready !== false) ?? models[0])?.id ?? ''; });

  const chosen = $derived(models.find((m) => m.id === modelId));
  const canCreate = $derived(!!chosen && chosen.ready !== false && !busy);

  async function create() {
    err = ''; busy = true;
    try {
      const s = await createSession({
        title: title.trim() || undefined, objective: objective.trim() || undefined, model_id: modelId,
        skill_ids: skillIds, autonomy, network: networkOffered && internet ? 'internet' : 'none'
      });
      upsertSession(s);
      open = false;
      await goto(`/work/${s.id}`);
    } catch (e) { err = problem(e).message; }
    finally { busy = false; }
  }
</script>

<Modal bind:open title="New work session" wide>
  <form id="ns" onsubmit={(e) => { e.preventDefault(); if (canCreate) create(); }}>
    <div class="field"><label for="ns-t">Title (optional)</label><input id="ns-t" class="input" bind:value={title} maxlength="120" placeholder="For example: Clean the sales data" /></div>
    <div class="field"><label for="ns-o">What should it get done? (optional)</label>
      <textarea id="ns-o" class="input" rows="3" bind:value={objective} maxlength="4000" aria-describedby="ns-oh"></textarea>
      <span id="ns-oh" class="hint">The model reads this at the start of every run. You can also just describe the task in your first message.</span></div>

    <div class="field"><label for="ns-m">Model</label>
      {#if !options && !work.optionsError}
        <p class="muted small" role="status">Loading the models you can use…</p>
      {:else if !options}
        <p class="notice bad" role="alert"><span>{work.optionsError?.message} <button type="button" class="linkish" onclick={() => loadOptions(true)}>Try again</button></span></p>
      {:else if !models.length}
        <p class="notice warn" role="status"><span>No model available to you can use tools in Work Mode. Ask your administrator to enable one.</span></p>
      {:else}
        <select id="ns-m" class="input" bind:value={modelId} aria-describedby="ns-mh">
          {#each models as m (m.id)}<option value={m.id} disabled={m.ready === false}>{m.alias}{m.vision ? ' (reads images)' : ''}{m.ready === false ? ' (not ready)' : ''}</option>{/each}
        </select>
        <span id="ns-mh" class="hint">Only models you may use and that can call tools are listed. The session uses the Sarness Engine for every model it runs.</span>
      {/if}
    </div>

    {#if skills.length}
      <fieldset class="field">
        <legend class="label">Skills to attach (optional)</legend>
        <span class="hint">A skill is a set of written instructions the model can open when it needs them. They never change what the session is allowed to do.</span>
        <div class="skills">
          {#each skills as k (k.id)}
            <label class="row">
              <input type="checkbox" checked={skillIds.includes(k.id)} onchange={(e) => (skillIds = e.currentTarget.checked ? [...skillIds, k.id] : skillIds.filter((x) => x !== k.id))} />
              <span><strong>{k.title || k.name}</strong> <span class="chip">{categoryLabel(k.category)}</span><br /><span class="muted small">{k.description}</span></span>
            </label>
          {/each}
        </div>
      </fieldset>
    {/if}

    <fieldset class="field">
      <legend class="label">How much should it ask first?</legend>
      <label class="row"><input type="radio" name="aut" value="contained" bind:group={autonomy} /><span><strong>Work on its own</strong><br /><span class="muted small">It runs commands and writes files inside its container without asking. It still asks before anything that needs network access, pushing code, or deleting many files.</span></span></label>
      <label class="row"><input type="radio" name="aut" value="ask" bind:group={autonomy} /><span><strong>Ask before each action</strong><br /><span class="muted small">You approve every command, script, file write and git action. Slower, and you see everything first.</span></span></label>
    </fieldset>

    {#if networkOffered}
      <div class="field">
        <label class="row"><input type="checkbox" bind:checked={internet} aria-describedby="ns-nw" /><span><strong>Allow internet access</strong></span></label>
        <div id="ns-nw" class="notice warn" role="note"><span>With internet access the container can also reach other devices on your local network, and anything it downloads runs inside the session. Leave this off unless the task needs it. Without it the container has no network at all.</span></div>
      </div>
    {/if}
  </form>
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#snippet footer()}
    <button class="btn" type="button" onclick={() => (open = false)}>Cancel</button>
    <button class="btn primary" type="submit" form="ns" disabled={!canCreate}>{busy ? 'Creating…' : 'Create session'}</button>
  {/snippet}
</Modal>

<style>
  fieldset { border: 0; padding: 0; margin: 0 0 var(--s4); min-width: 0; }
  legend { padding: 0; margin-bottom: 4px; }
  .skills { display: grid; gap: 6px; max-height: 200px; overflow: auto; margin-top: 8px; border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px; }
  label.row { gap: 10px; margin: 6px 0; cursor: pointer; }
  .notice { margin: 8px 0 0; }
</style>
