<script lang="ts">
  // Image and video generation. Every control is driven by the model's own limits as reported by the engine;
  // progress is whatever the engine reports (a determinate bar only when it gives a number).
  import { onMount } from 'svelte';
  import { get, post, del, qs, ApiError } from '$lib/api';
  import { toast } from '$lib/toast.svelte';
  import { app } from '$lib/session.svelte';
  import { TECH_LABELS, fmtTech } from '$lib/techlabels';
  import Icon from '$lib/components/Icon.svelte';
  import Modal from '$lib/components/Modal.svelte';
  import MediaProtection from './MediaProtection.svelte';

  let { kind }: { kind: 'image' | 'video' } = $props();
  const noun = $derived(kind === 'image' ? 'image' : 'video');

  let opts = $state<any>(null), blocked = $state(''), jobs = $state<any[]>([]), loading = $state(true);
  let busy = $state(false), formError = $state(''), protOpen = $state(false), live = $state('');
  let confirmDelete = $state<any>(null);

  // form
  let modelId = $state(''), prompt = $state(''), negative = $state(''), width = $state(512), height = $state(512);
  let steps = $state(4), guidance = $state(0), seed = $state(''), scheduler = $state(''), frames = $state(17), fps = $state(16);
  let resolution = $state(''), save = $state(true);

  const model = $derived<any>(opts?.models.find((m: any) => m.id === modelId));
  const lim = $derived<any>(model?.limits ?? {});
  const resolutions = $derived<number[][]>(lim.resolutions ?? []);
  const seconds = $derived(kind === 'video' && fps ? frames / fps : 0);
  const tooLong = $derived(kind === 'video' && opts?.video_max_seconds && seconds > opts.video_max_seconds + 1e-9);
  const atLimit = $derived(!!opts && opts.caps.active >= opts.caps.concurrent);
  const galleryFull = $derived(!!opts && save && opts.caps.max_assets > 0 && opts.caps.stored >= opts.caps.max_assets && opts.retention_days !== 0);
  const noStorage = $derived(opts?.retention_days === 0);

  function applyDefaults(m: any) {
    const d = m?.defaults ?? {};
    width = d.width ?? width; height = d.height ?? height; steps = d.steps ?? steps; guidance = d.guidance ?? guidance;
    frames = d.frames ?? frames; fps = d.fps ?? fps; scheduler = ''; seed = '';
    resolution = resolutions.length ? `${width}x${height}` : '';
  }
  let lastModel = '';
  $effect(() => { if (model && model.id !== lastModel) { lastModel = model.id; applyDefaults(model); } });
  $effect(() => { if (resolution && resolutions.length) { const [w, h] = resolution.split('x').map(Number); width = w; height = h; } });

  async function loadOptions() {
    try {
      opts = await get(`/api/media/options${qs({ kind })}`);
      blocked = '';
      if (!modelId || !opts.models.some((m: any) => m.id === modelId)) modelId = opts.models.find((m: any) => m.pinned)?.id ?? opts.models[0]?.id ?? '';
      if (!opts.ephemeral) save = true;
    } catch (e) { blocked = e instanceof ApiError ? e.message : 'Could not load this page.'; opts = null; }
  }
  async function loadJobs(quiet = false) {
    try {
      const rows: any[] = await get(`/api/media/jobs${qs({ kind })}`);
      for (const r of rows) {
        const old = jobs.find((j) => j.id === r.id);
        if (old && old.status !== r.status && r.status === 'completed') live = `Your ${noun} is ready.`;
        if (old && old.status !== r.status && r.status === 'failed') live = `A ${noun} generation failed.`;
      }
      jobs = rows;
    } catch (e) { if (!quiet && e instanceof ApiError) blocked ||= e.message; }
  }
  onMount(() => {
    (async () => { await loadOptions(); if (opts) await loadJobs(); loading = false; })();
    const t = setInterval(() => { if (!document.hidden && jobs.some((j) => j.status === 'queued' || j.status === 'running')) { loadJobs(true); loadOptions(); } }, 2000);
    return () => clearInterval(t);
  });

  async function generate() {
    if (!model || busy) return;
    busy = true; formError = '';
    const body: Record<string, unknown> = { kind, model_id: model.id, prompt, save, width, height, steps, guidance };
    if (negative.trim() && model.negative_prompt) body.negative_prompt = negative;
    if (seed.trim() !== '') body.seed = Number(seed);
    if (kind === 'image' && scheduler) body.scheduler = scheduler;
    if (kind === 'video') { body.frames = frames; body.fps = fps; }
    try {
      const j = await post('/api/media/jobs', body);
      if (j.forced_unsaved) toast('Your organisation stores no generated files, so this result is kept for a few minutes only.', 'info', 7000);
      await Promise.all([loadJobs(true), loadOptions()]);
    } catch (e) { formError = e instanceof ApiError ? e.message : 'Could not start the generation.'; }
    finally { busy = false; }
  }
  async function cancel(j: any) {
    try { await post(`/api/media/jobs/${j.id}/cancel`); } catch (e) { toast(e instanceof ApiError ? e.message : 'Could not cancel.', 'bad'); }
    await Promise.all([loadJobs(true), loadOptions()]);
  }
  async function remove(j: any) {
    confirmDelete = null;
    try { await del(`/api/media/jobs/${j.id}`); toast(`${noun[0].toUpperCase() + noun.slice(1)} deleted.`); } catch (e) { toast(e instanceof ApiError ? e.message : 'Could not delete.', 'bad'); }
    await Promise.all([loadJobs(true), loadOptions()]);
  }
  function reuse(j: any) {
    const p = j.params ?? {};
    if (j.model_id && opts.models.some((m: any) => m.id === j.model_id)) modelId = j.model_id;
    prompt = j.prompt ?? ''; negative = p.negative_prompt ?? '';
    width = p.width ?? width; height = p.height ?? height; steps = p.steps ?? steps; guidance = p.guidance ?? guidance;
    frames = p.frames ?? frames; fps = p.fps ?? fps; scheduler = p.scheduler ?? ''; seed = p.seed != null && p.seed >= 0 ? String(p.seed) : '';
    resolution = resolutions.length ? `${width}x${height}` : '';
    document.getElementById('prompt')?.focus();
  }
  function onKey(e: KeyboardEvent) { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); if (canGo) generate(); } }
  const canGo = $derived(!!model && !!prompt.trim() && !busy && !atLimit && !galleryFull && !tooLong);

  const when = (t: number) => new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
  const dur = (s: number) => (s < 90 ? `${Math.round(s)} s` : `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`);
  const mins = (t: number) => Math.max(1, Math.round((t - Date.now() / 1000) / 60));
  const statusWord = (j: any) => ({ queued: 'Queued', running: 'Generating', completed: 'Done', failed: 'Failed', cancelled: 'Cancelled' } as Record<string, string>)[j.status];
  const techRows = (t: any) => Object.entries(t ?? {}).filter(([k]) => k !== '_unavailable');
