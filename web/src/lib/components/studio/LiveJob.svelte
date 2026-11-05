<script lang="ts">
  // The live view of one studio job: what the engine is doing right now, the result when there is one, and the two ways to look closer
  // (VIEW PROCESS, VIEW COMFYUI). Everything shown is read from the server: the stage text and progress are the engine's own, and when it has no
  // percentage the bar is indeterminate instead of invented. Works the same for running, finished and failed jobs, and after a reload.
  import { onDestroy, tick, untrack, type Component } from 'svelte';
  import Icon from '$lib/components/Icon.svelte';
  import VideoPreview from '$lib/components/media/VideoPreview.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import { contentUrl, fmtDuration, listAssets, type Asset } from '$lib/media/assets';
  import { openViewer } from '$lib/media/viewer.svelte';
  import * as api from '$lib/studio/api';
  import type { Inspect } from '$lib/studio/types';
  import type { Studio } from '$lib/studio/store.svelte';

  let { s, job, compact = false }: { s: Studio; job: string; compact?: boolean } = $props();

  // The inspector screens are built separately. They are loaded only when opened, and only if this build contains them.
  const found = import.meta.glob('/src/lib/components/studio/inspector/*.svelte');
  const have = (n: string) => Object.keys(found).some((k) => k.endsWith(`/${n}`));
  async function component(n: string): Promise<Component<any> | null> {
    const k = Object.keys(found).find((p) => p.endsWith(`/${n}`));
    return k ? ((await found[k]()) as { default: Component<any> }).default : null;
  }

  let ins = $state<Inspect | null>(null), mj = $state<any>(null), err = $state(''), now = $state(Date.now() / 1000);
  let view = $state<'' | 'process' | 'comfy'>(''), Viewer = $state<Component<any> | null>(null), Strip = $state<Component<any> | null>(null), dlg: HTMLDivElement | undefined = $state();
  let cancelling = $state(false), exp = $state<{ busy: boolean; progress: number; error: string; asset: Asset | null }>({ busy: false, progress: 0, error: '', asset: null });
  let alive = true;

  const status = $derived<string>(mj?.status ?? ins?.user_status?.status ?? ins?.status ?? 'queued');
  const active = $derived(status === 'queued' || status === 'running' || status === 'starting');
  const done = $derived(status === 'completed');
  const failed = $derived(status === 'failed');
  const LABEL: Record<string, string> = { queued: 'Waiting to start', starting: 'Starting', running: 'Running', completed: 'Finished', failed: 'Failed', cancelled: 'Cancelled' };
  const comfy = $derived(ins?.comfy?.available === true);
  const result = $derived<Asset | null>((mj?.assets ?? []).find((a: Asset) => a.kind === 'video' && a.origin !== 'export') ?? null);
  const stage = $derived(ins?.stage_label || mj?.progress_text || LABEL[status] || '');
  const frac = $derived(ins?.progress?.fraction ?? (typeof mj?.progress === 'number' && mj.progress > 0 && mj.progress < 1 && !ins ? mj.progress : null));
  const elapsed = $derived(active && ins?.started_at ? Math.max(0, now - ins.started_at) : ins?.elapsed_s ?? mj?.elapsed_s ?? 0);
  const failing = $derived<{ message: string; node_title?: string; class_type?: string } | null>(ins?.error ?? (failed && mj?.error ? { message: String(mj.error) } : null));
  const segs = $derived(ins?.scenes);
  const wanted = $derived<{ width: number; height: number } | null>(mj?.params?.studio?.plan?.output?.requested ?? mj?.params?.studio?.requested_output ?? null);
  const needsExport = $derived(!!result && !!wanted && !!result.width && !!result.height && (result.width !== wanted.width || result.height !== wanted.height));

  // The project may have changed since this run: offer to put what the run used back
  const was = $derived(mj?.params?.studio);
  const changedSince = $derived(!compact && !!was && s.project?.id === was.project_id && (
    JSON.stringify((was.references ?? []).map((r: any) => [r.asset, r.active !== false, r.subject ?? null])) !== JSON.stringify(s.spec.references.map((r) => [r.asset, r.active, r.subject]))
    || (was.subject?.text ?? '') !== s.spec.subject.text || (!!was.mode && was.mode !== s.spec.mode)));
  let restored = $state(false);

  async function poll() {
    try {
      const [m, i] = await Promise.all([api.mediaJob(job).catch(() => null), api.inspectJob(job).catch(() => null)]);
      if (!alive) return;
      if (m) mj = m;
      if (i) ins = i;
      err = !m && !i ? 'Cannot read this job right now.' : '';
      if (m?.assets) for (const a of m.assets) s.assets[a.id] = a;
    } catch (e) { err = errText(e); }
  }
  $effect(() => {
    void job; ins = null; mj = null; exp = { busy: false, progress: 0, error: '', asset: null };
    let t: ReturnType<typeof setTimeout>;
    const loop = async () => { await poll(); if (alive) t = setTimeout(loop, active ? 1800 : 6000); };
    loop();
    return () => clearTimeout(t);
  });
  $effect(() => { if (!active) return; const t = setInterval(() => (now = Date.now() / 1000), 1000); return () => clearInterval(t); });
  const finished = $derived(!active && !!mj);
  $effect(() => { if (finished) untrack(() => s.refreshJobs()); });
  // an export made earlier is found again after a reload
  const rid = $derived(result?.id);
  $effect(() => {
    const id = rid; if (!id || compact) return;
    listAssets({ origin: 'export', limit: 40 }).then((l) => { if (!exp.asset) exp.asset = (l as Asset[]).find((a) => a.parent === id) ?? null; }).catch(() => {});
  });
  $effect(() => { if (compact || !have('JobInspector.svelte')) return; component('JobInspector.svelte').then((c) => (Strip = c)).catch(() => {}); });
  onDestroy(() => { alive = false; });

  async function show(v: 'process' | 'comfy') {
    const name = v === 'process' ? 'JobInspector.svelte' : 'ComfyGraphView.svelte';
    view = v; Viewer = null;
    try { Viewer = await component(name); } catch { Viewer = null; }
    await tick(); dlg?.focus();
  }
  const close = () => { view = ''; Viewer = null; };
  function key(e: KeyboardEvent) { if (e.key === 'Escape') { e.stopPropagation(); close(); } }

  async function cancel() { cancelling = true; try { await api.cancelJob(job); await poll(); } catch (e) { err = errText(e); } finally { cancelling = false; } }

  async function runExport() {
    if (!wanted || exp.busy) return;
    exp = { busy: true, progress: 0, error: '', asset: null };
    try {
      const { task } = await api.exportJob(job, wanted.width, wanted.height, 'scale');
      const a = await api.followTask(task, (p) => (exp.progress = p));
      s.assets[a.id] = a; exp = { busy: false, progress: 1, error: '', asset: a };
    } catch (e) { exp = { busy: false, progress: 0, error: errText(e), asset: null }; }
  }
  const t = (x: number) => fmtDuration(x);
