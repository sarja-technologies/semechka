<script lang="ts">
  // "View ComfyUI": a read-only picture of the exact workflow ComfyUI was given, with the live state of every node.
  // Nothing here sends anything to the server except GETs: it cannot change the job.
  import { onDestroy, untrack } from 'svelte';
  import { acquireLive } from '../../../studio/inspector/live.svelte';
  import { focusedNode } from '../../../studio/inspector/focus.svelte';
  import { layoutGraph, edgePath } from '../../../studio/inspector/layout.ts';
  import { fitView, zoomAt, centerOn, visibleWorld, intersects, pinch, toScreen, type View } from '../../../studio/inspector/view.ts';
  import { rollupStates, activeNodeId, baseId, isLink, shortValue, phaseOf, PHASE_LABEL, phaseTone } from '../../../studio/inspector/logic.ts';
  import { formatDuration } from '../../../work/format';
  import PanelShell from './PanelShell.svelte';
  import StateGlyph from './StateGlyph.svelte';
  import Icon from '../../Icon.svelte';

  let { jobId, onclose, mode = 'drawer', onnode }: {
    jobId: string;
    onclose?: () => void;
    /** 'drawer': fixed panel on the right (full-screen sheet on a phone). 'inline': fills the box the page gives it. */
    mode?: 'drawer' | 'inline';
    /** a card was picked (the shared focusedNode store is set as well) */
    onnode?: (nodeId: string) => void;
  } = $props();

  let handle = acquireLive(untrack(() => jobId));
  let live = $state.raw(handle.live);
  $effect(() => {
    const id = jobId;
    untrack(() => { if (handle.live.jobId !== id) { handle.release(); handle = acquireLive(id); live = handle.live; fitted = false; selected = null; } });
  });
  onDestroy(() => { handle.release(); cancelAnimationFrame(raf); });
  $effect(() => { const l = live; untrack(() => void l.ensureGraph()); });

  const doc = $derived(live.doc);
  const graph = $derived(live.graph);
  const lay = $derived(graph ? layoutGraph(graph) : null);
  const ids = $derived(lay ? lay.list.map((p) => p.id) : []);
  const stMap = $derived(rollupStates(doc?.node_states, ids));
  const stateOf = (id: string) => stMap.get(id) ?? { state: 'unknown', runs: null, total_ms: null, last_ms: null, expanded: 0, known: false };
  const active = $derived(activeNodeId(doc, graph));
  const paths = $derived(lay ? lay.edges.map((e) => ({ ...e, d: edgePath(lay.nodes.get(e.from)!, lay.nodes.get(e.to)!) })) : []);
  const phase = $derived(phaseOf(doc));
  const LABEL: Record<string, string> = { done: 'Done', running: 'Running', pending: 'Waiting', cached: 'Reused from cache', failed: 'Failed', unknown: 'No state yet' };

  // ---- view -----------------------------------------------------------------------------------------------------
  let view = $state<View>({ x: 0, y: 0, k: 1 });
  let vw = $state(0), vh = $state(0);
  const size = $derived({ w: vw, h: vh });
  let vp: HTMLDivElement | undefined = $state();
  let fitted = false;
  let follow = $state(true);
  let raf = 0;
  const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

  let anim: { from: View; to: View } | null = null;
  function setView(v: View, animate = false) {
    cancelAnimationFrame(raf); anim = null;
    if (!animate || reduced()) { view = v; return; }
    const a = (anim = { from: { ...view }, to: v });
    const t0 = performance.now(), D = 260;
    const step = (t: number) => {
      if (anim !== a) return;
      const u = Math.min(1, (t - t0) / D), e = 1 - (1 - u) ** 3;
      view = { x: a.from.x + (a.to.x - a.from.x) * e, y: a.from.y + (a.to.y - a.from.y) * e, k: a.from.k + (a.to.k - a.from.k) * e };
      if (u < 1) raf = requestAnimationFrame(step); else anim = null;
    };
    raf = requestAnimationFrame(step);
  }
  const fit = (animate = false) => { if (lay) setView(fitView({ w: lay.width, h: lay.height }, size), animate); };
  const zoomBy = (f: number) => setView(zoomAt(view, size.w / 2, size.h / 2, f), true);
  const hundred = () => { const c = { x: (size.w / 2 - view.x) / view.k, y: (size.h / 2 - view.y) / view.k }; setView(centerOn(view, c.x, c.y, size, 1), true); };

  // when the viewport changes size (the details pane opens, the phone turns) the point at its centre stays at its centre
  let lastW = 0, lastH = 0;
  $effect(() => {
    const w = vw, h = vh;
    untrack(() => {
      if (fitted && lastW > 0 && lastH > 0 && (w !== lastW || h !== lastH) && w > 0 && h > 0) {
        const dx = (w - lastW) / 2, dy = (h - lastH) / 2;
        view = { ...view, x: view.x + dx, y: view.y + dy };
        if (anim) { anim.from = { ...anim.from, x: anim.from.x + dx, y: anim.from.y + dy }; anim.to = { ...anim.to, x: anim.to.x + dx, y: anim.to.y + dy }; }
      }
      lastW = w; lastH = h;
    });
  });
  $effect(() => { if (lay && size.w > 0 && size.h > 0 && !fitted) untrack(() => { fitted = true; fit(); }); });

  /** Centre a node; zoom in to a readable size when the whole graph is on screen. */
  function goTo(id: string, animate = true) {
    const p = lay?.nodes.get(id);
    if (!p || size.w <= 0) return;
    const k = view.k < 0.45 ? 0.7 : view.k;
    setView(centerOn(view, p.x + p.w / 2, p.y + p.h / 2, size, k), animate);
  }

  // follow the node that is executing
  $effect(() => {
    const a = active, f = follow, l = lay;
    void size.w; void size.h;
    if (!f || !a || !l || size.w <= 0) return;
    untrack(() => goTo(a, true));
  });

  // ---- selection and the shared focus ----------------------------------------------------------------------------
  let selected = $state<string | null>(null);
  let paneOpen = $state(false);
  let tab = $state<'node' | 'raw'>('node');
  const resolve = (id: string | null) => (!id || !lay ? null : lay.nodes.has(id) ? id : lay.nodes.has(baseId(id)) ? baseId(id) : null);
  function select(id: string, center: boolean) {
    selected = id; paneOpen = true; tab = 'node';
    if (center) { follow = false; goTo(id); }
  }
  let handledSeq = -1;
  $effect(() => {
    const s = focusedNode.seq, l = lay;
    if (!l || s === handledSeq) return;
    const id = resolve(untrack(() => focusedNode.id));
    handledSeq = s;
    if (id) untrack(() => select(id, true));
  });
  // a node picked in the inspector's list (id set without a new request) is shown selected too
  $effect(() => { const id = resolve(focusedNode.id); if (id && id !== untrack(() => selected) && lay) untrack(() => { selected = id; }); });

  let moved = false;
  function cardClick(id: string) {
    if (moved) return;
    select(id, false);
    focusedNode.id = id;
    onnode?.(id);
  }

  // ---- pointer, wheel, keys ---------------------------------------------------------------------------------------
  const pts = new Map<number, { x: number; y: number }>();
  let drag: { sx: number; sy: number; vx: number; vy: number } | null = null;
  const rel = (p: { x: number; y: number }) => { const r = vp!.getBoundingClientRect(); return { x: p.x - r.left, y: p.y - r.top }; };
  function pdown(e: PointerEvent) {
    if (e.pointerType === 'mouse' && e.button !== 0) return;
    pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
    cancelAnimationFrame(raf);
    if (pts.size === 1) { drag = { sx: e.clientX, sy: e.clientY, vx: view.x, vy: view.y }; moved = false; } else drag = null;
    window.addEventListener('pointermove', pmove); window.addEventListener('pointerup', pup); window.addEventListener('pointercancel', pup);
  }
  function pmove(e: PointerEvent) {
    const prev = pts.get(e.pointerId);
    if (!prev || !vp) return;
    const cur = { x: e.clientX, y: e.clientY };
    if (pts.size === 2) {
      const otherId = [...pts.keys()].find((i) => i !== e.pointerId)!;
      const other = pts.get(otherId)!;
      view = pinch(view, rel(prev), rel(other), rel(cur), rel(other));
      pts.set(e.pointerId, cur); moved = true;
      return;
    }
    if (drag) {
      const dx = cur.x - drag.sx, dy = cur.y - drag.sy;
      if (!moved && Math.hypot(dx, dy) < 4) return;
      if (!moved) { moved = true; follow = false; }
      view = { ...view, x: drag.vx + dx, y: drag.vy + dy };
    }
    pts.set(e.pointerId, cur);
  }
  function pup(e: PointerEvent) {
    pts.delete(e.pointerId);
    if (pts.size === 0) { window.removeEventListener('pointermove', pmove); window.removeEventListener('pointerup', pup); window.removeEventListener('pointercancel', pup); drag = null; setTimeout(() => (moved = false), 0); }
    else if (pts.size === 1) { const p = [...pts.values()][0]; drag = { sx: p.x, sy: p.y, vx: view.x, vy: view.y }; }
  }
  onDestroy(() => { window.removeEventListener('pointermove', pmove); window.removeEventListener('pointerup', pup); window.removeEventListener('pointercancel', pup); });
  $effect(() => {
    const el = vp;
    if (!el) return;
    const h = (e: WheelEvent) => {
      e.preventDefault();
      cancelAnimationFrame(raf);
      const r = el.getBoundingClientRect();
      const f = Math.exp(-e.deltaY * (e.deltaMode === 1 ? 16 : 1) * (e.ctrlKey ? 0.01 : 0.0015));
      view = zoomAt(view, e.clientX - r.left, e.clientY - r.top, f);
    };
    el.addEventListener('wheel', h, { passive: false });
    return () => el.removeEventListener('wheel', h);
  });
  function key(e: KeyboardEvent) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement || e.ctrlKey || e.metaKey || e.altKey) return;
    const step = e.shiftKey ? 240 : 80;
    const pan = (dx: number, dy: number) => { follow = false; setView({ ...view, x: view.x + dx, y: view.y + dy }); e.preventDefault(); };
    switch (e.key) {
      case 'ArrowLeft': return pan(step, 0);
      case 'ArrowRight': return pan(-step, 0);
      case 'ArrowUp': return pan(0, step);
      case 'ArrowDown': return pan(0, -step);
      case '+': case '=': e.preventDefault(); return zoomBy(1.25);
      case '-': case '_': e.preventDefault(); return zoomBy(0.8);
      case '0': e.preventDefault(); return hundred();
      case 'f': case 'F': e.preventDefault(); follow = false; return fit(true);
    }
  }
  function cardFocus(id: string) {
    if (vp) { vp.scrollLeft = 0; vp.scrollTop = 0; }
    const p = lay?.nodes.get(id);
    if (!p) return;
    const s = toScreen(view, p.x, p.y);
    if (s.x < 0 || s.y < 0 || s.x + p.w * view.k > size.w || s.y + p.h * view.k > size.h) { follow = false; goTo(id, false); }
  }

  // render only what can be seen (plus the selected and executing nodes)
  const vis = $derived(visibleWorld(view, size, 240));
  const shown = $derived(lay ? lay.list.filter((p) => p.id === selected || p.id === active || intersects(vis, p)) : []);
  const lod = $derived(view.k >= 0.6 ? 0 : view.k >= 0.3 ? 1 : 2);

  // ---- find ------------------------------------------------------------------------------------------------------
  let query = $state('');
  const matches = $derived.by(() => {
    const q = query.trim().toLowerCase();
    if (!q || !graph) return [];
    return Object.entries(graph.nodes).filter(([id, n]) => `${id} ${n.title ?? ''} ${n.class_type}`.toLowerCase().includes(q)).slice(0, 8).map(([id, n]) => ({ id, title: n.title || n.class_type, cls: n.class_type }));
  });
  function pick(id: string) { query = ''; select(id, true); focusedNode.id = id; onnode?.(id); }

  // ---- details ---------------------------------------------------------------------------------------------------
  const selNode = $derived(selected && graph ? graph.nodes[selected] ?? null : null);
  const selSt = $derived(selected ? stateOf(selected) : null);
  const titleOf = (id: string) => graph?.nodes[id]?.title || graph?.nodes[id]?.class_type || `node ${id}`;
  const inputs = $derived(selNode ? Object.entries(selNode.inputs ?? {}).map(([name, v]) => (isLink(v) ? { name, link: { id: v[0], slot: v[1] } } : { name, value: shortValue(v, 400) })) : []);
  const feeds = $derived(selected && lay ? [...new Set(lay.edges.filter((e) => e.from === selected).map((e) => e.to))] : []);
  const nodeError = $derived.by(() => {
    if (!selected) return null;
    const e = doc?.error;
    if (e?.node_id && (e.node_id === selected || baseId(e.node_id) === selected)) return e.message;
    const ne = (live.history?.node_errors ?? {})[selected];
    return ne ? shortValue(ne, 600) : null;
  });
  const nodeOutputs = $derived(selected && live.history?.outputs ? (live.history.outputs as Record<string, unknown>)[selected] ?? null : null);
  const rawText = $derived.by(() => {
    if (!live.history) return '';
    const t = JSON.stringify(live.history, null, 2);
    return t.length > 200_000 ? `${t.slice(0, 200_000)}\n… (cut: ${t.length - 200_000} more characters)` : t;
  });
  function openRaw() { paneOpen = true; tab = 'raw'; void live.loadHistory(); }
  function tabKey(e: KeyboardEvent) {
    if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { tab = tab === 'node' ? 'raw' : 'node'; if (tab === 'raw') void live.loadHistory(); (e.currentTarget as HTMLElement).parentElement?.querySelector<HTMLElement>(`[data-tab="${tab}"]`)?.focus(); e.preventDefault(); }
  }
