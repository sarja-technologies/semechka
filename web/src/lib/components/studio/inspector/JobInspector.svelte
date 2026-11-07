<script lang="ts">
  // "View process": what the engine is really doing for one job, from the trace the engine writes (docs/OPENJUTSU_STUDIO.md 2.2).
  // Every number and word below comes from that document; a missing value is shown as "not reported", never as a guess.
  import { onDestroy, untrack } from 'svelte';
  import { acquireLive } from '../../../studio/inspector/live.svelte';
  import { focusedNode } from '../../../studio/inspector/focus.svelte';
  import { copyText } from '../../../studio/inspector/clip';
  import {
    phaseOf, PHASE_LABEL, phaseTone, elapsedNow, silentFor, fmtElapsed, progressView, sceneCells, sceneHeadline, segmentSummary, fmtMb, fmtBytes, isTerminalStatus
  } from '../../../studio/inspector/logic.ts';
  import PanelShell from './PanelShell.svelte';
  import SceneTrack from './SceneTrack.svelte';
  import NodeList from './NodeList.svelte';
  import EventLog from './EventLog.svelte';
  import Icon from '../../Icon.svelte';

  let { jobId, onclose, compact = false, mode = 'drawer', finalAssetId, onopenfinal, onnode, onshowgraph }: {
    jobId: string;
    onclose?: () => void;
    /** one line plus a thin progress bar, to sit under a job card */
    compact?: boolean;
    /** 'drawer': fixed panel on the right (full-screen sheet on a phone). 'inline': fills the box the page gives it. */
    mode?: 'drawer' | 'inline';
    /** Library asset of the finished video, when the page has saved one: enables playing and downloading it here */
    finalAssetId?: string;
    onopenfinal?: () => void;
    /** a node was picked in the list (the shared focusedNode store is set as well) */
    onnode?: (nodeId: string) => void;
    /** "Show in graph" on the error card: the page may open the ComfyUI view */
    onshowgraph?: (nodeId: string) => void;
  } = $props();

  let handle = acquireLive(untrack(() => jobId));
  let live = $state.raw(handle.live);
  $effect(() => {
    const id = jobId;
    untrack(() => { if (handle.live.jobId !== id) { handle.release(); handle = acquireLive(id); live = handle.live; sel = null; } });
  });
  onDestroy(() => handle.release());

  const doc = $derived(live.doc);
  const phase = $derived(phaseOf(doc));
  const tone = $derived(phaseTone(phase));
  const traceGone = $derived(doc?.trace_available === false);
  const running = $derived(!!doc && !isTerminalStatus(doc.status));

  let now = $state(Date.now());
  $effect(() => { if (!running) return; const t = setInterval(() => (now = Date.now()), 1000); return () => clearInterval(t); });
  const elapsed = $derived(elapsedNow(doc, live.receivedAt, now));
  const quiet = $derived(phase === 'running' ? silentFor(doc, live.receivedAt, now) : null);

  /** an image job of the engine's own runtime: one sampler, a picture instead of a video, no ComfyUI prompt id */
  const image = $derived(doc?.graph_kind === 'sarness-workflow' || doc?.backend === 'sarness-workflow');
  const prog = $derived(progressView(doc));
  const cells = $derived(sceneCells(doc));
  const headline = $derived(sceneHeadline(doc));
  const segments = $derived((doc?.scenes?.segments ?? []).filter((s) => s.state === 'saved'));
  const sampling = $derived(doc?.sampling ?? null);
  const stepText = $derived(sampling?.step != null ? `step ${sampling.step}${sampling.max != null ? `/${sampling.max}` : ''}` : '');

  let sel = $state<number | null>(null);
  const stripMeta = $derived([doc?.scenes ? headline : '', stepText, elapsed != null ? fmtElapsed(elapsed) : ''].filter(Boolean).join(' · '));
  const selSegment = $derived(sel == null ? null : segments.find((s) => s.index === sel) ?? null);

  // preview picture: refreshed at most every 4 s while a scene is being made, and when a scene is saved
  let pvKey = $state('');
  let pvOk = $state(false);
  $effect(() => {
    const d = doc;
    if (!d || d.status === 'queued' || compact || d.has_preview === false) return;
    const generating = d.status === 'running' && (d.scenes?.current != null || d.sampling != null);
    const k = generating ? `${Math.floor(Date.now() / 4000)}-${d.scenes?.completed ?? 0}` : String(d.updated_at ?? 'last');
    if (k !== untrack(() => pvKey)) pvKey = k;
  });

  let copied = $state('');
  async function copy(what: string, text: string) { if (await copyText(text)) { copied = what; setTimeout(() => { if (copied === what) copied = ''; }, 2000); } }

  const at = (ms: number) => (ms ? new Date(ms).toLocaleTimeString() : 'never');
  const showGraph = (id: string | null | undefined) => { if (!id) return; focusedNode.set(id); onshowgraph?.(id); };

  const connText = $derived(
    live.phase === 'live' ? 'Live' : live.phase === 'polling' ? 'Live (checking every 1.5 s)' : live.phase === 'reconnecting' ? 'Reconnecting…' :
    live.phase === 'ended' ? 'Final state received' : live.phase === 'paused' ? 'Paused while this tab is in the background' : live.phase === 'loading' ? 'Loading…' : ''
  );
  const refs = $derived(doc?.conditioning?.references ?? []);
  const mem = $derived(doc?.memory ?? null);
  const out = $derived(doc?.output ?? null);
