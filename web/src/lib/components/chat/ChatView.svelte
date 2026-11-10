<script lang="ts">
  // A conversation: text replies, pictures and videos in one thread. The same page serves the Code workspace.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { untrack, tick, onMount } from 'svelte';
  import { app, loadMe } from '$lib/session.svelte';
  import { get, patch } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { TECH_LABELS, fmtTech, trinodalPath, trinodalRows } from '$lib/techlabels';
  import { list, catalog, type Operation } from '$lib/chat/store.svelte';
  import { Thread, errText, type Msg, type MsgActions } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import ProtectionPanel from '$lib/components/ProtectionPanel.svelte';
  import ThreadView from '$lib/components/chat/ThreadView.svelte';
  import Composer from '$lib/components/chat/Composer.svelte';
  import WorkPane from '$lib/components/workmode/WorkPane.svelte';

  // `media`: the Experimental conversation studio, where pictures and videos are made inside the thread (the composer offers them, with attachments and
  // editing profiles). Ordinary Chat is for conversation, and carries the Chat | Work switch instead.
  let { workspace = 'chat', base = '/chat', media = false }: { workspace?: 'chat' | 'code'; base?: string; media?: boolean } = $props();
  const ui = $derived(app.me.ui);
  const flags = $derived(ui.ui ?? {});
  const brand = $derived<any>({ ...app.brand, ...(ui.brand ?? {}) });
  const routeId = $derived(page.params.id);

  const thread = new Thread(untrack(() => workspace));
  // Chat | Work: the same conversation, seen as a conversation or as an agent at work. Remembered per conversation in this browser.
  let mode = $state<'chat' | 'work'>('chat');
  const modeKey = (id: string) => `sem.mode.${id}`;
  function readMode(id: string | null | undefined): 'chat' | 'work' { try { return id && localStorage.getItem(modeKey(id)) === 'work' ? 'work' : 'chat'; } catch { return 'chat'; } }
  function setMode(m: 'chat' | 'work') {
    if (m === mode || thread.busy) return;
    mode = m;
    if (thread.id) { try { localStorage.setItem(modeKey(thread.id), m); } catch { /* storage unavailable */ } }
    if (m === 'chat' && thread.id) thread.reload(); // what the agent said and did while in Work is mirrored into the conversation
    else if (m === 'chat') tick().then(() => composer?.focus());
  }
  /** Work needs a saved conversation: the agent's session belongs to it. */
  const workWhy = $derived(thread.ephemeral ? 'Work Mode needs a saved conversation. Start a new conversation without the no-save option.' : '');
  async function ensureConversation() { return thread.ensure('', { assistantId, collectionIds }, created); }
  let composer: Composer | undefined = $state();
  let assistantId = $state<string | null>(null), collectionIds = $state<string[]>([]);
  let protOpen = $state(false), selected = $state<string | null>(null);

  // ---- route ---------------------------------------------------------------------------------------------------
  function onRoute(id: string | undefined) {
    if (id && id === thread.skipLoad) { thread.skipLoad = ''; return; }
    selected = null;
    if (id) mode = readMode(id);
    if (id) thread.open(id).then(() => {
      const c = thread.head?.context; assistantId = c?.assistant_id ?? null; collectionIds = c?.collection_ids ?? [];
      composer?.prefer(thread.head?.model_id, workspace === 'code' ? 'code' : 'text');
    });
    else if (thread.id || thread.messages.length) { thread.reset(false); assistantId = null; collectionIds = []; mode = 'chat'; }
  }
  $effect(() => { const id = routeId; untrack(() => onRoute(id)); });
  $effect(() => { list.title = thread.id || thread.ephemeral ? thread.title : ''; });
  $effect(() => () => { list.title = ''; });

  // Deep links (command palette, assistants page): ?new=<stamp>[&nosave=1][&model=<id>][&assistant=<id>]
  let lastNew = '';
  $effect(() => {
    const sp = page.url.searchParams, stamp = sp.get('new'), want = sp.get('model'), asst = sp.get('assistant');
    untrack(() => {
      if (stamp && stamp !== lastNew) {
        lastNew = stamp;
        // pictures and videos have their own screens now; older links land there
        const wantOp = sp.get('op');
        if (!media && (wantOp === 'image' || wantOp === 'video')) { goto(sp.get('preset') === 'iphone_photo' ? '/imajutsu' : `/${wantOp}`, { replaceState: true }); return; }
        mode = !media && sp.get('mode') === 'work' ? 'work' : 'chat';
        thread.reset(sp.get('nosave') === '1' && !!ui.composer.ephemeral);
        assistantId = asst; collectionIds = []; selected = null;
        if (want) composer?.prefer(want, workspace === 'code' ? 'code' : 'text');
        if (media) { const op = sp.get('op'); if (op) tick().then(() => composer?.setOp(op)); if (sp.get('preset') === 'iphone_photo') tick().then(() => composer?.setPreset(true)); }
        goto(base, { replace: true, reset: false });
        tick().then(() => composer?.focus());
      } else if (asst && !routeId) assistantId = asst;
    });
  });

  // ---- actions ---------------------------------------------------------------------------------------------------
  const textOp = $derived<Operation>(workspace === 'code' ? 'code' : 'text');
  function created(id: string) { goto(`${base}/${id}`, { replace: true, reset: false }); }
  function send(turn: any) { thread.send(turn, { assistantId, collectionIds }, created); }
  async function ctxChanged() {
    if (assistantId) get(`/api/assistants/${assistantId}`).then((a) => a.model_id && composer?.prefer(a.model_id, textOp)).catch(() => {});
    try { await thread.saveContext(assistantId, collectionIds); } catch (e) { toast(errText(e), 'bad'); }
  }
  const opFor = (m: Msg) => (m.kind === 'image' || m.kind === 'video' ? m.kind : (m.meta?.operation as 'image' | 'video' | undefined) ?? 'text');
  const act: MsgActions = {
    async copy(m) { try { await navigator.clipboard.writeText(m.content); toast('Copied'); } catch { toast('Copy is not available here.', 'bad'); } },
    edit(m, text) {
      const op = opFor(m) as 'text' | 'image' | 'video';
      const o: Operation = op === 'text' ? textOp : op;
      const cur = composer?.current();
      thread.edit(m, { content: text, attachments: (m.attachments ?? []).filter((a) => !a.missing), operation: op, modelId: composer?.modelFor(o) ?? '',
        modelLabel: catalog(o)?.models?.find((x: any) => x.id === composer?.modelFor(o))?.alias ?? cur?.modelLabel ?? 'Assistant',
        sourceId: (m.attachments ?? []).find((a) => a.kind === 'image')?.id ?? null,
        preset: m.meta?.preset?.id ? { id: m.meta.preset.id, scenes: m.meta.preset.scenes ?? [] } : undefined });
    },
    regenerate(m) {
      const media = m.kind === 'image' || m.kind === 'video';
      const id = media ? '' : composer?.modelFor(textOp) ?? '';
      thread.regenerate(m, { content: '', attachments: [], operation: media ? (m.kind as 'image') : 'text', modelId: id,
        modelLabel: media ? m.model ?? 'Assistant' : catalog(textOp)?.models?.find((x: any) => x.id === id)?.alias ?? m.model ?? 'Assistant' });
    },
    version: (id) => thread.showVersion(id),
    details: (m) => (selected = selected === m.id ? null : m.id),
    useSource: (a) => composer?.useSource(a),
    mediaChanged(m, job) {
      const status = ({ completed: 'complete', failed: 'error', cancelled: 'stopped' } as Record<string, string>)[job.status] ?? m.status;
      thread.patchMessage(m.id, { media: job, status });
    },
    icon(modelId) {
      for (const o of [textOp, 'image', 'video'] as Operation[]) { const hit = catalog(o)?.models?.find((x: any) => x.id === (modelId ?? composer?.modelFor(textOp))); if (hit) return hit.icon ?? hit.profile?.icon; }
      return null;
    }
  };
  const labelFor = (m: { model: string | null }) => (flags.model_names === false ? brand.app_name || 'Assistant' : m.model ?? 'Assistant');

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && thread.busy && !document.querySelector('[role="dialog"]')) { e.preventDefault(); thread.stop(); }
  }
  // Files dropped anywhere in the app are attached by the composer's drop target (MediaDropzone).

  // ---- technical details --------------------------------------------------------------------------------------
  const techOn = $derived(!!ui.tech.drawer && !!app.me.user.prefs.show_tech);
  const detail = $derived(thread.messages.find((m) => m.id === selected) ?? [...thread.messages].reverse().find((m) => m.role === 'assistant' && Object.keys(m.tech ?? {}).length));
  let resources = $state<any>(null);
  $effect(() => {
    if (!techOn || !ui.tech.fields.some((f: string) => f.startsWith('tech.resources'))) { resources = null; return; }
    let on = true; const t0 = () => get('/api/system/resources').then((r) => on && (resources = r)).catch(() => on && (resources = null));
    t0(); const t = setInterval(t0, 4000); return () => { on = false; clearInterval(t); };
  });
  async function toggleTech() { try { await patch('/api/me', { show_tech: !app.me.user.prefs.show_tech }); await loadMe(); } catch (e) { toast(errText(e), 'bad'); } }

  onMount(() => { if (!routeId) composer?.focus(); });
  const welcome = $derived(brand.welcome_title || (workspace === 'code' ? 'What are you building?' : 'How can I help?'));
