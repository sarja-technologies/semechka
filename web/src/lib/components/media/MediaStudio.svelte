<script lang="ts">
  // One screen for making a picture or a video: describe it, choose a model, optionally add a reference picture, press Generate.
  // The result is shown large beside the form. Progress is whatever the engine reports (MediaCard follows the job); nothing is invented.
  // `advanced` adds the model's own controls (size, seed, negative prompt, steps, frame rate) for the Experimental versions of both screens.
  import { onMount, untrack } from 'svelte';
  import { get } from '$lib/api';
  import { app } from '$lib/session.svelte';
  import { loadCatalog, catalog } from '$lib/chat/store.svelte';
  import { errText } from '$lib/chat/thread.svelte';
  import Icon from '$lib/components/Icon.svelte';
  import ModelChooser from '$lib/components/chat/ModelChooser.svelte';
  import MediaCard from '$lib/components/chat/MediaCard.svelte';
  import DurationControl from '$lib/components/media/DurationControl.svelte';
  import MediaAssetPicker from '$lib/components/media/MediaAssetPicker.svelte';
  import MediaThumb from '$lib/components/media/MediaThumb.svelte';
  import { Upload, precheck } from '$lib/media/upload.svelte';
  import { planDuration } from '$lib/media/duration';
  import { handoff } from '$lib/media/handoff.svelte';
  import { type Asset } from '$lib/media/assets';
  import { blockReason, mediaParams, recentJobs, referenceRules, sizeChoices, startFromReference, startFromWords, type Kind, type MediaModel } from '$lib/media/studio';

  let { kind, advanced = false }: { kind: Kind; advanced?: boolean } = $props();
  const noun = $derived(kind === 'image' ? 'picture' : 'video');
  const ui = $derived(app.me.ui);
  const op = $derived(ui.operations?.[kind]);
  const uid = $derived<string>(app.me.user.id);

  // ---- model ---------------------------------------------------------------------------------------------------------
  const cat = $derived(catalog(kind));
  let modelId = $state('');
  let opts = $state<any>(null), optsErr = $state('');
  $effect(() => { const k = kind; untrack(() => { loadCatalog(k); get(`/api/media/options?kind=${k}`).then((o) => (opts = o)).catch((e) => (optsErr = errText(e))); }); });
  $effect(() => {
    const c = cat; if (!c || c.error) return;
    if (!modelId || !c.models.some((m: any) => m.id === modelId)) modelId = c.default ?? c.models[0]?.id ?? '';
  });
  const model = $derived<MediaModel | undefined>(opts?.models?.find((m: MediaModel) => m.id === modelId));
  const rules = $derived(referenceRules(kind, model));
  const vmax = $derived<number | null>(kind === 'video' ? opts?.video_max_seconds ?? null : null);

  // ---- the request -----------------------------------------------------------------------------------------------------
  let prompt = $state(''), size = $state(''), seed = $state(''), negative = $state(''), steps = $state(''), secs = $state(0), fps = $state<number | null>(null);
  let lastModel = '', secsTouched = false;
  $effect(() => {
    const m = model; if (!m || m.id === lastModel) return;
    lastModel = m.id; const d = m.defaults ?? {};
    size = d.width && d.height ? `${d.width}x${d.height}` : '';
    steps = d.steps ? String(d.steps) : '';
    fps = null;
    secs = secsTouched && secs > 0 ? secs : (d.seconds ?? m.duration?.default ?? 0);
  });
  const sizes = $derived(sizeChoices(kind, model));
  const dplan = $derived(kind === 'video' && model?.duration ? planDuration(secs, model.duration, { fps, maxSeconds: vmax }) : null);
  const promptMax = $derived<number>(opts?.prompt_max ?? 4000);

  // ---- reference picture ------------------------------------------------------------------------------------------------
  let ref = $state<Asset | null>(null), up = $state<Upload | null>(null), pickerOpen = $state(false), refNote = $state(''), fileInput: HTMLInputElement | undefined = $state();
  const maxMb = $derived<number>(ui.composer.media_upload_max_mb ?? 512);
  const canUpload = $derived(!!ui.composer.media_library);
  function chooseFile(e: Event) {
    const i = e.currentTarget as HTMLInputElement, f = i.files?.[0]; i.value = '';
    if (!f) return;
    refNote = '';
    if (!f.type.startsWith('image/')) { refNote = `${f.name} is not a picture.`; return; }
    const why = precheck(f, maxMb); if (why) { refNote = why; return; }
    up?.cancel();
    const u = (up = new Upload(f, false));
    ref = null;
    u.start().then((a) => { if (up === u) { if (a) ref = a; else if (u.status === 'error') refNote = u.error; } });
  }
  function clearRef() { up?.cancel(); up = null; ref = null; refNote = ''; }
  onMount(() => { const h = handoff[kind]; if (h) { ref = h; handoff[kind] = null; } });
  const refBusy = $derived(!!up && up.active);

  // ---- result --------------------------------------------------------------------------------------------------------------
  let job = $state<any>(null), starting = $state(false), err = $state('');
  let recent = $state<any[]>([]), stage: HTMLElement | undefined = $state();
  const running = $derived(starting || (!!job && (job.status === 'queued' || job.status === 'running')));
  async function loadRecent() { try { recent = (await recentJobs(kind)).filter((j) => j.status === 'completed' && j.assets?.length).slice(0, 6); } catch { recent = []; } }
  onMount(loadRecent);

  const why = $derived(blockReason({ kind, model, prompt, hasRef: !!ref || refBusy, refBusy, secondsOver: !!vmax && !!dplan && dplan.seconds > vmax + 1e-9,
    noDuration: kind === 'video' && !!model?.duration && !dplan, tooLong: prompt.length > promptMax }));

  async function generate() {
    if (why || running) return;
    err = ''; starting = true;
    const media = mediaParams({ kind, size: advanced || kind === 'image' ? size : '', seed: advanced ? seed : '', negative: advanced ? negative : '', steps: advanced ? steps : '',
      seconds: dplan?.seconds ?? null, fps: advanced ? fps : null, model, editParams: !!ui.composer.edit_params });
    try {
      job = ref ? await startFromReference(uid, kind, modelId, prompt, ref.id, media) : await startFromWords(kind, modelId, prompt, media);
      stage?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    } catch (e) { err = errText(e); } finally { starting = false; }
  }
  function changed(j: any) { job = j; if (j.status === 'completed') loadRecent(); }
  function onKey(e: KeyboardEvent) { if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); generate(); } }
  const useAsRef = (a: any) => { if (kind === 'image' && a.kind !== 'video') { up?.cancel(); up = null; ref = a; } };
