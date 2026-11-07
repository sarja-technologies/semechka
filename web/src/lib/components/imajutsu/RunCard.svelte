<script lang="ts">
  // One run (one picture being made): what the engine is doing right now, taken from its own trace. The strip is the same inspector strip OpenJutsu
  // uses; while the sampler runs, its real intermediate picture is shown (the engine's latest preview, never a made-up one). A failed run names the
  // failing node. VIEW PROCESS shows everything; VIEW WORKFLOW shows the exact graph the engine ran, when the engine offers one.
  import { onDestroy, tick, untrack } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import JobInspector from '$lib/components/studio/inspector/JobInspector.svelte';
  import ComfyGraphView from '$lib/components/studio/inspector/ComfyGraphView.svelte';
  import { acquireLive } from '$lib/studio/inspector/live.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import * as api from '$lib/imajutsu/api';
  import { fmtDuration } from '$lib/media/assets';
  import type { Imajutsu } from '$lib/imajutsu/store.svelte';
  let { s, job, index = 0, total = 1 }: { s: Imajutsu; job: string; index?: number; total?: number } = $props();

  let handle = acquireLive(untrack(() => job));
  let live = $state.raw(handle.live);
  $effect(() => { const id = job; untrack(() => { if (handle.live.jobId !== id) { handle.release(); handle = acquireLive(id); live = handle.live; } }); });
  onDestroy(() => { alive = false; handle.release(); });

  let mj = $state<any>(null), err = $state(''), now = $state(Date.now() / 1000), alive = true, cancelling = $state(false);
  let view = $state<'' | 'process' | 'workflow'>(''), dlg: HTMLDivElement | undefined = $state();
  const doc = $derived(live.doc);
  const status = $derived<string>(mj?.status ?? doc?.user_status ?? doc?.status ?? 'queued');
  const active = $derived(status === 'queued' || status === 'running' || status === 'starting');
  const failed = $derived(status === 'failed');
  const LABEL: Record<string, string> = { queued: 'Waiting to start', starting: 'Starting', running: 'Running', completed: 'Finished', failed: 'Failed', cancelled: 'Cancelled' };
  const stage = $derived(doc?.stage_label || mj?.progress_text || LABEL[status] || '');
  const sampling = $derived(doc?.sampling);
  const step = $derived(sampling?.step != null ? `Step ${sampling.step}${sampling.max != null ? ` of ${sampling.max}` : ''}` : '');
  const failing = $derived<{ message: string; node_title?: string | null; class_type?: string | null } | null>(doc?.error ?? (failed && mj?.error ? { message: String(mj.error) } : null));
  const workflow = $derived(doc?.graph_kind === 'sarness-workflow');
  const elapsed = $derived(active && doc?.started_at ? Math.max(0, now - doc.started_at) : doc?.elapsed_s ?? mj?.elapsed_s ?? 0);
  // the engine's newest preview picture: only while it is sampling and only if the engine has one; it is asked again when the step changes
  let pvOk = $state(false);
  const pvKey = $derived(active && doc && doc.has_preview !== false && sampling?.step != null ? `${job}-${sampling.step}-${doc?.updated_at ?? ''}` : '');

  async function poll() {
    try {
      const m = await api.mediaJob(job).catch(() => null);
      if (!alive) return;
      if (m) { mj = m; s.jobData[job] = m; for (const a of m.assets ?? []) s.assets[a.id] = a; }
      err = m ? '' : 'Cannot read this job right now.';
    } catch (e) { err = errText(e); }
  }
  $effect(() => {
    void job; mj = null;
    let t: ReturnType<typeof setTimeout>;
    const loop = async () => { await poll(); if (alive) t = setTimeout(loop, active ? 1500 : 6000); };
    loop();
    return () => clearTimeout(t);
  });
  $effect(() => { if (!active) return; const t = setInterval(() => (now = Date.now() / 1000), 1000); return () => clearInterval(t); });
  // the run just finished while this card watched: refresh the lists and let the store make what the run's settings ask for (phone finish, original kept outside)
  let wasActive = false;
  $effect(() => {
    if (active) { if (mj) wasActive = true; return; }
    if (mj) untrack(() => { if (wasActive && status === 'completed') s.jobFinished(job); else s.refreshJobs(); wasActive = false; });
  });

  async function show(v: 'process' | 'workflow') { view = v; await tick(); dlg?.focus(); }
  const close = () => { view = ''; };
  function key(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }
  async function cancel() { cancelling = true; try { await api.cancelJob(job); await poll(); } catch (e) { err = errText(e); } finally { cancelling = false; } }
