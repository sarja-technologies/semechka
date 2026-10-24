<script lang="ts">
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { untrack, tick, onMount } from 'svelte';
  import { app, loadMe } from '$lib/session.svelte';
  import { get, post, patch, del, qs, stream, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { TECH_LABELS, fmtTech } from '$lib/techlabels';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Markdown from '$lib/components/Markdown.svelte';
  import ProtectionPanel from '$lib/components/ProtectionPanel.svelte';
  import ContextBar from '$lib/components/ContextBar.svelte';
  import Sources from '$lib/components/Sources.svelte';

  // The same page serves Chat and the Code workspace; only the workspace differs (see routes/(app)/code).
  let { workspace = 'chat' }: { workspace?: 'chat' | 'code' } = $props();
  const base = $derived(`/${workspace}`);

  type Msg = { id: string; parent_id: string | null; role: string; content: string; status: string; created_at: number; model: string | null; tech: Record<string, any>; sources?: any[] };
  const ui = $derived(app.me.ui);
  const routeId = $derived(page.params.id);

  let models = $state<any[]>([]), modelsError = $state(''), modelId = $state('');
  let convs = $state<any[]>([]), q = $state(''), showArchived = $state(false), listLoading = $state(true), listError = $state('');
  let conv = $state<{ id: string; title: string; active_leaf: string | null; messages: Msg[]; retention_note?: string; expires_at?: number | null } | null>(null);
  let assistantId = $state<string | null>(null), collectionIds = $state<string[]>([]);
  let convLoading = $state(false), convError = $state('');
  let ephemeral = $state(false), eph = $state<Msg[]>([]);
  let live = $state<null | { mode: string; anchor: string | null; userText: string; text: string; reasoning: string; status: 'sending' | 'streaming' | 'stopping'; error: string; startedUser: boolean; queue?: null | { position: number; ahead: number; waiting: number; running: number; limit: number }; sources?: any[]; skipped?: number }>(null);
  let ctrl: { abort: () => void; done: Promise<void> } | null = null;
  let input = $state(''), sendError = $state('');
  let files = $state<{ name: string; text: string }[]>([]), fileNote = $state('');
  const TEXT_EXT = /\.(txt|md|markdown|csv|tsv|json|ya?ml|toml|ini|log|xml|html?|css|js|mjs|ts|tsx|jsx|py|rb|go|rs|c|h|cpp|hpp|java|kt|sh|bash|sql|svelte|vue|php|swift|cs)$/i;
  async function pickFiles(e: Event) {
    const inp = e.currentTarget as HTMLInputElement; fileNote = '';
    for (const f of [...(inp.files ?? [])]) {
      if (files.length >= 5) { fileNote = 'At most 5 files per message.'; break; }
      if (!TEXT_EXT.test(f.name) && !f.type.startsWith('text/')) { fileNote = 'Images and binary files are not supported by this release. Attach text or code files.'; continue; }
      if (f.size > 200 * 1024) { fileNote = `${f.name} is larger than 200 KB.`; continue; }
      const text = await f.text();
      if (text.includes('\u0000')) { fileNote = 'Images and binary files are not supported by this release. Attach text or code files.'; continue; }
      files.push({ name: f.name, text });
    }
    inp.value = '';
  }
  let editing = $state<{ id: string; text: string } | null>(null);
  let sheetList = $state(false), protOpen = $state(false), renameOpen = $state(false), deleteOpen = $state(false), renameText = $state('');
  let selected = $state<string | null>(null), openThink = $state<Record<string, boolean>>({});
  let scroller: HTMLDivElement | undefined = $state(), composer: HTMLTextAreaElement | undefined = $state();
  let resources = $state<any>(null), skipLoad = '';

  // ---------- tree ----------
  const byParent = $derived.by(() => { const m: Record<string, Msg[]> = {}; for (const x of conv?.messages ?? []) (m[x.parent_id ?? ''] ??= []).push(x); for (const k in m) m[k].sort((a, b) => a.created_at - b.created_at); return m; });
  const path = $derived.by<Msg[]>(() => {
    if (ephemeral) return eph;
    const all = conv?.messages ?? []; if (!all.length) return [];
    const byId = new Map(all.map((x) => [x.id, x])); const on = new Set<string>();
    for (let c = conv?.active_leaf ? byId.get(conv.active_leaf) : undefined; c; c = c.parent_id ? byId.get(c.parent_id) : undefined) on.add(c.id);
    const out: Msg[] = []; let kids = byParent[''] ?? [];
    while (kids.length) { const pick = kids.find((k) => on.has(k.id)) ?? kids[kids.length - 1]; out.push(pick); kids = byParent[pick.id] ?? []; }
    return out;
  });
  const shown = $derived.by<Msg[]>(() => {
    if (!live || ephemeral) return path;
    if (live.mode === 'edit' && live.anchor) { const i = path.findIndex((m) => m.id === live!.anchor); return i >= 0 ? path.slice(0, i) : path; }
    if (live.mode === 'regenerate' && live.anchor) { const i = path.findIndex((m) => m.id === live!.anchor); return i >= 0 ? path.slice(0, i) : path; }
    return path;
  });
  const lastAssistant = $derived([...path].reverse().find((m) => m.role === 'assistant' && Object.keys(m.tech ?? {}).length));
  const techOn = $derived(!!ui.tech.drawer && !!app.me.user.prefs.show_tech);
  const detailMsg = $derived(path.find((m) => m.id === selected) ?? lastAssistant);
  const busy = $derived(live !== null);
  const model = $derived(models.find((m) => m.id === modelId));

  function siblings(m: Msg) { return byParent[m.parent_id ?? ''] ?? [m]; }
  async function switchTo(m: Msg, dir: number) {
    const sib = siblings(m); const i = sib.findIndex((x) => x.id === m.id); const t = sib[i + dir]; if (!t || !conv) return;
    let leaf = t; for (let k = byParent[leaf.id]; k?.length; k = byParent[leaf.id]) leaf = k[k.length - 1];
    conv.active_leaf = leaf.id;
    try { await patch(`/api/chat/conversations/${conv.id}`, { active_leaf: leaf.id }); } catch (e) { toast(msg(e), 'bad'); }
  }

  function useAssistantModel() {
    const a = assistantId; if (!a) return;
    get(`/api/assistants/${a}`).then((x) => { if (x.model_id && models.find((m) => m.id === x.model_id)) modelId = x.model_id; }).catch(() => {});
  }
  async function ctxChanged() {
    useAssistantModel();
    if (!conv || ephemeral) return; // saved conversations remember it; new ones send it with the first message
    try { await patch(`/api/chat/conversations/${conv.id}`, { assistant_id: assistantId, collection_ids: collectionIds }); }
    catch (e) { toast(msg(e), 'bad'); }
  }

  const msg = (e: unknown) => (e instanceof ApiError ? e.message : 'Something unexpected happened. Try again.');

  // ---------- loading ----------
  async function loadModels() {
    try {
      models = await get(`/api/chat/models${qs({ mode: workspace === 'code' ? 'code' : undefined })}`); modelsError = '';
      if (workspace === 'code') models = [...models].sort((a, b) => Number(!!b.code_hint) - Number(!!a.code_hint)); // admin-tagged or code-named models first
      if (!models.find((m) => m.id === modelId)) modelId = models[0]?.id ?? '';
    }
    catch (e) { models = []; modelsError = msg(e); }
  }
  let searchTimer: ReturnType<typeof setTimeout>;
  async function loadList() {
    listLoading = true;
    try { convs = await get(`/api/chat/conversations${qs({ q: q || undefined, archived: showArchived || undefined, mode: workspace === 'code' ? 'code' : undefined })}`); listError = ''; }
    catch (e) { listError = msg(e); } finally { listLoading = false; }
  }
  function onSearch() { clearTimeout(searchTimer); searchTimer = setTimeout(loadList, 250); }
  async function loadConv(id: string) {
    convLoading = true; convError = '';
    try { const c = await get(`/api/chat/conversations/${id}`); conv = c; assistantId = c.context?.assistant_id ?? null; collectionIds = c.context?.collection_ids ?? []; if (c.model_id && models.find((m) => m.id === c.model_id)) modelId = c.model_id; selected = null; await scrollEnd(true); }
    catch (e) { conv = null; convError = e instanceof ApiError && e.status === 404 ? 'This conversation does not exist or is not yours.' : msg(e); }
    finally { convLoading = false; }
  }
  function onRoute(id: string | undefined) {
    if (id && id === skipLoad) { skipLoad = ''; return; }
    if (id) { ephemeral = false; eph = []; loadConv(id); } else { conv = null; convError = ''; const a = page.url.searchParams.get('assistant'); if (a) { assistantId = a; useAssistantModel(); } }
    sheetList = false;
  }
  $effect(() => { const id = routeId; untrack(() => onRoute(id)); });
  $effect(() => { showArchived; untrack(loadList); });
  onMount(() => { loadModels(); });

  // resource readings only for people granted them, only while the drawer is open
  $effect(() => {
    if (!techOn || !ui.tech.fields.some((f: string) => f.startsWith('tech.resources'))) { resources = null; return; }
    let on = true; const tick_ = () => get('/api/system/resources').then((r) => on && (resources = r)).catch(() => on && (resources = null));
    tick_(); const t = setInterval(tick_, 4000); return () => { on = false; clearInterval(t); };
  });

  async function scrollEnd(force = false) {
    await tick(); if (!scroller) return;
    if (force || scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160) scroller.scrollTop = scroller.scrollHeight;
  }

  // ---------- sending ----------
  async function ensureConv(): Promise<string | null> {
    if (conv) return conv.id;
    const c = await post('/api/chat/conversations', { model_id: modelId, mode: workspace, assistant_id: assistantId, collection_ids: collectionIds });
    conv = { id: c.id, title: c.title, active_leaf: null, messages: [], expires_at: c.expires_at };
    skipLoad = c.id; goto(`${base}/${c.id}`, { replaceState: true });
    return c.id;
  }
  async function run(mode: 'send' | 'regenerate' | 'edit', content: string, anchor: string | null) {
    if (busy || !modelId) return;
    sendError = '';
    live = { mode, anchor, userText: content, text: '', reasoning: '', status: 'sending', error: '', startedUser: false };
    try {
      let url: string, body: any;
      if (ephemeral) {
        let hist = eph.map((m) => ({ role: m.role, content: m.content }));
        if (mode === 'regenerate') { while (hist.length && hist[hist.length - 1].role === 'assistant') hist.pop(); }
        else { hist.push({ role: 'user', content }); }
        url = '/api/chat/ephemeral/generate'; body = { messages: hist, model_id: modelId, workspace, assistant_id: assistantId, collection_ids: collectionIds };
      } else {
        const id = await ensureConv(); if (!id) return;
        url = `/api/chat/conversations/${id}/generate`;
        body = mode === 'send' ? { mode, content, model_id: modelId, parent_id: path.length ? path[path.length - 1].id : null }
          : { mode, content: mode === 'edit' ? content : undefined, parent_id: anchor, model_id: modelId };
      }
      let final: any = null;
      ctrl = stream(url, body, (ev) => {
        if (!live) return;
        if (ev.type === 'start') { live.status = 'streaming'; live.startedUser = true; }
        else if (ev.type === 'delta') { live.text += ev.content; live.reasoning += ev.reasoning; scrollEnd(); }
        else if (ev.type === 'queue') live.queue = ev.running === true ? null : ev;
        else if (ev.type === 'sources') { live.sources = ev.sources; live.skipped = ev.skipped; }
        else if (ev.type === 'error') live.error = ev.message;
        else if (ev.type === 'done') final = ev;
      });
      await ctrl.done;
      if (ephemeral) {
        if (mode !== 'regenerate') eph.push({ id: 'u' + eph.length + Date.now(), parent_id: null, role: 'user', content, status: 'complete', created_at: 0, model: null, tech: {} });
        else { while (eph.length && eph[eph.length - 1].role === 'assistant') eph.pop(); }
        const text = final?.content ?? live?.text ?? '';
        if (text || live?.error) eph.push({ id: 'a' + eph.length + Date.now(), parent_id: null, role: 'assistant', content: text, status: live?.error ? 'error' : final?.status ?? 'stopped', created_at: 0, model: model?.alias ?? null, tech: final?.tech ?? {}, sources: final?.sources });
      } else if (conv) {
        if (live?.status === 'stopping') await new Promise((r) => setTimeout(r, 350));
        await loadConv(conv.id); loadList();
      }
      if (live?.error) sendError = live.error;
    } catch (e) {
      sendError = msg(e);
      if (!ephemeral && conv && conv.messages.length === 0 && !live?.startedUser) { /* empty conversation stays; user can retry */ }
    } finally { live = null; ctrl = null; scrollEnd(true); composer?.focus(); }
  }
  function submit() { if (busy) return; const body = input.trim(); if (!body && !files.length) return; const t = [...files.map((f) => `<file name="${f.name.replace(/"/g, '')}">\n${f.text}\n</file>`), body].filter(Boolean).join('\n\n'); if (t.length > ui.composer.max_chars) { sendError = `Messages are limited to ${ui.composer.max_chars.toLocaleString()} characters.`; return; } input = ''; files = []; fileNote = ''; autosize(); run('send', t, null); }
  function stop() { if (!live || !ctrl) return; live.status = 'stopping'; ctrl.abort(); }
  function key(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); submit(); }
    else if (e.key === 'Escape' && busy) { e.preventDefault(); stop(); }
  }
  function autosize() { tick().then(() => { if (!composer) return; composer.style.height = 'auto'; composer.style.height = Math.min(composer.scrollHeight, 220) + 'px'; }); }
  async function copy(t: string) { try { await navigator.clipboard.writeText(t); toast('Copied'); } catch { toast('Copy is not available in this browser context.', 'bad'); } }

  // ---------- conversation actions ----------
  function newChat(eph_ = false) { ephemeral = eph_; eph = []; conv = null; sendError = ''; assistantId = null; collectionIds = []; if (routeId) goto(base); sheetList = false; tick().then(() => composer?.focus()); }
  async function rename() { if (!conv || !renameText.trim()) return; try { await patch(`/api/chat/conversations/${conv.id}`, { title: renameText.trim() }); conv.title = renameText.trim(); renameOpen = false; loadList(); } catch (e) { toast(msg(e), 'bad'); } }
  async function archive() { if (!conv) return; try { await patch(`/api/chat/conversations/${conv.id}`, { archived: !showArchived }); toast(showArchived ? 'Restored' : 'Archived'); newChat(); loadList(); } catch (e) { toast(msg(e), 'bad'); } }
  async function remove() { if (!conv) return; try { await del(`/api/chat/conversations/${conv.id}`); deleteOpen = false; toast('Conversation deleted'); newChat(); loadList(); } catch (e) { toast(msg(e), 'bad'); } }
  async function exportMd() {
    if (!conv) return;
    try { const r = await fetch(`/api/chat/conversations/${conv.id}/export?format=md`, { credentials: 'same-origin' }); if (!r.ok) throw new ApiError(r.status, (await r.json()).detail);
      const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = `${conv.title.replace(/[^\w.-]+/g, '_').slice(0, 50) || 'conversation'}.md`; a.click(); URL.revokeObjectURL(a.href);
    } catch (e) { toast(msg(e), 'bad'); }
  }
  async function toggleTech() { try { await patch('/api/me', { show_tech: !app.me.user.prefs.show_tech }); await loadMe(); } catch (e) { toast(msg(e), 'bad'); } }

  const groups = $derived.by(() => {
    const now = new Date(); const d0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime() / 1000;
    const g: [string, any[]][] = [['Today', []], ['Yesterday', []], ['Previous 7 days', []], ['Older', []]];
    for (const c of convs) { const t = c.updated_at; (t >= d0 ? g[0][1] : t >= d0 - 86400 ? g[1][1] : t >= d0 - 7 * 86400 ? g[2][1] : g[3][1]).push(c); }
    return g.filter(([, l]) => l.length);
  });
  const badge = $derived(ephemeral ? 'Not saved' : conv?.expires_at ? `Stored for ${Math.max(1, Math.round((conv.expires_at - Date.now() / 1000) / 86400))} days` : ui.retention_days === 0 ? 'Not stored' : ui.retention_days > 0 ? `Stored for ${ui.retention_days} days` : 'Stored until you delete it');
  const canEph = $derived(ui.composer.ephemeral);
  const fieldsAvail = $derived(ui.tech.fields.length > 0);