</script>

<section class="lj" class:compact aria-label="Live job" data-testid="live-job" data-status={status}>
  <div class="head">
    <span class="st-badge" class:info={active} class:ok={done} class:bad={failed}>{LABEL[status] ?? status}</span>
    <span class="stage" role="status" aria-live="polite" data-testid="job-stage">{stage}</span>
    <span class="tiny muted el" aria-label="Time so far">{t(elapsed)}</span>
  </div>

  {#if active}
    {#if frac != null}
      <div class="st-prog" role="progressbar" aria-label="Progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(frac * 100)} data-testid="job-progress"><i style:width={`${Math.max(2, frac * 100)}%`}></i></div>
      <p class="tiny muted">{Math.round(frac * 100)}%{ins?.progress?.basis ? ` · ${ins.progress.basis.replace(/_/g, ' ')}` : ''}</p>
    {:else}
      <div class="st-prog ind" role="progressbar" aria-label="Working, no percentage available" data-testid="job-progress-indeterminate"><i></i></div>
    {/if}
    {#if segs?.total}<p class="tiny muted" data-testid="job-scenes">Clip {Math.min(segs.total, segs.current ?? (segs.completed ?? 0) + 1)} of {segs.total} · {segs.completed ?? 0} saved</p>{/if}
  {/if}

  {#if failing}
    <div class="notice bad fail" role="alert" data-testid="job-error"><Icon name="warn" size={16} />
      <div><strong>It stopped{failing.node_title ? ` in “${failing.node_title}”` : ''}.</strong> {failing.message}{#if failing.class_type} <span class="tiny muted">({failing.class_type})</span>{/if}</div></div>
  {/if}
  {#if err}<p class="st-note warn" role="status">{err}</p>{/if}

  <div class="btns">
    <button type="button" class="btn sm" onclick={() => show('process')} disabled={!have('JobInspector.svelte')} title={have('JobInspector.svelte') ? 'See every step the engine takes' : 'The process view is not part of this build'}>VIEW PROCESS</button>
    {#if comfy}<button type="button" class="btn sm" onclick={() => show('comfy')} disabled={!have('ComfyGraphView.svelte')}>VIEW COMFYUI</button>{/if}
    {#if active}<button type="button" class="btn sm" onclick={cancel} disabled={cancelling}>{cancelling ? 'Stopping…' : 'Cancel'}</button>{/if}
  </div>

  {#if changedSince}
    <p class="st-note" data-testid="changed-since">The pictures or the subject have changed since this run. <button type="button" class="linkish" onclick={async () => { await s.restoreFromJob(job); restored = true; }}>Put this run's pictures and subject back</button>{#if restored} <span role="status">Done.</span>{/if}</p>
  {/if}

  {#if Strip && !compact}<div class="strip"><Strip jobId={job} compact={true} /></div>{/if}

  {#if done && result}
    <figure class="res" data-testid="job-result">
      <div class="vid"><VideoPreview asset={result} ratio={`${result.width || 16} / ${result.height || 9}`} onopen={() => openViewer(result, { siblings: [result] })} /></div>
      <figcaption>
        <span class="tiny muted">{result.width}×{result.height}{result.duration ? ` · ${fmtDuration(result.duration, true)}` : ''}</span>
        <a class="btn sm" href={contentUrl(result, { download: true })} download aria-label="Download the result"><Icon name="download" size={14} /> Download</a>
        <button type="button" class="btn sm" onclick={() => openViewer(result, { siblings: [result] })}><Icon name="expand" size={14} /> Open</button>
      </figcaption>
    </figure>
    {#if !compact && needsExport && wanted}
      <div class="exp" data-testid="export-offer">
        {#if exp.asset}
          <p class="st-note ok" role="status"><Icon name="check" size={13} /> Exported at {exp.asset.width}×{exp.asset.height}. It is in your Library.</p>
          <div class="st-row"><button type="button" class="btn sm" onclick={() => openViewer(exp.asset!, { siblings: [exp.asset!] })}>Open the export</button>
            <a class="btn sm" href={contentUrl(exp.asset, { download: true })} download><Icon name="download" size={14} /> Download export</a></div>
        {:else if exp.busy}
          <div class="st-prog" role="progressbar" aria-label="Exporting" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(exp.progress * 100)}><i style:width={`${Math.max(4, exp.progress * 100)}%`}></i></div>
          <p class="st-note" role="status">Exporting at {wanted.width}×{wanted.height}…</p>
        {:else}
          <p class="st-note">The model made {result.width}×{result.height}. You asked for {wanted.width}×{wanted.height}. The result above is untouched; exporting makes a scaled copy{wanted.width * wanted.height > (result.width ?? 0) * (result.height ?? 0) ? ' (scaling up adds size, not detail)' : ''}.</p>
          <button type="button" class="btn primary sm" onclick={runExport}>Export at output size ({wanted.width}×{wanted.height})</button>
          {#if exp.error}<p class="st-note bad" role="alert">{exp.error}</p>{/if}
        {/if}
      </div>
    {/if}
  {:else if done}
    <p class="st-note warn" role="status">The job finished but no video was attached to it. Check your Library.</p>
  {/if}
</section>

{#if view}
  <div class="scrim" role="presentation" onclick={close}></div>
  <div class="ovl" role="dialog" aria-modal="true" aria-label={view === 'process' ? 'Job process' : 'ComfyUI graph'} tabindex="-1" bind:this={dlg} onkeydown={key}>
    <header><h3>{view === 'process' ? 'Process' : 'ComfyUI'}</h3><button type="button" class="btn ghost icon sm" onclick={close} aria-label="Close"><Icon name="x" size={16} /></button></header>
    <div class="inner">{#if Viewer}<Viewer jobId={job} onclose={close} />{:else}<p class="st-note" role="status">This view is not part of this build.</p>{/if}</div>
  </div>
{/if}

<style>
  .lj { display: grid; gap: 10px; padding: 12px; border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--m-card); }
  .head { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; } .stage { font-weight: 500; font-size: 13.5px; flex: 1 1 140px; min-width: 0; } .el { font-variant-numeric: tabular-nums; }
  .btns { display: flex; flex-wrap: wrap; gap: 8px; } .btns .btn { letter-spacing: .04em; font-weight: 600; font-size: 12px; }
  .fail { margin: 0; align-items: flex-start; } .fail :global(svg) { flex: none; margin-top: 2px; color: var(--danger); }
  .res { margin: 0; display: grid; gap: 8px; } .vid { border-radius: 10px; overflow: hidden; border: 1px solid var(--border); max-height: 420px; background: #000; display: grid; justify-items: center; }
  .vid :global(.vp) { max-height: 420px; } figcaption { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } figcaption .tiny { margin-right: auto; }
  .exp { display: grid; gap: 8px; padding: 10px; border-radius: 10px; border: 1px dashed var(--border-strong); }
  .strip { min-width: 0; }
  .scrim { position: fixed; inset: 0; background: rgba(10, 12, 12, .55); z-index: 90; }
  .ovl { position: fixed; z-index: 91; inset: 3vh 3vw; display: flex; flex-direction: column; background: var(--surface); border: 1px solid var(--border-strong); border-radius: var(--radius); box-shadow: var(--shadow); outline: none; }
  .ovl header { display: flex; align-items: center; justify-content: space-between; padding: 8px 8px 8px 16px; border-bottom: 1px solid var(--border); } .inner { flex: 1; min-height: 0; overflow: auto; padding: 12px; }
  .compact .exp { display: none; }
</style>
