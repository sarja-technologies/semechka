<script lang="ts">
  // A generated picture or video inside the conversation. Follows its job while it runs (the engine's own progress;
  // a determinate bar only when the engine reports a number), then shows the result with its details and a download.
  import { onMount } from 'svelte';
  import { get, post, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';

  let { job: initial, kind, onchange, onuse }: { job: any; kind: 'image' | 'video'; onchange?: (job: any) => void; onuse?: (asset: any) => void } = $props();
  let job = $state<any>(null);
  $effect.pre(() => { if (!job || initial?.id !== job.id || initial?.status !== job.status) job = initial; });
  let cancelling = $state(false), zoom = $state<any>(null);
  const active = $derived(job && (job.status === 'queued' || job.status === 'running'));

  onMount(() => {
    let stop = false, t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (stop) return;
      if (job && (job.status === 'queued' || job.status === 'running') && !document.hidden) {
        try {
          const j = await get(`/api/media/jobs/${job.id}`);
          if (stop) return;
          const changed = j.status !== job.status;
          job = j;
          if (changed) onchange?.(j);
        } catch (e) { if (e instanceof ApiError && e.status === 404) { stop = true; return; } }
      }
      if (job && (job.status === 'queued' || job.status === 'running')) t = setTimeout(tick, 1500);
    };
    t = setTimeout(tick, 800);
    const vis = () => { if (!document.hidden && active) { clearTimeout(t); tick(); } };
    document.addEventListener('visibilitychange', vis);
    return () => { stop = true; clearTimeout(t); document.removeEventListener('visibilitychange', vis); };
  });

  async function cancel() {
    cancelling = true;
    try { job = await post(`/api/media/jobs/${job.id}/cancel`); onchange?.(job); }
    catch (e) { toast(e instanceof ApiError ? e.message : 'Could not cancel.', 'bad'); }
    finally { cancelling = false; }
  }
  const src = (a: any) => `/api/media/assets/${a.id}/content`;
  const dl = (a: any) => `/api/media/assets/${a.id}/content?download=true`;
  const dur = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
  const mins = (t: number) => Math.max(1, Math.round((t - Date.now() / 1000) / 60));
  function details(a: any) {
    const p = { ...(job?.params ?? {}), ...(a.params ?? {}) };
    return [a.width && a.height ? `${a.width}×${a.height}` : null, a.duration ? `${Math.round(a.duration * 10) / 10} s` : p.frames && p.fps ? `${p.frames} frames at ${p.fps} fps` : null,
      p.steps ? `${p.steps} steps` : null, a.seed != null && a.seed >= 0 ? `seed ${a.seed}` : p.seed != null && p.seed >= 0 ? `seed ${p.seed}` : null].filter(Boolean).join(' · ');
  }
  const pct = $derived(typeof job?.progress === 'number' && job.progress > 0 ? Math.round(job.progress * 100) : null);
</script>