</script>

<svelte:window onkeydown={onKey} />
<svelte:head><title>{thread.id ? `${thread.title} · ` : ''}{brand.tab_title || 'Semechka'}</title></svelte:head>

{#if !ui.modes[workspace]?.enabled}
  <div class="blocked"><h1>{workspace === 'code' ? 'Code' : 'Chat'}</h1><div class="notice warn" role="status"><div><strong>{workspace === 'code' ? 'The code workspace is' : 'Chat is'} not available to you.</strong> {ui.modes[workspace]?.reason}</div></div></div>
{:else}
  <div class="chat" class:drawer={techOn}>
    <section class="conv" aria-label="Conversation">
      <div class="convbar">
        <span class="cb-l"></span>
        {#if !media}<div class="modes" role="radiogroup" aria-label="Mode of this conversation" data-testid="mode-switch">
          <button type="button" role="radio" aria-checked={mode === 'chat'} class:on={mode === 'chat'} disabled={thread.busy && mode !== 'chat'} onclick={() => setMode('chat')}><Icon name="chat" size={14} /> Chat</button>
          <button type="button" role="radio" aria-checked={mode === 'work'} class:on={mode === 'work'} disabled={thread.busy && mode !== 'work'} onclick={() => setMode('work')}
            title={thread.busy ? 'Wait for the reply to finish' : 'The agent works in a sandbox with files and a terminal'}><Icon name="work" size={14} /> Work</button>
        </div>{:else}<span class="mtitle">Conversation studio</span>{/if}
        <span class="cb-r">{#if ui.tech.drawer && mode === 'chat'}<button class="btn ghost sm" aria-pressed={techOn} onclick={toggleTech} title="Technical details for replies"><Icon name="cpu" size={15} /> {techOn ? 'Hide details' : 'Details'}</button>{/if}</span>
      </div>
      {#if mode === 'work' && !media}
        <WorkPane cid={thread.id} ensure={ensureConversation} workable={!thread.ephemeral} why={workWhy} />
      {:else}
      <ThreadView {thread} {labelFor} modelState={(id) => catalog(textOp)?.models?.find((m: any) => m.id === id)?.state} {act} canBranch={!!ui.composer.branching} {techOn} showSources={flags.tool_indicators !== false} {selected}>
        {#snippet empty()}
          <div class="empty">
            <h1>{thread.ephemeral ? 'This conversation will not be saved' : welcome}</h1>
            {#if thread.ephemeral}
              <p class="muted">Nothing you write here is stored. Read how it is handled before you send your first message.</p>
              <div class="panel"><div class="panel-b"><ProtectionPanel modelId={composer?.modelFor(textOp)} ephemeral /></div></div>
            {:else}
              {#if brand.welcome_text}<p class="muted">{brand.welcome_text}</p>{/if}
              {#if workspace === 'code'}<p class="small muted">The code workspace writes and explains code. It cannot run it, open your files or reach the internet, so test what it gives you.</p>{/if}
              {#if ui.suggested_prompts?.length}
                <div class="sugg">{#each ui.suggested_prompts as s (s)}<button class="btn" onclick={() => composer?.setText(s)}>{s}</button>{/each}</div>
              {/if}
            {/if}
          </div>
        {/snippet}
      </ThreadView>
      <div class="dock">
        <Composer bind:this={composer} {thread} {workspace} textOnly={!media} bind:assistantId bind:collectionIds onsend={send} onstop={() => thread.stop()} onctx={ctxChanged} onprotect={() => (protOpen = true)} />
      </div>
      {/if}
    </section>

    {#if techOn && mode === 'chat'}
      <aside class="details" aria-label="Technical details">
        <div class="dh"><h2>Technical details</h2><button class="btn ghost icon sm" aria-label="Close technical details" onclick={toggleTech}><Icon name="x" size={16} /></button></div>
        <p class="tiny muted">Only the fields your administrator allows. Reading them gives no control over the system.</p>
        {#if detail && Object.keys(detail.tech ?? {}).length}
          <h3 class="small">{selected ? 'Selected reply' : 'Latest reply'}</h3>
          {#if trinodalPath(detail.tech.trinodal)}<p class="small tpath">Path: {trinodalPath(detail.tech.trinodal)}</p>{/if}
          <dl class="kv">
            {#each Object.entries(detail.tech).filter(([k]) => k !== '_unavailable') as [k, v] (k)}
              {#if k === 'trinodal'}{#each trinodalRows(v) as [tl, tv] (tl)}<dt>{tl}</dt><dd>{tv}</dd>{/each}
              {:else}<dt>{TECH_LABELS[k] ?? k}</dt><dd>{#if k === 'gen_params' && v && typeof v === 'object'}{#each Object.entries(v) as [a, b2] (a)}<div>{a}: {b2}</div>{/each}{:else}{fmtTech(k, v)}{/if}</dd>{/if}
            {/each}
            {#each detail.tech._unavailable ?? [] as k (k)}<dt>{TECH_LABELS[k] ?? k}</dt><dd class="muted na">Not supplied by this backend</dd>{/each}
          </dl>
        {:else}<p class="small muted">Details appear here after a reply.</p>{/if}
        {#if resources}
          <h3 class="small">Live resources</h3>
          {#each resources.gpus ?? [] as g, i (i)}<dl class="kv"><dt>GPU</dt><dd>{g.name}</dd><dt>{g.unified_memory ? 'Memory' : 'VRAM'}</dt><dd>{g.unified_memory ? 'Unified with system memory' : `${g.vram_used_mb} / ${g.vram_total_mb} MiB`}</dd><dt>Load</dt><dd>{g.utilization_pct}%</dd><dt>Temperature</dt><dd>{g.temperature_c} °C</dd></dl>{/each}
          {#if resources.gpus === null}<p class="small muted">{resources.gpu_note}</p>{/if}
          {#if resources.host}<dl class="kv"><dt>CPU threads</dt><dd>{resources.host.cpu_count}</dd><dt>Memory free</dt><dd>{resources.host.memory_available_mb} / {resources.host.memory_total_mb} MiB</dd></dl>{/if}
        {/if}
      </aside>
    {/if}
  </div>
{/if}

<Modal bind:open={protOpen} title="How is this conversation handled?" wide><ProtectionPanel modelId={composer?.modelFor(textOp)} ephemeral={thread.ephemeral} />{#snippet footer()}<button class="btn" onclick={() => (protOpen = false)}>Close</button>{/snippet}</Modal>

<style>
  .blocked { padding: 24px; max-width: 640px; } .blocked h1 { margin-bottom: 12px; }
  .chat { flex: 1; display: grid; grid-template-columns: minmax(0, 1fr); min-height: 0; height: 100%; }
  .chat.drawer { grid-template-columns: minmax(0, 1fr) 300px; }
  .conv { display: flex; flex-direction: column; min-width: 0; min-height: 0; position: relative; }
  .convbar { flex: none; display: grid; grid-template-columns: 1fr auto 1fr; align-items: center; padding: 6px 12px 0; min-height: 40px; }
  .cb-r { justify-self: end; } .mtitle { font-size: 14px; font-weight: 500; }
  .modes { display: inline-flex; padding: 2px; gap: 2px; background: var(--bg-2); border: 1px solid var(--border); border-radius: 999px; }
  .modes button { all: unset; box-sizing: border-box; display: inline-flex; align-items: center; gap: 6px; min-height: 28px; padding: 0 14px; border-radius: 999px; cursor: pointer; font-size: 13px; font-weight: 500; color: var(--fg-muted); }
  .modes button:hover:not(:disabled) { color: var(--fg); }
  .modes button.on { background: var(--card-bg); color: var(--fg); box-shadow: var(--shadow-sm); }
  .modes button:disabled { opacity: 0.5; cursor: not-allowed; } .modes button:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
  @media (pointer: coarse) { .modes button { min-height: 40px; } }
  .dock { flex: none; padding: 0 24px 14px; background: var(--bg); }
  .dock > :global(*) { max-width: var(--msg-w); margin: 0 auto; }
  /* the welcome sits in the lower-middle of the pane, just above the composer */
  .empty { margin-top: auto; padding: 48px 0 28px; display: grid; gap: 12px; align-content: end; justify-items: center; text-align: center; }
  .empty h1 { font-size: 28px; font-weight: 400; letter-spacing: -0.015em; }
  .empty p { margin: 0; max-width: 60ch; }
  .empty .panel { text-align: left; width: 100%; }
  .sugg { display: grid; gap: 8px; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); width: 100%; margin-top: 12px; }
  .sugg .btn { white-space: normal; text-align: left; justify-content: flex-start; align-items: flex-start; height: auto; padding: 12px 14px; line-height: 1.4;
    background: var(--card-bg); border-radius: var(--radius-composer); box-shadow: none; color: var(--fg-2); font-weight: 400; }
  .details { border-left: 1px solid var(--border); padding: 12px 16px; overflow: auto; background: var(--card-bg); display: grid; gap: 8px; align-content: start; }
  .dh { display: flex; align-items: center; justify-content: space-between; } .dh h2 { font-size: 15px; }
  .details h3 { margin-top: 8px; }
  .tpath { margin: 0; overflow-wrap: anywhere; }
  .na { font-family: var(--font); }
  @media (max-width: 1099px) {
    .chat.drawer { grid-template-columns: minmax(0, 1fr); }
    .details { position: fixed; z-index: 40; left: 0; right: 0; bottom: 0; max-height: 45vh; border-left: 0; border-top: 1px solid var(--border-strong); box-shadow: var(--shadow); }
  }
  @media (max-width: 719px) { .dock { padding: 0 8px 8px; } .empty h1 { font-size: 24px; } .convbar { padding: 4px 8px 0; } }
</style>
