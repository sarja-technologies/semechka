<script lang="ts">
  import { onMount } from 'svelte';
  import { app, can } from '$lib/session.svelte';
  import { get, post, put, del, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  const mode = $derived(app.me.ui.modes.agents);
  const kmode = $derived(app.me.ui.modes.knowledge);
  const teams = $derived<{ id: string; name: string }[]>(app.me.teams ?? []);
  const msg = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened. Try again.');
  let rows = $state<any[]>([]), loading = $state(true), error = $state(''), models = $state<any[]>([]), cols = $state<any[]>([]);

  async function load() {
    try { rows = await get('/api/assistants'); error = ''; } catch (e) { error = msg(e); } finally { loading = false; }
  }
  onMount(async () => {
    if (!mode.enabled) { loading = false; return; }
    await load();
    try { models = await get('/api/chat/models?mode=agents'); } catch { /* the form explains an empty list */ }
    if (kmode.enabled) { try { cols = await get('/api/knowledge/collections'); } catch { /* optional */ } }
  });

  let open = $state(false), cur = $state<any>(null), err = $state('');
  let f = $state({ name: '', description: '', system_prompt: '', visibility: 'private', team_id: '', model_id: '', temperature: '', max_tokens: '', collection_ids: [] as string[] });
  const allowedCols = $derived(cols.filter((c) => f.visibility === 'private' || (f.visibility === 'team' && c.team_id === f.team_id)));
  async function edit(a: any | null) {
    err = '';
    if (a) {
      try { a = await get(`/api/assistants/${a.id}`); } catch (e) { toast(msg(e), 'bad'); return; }
    }
    cur = a;
    f = { name: a?.name ?? '', description: a?.description ?? '', system_prompt: a?.system_prompt ?? '', visibility: a?.visibility ?? 'private', team_id: a?.team_id ?? teams[0]?.id ?? '',
      model_id: a?.model_id ?? '', temperature: a?.params?.temperature != null ? String(a.params.temperature) : '', max_tokens: a?.params?.max_tokens != null ? String(a.params.max_tokens) : '', collection_ids: [...(a?.collection_ids ?? [])] };
    open = true;
  }
  async function save() {
    err = '';
    const params: Record<string, number> = {};
    if (f.temperature !== '') params.temperature = Number(f.temperature);
    if (f.max_tokens !== '') params.max_tokens = Number(f.max_tokens);
    const body = { name: f.name, description: f.description, system_prompt: f.system_prompt, visibility: f.visibility, team_id: f.visibility === 'team' ? f.team_id : null,
      model_id: f.model_id || null, params, collection_ids: f.visibility === 'org' ? [] : f.collection_ids.filter((id) => allowedCols.some((c) => c.id === id)) };
    try { if (cur) await put(`/api/assistants/${cur.id}`, body); else await post('/api/assistants', body); open = false; toast('Assistant saved'); load(); }
    catch (e) { err = msg(e); }
  }
  let delA = $state<any>(null), delOpen = $state(false);
  $effect(() => { if (!delOpen) delA = null; });
  async function remove() { if (!delA) return; try { await del(`/api/assistants/${delA.id}`); delOpen = false; toast('Assistant deleted'); load(); } catch (e) { toast(msg(e), 'bad'); } }
  const where = (a: any) => (a.visibility === 'org' ? 'Everyone' : a.visibility === 'team' ? `Team ${a.team}` : 'Only you');
</script>

<svelte:head><title>Assistants</title></svelte:head>
<div class="pg">
  <header class="hd"><h1 class="grow">Assistants</h1>{#if mode.enabled}<button class="btn primary" onclick={() => edit(null)}><Icon name="plus" /> New assistant</button>{/if}</header>
  {#if !mode.enabled}
    <div class="notice warn" role="status"><div><strong>Assistants are not available to you.</strong> {mode.reason}</div></div>
  {:else}
    <div class="notice info"><div class="small">
      <strong>What an assistant is.</strong> A saved set of instructions for the model, with an optional default model, settings and knowledge collections. Pick one in Chat or Code and every reply follows it.<br />
      <strong>What it is not.</strong> It has no tools. It cannot run code, open files, browse the web, send messages or act on its own. Autonomous agents are not part of this release.
    </div></div>
    {#if loading}<p class="muted" role="status">Loading…</p>
    {:else if error}<div class="notice bad" role="alert"><div>{error} <button class="btn sm" onclick={load}>Retry</button></div></div>
    {:else if !rows.length}<p class="muted">No assistants yet. Create one for a task you repeat, such as “review my pull requests” or “answer from our handbook”.</p>
    {:else}
      <div class="grid">
        {#each rows as a (a.id)}
          <article class="panel"><div class="panel-b">
            <h2>{a.name}</h2>
            <p class="muted small">{a.description || 'No description.'}</p>
            <div class="row" style="gap:6px;margin-bottom:12px"><span class="chip">{where(a)}</span>{#if a.collection_ids.length}<span class="chip info">{a.collection_ids.length} collection{a.collection_ids.length > 1 ? 's' : ''}</span>{/if}{#if a.mine}<span class="chip">Yours</span>{/if}</div>
            <div class="row" style="gap:6px">
              <a class="btn primary sm" href={`/chat?assistant=${a.id}`}>Start chat</a>
              {#if a.can_edit}<button class="btn sm" onclick={() => edit(a)}>Edit</button><button class="btn sm danger" onclick={() => { delA = a; delOpen = true; }}>Delete</button>{/if}
            </div>
          </div></article>
        {/each}
      </div>
    {/if}
  {/if}
</div>

<Modal bind:open title={cur ? `Edit ${cur.name}` : 'New assistant'} wide>
  <div class="field"><label for="an">Name</label><input id="an" class="input" bind:value={f.name} maxlength="100" /></div>
  <div class="field"><label for="ad">Description</label><input id="ad" class="input" bind:value={f.description} maxlength="500" /><span class="hint">Shown to everyone who can use it.</span></div>
  <div class="field"><label for="ap">Instructions for the model</label><textarea id="ap" class="input" rows="7" bind:value={f.system_prompt} maxlength="8000"></textarea>
    <span class="hint">Only people who can edit this assistant can read these instructions here. Anyone using it can still try to make the model repeat them, so do not put secrets in them.</span></div>
  <div class="field"><label for="av">Who can use it</label>
    <select id="av" class="input" bind:value={f.visibility}><option value="private">Only me</option>{#if teams.length}<option value="team">A team I belong to</option>{/if}{#if can('admin.teams.manage')}<option value="org">Everyone in the organisation</option>{/if}</select></div>
  {#if f.visibility === 'team'}<div class="field"><label for="at">Team</label><select id="at" class="input" bind:value={f.team_id}>{#each teams as t}<option value={t.id}>{t.name}</option>{/each}</select></div>{/if}
  <div class="field"><label for="am">Default model (optional)</label>
    <select id="am" class="input" bind:value={f.model_id}><option value="">No preference</option>{#each models as m}<option value={m.id}>{m.alias}</option>{/each}</select>
    <span class="hint">Used when someone starts a conversation with this assistant; they can still change it, and it only applies if they have access to that model.</span></div>
  <div class="row" style="align-items:flex-start">
    <div class="field grow"><label for="at1">Temperature (0–2, optional)</label><input id="at1" class="input" inputmode="decimal" bind:value={f.temperature} /></div>
    <div class="field grow"><label for="at2">Maximum reply tokens (optional)</label><input id="at2" class="input" inputmode="numeric" bind:value={f.max_tokens} /></div>
  </div>
  {#if kmode.enabled}
    <fieldset class="field" style="border:0;padding:0;margin:0 0 16px"><legend class="label">Knowledge collections</legend>
      {#if f.visibility === 'org'}<p class="hint">Assistants shared with everyone cannot use collections yet, because each collection has its own readers. Share with a team to use that team’s collections.</p>
      {:else if !allowedCols.length}<p class="hint">{f.visibility === 'team' ? 'This team has no collections. ' : 'You have no collections. '}<a href="/knowledge">Create one</a>.</p>
      {:else}{#each allowedCols as c (c.id)}<label class="row" style="gap:8px;margin:4px 0"><input type="checkbox" checked={f.collection_ids.includes(c.id)} onchange={(e) => (f.collection_ids = e.currentTarget.checked ? [...f.collection_ids, c.id] : f.collection_ids.filter((x) => x !== c.id))} /> {c.name} <span class="muted small">{c.scope === 'team' ? `team ${c.team}` : 'personal'}</span></label>{/each}
        <span class="hint">Searched by keyword before each reply.</span>{/if}
    </fieldset>
  {/if}
  {#if err}<p class="err" role="alert">{err}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (open = false)}>Cancel</button><button class="btn primary" disabled={!f.name.trim()} onclick={save}>Save assistant</button>{/snippet}
</Modal>
<Modal bind:open={delOpen} title="Delete this assistant?"><p>“{delA?.name}” will be deleted for everyone it is shared with. Conversations that used it will ask you to remove it before continuing.</p>
  {#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete assistant</button>{/snippet}</Modal>

<style>
  .pg { padding: 16px 24px 32px; max-width: 1100px; } .hd { display: flex; align-items: center; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; } h1 { font-size: 22px; }
  .grid { display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); } h2 { font-size: 16px; margin-bottom: 4px; overflow-wrap: anywhere; }
  @media (max-width: 719px) { .pg { padding: 12px; } }
</style>