</script>

<div class="studio">
  <header class="head">
    <h1>{kind === 'image' ? 'Images' : 'Video'}</h1>
    <button class="btn ghost sm" onclick={() => (protOpen = true)}>How is this protected?</button>
  </header>

  {#if loading}
    <p class="muted" role="status">Loading…</p>
  {:else if blocked}
    <div class="notice warn" role="alert"><div><strong>Not available.</strong> {blocked}</div></div>
    <a class="btn" href="/chat">Go to chat</a>
  {:else if opts}
    <div class="cols">
      <form class="panel form" onsubmit={(e) => { e.preventDefault(); generate(); }} aria-label={`Generate ${noun}`}>
        <div class="panel-b">
          <div class="field">
            <label for="model">Model</label>
            <select id="model" class="input" bind:value={modelId}>
              {#each opts.models as m}<option value={m.id}>{m.alias}</option>{/each}
            </select>
            {#if model?.description}<span class="hint">{model.description}</span>{/if}
            {#if model?.audio}<span class="hint">This model also generates sound. It plays with the video if the engine included it in the file.</span>{/if}
          </div>
          <div class="field">
            <label for="prompt">Prompt</label>
            <textarea id="prompt" class="input" rows="4" maxlength={opts.prompt_max} bind:value={prompt} onkeydown={onKey}
              placeholder={kind === 'image' ? 'Describe the picture' : 'Describe the scene and how it moves'}></textarea>
            <span class="hint">{prompt.length} / {opts.prompt_max}. <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to generate.</span>
          </div>
          {#if model?.negative_prompt}
            <div class="field">
              <label for="neg">Negative prompt <span class="muted">(optional)</span></label>
              <textarea id="neg" class="input" rows="2" maxlength={opts.prompt_max} bind:value={negative}></textarea>
            </div>
          {/if}

          {#if kind === 'video' && resolutions.length}
            <div class="field">
              <label for="res">Resolution</label>
              <select id="res" class="input" bind:value={resolution}>
                {#each resolutions as r}<option value={`${r[0]}x${r[1]}`}>{r[0]} × {r[1]}</option>{/each}
              </select>
            </div>
          {:else}
            <div class="two">
              <div class="field"><label for="w">Width</label>
                <input id="w" class="input" type="number" inputmode="numeric" bind:value={width} min={lim.min_side} max={lim.max_side} step={lim.multiple ?? 8} /></div>
              <div class="field"><label for="h">Height</label>
                <input id="h" class="input" type="number" inputmode="numeric" bind:value={height} min={lim.min_side} max={lim.max_side} step={lim.multiple ?? 8} /></div>
            </div>
            {#if lim.min_side}<p class="hint sp">{lim.min_side} to {lim.max_side} px, in steps of {lim.multiple ?? 8}.</p>{/if}
          {/if}

          {#if kind === 'video'}
            <div class="two">
              <div class="field"><label for="fr">Frames</label>
                <input id="fr" class="input" type="number" inputmode="numeric" bind:value={frames} min={lim.frames?.[0]} max={lim.frames?.[1]} step={lim.frame_step ?? 4} /></div>
              <div class="field"><label for="fps">Frames per second</label>
                <input id="fps" class="input" type="number" inputmode="numeric" bind:value={fps} min={lim.fps?.[0]} max={lim.fps?.[1]} /></div>
            </div>
            <p class="hint sp" class:over={tooLong}>
              Clip length {seconds.toFixed(1)} s{#if opts.video_max_seconds}; your organisation allows up to {opts.video_max_seconds} s{/if}.
              Frames must be 1 more than a multiple of {lim.frame_step ?? 4}.
            </p>
          {/if}

          <div class="two">
            <div class="field"><label for="steps">Steps</label>
              <input id="steps" class="input" type="number" inputmode="numeric" bind:value={steps} min={lim.steps?.[0]} max={lim.steps?.[1]} /></div>
            <div class="field"><label for="g">Guidance</label>
              <input id="g" class="input" type="number" inputmode="decimal" bind:value={guidance} min={lim.guidance?.[0]} max={lim.guidance?.[1]} step="0.1" /></div>
          </div>
          <div class="two">
            <div class="field"><label for="seed">Seed</label>
              <input id="seed" class="input" type="number" inputmode="numeric" bind:value={seed} min="0" placeholder="Random" /></div>
            {#if kind === 'image' && model?.schedulers?.length}
              <div class="field"><label for="sch">Sampler</label>
                <select id="sch" class="input" bind:value={scheduler}><option value="">Model default</option>{#each model.schedulers as s}<option value={s}>{s}</option>{/each}</select></div>
            {/if}
          </div>

          {#if noStorage}
            <div class="notice info"><div class="small">Your organisation stores no generated files. The result stays available for a few minutes so you can download it.</div></div>
          {:else if opts.ephemeral}
            <div class="field check">
              <label class="cb"><input type="checkbox" checked={!save} onchange={(e) => (save = !e.currentTarget.checked)} /> No-save: keep neither the prompt nor the result</label>
              <span class="hint">The result is held in memory for a few minutes so you can download it, then forgotten.</span>
            </div>
          {/if}

          {#if formError}<div class="notice bad" role="alert"><div>{formError}</div></div>{/if}
          {#if atLimit}<p class="hint" role="status">You have {opts.caps.active} running (limit {opts.caps.concurrent}). Wait for one to finish or cancel it.</p>{/if}
          {#if galleryFull}<p class="hint" role="status">Your gallery is full ({opts.caps.stored} of {opts.caps.max_assets}). Delete something, or turn on no-save.</p>{/if}
          <button class="btn primary go" type="submit" disabled={!canGo}>{busy ? 'Sending…' : `Generate ${noun}`}</button>
          <p class="hint sp">
            {#if opts.caps.max_assets}{opts.caps.stored} of {opts.caps.max_assets} saved.{/if}
            {#if opts.caps.daily}{opts.caps.today} of {opts.caps.daily} today.{/if}
          </p>
        </div>
      </form>

      <section class="gallery" aria-label={`Your ${noun}s`}>
        <div class="sr-only" role="status" aria-live="polite">{live}</div>
        {#if !jobs.length}
          <div class="empty"><Icon name={kind} size={28} /><p><strong>Nothing yet.</strong></p>
            <p class="muted small">{kind === 'image' ? 'Write a prompt and press Generate. Pictures appear here.' : 'Video takes longer than images and runs one clip at a time. Results appear here.'}</p></div>
        {/if}
        <ul class="cards">
          {#each jobs as j (j.id)}
            <li class="card" aria-label={`${statusWord(j)}: ${j.prompt || 'no-save generation'}`}>
              <div class="media">
                {#if j.status === 'completed' && j.assets.length}
                  {#each j.assets as a}
                    {#if a.kind === 'image'}
                      <img src={`/api/media/assets/${a.id}/content`} alt={j.prompt || 'Generated image (prompt not saved)'} width={a.width ?? undefined} height={a.height ?? undefined} loading="lazy" />
                    {:else}
                      <!-- svelte-ignore a11y_media_has_caption : generated clips have no transcript to caption; the prompt is the accessible label -->
                      <video src={`/api/media/assets/${a.id}/content`} controls preload="metadata" playsinline aria-label={j.prompt || 'Generated video (prompt not saved)'}></video>
                    {/if}
                  {/each}
                {:else if j.status === 'queued' || j.status === 'running'}
                  <div class="wait">
                    <span class="chip info">{statusWord(j)}</span>
                    <p class="small">{j.progress_text || '…'}{#if j.status === 'queued' && j.queue_position} (position {j.queue_position}){/if}</p>
                    {#if j.progress != null}<progress value={j.progress} max="1" aria-label="Progress reported by the engine"></progress>{/if}
                    <p class="tiny muted">{dur(j.elapsed_s)} so far. {kind === 'video' ? 'Video can take several minutes.' : ''}</p>
                  </div>
                {:else if j.status === 'failed'}
                  <div class="wait"><span class="chip bad">Failed</span><p class="small">{j.error || 'The engine reported a failure.'}</p></div>
                {:else}
                  <div class="wait"><span class="chip">{statusWord(j)}</span>{#if j.status === 'completed'}<p class="small muted">The result is no longer available.</p>{/if}</div>
                {/if}
              </div>
              <div class="meta">
                <p class="pr">{j.prompt || 'Prompt not saved'}</p>
                <p class="tiny muted">
                  {j.model ?? 'Model removed'} · {when(j.created_at)}
                  {#if j.assets[0]?.seed != null} · seed {j.assets[0].seed}{/if}
                  {#if j.assets[0]?.width} · {j.assets[0].width}×{j.assets[0].height}{/if}
                  {#if kind === 'video' && j.assets[0]?.duration} · {j.assets[0].duration.toFixed(1)} s{/if}
                </p>
                {#if j.assets.some((a: any) => !a.saved)}
                  <p class="tiny"><span class="chip warn">Not saved</span> Disappears in about {mins(j.assets[0].expires_at)} min. Download it now if you want it.</p>
                {:else if j.assets[0]?.expires_at}
                  <p class="tiny muted">Deleted automatically on {when(j.assets[0].expires_at)}.</p>
                {/if}
                <div class="acts">
                  {#each j.assets as a}<a class="btn sm" href={`/api/media/assets/${a.id}/content?download=true`} download><Icon name="download" size={15} /> Download</a>{/each}
                  {#if j.prompt}<button class="btn sm" onclick={() => reuse(j)}><Icon name="refresh" size={15} /> Reuse settings</button>{/if}
                  {#if j.status === 'queued' || j.status === 'running'}
                    <button class="btn sm" onclick={() => cancel(j)}><Icon name="stop" size={15} /> Cancel</button>
                  {:else}
                    <button class="btn sm danger" onclick={() => (confirmDelete = j)}><Icon name="trash" size={15} /> Delete</button>
                  {/if}
                </div>
                {#if techRows(j.tech).length}
                  <details class="tech"><summary>Technical details</summary>
                    <dl>{#each techRows(j.tech) as [k, v]}<dt>{TECH_LABELS[k] ?? k}</dt><dd class="mono">{k === 'gen_params' ? JSON.stringify(v) : fmtTech(k, v)}</dd>{/each}</dl>
                    {#if j.tech?._unavailable?.length}<p class="tiny muted">The engine did not report: {j.tech._unavailable.map((k: string) => TECH_LABELS[k] ?? k).join(', ')}.</p>{/if}
                  </details>
                {/if}
              </div>
            </li>
          {/each}
        </ul>
      </section>
    </div>
  {/if}
</div>

<Modal bind:open={protOpen} title={`How are generated ${noun}s protected?`} wide><MediaProtection {kind} />{#snippet footer()}<button class="btn" onclick={() => (protOpen = false)}>Close</button>{/snippet}</Modal>
<Modal open={!!confirmDelete} title={`Delete this ${noun}?`}>
  <p>The {noun} and its saved prompt will be permanently deleted from this server. Backups expire on their own schedule.</p>
  {#snippet footer()}<button class="btn" onclick={() => (confirmDelete = null)}>Cancel</button><button class="btn danger solid" onclick={() => remove(confirmDelete)}>Delete {noun}</button>{/snippet}
</Modal>

<style>
  .studio { padding: 20px 24px 32px; max-width: 1280px; margin: 0 auto; }
  .head { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 16px; flex-wrap: wrap; }
  .cols { display: grid; grid-template-columns: minmax(300px, 380px) minmax(0, 1fr); gap: 20px; align-items: start; }
  .form { position: sticky; top: 0; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .sp { margin: -8px 0 12px; }
  .over { color: var(--danger); }
  .go { width: 100%; justify-content: center; min-height: 44px; }
  .cb { display: flex; gap: 8px; align-items: flex-start; font-weight: 500; }
  .cb input { margin-top: 3px; }
  textarea.input { resize: vertical; min-height: 44px; font: inherit; }
  .cards { list-style: none; margin: 0; padding: 0; display: grid; gap: 16px; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); }
  .card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); overflow: hidden; display: grid; align-content: start; min-width: 0; }
  .media { background: var(--surface-2); display: grid; }
  .media img, .media video { width: 100%; height: auto; max-height: 520px; object-fit: contain; display: block; background: var(--surface-2); }
  .wait { padding: 20px 16px; display: grid; gap: 6px; justify-items: start; min-height: 140px; align-content: center; }
  .wait p { margin: 0; }
  progress { width: 100%; height: 8px; accent-color: var(--accent); }
  .meta { padding: 12px 14px 14px; display: grid; gap: 6px; }
  .meta p { margin: 0; }
  .pr { overflow-wrap: anywhere; display: -webkit-box; -webkit-line-clamp: 3; line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden; }
  .acts { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 4px; }
  .tech summary { cursor: pointer; font-size: 13px; color: var(--fg-muted); }
  .tech dl { display: grid; grid-template-columns: max-content 1fr; gap: 2px 12px; margin: 8px 0 0; font-size: 12px; }
  .tech dt { color: var(--fg-muted); } .tech dd { margin: 0; overflow-wrap: anywhere; }
  .empty { border: 1px dashed var(--border-strong); border-radius: var(--radius); padding: 40px 20px; text-align: center; color: var(--fg-muted); display: grid; justify-items: center; gap: 4px; }
  .empty p { margin: 0; }
  @media (max-width: 899px) {
    .cols { grid-template-columns: minmax(0, 1fr); }
    .form { position: static; }
    .studio { padding: 16px 16px 24px; }
  }
  @media (max-width: 420px) { .cards { grid-template-columns: minmax(0, 1fr); } }
</style>
