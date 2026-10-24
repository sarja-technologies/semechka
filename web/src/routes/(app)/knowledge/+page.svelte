<script lang="ts">
  import { onMount } from 'svelte';
  import { app } from '$lib/session.svelte';
  import { get, post, patch, del, upload, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';

  const mode = $derived(app.me.ui.modes.knowledge);
  const msg = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened. Try again.');
  let info = $state<any>(null), cols = $state<any[]>([]), loading = $state(true), error = $state('');
  let selId = $state<string | null>(null), docs = $state<any[]>([]), docsLoading = $state(false), docsError = $state('');
  let list = $state(false);
  const sel = $derived(cols.find((c) => c.id === selId) ?? null);

  async function loadCols(keep = true) {
    try { cols = await get('/api/knowledge/collections'); error = ''; if (!keep || !cols.find((c) => c.id === selId)) selId = cols[0]?.id ?? null; }
    catch (e) { error = msg(e); } finally { loading = false; }
  }
  async function loadDocs() {
    if (!selId) { docs = []; return; }
    docsLoading = true; docsError = '';
    try { docs = await get(`/api/knowledge/collections/${selId}/documents`); } catch (e) { docsError = msg(e); docs = []; } finally { docsLoading = false; }
  }
  onMount(async () => { if (!mode.enabled) { loading = false; return; } try { info = await get('/api/knowledge/info'); } catch (e) { error = msg(e); } await loadCols(false); });
  $effect(() => { selId; results = null; searchError = ''; if (mode.enabled) loadDocs(); });

  // ---- create / rename / delete collection ----
  let newOpen = $state(false), nName = $state(''), nDesc = $state(''), nScope = $state(''), nErr = $state('');
  async function create() {
    nErr = '';
    try { const c = await post('/api/knowledge/collections', { name: nName, description: nDesc, team_id: nScope || null }); newOpen = false; nName = nDesc = nScope = ''; await loadCols(); selId = c.id; list = false; }
    catch (e) { nErr = msg(e); }
  }
  let editOpen = $state(false), eName = $state(''), eDesc = $state(''), delOpen = $state(false), delDoc = $state<any>(null), delDocOpen = $state(false);
  $effect(() => { if (!delDocOpen) delDoc = null; });
  async function saveEdit() { if (!sel) return; try { await patch(`/api/knowledge/collections/${sel.id}`, { name: eName, description: eDesc }); editOpen = false; loadCols(); } catch (e) { toast(msg(e), 'bad'); } }
  async function removeCol() { if (!sel) return; try { await del(`/api/knowledge/collections/${sel.id}`); delOpen = false; toast('Collection deleted'); selId = null; await loadCols(false); } catch (e) { toast(msg(e), 'bad'); } }
  async function removeDoc() { if (!sel || !delDoc) return; try { await del(`/api/knowledge/collections/${sel.id}/documents/${delDoc.id}`); delDocOpen = false; toast('Document deleted'); loadDocs(); loadCols(); } catch (e) { toast(msg(e), 'bad'); } }

  // ---- upload ----
  let log = $state<{ name: string; ok: boolean; text: string }[]>([]), uploading = $state(false);
  async function pick(e: Event) {
    const inp = e.currentTarget as HTMLInputElement; const files = [...(inp.files ?? [])]; inp.value = '';
    if (!sel || !files.length) return;
    uploading = true; log = [];
    for (const f of files) {
      try { const d = await upload(`/api/knowledge/collections/${sel.id}/documents`, f); log.push({ name: f.name, ok: true, text: `${d.passages} passage${d.passages === 1 ? '' : 's'}` }); }
      catch (err) { log.push({ name: f.name, ok: false, text: msg(err) }); }
    }
    uploading = false; loadDocs(); loadCols();
  }

  // ---- try a search ----
  let q = $state(''), results = $state<any[] | null>(null), searching = $state(false), searchError = $state('');
  async function search() {
    if (!sel || !q.trim()) return; searching = true; searchError = '';
    try { results = (await post(`/api/knowledge/collections/${sel.id}/search`, { query: q })).results; } catch (e) { searchError = msg(e); results = null; } finally { searching = false; }
  }
  const kb = (n: number) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
</script>

<svelte:head><title>Knowledge</title></svelte:head>
{#if !mode.enabled}
  <div style="padding:24px;max-width:640px"><h1 style="margin-bottom:12px">Knowledge</h1><div class="notice warn" role="status"><div><strong>Knowledge is not available to you.</strong> {mode.reason}</div></div></div>
{:else}
<div class="kn">
  <aside class="side" class:open={list} aria-label="Collections">
    <div class="sh"><button class="btn primary grow" onclick={() => { nErr = ''; newOpen = true; }}><Icon name="plus" /> New collection</button><button class="btn ghost icon sm closeb" aria-label="Close list" onclick={() => (list = false)}><Icon name="x" /></button></div>
    {#if loading}<p class="muted small" role="status">Loading…</p>
    {:else if error}<div class="notice bad" role="alert"><div>{error} <button class="btn sm" onclick={() => loadCols()}>Retry</button></div></div>
    {:else if !cols.length}<p class="muted small">No collections yet. A collection is a set of documents you can attach to a conversation.</p>{/if}
    <ul>
      {#each cols as c (c.id)}
        <li><button class="ci" class:on={c.id === selId} aria-current={c.id === selId ? 'true' : undefined} onclick={() => { selId = c.id; list = false; }}>
          <span class="n">{c.name}</span><span class="tiny muted">{c.documents} document{c.documents === 1 ? '' : 's'} · {c.scope === 'team' ? `team ${c.team}` : 'personal'}</span></button></li>
      {/each}
    </ul>
  </aside>
  {#if list}<div class="scrim" role="presentation" onclick={() => (list = false)}></div>{/if}

  <section class="main" aria-label="Collection">
    <div class="top">
      <button class="btn ghost sm listb" onclick={() => (list = true)}><Icon name="list" size={16} /> Collections</button>
      <h1 class="grow">{sel ? sel.name : 'Knowledge'}</h1>
    </div>
    <div class="notice info"><div class="small"><strong>Keyword search.</strong> {info?.search_note ?? 'Finds passages that contain your words.'} Attach a collection in Chat to give the best-matching passages to the model with each question. {info?.types ?? ''}</div></div>

    {#if !sel && !loading && !error}
      <p class="muted">Create a collection to begin.</p>
    {:else if sel}
      <div class="row" style="margin-bottom:12px">
        <span class="chip">{sel.scope === 'team' ? `Team: ${sel.team}` : 'Personal'}</span>
        <span class="chip mono">{sel.documents} documents</span><span class="chip mono">{sel.passages.toLocaleString()} passages</span>
        <span class="spacer"></span>
        {#if sel.can_write}
          <button class="btn sm" onclick={() => { eName = sel.name; eDesc = sel.description; editOpen = true; }}><Icon name="edit" size={15} /> Rename</button>
          <button class="btn sm danger" onclick={() => (delOpen = true)}><Icon name="trash" size={15} /> Delete</button>
        {:else}<span class="small muted">You can read this collection but not change it.</span>{/if}
      </div>
      {#if sel.description}<p class="muted">{sel.description}</p>{/if}

      {#if sel.can_write}
        <div class="panel" style="margin-bottom:16px"><div class="panel-b row">
          <label class="btn primary" class:dis={uploading} aria-disabled={uploading}><Icon name="plus" /> {uploading ? 'Uploading…' : 'Add documents'}<input type="file" multiple class="sr-only" onchange={pick} disabled={uploading} /></label>
          <span class="small muted grow">Up to {info?.limits.document_kb.toLocaleString()} KB each, {info?.limits.documents} documents and {info?.limits.passages.toLocaleString()} passages per collection. Stored encrypted.</span>
        </div></div>
        {#if log.length}<ul class="ulog" aria-live="polite">{#each log as l}<li class={l.ok ? '' : 'err'}><strong>{l.name}</strong>: {l.ok ? `added, ${l.text}` : l.text}</li>{/each}</ul>{/if}
      {/if}

      {#if docsLoading}<p class="muted" role="status">Loading documents…</p>
      {:else if docsError}<div class="notice bad" role="alert"><div>{docsError} <button class="btn sm" onclick={loadDocs}>Retry</button></div></div>
      {:else if !docs.length}<p class="muted">No documents yet.{sel.can_write ? ' Add text, Markdown, CSV, JSON, code or PDF files.' : ''}</p>
      {:else}
        <div class="tablewrap"><table class="t"><caption class="sr-only">Documents in {sel.name}</caption>
          <thead><tr><th scope="col">Name</th><th scope="col">Type</th><th scope="col" class="num">Size</th><th scope="col" class="num">Passages</th><th scope="col"><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>{#each docs as d (d.id)}<tr><td class="wrap" style="overflow-wrap:anywhere">{d.name}</td><td>{d.kind === 'pdf' ? 'PDF' : 'Text'}</td><td class="num">{kb(d.size_bytes)}</td><td class="num">{d.passages}</td>
            <td>{#if sel.can_write}<button class="btn sm danger" onclick={() => { delDoc = d; delDocOpen = true; }} aria-label={`Delete ${d.name}`}>Delete</button>{/if}</td></tr>{/each}</tbody></table></div>
      {/if}

      {#if docs.length}
        <h2 style="margin:24px 0 8px;font-size:17px">Try a keyword search</h2>
        <p class="small muted">Runs the same search that Chat uses, so you can see which passages would be given to the model.</p>
        <form class="row" style="margin-bottom:12px" onsubmit={(e) => { e.preventDefault(); search(); }}>
          <label class="sr-only" for="kq">Search words</label>
          <input id="kq" class="input grow" type="search" placeholder="Words to look for" bind:value={q} />
          <button class="btn primary" disabled={searching || !q.trim()}>{searching ? 'Searching…' : 'Search'}</button>
        </form>
        {#if searchError}<p class="err" role="alert">{searchError}</p>{/if}
        {#if results}
          {#if !results.length}<p class="muted" role="status">No passage contains those words. Try different words; synonyms are not matched.</p>
          {:else}<ol class="res" aria-label="Keyword matches">{#each results as r}<li><div class="small"><strong>{r.file}</strong> <span class="muted">passage {r.passage} · score {r.score}</span></div><p>{r.text}</p></li>{/each}</ol>{/if}
        {/if}
      {/if}
    {/if}
  </section>
</div>
{/if}

<Modal bind:open={newOpen} title="New collection">
  <div class="field"><label for="cn">Name</label><input id="cn" class="input" bind:value={nName} maxlength="120" /></div>
  <div class="field"><label for="cd">Description (optional)</label><input id="cd" class="input" bind:value={nDesc} maxlength="1000" /></div>
  <div class="field"><label for="cs">Who can read it</label>
    <select id="cs" class="input" bind:value={nScope}><option value="">Only me</option>{#each info?.teams ?? [] as t}<option value={t.id}>Members of team {t.name}</option>{/each}</select>
    <span class="hint">Team members can search and attach it. Only you and team managers can change it. Administrators do not see collections they are not part of.</span></div>
  {#if nErr}<p class="err" role="alert">{nErr}</p>{/if}
  {#snippet footer()}<button class="btn" onclick={() => (newOpen = false)}>Cancel</button><button class="btn primary" disabled={!nName.trim()} onclick={create}>Create</button>{/snippet}
</Modal>
<Modal bind:open={editOpen} title="Rename collection">
  <div class="field"><label for="en">Name</label><input id="en" class="input" bind:value={eName} maxlength="120" /></div>
  <div class="field"><label for="ed">Description</label><input id="ed" class="input" bind:value={eDesc} maxlength="1000" /></div>
  {#snippet footer()}<button class="btn" onclick={() => (editOpen = false)}>Cancel</button><button class="btn primary" disabled={!eName.trim()} onclick={saveEdit}>Save</button>{/snippet}
</Modal>
<Modal bind:open={delOpen} title="Delete this collection?"><p>“{sel?.name}” and its {sel?.documents} document{sel?.documents === 1 ? '' : 's'} will be permanently deleted. Conversations that used it keep working without it. Backups expire on their own schedule.</p>
  {#snippet footer()}<button class="btn" onclick={() => (delOpen = false)}>Cancel</button><button class="btn danger solid" onclick={removeCol}>Delete collection</button>{/snippet}</Modal>
<Modal bind:open={delDocOpen} title="Delete this document?"><p>“{delDoc?.name}” will be removed from the collection.</p>
  {#snippet footer()}<button class="btn" onclick={() => (delDocOpen = false)}>Cancel</button><button class="btn danger solid" onclick={removeDoc}>Delete document</button>{/snippet}</Modal>

<style>
  .kn { display: grid; grid-template-columns: 280px 1fr; height: 100%; min-height: 0; }
  .side { border-right: 1px solid var(--border); padding: 12px; overflow: auto; background: var(--bg); }
  .sh { display: flex; gap: 8px; margin-bottom: 12px; } .closeb { display: none; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: 2px; }
  .ci { all: unset; box-sizing: border-box; width: 100%; display: grid; gap: 1px; padding: 8px; border-radius: var(--radius-sm); cursor: pointer; }
  .ci:hover { background: var(--surface-2); } .ci:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; } .ci.on { background: var(--accent-soft); }
  .ci .n { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .main { overflow: auto; padding: 16px 24px 32px; min-width: 0; }
  .top { display: flex; align-items: center; gap: 8px; margin-bottom: 12px; } h1 { font-size: 22px; overflow-wrap: anywhere; } .listb { display: none; }
  .scrim { display: none; }
  label.btn.dis { opacity: 0.5; pointer-events: none; }
  label.btn:has(input:focus-visible) { outline: 2px solid var(--accent); outline-offset: 2px; }
  .ulog { margin: 0 0 16px; font-size: 13px; display: grid; gap: 2px; } .ulog .err { color: var(--danger); }
  .res { list-style: none; padding: 0; margin: 0; display: grid; gap: 12px; }
  .res li { border-left: 2px solid var(--border-strong); padding-left: 10px; } .res p { margin: 2px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 13.5px; }
  @media (max-width: 1023px) {
    .kn { grid-template-columns: 1fr; }
    .side { position: fixed; z-index: 60; left: 0; top: 0; bottom: 0; width: min(320px, 88vw); transform: translateX(-102%); background: var(--surface); box-shadow: var(--shadow); }
    .side.open { transform: none; } .closeb { display: inline-flex; } .listb { display: inline-flex; }
    .scrim { display: block; position: fixed; inset: 0; background: rgba(10,12,12,.5); z-index: 55; }
  }
  @media (max-width: 719px) { .main { padding: 12px; } }
  @media (prefers-reduced-motion: no-preference) { .side { transition: transform 140ms ease-out; } }
</style>