</script>

{#if !ui.modes[workspace].enabled}
  <div style="padding:24px;max-width:640px"><h1 style="margin-bottom:12px">{workspace === 'code' ? 'Code' : 'Chat'}</h1><div class="notice warn" role="status"><div><strong>{workspace === 'code' ? 'The code workspace is' : 'Chat is'} not available to you.</strong> {ui.modes[workspace].reason}</div></div></div>
{:else}
<div class="chat" class:withdrawer={techOn}>
  <aside class="list" class:sheet={sheetList} aria-label="Conversations">
    <div class="lh">
      <button class="btn primary grow" onclick={() => newChat(false)}><Icon name="plus" /> New chat</button>
      <button class="btn ghost icon sm sheetx" aria-label="Close conversation list" onclick={() => (sheetList = false)}><Icon name="x" /></button>
    </div>
    <div class="field" style="margin:0 0 8px"><label class="sr-only" for="qs">Search conversations</label><input id="qs" data-search class="input" type="search" placeholder="Search  ( / )" bind:value={q} oninput={onSearch} /></div>
    <label class="small row" style="gap:6px;margin-bottom:8px"><input type="checkbox" bind:checked={showArchived} /> Show archived</label>
    <div class="items">
      {#if listLoading && !convs.length}<p class="muted small" role="status">Loading…</p>
      {:else if listError}<div class="notice bad" role="alert"><div>{listError} <button class="btn sm" onclick={loadList}>Retry</button></div></div>
      {:else if !convs.length}<p class="muted small">{q ? 'No conversation matches that search.' : showArchived ? 'No archived conversations.' : 'No saved conversations yet. Start one and it will appear here.'}</p>{/if}
      {#each groups as [label, list]}
        <h2 class="gh">{label}</h2>
        {#each list as c (c.id)}<a class="ci" class:on={conv?.id === c.id || routeId === c.id} href={`${base}/${c.id}`} aria-current={routeId === c.id ? 'page' : undefined}>{c.title}</a>{/each}
      {/each}
    </div>
  </aside>
  {#if sheetList}<div class="scrim" role="presentation" onclick={() => (sheetList = false)}></div>{/if}

  <section class="conv" aria-label="Conversation">
    <header class="ch">
      <button class="btn ghost icon sm listbtn" aria-label="Show conversations" onclick={() => (sheetList = true)}><Icon name="list" /></button>
      <h1 class="ttl grow">{ephemeral ? 'No-save conversation' : conv?.title ?? 'New conversation'}</h1>
      {#if conv && !ephemeral}
        <button class="btn ghost icon sm" aria-label="Rename" title="Rename" onclick={() => { renameText = conv!.title; renameOpen = true; }}><Icon name="edit" /></button>
        {#if ui.composer.export}<button class="btn ghost icon sm" aria-label="Export as Markdown" title="Export as Markdown" onclick={exportMd}><Icon name="download" /></button>{/if}
        <button class="btn ghost icon sm" aria-label={showArchived ? 'Restore from archive' : 'Archive'} title={showArchived ? 'Restore' : 'Archive'} onclick={archive}><Icon name="history" /></button>
        <button class="btn ghost icon sm" aria-label="Delete conversation" title="Delete" onclick={() => (deleteOpen = true)}><Icon name="trash" /></button>
      {/if}
      {#if ui.tech.drawer}<button class="btn sm" class:primary={techOn} aria-pressed={techOn} onclick={toggleTech} title="Show technical details for replies"><Icon name="cpu" size={16} /> Technical details</button>{/if}
    </header>

    <div class="scroll" bind:this={scroller}>
      <div class="col">
        {#if modelsError}
          <div class="notice warn" role="alert"><div><strong>Chat is not ready.</strong> {modelsError}</div></div>
        {:else if convLoading}<p class="muted" role="status">Loading conversation…</p>
        {:else if convError}<div class="notice bad" role="alert"><div>{convError} <a href={base}>Start a new chat</a></div></div>
        {:else if !shown.length && !live}
          <div class="empty">
            <h2>{ephemeral ? 'This conversation will not be saved' : workspace === 'code' ? 'What are you building?' : 'What are you working on?'}</h2>
            {#if workspace === 'code' && !ephemeral}<div class="notice info"><div class="small"><strong>Answers only.</strong> The code workspace writes and explains code but cannot run it, open your files or reach the internet. Test anything it gives you yourself. Attach source files with the + button.</div></div>{/if}
            {#if ephemeral}
              <p class="small muted">Read how it is handled before you send your first message.</p>
              <div class="panel"><div class="panel-b"><ProtectionPanel {modelId} ephemeral /></div></div>
            {:else if ui.suggested_prompts.length}
              <p class="small muted">Suggestions from your administrator:</p>
              <div class="sugg">{#each ui.suggested_prompts as s}<button class="btn" onclick={() => { input = s; composer?.focus(); autosize(); }}>{s}</button>{/each}</div>
            {:else}<p class="small muted">Type a message below. Enter sends, Shift+Enter adds a line.</p>{/if}
          </div>
        {/if}

        {#each shown as m (m.id)}
          {@const sib = !ephemeral ? siblings(m) : [m]}
          {@const si = sib.findIndex((x) => x.id === m.id)}
          <article class="msg {m.role}" aria-label={m.role === 'user' ? 'You' : (m.model ?? 'Assistant')}>
            <div class="who">{m.role === 'user' ? 'You' : (m.model ?? 'Assistant')}
              {#if m.status === 'stopped'}<span class="chip warn">Stopped</span>{/if}{#if m.status === 'error'}<span class="chip bad">Failed</span>{/if}
              {#if sib.length > 1}<span class="sibs" aria-label={`Version ${si + 1} of ${sib.length}`}><button class="btn ghost icon sm" aria-label="Previous version" disabled={si === 0 || busy} onclick={() => switchTo(m, -1)}><span style="transform:rotate(180deg);display:grid"><Icon name="chevron" size={14} /></span></button><span class="mono tiny">{si + 1}/{sib.length}</span><button class="btn ghost icon sm" aria-label="Next version" disabled={si === sib.length - 1 || busy} onclick={() => switchTo(m, 1)}><Icon name="chevron" size={14} /></button></span>{/if}
            </div>
            {#if m.role === 'user'}
              {#if editing?.id === m.id}
                <textarea class="input" rows="3" bind:value={editing.text} aria-label="Edit message"></textarea>
                <div class="row" style="margin-top:8px"><button class="btn primary sm" disabled={!editing.text.trim() || busy} onclick={() => { const e = editing!; editing = null; run('edit', e.text.trim(), m.id); }}>Save and regenerate</button><button class="btn sm" onclick={() => (editing = null)}>Cancel</button></div>
              {:else}<div class="utext">{m.content}</div>{/if}
            {:else}
              {#if m.sources?.length}<Sources sources={m.sources} />{/if}
              {#if m.content}<Markdown text={m.content} />{:else}<p class="muted small">{m.status === 'error' ? 'The model did not return a reply.' : 'No text was produced.'}</p>{/if}
              {#if techOn && m.tech.tokens_per_s}<span class="chip mono">{m.tech.tokens_per_s} tok/s</span>{/if}
            {/if}
            {#if !busy && editing?.id !== m.id}
              <div class="acts">
                <button class="btn ghost sm" onclick={() => copy(m.content)}><Icon name="copy" size={15} /> Copy</button>
                {#if m.role === 'user' && ui.composer.branching && !ephemeral}<button class="btn ghost sm" onclick={() => (editing = { id: m.id, text: m.content })}><Icon name="edit" size={15} /> Edit</button>{/if}
                {#if m.role === 'assistant' && (ephemeral ? m.id === eph[eph.length - 1]?.id : ui.composer.branching)}<button class="btn ghost sm" onclick={() => run('regenerate', '', m.id)}><Icon name="refresh" size={15} /> Regenerate</button>{/if}
                {#if m.role === 'assistant' && techOn && Object.keys(m.tech).length}<button class="btn ghost sm" aria-pressed={detailMsg?.id === m.id} onclick={() => (selected = m.id)}><Icon name="cpu" size={15} /> Details</button>{/if}
              </div>
            {/if}
          </article>
        {/each}

        {#if live}
          {#if live.mode !== 'regenerate'}<article class="msg user pending"><div class="who">You {#if live.status === 'sending'}<span class="chip">Sending</span>{/if}</div><div class="utext">{live.userText}</div></article>{/if}
          <article class="msg assistant" aria-live="off" aria-label={model?.alias ?? 'Assistant'}>
            <div class="who">{model?.alias ?? 'Assistant'} <span class="chip info" role="status">{live.queue ? `In queue: position ${live.queue.position}` : live.status === 'sending' ? 'Waiting for the model' : live.status === 'stopping' ? 'Stopping' : live.text || live.reasoning ? 'Writing' : 'Thinking'}</span></div>
            {#if live.queue}<p class="small muted" style="margin:0 0 6px">{live.queue.running} of {live.queue.limit} reply slot{live.queue.limit === 1 ? '' : 's'} in use; {live.queue.waiting} waiting. Your request starts when it reaches the front. Press Stop to leave the queue.</p>{/if}
            {#if live.sources?.length || live.skipped}<Sources sources={live.sources ?? []} skipped={live.skipped ?? 0} />{/if}
            {#if live.reasoning}<details bind:open={openThink['live']}><summary class="small muted">See thinking</summary><pre class="think">{live.reasoning}</pre></details>{/if}
            {#if live.text}<Markdown text={live.text} />{/if}
          </article>
        {/if}
        {#if sendError}<div class="notice bad" role="alert"><div>{sendError}</div></div>{/if}
      </div>
    </div>

    <div class="composer">
      <div class="col">
        <div class="cbar">
          <label class="sr-only" for="mdl">Model</label>
          <select id="mdl" class="input msel" bind:value={modelId} disabled={busy || !models.length || (!!conv && conv.messages.length > 0 && false)}>
            {#if !models.length}<option value="">No model available</option>{/if}
            {#each models as m}<option value={m.id}>{m.alias}{m.pinned ? ' (pinned)' : ''}{m.external ? ' (external)' : ''}</option>{/each}
          </select>
          {#if model?.external}<span class="chip warn">External service</span>{/if}
          {#if workspace === 'code' && model?.code_hint}<span class="chip info" title="Why this is suggested">Suggested for code: {model.code_hint}</span>{/if}
          <ContextBar bind:assistantId bind:collectionIds disabled={busy} onchange={ctxChanged} />
          {#if techOn && model}{#each Object.entries(model.tech).filter(([k]) => k !== '_unavailable' && ['model_real_id', 'quantization', 'context', 'params'].includes(k)) as [k, v]}<span class="chip mono" title={TECH_LABELS[k]}>{fmtTech(k, v)}</span>{/each}{/if}
          <span class="spacer"></span>
          {#if canEph && !conv && !shown.length}<label class="small row" style="gap:6px"><input type="checkbox" checked={ephemeral} onchange={(e) => { ephemeral = e.currentTarget.checked; eph = []; }} /> No-save</label>{/if}
          <button class="btn sm" onclick={() => (protOpen = true)} title="How is this chat protected?"><Icon name="lock" size={15} /> {badge}</button>
        </div>
        <div class="cin">
          <label class="sr-only" for="msg">Message</label>
          <textarea id="msg" class="input" rows="1" placeholder={models.length ? 'Message…' : 'No model is available to you'} bind:this={composer} bind:value={input} onkeydown={key} oninput={autosize} disabled={!models.length || !!modelsError}></textarea>
          {#if ui.composer.attachments}<label class="btn icon" title="Attach text or code files" aria-label="Attach text or code files"><Icon name="plus" /><input type="file" multiple class="sr-only" onchange={pickFiles} /></label>{/if}
          {#if busy}<button class="btn danger solid" onclick={stop} aria-label="Stop generating (Esc)"><Icon name="stop" size={16} /> Stop</button>
          {:else}<button class="btn primary" onclick={submit} disabled={(!input.trim() && !files.length) || !modelId} aria-label="Send (Enter)"><Icon name="send" size={16} /> Send</button>{/if}
        </div>
        {#if files.length || fileNote}<div class="row" style="margin-top:6px">{#each files as f, i}<span class="chip mono">{f.name}<button class="btn ghost icon sm" style="width:20px;min-height:20px" aria-label={`Remove ${f.name}`} onclick={() => files.splice(i, 1)}>×</button></span>{/each}{#if fileNote}<span class="small err" role="status">{fileNote}</span>{/if}</div>{/if}
        {#if input.length > ui.composer.max_chars * 0.8}<p class="tiny" class:err={input.length > ui.composer.max_chars}>{input.length.toLocaleString()} / {ui.composer.max_chars.toLocaleString()} characters</p>{/if}
      </div>
    </div>
  </section>

  {#if techOn}
    <aside class="drawer" aria-label="Technical details">
      <h2 style="font-size:15px;margin-bottom:4px">Technical details</h2>
      <p class="tiny muted">Shows only the fields your administrator has allowed. Reading these does not give you any control over the system.</p>
      {#if detailMsg}
        <h3 class="small" style="margin:12px 0 4px">Selected reply</h3>
        <dl class="kv">
          {#each Object.entries(detailMsg.tech).filter(([k]) => k !== '_unavailable') as [k, v]}<dt>{TECH_LABELS[k] ?? k}</dt><dd>{#if k === 'gen_params'}{#each Object.entries(v) as [a, b]}<div>{a}: {b}</div>{/each}{:else}{fmtTech(k, v)}{/if}</dd>{/each}
          {#each detailMsg.tech._unavailable ?? [] as k}<dt>{TECH_LABELS[k] ?? k}</dt><dd class="muted" style="font-family:var(--font)">Not supplied by this backend</dd>{/each}
        </dl>
      {:else}<p class="small muted" style="margin-top:12px">Details appear here after the model replies.</p>{/if}
      {#if resources}
        <h3 class="small" style="margin:16px 0 4px">Live resources</h3>
        {#each resources.gpus ?? [] as g}<dl class="kv"><dt>GPU</dt><dd>{g.name}</dd><dt>VRAM</dt><dd>{g.vram_used_mb} / {g.vram_total_mb} MiB</dd><dt>Load</dt><dd>{g.utilization_pct}%</dd><dt>Temperature</dt><dd>{g.temperature_c} °C</dd></dl>{/each}
        {#if resources.gpus === null}<p class="small muted">{resources.gpu_note}</p>{/if}
        {#if resources.host}<dl class="kv"><dt>CPU threads</dt><dd>{resources.host.cpu_count}</dd><dt>Memory free</dt><dd>{resources.host.memory_available_mb} / {resources.host.memory_total_mb} MiB</dd></dl>{/if}
      {/if}
    </aside>
  {/if}
</div>
{/if}

<Modal bind:open={protOpen} title="How is this chat protected?" wide><ProtectionPanel {modelId} {ephemeral} />{#snippet footer()}<button class="btn" onclick={() => (protOpen = false)}>Close</button>{/snippet}</Modal>
<Modal bind:open={renameOpen} title="Rename conversation"><div class="field"><label for="rn">Title</label><input id="rn" class="input" bind:value={renameText} maxlength="200" onkeydown={(e) => e.key === 'Enter' && rename()} /></div>{#snippet footer()}<button class="btn" onclick={() => (renameOpen = false)}>Cancel</button><button class="btn primary" onclick={rename} disabled={!renameText.trim()}>Rename</button>{/snippet}</Modal>
<Modal bind:open={deleteOpen} title="Delete this conversation?"><p>“{conv?.title}” and all of its branches will be permanently deleted. Backups expire on their own schedule.</p>{#snippet footer()}<button class="btn" onclick={() => (deleteOpen = false)}>Cancel</button><button class="btn danger solid" onclick={remove}>Delete conversation</button>{/snippet}</Modal>

<style>
  .chat { display: grid; grid-template-columns: 264px 1fr; height: 100%; min-height: 0; }
  .chat.withdrawer { grid-template-columns: 264px 1fr 300px; }
  .list { border-right: 1px solid var(--border); padding: 12px; display: flex; flex-direction: column; min-height: 0; background: var(--bg); }
  .lh { display: flex; gap: 8px; margin-bottom: 8px; } .sheetx { display: none; }
  .items { overflow: auto; flex: 1; }
  .gh { font-size: 12px; font-weight: 600; color: var(--fg-muted); margin: 12px 0 4px; text-transform: none; }
  .ci { display: block; padding: 7px 8px; border-radius: var(--radius-sm); color: var(--fg); text-decoration: none; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 14px; }
  .ci:hover { background: var(--surface-2); } .ci.on { background: var(--accent-soft); font-weight: 600; }
  .conv { display: flex; flex-direction: column; min-width: 0; min-height: 0; }
  .ch { display: flex; align-items: center; gap: 6px; padding: 8px 16px; border-bottom: 1px solid var(--border); min-height: 52px; }
  .ttl { font-size: 15px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 600; }
  .listbtn { display: none; }
  .scroll { flex: 1; overflow: auto; min-height: 0; }
  .col { max-width: 760px; margin: 0 auto; padding: 16px; width: 100%; }
  .empty { padding: 8vh 0 0; } .empty h2 { margin-bottom: 8px; } .sugg { display: grid; gap: 8px; justify-items: start; margin-top: 8px; }
  .sugg .btn { white-space: normal; text-align: left; height: auto; padding: 8px 12px; }
  .msg { margin-bottom: 20px; } .who { font-size: 13px; font-weight: 600; margin-bottom: 4px; display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .msg.user .utext { white-space: pre-wrap; overflow-wrap: anywhere; background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 8px 12px; max-width: 72ch; }
  .msg.pending { opacity: 0.75; }
  .acts { display: flex; gap: 2px; margin-top: 4px; flex-wrap: wrap; } .sibs { display: inline-flex; align-items: center; gap: 2px; font-weight: 400; }
  .think { white-space: pre-wrap; font: 12px/1.5 var(--mono); color: var(--fg-muted); margin: 4px 0 8px; border-left: 2px solid var(--border-strong); padding-left: 10px; }
  .composer { border-top: 1px solid var(--border); background: var(--bg); }
  .cbar { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-bottom: 8px; } .msel { width: auto; min-width: 160px; max-width: 260px; }
  .cin { display: flex; gap: 8px; align-items: flex-end; } .cin textarea { min-height: 40px; max-height: 220px; resize: none; }
  .drawer { border-left: 1px solid var(--border); padding: 16px; overflow: auto; background: var(--surface); }
  .scrim { display: none; }
  @media (max-width: 1023px) {
    .chat, .chat.withdrawer { grid-template-columns: 1fr; }
    .list { position: fixed; z-index: 60; left: 0; top: 0; bottom: 0; width: min(320px, 88vw); transform: translateX(-102%); background: var(--surface); box-shadow: var(--shadow); }
    .list.sheet { transform: none; } .sheetx { display: inline-flex; } .scrim { display: block; position: fixed; inset: 0; background: rgba(10,12,12,.5); z-index: 55; }
    .listbtn { display: inline-flex; }
    .drawer { position: fixed; z-index: 40; left: 0; right: 0; bottom: 0; max-height: 45vh; border-top: 1px solid var(--border-strong); border-left: 0; box-shadow: var(--shadow); }
  }
  @media (max-width: 719px) { .ch { padding: 8px; } .col { padding: 12px; } .msel { min-width: 0; flex: 1; } }
  @media (prefers-reduced-motion: no-preference) { .list { transition: transform 140ms ease-out; } }
</style>
