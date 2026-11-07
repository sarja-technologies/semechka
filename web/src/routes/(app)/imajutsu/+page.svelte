<script lang="ts">
  // Imajutsu: make and edit pictures. Choose a mode, give it the words and pictures it needs, see exactly what will be sent, run it and watch the engine work.
  // Everything chosen lives in a project on the server (saved a moment after each change), so leaving and coming back loses nothing.
  import { page } from '$app/state';
  import { goto } from '$app/navigation';
  import { onMount, untrack } from 'svelte';
  import { app } from '$lib/session.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import MediaDropzone from '$lib/components/media/MediaDropzone.svelte';
  import ModeBar from '$lib/components/imajutsu/ModeBar.svelte';
  import PanelRail from '$lib/components/imajutsu/PanelRail.svelte';
  import Stage from '$lib/components/imajutsu/Stage.svelte';
  import StartCard from '$lib/components/imajutsu/StartCard.svelte';
  import PromptPanel from '$lib/components/imajutsu/PromptPanel.svelte';
  import SourcePanel from '$lib/components/imajutsu/SourcePanel.svelte';
  import SelectionPanel from '$lib/components/imajutsu/SelectionPanel.svelte';
  import RefPanel from '$lib/components/imajutsu/RefPanel.svelte';
  import SizePanel from '$lib/components/imajutsu/SizePanel.svelte';
  import ModelPanel from '$lib/components/imajutsu/ModelPanel.svelte';
  import AdjustPanel from '$lib/components/imajutsu/AdjustPanel.svelte';
  import GeneratePanel from '$lib/components/imajutsu/GeneratePanel.svelte';
  import LivePanel from '$lib/components/imajutsu/LivePanel.svelte';
  import ResultsPanel from '$lib/components/imajutsu/ResultsPanel.svelte';
  import JobsDrawer from '$lib/components/imajutsu/JobsDrawer.svelte';
  import '$lib/components/studio/studio.css';
  import { Imajutsu, type Panel } from '$lib/imajutsu/store.svelte';

  const op = $derived(app.me?.ui?.operations?.imajutsu);
  const s = new Imajutsu();
  let jobsOpen = $state(false);
  const ADV = 'sem.imajutsu.advanced';

  onMount(() => {
    if (!op?.enabled) return;
    try { s.advanced = localStorage.getItem(ADV) === '1'; } catch { /* storage unavailable */ }
    const u = page.url.searchParams;
    s.init(u.get('project'), u.has('new')).then(() => {
      const job = u.get('job');
      if (job && s.project && s.jobs.some((j) => j.job === job)) s.batch = s.batch.includes(job) ? s.batch : [job];
    });
    const leave = () => s.flush();
    const hide = () => { if (document.visibilityState === 'hidden') s.flush(); };
    addEventListener('pagehide', leave); document.addEventListener('visibilitychange', hide);
    return () => { removeEventListener('pagehide', leave); document.removeEventListener('visibilitychange', hide); s.saveNow(); };
  });
  const setAdvanced = (on: boolean) => { s.advanced = on; try { localStorage.setItem(ADV, on ? '1' : '0'); } catch { /* storage unavailable */ } };
  const adv = $derived(s.advanced || s.key === 'advanced');
  $effect(() => { if (s.key === 'advanced') s.advanced = true; });

  // the address always names the open project (and the run on show), so a reload or a shared link comes back to the same place
  $effect(() => {
    const id = s.project?.id ?? null, job = s.batch[s.batch.length - 1] ?? null;
    if (s.loading) return;
    untrack(() => {
      const url = new URL(page.url.href), p = new URLSearchParams();
      if (id) { p.set('project', id); if (job) p.set('job', job); } else p.set('new', '1');
      if (url.searchParams.toString() === p.toString()) return;
      goto(`${url.pathname}?${p}`, { replaceState: true });
    });
  });

  const saveText = $derived({ idle: '', dirty: 'Unsaved changes…', saving: 'Saving…', saved: 'All changes saved', error: 'Not saved', conflict: 'Not saved' }[s.saveState]);
  const n = $derived(s.needs);
  const list = $derived.by((): { id: Panel; label: string }[] => {
    if (!s.project) return [];
    const l: { id: Panel; label: string }[] = [];
    if (n.prompt) l.push({ id: 'prompt', label: 'Prompt' });
    if (n.source) l.push({ id: 'source', label: 'Picture' });
    if (n.selection !== 'none') l.push({ id: 'selection', label: n.selection === 'area' ? 'New area' : 'Selection' });
    if (n.refs !== 'none') l.push({ id: 'references', label: 'References' });
    if (n.size) l.push({ id: 'size', label: 'Size' });
    if (n.model) l.push({ id: 'model', label: 'Model' });
    l.push({ id: 'adjust', label: 'Adjust' }, { id: 'generate', label: s.key === 'upscale' ? 'Scale up' : 'Generate' });
    return l;
  });
  const no = (id: Panel) => list.findIndex((x) => x.id === id) + 1;
  const has = (id: Panel) => list.some((x) => x.id === id);
</script>

<svelte:head><title>Imajutsu</title></svelte:head>

