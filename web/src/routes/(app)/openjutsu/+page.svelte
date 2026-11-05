<script lang="ts">
  // OpenJutsu Studio: prepare a video, add reference pictures, say what to replace, pick a model that really supports it, run it and watch it.
  // Everything chosen lives in a project on the server (saved a moment after each change), so leaving and coming back loses nothing.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount, untrack } from 'svelte';
  import { app } from '$lib/session.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import MediaDropzone from '$lib/components/media/MediaDropzone.svelte';
  import VideoStage from '$lib/components/studio/VideoStage.svelte';
  import StudioStepper from '$lib/components/studio/StudioStepper.svelte';
  import StartCard from '$lib/components/studio/StartCard.svelte';
  import StepVideo from '$lib/components/studio/StepVideo.svelte';
  import StepPrepare from '$lib/components/studio/StepPrepare.svelte';
  import StepReferences from '$lib/components/studio/StepReferences.svelte';
  import StepOperation from '$lib/components/studio/StepOperation.svelte';
  import StepModel from '$lib/components/studio/StepModel.svelte';
  import StepGenerate from '$lib/components/studio/StepGenerate.svelte';
  import JobsDrawer from '$lib/components/studio/JobsDrawer.svelte';
  import '$lib/components/studio/studio.css';
  import { Studio, STEPS } from '$lib/studio/store.svelte';

  const op = $derived(app.me?.ui?.operations?.openjutsu);
  const s = new Studio();
  let jobsOpen = $state(false);

  onMount(() => {
    if (!op?.enabled) return;
    const u = page.url.searchParams;
    s.init(u.get('project'), u.has('new')).then(() => {
      const st = u.get('step');
      if (!s.project) return;
      if (st && STEPS.some((x) => x.id === st)) s.step = st as typeof s.step;
      const job = u.get('job');
      if (job && s.jobs.some((j) => j.job === job)) s.jobId = job;
    });
    const leave = () => s.flush();
    const hide = () => { if (document.visibilityState === 'hidden') s.flush(); };
    addEventListener('pagehide', leave); document.addEventListener('visibilitychange', hide);
    return () => { removeEventListener('pagehide', leave); document.removeEventListener('visibilitychange', hide); s.saveNow(); };
  });

  // the address always names the open project, so a reload or a shared link comes back to the same place
  $effect(() => {
    const id = s.project?.id ?? null, job = s.jobId, step = s.step;
    if (s.loading) return;
    untrack(() => {
      const url = new URL(page.url.href), p = new URLSearchParams();
      if (id) { p.set('project', id); if (job) p.set('job', job); p.set('step', step); } else p.set('new', '1');
      if (url.searchParams.toString() === p.toString()) return;
      goto(`${url.pathname}?${p}`, { replaceState: true });
    });
  });

  const saveText = $derived({ idle: '', dirty: 'Unsaved changes…', saving: 'Saving…', saved: 'All changes saved', error: 'Not saved', conflict: 'Not saved' }[s.saveState]);
  const stepIdx = $derived(STEPS.findIndex((x) => x.id === s.step));
</script>

<svelte:head><title>OpenJutsu Studio</title></svelte:head>

{#if !op?.enabled}
  <div class="gate"><h1>OpenJutsu Studio</h1>
    <div class="notice warn" role="status"><div><strong>OpenJutsu Studio is not available to you.</strong> {op?.reason || 'It is turned off, or your account does not have permission to use it. If you think you should have access, ask your administrator.'}</div></div>
    <a class="btn" href="/chat">Go to chat</a></div>
{:else}
  <MediaDropzone onfiles={(f) => s.addFiles(f)} hint="Drop a video to start, or pictures to use as references" sub="Videos start a project. Pictures are added as references.">
    <div class="studio">
      <header class="top">
        <div class="ttl"><h1>OpenJutsu Studio</h1>{#if s.project}<span class="proj" title={s.project.title}>{s.project.title || 'Untitled project'}</span>{/if}</div>
        <div class="acts">
          {#if s.project}<span class="save tiny" class:bad={s.saveState === 'error' || s.saveState === 'conflict'} role="status" aria-live="polite" data-testid="save-state">{saveText}</span>{/if}
          <button type="button" class="btn sm" onclick={() => (jobsOpen = true)} data-testid="open-jobs"><Icon name="history" size={14} /> Jobs{s.jobs.length ? ` (${s.jobs.length})` : ''}</button>
          {#if s.project}<button type="button" class="btn sm" onclick={() => { s.startOver(); }}><Icon name="plus" size={14} /> New video</button>{/if}
        </div>
      </header>

      {#if s.saveState === 'conflict'}
        <div class="notice warn" role="alert"><div><strong>{s.saveError}</strong> Choose which version to keep.</div>
          <button type="button" class="btn sm" onclick={() => s.resolveConflict(false)}>Load the other version</button>
          <button type="button" class="btn sm" onclick={() => s.resolveConflict(true)}>Keep mine</button></div>
      {:else if s.saveState === 'error'}
        <div class="notice bad" role="alert"><div><strong>Your changes could not be saved.</strong> {s.saveError}</div><button type="button" class="btn sm" onclick={() => s.retrySave()}>Try again</button></div>
      {/if}
      {#if s.error}<div class="notice bad" role="alert"><div>{s.error}</div></div>{/if}
      {#if s.capsError}<div class="notice bad" role="alert"><div><strong>The Studio could not read what the server can do.</strong> {s.capsError}</div></div>{/if}

      {#if s.loading}
        <p class="muted pad" role="status">Loading…</p>
      {:else if !s.project}
        <StartCard {s} />
      {:else}
        <StudioStepper {s} />
        <div class="grid" data-current-step={s.step}>
          <div class="stagecol"><VideoStage {s} /></div>
          <section class="panel" aria-label={`Step ${stepIdx + 1}: ${STEPS[stepIdx]?.label}`} data-testid="panel">
            {#if s.step === 'video'}<StepVideo {s} />
            {:else if s.step === 'prepare'}<StepPrepare {s} />
            {:else if s.step === 'references'}<StepReferences {s} />
            {:else if s.step === 'operation'}<StepOperation {s} />
            {:else if s.step === 'model'}<StepModel {s} />
            {:else}<StepGenerate {s} />{/if}
          </section>
        </div>
      {/if}
    </div>
  </MediaDropzone>
  <JobsDrawer {s} bind:open={jobsOpen} />
{/if}

<style>
  .gate { max-width: 560px; margin: 48px auto; padding: 0 var(--pad); display: grid; gap: var(--s3); }
  .studio { padding: var(--pad); display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s3); max-width: 1400px; width: 100%; margin: 0 auto; align-content: start; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
  .ttl { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; min-width: 0; } .proj { color: var(--fg-muted); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 36ch; }
  .acts { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; } .save { color: var(--fg-muted); } .save.bad { color: var(--danger); }
  .grid { display: grid; grid-template-columns: minmax(0, 1.25fr) minmax(360px, 1fr); gap: var(--s4); align-items: start; }
  .stagecol { position: sticky; top: var(--s3); min-width: 0; }
  .panel { min-width: 0; padding: var(--s4); border: 1px solid var(--border); border-radius: var(--radius-menu); background: var(--card-bg); }
  .pad { padding: var(--s5); }
  @media (max-width: 1000px) { .grid { grid-template-columns: minmax(0, 1fr); } .stagecol { position: static; } }
  @media (max-width: 560px) { .studio { padding: 10px; } .panel { padding: 12px; } }
</style>