</script>

<section class="run" aria-label={total > 1 ? `Picture ${index + 1} of ${total}` : 'Live job'} data-testid="run-card" data-job={job} data-status={status}>
  <div class="head">
    {#if total > 1}<span class="st-badge">{index + 1}/{total}</span>{/if}
    <span class="st-badge" class:info={active} class:ok={status === 'completed'} class:bad={failed}>{LABEL[status] ?? status}</span>
    <span class="stage" role="status" aria-live="polite" data-testid="job-stage">{stage}{step && active ? ` · ${step}` : ''}</span>
    <span class="tiny muted el" aria-label="Time so far">{fmtDuration(elapsed)}</span>
  </div>
  <JobInspector jobId={job} compact={true} />

  {#if pvKey}
    <figure class="pv" style:display={pvOk ? 'block' : 'none'}>
      <img src={`/api/studio/jobs/${job}/preview?v=${encodeURIComponent(pvKey)}`} alt="The engine's latest intermediate result while it works" data-testid="job-preview" onload={() => (pvOk = true)} onerror={() => (pvOk = false)} />
      <figcaption class="tiny muted">Latest picture from the engine{step ? `, ${step.toLowerCase()}` : ''}. It sharpens as the steps run.</figcaption>
    </figure>
  {/if}

  {#if failing}
    <div class="notice bad fail" role="alert" data-testid="job-error"><Icon name="warn" size={16} />
      <div><strong>It stopped{failing.node_title ? ` in “${failing.node_title}”` : ''}.</strong> {failing.message}{#if failing.class_type} <span class="tiny muted">({failing.class_type})</span>{/if}</div></div>
  {/if}
  {#if err}<p class="st-note warn" role="status">{err}</p>{/if}

  <div class="btns">
    <button type="button" class="btn sm" onclick={() => show('process')} data-testid="view-process">VIEW PROCESS</button>
    {#if workflow}<button type="button" class="btn sm" onclick={() => show('workflow')} data-testid="view-workflow">VIEW WORKFLOW</button>{/if}
    {#if active}<button type="button" class="btn sm" onclick={cancel} disabled={cancelling}>{cancelling ? 'Stopping…' : 'Cancel'}</button>{/if}
  </div>
</section>

{#if view}
  <div class="scrim" role="presentation" onclick={close}></div>
  <div class="ovl" role="dialog" aria-modal="true" aria-label={view === 'process' ? 'Job process' : 'Engine workflow'} tabindex="-1" bind:this={dlg} onkeydown={key}>
    <header><h3>{view === 'process' ? 'Process' : 'Workflow'}</h3><button type="button" class="btn ghost icon sm" onclick={close} aria-label="Close"><Icon name="x" size={16} /></button></header>
    <div class="inner">{#if view === 'process'}<JobInspector jobId={job} mode="inline" onclose={close} />{:else}<ComfyGraphView jobId={job} mode="inline" onclose={close} />{/if}</div>
  </div>
{/if}

<style>
  .run { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--m-card); min-width: 0; }
  .head { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .stage { font-weight: 500; font-size: 13.5px; flex: 1 1 140px; min-width: 0; } .el { font-variant-numeric: tabular-nums; }
  .btns { display: flex; flex-wrap: wrap; gap: 8px; } .btns .btn { letter-spacing: .04em; font-weight: 600; font-size: 12px; min-height: 34px; }
  .fail { margin: 0; align-items: flex-start; } .fail :global(svg) { flex: none; margin-top: 2px; color: var(--danger); }
  .pv { margin: 0; } .pv img { display: block; max-width: 100%; max-height: 300px; border-radius: 10px; border: 1px solid var(--border); background: #000; }
  .scrim { position: fixed; inset: 0; background: rgba(10, 12, 12, .55); z-index: 90; }
  .ovl { position: fixed; z-index: 91; inset: 3vh 3vw; display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); outline: none; }
  .ovl header { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 8px 16px; border-bottom: 1px solid var(--border); } .inner { flex: 1; min-height: 0; overflow: auto; padding: 12px; }
</style>
