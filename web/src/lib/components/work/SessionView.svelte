<script lang="ts">
  // One session: header with its state, the live conversation and timeline, and the workspace panel.
  import { onMount, tick, untrack } from 'svelte';
  import { Feed } from '$lib/work/feed.svelte';
  import { getSession, getState, listArtifacts, sendMessage, control, problem, type Problem } from '$lib/work/client';
  import { buildItems, pendingApprovals } from '$lib/work/events';
  import { sessionStatus, containerState, networkLabel, autonomyLabel, formatBytes } from '$lib/work/format';
  import { work, patchRow } from '$lib/work/store.svelte';
  import type { WorkSession, WorkState, WorkArtifact, WorkEvent } from '$lib/work/types';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import Timeline from './Timeline.svelte';
  import WorkComposer from './WorkComposer.svelte';
  import FilesPanel from './FilesPanel.svelte';
  import ArtifactsPanel from './ArtifactsPanel.svelte';
  import JobsPanel from './JobsPanel.svelte';

  let { id, now }: { id: string; now: number } = $props();

  const feed = new Feed();
  let session = $state<WorkSession | null>(null);
  let wstate = $state<WorkState | null>(null);
  let loadErr = $state<Problem | null>(null), loading = $state(true);
  let artifacts = $state<WorkArtifact[]>([]), artLoaded = $state(false), artErr = $state('');
  let filesKey = $state(0), sendErr = $state(''), acting = $state(''), confirmCancel = $state(false);
  let tab = $state<'files' | 'artifacts' | 'jobs' | 'skills'>('files');
  let visited = $state<Record<string, boolean>>({ files: true });
  let mobile = $state<'chat' | 'panel'>('chat');
  let panelOpen = $state(true);
  let scroller: HTMLDivElement | undefined = $state();
  let stick = $state(true);
  let composer: WorkComposer | undefined = $state();

  const items = $derived(buildItems(feed.events));
  const savedMap = $derived(Object.fromEntries(artifacts.map((a) => [a.id, !!a.saved_to_library])));
  const markSaved = (aid: string) => { artifacts = artifacts.map((a) => (a.id === aid ? { ...a, saved_to_library: true } : a)); };
  const waiting = $derived(pendingApprovals(items));
  const status = $derived(wstate?.status ?? session?.status ?? 'idle');
  const st = $derived(sessionStatus(status));
  const cs = $derived(containerState(wstate?.container?.state ?? session?.container_state ?? 'absent'));
  const network = $derived(wstate?.network ?? session?.network ?? 'none');
  const autonomy = $derived(wstate?.autonomy ?? session?.autonomy ?? 'contained');
  const model = $derived(work.options?.models.find((m) => m.id === (wstate?.model_id ?? session?.model_id)));
  const modelName = $derived(session?.model_alias ?? model?.alias ?? session?.model_id ?? 'Assistant');
  const jobs = $derived(wstate?.jobs ?? []);
  const activeJobs = $derived(jobs.filter((j) => j.status === 'queued' || j.status === 'running').length);
  const attached = $derived((session?.skill_ids ?? []).map((sid) => work.options?.skills.find((s) => s.id === sid) ?? { id: sid, name: sid, title: sid, description: '', category: '', scope: 'user' as const, enabled: true }));
  const usage = $derived(wstate?.usage);
  const run = $derived(wstate?.active_run);

  // ---- loading ---------------------------------------------------------------------------------------------------
  async function loadSession() {
    loading = true;
    try { session = await getSession(id); loadErr = null; }
    catch (e) { loadErr = problem(e); }
    finally { loading = false; }
  }
  let stateBusy = false;
  async function refreshState() {
    if (stateBusy) return; stateBusy = true;
    try {
      const s = await getState(id);
      wstate = s;
      patchRow(id, { status: s.status, container_state: s.container.state });
    } catch (e) { const p = problem(e); if (p.kind === 'notfound' || p.kind === 'denied') loadErr = p; }
    finally { stateBusy = false; }
  }
  async function refreshArtifacts() {
    try { artifacts = await listArtifacts(id); artErr = ''; } catch (e) { artErr = problem(e).message; }
    finally { artLoaded = true; }
  }
  let debounce: ReturnType<typeof setTimeout>;
  feed.onnew = (fresh: WorkEvent[]) => {
    clearTimeout(debounce);
    debounce = setTimeout(refreshState, 350);
    if (fresh.some((e) => e.kind === 'artifact')) refreshArtifacts();
    if (fresh.some((e) => e.kind === 'tool_result' || e.kind === 'artifact' || e.kind === 'state')) filesKey++;
  };

  $effect(() => {
    id;
    untrack(() => {
      session = null; wstate = null; artifacts = []; artLoaded = false; loadErr = null; stick = true; sendErr = ''; visited = { files: true }; tab = 'files'; mobile = 'chat';
      loadSession().then(() => { if (!loadErr) { refreshState(); refreshArtifacts(); } });
      feed.start(id);
    });
    return () => { feed.stop(); clearTimeout(debounce); };
  });
  $effect(() => { if (feed.phase === 'denied' || feed.phase === 'notfound') loadErr = { kind: feed.phase, message: feed.message }; });

  // state keeps moving while the session works (jobs report progress); poll it, slower when idle
  onMount(() => {
    let t: ReturnType<typeof setTimeout>;
    const loop = () => {
      if (!document.hidden && !loadErr) refreshState();
      t = setTimeout(loop, st.busy || activeJobs ? 4000 : 20000);
    };
    t = setTimeout(loop, 4000);
    const wake = () => { if (!document.hidden) { feed.poke(); refreshState(); } };
    const off = () => feed.poke(); // notice a lost connection now instead of when the held request times out
    document.addEventListener('visibilitychange', wake); addEventListener('online', wake); addEventListener('offline', off);
    try { panelOpen = localStorage.getItem('sem.work.panel') !== '0'; } catch { /* storage unavailable */ }
    return () => { clearTimeout(t); document.removeEventListener('visibilitychange', wake); removeEventListener('online', wake); removeEventListener('offline', off); };
  });

  // when the session stops working, files and artifacts have probably changed
  let wasBusy = false;
  $effect(() => { const b = st.busy; if (wasBusy && !b) { untrack(() => { filesKey++; refreshArtifacts(); }); } wasBusy = b; });

  // ---- scrolling -------------------------------------------------------------------------------------------------
  const onScroll = () => { if (scroller) stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 90; };
  $effect(() => {
    feed.events;
    if (untrack(() => stick)) tick().then(() => scroller?.scrollTo({ top: scroller.scrollHeight, behavior: 'auto' }));
  });
  const jump = () => { stick = true; scroller?.scrollTo({ top: scroller.scrollHeight, behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' }); };

  // ---- actions ---------------------------------------------------------------------------------------------------
  async function send(text: string): Promise<boolean> {
    sendErr = '';
    try { await sendMessage(id, text); feed.poke(); stick = true; refreshState(); return true; }
    catch (e) { sendErr = problem(e).message; return false; }
  }
  async function act(a: 'pause' | 'resume' | 'cancel') {
    acting = a;
    try { await control(id, a); feed.poke(); await refreshState(); if (a === 'cancel') confirmCancel = false; }
    catch (e) { toast(problem(e).message, 'bad'); confirmCancel = false; }
    finally { acting = ''; }
  }
  function goApproval() {
    const first = waiting[0]; if (!first) return;
    mobile = 'chat';
    tick().then(() => { const el = document.querySelector<HTMLElement>(`[data-key="${CSS.escape(first.key)}"]`); el?.scrollIntoView({ block: 'center' }); el?.querySelector<HTMLElement>('button')?.focus(); });
  }
  function togglePanel() { panelOpen = !panelOpen; try { localStorage.setItem('sem.work.panel', panelOpen ? '1' : '0'); } catch { /* storage unavailable */ } }

  const TABS = $derived([
    { id: 'files' as const, label: 'Files' },
    { id: 'artifacts' as const, label: 'Artifacts', n: artifacts.length },
    { id: 'jobs' as const, label: 'Jobs', n: activeJobs || undefined },
    { id: 'skills' as const, label: 'Skills', n: session?.skill_ids.length || undefined }
  ]);
  function pickTab(t: typeof tab) { tab = t; visited[t] = true; }
  function tabKey(e: KeyboardEvent) {
    const i = TABS.findIndex((t) => t.id === tab);
    let n = -1;
    if (e.key === 'ArrowRight') n = (i + 1) % TABS.length; else if (e.key === 'ArrowLeft') n = (i + TABS.length - 1) % TABS.length;
    else if (e.key === 'Home') n = 0; else if (e.key === 'End') n = TABS.length - 1;
    if (n < 0) return;
    e.preventDefault(); pickTab(TABS[n].id);
    tick().then(() => document.getElementById(`wt-${TABS[n].id}`)?.focus());
  }
  const canPause = $derived(status === 'running' || status === 'awaiting_approval');
  const canResume = $derived(status === 'paused');
  const canCancel = $derived(status === 'running' || status === 'awaiting_approval' || status === 'paused');
  const title = $derived(session?.title || 'Untitled session');
</script>

<svelte:head><title>{session ? `${title} · Work` : 'Work'}</title></svelte:head>

{#if loading && !session}
  <div class="state" role="status"><p class="muted">Loading session…</p></div>
{:else if loadErr && !session}
  <div class="state">
    <div class="notice {loadErr.kind === 'offline' ? 'warn' : 'bad'}" role="alert">
      <div>
        <strong>{loadErr.kind === 'notfound' ? 'This session was not found.' : loadErr.kind === 'denied' ? 'You cannot open this session.' : loadErr.kind === 'offline' ? 'You are offline.' : 'The session could not be loaded.'}</strong>
        {loadErr.message}
        <div class="row" style="margin-top:8px">
          {#if loadErr.kind === 'notfound' || loadErr.kind === 'denied'}<a class="btn sm" href="/work">Back to sessions</a>{:else}<button class="btn sm" onclick={loadSession}>Try again</button>{/if}
        </div>
      </div>
    </div>
  </div>
{:else if session}
  <div class="sv">
    <header class="hd">
      <a class="back btn ghost sm" href="/work" aria-label="Back to all sessions"><Icon name="chevron" size={14} />Sessions</a>
      <div class="titlebox">
        <h1 title={title}>{title}</h1>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
        <div class="chips" role="list" aria-label="Session details" tabindex="0">
          <span role="listitem" class="chip {st.tone === 'plain' ? '' : st.tone}">{st.label}{run?.step_n != null && st.busy ? ` · step ${run.step_n}${run.max_steps ? ` of ${run.max_steps}` : ''}` : ''}</span>
          <span role="listitem" class="chip {cs.tone === 'plain' ? '' : cs.tone}" title={wstate?.container?.note}>{cs.label}</span>
          <span role="listitem" class="chip {network === 'internet' ? 'warn' : ''}" title={network === 'internet' ? 'The container can reach the internet, and other devices on your local network.' : 'The container has no network.'}>{networkLabel(network)}</span>
          <span role="listitem" class="chip">{autonomyLabel(autonomy)}</span>
          <span role="listitem" class="chip" title="Model that runs the session">{modelName}</span>
          {#if usage && (usage.steps != null || usage.tokens_in != null || usage.tokens_out != null)}
            <span role="listitem" class="chip" title="Used so far in this session">{[usage.steps != null ? `${usage.steps} steps` : '', usage.tokens_in != null || usage.tokens_out != null ? `${((usage.tokens_in ?? 0) + (usage.tokens_out ?? 0)).toLocaleString()} tokens` : ''].filter(Boolean).join(' · ')}</span>
          {/if}
        </div>
      </div>
      <div class="acts">
        {#if canPause}<button class="btn sm" disabled={!!acting} onclick={() => act('pause')}><Icon name="pause" size={14} /> {acting === 'pause' ? 'Pausing…' : 'Pause'}</button>{/if}
        {#if canResume}<button class="btn sm primary" disabled={!!acting} onclick={() => act('resume')}><Icon name="play" size={14} /> {acting === 'resume' ? 'Resuming…' : 'Resume'}</button>{/if}
        {#if canCancel}<button class="btn sm danger" disabled={!!acting} onclick={() => (confirmCancel = true)}><Icon name="stop" size={14} /> Cancel run</button>{/if}
        <button class="btn sm icon panelbtn" aria-label={panelOpen ? 'Hide the workspace panel' : 'Show the workspace panel'} aria-expanded={panelOpen} onclick={togglePanel}><Icon name="sidebar" size={15} /></button>
      </div>
    </header>

    <div class="switch" role="group" aria-label="Show">
      <button class="seg" aria-pressed={mobile === 'chat'} onclick={() => (mobile = 'chat')}>Conversation</button>
      <button class="seg" aria-pressed={mobile === 'panel'} onclick={() => (mobile = 'panel')}>Workspace{#if waiting.length}<span class="chip warn dot">{waiting.length}</span>{/if}</button>
    </div>

    {#if feed.phase === 'offline' || feed.phase === 'retrying'}
      <div class="notice warn conn" role="status"><div>{feed.phase === 'offline' ? 'You are offline.' : 'The connection to the server dropped.'} Showing what was received so far. Reconnecting automatically{feed.fails > 1 ? ` (attempt ${feed.fails})` : ''}. <span class="muted">{feed.phase === 'retrying' ? feed.message : ''}</span></div></div>
    {/if}

    <div class="body" class:nopanel={!panelOpen} data-mobile={mobile}>
      <section class="main" aria-label="Conversation">
        <div class="scroll" bind:this={scroller} onscroll={onScroll} tabindex="-1">
          <div class="col">
            {#if session.objective}<details class="obj"><summary>Task</summary><p class="small">{session.objective}</p></details>{/if}
            {#if feed.phase === 'loading' && !items.length}<p class="muted" role="status">Loading the timeline…</p>
            {:else if !items.length}
              <div class="empty"><h2>Nothing has happened yet</h2><p class="muted">Describe the task in the box below. The container is created the first time the session needs one, and everything it does shows up here as it happens.</p></div>
            {:else}
              <Timeline {items} sessionId={id} {now} {modelName} saved={savedMap} onsaved={markSaved} onanswered={() => { feed.poke(); refreshState(); }} />
            {/if}
          </div>
        </div>
        {#if !stick && items.length}<button class="btn sm jump" onclick={jump}><Icon name="down" size={14} /> Latest</button>{/if}
        <div class="foot">
          {#if waiting.length}<div class="notice warn wait" role="status"><div>{waiting.length === 1 ? 'One action is waiting for your approval.' : `${waiting.length} actions are waiting for your approval.`} <button class="linkish" onclick={goApproval}>Review</button></div></div>{/if}
          {#if sendErr}<p class="err" role="alert">{sendErr}</p>{/if}
          <WorkComposer bind:this={composer} onsend={send} placeholder={status === 'idle' ? 'Describe the task or ask a question' : 'Message the session'} />
        </div>
      </section>

      <aside class="panel" aria-label="Workspace">
        <div class="tabs" role="tablist" aria-label="Workspace sections" tabindex="-1" onkeydown={tabKey}>
          {#each TABS as t (t.id)}
            <button role="tab" id={`wt-${t.id}`} aria-selected={tab === t.id} aria-controls={`wp-${t.id}`} tabindex={tab === t.id ? 0 : -1} class="tab" onclick={() => pickTab(t.id)}>{t.label}{#if t.n}<span class="n">{t.n}</span>{/if}</button>
          {/each}
        </div>
        <div class="tp">
          {#if visited.files}<div role="tabpanel" id="wp-files" aria-labelledby="wt-files" hidden={tab !== 'files'}><FilesPanel sessionId={id} refreshKey={filesKey} uploadMaxMb={work.options?.limits?.upload_max_mb} {usage} /></div>{/if}
          {#if tab === 'artifacts'}<div role="tabpanel" id="wp-artifacts" aria-labelledby="wt-artifacts"><ArtifactsPanel {artifacts} loaded={artLoaded} error={artErr} onretry={refreshArtifacts} onsaved={markSaved} /></div>{/if}
          {#if tab === 'jobs'}<div role="tabpanel" id="wp-jobs" aria-labelledby="wt-jobs"><JobsPanel {jobs} loaded={!!wstate} /></div>{/if}
          {#if tab === 'skills'}
            <div role="tabpanel" id="wp-skills" aria-labelledby="wt-skills">
              {#if !attached.length}<p class="muted small">No skills are attached to this session.</p>
              {:else}<ul class="sk">{#each attached as k (k.id)}<li><strong>{k.title || k.name}</strong> {#if k.category}<span class="chip">{k.category.replace(/-/g, ' ')}</span>{/if}<p class="small muted">{k.description}</p></li>{/each}</ul>{/if}
              <p class="tiny muted">Attached when the session was created. The model opens a skill's full text only when it needs it. <a href="/work/skills">Manage skills</a></p>
            </div>
          {/if}
        </div>
      </aside>
    </div>
  </div>
{/if}

<Modal bind:open={confirmCancel} title="Cancel this run?">
  <p>The model stops what it is doing now. Files and everything it already did stay as they are, and you can send another message afterwards to continue.</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirmCancel = false)}>Keep working</button><button class="btn danger solid" disabled={acting === 'cancel'} onclick={() => act('cancel')}>{acting === 'cancel' ? 'Cancelling…' : 'Cancel run'}</button>{/snippet}
</Modal>

<style>
  .state { padding: var(--s5); max-width: 640px; }
  .sv { display: flex; flex-direction: column; flex: 1; min-height: 0; min-width: 0; }
  .hd { display: flex; align-items: flex-start; gap: var(--s3); padding: var(--s3) var(--s4); border-bottom: 1px solid var(--border); flex-wrap: wrap; }
  .back { display: none; grid-area: back; justify-self: start; margin-left: -8px; } .back :global(svg) { transform: rotate(180deg); }
  .titlebox { flex: 1 1 320px; min-width: 0; display: grid; gap: 6px; }
  h1 { font-size: 18px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .chips { display: flex; flex-wrap: wrap; gap: 6px; min-width: 0; }
  .acts { display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
  .switch { display: none; gap: 0; padding: var(--s2) var(--s4); border-bottom: 1px solid var(--border); }
  .seg { flex: 1; min-height: 36px; border: 1px solid var(--border-strong); background: var(--btn-bg); color: var(--btn-fg); font: 500 14px var(--font); cursor: pointer; display: inline-flex; gap: 6px; justify-content: center; align-items: center; }
  .seg:first-child { border-radius: var(--radius-chip) 0 0 var(--radius-chip); } .seg:last-child { border-radius: 0 var(--radius-chip) var(--radius-chip) 0; border-left: 0; }
  .seg[aria-pressed='true'] { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-color: var(--btn-primary-bg); }
  .dot { margin-left: 2px; }
  .conn { margin: var(--s2) var(--s4) 0; }
  .body { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) 360px; }
  .body.nopanel { grid-template-columns: minmax(0, 1fr); } .body.nopanel .panel { display: none; }
  .main { position: relative; display: flex; flex-direction: column; min-height: 0; min-width: 0; }
  .scroll { flex: 1; min-height: 0; overflow-y: auto; padding: var(--s4); }
  .scroll:focus { outline: none; }
  .col { max-width: var(--msg-w); margin: 0 auto; display: grid; gap: var(--s3); }
  .obj { border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 6px 10px; background: var(--surface); } .obj summary { cursor: pointer; font-weight: 500; font-size: 13px; } .obj p { margin: 6px 0 0; white-space: pre-wrap; }
  .empty { padding: var(--s6) 0; } .empty h2 { margin-bottom: 6px; }
  .jump { position: absolute; right: var(--s4); bottom: 150px; box-shadow: var(--shadow); }
  .foot { flex: none; padding: var(--s2) var(--s4) var(--s3); max-width: calc(var(--msg-w) + 2 * var(--s4)); width: 100%; margin: 0 auto; }
  .wait { margin-bottom: var(--s2); padding: 6px 12px; }
  .panel { border-left: 1px solid var(--border); display: flex; flex-direction: column; min-height: 0; min-width: 0; background: var(--bg); }
  .tabs { display: flex; gap: 2px; padding: var(--s2) var(--s3) 0; border-bottom: 1px solid var(--border); overflow-x: auto; }
  .tab { all: unset; box-sizing: border-box; cursor: pointer; padding: 8px 10px; font: 500 13px var(--font); color: var(--fg-muted); border-bottom: 2px solid transparent; margin-bottom: -1px; display: inline-flex; gap: 6px; align-items: center; white-space: nowrap; min-height: 36px; }
  .tab:hover { color: var(--fg); } .tab[aria-selected='true'] { color: var(--fg); border-bottom-color: var(--accent); }
  .tab:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: var(--radius-sm); }
  .n { font-size: 11px; background: var(--surface-2); border-radius: var(--radius-chip); padding: 0 6px; line-height: 17px; color: var(--fg-2); font-variant-numeric: tabular-nums; }
  .tp { flex: 1; min-height: 0; overflow: auto; padding: var(--s3); }
  .sk { list-style: none; padding: 0; margin: 0 0 var(--s3); display: grid; gap: var(--s3); } .sk p { margin: 2px 0 0; }
  @media (max-width: 1099px) {
    .body, .body.nopanel { grid-template-columns: minmax(0, 1fr); }
    .panelbtn { display: none; }
    .switch { display: flex; }
    .body[data-mobile='chat'] .panel, .body.nopanel[data-mobile='panel'] .main { display: none; }
    .body[data-mobile='panel'] .main { display: none; }
    .body[data-mobile='panel'] .panel, .body.nopanel[data-mobile='panel'] .panel { display: flex; border-left: 0; }
  }
  @media (max-width: 899px) {
    .hd { display: grid; grid-template-columns: minmax(0, 1fr) auto; grid-template-areas: 'back acts' 'title title'; gap: 2px var(--s2); align-items: center; }
    .titlebox { grid-area: title; flex: none; } .acts { grid-area: acts; justify-self: end; } .chips { flex-wrap: nowrap; overflow-x: auto; padding-bottom: 4px; } .chips .chip { flex: none; }
    h1 { font-size: 16px; }
  }
  @media (max-width: 899px) { .back { display: inline-flex; } .hd { padding: var(--s2) var(--s3); } .scroll { padding: var(--s3); } .foot { padding: var(--s2) var(--s3) var(--s3); } .switch { padding: var(--s2) var(--s3); } }
</style>