</script>

{#if !op?.enabled}
  <div class="studio"><div class="notice warn" role="status"><div><strong>{kind === 'image' ? 'Images are' : 'Videos are'} not available to you.</strong> {op?.reason}</div></div></div>
{:else}
  <div class="studio" class:adv={advanced}>
    <form class="form" onsubmit={(e) => { e.preventDefault(); generate(); }} aria-label={kind === 'image' ? 'Make a picture' : 'Make a video'}>
      <div class="field">
        <label for="st-prompt">{kind === 'image' ? 'What should the picture show?' : 'What should happen in the video?'}</label>
        <textarea id="st-prompt" class="input" rows="5" bind:value={prompt} onkeydown={onKey} maxlength={promptMax + 1000}
          placeholder={kind === 'image' ? 'A lighthouse on a cliff at dusk, long shadows, calm sea' : 'A paper boat drifts down a rain-soaked street, the camera follows at water level'}
          aria-describedby="st-hint"></textarea>
        <span id="st-hint" class="hint tiny">Ctrl+Enter makes it.{#if prompt.length > promptMax * 0.8} {prompt.length} of {promptMax} characters.{/if}</span>
      </div>

      <div class="field">
        <span class="label" id="st-model-l">Model</span>
        {#if cat?.error}<p class="err" role="alert">{cat.error}</p>
        {:else if !cat}<p class="muted small" role="status">Loading models…</p>
        {:else if !cat.models.length}<p class="muted small">No {noun} model is available to you.</p>
        {:else}<ModelChooser catalog={cat} op={kind} bind:value={modelId} placement="down" label={kind === 'image' ? 'Picture model' : 'Video model'} />{/if}
        {#if model?.description}<span class="hint tiny">{model.description}</span>{/if}
      </div>

      {#if kind === 'image' && sizes.length}
        <div class="field">
          <label for="st-size">Shape</label>
          <select id="st-size" class="input" bind:value={size}>{#each sizes as s (s.v)}<option value={s.v}>{s.label}</option>{/each}</select>
        </div>
      {/if}

      {#if kind === 'video' && model?.duration}
        <DurationControl spec={model.duration} bind:seconds={secs} {fps} maxSeconds={vmax} onuser={() => (secsTouched = true)} />
      {/if}

      <div class="field ref">
        <span class="label" id="st-ref-l">Reference picture {#if rules.needs}<span class="req">required</span>{:else}<span class="muted">optional</span>{/if}</span>
        {#if ref || up}
          <div class="refcard">
            <span class="rt">{#if ref}<MediaThumb id={ref.id} kind="image" name="Reference picture" ratio="1 / 1" />{:else if up?.local}<img src={up.local} alt="" />{/if}</span>
            <span class="rn">
              <strong>{ref?.name ?? up?.file.name}</strong>
              {#if refBusy}<span class="tiny muted" role="status">{up?.status === 'processing' ? 'Processing…' : `Uploading ${Math.round((up?.fraction ?? 0) * 100)}%`}</span>
              {:else if ref}<span class="tiny muted">{kind === 'image' ? 'The model changes or follows this picture.' : 'The video starts from this picture.'}</span>{/if}
            </span>
            <button type="button" class="btn ghost sm" onclick={clearRef} aria-label="Remove the reference picture"><Icon name="x" size={14} /> Remove</button>
          </div>
        {:else}
          <div class="refadd">
            <button type="button" class="btn" disabled={!canUpload} title={canUpload ? '' : 'Uploads are turned off for you.'} onclick={() => fileInput?.click()}><Icon name="upload" size={15} /> Upload a picture</button>
            <button type="button" class="btn" disabled={!canUpload} onclick={() => (pickerOpen = true)}><Icon name="library" size={15} /> From Library</button>
          </div>
        {/if}
        <input bind:this={fileInput} type="file" accept="image/*" class="sr-only" tabindex="-1" aria-labelledby="st-ref-l" onchange={chooseFile} />
        {#if refNote}<p class="err small" role="alert">{refNote}</p>{/if}
        {#if !ref && !up && !rules.needs && model && !rules.accepts}<span class="hint tiny">{model.alias} works from words only.</span>{/if}
      </div>

      {#if advanced}
        <fieldset class="adv-set">
          <legend>Model controls</legend>
          {#if kind === 'video' && sizes.length}
            <div class="field"><label for="st-res">Resolution</label>
              <select id="st-res" class="input" bind:value={size}>{#each sizes as s (s.v)}<option value={s.v}>{s.label}</option>{/each}</select></div>
          {/if}
          {#if kind === 'image' && !sizes.length}<p class="hint tiny">This model publishes no size range.</p>{/if}
          <div class="two">
            <div class="field"><label for="st-seed">Seed</label><input id="st-seed" class="input" inputmode="numeric" placeholder="Random" bind:value={seed} /></div>
            {#if ui.composer.edit_params}<div class="field"><label for="st-steps">Steps</label><input id="st-steps" class="input" inputmode="numeric" bind:value={steps} /></div>{/if}
          </div>
          {#if model?.negative_prompt}
            <div class="field"><label for="st-neg">Negative prompt</label><input id="st-neg" class="input" bind:value={negative} placeholder="What to keep out" /></div>
          {/if}
          {#if kind === 'video' && model?.duration?.fps_configurable}
            <div class="field"><label for="st-fps">Frame rate</label>
              <input id="st-fps" class="input" type="number" min={model.duration.fps_range[0]} max={model.duration.fps_range[1]} placeholder={`${model.duration.fps} (the model's own)`} value={fps ?? ''}
                oninput={(e) => { const n = Number(e.currentTarget.value); fps = Number.isFinite(n) && n > 0 ? n : null; }} />
              <span class="hint tiny">Changing the frame rate changes which lengths this model can make.</span></div>
          {/if}
        </fieldset>
      {/if}

      <div class="go">
        <button type="submit" class="btn primary go-btn" disabled={!!why || running} aria-describedby="st-why" data-testid="generate">
          {#if running}{starting ? 'Sending…' : 'Working…'}{:else}Generate {noun}{/if}
        </button>
        <p id="st-why" class="tiny muted" role="status">{running ? `The ${noun} is being made. Cancel it from the result if you need to.` : why}</p>
      </div>
      {#if err}<div class="notice bad" role="alert"><div>{err}</div></div>{/if}
      {#if optsErr}<div class="notice bad" role="alert"><div>{optsErr}</div></div>{/if}
    </form>

    <section class="stage" bind:this={stage} aria-label="Result" aria-live="polite">
      {#if job}
        {#key job.id}<MediaCard {job} {kind} large onchange={changed} onuse={kind === 'image' ? useAsRef : undefined} />{/key}
      {:else}
        <div class="blank">
          <Icon name={kind} size={28} />
          <p>{kind === 'image' ? 'Your picture appears here.' : 'Your video appears here.'}</p>
          <p class="small muted">{kind === 'image' ? 'Describe it, pick a model and press Generate. The engine reports its own progress while it works.' : 'Describe the clip and choose how long it should be. A video can take several minutes; the engine reports its own progress.'}</p>
        </div>
      {/if}

      {#if recent.length}
        <div class="recent">
          <div class="rh"><h2>Recent {noun}s</h2><a class="small" href="/library">Everything in the Library</a></div>
          <ul>
            {#each recent as j (j.id)}
              <li><button type="button" class:on={job?.id === j.id} onclick={() => (job = j)} aria-label={`Show: ${(j.prompt || noun).slice(0, 60)}`} title={j.prompt}>
                <MediaThumb id={j.assets[0].id} kind={kind} name="" duration={j.assets[0].duration} ratio={kind === 'video' ? '16 / 9' : '1 / 1'} /></button></li>
            {/each}
          </ul>
        </div>
      {:else}
        <p class="small muted lib"><a href="/library">Library</a> keeps what you have made.</p>
      {/if}
    </section>
  </div>
  <MediaAssetPicker bind:open={pickerOpen} accept="image" title="Choose a reference picture" onpick={(a) => { up?.cancel(); up = null; ref = a[0]; }} />
{/if}

<style>
  .studio { display: grid; grid-template-columns: minmax(300px, 420px) minmax(0, 1fr); gap: 32px; padding: 20px 24px 40px; width: 100%; max-width: 1440px; margin: 0 auto; align-items: start; }
  .form { display: grid; gap: 4px; min-width: 0; }
  .form .field { margin-bottom: 14px; }
  textarea.input { min-height: 120px; }
  .req { color: var(--danger); font-weight: 500; font-size: 12px; margin-left: 4px; } .ref .muted { font-weight: 400; font-size: 12px; margin-left: 4px; }
  .refadd { display: flex; gap: 8px; flex-wrap: wrap; }
  .refcard { display: grid; grid-template-columns: 64px minmax(0, 1fr) auto; gap: 12px; align-items: center; padding: 8px; border: 1px solid var(--border); border-radius: var(--radius); background: var(--card-bg); }
  .rt { width: 64px; height: 64px; border-radius: var(--radius-sm); overflow: hidden; display: block; border: 1px solid var(--border); }
  .rt img { width: 100%; height: 100%; object-fit: cover; display: block; }
  .rn { display: grid; gap: 2px; min-width: 0; } .rn strong { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-weight: 500; }
  fieldset.adv-set { border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 14px 0; margin: 0 0 14px; min-width: 0; }
  fieldset.adv-set legend { font-weight: 600; font-size: 13px; padding: 0 6px; }
  .two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
  .go { display: grid; gap: 8px; position: sticky; bottom: 0; background: var(--bg); padding: 8px 0 4px; }
  .go-btn { min-height: 44px; font-size: 15px; width: 100%; }
  .go p { margin: 0; min-height: 1.5em; }
  .stage { display: grid; gap: 20px; min-width: 0; align-content: start; }
  .blank { display: grid; place-content: center; justify-items: center; gap: 4px; text-align: center; min-height: 360px; border: 1px dashed var(--border-strong); border-radius: var(--radius-menu); color: var(--fg-muted); padding: 32px; }
  .blank p { margin: 0; max-width: 46ch; } .blank p:first-of-type { color: var(--fg); font-size: 16px; margin-top: 8px; }
  .recent { display: grid; gap: 8px; } .rh { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; } .rh h2 { font-size: 14px; }
  .recent ul { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(72px, 96px)); gap: 8px; }
  .recent button { all: unset; display: block; width: 100%; border-radius: var(--radius-sm); overflow: hidden; border: 2px solid transparent; cursor: pointer; box-sizing: border-box; }
  .recent button.on { border-color: var(--accent); } .recent button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .lib { margin: 0; }
  @media (max-width: 899px) {
    .studio { grid-template-columns: minmax(0, 1fr); gap: 20px; padding: 12px 16px 24px; }
    .blank { min-height: 220px; }
  }
</style>