{#if !op?.enabled}
  <div class="gate"><h1>Imajutsu</h1>
    <div class="notice warn" role="status"><div><strong>Imajutsu is not available to you.</strong> {op?.reason || 'It is turned off, or your account does not have permission to use it. If you think you should have access, ask your administrator.'}</div></div>
    <a class="btn" href="/chat">Go to chat</a></div>
{:else}
  <MediaDropzone onfiles={(f) => s.addFiles(f)} hint="Drop pictures" sub="Your first picture is the one to work on; the rest become reference pictures.">
    <div class="ij">
      <header class="top">
        <div class="ttl"><h1>Imajutsu</h1>{#if s.project}<span class="proj" title={s.project.title}>{s.project.title || 'Untitled project'}</span>{/if}</div>
        <div class="acts">
          {#if s.project}<span class="save tiny" class:bad={s.saveState === 'error' || s.saveState === 'conflict'} role="status" aria-live="polite" data-testid="save-state">{saveText}</span>{/if}
          <label class="adv"><input type="checkbox" checked={adv} disabled={s.key === 'advanced'} onchange={(e) => setAdvanced(e.currentTarget.checked)} data-testid="advanced-toggle" /> Advanced options</label>
          <button type="button" class="btn sm" onclick={() => (jobsOpen = true)} data-testid="open-jobs"><Icon name="history" size={14} /> Jobs{s.jobs.length ? ` (${s.jobs.length})` : ''}</button>
          {#if s.project}<button type="button" class="btn sm" onclick={() => s.startOver()} data-testid="new-project"><Icon name="plus" size={14} /> New</button>{/if}
        </div>
      </header>

      {#if s.saveState === 'conflict'}
        <div class="notice warn" role="alert"><div><strong>{s.saveError}</strong> Choose which version to keep.</div>
          <button type="button" class="btn sm" onclick={() => s.resolveConflict(false)}>Load the other version</button><button type="button" class="btn sm" onclick={() => s.resolveConflict(true)}>Keep mine</button></div>
      {:else if s.saveState === 'error'}
        <div class="notice bad" role="alert"><div><strong>Your changes could not be saved.</strong> {s.saveError}</div><button type="button" class="btn sm" onclick={() => s.retrySave()}>Try again</button></div>
      {/if}
      {#if s.error}<div class="notice bad" role="alert"><div>{s.error}</div></div>{/if}
      {#if s.capsError}<div class="notice bad" role="alert"><div><strong>Imajutsu could not read what the server can do.</strong> {s.capsError}</div></div>{/if}

      {#if s.loading}
        <p class="muted pad" role="status">Loading…</p>
      {:else}
        <ModeBar {s} />
        {#if !s.project}
          <StartCard {s} />
        {:else}
          <PanelRail {s} {list} />
          <div class="grid" data-mode={s.key}>
            <div class="stagecol"><Stage {s} /></div>
            <div class="panels" data-testid="panels">
              {#if has('prompt')}<PromptPanel {s} n={no('prompt')} />{/if}
              {#if has('source')}<SourcePanel {s} n={no('source')} />{/if}
              {#if has('selection')}<SelectionPanel {s} n={no('selection')} />{/if}
              {#if has('references')}<RefPanel {s} n={no('references')} />{/if}
              {#if has('size')}<SizePanel {s} n={no('size')} />{/if}
              {#if has('model')}<ModelPanel {s} n={no('model')} />{/if}
              <AdjustPanel {s} n={no('adjust')} />
              <GeneratePanel {s} n={no('generate')} />
              <LivePanel {s} />
              <ResultsPanel {s} />
            </div>
          </div>
        {/if}
      {/if}
    </div>
  </MediaDropzone>
  <JobsDrawer {s} bind:open={jobsOpen} />
{/if}

<style>
  .gate { max-width: 560px; margin: 48px auto; padding: 0 var(--pad); display: grid; gap: var(--s3); }
  .ij { padding: var(--pad); display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--s3); max-width: 1440px; width: 100%; margin: 0 auto; align-content: start; }
  .top { display: flex; align-items: center; justify-content: space-between; gap: 10px; flex-wrap: wrap; }
  .ttl { display: flex; align-items: baseline; gap: 12px; flex-wrap: wrap; min-width: 0; } .proj { color: var(--fg-muted); font-size: 13px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; max-width: 36ch; }
  .acts { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; } .save { color: var(--fg-muted); } .save.bad { color: var(--danger); }
  .adv { display: inline-flex; align-items: center; gap: 7px; font-size: 13px; min-height: 34px; cursor: pointer; } .adv input { width: 17px; height: 17px; }
  .grid { display: grid; grid-template-columns: minmax(0, 1.2fr) minmax(380px, 1fr); gap: var(--s4); align-items: start; }
  .stagecol { position: sticky; top: var(--s3); min-width: 0; max-height: calc(100dvh - 24px); overflow: auto; } .panels { display: grid; gap: var(--s3); min-width: 0; }
  .pad { padding: var(--s5); }
  @media (max-width: 1000px) { .grid { grid-template-columns: minmax(0, 1fr); } .stagecol { position: static; max-height: none; overflow: visible; } }
  @media (max-width: 560px) { .ij { padding: 10px; } }
</style>
