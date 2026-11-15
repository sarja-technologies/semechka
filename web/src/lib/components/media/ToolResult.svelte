<script lang="ts">
  // The result of an Experimental tool: follows the job while it runs (the engine's own progress), then shows each file as what it is:
  // a picture (on a checkerboard, so transparency is visible), a sound with a player, a 3D mesh in the viewer, or a video. Always with a download.
  import { onMount } from 'svelte';
  import { get, post, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import GlbViewer from './GlbViewer.svelte';
  import { openViewer } from '$lib/media/viewer.svelte';
  import { assetUrl, fmtSeconds } from '$lib/media/extras';

  let { job: initial, outputs = [], typical = null, onchange }: { job: any; outputs?: string[]; typical?: number | null; onchange?: (job: any) => void } = $props();
  let job = $state<any>(null), cancelling = $state(false);
  $effect.pre(() => { if (!job || initial?.id !== job.id || initial?.status !== job.status) job = initial; });
  const active = $derived(job && (job.status === 'queued' || job.status === 'running'));
  const pct = $derived(typeof job?.progress === 'number' && job.progress > 0 ? Math.round(job.progress * 100) : null);

  onMount(() => {
    let stop = false, t: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (stop) return;
      if (job && (job.status === 'queued' || job.status === 'running') && !document.hidden) {
        try { const j = await get(`/api/media/jobs/${job.id}`); if (stop) return; const changed = j.status !== job.status; job = j; if (changed) onchange?.(j); }
        catch (e) { if (e instanceof ApiError && e.status === 404) { stop = true; return; } }
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
    try { job = await post(`/api/media/jobs/${job.id}/cancel`); onchange?.(job); } catch (e) { toast(e instanceof ApiError ? e.message : 'Could not cancel.', 'bad'); } finally { cancelling = false; }
  }
  const kindOf = (a: any) => (a.mime === 'model/gltf-binary' ? '3d' : a.mime?.startsWith('audio/') ? 'audio' : a.mime?.startsWith('video/') ? 'video' : 'image');
  const what = (a: any, i: number) => outputs[i] ?? { image: 'Picture', audio: 'Sound', '3d': '3D mesh', video: 'Video' }[kindOf(a)];
  const facts = (a: any) => [a.width && a.height ? `${a.width}×${a.height}` : '', a.duration ? `${Math.round(a.duration * 100) / 100} s` : '', a.size ? `${(a.size / 1048576).toFixed(a.size < 10485760 ? 1 : 0)} MB` : ''].filter(Boolean).join(' · ');
  const mins = (t: number) => Math.max(1, Math.round((t - Date.now() / 1000) / 60));
  const view = (a: any) => openViewer({ ...a, origin: a.origin ?? 'generated', name: a.name ?? (job?.prompt ? job.prompt.slice(0, 60) : undefined) }, { siblings: (job?.assets ?? []).filter((x: any) => x.saved !== false && kindOf(x) === 'image') });
</script>

{#if job}
  <div class="tres" data-job={job.id} data-status={job.status} aria-live="polite">
    {#if active}
      <div class="run" role="status">
        <span class="lbl">{job.status === 'queued' ? (job.queue_position ? `In the engine's queue, position ${job.queue_position}` : 'Waiting for the engine') : (job.progress_text || 'Working')}</span>
        {#if job.status === 'running'}{#if pct !== null}<progress max="100" value={pct} aria-label="Progress">{pct}%</progress><span class="tiny muted">{pct}%</span>{:else}<span class="bar" aria-hidden="true"></span>{/if}{/if}
        <span class="tiny muted">{fmtSeconds(job.elapsed_s ?? 0)} so far{typical ? ` · about ${fmtSeconds(typical)} when the server is free` : ''}</span>
        <button class="btn sm" onclick={cancel} disabled={cancelling}><Icon name="stop" size={14} /> {cancelling ? 'Cancelling…' : 'Cancel'}</button>
      </div>
    {:else if job.status === 'failed'}
      <div class="notice bad" role="alert"><div><strong>It did not work.</strong> {job.error || 'The engine reported a failure without a reason.'}</div></div>
    {:else if job.status === 'cancelled'}<p class="muted small">Cancelled before it finished.</p>
    {:else if !job.assets?.length}<p class="muted small">The engine finished without returning a file.</p>{/if}

    {#each job.assets ?? [] as a, i (a.id)}
      {@const k = kindOf(a)}
      <figure class="out" data-kind={k}>
        <figcaption><strong>{what(a, i)}</strong><span class="tiny muted">{facts(a)}</span>
          <a class="btn ghost sm dl" href={assetUrl(a.id, true)} download><Icon name="download" size={14} /> Download</a></figcaption>
        {#if k === 'image'}
          <button class="zoom checker" onclick={() => view(a)} aria-label={`Open ${what(a, i)} larger`}><img src={assetUrl(a.id)} alt={what(a, i)} width={a.width} height={a.height} loading="lazy" /></button>
        {:else if k === 'audio'}
          <audio src={assetUrl(a.id)} controls preload="metadata"></audio>
        {:else if k === '3d'}
          <GlbViewer src={assetUrl(a.id)} label={what(a, i)} />
        {:else}
          <!-- svelte-ignore a11y_media_has_caption -->
          <video src={assetUrl(a.id)} controls preload="metadata" playsinline width={a.width} height={a.height}></video>
        {/if}
        {#if !a.saved && a.expires_at}<span class="tiny muted">Not saved: available for about {mins(a.expires_at)} more minute{mins(a.expires_at) === 1 ? '' : 's'}. Download it to keep it.</span>{/if}
      </figure>
    {/each}
  </div>
{/if}

<style>
  .tres { display: grid; gap: 14px; }
  .run { display: flex; flex-wrap: wrap; gap: 8px 12px; align-items: center; padding: 14px 16px; border: 1px dashed var(--border-strong); border-radius: var(--radius); background: var(--bg-2); }
  .lbl { font-size: 13px; font-weight: 500; } progress { width: 160px; accent-color: var(--accent); }
  .bar { position: relative; width: 140px; height: 3px; border-radius: 2px; background: var(--border); overflow: hidden; }
  .bar::after { content: ''; position: absolute; inset: 0 auto 0 0; width: 35%; background: var(--accent); border-radius: 2px; }
  @media (prefers-reduced-motion: no-preference) { .bar::after { animation: slide 1.3s ease-in-out infinite; } @keyframes slide { from { transform: translateX(-100%); } to { transform: translateX(290%); } } }
  .run .btn { margin-left: auto; }
  .out { margin: 0; display: grid; gap: 6px; }
  figcaption { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 10px; } figcaption strong { font-weight: 600; font-size: 14px; } .dl { margin-left: auto; }
  .zoom { all: unset; cursor: zoom-in; display: block; justify-self: start; max-width: 100%; border-radius: var(--radius); overflow: hidden; border: 1px solid var(--border); }
  .zoom:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .zoom img { display: block; max-width: 100%; height: auto; max-height: min(70vh, 820px); object-fit: contain; }
  /* transparency made visible (cut-outs and layers are PNGs with an alpha channel) */
  .checker { background-color: #fff; background-image: linear-gradient(45deg, #d9d9d9 25%, transparent 25%), linear-gradient(-45deg, #d9d9d9 25%, transparent 25%),
    linear-gradient(45deg, transparent 75%, #d9d9d9 75%), linear-gradient(-45deg, transparent 75%, #d9d9d9 75%); background-size: 20px 20px; background-position: 0 0, 0 10px, 10px -10px, -10px 0; }
  audio { width: 100%; max-width: 560px; }
  video { width: 100%; max-height: 70vh; border-radius: var(--radius); border: 1px solid var(--border); background: var(--bg-2); }
</style>