{#if !job}
  <div class="mcard"><p class="muted small">This {kind === 'image' ? 'picture' : 'video'} is no longer available. Its job was removed.</p></div>
{:else}
  <div class="mcard" data-job={job.id} data-status={job.status}>
    {#if active}
      <div class="run" role="status" aria-live="polite">
        <div class="ph" class:video={kind === 'video'} style:aspect-ratio={job.params?.width && job.params?.height ? `${job.params.width} / ${job.params.height}` : undefined}>
          <span class="lbl">{job.status === 'queued' ? (job.queue_position ? `In the engine's queue, position ${job.queue_position}` : 'Waiting for the engine') : (job.progress_text || 'Generating')}</span>
          {#if job.status === 'running'}
            {#if pct !== null}<progress max="100" value={pct} aria-label="Progress">{pct}%</progress><span class="tiny muted">{pct}%</span>
            {:else}<span class="bar" aria-hidden="true"></span>{/if}
          {/if}
          <span class="tiny muted">{dur(job.elapsed_s ?? 0)} so far</span>
        </div>
        <button class="btn sm" onclick={cancel} disabled={cancelling}><Icon name="stop" size={14} /> {cancelling ? 'Cancelling…' : 'Cancel'}</button>
      </div>
    {:else if job.status === 'failed'}
      <div class="notice bad" role="alert"><div><strong>The {kind === 'image' ? 'picture' : 'video'} could not be generated.</strong> {job.error || 'The engine reported a failure without a reason.'}</div></div>
    {:else if job.status === 'cancelled'}
      <p class="muted small">Cancelled before it finished.</p>
    {:else if !job.assets?.length}
      <p class="muted small">The engine finished without returning a file.</p>
    {/if}
    {#each job.assets ?? [] as a (a.id)}
      <figure class="res">
        {#if a.kind === 'image' || a.mime?.startsWith('image/')}
          <button class="zoom" onclick={() => (zoom = a)} aria-label="Open the picture larger">
            <img src={src(a)} alt={`Generated from: ${job.prompt}`} loading="lazy" width={a.width} height={a.height} />
          </button>
        {:else}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video src={src(a)} controls preload="metadata" width={a.width} height={a.height} playsinline></video>
        {/if}
        <figcaption>
          <span class="tiny muted">{[job.model, details(a)].filter(Boolean).join(' · ')}</span>
          <span class="acts">
            <a class="btn ghost sm" href={dl(a)} download><Icon name="download" size={14} /> Download</a>
            {#if onuse && (a.kind === 'image' || a.mime?.startsWith('image/')) && a.saved}<button class="btn ghost sm" onclick={() => onuse?.(a)}><Icon name="edit" size={14} /> Use as source</button>{/if}
          </span>
          {#if !a.saved && a.expires_at}<span class="tiny muted">Not saved: available for about {mins(a.expires_at)} more minute{mins(a.expires_at) === 1 ? '' : 's'}. Download it to keep it.</span>{/if}
        </figcaption>
      </figure>
    {/each}
  </div>
{/if}

{#if zoom}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="lightbox" role="dialog" aria-modal="true" aria-label="Picture" tabindex="-1" onclick={() => (zoom = null)} onkeydown={(e) => e.key === 'Escape' && (zoom = null)}>
    <img src={src(zoom)} alt={`Generated from: ${job.prompt}`} />
    <button class="btn sm close" onclick={() => (zoom = null)}>Close</button>
  </div>
{/if}

<style>
  .mcard { display: grid; gap: 8px; max-width: min(100%, 560px); }
  .run { display: grid; gap: 8px; justify-items: start; }
  .ph { width: min(100%, 420px); aspect-ratio: 1; max-height: 320px; border: 1px dashed var(--border-strong); border-radius: var(--radius); background: var(--bg-2);
    display: grid; place-content: center; justify-items: center; gap: 8px; padding: 16px; text-align: center; }
  .ph.video { aspect-ratio: 16 / 9; }
  .lbl { font-size: 13px; font-weight: 500; }
  progress { width: 180px; accent-color: var(--accent); }
  /* indeterminate: a solid segment travelling along a track (the engine gave no number to show) */
  .bar { position: relative; width: 160px; height: 3px; border-radius: 2px; background: var(--border); overflow: hidden; }
  .bar::after { content: ''; position: absolute; inset: 0 auto 0 0; width: 35%; background: var(--accent); border-radius: 2px; }
  @media (prefers-reduced-motion: no-preference) { .bar::after { animation: slide 1.3s ease-in-out infinite; } @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(290%); } } }
  .res { margin: 0; display: grid; gap: 4px; }
  .zoom { all: unset; cursor: zoom-in; display: block; border-radius: var(--radius); overflow: hidden; border: 1px solid var(--border); }
  .zoom:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .res img, .res video { display: block; width: 100%; height: auto; max-height: 520px; object-fit: contain; background: var(--bg-2); }
  .res video { border-radius: var(--radius); border: 1px solid var(--border); }
  figcaption { display: flex; flex-wrap: wrap; align-items: center; gap: 4px 8px; }
  .acts { display: inline-flex; gap: 2px; margin-left: auto; }
  .lightbox { position: fixed; inset: 0; z-index: 95; background: rgba(8, 9, 10, 0.88); display: grid; place-items: center; padding: 24px; }
  .lightbox img { max-width: 100%; max-height: 100%; object-fit: contain; }
  .close { position: fixed; top: 12px; right: 12px; }
</style>