</script>

{#snippet strip()}
  <div class="strip" role="group" aria-label="Job progress">
    {#if doc}
      <span class="chip {tone}">{PHASE_LABEL[phase]}</span>
      <span class="s-stage">{doc.stage_label ?? 'Stage not reported'}</span>
      <span class="s-meta tiny muted">{stripMeta}</span>
      <div class="pbar thin" class:indet={!prog.determinate}><i style:width={prog.determinate ? `${prog.percent}%` : undefined}></i></div>
    {:else if live.phase === 'denied' || live.phase === 'notfound'}
      <span class="muted small">{live.message}</span>
    {:else}
      <span class="muted small">{live.phase === 'error' ? live.message : 'Loading the process…'}</span>
    {/if}
  </div>
{/snippet}

{#snippet body()}
  <div class="scroll">
    {#if live.stale}
      <p class="notice warn" role="status" data-testid="stale"><Icon name="warn" /><span><b>The inference engine is not answering.</b> Showing the last known state, from {at(live.receivedAt)}. {live.stale.reason}</span></p>
    {/if}
    {#if live.phase === 'reconnecting'}
      <p class="notice warn" role="status"><Icon name="warn" /><span><b>Reconnecting…</b> Live updates are interrupted. Last news from the server at {at(live.lastContact)}. This page keeps checking.</span></p>
    {:else if live.phase === 'paused'}
      <p class="notice info" role="status"><Icon name="pause" /><span>Updates are paused while this tab is in the background. They resume when you come back.</span></p>
    {/if}
    {#if !doc && (live.phase === 'denied' || live.phase === 'notfound')}
      <div class="notice bad" role="alert"><Icon name="lock" /><span>{live.message}</span></div>
    {:else if !doc && live.phase === 'error'}
      <div class="notice bad" role="alert"><Icon name="warn" /><span>{live.message} <button class="linkish" onclick={() => live.poke()}>Try again</button></span></div>
    {:else if !doc}
      <p class="muted" role="status">Loading the process…</p>
    {:else}
      <!-- overview -->
      <section class="sec" aria-labelledby="ov-h">
        <h3 id="ov-h" class="sr-only">Overview</h3>
        <div class="row head">
          <span class="chip big {tone}" data-testid="status-chip">{PHASE_LABEL[phase]}</span>
          <span class="muted small" data-testid="elapsed">Elapsed {phase === 'queued' ? 'not started yet' : fmtElapsed(elapsed)}</span>
          <span class="spacer"></span>
          <span class="conn tiny {live.phase}" role="status" data-testid="conn">{connText}</span>
        </div>
        <p class="what small"><b>{doc.profile ?? 'Model not reported'}</b> <span class="muted">· {doc.operation ?? 'operation not reported'}{doc.backend ? ` · on ${doc.backend}` : ''}</span></p>
        <dl class="ids">
          {#if !image}
          <dt>ComfyUI prompt id</dt>
          <dd><code>{doc.prompt_id ?? 'not assigned yet'}</code>{#if doc.prompt_id}<button class="btn ghost sm" onclick={() => copy('prompt', doc.prompt_id ?? '')} aria-label="Copy ComfyUI prompt id"><Icon name="copy" size={14} />{copied === 'prompt' ? 'Copied' : 'Copy'}</button>{/if}</dd>
          {/if}
          <dt>Job id</dt>
          <dd><code>{doc.job_id ?? jobId}</code></dd>
        </dl>

        {#if traceGone}
          <p class="notice info" role="note"><Icon name="info" /><span>The engine no longer has the detailed trace for this job; showing the saved result only.</span></p>
        {/if}

        {#if !traceGone}
          <p class="stage" data-testid="stage"><span class="lbl tiny muted">Stage</span> {doc.stage_label ?? 'not reported'}</p>
          <div class="pbar" class:indet={!prog.determinate} role="progressbar" aria-label="Job progress" aria-valuemin={prog.determinate ? 0 : undefined} aria-valuemax={prog.determinate ? 100 : undefined} aria-valuenow={prog.determinate ? Math.round(prog.percent ?? 0) : undefined} aria-valuetext={prog.determinate ? `${Math.round(prog.percent ?? 0)} percent` : 'In progress, no percentage reported'}>
            <i style:width={prog.determinate ? `${prog.percent}%` : undefined}></i>
          </div>
          <p class="tiny muted pbasis" data-testid="progress-basis">
            {#if prog.determinate}{Math.round(prog.percent ?? 0)}%{prog.basis ? ` · ${prog.basis}` : ''}{#if prog.held}{' · '}waiting for the output file{/if}
            {:else if phase === 'completed'}Finished{:else if phase === 'failed' || phase === 'cancelled'}Stopped{:else}No percentage yet: the engine has not reported one{prog.basis ? ` (${prog.basis})` : ''}{/if}
          </p>
        {/if}
        {#if phase === 'finishing'}
          <p class="notice info" role="status"><Icon name="info" /><span><b>Finishing:</b> waiting for the output file. The job is not complete until the file is written and checked.</span></p>
        {/if}
        {#if live.unsettled}
          <p class="notice warn" role="status"><Icon name="warn" /><span>The engine reported this job as completed but never reported the output file as ready.</span></p>
        {/if}
        {#if quiet != null && quiet > 45}
          <p class="notice warn" role="status"><Icon name="warn" /><span>No news from the engine for {fmtElapsed(quiet)}. It may be busy with a long step.</span></p>
        {/if}
      </section>

      {#if doc.error}
        <section class="sec" aria-labelledby="er-h">
          <div class="notice bad errcard" role="alert" data-testid="error-card">
            <Icon name="warn" />
            <div class="grow">
              <h3 id="er-h">{phase === 'failed' ? 'The job failed' : 'The engine reported an error'}</h3>
              <p class="msg">{doc.error.message}</p>
              {#if doc.error.node_title || doc.error.class_type || doc.error.node_id}
                <p class="small">Failed in <b>{doc.error.node_title ?? 'a node'}</b> <span class="muted mono">{doc.error.class_type ?? ''}{doc.error.node_id ? ` · #${doc.error.node_id}` : ''}</span></p>
              {/if}
              {#if doc.error.node_id}<button class="btn sm" onclick={() => showGraph(doc.error?.node_id)}><Icon name="network" size={14} /> Show in graph</button>{/if}
            </div>
          </div>
        </section>
      {/if}

      {#if image && !traceGone}
        <section class="sec" aria-labelledby="sm-h" data-testid="sampler">
          <h3 id="sm-h">Sampler</h3>
          {#if sampling}
            <p class="headline" data-testid="sampler-step">{sampling.title ?? 'Sampler'}: {stepText}</p>
            {#if sampling.step != null && sampling.max}<div class="pbar" role="progressbar" aria-label="Sampler steps" aria-valuemin={0} aria-valuemax={sampling.max} aria-valuenow={sampling.step} aria-valuetext={stepText}><i style:width="{Math.min(100, (sampling.step / sampling.max) * 100)}%"></i></div>{/if}
          {:else}<p class="muted small">{phase === 'completed' ? 'The sampler has finished.' : 'The sampler has not started yet.'}</p>{/if}
          {#if pvKey}
            <figure class="preview" style:display={pvOk ? 'block' : 'none'}>
              <img src="{live.url}/preview?v={pvKey}" alt="Latest engine preview" onload={() => (pvOk = true)} onerror={() => (pvOk = false)} data-testid="inspector-preview" />
              <figcaption class="tiny muted">{phase === 'running' ? 'Latest preview from the engine' : 'Last preview from the engine'}</figcaption>
            </figure>
          {/if}
        </section>
      {/if}

      {#if !traceGone && doc.scenes && !image}
        <section class="sec" aria-labelledby="sc-h">
          <h3 id="sc-h">Scenes</h3>
          <p class="headline" data-testid="scene-headline">{headline}{#if sampling && phase === 'running'}<span class="muted">{' · '}sampler {stepText}</span>{/if}</p>
          {#if sampling?.title && phase === 'running'}<p class="tiny muted">Now: {sampling.title}{sampling.node_id ? ` (node ${sampling.node_id})` : ''}</p>{/if}
          {#if cells.length}<SceneTrack {cells} {sampling} selected={sel} onselect={(i) => (sel = sel === i ? null : i)} />{:else}<p class="muted small">No scene has started yet.</p>{/if}

          {#if selSegment}
            <figure class="player">
              <!-- svelte-ignore a11y_media_has_caption -->
              <video controls preload="metadata" playsinline src="{live.url}/segments/{selSegment.index}" aria-label="Saved scene {selSegment.index}" data-testid="segment-video"></video>
              <figcaption class="tiny muted">Scene {selSegment.index}: {segmentSummary(selSegment)}</figcaption>
            </figure>
          {/if}

          {#if pvKey}
            <figure class="preview" style:display={pvOk ? 'block' : 'none'}>
              <img src="{live.url}/preview?v={pvKey}" alt="Latest engine preview" onload={() => (pvOk = true)} onerror={() => (pvOk = false)} />
              <figcaption class="tiny muted">Latest preview from the engine</figcaption>
            </figure>
          {/if}

          {#if segments.length}
            <ul class="segs" aria-label="Saved scenes">
              {#each segments as s (s.index)}
                <li>
                  <span class="sn">Scene {s.index}</span><span class="tiny muted grow">{segmentSummary(s)}</span>
                  <button class="btn sm" aria-pressed={sel === s.index} aria-label="Play scene {s.index}" onclick={() => (sel = sel === s.index ? null : s.index)}><Icon name="play" size={13} /> Play</button>
                </li>
              {/each}
            </ul>
          {/if}
        </section>

        {#if doc.loop}
          <section class="sec" aria-labelledby="lp-h">
            <h3 id="lp-h">What "Loop End" means</h3>
            <div class="loopbox" data-testid="loop-explainer">
              <p class="small"><b>Loop End = one scene was saved.</b> The job continues until <b>Final Video</b> has been written and the output file checked.</p>
              {#if doc.loop.meaning}<p class="tiny muted">{doc.loop.meaning}</p>{/if}
              <p class="tiny">Loop End seen <b data-testid="loop-count">{doc.loop.iterations_ended ?? 0}</b>{` time${(doc.loop.iterations_ended ?? 0) === 1 ? '' : 's'}${doc.scenes?.total != null ? ` of ${doc.scenes.total} scenes` : ''}. It is not the end of the job.`}</p>
            </div>
          </section>
        {/if}
      {/if}

      {#if !traceGone}
        <section class="sec" aria-labelledby="nd-h">
          <h3 id="nd-h">Nodes</h3>
          <NodeList {doc} events={live.events} onnode={(id) => onnode?.(id)} />
        </section>

        <section class="sec" aria-labelledby="cd-h">
          <h3 id="cd-h">What is conditioning the generation</h3>
          {#if doc.conditioning}
            {#if doc.conditioning.agree === true}
              <p class="notice ok" data-testid="agree"><Icon name="check" /><span><b>References, graph and submitted inputs match.</b> Every picture marked "Used" is loaded by exactly one picture node in the graph, and that node holds the file that was uploaded.</span></p>
            {:else if doc.conditioning.agree === false}
              <p class="notice bad" role="alert" data-testid="agree"><Icon name="warn" /><span><b>Mismatch.</b> The pictures in the {image ? "workflow" : "ComfyUI graph"} do not match what was submitted. The result may not use the pictures you chose. Check the list below.</span></p>
            {:else}
              <p class="notice" data-testid="agree"><Icon name="info" /><span>The engine has not checked the inputs against the graph yet.</span></p>
            {/if}
            <h4 class="small">{image ? 'Source picture' : 'Video'}</h4>
            {@const vid = doc.conditioning.video ?? (doc.conditioning as any).source ?? (doc.conditioning as any).image ?? null}
            <p class="small">{#if vid?.file}<code>{vid.file}</code> <span class="muted tiny">{vid.node_id ? `node ${vid.node_id}` : ''}</span>{:else}<span class="muted">not reported</span>{/if}</p>
            <h4 class="small">Reference pictures</h4>
            {#if refs.length}
              <ol class="refs">
                {#each refs as r (r.position)}
                  <li class:off={!r.used}>
                    <span class="pos">{r.position}</span>
                    <span class="grow"><b>{r.name ?? r.file ?? 'unnamed'}</b>{#if r.name && r.file && r.file !== r.name}<span class="tiny muted">{' · '}{r.file}</span>{/if}
                      {#if !r.used}<br /><span class="tiny">{r.reason ?? 'no reason reported'}</span>{:else if r.node_id}<br /><span class="tiny muted">loaded by node {r.node_id}</span>{/if}</span>
                    <span class="chip {r.used ? 'ok' : 'warn'}">{r.used ? 'Used' : 'Not used'}</span>
                  </li>
                {/each}
              </ol>
            {:else}<p class="muted small">No reference pictures were sent.</p>{/if}
          {:else}
            <p class="muted small">Not reported yet. It appears once the engine has built the workflow.</p>
          {/if}
        </section>

        <section class="sec" aria-labelledby="mm-h">
          <h3 id="mm-h">Memory</h3>
          {#if mem}
            <dl class="kv memkv">
              <dt>Free system memory</dt><dd>{fmtMb(mem.ram_available_mb)}</dd>
              <dt>{image ? 'Engine process' : 'ComfyUI process'}</dt><dd>{fmtMb(mem.comfyui_rss_mb)}</dd>
              <dt>Device (GPU) memory</dt><dd>{mem.vram_mb == null ? 'not reported by this machine' : fmtMb(mem.vram_mb)}</dd>
            </dl>
            {#if mem.source}<p class="tiny muted">Source: {mem.source}</p>{/if}
            {#if mem.basis}<p class="tiny muted" data-testid="mem-basis">Basis: {mem.basis}</p>{/if}
          {:else}<p class="muted small">Memory is not reported.</p>{/if}
        </section>
      {/if}

      <section class="sec" aria-labelledby="out-h">
        <h3 id="out-h">Output</h3>
        {#if out}
          <p class="small" data-testid="output-state">
            {#if out.state === 'ready'}<span class="chip ok"><Icon name="check" size={12} /> Ready</span> The final {image ? 'picture' : 'video'} was written and checked.
            {:else if out.state === 'writing'}<span class="chip info">Writing</span> The final {image ? 'picture' : 'video'} is being written.
            {:else}<span class="chip">None yet</span> The final {image ? 'picture' : 'video'} does not exist yet.{image ? '' : ' Saved scenes are listed above.'}{/if}
          </p>
          {#if out.state === 'ready' && out.file}
            <p class="tiny muted">{out.file.width ?? '?'}×{out.file.height ?? '?'}{image ? '' : ` · ${out.file.duration_s != null ? `${out.file.duration_s.toFixed(1)} s` : 'duration not reported'}`} · {fmtBytes(out.file.bytes)}{image ? '' : ` · ${out.file.has_audio == null ? 'audio not reported' : out.file.has_audio ? 'with sound' : 'no sound'}`}</p>
          {/if}
          {#if out.state === 'ready' && finalAssetId}
            <figure class="player">
              <!-- svelte-ignore a11y_media_has_caption -->
              <video controls preload="metadata" playsinline src="/api/media/assets/{finalAssetId}/content" aria-label="Final video" data-testid="final-video"></video>
            </figure>
            <div class="row">
              <a class="btn sm" href="/api/media/assets/{finalAssetId}/content?download=true" download><Icon name="download" size={14} /> Download</a>
              {#if onopenfinal}<button class="btn sm" onclick={() => onopenfinal?.()}>Open in the viewer</button>{/if}
            </div>
          {:else if out.state === 'ready'}
            <p class="tiny muted">No Library copy is linked to this panel.</p>
          {/if}
        {:else}<p class="muted small">Output is not reported.</p>{/if}
      </section>

      {#if !traceGone}
        <section class="sec" aria-labelledby="lg-h">
          <h3 id="lg-h">Events</h3>
          <EventLog events={live.events} startedAt={doc.started_at} />
        </section>
      {/if}
    {/if}
  </div>
{/snippet}

{#if compact}
  {@render strip()}
{:else}
  <PanelShell title="View process" subtitle={doc ? `${doc.profile ?? ''}${doc.operation ? ` · ${doc.operation}` : ''}` : ''} {mode} width={600} {onclose}>
    {@render body()}
  </PanelShell>
{/if}

<style>
  .scroll { flex: 1; min-height: 0; overflow: auto; padding: 4px 16px 24px; overscroll-behavior: contain; }
  .sec { padding: 14px 0; border-bottom: 1px solid var(--border); }
  .sec:last-child { border-bottom: 0; }
  .sec h3 { margin-bottom: 8px; }
  .head { gap: 10px; }
  .chip.big { font-size: 14px; padding: 3px 12px; line-height: 20px; }
  .conn { color: var(--fg-muted); } .conn.reconnecting { color: var(--warn); font-weight: 600; } .conn.live::before, .conn.polling::before { content: ''; display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--success); margin-right: 5px; }
  .what { margin: 8px 0 4px; }
  .ids { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; font-size: 13px; margin: 6px 0 10px; align-items: center; }
  .ids dt { color: var(--fg-muted); } .ids dd { margin: 0; display: flex; align-items: center; gap: 6px; flex-wrap: wrap; min-width: 0; } .ids code { overflow-wrap: anywhere; }
  .stage { font-size: 16px; font-weight: 600; margin: 10px 0 6px; } .stage .lbl { display: block; font-weight: 500; }
  .pbar { position: relative; height: 12px; border-radius: 6px; background: var(--surface-2); border: 1px solid var(--border-strong); overflow: hidden; }
  .pbar i { display: block; height: 100%; background: var(--accent); transition: width 0.4s var(--ease); }
  .pbar.indet i { width: 36%; position: absolute; left: 0; background: repeating-linear-gradient(135deg, var(--accent) 0 8px, color-mix(in srgb, var(--accent) 55%, transparent) 8px 16px); animation: slide 1.6s linear infinite alternate; }
  .pbar.thin { height: 6px; flex: 1 0 100%; }
  @keyframes slide { from { left: 0; } to { left: 64%; } }
  @media (prefers-reduced-motion: reduce) { .pbar.indet i { animation: none; left: 32%; } .pbar i { transition: none; } }
  .pbasis { margin: 4px 0 0; }
  .notice { margin: 10px 0 0; } .notice :global(svg) { flex: none; margin-top: 2px; }
  .errcard h3 { margin-bottom: 4px; } .errcard .msg { font-weight: 500; overflow-wrap: anywhere; }
  .headline { font-size: 16px; font-weight: 600; margin: 0 0 4px; }
  .player { margin: 10px 0 0; } .player video { width: 100%; max-height: 360px; background: #000; border-radius: var(--radius); }
  .preview { margin: 10px 0 0; } .preview img { max-width: 100%; max-height: 220px; border-radius: var(--radius-sm); border: 1px solid var(--border); display: block; }
  .segs { list-style: none; padding: 0; margin: 12px 0 0; border: 1px solid var(--border); border-radius: var(--radius); }
  .segs li { display: flex; align-items: center; gap: 10px; padding: 6px 10px; } .segs li + li { border-top: 1px solid var(--border); } .sn { font-weight: 600; white-space: nowrap; }
  .loopbox { border-left: 3px solid var(--accent); padding: 6px 12px; background: color-mix(in srgb, var(--accent) 7%, var(--card-bg)); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; } .loopbox p { margin: 0 0 4px; }
  .refs { list-style: none; margin: 4px 0 0; padding: 0; display: grid; gap: 6px; }
  .refs li { display: flex; align-items: flex-start; gap: 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); } .refs li.off { border-style: dashed; }
  .pos { width: 24px; height: 24px; flex: none; border-radius: 50%; display: inline-flex; align-items: center; justify-content: center; border: 1.5px solid var(--border-strong); font: 600 12px/1 var(--mono); }
  h4 { margin: 10px 0 4px; font-weight: 600; }
  .memkv { margin: 0 0 6px; }
  /* compact strip */
  .strip { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 10px; padding: 8px 10px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface); }
  .s-stage { font-weight: 600; font-size: 13px; } .s-meta { margin-left: auto; }
</style>