</script>

<PanelShell title="View ComfyUI" subtitle={graph ? `${Object.keys(graph.nodes).length} nodes${doc ? ` · ${PHASE_LABEL[phase]}` : ''}` : ''} {mode} width={1180} {onclose}>
  <p class="ro" role="note"><Icon name="lock" size={14} /> Read-only view of the workflow ComfyUI is running. Nothing here can change the job.</p>

  {#if live.graphState === 'loading' || live.graphState === 'idle'}
    <p class="msg muted" role="status">Loading the workflow…</p>
  {:else if live.graphState !== 'ready' || !lay}
    <div class="msg">
      <p class="notice {live.graphState === 'none' ? 'info' : 'bad'}" role="status" data-testid="graph-none"><Icon name="info" /><span>{live.graphMessage || 'The workflow is not available.'}</span></p>
      {#if live.graphState === 'error' || (live.graphState === 'none' && !live.graphMessage.startsWith('This model'))}<button class="btn sm" onclick={() => live.ensureGraph(true)}>Try again</button>{/if}
    </div>
  {:else}
    <div class="toolbar" role="toolbar" aria-label="Graph tools">
      <button class="btn sm icon" onclick={() => zoomBy(0.8)} aria-label="Zoom out"><Icon name="zoomout" size={16} /></button>
      <output class="zoom tiny mono" data-testid="zoom">{Math.round(view.k * 100)}%</output>
      <button class="btn sm icon" onclick={() => zoomBy(1.25)} aria-label="Zoom in"><Icon name="zoomin" size={16} /></button>
      <button class="btn sm" onclick={() => { follow = false; fit(true); }}>Fit</button>
      <button class="btn sm" onclick={hundred}>100%</button>
      <button class="btn sm" aria-pressed={follow} class:on={follow} onclick={() => (follow = !follow)} data-testid="follow"><Icon name="nav" size={14} /> Follow active node</button>
      <div class="find">
        <input class="input" type="search" placeholder="Find a node" aria-label="Find a node by name, class or id" bind:value={query}
          onkeydown={(e) => { if (e.key === 'Enter' && matches[0]) pick(matches[0].id); }} />
        {#if matches.length}
          <ul class="found" aria-label="Matching nodes">
            {#each matches as m (m.id)}<li><button onclick={() => pick(m.id)}><b>{m.title}</b> <span class="tiny muted">{m.cls} · #{m.id}</span></button></li>{/each}
          </ul>
        {/if}
      </div>
      <button class="btn sm" onclick={openRaw}>Raw history</button>
      <ul class="legend" aria-label="Node states">
        {#each ['pending', 'running', 'done', 'cached', 'failed'] as s (s)}<li><StateGlyph state={s} size={13} /> {s === 'pending' ? 'Waiting' : s === 'cached' ? 'Reused' : s[0].toUpperCase() + s.slice(1)}</li>{/each}
      </ul>
    </div>

    <div class="work" class:withpane={paneOpen}>
      <!-- svelte-ignore a11y_no_noninteractive_tabindex, a11y_no_noninteractive_element_interactions -->
      <div class="viewport" bind:this={vp} bind:clientWidth={vw} bind:clientHeight={vh} tabindex="0" role="application"
        aria-label="Workflow graph, {Object.keys(graph?.nodes ?? {}).length} nodes. Drag to pan. Scroll or pinch to zoom. Arrow keys pan, plus and minus zoom, F fits, 0 resets to 100 percent."
        onpointerdown={pdown} onkeydown={key} onscroll={() => { if (vp) { vp.scrollLeft = 0; vp.scrollTop = 0; } }} data-testid="graph-viewport">
        <div class="world lod{lod}" style:transform="translate({view.x}px, {view.y}px) scale({view.k})" style:width="{lay.width}px" style:height="{lay.height}px"
          data-testid="graph-world" data-k={view.k.toFixed(3)} data-x={view.x.toFixed(1)} data-y={view.y.toFixed(1)}>
          <svg class="edges" width={lay.width} height={lay.height} aria-hidden="true">
            <g stroke-width={Math.max(1.6, 1.6 / view.k)}>
              {#each paths as e (e.from + '>' + e.to + ':' + e.input)}
                {@const s = stateOf(e.from).state}{@const t = stateOf(e.to).state}
                <path d={e.d} class:act={s === 'running' || t === 'running'} class:sel={selected === e.from || selected === e.to} class:fail={t === 'failed'} />
              {/each}
            </g>
          </svg>
          {#each shown as p (p.id)}
            {@const st = stateOf(p.id)}{@const n = graph!.nodes[p.id]}
            <button class="gcard {st.state}" class:sel={selected === p.id} class:act={active === p.id} style:left="{p.x}px" style:top="{p.y}px" style:width="{p.w}px" style:height="{p.h}px"
              data-node-id={p.id} data-state={st.state} aria-pressed={selected === p.id}
              onclick={() => cardClick(p.id)} onfocus={() => cardFocus(p.id)}>
              <span class="gh"><StateGlyph state={st.state} size={lod ? 26 : 18} /><span class="gt">{n.title || n.class_type}</span></span>
              <span class="gc">{n.class_type} · #{p.id}</span>
              <span class="gin">
                {#each p.rows as r (r.name)}<span class="gr"><span class="gk">{r.name}</span><span class="gv">{r.value}</span></span>{/each}
                {#if p.moreInputs}<span class="gm">+ {p.moreInputs} more inputs</span>{/if}
              </span>
              <span class="sr-only">{LABEL[st.state] ?? st.state}.</span>
              <span class="gf">{[LABEL[st.state] ?? st.state, st.runs ? `ran ${st.runs}×` : '', st.last_ms != null ? `last ${formatDuration(st.last_ms)}` : ''].filter(Boolean).join(' · ')}</span>
            </button>
          {/each}
        </div>
      </div>

      {#if paneOpen}
        <aside class="pane" aria-label="Details">
          <div class="ptabbar">
          <div class="ptabs" role="tablist" aria-label="Details">
            <button role="tab" id="tab-node" data-tab="node" aria-selected={tab === 'node'} aria-controls="pane-node" tabindex={tab === 'node' ? 0 : -1} onkeydown={tabKey} onclick={() => (tab = 'node')}>Node</button>
            <button role="tab" id="tab-raw" data-tab="raw" aria-selected={tab === 'raw'} aria-controls="pane-raw" tabindex={tab === 'raw' ? 0 : -1} onkeydown={tabKey} onclick={openRaw}>Raw history</button>
          </div>
            <button class="btn ghost icon sm" aria-label="Close details" onclick={() => { paneOpen = false; }}><Icon name="x" size={16} /></button>
          </div>
          {#if tab === 'node'}
            <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
            <div class="pbody" role="tabpanel" id="pane-node" aria-labelledby="tab-node" tabindex="0" data-testid="node-detail">
              {#if selNode && selected}
                <h3>{selNode.title || selNode.class_type}</h3>
                <p class="tiny muted mono">{selNode.class_type} · node {selected}</p>
                <p class="stline"><StateGlyph state={selSt?.state ?? 'unknown'} /> <b>{LABEL[selSt?.state ?? 'unknown']}</b></p>
                <dl class="kv">
                  <dt>Runs</dt><dd>{selSt?.runs ?? 'not reported'}{#if selSt?.expanded} (including {selSt.expanded} loop steps){/if}</dd>
                  <dt>Last run</dt><dd>{selSt?.last_ms != null ? formatDuration(selSt.last_ms) : 'not reported'}</dd>
                  <dt>Total time</dt><dd>{selSt?.total_ms != null ? formatDuration(selSt.total_ms) : 'not reported'}</dd>
                </dl>
                {#if nodeError}<p class="notice bad" role="alert"><Icon name="warn" /><span><b>Error:</b> {nodeError}</span></p>{/if}
                <h4>Inputs</h4>
                {#if inputs.length}
                  <ul class="ins">
                    {#each inputs as i (i.name)}
                      <li><span class="ik">{i.name}</span>
                        {#if 'link' in i && i.link}<button class="linkish" onclick={() => pick(i.link.id)}>from {titleOf(i.link.id)} output {i.link.slot + 1}</button>
                        {:else}<span class="iv">{i.value}</span>{/if}
                      </li>
                    {/each}
                  </ul>
                {:else}<p class="muted small">No inputs.</p>{/if}
                {#if feeds.length}
                  <h4>Feeds</h4>
                  <ul class="ins">{#each feeds as f (f)}<li><button class="linkish" onclick={() => pick(f)}>{titleOf(f)}</button></li>{/each}</ul>
                {/if}
                <h4>Outputs</h4>
                {#if live.historyState === 'ready'}
                  {#if nodeOutputs}<pre class="json">{JSON.stringify(nodeOutputs, null, 2)}</pre>{:else}<p class="muted small">ComfyUI recorded no outputs for this node.</p>{/if}
                {:else if live.historyState === 'loading'}<p class="muted small" role="status">Loading the history…</p>
                {:else if live.historyState === 'none' || live.historyState === 'error'}<p class="muted small">{live.historyMessage}</p>
                {:else}<button class="btn sm" onclick={() => live.loadHistory()}>Load outputs from ComfyUI history</button>{/if}
              {:else}
                <p class="muted small">Pick a node to see its inputs, timing and outputs.</p>
              {/if}
            </div>
          {:else}
            <!-- svelte-ignore a11y_no_noninteractive_tabindex -->
            <div class="pbody" role="tabpanel" id="pane-raw" aria-labelledby="tab-raw" tabindex="0" data-testid="raw-history">
              <p class="tiny muted">ComfyUI's own history entry for this job, as the engine returns it. Read-only.</p>
              {#if live.historyState === 'loading'}<p class="muted small" role="status">Loading the history…</p>
              {:else if live.historyState === 'ready'}
                <details><summary>Show the raw JSON ({rawText.length.toLocaleString()} characters)</summary><pre class="json" tabindex="0" aria-label="Raw history JSON">{rawText}</pre></details>
              {:else if live.historyState === 'none' || live.historyState === 'error'}
                <p class="notice info"><Icon name="info" /><span>{live.historyMessage}</span></p><button class="btn sm" onclick={() => live.loadHistory(true)}>Try again</button>
              {/if}
            </div>
          {/if}
        </aside>
      {/if}
    </div>
  {/if}
</PanelShell>

<style>
  .ro { margin: 0; padding: 7px 16px; font-size: 13px; background: var(--info-soft); border-bottom: 1px solid var(--border); display: flex; align-items: center; gap: 6px; flex: none; }
  .msg { padding: 16px; }
  .toolbar { display: flex; flex-wrap: wrap; gap: 6px 8px; align-items: center; padding: 8px 12px; border-bottom: 1px solid var(--border); flex: none; position: relative; z-index: 3; background: var(--bg); }
  .zoom { min-width: 40px; text-align: center; color: var(--fg-2); }
  .btn.on { background: var(--btn-primary-bg); color: var(--btn-primary-fg); border-color: var(--btn-primary-bg); }
  .find { position: relative; flex: 0 1 200px; min-width: 130px; } .find .input { min-height: 30px; }
  .found { position: absolute; left: 0; right: 0; top: 100%; z-index: 5; list-style: none; margin: 2px 0 0; padding: 4px; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius-sm); box-shadow: var(--shadow); min-width: 260px; }
  .found button { all: unset; display: block; width: 100%; box-sizing: border-box; padding: 6px 8px; cursor: pointer; border-radius: 4px; } .found button:hover, .found button:focus-visible { background: var(--hover); outline: none; }
  .legend { display: flex; flex-wrap: wrap; gap: 2px 12px; list-style: none; margin: 0 0 0 auto; padding: 0; font-size: 12px; color: var(--fg-2); } .legend li { display: inline-flex; gap: 4px; align-items: center; }
  .work { flex: 1; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr); position: relative; }
  .work.withpane { grid-template-columns: minmax(0, 1fr) 340px; }
  .viewport { position: relative; overflow: hidden; touch-action: none; cursor: grab; min-height: 300px; background:
    radial-gradient(circle, color-mix(in srgb, var(--fg-muted) 28%, transparent) 1px, transparent 1.2px) 0 0 / 24px 24px, var(--bg-2); }
  .viewport:active { cursor: grabbing; }
  .world { position: absolute; left: 0; top: 0; transform-origin: 0 0; will-change: transform; }
  .edges { position: absolute; left: 0; top: 0; overflow: visible; pointer-events: none; }
  .edges path { fill: none; stroke: var(--border-strong); opacity: 0.75; }
  .edges path.act { stroke: var(--accent); opacity: 1; } .edges path.sel { stroke: var(--fg); opacity: 1; } .edges path.fail { stroke: var(--danger); opacity: 1; }
  .gcard { position: absolute; box-sizing: border-box; display: flex; flex-direction: column; gap: 2px; padding: 8px 10px; text-align: left; font: inherit; color: var(--fg); cursor: pointer;
    background: var(--card-bg); border: 1.5px solid var(--border-strong); border-radius: var(--radius-sm); overflow: hidden; }
  .gcard.pending { border-style: dashed; } .gcard.done { border-color: var(--success); } .gcard.cached { border-color: var(--info); border-style: dashed; }
  .gcard.running { border-color: var(--accent); border-width: 2.5px; background: color-mix(in srgb, var(--accent) 9%, var(--card-bg)); } .gcard.failed { border-color: var(--danger); border-width: 2.5px; background: var(--danger-soft); }
  .gcard.act { box-shadow: 0 0 0 4px color-mix(in srgb, var(--accent) 35%, transparent); animation: glow 1.6s var(--ease) infinite; }
  .gcard.sel { outline: 3px solid var(--fg); outline-offset: 2px; } .gcard:focus-visible { outline: 3px solid var(--accent); outline-offset: 2px; }
  @keyframes glow { 50% { box-shadow: 0 0 0 9px color-mix(in srgb, var(--accent) 12%, transparent); } }
  @media (prefers-reduced-motion: reduce) { .gcard.act { animation: none; } }
  .gh { display: flex; align-items: center; gap: 7px; min-width: 0; } .gt { font-weight: 600; font-size: 14px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .gc { font: 11px/1.3 var(--mono); color: var(--fg-muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .gin { display: grid; gap: 0; margin-top: 4px; font-size: 12px; line-height: 18px; }
  .gr { display: flex; gap: 6px; min-width: 0; } .gk { color: var(--fg-muted); flex: none; max-width: 40%; overflow: hidden; text-overflow: ellipsis; } .gv { font-family: var(--mono); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .gm { color: var(--fg-muted); font-size: 11px; }
  .gf { margin-top: auto; font-size: 12px; font-weight: 500; color: var(--fg-2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .lod1 .gin, .lod1 .gc, .lod1 .gf, .lod2 .gin, .lod2 .gc, .lod2 .gf { display: none; }
  .lod1 .gt { font-size: 22px; white-space: normal; } .lod2 .gt { font-size: 38px; white-space: normal; line-height: 1.1; }
  .lod1 .gcard, .lod2 .gcard { justify-content: center; }
  .pane { border-left: 1px solid var(--border); background: var(--bg); display: flex; flex-direction: column; min-height: 0; min-width: 0; }
  .ptabbar { display: flex; align-items: center; justify-content: space-between; padding: 4px 8px 0; border-bottom: 1px solid var(--border); flex: none; }
  .ptabs { display: flex; align-items: center; gap: 2px; }
  .ptabs [role='tab'] { all: unset; box-sizing: border-box; padding: 8px 12px; cursor: pointer; font-weight: 500; font-size: 13px; border-bottom: 2px solid transparent; color: var(--fg-2); min-height: 36px; }
  .ptabs [role='tab'][aria-selected='true'] { border-bottom-color: var(--accent); color: var(--fg); } .ptabs [role='tab']:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; }
  .pbody { padding: 12px 14px 20px; overflow: auto; flex: 1; min-height: 0; } .pbody h3 { overflow-wrap: anywhere; } .pbody h4 { margin: 14px 0 4px; }
  .stline { display: flex; align-items: center; gap: 8px; margin: 6px 0; }
  .ins { list-style: none; margin: 0; padding: 0; display: grid; gap: 3px; font-size: 13px; } .ins li { display: grid; grid-template-columns: minmax(70px, 38%) 1fr; gap: 8px; } .ik { color: var(--fg-muted); overflow-wrap: anywhere; } .iv { font-family: var(--mono); overflow-wrap: anywhere; }
  .ins .linkish { display: inline-block; padding: 4px 0; min-height: 24px; }
  .ins li:has(> .linkish:only-child) { display: block; }
  .json { font: 12px/1.45 var(--mono); background: var(--surface-2); border: 1px solid var(--border); border-radius: var(--radius-sm); padding: 8px; overflow: auto; max-height: 50vh; margin: 6px 0 0; white-space: pre-wrap; overflow-wrap: anywhere; }
  summary { cursor: pointer; font-weight: 500; }
  @media (max-width: 800px) {
    .work.withpane { grid-template-columns: minmax(0, 1fr); }
    .pane { position: fixed; left: 0; right: 0; bottom: 0; z-index: 80; max-height: 58dvh; border-left: 0; border-top: 1px solid var(--border-strong); box-shadow: var(--shadow); }
    .legend { margin-left: 0; }
  }
</style>
